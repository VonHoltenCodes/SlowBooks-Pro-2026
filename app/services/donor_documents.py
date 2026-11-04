"""Donor documents — what a nonprofit hands the people who give.

The IRS language lives here once (Publication 1771): a written
acknowledgment states the amount, the date, and either that no goods or
services were provided in exchange, or a description and good-faith
estimate of what was (the gala dinner) so the deductible portion is
clear. For property the charity describes it and never values it. The
donation receipt, the acknowledgment letter and the year-end giving
statement all read from ``irs_statement``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app.services.accounting import _q
from app.services.terminology import Terms, terms_for

NO_GOODS = "No goods or services were provided in exchange for this contribution."
RETAIN = "Please retain this acknowledgment for your tax records."


def _money(v) -> str:
    v = _q(Decimal(str(v or 0)))
    return f"${v:,.2f}"


def irs_statement(
    company: dict,
    amount,
    fair_value_amount=None,
    fair_value_description: Optional[str] = None,
    in_kind_descriptions: Optional[list[str]] = None,
) -> dict:
    """{deductible_amount, text} for one gift.

    amount               the contribution (None for property)
    fair_value_amount    value of goods/services the donor received back
    in_kind_descriptions the property given, when the gift is not cash
    """
    name = _org_name(company)
    ein = (company or {}).get("company_tax_id") or ""
    tail = f" {name}" + (f" (EIN {ein})" if ein else "") + f". {RETAIN}"

    if in_kind_descriptions:
        what = "; ".join(d for d in in_kind_descriptions if d)
        text = (
            f"Thank you for your gift of {what}. {NO_GOODS} {name} has not "
            "assigned a value to this gift; the donor is responsible for "
            "determining its fair market value."
        ) + tail
        return {"deductible_amount": None, "text": text}

    amount = _q(Decimal(str(amount or 0)))
    fv = _q(Decimal(str(fair_value_amount or 0)))
    if fv > 0:
        desc = f" ({fair_value_description})" if fair_value_description else ""
        deductible = max(amount - fv, Decimal("0"))
        text = (
            f"In exchange for this contribution of {_money(amount)}, {name} "
            f"provided goods or services with an estimated fair market value "
            f"of {_money(fv)}{desc}. The portion of your contribution "
            f"deductible for federal income tax purposes is limited to "
            f"{_money(deductible)}."
        ) + tail
        return {"deductible_amount": float(deductible), "text": text}
    text = f"{NO_GOODS} Contribution of {_money(amount)} received by" + tail
    return {"deductible_amount": float(amount), "text": text}


def invoice_doc_kind(inv, t: Terms) -> str:
    """The printed document's name: SalesReceipt / Invoice for a business;
    DonationReceipt / Pledge / Invoice for a nonprofit (a nonprofit still
    invoices program fees and rentals, so only a flagged pledge prints as
    one)."""
    receipt = getattr(inv, "is_sales_receipt", False)
    if not t.is_nonprofit:
        return "SalesReceipt" if receipt else "Invoice"
    if receipt:
        return "DonationReceipt"
    return "Pledge" if getattr(inv, "is_pledge", False) else "Invoice"


def document_label(inv, t: Terms) -> str:
    """The document's name as a person says it — Invoice, Pledge, Sales
    Receipt, Donation Receipt — from the same rule the printed page uses.
    Everything a posting writes about the document (its ledger lines, the
    void, the late fee, the payment-page line item) says this, so the
    ledger, the covering email and the printed document agree. Never the
    vocabulary swap: a nonprofit's program-fee invoice stays an Invoice
    on all three."""
    kind = invoice_doc_kind(inv, t)
    return {"SalesReceipt": "Sales Receipt", "DonationReceipt": "Donation Receipt"}.get(
        kind, kind
    )


def invoice_pdf_context(inv, company: dict) -> dict:
    """Extra template context for invoice_pdf.html: the document kind and,
    on a nonprofit donation receipt, the IRS acknowledgment block; the
    Bill To block's name, country and "For:" line; and the Ship To's
    country.

    The Bill To block comes from the invoice alone (2.31.1 gate, W-59):
    the name and country frozen with the street when the invoice was made
    (Invoice.bill_to_name, bill_to_country), and "For: <sub-customer>"
    when the invoice was billed to someone other than its customer
    (bill_to_customer_id, the parent of a sub-customer billed with it,
    #638). A tenant moved to another property, or billed on its own,
    afterwards does not change what its old invoice prints: the name over
    a street stays the name that street belongs to. The Ship To's country
    is the one frozen with the ship-to street (Invoice.ship_to_country,
    round 3), so a customer's move abroad does not print the new country
    under an old invoice's street; blank, it prints none. An invoice with
    nothing frozen (written around the ORM) is read off its customer as it
    was before: the parent's name and country when the customer is a sub
    billed with its parent, else the customer's own, and the customer's
    ship country."""
    t = terms_for(company)
    kind = invoice_doc_kind(inv, t)
    irs = None
    if kind == "DonationReceipt":
        irs = irs_statement(
            company,
            inv.total,
            getattr(inv, "fair_value_amount", None),
            getattr(inv, "fair_value_description", None),
        )
    customer = getattr(inv, "customer", None)
    bill_to_for = None
    if getattr(inv, "bill_to_name", None) is not None:
        bill_to_name = inv.bill_to_name
        bill_to_country = getattr(inv, "bill_to_country", None)
        ship_to_country = getattr(inv, "ship_to_country", None)
        billed_id = getattr(inv, "bill_to_customer_id", None)
        if (
            customer is not None
            and billed_id is not None
            and billed_id != getattr(inv, "customer_id", None)
        ):
            bill_to_for = customer.name
    else:
        bill_to_name = getattr(inv, "customer_name", None) or (
            customer.name if customer is not None else ""
        )
        bill_to_country = getattr(customer, "bill_country", None)
        ship_to_country = getattr(customer, "ship_country", None)
        parent = (
            customer.parent
            if customer is not None
            and getattr(customer, "parent_customer_id", None)
            and getattr(customer, "bill_with_parent", False)
            else None
        )
        if parent is not None:
            bill_to_name, bill_to_for = parent.name, customer.name
            bill_to_country = getattr(parent, "bill_country", None)
    return {
        "doc_kind": kind,
        "irs": irs,
        "bill_to_name": bill_to_name,
        "bill_to_for": bill_to_for,
        "bill_to_country": bill_to_country,
        "ship_to_country": ship_to_country,
    }


# ── gifts: one shape for a receipt, a payment, or property ───────────────

ACK_TEMPLATE_NAME = "donation_acknowledgment"
ACK_SUBJECT = (
    "Thank you for your gift to {{ company.display_name or company.company_name }}"
)
ACK_BODY = """<p>{{ donor.salutation or ('Dear ' ~ donor_name) }},</p>
<p>Thank you for your {% if gift.in_kind_lines %}generous gift{% else %}contribution of {{ gift.amount | currency }}{% endif %} on {{ gift.date | fdate }}{% if gift.number %} ({{ gift.number }}){% endif %}. Your support makes our work possible.</p>
<p>{{ irs.text }}</p>
<p>With gratitude,</p>
<p>{{ company.display_name or company.company_name }}</p>"""


class NotAGift(ValueError):
    """This document does not get an acknowledgment; `reason` says why, in
    the user's words (a fixed phrase, never exception text)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def gift_from_invoice(inv) -> dict:
    """A sales receipt is a gift; a pledge is not until it is paid."""
    if not inv.is_sales_receipt:
        raise NotAGift("Acknowledge the payment, not the pledge")
    return {
        "kind": "invoice",
        "id": inv.id,
        "number": inv.invoice_number,
        "date": inv.date,
        "amount": _q(Decimal(str(inv.total or 0))),
        "description": ", ".join(ln.description for ln in inv.lines if ln.description),
        "fair_value_amount": inv.fair_value_amount,
        "fair_value_description": inv.fair_value_description,
        "in_kind_lines": [],
        "customer_id": inv.customer_id,
    }


def payment_gift_amount(db, payment) -> Decimal:
    """The part of a payment that is a gift: everything except what it
    paid on a sales receipt (that receipt is acknowledged on its own)."""
    from app.models.invoices import Invoice
    from app.models.payments import PaymentAllocation

    on_receipts = (
        db.query(PaymentAllocation)
        .join(Invoice, Invoice.id == PaymentAllocation.invoice_id)
        .filter(
            PaymentAllocation.payment_id == payment.id,
            Invoice.is_sales_receipt.is_(True),
        )
        .all()
    )
    total = _q(Decimal(str(payment.amount or 0)))
    receipts = sum((Decimal(str(a.amount)) for a in on_receipts), Decimal("0"))
    return _q(max(total - receipts, Decimal("0")))


def gift_from_payment(db, payment) -> dict:
    if getattr(payment, "is_voided", False):
        raise NotAGift("This payment is void")
    amount = payment_gift_amount(db, payment)
    if amount <= 0:
        raise NotAGift(
            "This payment belongs to a donation receipt — acknowledge the receipt"
        )
    return {
        "kind": "payment",
        "id": payment.id,
        "number": payment.reference or payment.check_number or f"payment {payment.id}",
        "date": payment.date,
        "amount": amount,
        "description": payment.notes or "",
        "fair_value_amount": None,
        "fair_value_description": None,
        "in_kind_lines": [],
        "customer_id": payment.customer_id,
    }


def gift_from_in_kind(gift) -> dict:
    if gift.status == "void":
        raise NotAGift("This in-kind gift is void")
    return {
        "kind": "in-kind",
        "id": gift.id,
        "number": gift.number,
        "date": gift.date,
        "amount": None,
        "description": gift.memo or "",
        "fair_value_amount": None,
        "fair_value_description": None,
        "in_kind_lines": [
            {"description": ln.description, "quantity": ln.quantity}
            for ln in gift.lines
        ],
        "customer_id": gift.customer_id,
    }


def load_gift(db, kind: str, gift_id: int) -> dict:
    """Resolve a (kind, id) pair to the normalized gift dict."""
    if kind == "invoice":
        from app.models.invoices import Invoice

        inv = db.get(Invoice, gift_id)
        if inv is None:
            raise LookupError("Receipt not found")
        return gift_from_invoice(inv)
    if kind == "payment":
        from app.models.payments import Payment

        pmt = db.get(Payment, gift_id)
        if pmt is None:
            raise LookupError("Payment not found")
        return gift_from_payment(db, pmt)
    if kind == "in-kind":
        from app.models.in_kind import InKindGift

        ik = db.get(InKindGift, gift_id)
        if ik is None:
            raise LookupError("In-kind gift not found")
        return gift_from_in_kind(ik)
    if kind == "bank-entry":
        from app.models.transactions import TransactionLine

        line = db.get(TransactionLine, gift_id)
        if line is None:
            raise LookupError("Deposit line not found")
        return gift_from_bank_line(db, line)
    raise LookupError("Unknown gift kind")


def _org_name(company: Optional[dict]) -> str:
    """The organization as a donor's letter and statement name it: the
    display name when one is set, else the company name (#679)."""
    company = company or {}
    return (
        (company.get("display_name") or "").strip()
        or company.get("company_name")
        or "the organization"
    )


def gift_irs(company: dict, gift: dict) -> dict:
    if gift["in_kind_lines"]:
        descs = [
            (f"{ln['quantity']:g} × " if ln.get("quantity") not in (None, 1) else "")
            + ln["description"]
            for ln in gift["in_kind_lines"]
        ]
        return irs_statement(company, None, in_kind_descriptions=descs)
    return irs_statement(
        company,
        gift["amount"],
        gift["fair_value_amount"],
        gift["fair_value_description"],
    )


def render_acknowledgment(db, company: dict, customer, gift: dict) -> tuple[str, str]:
    """(subject, body_html) from the editable email template
    'donation_acknowledgment' (Settings -> Email Templates), falling back
    to the built-in text when the row has not been seeded. Rendered in the
    sandboxed environment, autoescaped."""
    from app.services.email_service import (
        customer_facing_company,
        render_subject,
        render_template_from_db,
        template_env,
    )

    context = {
        "donor": customer,
        "donor_name": customer.name,
        "customer_name": customer.name,
        # GHSA-c3v4-f43f-4wqm — see settings_service.redact_secrets; the
        # display name in company_name too, so a template saved before the
        # setting says the trade name (#679)
        "company": customer_facing_company(company),
        "gift": gift,
        "irs": gift_irs(company, gift),
    }
    subject, body = render_template_from_db(db, ACK_TEMPLATE_NAME, context)
    if subject is None:
        env = template_env()
        # a subject is a header: "Pike's Plumbing" as typed, not escaped
        subject = render_subject(env, ACK_SUBJECT, context)
        body = env.from_string(ACK_BODY).render(**context)
    return subject, body


# ── year-end giving statement ────────────────────────────────────────────


def gift_from_bank_line(db, line, txn=None) -> dict:
    """A gift booked on a deposit line (#606): an income credit on a
    register or bank-feed entry that names the donor."""
    from app.models.accounts import Account, AccountType
    from app.models.transactions import Transaction
    from app.services.entry_text import BANK_ENTRY_HEADER
    from app.services.voids import voided_transaction_ids

    txn = txn or db.get(Transaction, line.transaction_id)
    account = db.get(Account, line.account_id)
    if txn.source_type != "bank_entry" or account.account_type != AccountType.INCOME:
        raise NotAGift("This line is not a gift booked on a deposit")
    if not line.customer_id:
        raise NotAGift("This deposit line names no donor")
    if txn.id in voided_transaction_ids(db, [txn.id]):
        raise NotAGift("This deposit is void")
    amount = _q(Decimal(str(line.credit or 0)) - Decimal(str(line.debit or 0)))
    if amount <= 0:
        raise NotAGift("This line is not a gift booked on a deposit")
    memo = line.description or txn.description or ""
    return {
        "kind": "bank-entry",
        "id": line.id,
        "number": txn.reference or None,
        "date": txn.date,
        "amount": amount,
        "description": "" if memo == BANK_ENTRY_HEADER else memo,
        "fair_value_amount": None,
        "fair_value_description": None,
        "in_kind_lines": [],
        "customer_id": line.customer_id,
    }


def gifts_from_bank_entries(db, customer_id: int, start, end) -> list[dict]:
    """Gifts booked straight to the bank (#606): the income lines of a
    register or bank-feed deposit that name this donor, so one deposit of
    two gifts counts once for each. A void entry drops out."""
    from app.models.accounts import Account, AccountType
    from app.models.transactions import Transaction, TransactionLine

    rows = (
        db.query(TransactionLine, Transaction)
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .join(Account, Account.id == TransactionLine.account_id)
        .filter(
            TransactionLine.customer_id == customer_id,
            Transaction.source_type == "bank_entry",
            Transaction.date >= start,
            Transaction.date <= end,
            Account.account_type == AccountType.INCOME,
        )
        .order_by(Transaction.date, TransactionLine.id)
        .all()
    )
    gifts = []
    for line, txn in rows:
        try:
            gifts.append(gift_from_bank_line(db, line, txn))
        except NotAGift:
            continue
    return gifts


def collect_gifts(db, customer_id: int, year: int) -> dict:
    """Everything a donor gave in a calendar year: cash (donation receipts
    plus payments that are gifts — a receipt's own payment is never
    counted twice) and income lines of bank deposits that name the donor
    (#606), with the deductible portion per gift, and non-cash
    gifts listed without a value."""
    from datetime import date as _date

    from app.models.in_kind import InKindGift
    from app.models.invoices import Invoice, InvoiceStatus
    from app.models.payments import Payment

    start, end = _date(year, 1, 1), _date(year, 12, 31)
    cash: list[dict] = []
    receipts = (
        db.query(Invoice)
        .filter(
            Invoice.customer_id == customer_id,
            Invoice.is_sales_receipt.is_(True),
            Invoice.status != InvoiceStatus.VOID,
            Invoice.date >= start,
            Invoice.date <= end,
        )
        .order_by(Invoice.date, Invoice.id)
        .all()
    )
    for inv in receipts:
        cash.append(gift_from_invoice(inv))
    payments = (
        db.query(Payment)
        .filter(
            Payment.customer_id == customer_id,
            Payment.is_voided.is_(False),
            Payment.date >= start,
            Payment.date <= end,
        )
        .order_by(Payment.date, Payment.id)
        .all()
    )
    for pmt in payments:
        amount = payment_gift_amount(db, pmt)
        if amount > 0:
            g = gift_from_payment(db, pmt)
            cash.append(g)
    cash.extend(gifts_from_bank_entries(db, customer_id, start, end))
    cash.sort(key=lambda g: (g["date"], g["kind"], g["id"]))
    for g in cash:
        fv = _q(Decimal(str(g["fair_value_amount"] or 0)))
        g["deductible"] = _q(max(g["amount"] - fv, Decimal("0")))
    in_kind = [
        gift_from_in_kind(ik)
        for ik in db.query(InKindGift)
        .filter(
            InKindGift.customer_id == customer_id,
            InKindGift.status == "posted",
            InKindGift.date >= start,
            InKindGift.date <= end,
        )
        .order_by(InKindGift.date, InKindGift.id)
        .all()
    ]
    total = sum((g["amount"] for g in cash), Decimal("0"))
    fair_value = sum(
        (_q(Decimal(str(g["fair_value_amount"] or 0))) for g in cash), Decimal("0")
    )
    return {
        "cash": cash,
        "in_kind": in_kind,
        "totals": {
            "amount": _q(total),
            "fair_value": _q(fair_value),
            "deductible": _q(total - fair_value),
        },
    }


def giving_statement_irs_text(company: dict, totals: dict) -> str:
    name = _org_name(company)
    ein = (company or {}).get("company_tax_id") or ""
    base = (
        f"This statement summarizes contributions received by {name}"
        + (f" (EIN {ein})" if ein else "")
        + " during the year. "
    )
    if totals["fair_value"] > 0:
        return base + (
            "Except where a value of goods or services is shown above, no goods "
            "or services were provided in exchange for these contributions; where "
            "one is shown, the deductible portion is limited to the amount in the "
            "Deductible column. " + RETAIN
        )
    return base + NO_GOODS + " " + RETAIN
