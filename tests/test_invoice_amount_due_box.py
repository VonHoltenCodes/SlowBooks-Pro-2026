"""An Amount Due box near the top of the invoice PDF (#680).

The balance was only in the totals table at the bottom, so a customer
reading a long invoice turned to the last page for the figure. The box
beside Bill To carries the balance due and the due date; a pledge reads
Balance, as its totals row does; a paid invoice reads 0.00 and keeps the
box; a sales receipt, paid at the time of sale, has none.
"""

import io
import re
from datetime import date
from decimal import Decimal

import pytest

from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.services import pdf_service
from app.services.settings_service import get_all_settings


@pytest.fixture
def customer(db_session):
    c = Customer(name="Ada Customer", is_active=True)
    db_session.add(c)
    db_session.commit()
    return c


def _invoice(db_session, customer, paid="0", **over):
    total = Decimal("250.00")
    inv = Invoice(
        invoice_number=over.pop("invoice_number", "AD-1"),
        customer_id=customer.id,
        date=date(2026, 10, 1),
        due_date=date(2026, 10, 31),
        status=InvoiceStatus.SENT,
        subtotal=total,
        total=total,
        amount_paid=Decimal(paid),
        balance_due=total - Decimal(paid),
        lines=[InvoiceLine(description="Work", quantity=1, rate=total, amount=total)],
        **over,
    )
    db_session.add(inv)
    db_session.commit()
    return inv


def _box(html: str) -> str:
    m = re.search(r'<div class="amount-due-box">(.*?)\n\s*</div>', html, re.S)
    if not m:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()


def _html(db_session, inv, company=None):
    return pdf_service.render_invoice_html(inv, company or get_all_settings(db_session))


@pytest.mark.parametrize(
    "paid, figure",
    [("0", "$250.00"), ("100.00", "$150.00"), ("250.00", "$0.00")],
    ids=["open", "partly_paid", "paid"],
)
def test_the_box_shows_the_balance_and_the_due_date(db_session, customer, paid, figure):
    html = _html(db_session, _invoice(db_session, customer, paid=paid))
    assert _box(html) == f"Amount Due {figure} Due Oct 31, 2026"
    # beside Bill To, ahead of the line items
    bill_to = html.index('<div class="address-label">Bill To</div>')
    box = html.index('<div class="amount-due-box">')
    assert bill_to < box < html.index('<table class="line-items">')


def test_a_pledge_reads_balance(db_session, customer):
    inv = _invoice(db_session, customer, paid="50.00", is_pledge=True)
    company = get_all_settings(db_session)
    company["company_type"] = "nonprofit"
    assert _box(_html(db_session, inv, company)) == "Balance $200.00 Due Oct 31, 2026"


def test_a_sales_receipt_has_no_box(db_session, customer):
    inv = _invoice(db_session, customer, paid="250.00", is_sales_receipt=True)
    assert '<div class="amount-due-box">' not in _html(db_session, inv)


def test_the_box_prints_on_the_pdf(db_session, customer):
    pypdf = pytest.importorskip("pypdf")
    pdf = pdf_service.generate_invoice_pdf(
        _invoice(db_session, customer, paid="100.00"), get_all_settings(db_session)
    )
    text = pypdf.PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    assert "AMOUNT DUE" in text.upper(), text[:600]
    assert "$150.00" in text
    assert "Due Oct 31, 2026" in text
