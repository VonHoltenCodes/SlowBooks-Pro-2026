# ============================================================================
# Manual Journal Entries — CRUD for hand-entered journal entries
# Feature: Allow users to create/view/void manual journal entries
# ============================================================================

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.transactions import BASIS_BOTH, Transaction
from app.models.accounts import Account
from app.models.qbo_mapping import QBOMapping
from app.models.users import ROLE_ACCOUNTANT
from app.routes._roles import current_role
from app.schemas.journal import (
    JournalEntryCreate,
    JournalEntryResponse,
    JournalEntryUpdate,
)
from app.services.accounting import (
    _q,
    all_lines,
    create_journal_entry,
    reversing_lines,
)
from app.services.audit import log_event
from app.services.bank_posting import (
    assert_not_reconciled,
    reconciled_on,
    release_statement_links,
    replace_journal_lines,
    uncleared_note,
    uncleared_rows,
)
from app.services.bank_register import date_text
from app.services.closing_date import check_closing_date
from app.services.job_costing import line_cost_type
from app.services.safe_errors import safe_message
from app.services.entry_text import header_text

router = APIRouter(prefix="/api/journal", tags=["journal"])


def _journal_sources() -> list[str]:
    """The source types the page lists: entries that are journals in their
    own right, with no document behind them. Hand-typed ones, the
    QuickBooks Online import's journals, the lists IIF's opening balances
    (and its GENERAL JOURNAL blocks), the Transaction Detail by Account
    import, Migrate Data's sources, and the Opening Balances page's."""
    from app.routes.migration import SOURCES
    from app.services.qb_ledger_import import SOURCE_TYPE as QB_LEDGER

    from app.services.accountants_copy import SOURCE_TYPE as ACCOUNTANTS_COPY

    return [
        "manual",
        "qbo_journal",
        "iif_import",
        QB_LEDGER,
        "opening_balance",
        # an item's opening stock (DR Inventory / CR Opening Balance Equity):
        # shown here for the record, but never voidable — _voidable refuses
        # it, so its stock and GL can't drift apart (use an adjustment).
        "item_opening",
        # an accountant's entry applied from their copy (#346): a journal
        # of its own on the page, voidable like any other
        ACCOUNTANTS_COPY,
        *[source_type for _, _, source_type in SOURCES.values()],
    ]


# Documents whose own Void reverses their posting: the journal's void route
# sends a caller there instead of reversing the posting a second time
# (#661). Imported checks and late fees have no document of their own and
# are still voided here (Possible Duplicates voids a check this way).
_DOCUMENTS_WITH_A_VOID = frozenset(
    {
        "invoice",
        "invoice_edit",
        "payment",
        "bill",
        "bill_edit",
        "bill_payment",
        "expense",
        "credit_memo",
        "vendor_credit",
        "deposit",
        "bank_entry",
        "transfer",
        "cc_charge",
        "in_kind_gift",
        "job_deposit",
        "functional_allocation",
        "restriction_release",
    }
)


def _voidable(txn, voided: bool) -> bool:
    """Whether the page may offer Void (the route decides for real): a
    journal of its own that is not a reversal and not voided already. An
    imported entry (the lists IIF's opening balances, a Transaction Detail
    by Account journal, a migration's) is one; until now the page offered
    Void on hand-typed and QuickBooks Online entries only, so a wrong
    opening-balance import had no way out but the API (a CPA on X, 2.23.5)."""
    source = txn.source_type or ""
    if voided or source.endswith("_void"):
        return False
    # An item's opening stock is listed but not voidable: a reversal would
    # take the value off the books while the movement, qty and avg cost
    # stayed, so valuation and GL would disagree. Correct it with an
    # inventory adjustment instead; the route refuses the void too.
    if source == "item_opening":
        return False
    return source in _journal_sources() or source == "qbo_ledger"


# A journal entry opens for editing from the register (#388): a hand-typed
# one that is not voided. Imported entries keep to their own correction
# paths; a document's posting edits from its document.
_EDITABLE_SOURCES = ("manual",)


def _je_editable(txn, voided: bool, role: str = None) -> bool:
    # The accountant's sign-in posts and voids journal entries but does not
    # edit them (#340, #388): the middleware refuses the PUT, so the GET and
    # the register say so too, and no Edit is offered.
    if role == ROLE_ACCOUNTANT:
        return False
    return (txn.source_type or "") in _EDITABLE_SOURCES and not voided


def _reconciled_sentence(recon) -> str:
    """What the edit form says as it opens a journal entry a completed
    reconciliation holds (#388): the reconciled line's amount, account and
    the date are locked; only the lines the statement does not hold, and
    the memo, reference, class and job, can be corrected."""
    return (
        f"This entry was reconciled on the {date_text(recon.statement_date)} "
        "statement, so its amount and date stay as they are, and the "
        "reconciled line keeps its account (a reconciled entry cannot be "
        "voided either). Its memo, reference, class, job and the other "
        "lines' accounts can still be corrected."
    )


def _line_dict(line, acct) -> dict:
    """One posted line with its account and every dimension, for the
    list and single-entry responses alike."""
    return {
        "id": line.id,
        "account_id": line.account_id,
        "account_name": acct.name if acct else "",
        "account_number": (acct.account_number or "") if acct else "",
        "debit": float(line.debit),
        "credit": float(line.credit),
        "description": line.description or "",
        "job_id": line.job_id,
        "class_id": line.class_id,
        "cost_code_id": line.cost_code_id,
        "cost_type": line.cost_type,
        "function": line.function,
        "is_billable": bool(line.is_billable),
    }


def _basis_of(lines) -> str:
    """The basis an entry's lines share (#483); both when they differ or
    there are none (a document's mixed entry is not edited here)."""
    bases = {ln.basis for ln in lines}
    return bases.pop() if len(bases) == 1 else BASIS_BOTH


def _line_dims_offered(db: Session) -> dict:
    """Which line dimensions the journal form has a picker for, as
    journal.js decides it: a line class where the company is nonprofit or
    has a class of its own (Nonprofit.lineClassShown), a cost code where
    there is one (CostCodes.any)."""
    from app.models.classes import TxnClass
    from app.models.cost_codes import CostCode
    from app.services.settings_service import is_nonprofit

    classes = is_nonprofit(db) or (
        db.query(TxnClass.id)
        .filter(TxnClass.is_system_default.is_(False), TxnClass.is_archived.is_(False))
        .first()
        is not None
    )
    codes = db.query(CostCode.id).filter(CostCode.is_active.is_(True)).first()
    return {"class_id": classes, "cost_code_id": codes is not None}


def _journal_lines(
    db: Session, data, edit: bool = False, basis: str = BASIS_BOTH
) -> list[dict]:
    """The posted lines from the form's fields, the same for a new entry
    and an edit: a zero line dropped, an account checked to exist, each
    line's own description, job, class, cost code, billable flag and
    (nonprofit) function carried through."""
    # On an edit, a null class or cost code for a dimension the form has no
    # picker for is not a choice the client made: the line keeps what it
    # has (replace_journal_lines' carry-over), so a business company's memo
    # fix does not strip an imported line's class (#388 review).
    unmanaged = (
        {key for key, shown in _line_dims_offered(db).items() if not shown}
        if edit
        else set()
    )
    lines = []
    for line in data.lines:
        if line.debit == 0 and line.credit == 0:
            continue
        acct = db.query(Account).filter(Account.id == line.account_id).first()
        if not acct:
            raise HTTPException(
                status_code=404, detail=f"Account {line.account_id} not found"
            )
        row = {
            "account_id": line.account_id,
            "debit": Decimal(str(line.debit)),
            "credit": Decimal(str(line.credit)),
            "description": line.description or "",
            # the entry's basis applies to every line (#483)
            "basis": basis,
        }
        # class, job, cost code and the billable flag are carried only when
        # the request names them (the `function` trick): on an edit, a field
        # the body leaves out keeps what the line has, so a memo fix from a
        # client that sends bare lines doesn't clear a line's class, job,
        # cost code or billable mark (#388 review).
        for key in (
            "class_id",
            "job_id",
            "cost_code_id",
            "cost_type",
            "is_billable",
            "function",
        ):
            if key in line.model_fields_set:
                value = getattr(line, key)
                if value is None and key in unmanaged:
                    continue
                row[key] = value
        # Validate a sent cost type against cost_types (and fill from the code
        # when blank), the same check the other cost forms run (#550).
        if "cost_type" in row:
            row["cost_type"] = line_cost_type(
                db, row.get("cost_code_id"), row["cost_type"]
            )
        lines.append(row)
    return lines


def _acting(db: Session) -> str:
    """The sign-in making this request, as the audit log names it."""
    return db.info.get("acting_username") or "operator"


def _role(request: Optional[Request]) -> str:
    return current_role(request) if request is not None else "admin"


@router.get("", response_model=list[JournalEntryResponse])
def list_journal_entries(
    source_type: str = None,
    adjusting: Optional[bool] = Query(
        default=None,
        description="true for adjusting entries only, false for the rest (#341); "
        "omit for every entry",
    ),
    db: Session = Depends(get_db),
):
    q = db.query(Transaction)
    if adjusting is not None:
        q = q.filter(Transaction.is_adjusting == adjusting)
    if source_type:
        q = q.filter(Transaction.source_type == source_type)
    else:
        # Older report imports stored journals as qbo_ledger. Show those
        # immediately, even before the direct JournalEntry import is rerun.
        report_journals = db.query(QBOMapping.slowbooks_id).filter(
            QBOMapping.entity_type == "ledger",
            or_(
                QBOMapping.qbo_id.like("Journal Entry:%"),
                QBOMapping.qbo_id.like("General Journal:%"),
                QBOMapping.qbo_id.like("JournalEntry:%"),
                QBOMapping.qbo_id.like("Journal:%"),
            ),
        )
        # The journals a migration posted (Migrate Data's sources, the IIF
        # opening balances) are journal entries too, and list here (2.23.2,
        # NEW-118); their Type names the source. So do the Opening Balances
        # page's.
        q = q.filter(
            or_(
                Transaction.source_type.in_(_journal_sources()),
                (Transaction.source_type == "qbo_ledger")
                & Transaction.id.in_(report_journals),
            )
        )
    entries = q.order_by(Transaction.date.desc()).all()
    accounts = {a.id: a for a in db.query(Account).all()}
    from app.services.qbo_documents import REVERSALS

    voided = {
        source_id
        for (source_id,) in db.query(Transaction.source_id).filter(
            Transaction.source_type.in_(REVERSALS), Transaction.source_id.isnot(None)
        )
    }
    results = []
    for txn in entries:
        txn_lines = all_lines(db, txn.id)
        lines_data = []
        for line in txn_lines:
            lines_data.append(_line_dict(line, accounts.get(line.account_id)))
        results.append(
            JournalEntryResponse(
                id=txn.id,
                date=txn.date,
                description=header_text(txn),
                reference=txn.reference or "",
                source_type=txn.source_type or "",
                source_kind=txn.source_kind or "",
                is_adjusting=bool(txn.is_adjusting),
                posted_by=txn.posted_by or "",
                basis=_basis_of(txn_lines),
                lines=lines_data,
                total_debit=float(sum(line.debit for line in txn_lines)),
                total_credit=float(sum(line.credit for line in txn_lines)),
                voided=txn.id in voided,
                voidable=_voidable(txn, txn.id in voided),
            )
        )
    return results


@router.get("/{entry_id}", response_model=JournalEntryResponse)
def get_journal_entry(
    entry_id: int, db: Session = Depends(get_db), request: Request = None
):
    txn = db.query(Transaction).filter(Transaction.id == entry_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    from app.services.qbo_documents import reversed_already

    accounts = {a.id: a for a in db.query(Account).all()}
    txn_lines = all_lines(db, txn.id)
    lines_data = []
    for line in txn_lines:
        lines_data.append(_line_dict(line, accounts.get(line.account_id)))
    voided = reversed_already(db, txn)
    recon = reconciled_on(db, txn)
    # A bank entry is edited in its register, not here: the page says so
    # and links back to the bank or card account it was posted from (the
    # line on an account with a bank kind; the entry's other lines are its
    # categories).
    register = next(
        (
            accounts[line.account_id]
            for line in txn_lines
            if txn.source_type == "bank_entry"
            and accounts.get(line.account_id) is not None
            and accounts[line.account_id].bank_kind
        ),
        None,
    )
    return JournalEntryResponse(
        id=txn.id,
        date=txn.date,
        description=header_text(txn),
        reference=txn.reference or "",
        class_id=txn.class_id,
        job_id=txn.job_id,
        source_type=txn.source_type or "",
        source_kind=txn.source_kind or "",
        is_adjusting=bool(txn.is_adjusting),
        posted_by=txn.posted_by or "",
        basis=_basis_of(txn_lines),
        lines=lines_data,
        total_debit=float(sum(line.debit for line in txn_lines)),
        total_credit=float(sum(line.credit for line in txn_lines)),
        voided=voided,
        voidable=_voidable(txn, voided),
        editable=_je_editable(txn, voided, _role(request)),
        reconciled_on=recon.statement_date if recon else None,
        reconciled_note=_reconciled_sentence(recon) if recon else None,
        register_account_id=register.id if register is not None else None,
        register_account_name=register.name if register is not None else "",
    )


@router.post("", response_model=JournalEntryResponse, status_code=201)
def create_manual_journal_entry(
    data: JournalEntryCreate, db: Session = Depends(get_db), request: Request = None
):
    check_closing_date(db, data.date)
    # in an accountant's copy, the entry must be on or before the dividing
    # date (#346)
    from app.services.accountants_copy import check_entry_date

    check_entry_date(db, data.date)
    # An accountant's entries are adjustments unless the box is cleared;
    # everyone else's are ordinary unless it is ticked (#341).
    is_adjusting = (
        data.is_adjusting
        if data.is_adjusting is not None
        else _role(request) == ROLE_ACCOUNTANT
    )
    lines = _journal_lines(db, data, basis=data.basis or BASIS_BOTH)
    if not lines:
        raise HTTPException(status_code=400, detail="No valid lines")

    try:
        txn = create_journal_entry(
            db,
            data.date,
            data.description,
            lines,
            source_type="manual",
            reference=data.reference or "",
            class_id=data.class_id,
            job_id=data.job_id,
            is_adjusting=is_adjusting,
            posted_by=_acting(db),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.commit()
    db.refresh(txn)
    return get_journal_entry(txn.id, db, request)


def _triples(rows) -> list:
    """Each line as (account_id, debit, credit) — enough to tell a sign
    flip (DR↔CR on the same account) and an account move apart."""
    return sorted(
        (
            r["account_id"] if isinstance(r, dict) else r.account_id,
            _q((r["debit"] if isinstance(r, dict) else r.debit) or 0),
            _q((r["credit"] if isinstance(r, dict) else r.credit) or 0),
        )
        for r in rows
    )


def _recon_triples(lines) -> list:
    """The (account_id, debit, credit) of each line a completed
    reconciliation holds — each has to stay on its account for the same
    amount for the statement to still tie (a bare amount check would pass a
    sign flip, which moves the account's balance)."""
    return _triples([ln for ln in lines if ln.reconciliation_id])


def _in_multiset(subset: list, pool: list) -> bool:
    """Every item of `subset` is present in `pool`, counting repeats."""
    rest = list(pool)
    for item in subset:
        if item not in rest:
            return False
        rest.remove(item)
    return True


@router.put("/{entry_id}", response_model=JournalEntryResponse)
def update_journal_entry(
    entry_id: int,
    data: JournalEntryUpdate,
    db: Session = Depends(get_db),
    request: Request = None,
):
    """Rewrite a hand-entered journal entry in place (#388), opened from
    the register — the same rules the bank-entry edit follows. The lines
    are replaced the way a bill's are on edit (`replace_journal_lines`): the
    same journal entry, so its attachments, statement matches and audit
    trail stay with it. An entry a completed reconciliation holds keeps its
    amount and date (a change is refused with 400, as its void is); a memo,
    reference, class or job fix goes through; a change that moves an amount
    to a different account in that period is refused with 409 and the
    statement date until `confirm_reconciled`, then audited as such."""
    from app.services.qbo_documents import reversed_already

    txn = db.query(Transaction).filter(Transaction.id == entry_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    if reversed_already(db, txn):
        raise HTTPException(
            status_code=400,
            detail="This entry is void; a void entry is not edited. Enter it again.",
        )
    if not _je_editable(txn, False):
        raise HTTPException(
            status_code=400,
            detail="Only a hand-entered journal entry can be edited here.",
        )
    # The accountant's sign-in never reaches here: the middleware admits
    # only POST /api/journal and the void on this router, so PUT is 403'd
    # before the route (#340). Its GET and the register say editable is
    # false for the accountant, so no Edit is offered in the first place.
    old_lines = all_lines(db, txn.id)
    lines = _journal_lines(
        db, data, edit=True, basis=data.basis or _basis_of(old_lines)
    )
    if len(lines) < 2:
        raise HTTPException(status_code=400, detail="At least 2 lines required")
    check_closing_date(db, txn.date)
    check_closing_date(db, data.date)

    recon = reconciled_on(db, txn)
    moves = False
    if recon is not None:
        recon_triples = _recon_triples(old_lines)
        new_triples = _triples(lines)
        # every reconciled line must still be there, on its own account for
        # its own amount, on the same day: a new amount or date — a sign
        # flip among them — would take the entry off that statement, as a
        # void would
        if data.date != txn.date or not _in_multiset(recon_triples, new_triples):
            raise HTTPException(
                status_code=400,
                detail=f"This entry was reconciled on the "
                f"{date_text(recon.statement_date)} statement, so its amount "
                "and date cannot be changed, just as it cannot be voided. "
                "Correct the difference with a new entry.",
            )
        # the reconciled lines are intact; only a line the statement does
        # not hold may have moved account — that still shifts an amount
        # between accounts in a reconciled period, so ask first (a pure
        # memo, reference, class or job fix leaves every account alone and
        # goes straight through)
        rest = list(new_triples)
        for t in recon_triples:
            rest.remove(t)
        old_rest = _triples([ln for ln in old_lines if not ln.reconciliation_id])
        moves = sorted(rest) != old_rest
        if moves and not data.confirm_reconciled:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "reconciled",
                    "message": f"This entry was reconciled on the "
                    f"{date_text(recon.statement_date)} statement. Changing an "
                    "account moves its amount between accounts in a period "
                    "already reconciled.",
                    "statement_date": recon.statement_date.isoformat(),
                },
            )

    is_adjusting = (
        data.is_adjusting if data.is_adjusting is not None else bool(txn.is_adjusting)
    )
    try:
        uncleared = replace_journal_lines(
            db,
            txn,
            data.date,
            data.description,
            lines,
            source_type=txn.source_type or "manual",
            reference=data.reference or "",
            class_id=data.class_id,
            job_id=data.job_id,
            is_adjusting=is_adjusting,
            posted_by=txn.posted_by or _acting(db),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if recon is not None and moves:
        # the move within a reconciled period says so in the trail, beside
        # the DELETE/INSERT rows the lines wrote (#388, as the bank edit does)
        log_event(
            db,
            "transactions",
            txn.id,
            "UPDATE",
            old_values={"reconciled_on": recon.statement_date.isoformat()},
            new_values={
                "confirm_reconciled": True,
                "reconciliation_id": recon.id,
                "lines_uncleared": len(uncleared),
            },
            changed_fields=["confirm_reconciled"],
        )
    db.commit()
    db.refresh(txn)
    out = get_journal_entry(txn.id, db, request)
    out.uncleared = uncleared_rows(uncleared)
    out.note = uncleared_note(uncleared)
    return out


def assert_voidable_kind(txn: Transaction) -> None:
    """Refuse, with the journal's Void's own answer, an entry that is not
    the journal's to reverse by what it is: a reversal, a pay run's payroll
    entry, an item's opening stock and, last, a document's posting
    (_DOCUMENTS_WITH_A_VOID, #661), in the order the route always asked."""
    if txn.source_type and txn.source_type.endswith("_void"):
        raise HTTPException(status_code=400, detail="Cannot void a reversal entry")
    if txn.source_type == "payroll":
        # A pay run's posting is reversed by voiding the run (#432), which
        # also frees its time entries, backs out YTD and marks the stubs.
        # Reversing it here too would take wages, liabilities and cash off
        # a second time.
        raise HTTPException(
            status_code=400,
            detail="This is a pay run's payroll entry. Void the pay run "
            "instead (Payroll, open the run, Void).",
        )
    if txn.source_type == "item_opening":
        # Voiding would remove the value but leave the movement, quantity and
        # average cost, so valuation would say stock the GL no longer shows.
        raise HTTPException(
            status_code=400,
            detail="An item's opening stock can't be voided here; correct it "
            "with an inventory adjustment on the item.",
        )
    if (txn.source_type or "") in _DOCUMENTS_WITH_A_VOID:
        # A document's posting is voided with the document, whose own void
        # does the whole job. Most write the reversal under the document's
        # id, so the posting still looked unreversed below and a void here
        # took it off the books a second time (#661). The page never offers
        # Void on one.
        from app.services.reclassify import source_label

        label = source_label(txn.source_type).lower()
        # which one: an invoice's posting carries the invoice's own number
        # ("invoice 1001"); any other reference is what was typed in Ref #,
        # a check number as often as not, so it is said as one (2.31.2 gate)
        ref = (txn.reference or "").strip()
        if ref and (txn.source_type or "") in ("invoice", "invoice_edit"):
            what = which = f"{label} {ref}"
            said = ""
        else:
            what = ("an " if label[:1] in "aeiou" else "a ") + label
            which = f"the {label}"
            said = f" (Ref # {ref})" if ref else ""
        raise HTTPException(
            status_code=400,
            detail=f"This is {what}'s posting{said}, not a journal entry of its "
            f"own. Void {which} itself; that reverses this posting with it.",
        )


def assert_not_voided(db: Session, txn: Transaction) -> None:
    """Refuse, as the journal's Void does, an entry already reversed."""
    from app.services import qbo_documents

    if qbo_documents.reversed_already(db, txn):
        raise HTTPException(
            status_code=400, detail="This journal entry has already been voided."
        )


def reverse_journal_entry(
    db: Session,
    txn: Transaction,
    *,
    acting: str | None = None,
    source_type: str = "manual_void",
) -> Transaction:
    """A journal entry's void: its mirror image on its own date (every line,
    whatever its basis), the description and reference it had, an
    adjustment's reversal an adjustment, and its bank lines handed back to
    the review queue. The caller commits.

    The journal's Void, and the ledger import replacing a partial entry
    with more of its transaction from a later file (#663). Whoever calls
    it, it refuses what Void refuses, with Void's answers (#663): a
    reversal, a pay run's payroll entry, an item's opening stock, a
    document's posting, an entry voided already, one on a completed
    reconciliation and one in a closed period. The route asks the same first, in its own order (who may void
    sits between them), so its answers are as they were."""
    assert_voidable_kind(txn)
    assert_not_voided(db, txn)
    assert_not_reconciled(txn)
    check_closing_date(db, txn.date)
    void_txn = create_journal_entry(
        db,
        txn.date,
        # the reversal of an adjustment says so, and is one (#341)
        ("VOID adjusting entry: " if txn.is_adjusting else "VOID: ") + header_text(txn),
        reversing_lines(all_lines(db, txn.id)),
        source_type=source_type,
        source_id=txn.id,
        reference=txn.reference,
        class_id=txn.class_id,
        job_id=txn.job_id,
        is_adjusting=bool(txn.is_adjusting),
        posted_by=acting,
    )
    release_statement_links(db, txn)
    return void_txn


@router.post("/{entry_id}/void", response_model=JournalEntryResponse)
def void_journal_entry(
    entry_id: int, db: Session = Depends(get_db), request: Request = None
):
    txn = db.query(Transaction).filter(Transaction.id == entry_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    assert_voidable_kind(txn)
    acting = _acting(db)
    if _role(request) == ROLE_ACCOUNTANT and (txn.posted_by or "") != acting:
        # An accountant voids only what it posted (#340): the owner's
        # entries, and a document's posting, are not its to reverse.
        raise HTTPException(
            status_code=403,
            detail="An accountant's sign-in voids only the journal entries it "
            "posted; this one was posted by " + (txn.posted_by or "someone else") + ".",
        )
    from app.models.qbo_mapping import QBOMapping
    from app.services import qbo_documents

    assert_not_voided(db, txn)
    from_qbo = txn.source_type in {"qbo_ledger", "qbo_journal"}
    if from_qbo:
        # The QuickBooks Online import's posting of an invoice, sales receipt
        # or payment: voiding it voids the document, as voiding the document
        # would (its posting is reversed with it), so the two stay together.
        found = qbo_documents.document_of_posting(db, txn)
        if found is not None:
            from app.models.invoices import InvoiceStatus
            from app.routes.invoices.lifecycle import void_invoice
            from app.routes.payments import void_payment

            kind, document = found
            if kind == "payment":
                void_payment(document.id, db=db)
            else:
                if kind == "sales_receipt":
                    # Its payment's void voids the receipt too.
                    for alloc in list(document.payment_allocations):
                        if alloc.payment is not None and not alloc.payment.is_voided:
                            void_payment(alloc.payment_id, db=db)
                    db.refresh(document)
                if document.status != InvoiceStatus.VOID:
                    void_invoice(document.id, db)
            reversal = (
                db.query(Transaction)
                .filter(
                    Transaction.source_type == f"{txn.source_type}_void",
                    Transaction.source_id == txn.id,
                )
                .first()
            )
            return get_journal_entry(reversal.id if reversal else txn.id, db)

    assert_not_reconciled(txn)
    check_closing_date(db, txn.date)

    if all_lines(db, txn.id):
        try:
            void_txn = reverse_journal_entry(
                db,
                txn,
                acting=acting,
                # A QuickBooks Online import posting is reversed under its own
                # name, so a later import knows it was voided here.
                source_type=f"{txn.source_type}_void" if from_qbo else "manual_void",
            )
            if from_qbo:
                qbo_documents.mark_changed_here(
                    db,
                    *db.query(QBOMapping).filter(
                        QBOMapping.entity_type.in_(("ledger", "journal_entry")),
                        QBOMapping.slowbooks_id == txn.id,
                    ),
                )
            db.commit()
        except (ValueError, StatementError) as e:
            # A reversal that cannot be posted is a problem with the data
            # (out of balance, a closed period, a value the database
            # refuses), never a server fault: roll back so the entry is
            # left exactly as it was and answer 4xx with a sentence
            # instead of a bare 500.
            db.rollback()
            status = 400 if isinstance(e, ValueError) else 422
            raise HTTPException(
                status_code=status,
                detail="This journal entry could not be voided: "
                + safe_message(e, "void journal entry"),
            ) from e
        db.refresh(void_txn)
        return get_journal_entry(void_txn.id, db)

    raise HTTPException(status_code=400, detail="No lines to reverse")
