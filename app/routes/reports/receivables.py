import html
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import Response
from app.schemas.common import StrictModel
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.services.accounting import _q
from app.services.fiscal_year import fiscal_year_start
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment
from app.models.contacts import Customer
from app.services.pdf_service import (
    generate_statement_pdf,
    generate_collection_letter_pdf,
)
from app.services.settings_service import get_all_settings as get_settings
from app.services.request_utils import content_disposition
from app.routes.reports._router import router
from app.routes._roles import is_admin
from app.services.email_errors import sentence_for_last_send, where_to_see_why
from app.routes._helpers import refuse_unknown_filters

logger = logging.getLogger(__name__)


class CollectionLetterRequest(StrictModel):
    letter_type: str = "30"
    customer_ids: Optional[list[int]] = None
    send_email: bool = False


_AGING_COLS = ("current", "over_30", "over_60", "over_90", "total", "unapplied_credits")


def _aging_row(customer_names: dict, cid: int) -> dict:
    return {
        "customer_name": customer_names.get(cid, "Unknown"),
        "customer_id": cid,
        "current": Decimal(0),
        "over_30": Decimal(0),
        "over_60": Decimal(0),
        "over_90": Decimal(0),
        "total": Decimal(0),
        "unapplied_credits": Decimal(0),
    }


def _customer_labels(db: Session) -> dict[int, str]:
    """Every customer's name for a report row: "Parent: Sub" for a
    sub-customer (#638), so a tenant's row reads under its property."""
    rows = db.query(Customer.id, Customer.name, Customer.parent_customer_id).all()
    names = {cid: name for cid, name, _ in rows}
    return {
        cid: f"{names[pid]}: {name}" if pid in names else name
        for cid, name, pid in rows
    }


@router.get("/ar-aging", dependencies=[Depends(refuse_unknown_filters)])
def ar_aging(as_of_date: date = Query(default=None), db: Session = Depends(get_db)):
    """What each customer owes, by age, in home currency — and net of the
    credits they hold, so the total is what account 1100 says.

    Two gaps made the total disagree with the balance sheet (explore
    2.17.3, W-H9 / F17): a foreign-currency invoice counted at its document
    amount (EUR 850) instead of what it was booked at (USD 935), and money
    a customer paid that was not applied to an invoice was left out
    entirely (unapplied_credits read 0 for everyone)."""
    return ar_aging_report(db, as_of_date or date.today())


@router.get("/ar-aging/csv", dependencies=[Depends(refuse_unknown_filters)])
def ar_aging_csv_route(
    request: Request,
    as_of_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """The A/R Aging as a spreadsheet (#393): the file the Accountant's
    Package carries, on its own. Always the whole company, as the screen
    is (opened from a customer's page it only highlights their row)."""
    from app.routes.csv import _csv_response
    from app.routes.reports.financial import _company_name
    from app.services.ledger_exports import aging_csv

    data = ar_aging_report(db, as_of_date or date.today())
    name = f"ar-aging_{data['as_of_date']}"
    return _csv_response(
        aging_csv(data, _company_name(db), "ar"), f"{name}.csv", request
    )


@router.get("/ar-aging/pdf", dependencies=[Depends(refuse_unknown_filters)])
def ar_aging_pdf(as_of_date: date = Query(default=None), db: Session = Depends(get_db)):
    """The A/R Aging in the report PDF theme (#808): the as-of date, the
    buckets, a row per customer and the totals, as the screen shows them.
    Always the whole company, as the screen and Save CSV are."""
    from app.routes.reports.financial import _pdf_response
    from app.services.ledger_exports import aging_section
    from app.services.terminology import terms_from_db

    data = ar_aging_report(db, as_of_date or date.today())
    return _pdf_response(
        [aging_section(data, "ar", terms_from_db(db))],
        db,
        f"ar-aging_{data['as_of_date']}.pdf",
    )


def ar_aging_report(db: Session, as_of_date: date) -> dict:
    """The A/R Aging report's figures (see ar_aging). The analytics page's
    aging chart reads these too, so the two cannot disagree."""
    from app.services.contact_balances import home_amount
    from app.services.open_balances import open_invoices_as_of

    # What was open on the date, not what is open today: an invoice paid
    # since still counts, so the total is account 1100 on that date.
    invoices = open_invoices_as_of(db, as_of_date)

    customer_names = _customer_labels(db)
    # A sub-customer billed with its parent (#638) is on the parent's A/R:
    # its open items age on the parent's row, and the row keeps, under
    # "subs", what each such sub contributed, so the screen can show the
    # sub's balance under the parent without counting it twice. A sub
    # billed on its own has its own row, "Parent: Sub".
    from app.services.sub_customers import billed_with_parent

    routed = billed_with_parent(db)

    aging = {}
    detail: dict[int, dict[int, dict]] = {}

    def rows_for(cid: int) -> list[dict]:
        """The report row the customer's money lands on, and the detail
        row under the parent when it is a sub billed with it."""
        owner = routed.get(cid, cid)
        if owner not in aging:
            aging[owner] = _aging_row(customer_names, owner)
        out = [aging[owner]]
        if owner != cid:
            sub_rows = detail.setdefault(owner, {})
            if cid not in sub_rows:
                sub_rows[cid] = _aging_row(customer_names, cid)
            out.append(sub_rows[cid])
        return out

    for inv, open_amount in invoices:
        days = (as_of_date - inv.due_date).days if inv.due_date else 0
        # At the rate it was booked at, as the ledger carries it.
        bal = home_amount(open_amount, inv.exchange_rate)
        for row in rows_for(inv.customer_id):
            if days <= 0:
                row["current"] += bal
            elif days <= 30:
                row["over_30"] += bal
            elif days <= 60:
                row["over_60"] += bal
            else:
                row["over_90"] += bal
            row["total"] += bal

    # Unapplied credit memos. A credit memo credits A/R the moment it is
    # issued, so summing only invoice balances reads HIGHER than account
    # 1100 by every credit not yet applied — the aging and the control
    # account disagree, and the report overstates what customers owe. Found
    # while building the payable-side twin (issue #129); fixed on both sides
    # together so they cannot drift apart again.
    from app.services.open_balances import (
        unapplied_credit_memos_as_of,
        unapplied_payments_as_of,
    )

    held = [
        (cm.customer_id, _q(left))
        for cm, left in unapplied_credit_memos_as_of(db, as_of_date)
    ]
    # ...and payments, or the part of one, not applied to any invoice: they
    # credited A/R in full when received, at the payment's rate.
    held += [
        (p.customer_id, home_amount(left, p.exchange_rate))
        for p, left in unapplied_payments_as_of(db, as_of_date)
    ]
    for cid, amt in held:
        for row in rows_for(cid):
            row["unapplied_credits"] += amt
            # A credit has no due date: it offsets the newest bucket.
            row["current"] -= amt
            row["total"] -= amt

    _COLS = _AGING_COLS
    items = sorted(aging.values(), key=lambda i: (i["customer_name"] or "").lower())
    totals = {"customer_name": "TOTAL", "customer_id": 0}
    for k in _COLS:
        totals[k] = sum((i[k] for i in items), Decimal(0))
    # Convert Decimals to float for JSON
    for item in items:
        for k in _COLS:
            item[k] = float(item[k])
        subs = sorted(
            detail.get(item["customer_id"], {}).values(),
            key=lambda i: (i["customer_name"] or "").lower(),
        )
        for sub in subs:
            for k in _COLS:
                sub[k] = float(sub[k])
        item["subs"] = subs
    for k in _COLS:
        totals[k] = float(totals[k])

    return {"as_of_date": as_of_date.isoformat(), "items": items, "totals": totals}


@router.get("/income-by-customer")
def income_by_customer(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """Income by Customer: invoices dated in the period, in home currency.

    Sales is the pre-tax subtotal — sales tax is money collected for the
    state, not income — with the tax beside it (explore 2.17.3, W-L11).
    Paid includes money the customer paid in the period that is not
    applied to an invoice yet, and Balance is net of it, so
    Sales + Tax − Paid = Balance (W-H9: $612.30 shown paid of $682.30)."""
    from app.services.contact_balances import home_amount, unapplied_payments

    if not start_date:
        start_date = fiscal_year_start(db, date.today())
    if not end_date:
        end_date = date.today()

    invoices = (
        db.query(Invoice)
        .options(joinedload(Invoice.customer))
        .filter(Invoice.date >= start_date, Invoice.date <= end_date)
        .filter(Invoice.status != InvoiceStatus.VOID)
        .all()
    )

    by_customer = {}

    def row(cid, name):
        if cid not in by_customer:
            by_customer[cid] = {
                "customer_id": cid,
                "customer_name": name,
                "invoice_count": 0,
                "total_sales": Decimal(0),
                "total_tax": Decimal(0),
                "total_paid": Decimal(0),
                "total_balance": Decimal(0),
            }
        return by_customer[cid]

    for inv in invoices:
        r = row(inv.customer_id, inv.customer.full_name if inv.customer else "Unknown")
        rate = inv.exchange_rate
        r["invoice_count"] += 1
        r["total_sales"] += home_amount(inv.subtotal, rate)
        r["total_tax"] += home_amount(inv.tax_amount, rate)
        r["total_paid"] += home_amount(inv.amount_paid, rate)
        r["total_balance"] += home_amount(inv.balance_due, rate)

    unapplied = unapplied_payments(db, start=start_date, end=end_date)
    if unapplied:
        names = {
            c.id: c.name
            for c in db.query(Customer.id, Customer.name).filter(
                Customer.id.in_({p.customer_id for p in unapplied})
            )
        }
        for p in unapplied:
            r = row(p.customer_id, names.get(p.customer_id, "Unknown"))
            r["total_paid"] += p.unapplied_home
            r["total_balance"] -= p.unapplied_home

    items = sorted(by_customer.values(), key=lambda x: x["total_sales"], reverse=True)
    money = ("total_sales", "total_tax", "total_paid", "total_balance")
    grand = {k: sum((i[k] for i in items), Decimal(0)) for k in money}
    for item in items:
        for k in money:
            item[k] = float(item[k])

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "items": items,
        **{k: float(v) for k, v in grand.items()},
    }


def statement_activity(db: Session, customer: Customer, as_of_date: date) -> dict:
    """A customer statement's lines: invoices, payments and credit memos
    dated up to `as_of_date`, interleaved in date order with the balance
    after each one, and the totals.

    The statement used to list every invoice, then every payment, so the
    running balance followed no order a customer could check against their
    own records, and its Description column printed the invoice's notes —
    internal text, or the "Thank you for your business" footer (explore
    2.17.3, W-L9). A voided payment was also subtracted, and credit memos
    were left out, so Balance Due could disagree with the customer's
    balance. Each line now describes the document itself: when an invoice
    is due and its PO number; how a payment was made and which invoices it
    paid; what a credit memo was applied to.

    Amounts are in home currency, as A/R Aging and the customer's balance
    are: a foreign-currency invoice at the rate it was booked at, and a
    payment at what it took off A/R (each part applied to an invoice at
    that invoice's rate, the rest at the payment's own). A EUR 850.00
    invoice was summed as 850 dollars beside the dollar ones (explore
    2.17.3, A11). A foreign line names its own amount ("EUR 850.00")."""
    from app.models.credit_memos import CreditApplication, CreditMemo, CreditMemoStatus
    from app.models.payments import PaymentAllocation
    from app.services.contact_balances import home_amount
    from app.services.currency import home_currency
    from app.services.donor_documents import document_label
    from app.services.terminology import terms_from_db

    from app.services.sub_customers import billed_subs

    words = terms_from_db(db)
    home = home_currency(db)

    # A parent's statement carries the documents of the sub-customers
    # billed with it (#638), each line naming the sub; a sub's own
    # statement is its own activity.
    subs = {s.id: s.name for s in billed_subs(db, customer.id)}
    on_statement = [customer.id] + list(subs)
    ours = set(on_statement)

    def who(doc) -> list[str]:
        """The sub's name opens the line's description on the parent's
        statement; a line of the customer's own says nothing; a payment
        or credit of someone else that relieved an invoice of the
        customer's own (the parent's, on a sub-customer's own statement)
        says whose it is."""
        name = subs.get(doc.customer_id)
        if name:
            return [name]
        if doc.customer_id not in ours:
            payer = doc.customer
            return [
                f"from {payer.full_name if payer is not None else 'another customer'}"
            ]
        return []

    def foreign(doc) -> str:
        """The document's currency code when it is not the home currency."""
        code = (doc.currency or "").strip().upper()
        return code if code and code != home else ""

    invoices = (
        db.query(Invoice)
        .filter(Invoice.customer_id.in_(on_statement))
        .filter(Invoice.status != InvoiceStatus.VOID)
        .filter(Invoice.date <= as_of_date)
        .all()
    )
    # The payment and credit lines are built from what relieved the
    # statement's invoices (#638 review): the allocations and credit
    # applications on the invoices of the customers on the statement,
    # whoever paid (a parent's payment, on a sub-customer's own statement),
    # plus the statement customers' own payments and credit memos, applied
    # elsewhere or not at all. The balance follows what relieved these
    # invoices, as the customer's balance and A/R Aging do. For a customer
    # with no sub-customers every allocation of its payments is on its own
    # invoices, and the statement reads as it always did.
    #
    # Two rules keep the history where it belongs once a tenant has moved
    # (2.31.1 gate, W-58; a sub-customer is moved, or billed on its own,
    # only when none of its parent's money sits on an open invoice):
    #
    # * The customer's own payment or credit memo applied to an invoice
    #   not on this statement (a former tenant's, or a tenant billed on
    #   its own, the shape the IIF import makes when it allocates a
    #   payment by invoice number) is listed as a pair: the payment, and
    #   the invoice it paid, named "for Unit 1: #1001", at what this
    #   customer's money relieved on it. The former parent's statement
    #   keeps what it paid, and its balance is unchanged.
    # * A billed sub-customer's invoice is carried net of what a customer
    #   off the statement (its former parent) relieved on it, and not at
    #   all once that money covered it; that customer's payments and
    #   credits are not lines. The new parent's statement does not carry
    #   the old parent's payments or the invoices they relieved. The
    #   customer's own invoices show every payer, as they always did.
    paid_here = (
        db.query(PaymentAllocation.payment_id)
        .join(Invoice, Invoice.id == PaymentAllocation.invoice_id)
        .filter(Invoice.customer_id.in_(on_statement))
    )
    payments = (
        db.query(Payment)
        .options(
            selectinload(Payment.allocations).joinedload(PaymentAllocation.invoice)
        )
        .filter(or_(Payment.customer_id.in_(on_statement), Payment.id.in_(paid_here)))
        .filter(or_(Payment.is_voided.is_(False), Payment.is_voided.is_(None)))
        .filter(Payment.date <= as_of_date)
        .all()
    )
    credited_here = (
        db.query(CreditApplication.credit_memo_id)
        .join(Invoice, Invoice.id == CreditApplication.invoice_id)
        .filter(Invoice.customer_id.in_(on_statement))
    )
    memos = (
        db.query(CreditMemo)
        .options(
            selectinload(CreditMemo.applications).joinedload(CreditApplication.invoice)
        )
        .filter(
            or_(
                CreditMemo.customer_id.in_(on_statement),
                CreditMemo.id.in_(credited_here),
            )
        )
        .filter(CreditMemo.status != CreditMemoStatus.VOID)
        .filter(CreditMemo.date <= as_of_date)
        .all()
    )

    def numbers(docs):
        return ", ".join(f"#{d.invoice_number}" for d in docs if d is not None)

    def relief(application) -> Decimal:
        """What the allocation (or credit application) took off A/R, in
        home currency: at the invoice's rate, as its journal did."""
        return home_amount(application.amount, application.invoice.exchange_rate)

    def listed(doc, applications) -> list:
        """The allocations (or credit applications) of ``doc`` this
        statement lists: every one of the statement customers' own
        (those on invoices not here are paired with the invoice), and of
        anyone else's only those on the customer's own invoices."""
        if doc.customer_id in ours:
            return [a for a in applications if a.invoice is not None]
        return [
            a
            for a in applications
            if a.invoice is not None and a.invoice.customer_id == customer.id
        ]

    # what a customer off the statement relieved on a billed sub's invoice
    outside_relief: dict[int, Decimal] = {}
    for doc, applications in [(p, p.allocations) for p in payments] + [
        (cm, cm.applications) for cm in memos
    ]:
        if doc.customer_id in ours:
            continue
        for a in applications:
            if a.invoice is not None and a.invoice.customer_id in subs:
                outside_relief[a.invoice.id] = outside_relief.get(
                    a.invoice.id, Decimal(0)
                ) + relief(a)
    # the invoices not on the statement that the statement customers' own
    # money relieved, with how much of it: the pairs
    paired: dict[int, tuple] = {}

    def pair(application) -> None:
        inv = application.invoice
        if inv.customer_id in ours or inv.status == InvoiceStatus.VOID:
            return
        _, so_far = paired.get(inv.id, (inv, Decimal(0)))
        paired[inv.id] = (inv, so_far + relief(application))

    events = []
    invoiced = Decimal(0)
    # Retainage the customer's invoices hold back (a pay application's, #555),
    # less what later invoices released: owed, but not yet due, so it sits
    # apart from the amount due and the amount due is what A/R Aging says.
    retainage = Decimal(0)
    for inv in invoices:
        held = home_amount(inv.retainage_held, inv.exchange_rate)
        released = home_amount(inv.retainage_released, inv.exchange_rate)
        retainage += held - released
        # billed now: the invoice's total less the retainage it holds back,
        # plus any it releases (what its journal entry put on A/R)
        amount = home_amount(inv.total, inv.exchange_rate) - held + released
        outside = outside_relief.get(inv.id, Decimal(0))
        if outside:
            amount -= outside
            if amount <= 0:
                continue  # covered by money off this statement: not its business
        code = foreign(inv)
        parts = who(inv)
        if code:
            parts.append(f"{code} {Decimal(str(inv.total or 0)):,.2f}")
        if inv.po_number:
            parts.append(f"PO {inv.po_number}")
        if inv.due_date:
            parts.append(f"Due {inv.due_date.strftime('%b %d, %Y')}")
        if held:
            parts.append(f"${held:,.2f} retainage held")
        if released:
            parts.append(f"${released:,.2f} retainage released")
        invoiced += amount
        events.append(
            (
                inv.date,
                0,
                inv.id,
                {
                    "date": inv.date,
                    "type": document_label(inv, words),
                    "number": inv.invoice_number,
                    "description": " · ".join(parts),
                    "amount": amount,
                    "currency": code or None,
                },
            )
        )
    received = Decimal(0)
    for p in payments:
        code = foreign(p)
        own = p.customer_id in ours
        here = listed(p, p.allocations)
        applied = sum((Decimal(str(a.amount)) for a in p.allocations), Decimal(0))
        left = Decimal(str(p.amount)) - applied if own else Decimal(0)
        if not here and left <= 0:
            # relieved nothing on this statement
            continue
        # What the payment took off this statement's A/R, in home
        # currency, as its journal entry and any later apply did: each
        # part applied to an invoice at that invoice's rate, the
        # customer's own unapplied remainder at the payment's rate.
        relieved = sum((relief(a) for a in here), Decimal(0))
        if left > 0:
            relieved += home_amount(left, p.exchange_rate)
        received += relieved
        for a in here:
            pair(a)
        parts = who(p)
        if code:
            parts.append(f"{code} {Decimal(str(p.amount)):,.2f}")
        if p.method:
            parts.append(p.method)
        paid = numbers(a.invoice for a in here)
        if paid:
            parts.append(f"applied to {paid}")
        if left > 0:
            left_text = f"{code} {left:,.2f}" if code else f"${left:,.2f}"
            parts.append(
                words.text(f"{left_text} not applied to an invoice yet")
                if paid
                else words.text("not applied to an invoice yet")
            )
        events.append(
            (
                p.date,
                1,
                p.id,
                {
                    "date": p.date,
                    "type": "Payment",
                    "number": p.check_number or p.reference or "",
                    "description": " · ".join(parts),
                    "amount": -relieved,
                    "currency": code or None,
                },
            )
        )
    credited = Decimal(0)
    for cm in memos:
        here = listed(cm, cm.applications)
        if cm.customer_id in ours:
            # the customer's own credit, whole: what it took off invoices,
            # here or paired, plus what is still unapplied
            amount = Decimal(str(cm.total or 0))
        else:
            amount = sum((Decimal(str(a.amount)) for a in here), Decimal(0))
        if amount == 0:
            continue
        credited += amount
        for a in here:
            pair(a)
        applied_to = numbers(a.invoice for a in here)
        events.append(
            (
                cm.date,
                2,
                cm.id,
                {
                    "date": cm.date,
                    "type": "Credit Memo",
                    "number": cm.memo_number,
                    "description": " · ".join(
                        who(cm)
                        + [f"applied to {applied_to}" if applied_to else "credit"]
                    ),
                    "amount": -amount,
                    "currency": None,
                },
            )
        )
    for inv, amount in paired.values():
        # the other half of the pair: the invoice this statement's money
        # paid, at what it paid, named for whose it is
        code = foreign(inv)
        payee = inv.customer
        invoiced += amount
        events.append(
            (
                inv.date,
                0,
                inv.id,
                {
                    "date": inv.date,
                    "type": document_label(inv, words),
                    "number": inv.invoice_number,
                    "description": (
                        f"for {payee.name if payee is not None else 'another customer'}: "
                        f"#{inv.invoice_number}"
                    ),
                    "amount": amount,
                    "currency": code or None,
                },
            )
        )

    events.sort(key=lambda e: e[:3])
    running = Decimal(0)
    lines = []
    for *_, line in events:
        running += line["amount"]
        line["balance"] = running
        lines.append(line)
    return {
        "lines": lines,
        "total_invoiced": invoiced,
        "total_payments": received,
        "total_credits": credited,
        "balance_due": running,
        "retainage_held": retainage,
        "home_currency": home,
        "has_foreign": any(line["currency"] for line in lines),
    }


@router.get("/customer-statement/{customer_id}/pdf")
def customer_statement_pdf(
    customer_id: int,
    as_of_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """Customer statement PDF."""
    if not as_of_date:
        as_of_date = date.today()

    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    company = get_settings(db)
    pdf_bytes = generate_statement_pdf(
        customer, statement_activity(db, customer, as_of_date), company, as_of_date
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(f"Statement_{customer.name}.pdf")
        },
    )


@router.post("/batch-email-statements")
def batch_email_statements(db: Session = Depends(get_db)):
    """Email a statement to every customer with an overdue invoice.

    Reports what actually went out. send_email() returns False rather than
    raising when SMTP is not set up or the server refuses; this counted
    every attempt as sent, so with no mail server at all A/R Aging said
    "Sent 2 statements" (explore 2.17.3, W-H7). A draft was never sent to
    the customer, so it is not overdue and does not trigger a statement."""
    from app.services.email_service import send_email

    settings = get_settings(db)
    as_of_date = date.today()

    overdue_invoices = (
        db.query(Invoice)
        .filter(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL]))
        .filter(Invoice.balance_due > 0)
        .filter(Invoice.due_date < as_of_date)
        .all()
    )
    # A sub-customer billed with its parent (#638) is on the parent's
    # statement: the parent gets one statement carrying it, the sub none.
    from app.services.sub_customers import billed_to

    owners = billed_to(db, {inv.customer_id for inv in overdue_invoices})
    customer_ids = sorted(set(owners.values()))
    if not customer_ids:
        return {"sent": 0, "failed": 0, "errors": []}
    if not (settings.get("smtp_host") or "").strip():
        raise HTTPException(
            status_code=400,
            detail=(
                "Email isn't set up yet, so no statements were sent. Enter your "
                "mail server under Settings, Email, then try again."
            ),
        )

    sent = 0
    failed = 0
    errors = []
    # the display name (DBA, #679) when one is set: the statement goes to
    # the customer, who knows the company by it
    company_name = settings.get("display_name") or settings.get("company_name") or ""

    for cid in customer_ids:
        customer = db.query(Customer).filter(Customer.id == cid).first()
        if not customer or not customer.email:
            errors.append(
                f"{customer.name if customer else cid}: no email address on file"
            )
            failed += 1
            continue

        ok = False
        ran = False
        try:
            pdf_bytes = generate_statement_pdf(
                customer,
                statement_activity(db, customer, as_of_date),
                settings,
                as_of_date,
            )

            ran = True
            ok = send_email(
                db=db,
                to_email=customer.email,
                subject=f"Account Statement — {company_name or 'Our Company'}",
                html_body=(
                    f"<p>Dear {html.escape(customer.name)},</p>"
                    "<p>Please find your account statement attached.</p>"
                    f"<p>{html.escape(company_name)}</p>"
                ),
                attachment_bytes=pdf_bytes,
                attachment_name=f"Statement_{customer.name}.pdf",
                entity_type="statement",
                entity_id=cid,
            )
        except Exception:
            logger.exception("Failed to send statement to customer %s", customer.id)
        if ok:
            sent += 1
        else:
            failed += 1
            # the reply's sentence when the send ran and the server said
            # why (#811); the raw text stays in the email log
            why = sentence_for_last_send(db, "statement", cid) if ran else ""
            errors.append(
                f"{customer.name}: the statement could not be sent. {why}".rstrip()
                if why
                else f"{customer.name}: the statement could not be sent (the "
                "email log has the reason)"
            )

    return {"sent": sent, "failed": failed, "errors": errors}


@router.post("/collection-letters")
def collection_letters(
    data: CollectionLetterRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Generate and optionally email collection letters."""
    from app.services.email_service import send_email

    letter_type = data.letter_type
    customer_ids = data.customer_ids
    send_email_flag = data.send_email
    settings = get_settings(db)
    today = date.today()

    # Map letter type to minimum days overdue
    min_days = {"30": 30, "60": 60, "90": 90}.get(letter_type, 30)

    # A draft was never sent, so it can't be overdue (as for statements).
    q = (
        db.query(Invoice)
        .filter(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL]))
        .filter(Invoice.balance_due > 0)
        .filter(Invoice.due_date <= today - timedelta(days=min_days))
    )
    if customer_ids:
        from app.services.sub_customers import billed_subs

        # a parent picked covers the sub-customers billed with it (#638)
        wanted = set(customer_ids)
        for cid in customer_ids:
            wanted.update(s.id for s in billed_subs(db, cid))
        q = q.filter(Invoice.customer_id.in_(wanted))

    overdue_invoices = q.all()

    # Group by the customer the invoice is billed to: the parent's letter
    # lists the invoices of the sub-customers billed with it (#638).
    from app.services.sub_customers import billed_to

    owners = billed_to(db, {inv.customer_id for inv in overdue_invoices})
    by_customer = {}
    for inv in overdue_invoices:
        by_customer.setdefault(owners[inv.customer_id], []).append(inv)

    generated = 0
    emailed = 0
    errors = []

    for cid, invs in by_customer.items():
        customer = db.query(Customer).filter(Customer.id == cid).first()
        if not customer:
            continue

        # Add days_overdue to each invoice for the template
        for inv in invs:
            inv.days_overdue = (today - inv.due_date).days if inv.due_date else 0

        total_due = sum(float(inv.balance_due) for inv in invs)

        try:
            pdf_bytes = generate_collection_letter_pdf(
                customer, invs, settings, letter_type, total_due
            )
            generated += 1

            if send_email_flag and not customer.email:
                errors.append(f"{customer.name}: no email address on file")
            elif send_email_flag:
                type_labels = {
                    "30": "Payment Reminder",
                    "60": "Second Notice",
                    "90": "Final Notice",
                }
                # send_email() returns False instead of raising; only a
                # letter that went out counts as emailed.
                if send_email(
                    db=db,
                    to_email=customer.email,
                    subject=f"{type_labels.get(letter_type, 'Collection Notice')} — {settings.get('display_name') or settings.get('company_name', '')}",
                    html_body=f"<p>Dear {html.escape(customer.name)},</p><p>Please see the attached collection notice regarding your outstanding balance of ${total_due:,.2f}.</p>",
                    attachment_bytes=pdf_bytes,
                    attachment_name=f"Collection_{letter_type}day_{customer.name}.pdf",
                    entity_type="collection",
                    entity_id=cid,
                ):
                    emailed += 1
                else:
                    why = sentence_for_last_send(db, "collection", cid)
                    errors.append(
                        f"{customer.name}: the letter could not be emailed. "
                        + (why + " " if why else "")
                        + where_to_see_why(is_admin(request))
                    )
        except Exception:
            logger.exception(
                "Failed to generate collection letter for customer %s", customer.id
            )
            errors.append(f"{customer.name}: unable to generate collection letter")

    return {"generated": generated, "emailed": emailed, "errors": errors}
