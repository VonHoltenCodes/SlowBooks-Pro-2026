import html
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response
from app.schemas.common import StrictModel
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
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

logger = logging.getLogger(__name__)


class CollectionLetterRequest(StrictModel):
    letter_type: str = "30"
    customer_ids: Optional[list[int]] = None
    send_email: bool = False


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


@router.get("/ar-aging")
def ar_aging(as_of_date: date = Query(default=None), db: Session = Depends(get_db)):
    """What each customer owes, by age, in home currency — and net of the
    credits they hold, so the total is what account 1100 says.

    Two gaps made the total disagree with the balance sheet (explore
    2.17.3, W-H9 / F17): a foreign-currency invoice counted at its document
    amount (EUR 850) instead of what it was booked at (USD 935), and money
    a customer paid that was not applied to an invoice was left out
    entirely (unapplied_credits read 0 for everyone)."""
    from app.services.contact_balances import home_amount, unapplied_payments

    if not as_of_date:
        as_of_date = date.today()

    invoices = (
        db.query(Invoice)
        .filter(
            Invoice.status.in_(
                [InvoiceStatus.DRAFT, InvoiceStatus.SENT, InvoiceStatus.PARTIAL]
            )
        )
        .filter(Invoice.date <= as_of_date)
        .filter(Invoice.balance_due > 0)
        .all()
    )

    customer_names = {c.id: c.name for c in db.query(Customer.id, Customer.name).all()}

    aging = {}
    for inv in invoices:
        cid = inv.customer_id
        if cid not in aging:
            aging[cid] = _aging_row(customer_names, cid)

        days = (as_of_date - inv.due_date).days if inv.due_date else 0
        # At the rate it was booked at, as the ledger carries it.
        bal = home_amount(inv.balance_due, inv.exchange_rate)
        if days <= 0:
            aging[cid]["current"] += bal
        elif days <= 30:
            aging[cid]["over_30"] += bal
        elif days <= 60:
            aging[cid]["over_60"] += bal
        else:
            aging[cid]["over_90"] += bal
        aging[cid]["total"] += bal

    # Unapplied credit memos. A credit memo credits A/R the moment it is
    # issued, so summing only invoice balances reads HIGHER than account
    # 1100 by every credit not yet applied — the aging and the control
    # account disagree, and the report overstates what customers owe. Found
    # while building the payable-side twin (issue #129); fixed on both sides
    # together so they cannot drift apart again.
    from app.models.credit_memos import CreditMemo, CreditMemoStatus

    credits = (
        db.query(CreditMemo)
        .filter(CreditMemo.status != CreditMemoStatus.VOID)
        .filter(CreditMemo.date <= as_of_date)
        .filter(CreditMemo.balance_remaining > 0)
        .all()
    )
    held = [(cm.customer_id, Decimal(str(cm.balance_remaining))) for cm in credits]
    # ...and payments, or the part of one, not applied to any invoice: they
    # credited A/R in full when received, at the payment's rate.
    held += [
        (p.customer_id, p.unapplied_home)
        for p in unapplied_payments(db, end=as_of_date)
    ]
    for cid, amt in held:
        if cid not in aging:
            aging[cid] = _aging_row(customer_names, cid)
        aging[cid]["unapplied_credits"] += amt
        # A credit has no due date: it offsets the newest bucket.
        aging[cid]["current"] -= amt
        aging[cid]["total"] -= amt

    _COLS = ("current", "over_30", "over_60", "over_90", "total", "unapplied_credits")
    items = sorted(aging.values(), key=lambda i: (i["customer_name"] or "").lower())
    totals = {"customer_name": "TOTAL", "customer_id": 0}
    for k in _COLS:
        totals[k] = sum((i[k] for i in items), Decimal(0))
    # Convert Decimals to float for JSON
    for item in items:
        for k in _COLS:
            item[k] = float(item[k])
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
        start_date = date(date.today().year, 1, 1)
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
        r = row(inv.customer_id, inv.customer.name if inv.customer else "Unknown")
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
    from app.models.credit_memos import CreditMemo, CreditMemoStatus
    from app.models.payments import PaymentAllocation
    from app.services.contact_balances import home_amount
    from app.services.currency import home_currency
    from app.services.donor_documents import document_label
    from app.services.terminology import terms_from_db

    words = terms_from_db(db)
    home = home_currency(db)

    def foreign(doc) -> str:
        """The document's currency code when it is not the home currency."""
        code = (doc.currency or "").strip().upper()
        return code if code and code != home else ""

    invoices = (
        db.query(Invoice)
        .filter(Invoice.customer_id == customer.id)
        .filter(Invoice.status != InvoiceStatus.VOID)
        .filter(Invoice.date <= as_of_date)
        .all()
    )
    payments = (
        db.query(Payment)
        .options(
            selectinload(Payment.allocations).joinedload(PaymentAllocation.invoice)
        )
        .filter(Payment.customer_id == customer.id)
        .filter(or_(Payment.is_voided.is_(False), Payment.is_voided.is_(None)))
        .filter(Payment.date <= as_of_date)
        .all()
    )
    memos = (
        db.query(CreditMemo)
        .filter(CreditMemo.customer_id == customer.id)
        .filter(CreditMemo.status != CreditMemoStatus.VOID)
        .filter(CreditMemo.date <= as_of_date)
        .all()
    )

    def numbers(docs):
        return ", ".join(f"#{d.invoice_number}" for d in docs if d is not None)

    events = []
    invoiced = Decimal(0)
    for inv in invoices:
        code = foreign(inv)
        parts = [f"{code} {Decimal(str(inv.total or 0)):,.2f}"] if code else []
        if inv.po_number:
            parts.append(f"PO {inv.po_number}")
        if inv.due_date:
            parts.append(f"Due {inv.due_date.strftime('%b %d, %Y')}")
        amount = home_amount(inv.total, inv.exchange_rate)
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
        applied = sum((Decimal(str(a.amount)) for a in p.allocations), Decimal(0))
        left = Decimal(str(p.amount)) - applied
        # What the payment took off A/R, in home currency, as its journal
        # entry and any later apply did.
        relieved = sum(
            (
                home_amount(
                    a.amount, a.invoice.exchange_rate if a.invoice else p.exchange_rate
                )
                for a in p.allocations
            ),
            Decimal(0),
        )
        if left > 0:
            relieved += home_amount(left, p.exchange_rate)
        received += relieved
        parts = [f"{code} {Decimal(str(p.amount)):,.2f}"] if code else []
        if p.method:
            parts.append(p.method)
        paid = numbers(a.invoice for a in p.allocations)
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
    for cm in memos:
        applied_to = numbers(a.invoice for a in cm.applications)
        events.append(
            (
                cm.date,
                2,
                cm.id,
                {
                    "date": cm.date,
                    "type": "Credit Memo",
                    "number": cm.memo_number,
                    "description": (
                        f"applied to {applied_to}" if applied_to else "credit"
                    ),
                    "amount": -Decimal(str(cm.total or 0)),
                    "currency": None,
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
        "total_credits": sum((Decimal(str(m.total or 0)) for m in memos), Decimal(0)),
        "balance_due": running,
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
    customer_ids = sorted({inv.customer_id for inv in overdue_invoices})
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
    company_name = settings.get("company_name", "") or ""

    for cid in customer_ids:
        customer = db.query(Customer).filter(Customer.id == cid).first()
        if not customer or not customer.email:
            errors.append(
                f"{customer.name if customer else cid}: no email address on file"
            )
            failed += 1
            continue

        ok = False
        try:
            pdf_bytes = generate_statement_pdf(
                customer,
                statement_activity(db, customer, as_of_date),
                settings,
                as_of_date,
            )

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
            errors.append(
                f"{customer.name}: the statement could not be sent (the email "
                "log has the reason)"
            )

    return {"sent": sent, "failed": failed, "errors": errors}


@router.post("/collection-letters")
def collection_letters(data: CollectionLetterRequest, db: Session = Depends(get_db)):
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
        q = q.filter(Invoice.customer_id.in_(customer_ids))

    overdue_invoices = q.all()

    # Group by customer
    by_customer = {}
    for inv in overdue_invoices:
        by_customer.setdefault(inv.customer_id, []).append(inv)

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
                    subject=f"{type_labels.get(letter_type, 'Collection Notice')} — {settings.get('company_name', '')}",
                    html_body=f"<p>Dear {html.escape(customer.name)},</p><p>Please see the attached collection notice regarding your outstanding balance of ${total_due:,.2f}.</p>",
                    attachment_bytes=pdf_bytes,
                    attachment_name=f"Collection_{letter_type}day_{customer.name}.pdf",
                    entity_type="collection",
                    entity_id=cid,
                ):
                    emailed += 1
                else:
                    errors.append(
                        f"{customer.name}: the letter could not be emailed (the "
                        "email log has the reason)"
                    )
        except Exception:
            logger.exception(
                "Failed to generate collection letter for customer %s", customer.id
            )
            errors.append(f"{customer.name}: unable to generate collection letter")

    return {"generated": generated, "emailed": emailed, "errors": errors}
