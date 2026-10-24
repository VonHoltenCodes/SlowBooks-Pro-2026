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
from app.models.transactions import Transaction
from app.models.accounts import Account
from app.models.qbo_mapping import QBOMapping
from app.models.users import ROLE_ACCOUNTANT
from app.routes._roles import current_role
from app.schemas.journal import JournalEntryCreate, JournalEntryResponse
from app.services.accounting import create_journal_entry, reversing_lines
from app.services.bank_posting import assert_not_reconciled, release_statement_links
from app.services.closing_date import check_closing_date
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
        "function": line.function,
        "is_billable": bool(line.is_billable),
    }


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
        lines_data = []
        for line in txn.lines:
            lines_data.append(_line_dict(line, accounts.get(line.account_id)))
        results.append(
            JournalEntryResponse(
                id=txn.id,
                date=txn.date,
                description=header_text(txn),
                reference=txn.reference or "",
                source_type=txn.source_type or "",
                is_adjusting=bool(txn.is_adjusting),
                posted_by=txn.posted_by or "",
                lines=lines_data,
                total_debit=float(sum(line.debit for line in txn.lines)),
                total_credit=float(sum(line.credit for line in txn.lines)),
                voided=txn.id in voided,
                voidable=_voidable(txn, txn.id in voided),
            )
        )
    return results


@router.get("/{entry_id}", response_model=JournalEntryResponse)
def get_journal_entry(entry_id: int, db: Session = Depends(get_db)):
    txn = db.query(Transaction).filter(Transaction.id == entry_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    from app.services.qbo_documents import reversed_already

    accounts = {a.id: a for a in db.query(Account).all()}
    lines_data = []
    for line in txn.lines:
        lines_data.append(_line_dict(line, accounts.get(line.account_id)))
    # A bank entry is edited in its register, not here: the page says so
    # and links back to the bank or card account it was posted from (the
    # line on an account with a bank kind; the entry's other lines are its
    # categories).
    register = next(
        (
            accounts[line.account_id]
            for line in txn.lines
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
        source_type=txn.source_type or "",
        is_adjusting=bool(txn.is_adjusting),
        posted_by=txn.posted_by or "",
        lines=lines_data,
        total_debit=float(sum(line.debit for line in txn.lines)),
        total_credit=float(sum(line.credit for line in txn.lines)),
        voided=reversed_already(db, txn),
        voidable=_voidable(txn, reversed_already(db, txn)),
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
    lines = []
    for line in data.lines:
        if line.debit == 0 and line.credit == 0:
            continue
        acct = db.query(Account).filter(Account.id == line.account_id).first()
        if not acct:
            raise HTTPException(
                status_code=404, detail=f"Account {line.account_id} not found"
            )
        lines.append(
            {
                "account_id": line.account_id,
                "debit": Decimal(str(line.debit)),
                "credit": Decimal(str(line.credit)),
                "description": line.description or "",
                "job_id": line.job_id,
                "class_id": line.class_id,
                "cost_code_id": line.cost_code_id,
                "is_billable": line.is_billable,
                **(
                    {"function": line.function}
                    if "function" in line.model_fields_set
                    else {}
                ),
            }
        )

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
    return get_journal_entry(txn.id, db)


@router.post("/{entry_id}/void", response_model=JournalEntryResponse)
def void_journal_entry(
    entry_id: int, db: Session = Depends(get_db), request: Request = None
):
    txn = db.query(Transaction).filter(Transaction.id == entry_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    if txn.source_type and txn.source_type.endswith("_void"):
        raise HTTPException(status_code=400, detail="Cannot void a reversal entry")
    if txn.source_type == "item_opening":
        # Voiding would remove the value but leave the movement, quantity and
        # average cost, so valuation would say stock the GL no longer shows.
        raise HTTPException(
            status_code=400,
            detail="An item's opening stock can't be voided here; correct it "
            "with an inventory adjustment on the item.",
        )
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

    if qbo_documents.reversed_already(db, txn):
        raise HTTPException(
            status_code=400, detail="This journal entry has already been voided."
        )
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
                void_payment(document.id, db)
            else:
                if kind == "sales_receipt":
                    # Its payment's void voids the receipt too.
                    for alloc in list(document.payment_allocations):
                        if alloc.payment is not None and not alloc.payment.is_voided:
                            void_payment(alloc.payment_id, db)
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

    reverse_lines = reversing_lines(txn.lines)
    if reverse_lines:
        try:
            void_txn = create_journal_entry(
                db,
                txn.date,
                # the reversal of an adjustment says so, and is one (#341)
                ("VOID adjusting entry: " if txn.is_adjusting else "VOID: ")
                + (txn.description or ""),
                reverse_lines,
                # A QuickBooks Online import posting is reversed under its own
                # name, so a later import knows it was voided here.
                source_type=f"{txn.source_type}_void" if from_qbo else "manual_void",
                source_id=txn.id,
                reference=txn.reference,
                class_id=txn.class_id,
                job_id=txn.job_id,
                is_adjusting=bool(txn.is_adjusting),
                posted_by=acting,
            )
            release_statement_links(db, txn)
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
