# ============================================================================
# Business logic behind the "Create Invoices" window. Auto-numbering
# lives in app/services/numbering.py.
# ============================================================================

from datetime import date
from decimal import Decimal
import logging

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.invoices import InvoiceStatus
from app.models.items import Item
from app.services.accounting import (
    compute_line_totals,
    _q,
)
from app.services.terminology import document_reference

ZERO_TOTAL_CODE = "zero_total"
CREDIT_LIMIT_CODE = "credit_limit"
MONEY_APPLIED_CODE = "money_applied"
logger = logging.getLogger(__name__)


def _zero_total_sentence(noun: str, action: str) -> str:
    return (
        f"This {noun} adds up to $0.00. Enter a rate on at least one line "
        f"before {action}."
    )


def refuse_zero_total(total, noun: str = "invoice", action: str = "saving it") -> None:
    """A sales document for nothing is refused before anything is written.

    The credit memo and recurring forms filled no price when an item was
    picked, so a $0.00 credit memo and a schedule that generated two $0.00
    invoices (which then sat on the dashboard as overdue) were saved without
    a word; an invoice with one blank line saved at $0.00 too (2.17.3
    exploratory, both QA agents). `noun` is the document as the user calls
    it ("invoice", "credit memo", "recurring pledge").

    This strict form has no way past it: a recurring schedule for nothing
    would bill $0.00 every period. A single document can be meant to be
    free — see confirm_zero_total."""
    if Decimal(str(total or 0)) <= 0:
        raise HTTPException(status_code=400, detail=_zero_total_sentence(noun, action))


def confirm_zero_total(
    total,
    allowed: bool,
    noun: str = "invoice",
    action: str = "saving it",
    question: str | None = None,
) -> None:
    """An invoice or credit memo for nothing is saved only when the person
    says it is meant to be: warranty work billed at no charge is a real
    invoice, a form that filled in no price was the accident (both QA
    reports: "refuse a zero-total document unless the user explicitly
    allows it"). Unless the request carries ``allow_zero_total: true`` it
    is refused with 409 and ``detail = {"code": "zero_total", "message",
    "question"}``: the page asks the question ("This invoice adds up to
    $0.00. Save it anyway?") and sends the same request again with the
    flag. Nothing is written by the refused request."""
    if allowed or Decimal(str(total or 0)) > 0:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": ZERO_TOTAL_CODE,
            "message": _zero_total_sentence(noun, action),
            "question": question or f"This {noun} adds up to $0.00. Save it anyway?",
        },
    )


def warn_or_refuse_oversell(db, lines, before=(), action: str = "sale") -> list[str]:
    """A sale of more of a stock item than is on hand (#642). The sale
    posts whatever the count is, as it always has (some businesses enter
    the receiving bill after the sale), and the count went below zero with
    nothing said at the point of sale. Now the sentences to carry back as
    ``warnings`` are returned ("Widget: 2 on hand; this sale takes it to
    -4."), one per item; an empty list when nothing is short. With
    Settings -> inventory_refuse_oversell on, the sale is refused instead
    with a 422 naming each item and how many short. ``before`` is the
    invoice's stored lines on an edit, so only what the edit adds counts.
    A vendor credit's return to the supplier is asked the same way, with
    ``action="credit"`` ("this credit takes it to -3.", W-74). Called
    before anything is written, so the refused request writes nothing."""
    from app.services.inventory_hooks import oversold_items

    return warn_or_refuse_shorts(db, oversold_items(db, lines, before), action)


def warn_or_refuse_shorts(db, shorts, action: str = "sale") -> list[str]:
    """The sentences for stock items already found short (oversold_items'
    shape), or the 422 when Settings says to refuse. A bill's void and edit
    find their shorts from the bill's receipts (oversold_by_reversal, W-67)
    and say "this void" or "this edit" in place of "this sale"; the refusal
    says what to do about that one."""
    from app.services.inventory_hooks import oversell_refusal, oversell_warning
    from app.services.settings_service import refuse_oversell

    if not shorts:
        return []
    if refuse_oversell(db):
        raise HTTPException(status_code=422, detail=oversell_refusal(shorts, action))
    return [oversell_warning(s, action) for s in shorts]


def confirm_credit_limit(
    db,
    customer,
    owed,
    allowed: bool,
    noun: str = "invoice",
    exclude_invoice_id=None,
    question: str | None = None,
    ask: str = "Save it anyway?",
) -> None:
    """A customer's credit limit, told to every way of saving an invoice
    (#256, 2.22.2). The form asked before it saved and nothing else did:
    `POST /api/invoices` from an agent saved an over-limit invoice without
    a word, though the limit is stored and imported from QuickBooks. Now a
    document that would take what the customer owes past their limit is
    refused with 409 and ``detail = {"code": "credit_limit", "message",
    "question", "limit", "would_owe"}`` unless the request carries
    ``allow_over_credit_limit: true``; the page asks the question and sends
    the request again with the flag. What they owe is their open balance in
    home dollars, net of the credits and unapplied payments they hold (as
    A/R Aging and the customer page count it), less the invoice being
    edited (whose new amount is `owed`). Nothing is written by the refused
    request. A customer with no limit is never asked.

    The limit checked is the payer's (2.31.1 gate, W-62): an invoice to a
    sub-customer billed with its parent lands on the parent's A/R, so the
    parent's limit is checked against what sits on that A/R, the parent's
    own open documents and those of every sub-customer billed with it
    (billed_balance, the A/R Aging row), and the 409 names the parent. A
    customer billed on its own is its own payer, as before. ``ask`` ends
    the question for the action at hand ("Duplicate it anyway?")."""
    from app.models.invoices import Invoice
    from app.services.contact_balances import home_amount
    from app.services.sub_customers import billed_balance, payer_for

    if allowed or customer is None:
        return
    payer = payer_for(db, customer)
    limit = Decimal(str(getattr(payer, "credit_limit", None) or 0))
    if limit <= 0:
        return
    open_balance = billed_balance(db, payer.id)
    if exclude_invoice_id is not None:
        current = db.get(Invoice, exclude_invoice_id)
        if current is not None and current.status != InvoiceStatus.VOID:
            open_balance -= home_amount(current.balance_due, current.exchange_rate)
    after = _q(open_balance + Decimal(str(owed or 0)))
    if after <= limit:
        return
    sentence = (
        f"{payer.name}'s credit limit is ${limit:,.2f}. "
        f"With this {noun} they would owe ${after:,.2f}."
    )
    raise HTTPException(
        status_code=409,
        detail={
            "code": CREDIT_LIMIT_CODE,
            "message": sentence,
            "question": question or f"{sentence} {ask}",
            "limit": str(_q(limit)),
            "would_owe": str(after),
        },
    )


def opening_status(total) -> InvoiceStatus:
    """What a new invoice starts as: a draft, unless it is for nothing —
    then nothing is owed and it starts paid, as QuickBooks marks a $0.00
    invoice. A $0.00 draft or sent invoice would otherwise count as overdue
    on the dashboard once its due date passed, the harm the zero-total
    refusal was added for."""
    if Decimal(str(total or 0)) == 0:
        return InvoiceStatus.PAID
    return InvoiceStatus.DRAFT


def _day(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def refuse_due_before_date(doc_date: date | None, due_date: date | None) -> None:
    """A due date before the invoice's own date was accepted and saved
    (2.17.3 exploratory W-L4). An invoice cannot fall due before it exists."""
    if doc_date and due_date and due_date < doc_date:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The due date ({_day(due_date)}) is before the invoice date "
                f"({_day(doc_date)}). Pick a due date on or after the invoice date."
            ),
        )


def money_applied(db: Session, invoice) -> list[dict]:
    """The payments and credit memos (a write-off is one) applied to an
    invoice and still standing, oldest first, each as ``{kind, id,
    number, customer_id, customer_name, date, amount}``: ``kind`` is
    "payment", "credit_memo", "write_off" or "discount" (an early-payment
    discount, #664), ``customer_name`` whose money it is ("Parent: Sub"
    for a sub-customer). A voided payment, or a
    credit memo voided (which takes its applications off), is not on the
    invoice any more."""
    from sqlalchemy import or_

    from app.models.credit_memos import (
        CreditApplication,
        CreditMemo,
        CreditMemoStatus,
    )
    from app.models.payments import Payment, PaymentAllocation

    found: dict = {}

    def add(kind, doc, number, amount):
        row = found.setdefault(
            (kind, doc.id),
            {
                "kind": kind,
                "id": doc.id,
                "number": number,
                "customer_id": doc.customer_id,
                "customer_name": doc.customer.full_name if doc.customer else "",
                "date": doc.date,
                "amount": Decimal("0"),
            },
        )
        row["amount"] += Decimal(str(amount or 0))

    for payment, amount in (
        db.query(Payment, PaymentAllocation.amount)
        .join(PaymentAllocation, PaymentAllocation.payment_id == Payment.id)
        .filter(
            PaymentAllocation.invoice_id == invoice.id,
            or_(Payment.is_voided.is_(False), Payment.is_voided.is_(None)),
        )
    ):
        add("payment", payment, payment.check_number or payment.reference, amount)
    for memo, amount in (
        db.query(CreditMemo, CreditApplication.amount)
        .join(CreditApplication, CreditApplication.credit_memo_id == CreditMemo.id)
        .filter(
            CreditApplication.invoice_id == invoice.id,
            CreditMemo.status != CreditMemoStatus.VOID,
        )
    ):
        kind = (
            "write_off"
            if memo.is_write_off
            else "discount" if memo.discount_payment_id else "credit_memo"
        )
        add(kind, memo, memo.memo_number, amount)
    return sorted(
        (row for row in found.values() if row["amount"]),
        key=lambda row: (row["date"], row["kind"] != "payment", row["id"]),
    )


def _money_phrase(row: dict) -> str:
    amount = f"${row['amount']:,.2f}"
    who = f"{row['customer_name']}'s" if row["customer_name"] else "a"
    if row["kind"] == "payment":
        return f"{who} payment of {amount} ({_day(row['date'])})"
    noun = {"write_off": "write-off", "discount": "early-payment discount"}.get(
        row["kind"], "credit memo"
    )
    number = f" {row['number']}" if row["number"] else ""
    return f"{who} {noun}{number} for {amount}"


def refuse_moving_money(db: Session, invoice, moved_to, words) -> None:
    """An edit may not move an invoice to another customer while a payment
    or credit memo is applied to it (2.31.1 gate, round 3): moved, an
    invoice Alpha paid went to Beta's statement with Alpha's payment on
    it, and Alpha's statement still listed the payment, so both carried
    it. Refused with 409 ``{"code": "money_applied", "message",
    "applied"}``, the message naming each payment and credit, whose it is
    and for how much, and the way out: void it, and enter it again where
    it belongs, then move the invoice. The rule W-58 keeps for a tenant
    while its parent's money is on its invoices, for every invoice. An
    unpaid invoice moves; nothing is written by the refused request."""
    from app.services.donor_documents import document_label
    from app.services.terminology import document_reference

    applied = money_applied(db, invoice)
    paid = Decimal(str(invoice.amount_paid or 0))
    if not applied and paid <= 0:
        return
    face = document_label(invoice, words)
    noun = face.lower()
    ref = document_reference(face, invoice.invoice_number)
    what = f"{ref} cannot move to {moved_to.full_name}"
    if applied:
        phrases = [_money_phrase(row) for row in applied]
        listed = (
            phrases[0]
            if len(phrases) == 1
            else ", ".join(phrases[:-1]) + " and " + phrases[-1]
        )
        if len(applied) == 1:
            kind = {"payment": "payment", "write_off": "write-off"}.get(
                applied[0]["kind"], "credit memo"
            )
            undo = f"Void the {kind} first and enter it again where it belongs"
        else:
            undo = "Void them first and enter them again where they belong"
        message = (
            f"{what} while money is applied to it: {listed}. {undo}, then "
            f"move the {noun}."
        )
    else:
        message = (
            f"{what} while money is applied to it: ${paid:,.2f} is paid on it. "
            f"Void what was paid on it first, then move the {noun}."
        )
    raise HTTPException(
        status_code=409,
        detail={
            "code": MONEY_APPLIED_CODE,
            "message": message,
            "applied": [
                {
                    **row,
                    "date": row["date"].isoformat() if row["date"] else None,
                    "amount": str(_q(row["amount"])),
                }
                for row in applied
            ],
        },
    )


def _due_date_from_terms(db: Session, base_date: date, terms: str | None) -> date:
    """A due date from a base date and terms, by the one parser
    (app/services/payment_terms.py, #664): the terms list first, then
    "Net N" and Due on Receipt. Terms it doesn't understand keep the old
    30 days; the document's terms_note says so."""
    from app.services.payment_terms import due_date_for

    return due_date_for(db, base_date, terms)


def resolve_line_taxable(db: Session, lines_data, customer=None) -> None:
    """Decide is_taxable on every line. A customer marked non-taxable (a
    reseller permit, an exempt organization) pays no sales tax on ANY line,
    whatever the line says — the page always sends each line's tax box,
    defaulted from the item, so an exemption that only filled unset lines
    was never consulted for anything made at the window (2.16.2 gate,
    skytech: 8.90 charged where 0.00 was owed). Otherwise an unset line
    takes the item's flag, else the company's "New lines and items are
    taxable" setting (#486; on by default, so taxable as before). Mutates
    the pydantic line objects in place so the same objects feed both the
    totals and the stored rows."""
    from app.models.items import Item
    from app.services.settings_service import new_lines_taxable

    cust_taxable = True if customer is None else (customer.is_taxable is not False)
    company_default = None
    for ln in lines_data:
        if not cust_taxable:
            ln.is_taxable = False
            continue
        if getattr(ln, "is_taxable", None) is not None:
            continue
        item = db.get(Item, ln.item_id) if getattr(ln, "item_id", None) else None
        if item is not None:
            ln.is_taxable = item.is_taxable is not False
            continue
        if company_default is None:
            company_default = new_lines_taxable(db)
        ln.is_taxable = company_default


def refuse_negative_lines(db: Session, lines, invoice=None) -> None:
    """A negative price is a discount. A line may carry one on an item that
    takes a negative price (services/discount_items.py: one marked as a
    discount or concession, #639, or the one a QuickBooks Online discount
    came in on), or on an item (or no item) the invoice being edited
    already has a negative line on: a QuickBooks Online document keeps the
    negative lines it came with, and an edit saves them back. Any other is
    refused as it always was, in the same words: a refund belongs on a
    credit memo."""
    from app.services.discount_items import takes_negative_price

    had = {
        line.item_id
        for line in (invoice.lines if invoice is not None else [])
        if Decimal(str(line.rate or 0)) < 0
    }
    for index, line in enumerate(lines):
        if Decimal(str(line.rate or 0)) >= 0:
            continue
        if line.item_id in had or takes_negative_price(db, line.item_id):
            continue
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("body", "lines", index),
                    "msg": "Value error, rate must be non-negative; use a credit "
                    "memo for refunds",
                    "input": None,
                }
            ]
        )


def kept_tax(invoice, tax_rate, lines):
    """The tax amount an edit keeps as it is: one an import brought with no
    rate that gives it (QuickBooks Online's, when its tax lines didn't make
    a single rate). Worked out from the rate, it
    would come to 0.00. None when a rate is entered, when the invoice has a
    rate or no tax, or when no line is taxable now."""
    if tax_rate and Decimal(str(tax_rate)) != 0:
        return None
    if Decimal(str(invoice.tax_rate or 0)) != 0 or not invoice.tax_amount:
        return None
    if not any(getattr(line, "is_taxable", None) is not False for line in lines):
        return None
    return Decimal(str(invoice.tax_amount))


def _compute_totals(lines_data, tax_rate):
    """Tax applies to the lines flagged taxable (QuickBooks-style per-line
    tax); the rate itself stays on the document."""
    return compute_line_totals(lines_data, tax_rate)


def _build_invoice_journal_lines(
    db: Session,
    invoice_total,
    tax_amount,
    tax_account_id,
    ar_id,
    default_income_id,
    lines_iter,
    invoice_number,
    face: str = "Invoice",
):
    """Build the journal-line list for an invoice. Used by create/update/duplicate.

    `lines_iter` yields objects with .quantity, .rate, and .item_id.
    `face` is the document's own name (donor_documents.document_label).
    """
    journal_lines = []
    journal_lines.append(
        {
            "account_id": ar_id,
            "debit": Decimal(str(invoice_total)),
            "credit": Decimal("0"),
            "description": document_reference(face, invoice_number),
        }
    )
    for ld in lines_iter:
        # Round each line to 2dp BEFORE summing — must match compute_line_totals
        # exactly, or the credits won't sum to the rounded A/R debit and
        # create_journal_entry rejects the unbalanced entry (sub-cent rates
        # like fuel @ 1.005 / fractional qty otherwise 500 the whole post).
        line_amount = _q(Decimal(str(ld.quantity)) * Decimal(str(ld.rate)))
        if line_amount == 0:
            continue
        income_id = default_income_id
        if ld.item_id:
            item = db.query(Item).filter(Item.id == ld.item_id).first()
            if item and item.income_account_id:
                income_id = item.income_account_id
        journal_lines.append(
            {
                "account_id": income_id,
                # A discount line (negative) is taken off its account, the
                # discount account of the item it is on: a debit.
                "debit": -line_amount if line_amount < 0 else Decimal("0"),
                "credit": line_amount if line_amount > 0 else Decimal("0"),
                "description": (getattr(ld, "description", "") or ""),
                "class_id": getattr(ld, "class_id", None),
                "job_id": getattr(ld, "job_id", None),
                "cost_code_id": getattr(ld, "cost_code_id", None),
            }
        )
    if tax_amount and tax_amount > 0 and tax_account_id:
        journal_lines.append(
            {
                "account_id": tax_account_id,
                "debit": Decimal("0"),
                "credit": Decimal(str(tax_amount)),
                "description": "Sales tax",
            }
        )
    return journal_lines


def invoice_customer_name(invoice):
    """The name an invoice (or the credit memo written off it) carries for
    its customer, everywhere a response or a journal names it: "Parent:
    Sub" for a sub-customer (#638), the customer's name otherwise, None
    with no customer. One helper, so send, void, duplicate and edit answer
    the name the list and get answer."""
    customer = getattr(invoice, "customer", None)
    return customer.full_name if customer is not None else None


def _post_invoice_journal(
    db: Session,
    invoice,
    lines,
    customer_name,
    *,
    existing_transaction=None,
    balance_on_income=False,
):
    """One construction/conversion/posting path for invoice create and edit.

    `balance_on_income` is for an invoice the QuickBooks Online import
    created. Its discount and bundles came across as lines, so its lines
    add up to its total; a line of a kind the import didn't bring across
    still leaves a difference,
    which the import log named. That difference posts to the income
    account, said plainly on the entry and in the server log, so the entry
    carries the total the invoice shows."""
    from app.services.accounting import (
        create_journal_entry,
        get_ar_account_id,
        get_default_income_account_id,
        get_sales_tax_account_id,
    )
    from app.services.currency import convert_lines
    from app.services.donor_documents import document_label
    from app.services.terminology import terms_from_db

    face = document_label(invoice, terms_from_db(db))
    journal_lines = _build_invoice_journal_lines(
        db,
        invoice.total,
        invoice.tax_amount,
        get_sales_tax_account_id(db),
        get_ar_account_id(db),
        get_default_income_account_id(db),
        lines,
        invoice.invoice_number,
        face=face,
    )
    if balance_on_income:
        short = sum(line["debit"] for line in journal_lines) - sum(
            line["credit"] for line in journal_lines
        )
        if short:
            journal_lines.append(
                {
                    "account_id": get_default_income_account_id(db),
                    "debit": -short if short < 0 else Decimal("0"),
                    "credit": short if short > 0 else Decimal("0"),
                    "description": "Part of the QuickBooks Online total on no "
                    "line here",
                }
            )
            logger.warning(
                "%s %s from QuickBooks Online: its lines and tax come to %s and "
                "its total to %s; the %s on no line was posted to income",
                face,
                invoice.invoice_number,
                invoice.total - short,
                invoice.total,
                short,
            )
    # A $0.00 invoice (allowed with allow_zero_total) builds a journal whose
    # every line is zero: the A/R debit is 0 and each income/tax line was
    # skipped. create_journal_entry would write a header with no splits — an
    # empty journal entry. Post nothing instead and let the caller leave
    # transaction_id NULL; the void and edit paths tolerate an unposted
    # invoice. A document whose lines net to zero but carry real amounts (a
    # full discount) still has non-zero splits and posts as before.
    if not any(
        _q(line.get("debit", 0)) != 0 or _q(line.get("credit", 0)) != 0
        for line in journal_lines
    ):
        return None
    return create_journal_entry(
        db,
        invoice.date,
        document_reference(face, invoice.invoice_number, customer_name),
        convert_lines(journal_lines, Decimal(str(invoice.exchange_rate or 1))),
        source_type="invoice",
        source_id=invoice.id,
        reference=invoice.invoice_number,
        class_id=invoice.class_id,
        job_id=invoice.job_id,
        existing_transaction=existing_transaction,
    )


def _reverse_and_delete_journal(db: Session, transaction_id: int):
    """Reverse account balances for existing journal lines, then delete them,
    each leaving a DELETE audit event with its account and amounts (#289).

    Used by update_invoice and update_bill to prepare for a fresh journal
    rebuild.
    """
    from app.models.transactions import (
        BASIS_CASH,
        LEDGER_BASIS_OPTION,
        READ_ALL,
        TransactionLine,
    )
    from app.services.accounting import apply_balance_deltas
    from app.services.audit import audited_bulk_delete

    # Every basis is deleted (a cash-basis journal entry's lines are all
    # cash lines); a cash line never moved a stored balance, so it is not
    # reversed (#483).
    everything = {LEDGER_BASIS_OPTION: READ_ALL}
    old_lines = (
        db.query(TransactionLine)
        .filter(TransactionLine.transaction_id == transaction_id)
        .execution_options(**everything)
        .all()
    )
    # Aggregate the reversal per account and apply atomically, the same way
    # the posting path does, so this can't lose a concurrent update.
    balance_deltas: dict = {}
    for ol in old_lines:
        if ol.basis == BASIS_CASH:
            continue
        account = db.query(Account).filter(Account.id == ol.account_id).first()
        if account:
            if account.account_type.value in ("asset", "expense", "cogs"):
                delta = -(ol.debit - ol.credit)
            else:
                delta = -(ol.credit - ol.debit)
            balance_deltas[account.id] = (
                balance_deltas.get(account.id, Decimal("0")) + delta
            )
    apply_balance_deltas(db, balance_deltas)
    audited_bulk_delete(
        db,
        db.query(TransactionLine)
        .filter(TransactionLine.transaction_id == transaction_id)
        .execution_options(**everything),
    )
