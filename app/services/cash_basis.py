# ============================================================================
# Reports on a cash basis (#483, docs/design/cash-basis.md).
#
# Every ledger line carries a basis. An invoice's or bill's lines count on
# the accrual basis only; a payment writes, into its own entry, the cash
# recognition of what it collected or paid — the document's income or
# expense lines prorated by the amount applied — and those count on the
# cash basis only. The reading a query gets is chosen on the session
# (app/models/transactions.py); this module says which lines are which,
# builds the cash lines, and backfills a file that predates the column.
# ============================================================================

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models.accounts import AccountType
from app.models.bills import Bill, BillPayment, BillPaymentAllocation
from app.models.invoices import Invoice
from app.models.payments import Payment, PaymentAllocation
from app.models.transactions import (
    BASIS_ACCRUAL,
    BASIS_BOTH,
    BASIS_CASH,
    LEDGER_BASIS_OPTION,
    READ_ACCRUAL,
    READ_ALL,
    READ_CASH,
    Transaction,
    TransactionLine,
)

log = logging.getLogger(__name__)

# Payments written between commits by the backfill.
BACKFILL_BATCH = 200

# Entries whose every line counts on the accrual basis only: the documents
# that put something on A/R or A/P, their voids and the adjustments an edit
# posts beside them. Everything else is "both" unless a line says otherwise.
ACCRUAL_SOURCE_TYPES = frozenset(
    {
        "invoice",
        "invoice_void",
        "invoice_edit",
        "late_fee",
        "bill",
        "bill_void",
        "bill_edit",
        "credit_memo",
        "credit_memo_void",
        "vendor_credit",
        "vendor_credit_void",
    }
)

# The entries a customer payment's cash lines are prorated over: the
# invoice's own posting, the cost-of-goods entry the stock movement posts
# under the same source, a late fee added to it, and an edit's adjustment.
INVOICE_SOURCE_TYPES = ("invoice", "invoice_edit", "late_fee")
BILL_SOURCE_TYPES = ("bill", "bill_edit")

# The columns that tie a cash recognition line to the invoice or bill it
# recognizes, so an edit of that document can rewrite them.
INVOICE_LINK = "recognizes_invoice_id"
BILL_LINK = "recognizes_bill_id"
LINK_KEYS = (INVOICE_LINK, BILL_LINK)

# The company default, and what the report parameter may say.
REPORT_BASES = (READ_ACCRUAL, READ_CASH)
SETTING_KEY = "report_basis"

# QuickBooks' accounts for money received or paid against no document yet,
# created on demand like 3900 and 6999 are.
UNAPPLIED_INCOME = ("4950", "Unapplied Cash Payment Income", AccountType.INCOME)
UNAPPLIED_EXPENSE = (
    "6990",
    "Unapplied Cash Bill Payment Expense",
    AccountType.EXPENSE,
)


def default_basis_for(source_type: str | None) -> str:
    """The basis a line takes when it names none: accrual for the documents
    in ACCRUAL_SOURCE_TYPES, both for everything else."""
    return BASIS_ACCRUAL if source_type in ACCRUAL_SOURCE_TYPES else BASIS_BOTH


def company_basis(db: Session) -> str:
    """The company's default report basis (Settings → Company)."""
    from app.services.settings_service import get_setting_raw

    value = (get_setting_raw(db, SETTING_KEY) or "").strip().lower()
    return value if value in REPORT_BASES else READ_ACCRUAL


def resolve_basis(db: Session, requested: str | None) -> str:
    """The basis a report runs on: the `basis` parameter when given, else
    the company default. Anything else is a 422 saying what is accepted."""
    if requested is None or requested == "":
        return company_basis(db)
    if not isinstance(requested, str) or requested.lower() not in REPORT_BASES:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422, detail="basis must be accrual, cash or left out"
        )
    return requested.lower()


def reading(basis: str) -> dict:
    """Execution options that read the ledger on `basis`, for
    `.execution_options(**reading(basis))`."""
    return {LEDGER_BASIS_OPTION: basis}


def receivable_note(db: Session, as_of, basis: str) -> str | None:
    """A short note for a cash-basis report when A/R or A/P is not zero at
    `as_of`, else None. History that came in as journal entries (a QuickBooks
    Desktop ledger import, opening A/R and A/P) counts on both bases and is
    not converted, so a balance left there must not be read as cash."""
    if basis != READ_CASH:
        return None
    from sqlalchemy import func

    from app.services.accounting import get_ap_account_id, get_ar_account_id
    from app.services.control_accounts import MissingControlAccount

    ids = []
    for resolve in (get_ar_account_id, get_ap_account_id):
        try:
            ids.append(resolve(db))
        except MissingControlAccount:
            pass
    if not ids:
        return None
    rows = (
        db.query(
            TransactionLine.account_id,
            func.coalesce(func.sum(TransactionLine.debit - TransactionLine.credit), 0),
        )
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id.in_(ids), Transaction.date <= as_of)
        .group_by(TransactionLine.account_id)
        .execution_options(**reading(READ_CASH))
        .all()
    )
    if not any(_q(net) != 0 for _, net in rows):
        return None
    return (
        "Accounts Receivable or Accounts Payable is not zero on this cash-basis "
        "report. That balance came in as journal entries (an imported ledger or "
        "opening balances), which are not converted to cash, so do not read that "
        "history as cash."
    )


def unapplied_income_id(db: Session) -> int:
    from app.services.accounting import ensure_account

    return ensure_account(db, *UNAPPLIED_INCOME).id


def unapplied_expense_id(db: Session) -> int:
    from app.services.accounting import ensure_account

    return ensure_account(db, *UNAPPLIED_EXPENSE).id


# ---------------------------------------------------------------------------
# Recognition lines of a document
# ---------------------------------------------------------------------------


def _q(value) -> Decimal:
    from app.services.accounting import _q as quantize

    return quantize(value)


def document_lines(
    db: Session,
    source_types,
    source_id: int,
    control_account_id: int,
    skip_account_ids=(),
) -> list[dict]:
    """The lines a document recognizes, netted by account and dimensions:
    every line of its entries except the control account's (A/R or A/P),
    each as {account_id, amount, class_id, job_id, cost_code_id, cost_type,
    function}, `amount` signed credit-positive. `skip_account_ids` leaves
    out more accounts (a bill's Retainage Payable). Read on every basis: the
    entries are accrual ones, and a repair may run before they are tagged."""
    rows = (
        db.query(TransactionLine)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(
            Transaction.source_type.in_(tuple(source_types)),
            Transaction.source_id == source_id,
            TransactionLine.account_id.notin_((control_account_id, *skip_account_ids)),
        )
        .order_by(TransactionLine.id)
        .execution_options(**reading(READ_ALL))
        .all()
    )
    netted: dict[tuple, dict] = {}
    for ln in rows:
        key = (
            ln.account_id,
            ln.class_id,
            ln.job_id,
            ln.cost_code_id,
            ln.cost_type,
            ln.function,
        )
        entry = netted.setdefault(
            key,
            {
                "account_id": ln.account_id,
                "class_id": ln.class_id,
                "job_id": ln.job_id,
                "cost_code_id": ln.cost_code_id,
                "cost_type": ln.cost_type,
                "function": ln.function,
                "amount": Decimal("0"),
            },
        )
        entry["amount"] += Decimal(str(ln.credit or 0)) - Decimal(str(ln.debit or 0))
    return [e for e in netted.values() if e["amount"] != 0]


def prorate(lines: list[dict], applied: Decimal, total: Decimal) -> list[dict]:
    """Each line's share of `applied` out of `total`, to the cent, the
    rounding cent on the largest share, so the shares add up to `applied`
    exactly. Lines keep their sign (a discount stays a debit)."""
    applied = _q(applied)
    if total == 0 or applied == 0 or not lines:
        return []
    ratio = applied / total
    shares = []
    for line in lines:
        share = _q(line["amount"] * ratio)
        if share != 0:
            shares.append({**line, "amount": share})
    if not shares:
        return []
    off = applied - sum((s["amount"] for s in shares), Decimal("0"))
    if off != 0:
        biggest = max(shares, key=lambda s: abs(s["amount"]))
        biggest["amount"] += off
    return [s for s in shares if s["amount"] != 0]


def _cash_line(line: dict, memo: str) -> dict:
    """A recognition line as create_journal_entry takes it: a positive
    amount is a credit, a negative one a debit."""
    amount = line["amount"]
    return {
        "account_id": line["account_id"],
        "debit": -amount if amount < 0 else Decimal("0"),
        "credit": amount if amount > 0 else Decimal("0"),
        "description": memo,
        "class_id": line.get("class_id"),
        "job_id": line.get("job_id"),
        "cost_code_id": line.get("cost_code_id"),
        "cost_type": line.get("cost_type"),
        "function": line.get("function"),
        "basis": BASIS_CASH,
        # the document this line recognizes (#483), when it names one
        **{k: line[k] for k in LINK_KEYS if line.get(k) is not None},
    }


def _ledger_posted(db: Session, document) -> bool:
    """Whether the QuickBooks Online ledger import posted this invoice and
    that posting stands. Its lines are `both`: its income and A/R already
    count on the cash view, so a payment on it recognizes nothing more."""
    from app.services.qbo_documents import import_posting, invoice_origin

    return import_posting(db, invoice_origin(db, document)) is not None


def _recognition(
    db: Session,
    source_types,
    document_id: int,
    control_id: int,
    applied_home: Decimal,
    memo: str,
    fallback_account_id,
    link_key: str,
    skip_account_ids=(),
) -> list[dict]:
    """The cash lines recognizing `applied_home` of one document: its
    recognition lines prorated, each on the same side as on the document
    and tied to it through `link_key`. `skip_account_ids` leaves accounts
    out of the recognition (a bill's Retainage Payable). A document with no
    recognition lines of its own (an import that posted none) is recognized
    to the unapplied account instead."""
    lines = document_lines(db, source_types, document_id, control_id, skip_account_ids)
    total = sum((ln["amount"] for ln in lines), Decimal("0"))
    shares = prorate(lines, applied_home, total) if total else []
    if not shares:
        if applied_home == 0:
            return []
        shares = [{"account_id": fallback_account_id(db), "amount": applied_home}]
    return [_cash_line({**s, link_key: document_id}, memo) for s in shares]


def customer_payment_cash_lines(
    db: Session, ar_id: int, applied: list[tuple], unapplied_home, memo: str
) -> list[dict]:
    """The cash lines of a customer payment's entry (#483): for each
    (invoice, applied_home) the invoice's income, discount, sales tax and
    cost lines prorated; the unapplied remainder to Unapplied Cash Payment
    Income; and one Dr A/R for the whole, so the cash lines balance."""
    out: list[dict] = []
    for invoice, applied_home in applied:
        if _ledger_posted(db, invoice):
            continue
        out.extend(
            _recognition(
                db,
                INVOICE_SOURCE_TYPES,
                invoice.id,
                ar_id,
                _q(applied_home),
                memo,
                unapplied_income_id,
                INVOICE_LINK,
            )
        )
    unapplied_home = _q(unapplied_home or 0)
    if unapplied_home > 0:
        out.append(
            _cash_line(
                {"account_id": unapplied_income_id(db), "amount": unapplied_home}, memo
            )
        )
    return _balanced_on(out, ar_id, memo)


def bill_payment_cash_lines(
    db: Session, ap_id: int, applied: list[tuple], unapplied_home, memo: str
) -> list[dict]:
    """The mirror for a bill payment: each bill's expense, cost and inventory
    lines prorated (debits), the remainder to Unapplied Cash Bill Payment
    Expense, and one Cr A/P for the whole."""
    out: list[dict] = []
    held = _retainage_ids(db)
    for bill, applied_home in applied:
        # a bill's lines are debits: recognize -applied of credit. Retainage
        # Payable is left out, so the net paid is spread over the expense
        # lines and the held part is not yet an expense (released later).
        out.extend(
            _recognition(
                db,
                BILL_SOURCE_TYPES,
                bill.id,
                ap_id,
                -_q(applied_home),
                memo,
                unapplied_expense_id,
                BILL_LINK,
                held,
            )
        )
    unapplied_home = _q(unapplied_home or 0)
    if unapplied_home > 0:
        out.append(
            _cash_line(
                {"account_id": unapplied_expense_id(db), "amount": -unapplied_home},
                memo,
            )
        )
    return _balanced_on(out, ap_id, memo)


def _retainage_ids(db: Session) -> tuple:
    """Retainage Payable (2050), when the chart has it: a bill holding
    retainage credits it, and it is not an expense until released."""
    from app.services import control_accounts

    found = control_accounts.find(db, "2050")
    return (found,) if found is not None else ()


def retainage_release_cash_lines(
    db: Session, bill, released_home, memo: str
) -> list[dict]:
    """The cash lines of releasing `released_home` of a bill's held retainage
    (#483): the bill's expense, cost and inventory lines prorated for the
    released amount (debits) and one Cr Retainage Payable (cash), so the
    expense lands when the held part is paid. They go in the release's own
    entry, beside its Dr 2050 / Cr bank (both), which then net to zero on
    2050 in the cash view. A release that is not tied to a bill (a plain
    check or journal entry against 2050) has nothing to recognize."""
    from app.services.accounting import ensure_construction_accounts, get_ap_account_id

    released_home = _q(released_home)
    if released_home <= 0:
        return []
    held = _retainage_ids(db)
    lines = _recognition(
        db,
        BILL_SOURCE_TYPES,
        bill.id,
        get_ap_account_id(db),
        -released_home,
        memo,
        unapplied_expense_id,
        BILL_LINK,
        held,
    )
    return _balanced_on(lines, ensure_construction_accounts(db)["2050"].id, memo)


def discount_cash_lines(
    db: Session,
    control_id: int,
    document,
    amount_home,
    discount_account_id: int,
    memo: str,
    invoice: bool = True,
) -> list[dict]:
    """The cash lines of an early-payment discount's entry (#664). The
    discount settles part of the document with no cash, so on the cash basis
    it recognizes that part as a payment would (an invoice's income and tax
    lines prorated, a bill's expense lines) against the discount account.
    A settled document then reads the same on both bases: its income less
    the discount, and all of its tax. An invoice the QuickBooks ledger import
    posted already counts its income on cash, so only the discount moves."""
    amount_home = _q(amount_home)
    if amount_home == 0:
        return []
    if invoice and _ledger_posted(db, document):
        return [
            _cash_line(
                {"account_id": discount_account_id, "amount": -amount_home}, memo
            ),
            {
                "account_id": control_id,
                "debit": Decimal("0"),
                "credit": amount_home,
                "description": memo,
                "basis": BASIS_CASH,
            },
        ]
    if invoice:
        lines = _recognition(
            db,
            INVOICE_SOURCE_TYPES,
            document.id,
            control_id,
            amount_home,
            memo,
            unapplied_income_id,
            INVOICE_LINK,
        )
    else:
        lines = _recognition(
            db,
            BILL_SOURCE_TYPES,
            document.id,
            control_id,
            -amount_home,
            memo,
            unapplied_expense_id,
            BILL_LINK,
            _retainage_ids(db),
        )
    net = sum((ln["credit"] - ln["debit"] for ln in lines), Decimal("0"))
    if net == 0:
        return lines
    return lines + [
        _cash_line({"account_id": discount_account_id, "amount": -net}, memo)
    ]


def _balanced_on(lines: list[dict], control_id: int, memo: str) -> list[dict]:
    """Add the control-account cash line that balances the recognition
    lines: Dr A/R when they are net credits (income), Cr A/P when net
    debits (expense)."""
    if not lines:
        return []
    net = sum((ln["credit"] - ln["debit"] for ln in lines), Decimal("0"))
    if net == 0:
        return lines
    return lines + [
        {
            "account_id": control_id,
            "debit": net if net > 0 else Decimal("0"),
            "credit": -net if net < 0 else Decimal("0"),
            "description": memo,
            "basis": BASIS_CASH,
        }
    ]


def apply_reclass_lines(
    db: Session, ar_id: int, invoice, applied_home, memo: str
) -> list[dict]:
    """When an unapplied remainder is applied to an invoice later: the
    invoice's recognition lines, and Unapplied Cash Payment Income debited
    for the same. Cash lines only; the accrual reading sees nothing.
    An invoice the QuickBooks ledger import posted already counts its income
    on cash (`both`), so the unapplied income is simply taken back to A/R."""
    if _ledger_posted(db, invoice):
        applied_home = _q(applied_home)
        if applied_home == 0:
            return []
        return [
            _cash_line(
                {"account_id": unapplied_income_id(db), "amount": -applied_home}, memo
            ),
            {
                "account_id": ar_id,
                "debit": Decimal("0"),
                "credit": applied_home,
                "description": memo,
                "basis": BASIS_CASH,
            },
        ]
    lines = _recognition(
        db,
        INVOICE_SOURCE_TYPES,
        invoice.id,
        ar_id,
        _q(applied_home),
        memo,
        unapplied_income_id,
        INVOICE_LINK,
    )
    if not lines:
        return []
    net = sum((ln["credit"] - ln["debit"] for ln in lines), Decimal("0"))
    if net == 0:
        return lines
    return lines + [
        _cash_line({"account_id": unapplied_income_id(db), "amount": -net}, memo)
    ]


# ---------------------------------------------------------------------------
# Editing a paid document: its payments' cash lines are recomputed
# ---------------------------------------------------------------------------


def _cash_lines_of(db: Session, transaction_id: int) -> list[TransactionLine]:
    return (
        db.query(TransactionLine)
        .filter(
            TransactionLine.transaction_id == transaction_id,
            TransactionLine.basis == BASIS_CASH,
        )
        .order_by(TransactionLine.id)
        .execution_options(**reading(READ_ALL))
        .all()
    )


def _lines_to_rewrite(
    mine: list, theirs: list, only_document: bool, control_id: int
) -> list | None:
    """The cash lines of one payment's entry that recognize the document.
    Lines written since the link existed name it. A line written before it
    names nothing; those are the document's only when the payment paid this
    document alone with nothing left over, and then every cash line but the
    control account's is. Anything else is ambiguous and is left alone,
    reported as None."""
    if mine:
        return mine
    unlinked = [
        ln
        for ln in theirs
        if ln.recognizes_invoice_id is None and ln.recognizes_bill_id is None
    ]
    if not unlinked:
        return []
    if only_document:
        return [ln for ln in unlinked if ln.account_id != control_id]
    return None


def _recompute(
    db: Session,
    document,
    *,
    link_key: str,
    source_types,
    control_id: int,
    fallback_account_id,
    allocations,
    fully_applied,
    adopted: bool,
) -> int:
    """Rewrite, in each non-void payment of `document`, the cash lines that
    recognize it from the document's lines as they now stand, prorated by
    the same applied amount and kept on the payment's date. A payment on or
    before the closing date keeps what it has; so does one whose older cash
    lines cannot be told from another document's. Returns the payments
    rewritten."""
    from app.services.audit import audited_bulk_delete
    from app.services.closing_date import get_closing_date
    from app.services.currency import to_home

    db.flush()  # lines added a moment ago (an adoption) are looked for below
    closing = get_closing_date(db)
    rate = document.exchange_rate or Decimal("1")
    rewritten = 0
    for payment, alloc in allocations(document):
        if payment is None or payment.is_voided or payment.transaction_id is None:
            continue
        txn = db.get(Transaction, payment.transaction_id)
        if txn is None:
            continue
        if closing is not None and txn.date <= closing:
            log.info(
                "Cash-basis lines of payment %s kept as they are: it is on or "
                "before the closing date",
                payment.id,
            )
            continue
        theirs = _cash_lines_of(db, txn.id)
        mine = [
            ln
            for ln in theirs
            if getattr(ln, link_key) == document.id and ln.account_id != control_id
        ]
        if adopted:
            # the document had no lines of its own before this edit
            old = mine
        else:
            old = _lines_to_rewrite(
                mine, theirs, fully_applied(payment, alloc), control_id
            )
            if old is None:
                log.warning(
                    "Cash-basis lines of payment %s not recomputed: it paid more "
                    "than one document before lines named theirs",
                    payment.id,
                )
                continue
        new = _recognition(
            db,
            source_types,
            document.id,
            control_id,
            (1 if link_key == INVOICE_LINK else -1) * _q(to_home(alloc.amount, rate)),
            txn.description or "Payment",
            fallback_account_id,
            link_key,
            # a bill's Retainage Payable stays out of the recognition (step 4)
            _retainage_ids(db) if link_key == BILL_LINK else (),
        )
        if old:
            audited_bulk_delete(
                db,
                db.query(TransactionLine)
                .filter(TransactionLine.id.in_([ln.id for ln in old]))
                .execution_options(**reading(READ_ALL)),
            )
        _insert_cash_lines(db, txn, new)
        db.flush()
        # keep the entry's cash lines balanced: the control line carries
        # whatever the recognition lines no longer (or newly) offset
        net = sum(
            (
                Decimal(str(ln.credit)) - Decimal(str(ln.debit))
                for ln in _cash_lines_of(db, txn.id)
            ),
            Decimal("0"),
        )
        if net != 0:
            _insert_cash_lines(
                db,
                txn,
                [
                    {
                        "account_id": control_id,
                        "debit": net if net > 0 else Decimal("0"),
                        "credit": -net if net < 0 else Decimal("0"),
                        "description": txn.description or "Payment",
                    }
                ],
            )
        rewritten += 1
    db.flush()
    return rewritten


def recompute_invoice_cash_lines(
    db: Session, invoice: Invoice, *, adopted: bool = False
) -> int:
    """After an invoice's journal is reposted (an edit, #483): rewrite the
    cash recognition lines of each payment on it. `adopted` is for an invoice
    the QuickBooks Online ledger import had posted: its payments had no
    lines for it, so this writes them."""
    from app.services.accounting import get_ar_account_id

    if _ledger_posted(db, invoice):
        return 0

    def allocations(inv):
        return [(a.payment, a) for a in list(inv.payment_allocations)]

    def fully_applied(payment, alloc):
        n = (
            db.query(PaymentAllocation)
            .filter(PaymentAllocation.payment_id == payment.id)
            .count()
        )
        return n == 1 and Decimal(str(payment.amount)) == Decimal(str(alloc.amount))

    rewritten = _recompute(
        db,
        invoice,
        link_key=INVOICE_LINK,
        source_types=INVOICE_SOURCE_TYPES,
        control_id=get_ar_account_id(db),
        fallback_account_id=unapplied_income_id,
        allocations=allocations,
        fully_applied=fully_applied,
        adopted=adopted,
    )
    return rewritten + recompute_discount_cash_lines(db, invoice, invoice=True)


def recompute_bill_cash_lines(db: Session, bill: Bill) -> int:
    """The same for a bill's payments after its journal is reposted."""
    from app.services.accounting import get_ap_account_id

    def allocations(b):
        return [(a.bill_payment, a) for a in list(b.payment_allocations)]

    def fully_applied(payment, alloc):
        n = (
            db.query(BillPaymentAllocation)
            .filter(BillPaymentAllocation.bill_payment_id == payment.id)
            .count()
        )
        return n == 1 and Decimal(str(payment.amount)) == Decimal(str(alloc.amount))

    rewritten = _recompute(
        db,
        bill,
        link_key=BILL_LINK,
        source_types=BILL_SOURCE_TYPES,
        control_id=get_ap_account_id(db),
        fallback_account_id=unapplied_expense_id,
        allocations=allocations,
        fully_applied=fully_applied,
        adopted=False,
    )
    return rewritten + recompute_discount_cash_lines(db, bill, invoice=False)


def recompute_discount_cash_lines(db: Session, document, invoice: bool = True) -> int:
    """After an invoice's or bill's journal is reposted (#665): rewrite the
    cash lines of each early-payment discount standing on it from its lines
    as they now stand, as its payments' are, so the cash basis and the cash
    Sales Tax report (which prorates the discount from the document as it
    is) agree to the cent. A discount on or before the closing date keeps
    what it has. Returns the discounts rewritten."""
    from app.models.credit_memos import CreditMemo, CreditMemoStatus
    from app.models.vendor_credits import VendorCredit, VendorCreditStatus
    from app.services.accounting import get_ap_account_id, get_ar_account_id
    from app.services.audit import audited_bulk_delete
    from app.services.closing_date import get_closing_date

    if invoice:
        control_id = get_ar_account_id(db)
        standing = (
            db.query(CreditMemo)
            .filter(
                CreditMemo.original_invoice_id == document.id,
                CreditMemo.discount_payment_id.isnot(None),
                CreditMemo.status != CreditMemoStatus.VOID,
            )
            .all()
        )
    else:
        control_id = get_ap_account_id(db)
        standing = (
            db.query(VendorCredit)
            .filter(
                VendorCredit.original_bill_id == document.id,
                VendorCredit.discount_bill_payment_id.isnot(None),
                VendorCredit.status != VendorCreditStatus.VOID,
            )
            .all()
        )
    closing = get_closing_date(db)
    rewritten = 0
    for credit in standing:
        txn = (
            db.get(Transaction, credit.transaction_id)
            if credit.transaction_id
            else None
        )
        if txn is None or (closing is not None and txn.date <= closing):
            continue
        # the discount account: the accrual line that is not the control's
        account_id = next(
            (
                ln.account_id
                for ln in db.query(TransactionLine)
                .filter(
                    TransactionLine.transaction_id == txn.id,
                    TransactionLine.basis == BASIS_ACCRUAL,
                    TransactionLine.account_id != control_id,
                )
                .execution_options(**reading(READ_ALL))
            ),
            None,
        )
        if account_id is None:
            continue
        old = _cash_lines_of(db, txn.id)
        if old:
            audited_bulk_delete(
                db,
                db.query(TransactionLine)
                .filter(TransactionLine.id.in_([ln.id for ln in old]))
                .execution_options(**reading(READ_ALL)),
            )
        _insert_cash_lines(
            db,
            txn,
            discount_cash_lines(
                db,
                control_id,
                document,
                Decimal(str(credit.total)),
                account_id,
                txn.description or "Early-payment discount",
                invoice=invoice,
            ),
        )
        rewritten += 1
    db.flush()
    return rewritten


# ---------------------------------------------------------------------------
# Backfill: a file written before lines carried a basis
# ---------------------------------------------------------------------------


def _has_cash_line(db: Session, transaction_id: int) -> bool:
    return (
        db.query(
            exists().where(
                TransactionLine.transaction_id == transaction_id,
                TransactionLine.basis == BASIS_CASH,
            )
        )
        .execution_options(**reading(READ_ALL))
        .scalar()
    )


def _tag_accrual_entries(db: Session) -> int:
    """Every line of an accrual entry still marked both → accrual."""
    ids = select(Transaction.id).where(
        Transaction.source_type.in_(tuple(ACCRUAL_SOURCE_TYPES))
    )
    q = db.query(TransactionLine).filter(
        TransactionLine.transaction_id.in_(ids),
        TransactionLine.basis == BASIS_BOTH,
    )
    return q.update({TransactionLine.basis: BASIS_ACCRUAL}, synchronize_session=False)


def append_cash_lines(db: Session, txn: Transaction, lines: list[dict]) -> int:
    """Append cash lines to an existing entry: the backfill, and the
    reclass when an unapplied remainder is applied later. Straight inserts
    — the entry is dated and closing-date checked already, and the stored
    balance never takes a cash line — but the lines must balance among
    themselves, as create_journal_entry would insist."""
    from app.services.safe_errors import DataProblem

    out = sum(
        (_q(ln.get("debit", 0)) - _q(ln.get("credit", 0)) for ln in lines),
        Decimal("0"),
    )
    if out != 0:
        raise DataProblem(
            f"The cash-basis lines of this entry are out of balance by ${abs(out):,.2f}"
        )
    return _insert_cash_lines(db, txn, lines)


def _insert_cash_lines(db: Session, txn: Transaction, lines: list[dict]) -> int:
    """Insert cash lines into an entry; the caller keeps the entry's cash
    lines balanced."""
    from app.services.classes_service import default_function_of

    fn_cache: dict = {}
    for line in lines:
        debit = _q(line.get("debit", 0))
        credit = _q(line.get("credit", 0))
        if debit == 0 and credit == 0:
            continue
        class_id = line.get("class_id")
        function = (
            line["function"]
            if "function" in line
            else default_function_of(db, class_id, fn_cache)
        )
        db.add(
            TransactionLine(
                transaction_id=txn.id,
                account_id=line["account_id"],
                debit=debit,
                credit=credit,
                description=line.get("description", ""),
                job_id=line.get("job_id"),
                class_id=class_id,
                function=function,
                cost_code_id=line.get("cost_code_id"),
                cost_type=line.get("cost_type"),
                basis=BASIS_CASH,
                recognizes_invoice_id=line.get(INVOICE_LINK),
                recognizes_bill_id=line.get(BILL_LINK),
            )
        )
    return len(lines)


def _payments_without_cash_lines(db: Session):
    cash_lines = select(TransactionLine.transaction_id).where(
        TransactionLine.basis == BASIS_CASH
    )
    return (
        db.query(Payment)
        .filter(
            Payment.transaction_id.isnot(None),
            Payment.is_voided.isnot(True),
            Payment.transaction_id.notin_(cash_lines),
        )
        .order_by(Payment.date, Payment.id)
        .execution_options(**reading(READ_ALL))
        .all()
    )


def _bill_payments_without_cash_lines(db: Session):
    cash_lines = select(TransactionLine.transaction_id).where(
        TransactionLine.basis == BASIS_CASH
    )
    return (
        db.query(BillPayment)
        .filter(
            BillPayment.transaction_id.isnot(None),
            BillPayment.is_voided.isnot(True),
            BillPayment.transaction_id.notin_(cash_lines),
        )
        .order_by(BillPayment.date, BillPayment.id)
        .execution_options(**reading(READ_ALL))
        .all()
    )


def _customer_payment_lines(db: Session, payment: Payment, ar_id: int):
    """(entry, cash lines) for a customer payment that has none yet."""
    from app.services.currency import to_home

    txn = db.get(Transaction, payment.transaction_id)
    if txn is None or _has_cash_line(db, txn.id):
        return None, []
    pay_rate = Decimal(str(payment.exchange_rate or 1))
    allocations = (
        db.query(PaymentAllocation)
        .filter(PaymentAllocation.payment_id == payment.id)
        .all()
    )
    applied = []
    applied_total = Decimal("0")
    for alloc in allocations:
        invoice = db.get(Invoice, alloc.invoice_id)
        if invoice is None:
            continue
        applied.append(
            (invoice, to_home(alloc.amount, invoice.exchange_rate or Decimal("1")))
        )
        applied_total += Decimal(str(alloc.amount))
    unapplied = to_home(
        max(Decimal(str(payment.amount)) - applied_total, Decimal("0")), pay_rate
    )
    return txn, customer_payment_cash_lines(
        db, ar_id, applied, unapplied, txn.description or "Payment"
    )


def _bill_payment_lines(db: Session, payment: BillPayment, ap_id: int):
    """(entry, cash lines) for a bill payment that has none yet."""
    from app.services.currency import to_home

    txn = db.get(Transaction, payment.transaction_id)
    if txn is None or _has_cash_line(db, txn.id):
        return None, []
    pay_rate = Decimal(str(payment.exchange_rate or 1))
    allocations = (
        db.query(BillPaymentAllocation)
        .filter(BillPaymentAllocation.bill_payment_id == payment.id)
        .all()
    )
    applied = []
    applied_total = Decimal("0")
    for alloc in allocations:
        bill = db.get(Bill, alloc.bill_id)
        if bill is None:
            continue
        applied.append(
            (bill, to_home(alloc.amount, bill.exchange_rate or Decimal("1")))
        )
        applied_total += Decimal(str(alloc.amount))
    unapplied = to_home(
        max(Decimal(str(payment.amount)) - applied_total, Decimal("0")), pay_rate
    )
    return txn, bill_payment_cash_lines(
        db, ap_id, applied, unapplied, txn.description or "Bill payment"
    )


def add_payment_cash_lines(db: Session, payment: Payment) -> int:
    """Give one posted customer payment its cash lines now, when it has none
    (a payment posted before the invoice it pays had its own entry). Returns
    the number of lines written."""
    from app.services.accounting import get_ar_account_id

    txn, lines = _customer_payment_lines(db, payment, get_ar_account_id(db))
    return append_cash_lines(db, txn, lines) if lines else 0


class _Progress:
    """Counts the backfill's writes and commits every BACKFILL_BATCH of them
    so a big book never holds one long write transaction."""

    def __init__(self, db: Session):
        self.db = db
        self.pending = 0
        self.failed = 0

    def run(self, payments, make_lines, label: str) -> int:
        """Write the cash lines of each payment inside its own savepoint:
        one broken payment is logged by id and skipped, the rest go on."""
        wrote = 0
        for payment in payments:
            payment_id = payment.id  # read before a failure expires the row
            try:
                with self.db.begin_nested():
                    txn, lines = make_lines(self.db, payment)
                    if not lines:
                        continue
                    append_cash_lines(self.db, txn, lines)
            except Exception:
                self.failed += 1
                log.exception(
                    "Cash-basis backfill skipped %s %s (entry %s)",
                    label,
                    payment_id,
                    getattr(payment, "transaction_id", None),
                )
                continue
            wrote += 1
            self.pending += 1
            if self.pending >= BACKFILL_BATCH:
                self.db.commit()
                self.pending = 0
        return wrote


def backfill(db: Session) -> dict:
    """Tag the accrual entries and write the cash lines of every payment
    that has none (#483). Idempotent: a second run finds nothing. Commits
    as it goes (every BACKFILL_BATCH payments) and a payment that fails is
    logged and skipped, not fatal. Returns counts for the start-up log:
    tagged, payments, bill_payments and failed."""
    from app.services.accounting import get_ap_account_id, get_ar_account_id
    from app.services.control_accounts import MissingControlAccount

    tagged = _tag_accrual_entries(db)
    db.commit()
    progress = _Progress(db)
    payments = bills = 0

    try:
        ar_id = get_ar_account_id(db)
    except MissingControlAccount:
        ar_id = None
    if ar_id is not None:
        payments = progress.run(
            _payments_without_cash_lines(db),
            lambda d, p: _customer_payment_lines(d, p, ar_id),
            "payment",
        )

    try:
        ap_id = get_ap_account_id(db)
    except MissingControlAccount:
        ap_id = None
    if ap_id is not None:
        bills = progress.run(
            _bill_payments_without_cash_lines(db),
            lambda d, p: _bill_payment_lines(d, p, ap_id),
            "bill payment",
        )

    db.commit()
    return {
        "tagged": tagged,
        "payments": payments,
        "bill_payments": bills,
        "failed": progress.failed,
    }


def backfill_after_import(db: Session) -> dict:
    """Give the payments an importer just created their cash lines (#483),
    so the cash reports are right without a restart. Never fails the import:
    a payment that cannot be given cash lines is logged and retried at the
    next start."""
    try:
        return backfill(db)
    except Exception:
        db.rollback()
        log.exception("Cash-basis backfill after an import failed")
        return {"tagged": 0, "payments": 0, "bill_payments": 0, "failed": 0}


def pending_backfill(db: Session) -> bool:
    """Whether the backfill has anything to do (the tests' check)."""
    untagged = (
        db.query(TransactionLine.id)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(
            Transaction.source_type.in_(tuple(ACCRUAL_SOURCE_TYPES)),
            TransactionLine.basis == BASIS_BOTH,
        )
        .execution_options(**reading(READ_ALL))
        .first()
    )
    if untagged:
        return True
    return bool(_payments_without_cash_lines(db)) or bool(
        _bill_payments_without_cash_lines(db)
    )


__all__ = [
    "ACCRUAL_SOURCE_TYPES",
    "add_payment_cash_lines",
    "append_cash_lines",
    "apply_reclass_lines",
    "backfill",
    "bill_payment_cash_lines",
    "company_basis",
    "customer_payment_cash_lines",
    "default_basis_for",
    "pending_backfill",
    "reading",
    "recompute_bill_cash_lines",
    "recompute_invoice_cash_lines",
    "retainage_release_cash_lines",
    "resolve_basis",
    "unapplied_expense_id",
    "unapplied_income_id",
]
