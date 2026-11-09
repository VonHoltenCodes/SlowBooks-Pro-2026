import re
from datetime import date
from datetime import date as dt_date
from decimal import Decimal
from typing import Optional

from fastapi import Depends, HTTPException, Query, Request
from app.schemas.common import StrictModel
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.database import get_db
from app.services.accounting import _q, taxable_subtotal
from app.models.accounts import Account
from app.models.invoices import Invoice, InvoiceStatus
from app.models.contacts import Vendor
from app.routes.reports._router import router
from app.services.payment_terms import document_due_date
from app.services import cash_basis
from app.routes._helpers import refuse_unknown_filters

# accrual, cash, or the company default (#483), as the statements take it
_BASIS_QUERY = Query(
    default=None,
    description="accrual or cash; omitted, the company's default report basis",
)


class SalesTaxPaymentRequest(StrictModel):
    # `dt_date`, not `date`: inside the class body the field named `date`
    # shadows the type, so `Optional[date]` became `Optional[None]` and every
    # real date was refused with "date: Input should be None" (2.17.3).
    date: Optional[dt_date] = None
    amount: Decimal
    pay_from_account_id: int
    check_number: Optional[str] = ""
    reference: Optional[str] = None


@router.get("/sales-tax")
def sales_tax_report(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    basis: str = _BASIS_QUERY,
    db: Session = Depends(get_db),
):
    """Sales Tax report: the tax charged on sales less the tax given back on
    credit memos, in the company's currency, checked against Sales Tax
    Payable (2200).

    Credit memos were left out (2.17.3, macbase1 F16), so the report read
    higher than 2200 by the tax on every return. A document with nothing
    taxable carries no rate: an all-labour invoice is not "8.25%, $0.00".
    """
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date.today()
    basis = cash_basis.resolve_basis(db, basis if isinstance(basis, str) else None)
    if basis == "cash":
        return _sales_tax_cash(db, start_date, end_date)

    # Eager loads avoid an N+1 on .customer / .lines in the loops below.
    from sqlalchemy.orm import joinedload, selectinload

    from app.models.credit_memos import CreditMemo, CreditMemoStatus
    from app.services.currency import to_home

    invoices = (
        db.query(Invoice)
        .options(joinedload(Invoice.customer), selectinload(Invoice.lines))
        .filter(Invoice.date >= start_date, Invoice.date <= end_date)
        .filter(Invoice.status != InvoiceStatus.VOID)
        .all()
    )
    # A write-off forgives a balance to Bad Debt; it returns no goods and
    # carries no tax, so it is not a sale reversed.
    memos = (
        db.query(CreditMemo)
        .options(joinedload(CreditMemo.customer), selectinload(CreditMemo.lines))
        .filter(CreditMemo.date >= start_date, CreditMemo.date <= end_date)
        .filter(CreditMemo.status != CreditMemoStatus.VOID)
        .filter(CreditMemo.is_write_off.isnot(True))
        # nor is an early-payment discount (#664)
        .filter(CreditMemo.discount_payment_id.is_(None))
        .all()
    )

    rows = []
    for inv in invoices:
        # Booked amounts: a foreign-currency invoice posts to the ledger at
        # its exchange rate, and the tax is owed in the company's currency.
        fx = Decimal(str(inv.exchange_rate or 1))
        tax = to_home(inv.tax_amount or 0, fx)
        # Only the taxable lines form the base (a labor line on a
        # customer-owned device sits beside a taxed part).
        taxable = to_home(taxable_subtotal(inv.lines), fx) if tax > 0 else _q(0)
        subtotal = to_home(inv.subtotal or 0, fx)
        rows.append(("invoice", inv, inv.invoice_number, subtotal, taxable, tax))
    for cm in memos:
        tax = _q(cm.tax_amount or 0)
        taxable = taxable_subtotal(cm.lines) if tax > 0 else _q(0)
        subtotal = _q(cm.subtotal or 0)
        rows.append(("credit_memo", cm, cm.memo_number, -subtotal, -taxable, -tax))
    rows.sort(key=lambda r: (r[1].date, r[0] != "invoice", str(r[2])))

    items = []
    for kind, doc, number, subtotal, taxable, tax in rows:
        items.append(
            {
                "type": kind,
                "date": doc.date.isoformat(),
                "number": number,
                "invoice_number": number if kind == "invoice" else None,
                "memo_number": number if kind == "credit_memo" else None,
                "customer_name": doc.customer.name if doc.customer else "",
                "subtotal": float(subtotal),
                "taxable": float(taxable),
                "tax_rate": float(doc.tax_rate or 0) if taxable else None,
                "tax_amount": float(tax),
            }
        )

    total_sales = sum((r[3] for r in rows), Decimal(0))
    total_taxable = sum((r[4] for r in rows), Decimal(0))
    tax_on_sales = sum((r[5] for r in rows if r[0] == "invoice"), Decimal(0))
    tax_credited = -sum((r[5] for r in rows if r[0] == "credit_memo"), Decimal(0))
    total_tax = tax_on_sales - tax_credited

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "items": items,
        "total_sales": float(total_sales),
        "total_taxable": float(total_taxable),
        "total_non_taxable": float(total_sales - total_taxable),
        "tax_on_sales": float(tax_on_sales),
        "tax_credited": float(tax_credited),
        "total_tax": float(total_tax),
        "ledger": _sales_tax_ledger(db, start_date, end_date, total_tax),
        "use_tax": _use_tax_section(db, start_date, end_date),
        "basis": "accrual",
        "basis_note": None,
    }


def _sales_tax_cash(db: Session, start_date, end_date):
    """The report on the cash basis (#483): the tax collected on money
    received in the dates, each payment's allocation to an invoice taking
    that invoice's sales, taxable amount and tax in the proportion paid (the
    proportion the payment's own 2200 cash lines were written in). Credit
    memos are accrual documents and never counted: no cash came for them.
    An early-payment discount is the exception (#664): it settles its share
    as a payment does, and its cash lines take that share of the tax."""
    from sqlalchemy.orm import joinedload, selectinload

    from app.models.payments import Payment, PaymentAllocation
    from app.services.currency import to_home

    payments = (
        db.query(Payment)
        .options(
            selectinload(Payment.allocations)
            .joinedload(PaymentAllocation.invoice)
            .options(joinedload(Invoice.customer), selectinload(Invoice.lines))
        )
        .filter(Payment.date >= start_date, Payment.date <= end_date)
        .filter(Payment.is_voided.isnot(True))
        .all()
    )
    # An early-payment discount settles its share of an invoice with no
    # cash, and the cash basis recognizes it as it does a payment (#664),
    # so the discount takes its share of the sales and the tax here too.
    from app.models.credit_memos import CreditMemo, CreditMemoStatus

    discounts = (
        db.query(CreditMemo)
        .options(
            joinedload(CreditMemo.original_invoice).options(
                joinedload(Invoice.customer), selectinload(Invoice.lines)
            )
        )
        .filter(CreditMemo.discount_payment_id.isnot(None))
        .filter(CreditMemo.status != CreditMemoStatus.VOID)
        .filter(CreditMemo.date >= start_date, CreditMemo.date <= end_date)
        .all()
    )
    settled = [
        (pay, alloc.invoice, alloc.amount)
        for pay in payments
        for alloc in pay.allocations
    ] + [(cm, cm.original_invoice, cm.total) for cm in discounts]
    rows = []
    for pay, inv, amount in settled:
        if inv is None or inv.status == InvoiceStatus.VOID:
            continue
        fx = Decimal(str(inv.exchange_rate or 1))
        # The base the payment's own cash lines were prorated on: the
        # invoice's amount due when it was issued, net of the retainage
        # it holds back and with any it releases (a pay application's,
        # #555). Dividing by the invoice total read less tax than 2200's
        # cash lines took on a retainage invoice (2.30.0 gate NEW-184).
        due = to_home(
            Decimal(str(inv.total or 0))
            - Decimal(str(inv.retainage_held or 0))
            + Decimal(str(inv.retainage_released or 0)),
            fx,
        )
        applied = to_home(amount, fx)
        if due == 0 or applied == 0:
            continue
        ratio = applied / due
        tax_full = to_home(inv.tax_amount or 0, fx)
        taxable_full = (
            to_home(taxable_subtotal(inv.lines), fx) if tax_full > 0 else _q(0)
        )
        rows.append(
            (
                pay,
                inv,
                _q(to_home(inv.subtotal or 0, fx) * ratio),
                _q(taxable_full * ratio),
                _q(tax_full * ratio),
            )
        )
    rows.sort(key=lambda r: (r[0].date, r[0].id, str(r[1].invoice_number)))

    items = [
        {
            "type": "invoice",
            "date": pay.date.isoformat(),
            "number": inv.invoice_number,
            "invoice_number": inv.invoice_number,
            "memo_number": None,
            "customer_name": inv.customer.name if inv.customer else "",
            "subtotal": float(subtotal),
            "taxable": float(taxable),
            "tax_rate": float(inv.tax_rate or 0) if taxable else None,
            "tax_amount": float(tax),
        }
        for pay, inv, subtotal, taxable, tax in rows
    ]
    total_sales = sum((r[2] for r in rows), Decimal(0))
    total_taxable = sum((r[3] for r in rows), Decimal(0))
    total_tax = sum((r[4] for r in rows), Decimal(0))
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "items": items,
        "total_sales": float(total_sales),
        "total_taxable": float(total_taxable),
        "total_non_taxable": float(total_sales - total_taxable),
        "tax_on_sales": float(total_tax),
        "tax_credited": 0.0,
        "total_tax": float(total_tax),
        "ledger": _sales_tax_ledger(db, start_date, end_date, total_tax, "cash"),
        "use_tax": _use_tax_section(db, start_date, end_date, "cash"),
        "basis": "cash",
        "basis_note": cash_basis.receivable_note(db, end_date, "cash"),
    }


def _sales_tax_ledger(
    db: Session, start_date, end_date, total_tax: Decimal, basis: str = "accrual"
):
    """What Sales Tax Payable (2200) says for the same period: the tax posted
    to it (sales and credit memos, and anything else that touched it), the
    payments made, the balance owed at the end date, and how far the posted
    tax is from the report's total. A difference names a posting the report
    cannot see: tax on a bill, a journal entry, a void of an earlier sale."""
    from sqlalchemy import or_

    from app.models.transactions import Transaction, TransactionLine
    from app.services import control_accounts

    tax_id = control_accounts.find(db, "2200")
    if tax_id is None:
        return None
    account = db.get(Account, tax_id)

    def owed(*conds) -> Decimal:
        dr, cr = (
            db.query(
                sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
                sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
            )
            .join(Transaction, TransactionLine.transaction_id == Transaction.id)
            .filter(TransactionLine.account_id == tax_id, *conds)
            .execution_options(**cash_basis.reading(basis))
            .one()
        )
        return _q(Decimal(str(cr)) - Decimal(str(dr)))

    in_period = (Transaction.date >= start_date, Transaction.date <= end_date)
    payment = Transaction.source_type == "sales_tax_payment"
    posted = owed(*in_period, or_(Transaction.source_type.is_(None), ~payment))
    # Tax paid to suppliers that bills and vendor credits posted here before
    # 2.18 (it is part of a purchase's cost now, never Sales Tax Payable).
    # Named so the difference it causes is explained, with the correction.
    purchase = Transaction.source_type.in_(
        ("bill", "bill_void", "vendor_credit", "vendor_credit_void")
    )
    return {
        "account_number": account.account_number,
        "account_name": account.name,
        "tax_posted": float(posted),
        "payments": float(-owed(*in_period, payment)),
        "balance": float(owed(Transaction.date <= end_date)),
        "difference": float(_q(total_tax - posted)),
        "purchase_tax": float(-owed(*in_period, purchase)),
        "purchase_tax_to_date": float(-owed(Transaction.date <= end_date, purchase)),
    }


# The entries that accrue use tax on 2220 Use Tax Payable: the bills that
# post it, their voids and edits, and (on the cash basis) the bill payments
# that recognize it. Any other debit to 2220 is the tax paid to the agency.
_USE_TAX_ACCRUING = (
    "bill",
    "bill_void",
    "bill_edit",
    "bill_payment",
    "bill_payment_void",
)


def _use_tax_section(db: Session, start_date, end_date, basis: str = "accrual"):
    """The Use tax section (construction): the use tax material bills accrued
    on 2220 Use Tax Payable in the dates, line by line, what was paid, and
    the balance owed at the end date. None for a company that is not
    construction and whose 2220 never moved (a report read creates nothing)."""
    from sqlalchemy import and_, or_

    from app.models.bills import Bill
    from app.models.jobs import Job
    from app.models.transactions import Transaction, TransactionLine
    from app.services.purchase_posting import use_tax_rate
    from app.services.terminology import terms_from_db

    account = db.query(Account).filter(Account.name == "Use Tax Payable").first()
    construction = terms_from_db(db).is_construction
    if account is None:
        return None if not construction else _empty_use_tax(db, use_tax_rate)

    def lines(*conds):
        return (
            db.query(TransactionLine, Transaction)
            .join(Transaction, TransactionLine.transaction_id == Transaction.id)
            .filter(TransactionLine.account_id == account.id, *conds)
            .execution_options(**cash_basis.reading(basis))
        )

    accruing = Transaction.source_type.in_(_USE_TAX_ACCRUING)
    paying = or_(Transaction.source_type.is_(None), ~accruing)
    in_period = and_(Transaction.date >= start_date, Transaction.date <= end_date)

    def net(query) -> Decimal:
        return _q(
            sum(
                (
                    Decimal(str(tl.credit or 0)) - Decimal(str(tl.debit or 0))
                    for tl, _t in query
                ),
                Decimal("0"),
            )
        )

    if not construction and not lines().first():
        return None
    items = []
    for tl, txn in lines(in_period, accruing).order_by(
        Transaction.date, TransactionLine.id
    ):
        # a bill's own entries carry its id; a payment's carry the payment's
        on_bill = txn.source_type in ("bill", "bill_void", "bill_edit")
        bill = db.get(Bill, txn.source_id) if on_bill and txn.source_id else None
        job = db.get(Job, tl.job_id) if tl.job_id else None
        items.append(
            {
                "date": txn.date.isoformat(),
                "type": txn.source_type,
                "bill_id": bill.id if bill else None,
                "bill_number": bill.bill_number if bill else None,
                "vendor_name": bill.vendor.name if bill and bill.vendor else "",
                "job_name": job.name if job else "",
                "description": re.sub(r" \([\d.]+%\)$", "", tl.description or ""),
                "rate": _booked_use_tax_rate(tl.description),
                "amount": float(
                    _q(Decimal(str(tl.credit or 0)) - Decimal(str(tl.debit or 0)))
                ),
            }
        )
    return {
        "account_id": account.id,
        "account_number": account.account_number,
        "account_name": account.name,
        "rate": float(use_tax_rate(db)),
        "items": items,
        "accrued": float(net(lines(in_period, accruing))),
        "paid": float(-net(lines(in_period, paying))),
        "balance": float(net(lines(Transaction.date <= end_date))),
    }


def _booked_use_tax_rate(memo: Optional[str]):
    """The rate (a fraction) an accrual was booked at, read from the "(6.5%)"
    its memo ends with; None for an entry that does not carry one."""
    match = re.search(r"\(([\d.]+)%\)$", memo or "")
    if match is None:
        return None
    return float(Decimal(match.group(1)) / Decimal("100"))


def _empty_use_tax(db: Session, use_tax_rate):
    """A construction company whose 2220 does not exist yet: nothing accrued."""
    return {
        "account_id": None,
        "account_number": None,
        "account_name": "Use Tax Payable",
        "rate": float(use_tax_rate(db)),
        "items": [],
        "accrued": 0.0,
        "paid": 0.0,
        "balance": 0.0,
    }


@router.post("/sales-tax/pay")
def pay_sales_tax(data: SalesTaxPaymentRequest, db: Session = Depends(get_db)):
    """Record a sales tax payment — DR Sales Tax Payable, CR Bank Account"""
    from app.services.accounting import create_journal_entry, get_sales_tax_account_id
    from app.services.closing_date import check_closing_date

    pay_date = data.date or date.today()
    check_closing_date(db, pay_date)

    amount = _q(data.amount)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    bank_account = (
        db.query(Account).filter(Account.id == data.pay_from_account_id).first()
    )
    if not bank_account:
        raise HTTPException(status_code=404, detail="Bank account not found")
    # Tax is paid out of a bank or card account. The picker used to offer
    # every asset (Accounts Receivable, Inventory, Undeposited Funds ...)
    # and the credit landed wherever the user pointed it.
    if not bank_account.bank_kind:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{bank_account.name} is not a bank or credit card account. "
                "Pick the bank or credit card account the tax was paid from."
            ),
        )

    tax_account_id = get_sales_tax_account_id(db)
    if not tax_account_id:
        raise HTTPException(
            status_code=400, detail="Sales Tax Payable account (2200) not found"
        )

    journal_lines = [
        {
            "account_id": tax_account_id,
            "debit": amount,
            "credit": Decimal("0"),
            "description": "Sales tax payment",
        },
        {
            "account_id": bank_account.id,
            "debit": Decimal("0"),
            "credit": amount,
            "description": "Sales tax payment",
        },
    ]

    reference = data.reference if data.reference is not None else data.check_number

    txn = create_journal_entry(
        db,
        pay_date,
        "Sales Tax Payment",
        journal_lines,
        source_type="sales_tax_payment",
        reference=reference,
    )
    db.commit()
    return {"status": "ok", "transaction_id": txn.id, "amount": float(amount)}


def ap_aging_report(db: Session, as_of_date: date) -> dict:
    """The A/P Aging report's figures, in home currency, netted of the credits
    a vendor holds for us (unapplied vendor credits and the part of a bill
    payment not yet applied to a bill), so the total equals account 2000 and
    each vendor's row equals the vendor balance. The analytics page and the
    assistant read these too, so no two views of payables disagree (found
    integrating the 2.17.3 exploratory fixes: the A/R side had been made to
    tie; this side still summed raw document-currency balances)."""
    from app.services.contact_balances import home_amount
    from app.services.open_balances import (
        open_bills_as_of,
        unapplied_bill_payments_as_of,
        unapplied_vendor_credits_as_of,
    )

    vendor_names = {v.id: v.name for v in db.query(Vendor.id, Vendor.name).all()}
    aging: dict = {}

    def row(vid):
        if vid not in aging:
            aging[vid] = {
                "vendor_name": vendor_names.get(vid, "Unknown"),
                "vendor_id": vid,
                "current": Decimal(0),
                "over_30": Decimal(0),
                "over_60": Decimal(0),
                "over_90": Decimal(0),
                "total": Decimal(0),
                "unapplied_credits": Decimal(0),
            }
        return aging[vid]

    # What was open on the date, not what is open today: a bill paid since
    # still counts, so the total is account 2000 on that date, as the A/R
    # side does. Only bills dated on or before it (#285).
    bills = open_bills_as_of(db, as_of_date)
    for bill, open_amount in bills:
        r = row(bill.vendor_id)
        # A bill with no due date (bills made from a PO before 2.18
        # never got one) ages from its date and terms, not as current.
        # by the bill's own rule (#664): its term if it was saved under one,
        # else the old reading of its text, never a term added since
        due = bill.due_date or document_due_date(db, bill)
        days = (as_of_date - due).days
        bal = home_amount(open_amount, bill.exchange_rate)
        if days <= 0:
            r["current"] += bal
        elif days <= 30:
            r["over_30"] += bal
        elif days <= 60:
            r["over_60"] += bal
        else:
            r["over_90"] += bal
        r["total"] += bal

    def credit(vid, amount):
        # A credit has no due date, so it reduces the newest bucket — it is
        # money available now, not money aged — and shows on its own line,
        # because "you owe 700" and "you owe 1,000 and hold a 300 credit" are
        # different facts to a person about to pay a vendor (issue #129).
        r = row(vid)
        r["unapplied_credits"] += amount
        r["current"] -= amount
        r["total"] -= amount

    for vc, left in unapplied_vendor_credits_as_of(db, as_of_date):
        credit(vc.vendor_id, _q(left))

    for bp, left in unapplied_bill_payments_as_of(db, as_of_date):
        credit(bp.vendor_id, home_amount(left, bp.exchange_rate))

    _COLS = ("current", "over_30", "over_60", "over_90", "total", "unapplied_credits")
    items = sorted(aging.values(), key=lambda i: (i["vendor_name"] or "").lower())
    totals = {"vendor_name": "TOTAL", "vendor_id": 0}
    for k in _COLS:
        totals[k] = float(sum((i[k] for i in items), Decimal(0)))
    for item in items:
        for k in _COLS:
            item[k] = float(item[k])
    return {"as_of_date": as_of_date.isoformat(), "items": items, "totals": totals}


@router.get("/ap-aging", dependencies=[Depends(refuse_unknown_filters)])
def ap_aging(as_of_date: date = Query(default=None), db: Session = Depends(get_db)):
    """AP Aging report — mirrors AR aging but for bills."""
    return ap_aging_report(db, as_of_date or date.today())


@router.get("/ap-aging/csv", dependencies=[Depends(refuse_unknown_filters)])
def ap_aging_csv_route(
    request: Request,
    as_of_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """The A/P Aging as a spreadsheet (#393): the file the Accountant's
    Package carries, on its own. Always the whole company, as the screen
    is (opened from a vendor's page it only highlights their row)."""
    from app.routes.csv import _csv_response
    from app.routes.reports.financial import _company_name
    from app.services.ledger_exports import aging_csv

    data = ap_aging_report(db, as_of_date or date.today())
    name = f"ap-aging_{data['as_of_date']}"
    return _csv_response(
        aging_csv(data, _company_name(db), "ap"), f"{name}.csv", request
    )


@router.get("/1099-summary")
def report_1099_summary(
    year: int = Query(default=None),
    db: Session = Depends(get_db),
):
    """1099 Summary: total payments to 1099 vendors for a year."""
    if not year:
        year = date.today().year

    from app.models.bills import BillPayment, BillPaymentAllocation

    from app.services.form_1099 import is_1099_vendor, register_payment_totals

    # The same rule as the 1099-NEC / 1096, so a vendor on this summary is
    # on those forms too (NEC type).
    vendors_1099 = db.query(Vendor).filter(is_1099_vendor()).all()
    if not vendors_1099:
        return {"year": year, "items": [], "total": 0, "vendors_above_threshold": 0}

    # Single query: sum bill payment allocations grouped by vendor for the year.
    # Replaces a query-per-vendor loop that made this O(N) in DB round-trips.
    totals_by_vendor = dict(
        db.query(
            BillPayment.vendor_id,
            sqlfunc.coalesce(sqlfunc.sum(BillPaymentAllocation.amount), 0),
        )
        .join(
            BillPaymentAllocation,
            BillPaymentAllocation.bill_payment_id == BillPayment.id,
        )
        .filter(sqlfunc.extract("year", BillPayment.date) == year)
        # a voided payment keeps its allocation rows (the void restores the
        # bills' balances and flags the payment); it is not money paid (#287)
        .filter(BillPayment.is_voided.isnot(True))
        .group_by(BillPayment.vendor_id)
        .all()
    )
    # plus the register entries that name the vendor (#590): the same source
    # the 1099-NEC adds beside bill payments
    for vid, paid in register_payment_totals(db, year).items():
        totals_by_vendor[vid] = Decimal(str(totals_by_vendor.get(vid, 0))) + paid

    from app.services.form_1099 import nec_threshold

    threshold = nec_threshold(year)  # $600 through 2025, $2,000 from 2026
    items = []
    total = Decimal(0)
    above_threshold = 0
    missing_w9 = 0  # reportable, and no W-9 on file (#257)

    for vendor in vendors_1099:
        vendor_total = Decimal(str(totals_by_vendor.get(vendor.id, 0) or 0))
        total += vendor_total
        flagged = vendor_total >= threshold
        if flagged:
            above_threshold += 1
            if not vendor.w9_on_file:
                missing_w9 += 1

        items.append(
            {
                "vendor_id": vendor.id,
                "vendor_name": vendor.name,
                "tax_id": vendor.tax_id or "",
                "vendor_1099_type": vendor.vendor_1099_type or "NEC",
                "total_paid": float(vendor_total),
                "above_threshold": flagged,
                "w9_on_file": bool(vendor.w9_on_file),
            }
        )

    items.sort(key=lambda x: x["total_paid"], reverse=True)

    return {
        "year": year,
        "items": items,
        "total": float(total),
        "vendors_above_threshold": above_threshold,
        "vendors_missing_w9": missing_w9,
        "threshold": float(threshold),
    }
