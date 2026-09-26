import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account
from app.services import control_accounts
from app.models.transactions import TransactionLine
from app.schemas.accounts import AccountCreate, AccountUpdate, AccountResponse
from app.routes._helpers import get_or_404

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
_ACCOUNT_NUMBER_RE = re.compile(r"^\d+(?:[.-]\d+)*$")


def _check_account_number(number) -> None:
    if not number:
        raise HTTPException(
            status_code=400,
            detail=(
                "Give the account a number, like 6150. A sub-account can use "
                "6150.1 or 6150-01."
            ),
        )
    if len(number) > 20 or not _ACCOUNT_NUMBER_RE.match(number):
        raise HTTPException(
            status_code=400,
            detail=(
                f'"{number[:40]}" is not an account number. Use digits, like '
                "6150; a sub-account can use 6150.1 or 6150-01."
            ),
        )


_BANK_KIND_FOR_TYPE = {"bank": "asset", "credit_card": "liability"}


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


@router.get("", response_model=list[AccountResponse])
def list_accounts(
    active_only: bool = False,
    account_type: str = None,
    bank: bool = False,
    db: Session = Depends(get_db),
):
    """`?bank=1` lists the bank and credit-card accounts (bank_kind set) —
    what the register, the transfer form and every paid-from / deposit-to
    picker use."""
    q = db.query(Account)
    if active_only:
        q = q.filter(Account.is_active)
    if account_type:
        q = q.filter(Account.account_type == account_type)
    if bank:
        q = q.filter(Account.bank_kind.isnot(None))
    return _in_company_words(db, q.order_by(Account.account_number).all())


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
    _check_account_number(data.account_number)
    _reject_duplicate_number(db, data.account_number)
    account = Account(**data.model_dump())
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
    fields = data.model_dump(exclude_unset=True)

    # Issue #119: the posting code resolves these accounts BY NUMBER, so
    # renumbering one makes every later document skip its journal entry —
    # silently, with a trial balance that still balances. Renaming is fine
    # and stays allowed; only the number and the type are load-bearing.
    # (Not gated on is_system: every seeded account carries that flag, and
    # renumbering an ordinary expense account is a reasonable request.)
    if control_accounts.is_control_number(account.account_number):
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
        _reject_duplicate_number(db, fields["account_number"], exclude_id=account_id)
    if fields.get("parent_id") == account_id:
        raise HTTPException(
            status_code=400, detail="An account cannot be its own parent."
        )
    if "bank_kind" in fields or "account_type" in fields:
        _check_bank_kind(
            fields.get("bank_kind", account.bank_kind),
            fields.get("account_type", account.account_type),
        )

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
    return account


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
    # Fifteen numbers genuinely cannot go: the posting code resolves them
    # literally, and a document that cannot find one is #119. Those can be
    # renamed, not removed. The other forty-two are ordinary accounts.
    if control_accounts.is_control_number(account.account_number):
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
