import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy import nullslast
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import (
    BASE_TYPE_OF,
    PINNED_DETAIL_TYPES,
    Account,
    DETAIL_TYPES,
    DetailType,
    default_detail_type,
    detail_label,
    detail_type_problem,
)
from app.services import control_accounts
from app.services.account_tree import (
    BASE_WORDS,
    MAX_DEPTH,
    chain_depth,
    depth_message,
    loop_message,
    loop_through,
    takes_no_new_parent,
)
from app.models.transactions import TransactionLine
from app.schemas.accounts import (
    AccountCreate,
    AccountUpdate,
    AccountResponse,
    RetypedSubAccount,
)
from app.routes._helpers import get_or_404, reject_null_updates
from app.routes._roles import require_admin

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _reject_duplicate_number(db: Session, number, exclude_id=None):
    """account_number is UNIQUE. Without this the violation surfaces from the
    database as an unhandled IntegrityError, i.e. an opaque HTTP 500 that
    names neither the field nor the account already using it."""
    if not number:
        return
    q = db.query(Account).filter(Account.account_number == number)
    if exclude_id is not None:
        q = q.filter(Account.id != exclude_id)
    clash = q.first()
    if clash:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Account number {number} is already used by "
                f"'{clash.name}'. Account numbers must be unique."
            ),
        )


# Digits, optionally in dotted or dashed groups for a sub-account (6150.1,
# 6150-01) — the pattern the seeded chart and the chart importer use. "ABC"
# and a blank number were accepted, and a blank one then listed as
# " - Name" in every picker (2.17.3 exploratory test, W-L4). Charts brought
# in by an importer keep whatever numbers they carry; this is the rule for
# numbers typed on the form or sent to the API.
_ACCOUNT_NUMBER_RE = re.compile(r"\d+(?:[.-]\d+)*")


def _check_account_number(number) -> None:
    """A number is optional (#332: many companies don't number their
    accounts; the books never needed one). Given, it is digits, like 6150,
    or 6150.1 / 6150-01 for a sub-account."""
    if not number:
        return
    if len(number) > 20 or not _ACCOUNT_NUMBER_RE.fullmatch(number):
        raise HTTPException(
            status_code=400,
            detail=(
                f'"{number[:40]}" is not an account number. Use digits, like '
                "6150; a sub-account can use 6150.1 or 6150-01."
            ),
        )


def _reject_reserved_number(number, name=None, account_type=None) -> None:
    """A new or renumbered account may not land on a control-account number.

    The posting code resolves the control accounts BY NUMBER (see
    control_accounts.py): inventory write-offs go to whatever account carries
    5900, invoices to 1100, and so on. 5900 and 4800 are created on demand, so
    their slot is usually empty — a user who numbers a new expense 5900 then
    silently becomes where inventory adjustments post. The seeded ones (1100,
    2000, …) are normally occupied, where _reject_duplicate_number already
    refuses a second account; this guard also covers the slot being empty on a
    hand-built chart. The control accounts themselves are created by the system
    paths (seeding, chart import, importers, ensure_account), none of which go
    through this route, so only the user-facing form is guarded.

    Restoring the control account itself — the registry's exact name AND its
    expected type, as MissingControlAccount tells the operator to — is allowed
    while the slot is empty. A right name with the wrong type is a 400 (fix the
    type); a foreign name is a 409 (the number is reserved)."""
    reason = control_accounts.restore_conflict(number, name, account_type)
    if not reason:
        return
    # The right-name/wrong-type message names the type to set; a foreign-name
    # collision is a 409 against the number itself.
    status = 400 if "must be a" in reason else 409
    raise HTTPException(status_code=status, detail=reason)


_BANK_KIND_FOR_TYPE = {"bank": "asset", "credit_card": "liability"}


def _check_parent_usable(db: Session, parent_id) -> None:
    """A parent id must name an account that can take sub-accounts (#416):
    it exists (SQLite does not enforce the FK) and is active. The form hides
    inactive accounts from the picker; refuse one sent straight to the API,
    so the two agree."""
    if parent_id is None:
        return
    parent = db.get(Account, parent_id)
    if parent is None:
        raise HTTPException(
            status_code=400, detail="That parent account does not exist."
        )
    if not parent.is_active:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{_named(parent)} is inactive and cannot take sub-accounts. "
                f"Reactivate it, or choose another parent."
            ),
        )


def _check_bank_kind(bank_kind, account_type) -> None:
    """A bank is an asset, a card is a liability; anything else is a
    mistake the picker would propagate everywhere."""
    if bank_kind is None:
        return
    want = _BANK_KIND_FOR_TYPE.get(bank_kind)
    have = getattr(account_type, "value", account_type)
    if want != have:
        raise HTTPException(
            status_code=400,
            detail=f"bank_kind {bank_kind!r} needs account_type {want!r}, not {have!r}",
        )


def _bank_history(db: Session, account: Account) -> list[str]:
    """What hangs off a bank or card account as a register: reconciliations,
    bank-feed rows, register entries and transfers. Each is keyed off
    bank_kind; clearing it would orphan them."""
    from app.models.banking import BankAccount, BankTransaction, Reconciliation
    from app.models.transactions import Transaction

    feeds = [
        i
        for (i,) in db.query(BankAccount.id).filter(
            BankAccount.account_id == account.id
        )
    ]
    found = []
    recs = (
        db.query(func.count(Reconciliation.id))
        .filter(
            (Reconciliation.account_id == account.id)
            | (Reconciliation.bank_account_id.in_(feeds or [-1]))
        )
        .scalar()
    )
    if recs:
        found.append(f"{recs} reconciliation(s)")
    rows = (
        db.query(func.count(BankTransaction.id))
        .filter(BankTransaction.bank_account_id.in_(feeds or [-1]))
        .scalar()
    )
    if rows:
        found.append(f"{rows} bank-feed row(s)")
    entries = (
        db.query(func.count(func.distinct(Transaction.id)))
        .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
        .filter(
            TransactionLine.account_id == account.id,
            Transaction.source_type.in_(("bank_entry", "transfer")),
        )
        .scalar()
    )
    if entries:
        found.append(f"{entries} register entr{'y' if entries == 1 else 'ies'}")
    return found


def _check_bank_kind_kept(db: Session, account: Account, fields: dict) -> None:
    """A bank or card account in use keeps its bank kind: the register, its
    reconciliations and its feed all key off it (#390 review)."""
    if "bank_kind" not in fields or not account.bank_kind:
        return
    if fields["bank_kind"] == account.bank_kind:
        return
    used = _bank_history(db, account)
    if used:
        kind = account.bank_kind.replace("_", " ")
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{account.name}' is a {kind} account with {', '.join(used)}; "
                f"changing its kind would take its register, reconciliations "
                f"and feed away. Make a new account for the other use and "
                f"deactivate this one."
            ),
        )


def _named(acct: Account) -> str:
    return " ".join(p for p in (acct.account_number, acct.name) if p)


def _move_out_first(child: Account, parent: Account) -> str:
    """The way out of a type change a parent and its sub-account block for
    each other: base types are not cascaded."""
    return (
        f" Move {_named(child) or 'the sub-account'} out from under "
        f"{_named(parent)} first (Parent account: none, on its form), change "
        f"the types, then put it back."
    )


def _check_pinned_parent_base(
    parent: Account | None, atype, child: Account | None = None
) -> None:
    """Under 1100, 2000, a bank or a card a sub-account keeps its own detail
    type, but not another base type (#416): an expense under a bank account
    would be listed, and later totalled, under a parent in another section
    of the statements. Other parents already refuse it (_sub_account_refusal)."""
    if parent is None or parent.detail_type not in PINNED_DETAIL_TYPES:
        return
    want = getattr(parent.account_type, "value", parent.account_type)
    if getattr(atype, "value", atype) == want:
        return
    detail = (
        f"a sub-account of {_named(parent)} ({detail_label(parent.detail_type)}) "
        f"must be {BASE_WORDS[want]} too"
    )
    if child is not None:
        detail += "." + _move_out_first(child, parent)
    raise HTTPException(status_code=400, detail=detail)


def _check_children_base(account: Account, atype) -> None:
    """A parent whose type changes would leave a sub-account of the old type
    under it; the shared-detail-type ones are refused by the cascade, this
    catches the rest (a pinned parent's own-type sub-accounts)."""
    have = getattr(atype, "value", atype)
    for child in account.children:
        if getattr(child.account_type, "value", child.account_type) != have:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"its sub-account {_named(child)} is "
                    f"{BASE_WORDS[getattr(child.account_type, 'value', child.account_type)]}."
                    + _move_out_first(child, account)
                ),
            )


def _sub_account_refusal(
    parent: Account, why: str | None = None, child: Account | None = None, atype=None
) -> HTTPException:
    label = detail_label(parent.detail_type)
    detail = (
        f"a sub-account shares its parent's detail type, except under a bank, "
        f"card, Accounts Receivable or Accounts Payable parent; a sub-account "
        f"of {_named(parent)} ({label}) must be "
        f"a{'n' if label[0] in 'AEIOU' else ''} {label} too"
    ) + (f": {why}" if why else "")
    base = BASE_TYPE_OF[DetailType(parent.detail_type)]
    if (
        child is not None
        and atype is not None
        and getattr(atype, "value", atype) != (base.value)
    ):
        detail += "." + _move_out_first(child, parent)
    return HTTPException(status_code=400, detail=detail)


def _shares_parent(parent: Account | None) -> bool:
    """A sub-account matches its parent's detail type, except under a
    pinned one (1100, 2000, a bank, a card): those are checked alone."""
    return parent is not None and parent.detail_type not in PINNED_DETAIL_TYPES


def _create_detail_type(db: Session, data: AccountCreate) -> str:
    """The detail type a new account gets (#390): the one sent, else its
    parent's, else the default for its type and bank kind — by 2.27.0's
    number and name rule, so a new "Truck Loan" is a Long Term Liability
    and a "1650 Delivery Van" a Fixed Asset (2.28.0; default_detail_type
    has the precedence). A sub-account of a parent other than 1100, 2000,
    a bank or a card shares its parent's detail type; the pair with the
    type, the bank kind and the control numbers is checked in
    detail_type_problem."""
    parent = db.get(Account, data.parent_id) if data.parent_id else None
    if _shares_parent(parent):
        if data.detail_type is not None and data.detail_type.value != (
            parent.detail_type
        ):
            raise _sub_account_refusal(parent)
        why = detail_type_problem(
            parent.detail_type, data.account_type, data.bank_kind, data.account_number
        )
        if why:
            raise _sub_account_refusal(parent, why)
        return parent.detail_type
    detail = data.detail_type or default_detail_type(
        data.account_type, data.bank_kind, data.account_number, name=data.name
    )
    why = detail_type_problem(
        detail, data.account_type, data.bank_kind, data.account_number
    )
    if why:
        raise HTTPException(status_code=400, detail=why)
    return getattr(detail, "value", detail)


def _cascade_detail_type(account: Account, old: str, new: str, seen: set) -> list[dict]:
    """A parent's new detail type goes down to the sub-accounts that shared
    the old one; one that cannot take it refuses the change. Returns the
    sub-accounts it retyped (#416), each with its from/to, so the PUT can name
    them."""
    retyped: list[dict] = []
    if new in PINNED_DETAIL_TYPES:
        return retyped  # under a pinned detail type each sub-account stands alone
    if old in PINNED_DETAIL_TYPES:
        # Nothing shared the pinned one; under a plain parent every
        # sub-account must share the new one (#416), so one that does not
        # would be a pair the API refuses everywhere else.
        for child in account.children:
            if child.detail_type != new:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"its sub-account {_named(child)} is "
                        f"{detail_label(child.detail_type)}, and under "
                        f"{detail_label(new)} it would have to be "
                        f"{detail_label(new)} too." + _move_out_first(child, account)
                    ),
                )
        return retyped
    for child in account.children:
        if child.id in seen or child.detail_type != old:
            continue
        seen.add(child.id)
        why = detail_type_problem(
            new, child.account_type, child.bank_kind, child.account_number
        )
        if why:
            detail = (
                f"its sub-account {_named(child)} would have to become "
                f"{detail_label(new)} too, and cannot: {why}."
            )
            if BASE_TYPE_OF[DetailType(new)] != BASE_TYPE_OF[DetailType(old)]:
                detail += _move_out_first(child, account)
            raise HTTPException(status_code=400, detail=detail)
        retyped.extend(_cascade_detail_type(child, old, new, seen))
        child.detail_type = new
        retyped.append(
            {
                "id": child.id,
                "name": child.name,
                "account_number": child.account_number,
                "from_detail_type": old,
                "to_detail_type": new,
            }
        )
    return retyped


def _update_detail_type(db: Session, account: Account, fields: dict) -> list[dict]:
    """Settle fields["detail_type"] for a PUT (#390). Omitted, the current
    detail type stays while it still fits; a type or bank-kind change that
    it no longer fits resets it to the default, and a new parent's is taken.
    Control accounts keep theirs; a sub-account matches its parent."""
    atype = fields.get("account_type", account.account_type)
    kind = fields.get("bank_kind", account.bank_kind)
    number = fields.get("account_number", account.account_number)
    parent_id = fields.get("parent_id", account.parent_id)
    parent = db.get(Account, parent_id) if parent_id else None
    current = account.detail_type
    if fields.get("detail_type") is not None:
        detail = fields["detail_type"].value
    elif (
        "parent_id" in fields
        and _shares_parent(parent)
        and not control_accounts.is_control(
            account.account_number, account.account_type
        )
    ):
        # a control account never takes a parent's: the sub-account check
        # below says why it cannot go there
        detail = parent.detail_type
    elif detail_type_problem(current, atype, kind, number) is None:
        detail = current
    else:
        detail = default_detail_type(atype, kind, number).value
    asked = {"detail_type", "bank_kind", "account_type"} & set(fields)
    if (
        detail != current
        and asked
        and control_accounts.is_control(account.account_number, account.account_type)
    ):
        name, purpose = control_accounts.describe(account.account_number)
        raise HTTPException(
            status_code=400,
            detail=(
                f"{account.account_number} {name} is a control account — the "
                f"software finds it by its number to post {purpose}. Its detail "
                f"type stays {detail_label(current)}. You can rename it."
            ),
        )
    if _shares_parent(parent) and ("parent_id" in fields or detail != current):
        parent_why = detail_type_problem(parent.detail_type, atype, kind, number)
        if detail != parent.detail_type or parent_why:
            raise _sub_account_refusal(parent, parent_why, account, atype)
    why = detail_type_problem(detail, atype, kind, number)
    if why:
        raise HTTPException(status_code=400, detail=why)
    retyped: list[dict] = []
    if detail != current:
        retyped = _cascade_detail_type(account, current, detail, {account.id})
    if detail != current or "detail_type" in fields:
        fields["detail_type"] = detail
    return retyped


@router.get("", response_model=list[AccountResponse])
def list_accounts(
    active_only: bool = False,
    account_type: str = None,
    bank: bool = False,
    detail_type: str = None,
    db: Session = Depends(get_db),
):
    """`?bank=1` lists the bank and credit-card accounts (bank_kind set) —
    what the register, the transfer form and every paid-from / deposit-to
    picker use. `?detail_type=long_term_liability` filters on the detail
    type (#390)."""
    q = db.query(Account)
    if active_only:
        q = q.filter(Account.is_active)
    if account_type:
        q = q.filter(Account.account_type == account_type)
    if bank:
        q = q.filter(Account.bank_kind.isnot(None))
    if detail_type:
        q = q.filter(Account.detail_type == detail_type)
    # By number, and accounts without one (a chart imported from QuickBooks
    # Online, whose accounts carry none) by name, so a sub-account named
    # Parent:Child sits with its parent.
    return _in_company_words(
        db, q.order_by(nullslast(Account.account_number), Account.name).all()
    )


# The seeded accounts a pay run, a PTO accrual or a benefits posting finds
# by number, with nothing posted to them until the first run (2.23.5 round
# 2, W-16 / NEW-139: the cleanup took them, and the next pay run either
# failed with a 500 or posted wages to Advertising). They stay.
_FOUND_BY_NUMBER = {
    "6110": "payroll posts wages to it",
    "6140": "payroll posts reimbursements to it",
    "6160": "PTO accruals post to it",
    "6950": "payroll's fallback for reimbursements",
    "2300": "payroll liabilities post to it",
    "2310": "payroll posts federal withholding to it",
    "2320": "payroll posts state withholding to it",
    "2330": "payroll posts Social Security to it",
    "2340": "payroll posts Medicare to it",
    "2350": "payroll posts FUTA to it",
    "2360": "payroll posts SUTA to it",
    "2370": "payroll posts other withholdings to it",
    "2380": "benefits post to it",
    "2390": "PTO accruals post to it",
}


def _seeded_unused(db: Session) -> tuple[list[dict], list[dict]]:
    """The seeded accounts nobody uses, and the seeded ones that stay and
    why (#332): a control account, postings, sub-accounts, or a row that
    names it (a register, an item, a setting)."""
    removable, kept = [], []
    seeded = (
        db.query(Account)
        .filter(Account.is_system.is_(True))
        .order_by(nullslast(Account.account_number), Account.name)
        .all()
    )
    children = {
        pid
        for (pid,) in db.query(Account.parent_id).filter(Account.parent_id.isnot(None))
    }
    posted = {aid for (aid,) in db.query(TransactionLine.account_id).distinct()}
    for a in seeded:
        row = {"id": a.id, "account_number": a.account_number or "", "name": a.name}
        if control_accounts.is_control(a.account_number, a.account_type):
            kept.append({**row, "reason": "a control account"})
        elif a.account_number in _FOUND_BY_NUMBER:
            kept.append({**row, "reason": _FOUND_BY_NUMBER[a.account_number]})
        elif a.id in posted:
            kept.append({**row, "reason": "has postings"})
        elif a.id in children:
            kept.append({**row, "reason": "has sub-accounts"})
        else:
            others = [
                (t, n)
                for t, n in _referencing_rows(db, a.id)
                if t != "transaction lines"
            ]
            if others:
                kept.append(
                    {
                        **row,
                        "reason": "in use: "
                        + ", ".join(f"{n} in {t}" for t, n in others),
                    }
                )
            else:
                removable.append(row)
    return removable, kept


@router.get("/detail-types")
def list_detail_types():
    """QuickBooks' fifteen account types, in picker order, each with its
    base type (#390). Accounts Receivable and Payable are kept for 1100 and
    2000 (`control_only`), so the picker offers them nowhere else."""
    return [
        {
            "value": d.value,
            "label": label,
            "account_type": base.value,
            "control_only": d.value in ("accounts_receivable", "accounts_payable"),
        }
        for d, (label, base) in DETAIL_TYPES.items()
    ]


@router.get("/unused-seeded")
def unused_seeded(db: Session = Depends(get_db)):
    """A dry run: the seeded accounts that can go, and the ones that stay."""
    removable, kept = _seeded_unused(db)
    return {"removable": removable, "kept": kept}


@router.post("/unused-seeded/remove")
def remove_unused_seeded(db: Session = Depends(get_db)):
    """Remove every seeded account nobody uses, in one step; the result
    names what went and what stayed (#332)."""
    removable, kept = _seeded_unused(db)
    removed = []
    for row in removable:
        acct = db.get(Account, row["id"])
        if acct is None:
            continue
        sp = db.begin_nested()
        try:
            db.delete(acct)
            db.flush()
            sp.commit()
            removed.append(row)
        except IntegrityError:
            sp.rollback()
            kept.append({**row, "reason": "still referenced"})
    db.commit()
    return {"removed": removed, "kept": kept}


@router.post("/rebuild-balances")
def rebuild_balances(request: Request, db: Session = Depends(get_db)):
    """Recompute every account's stored balance from its ledger lines
    (#490) and say which were off. An administrator's call: the ledger is
    the record and nothing posted changes, but a total shown on the Chart
    of Accounts does, and the one who runs it should be the one who can
    read why in the answer. The same repair runs once at every start, so
    this is for a company opened after the server started, or to check.
    Idempotent: books whose totals agree with the ledger answer with no
    corrections."""
    require_admin(request)
    from app.services.accounting import rebuild_stored_balances

    corrected = rebuild_stored_balances(db)
    return {"count": len(corrected), "corrected": corrected}


@router.get("/{account_id}", response_model=AccountResponse)
def get_account(account_id: int, db: Session = Depends(get_db)):
    return _in_company_words(db, [get_or_404(db, Account, account_id)])[0]


def _in_company_words(db: Session, accounts):
    """The control-account purpose ("what customers owe — every invoice and
    payment") is written in the business words the registry keeps; the
    chart page prints it beside the account. Say it in the company's."""
    from app.services.terminology import terms_from_db

    terms = terms_from_db(db)
    out = [AccountResponse.model_validate(a) for a in accounts]
    if terms.is_nonprofit:
        for r in out:
            if r.control_purpose:
                r.control_purpose = terms.text(r.control_purpose)
    return out


@router.post("", response_model=AccountResponse, status_code=201)
def create_account(data: AccountCreate, db: Session = Depends(get_db)):
    _check_bank_kind(data.bank_kind, data.account_type)
    data.detail_type = _create_detail_type(db, data)
    _check_account_number(data.account_number)
    _reject_duplicate_number(db, data.account_number)
    _check_parent_usable(db, data.parent_id)
    # Refuse past MAX_DEPTH on create too (#416): a chain built this deep
    # with POST could not be re-parented onto, and the loop guard's message
    # would read as a loop. Beside the parent check, before the account is
    # built.
    if data.parent_id is not None:
        depth = chain_depth(db, Account, data.parent_id) + 1
        if depth > MAX_DEPTH:
            raise HTTPException(status_code=400, detail=depth_message(data.name, depth))
    _check_pinned_parent_base(
        db.get(Account, data.parent_id) if data.parent_id else None, data.account_type
    )
    _reject_reserved_number(data.account_number, data.name, data.account_type)
    account = Account(**data.model_dump())
    # Restoring a SEEDED control account (the guard above let it through as
    # the registry's name and type) brings it back is_system=True, as the seed
    # creates it — otherwise the same account differs from a fresh company's.
    # 4800 and 5900 are not seeded, so they restore is_system=False, unchanged.
    if (
        control_accounts.is_control(account.account_number, account.account_type)
        and account.account_number in control_accounts.seeded_numbers()
    ):
        account.is_system = True
    db.add(account)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Account violates a uniqueness or reference constraint.",
        )
    db.refresh(account)
    return account


@router.put("/{account_id}", response_model=AccountResponse)
def update_account(account_id: int, data: AccountUpdate, db: Session = Depends(get_db)):
    account = get_or_404(db, Account, account_id)
    fields = reject_null_updates(Account, data.model_dump(exclude_unset=True))
    # A full-object PUT resends the parent it has: that is no move, and must
    # not set off the sub-account checks on a pair saved before (#416)
    if "parent_id" in fields and fields["parent_id"] == account.parent_id:
        del fields["parent_id"]
    moved = "parent_id" in fields

    # Issue #119: the posting code resolves these accounts BY NUMBER, so
    # renumbering one makes every later document skip its journal entry —
    # silently, with a trial balance that still balances. Renaming is fine
    # and stays allowed; only the number and the type are load-bearing.
    # (Not gated on is_system: every seeded account carries that flag, and
    # renumbering an ordinary expense account is a reasonable request.)
    if control_accounts.is_control(account.account_number, account.account_type):
        name, purpose = control_accounts.describe(account.account_number)
        for field, what in (("account_number", "number"), ("account_type", "type")):
            if field in fields and fields[field] != getattr(account, field):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{account.account_number} {name} is a control account — "
                        f"the software finds it by its number to post {purpose}. "
                        f"Changing its {what} would stop new documents reaching "
                        f"the ledger. You can rename it."
                    ),
                )

    if "account_number" in fields:
        # Checked only when it changes: an account an importer brought in
        # with its own number (or none) can still be renamed from the form.
        if fields["account_number"] != account.account_number:
            _check_account_number(fields["account_number"])
            # Renumbering an ordinary account onto a control-account slot is
            # the same trap as creating one there — it would quietly become
            # where that posting path resolves. (The account that already
            # holds a control number is handled above; this is a DIFFERENT
            # account moving onto one.)
            # The account's own name and type go with it, so restoring the
            # control account itself into its empty slot is allowed.
            _reject_reserved_number(
                fields["account_number"],
                fields.get("name", account.name),
                fields.get("account_type", account.account_type),
            )
        _reject_duplicate_number(db, fields["account_number"], exclude_id=account_id)
    if fields.get("parent_id") == account_id:
        raise HTTPException(
            status_code=400, detail="An account cannot be its own parent."
        )
    if fields.get("parent_id") is not None:
        _check_parent_usable(db, fields["parent_id"])
        # Refuse past MAX_DEPTH before the loop guard (#416), so loop_message
        # is only ever a real loop and never a too-deep chain.
        depth = chain_depth(db, Account, fields["parent_id"]) + 1
        if depth > MAX_DEPTH:
            raise HTTPException(
                status_code=400,
                detail=depth_message(fields.get("name", account.name), depth),
            )
        # Testing #101: A under B, then B under A, was accepted, and every
        # walk up the tree went round forever.
        through = loop_through(db, account_id, fields["parent_id"])
        if through is not None:
            raise HTTPException(
                status_code=400,
                detail=loop_message(fields.get("name", account.name), through.name),
            )
    if "bank_kind" in fields or "account_type" in fields:
        _check_bank_kind(
            fields.get("bank_kind", account.bank_kind),
            fields.get("account_type", account.account_type),
        )
    _check_bank_kind_kept(db, account, fields)
    retyped_subs = _update_detail_type(db, account, fields)
    # 1100, 2000 and the other control accounts are found by number and
    # stand alone: they take no new parent (#416); out from under one they
    # have is still allowed. After the detail-type checks, whose sentence
    # says more under a plain parent.
    if fields.get("parent_id") is not None and takes_no_new_parent(
        account.detail_type, account.account_number, account.account_type
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{_named(account)} is found by its number when the books "
                f"post, so it stays a top-level account."
            ),
        )
    # only on a move or a real type change: the form sends the type on
    # every save, and a chart imported with a mixed pair stays editable
    atype = fields.get("account_type", account.account_type)
    retyped = getattr(atype, "value", atype) != account.account_type.value
    if moved or retyped:
        parent_id = fields.get("parent_id", account.parent_id)
        _check_pinned_parent_base(
            db.get(Account, parent_id) if parent_id else None,
            atype,
            None if moved else account,
        )
    if retyped:
        _check_children_base(account, atype)

    for key, val in fields.items():
        setattr(account, key, val)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Update violates a uniqueness or reference constraint.",
        )
    db.refresh(account)
    # name the sub-accounts the cascade retyped (#416); None on a PUT that
    # did not cascade, so a client can tell the two apart
    response = AccountResponse.model_validate(account)
    if retyped_subs:
        response.retyped_sub_accounts = [RetypedSubAccount(**r) for r in retyped_subs]
    return response


def _referencing_rows(db: Session, account_id: int) -> list[tuple[str, int]]:
    """[(table, count)] for every row in the database pointing at this
    account, derived from the schema rather than a hand-kept list.

    Thirty-five columns across seventeen models reference `accounts.id`, and
    that number grows with every feature — vendor credits added one this
    week. A list maintained by hand would be wrong within a release, and the
    symptom of it being wrong is a foreign-key violation surfacing as a 500.
    Walking the metadata cannot fall behind the schema.
    """
    from app.database import Base

    found: list[tuple[str, int]] = []
    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            if fk.column.table.name != "accounts":
                continue
            n = (
                db.query(func.count())
                .select_from(table)
                .filter(fk.parent == account_id)
                .scalar()
            ) or 0
            if n:
                found.append((table.name.replace("_", " "), n))
    return found


@router.delete("/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    account = get_or_404(db, Account, account_id)

    # Gated on the control-account registry, NOT on is_system.
    #
    # Every one of the 57 accounts a new company is seeded with carries
    # is_system, so gating on it refused all of them — which left an operator
    # with someone else's chart of accounts and no way to shrink it: delete
    # refused, and there was no deactivate control on the page either. That
    # is the same wrong flag 2.10.2 found hiding the Edit button, in a second
    # place (issue #139, reported by tresero coming from hledger).
    #
    # Sixteen numbers genuinely cannot go: the posting code resolves them
    # literally, and a document that cannot find one is #119. Those can be
    # renamed, not removed. The other forty-two are ordinary accounts. Gated
    # on number AND type: an imported 3900 expense is not Opening Balance
    # Equity, so it deletes like any ordinary account.
    if control_accounts.is_control(account.account_number, account.account_type):
        name, purpose = control_accounts.describe(account.account_number)
        raise HTTPException(
            status_code=400,
            detail=(
                f"{account.account_number} {name} is a control account — the "
                f"software posts to it by number for {purpose}, so it cannot "
                f"be deleted. You can rename it, or deactivate it to hide it "
                f"from new entries."
            ),
        )

    # An account carrying ledger history must not be deleted — the postings
    # would lose their anchor. Refusing is correct; the bug was that the
    # refusal arrived as a raw foreign-key violation (HTTP 500) instead of a
    # business rule the caller can act on.
    posted = (
        db.query(TransactionLine)
        .filter(TransactionLine.account_id == account_id)
        .count()
    )
    if posted:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{account.name}' has {posted} posted transaction line(s) and "
                f"cannot be deleted. Deactivate it instead (set is_active=false) "
                f"to hide it from new entries while preserving history."
            ),
        )

    # Anything else still pointing at it — an item's income account, a
    # vendor's default expense account, a bank feed, a budget line. Named,
    # because "referenced by other records" tells the operator nothing about
    # where to go and undo it.
    others = [
        (t, n) for t, n in _referencing_rows(db, account_id) if t != "transaction lines"
    ]
    if others:
        where = ", ".join(f"{n} in {t}" for t, n in others)
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{account.name}' is still in use: {where}. Point those at "
                f"another account first, or deactivate this one to hide it "
                f"from new entries."
            ),
        )

    db.delete(account)
    try:
        db.commit()
    except IntegrityError:
        # Any of the other tables referencing accounts.id — belt and braces so
        # no constraint can ever leak as a 500 from this endpoint again.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{account.name}' is still referenced by other records and "
                f"cannot be deleted. Deactivate it instead."
            ),
        )
    return {"message": "Account deleted"}
