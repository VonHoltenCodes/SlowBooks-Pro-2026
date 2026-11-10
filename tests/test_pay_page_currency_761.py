"""The customer payment page prints amounts in the invoice's currency (#761).

The page an emailed "Copy Payment Link" opens hard-coded "$" on Invoice
Total, Amount Paid, Balance Due and the Pay button, so a EUR invoice read
"EUR 3,000.00" on its PDF and "$3000.00" on the page the customer pays from.
It now formats through the PDF's currency filter: the invoice's code when it
is not the home currency, plain dollars with thousands separators when it is.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus


@pytest.fixture
def make_invoice(db_session, seed_accounts):
    customer = Customer(name="Pay Page Customer", is_active=True)
    db_session.add(customer)
    db_session.flush()

    def _make(number, total, paid="0", currency=None):
        total = Decimal(total)
        paid = Decimal(paid)
        inv = Invoice(
            invoice_number=number,
            customer_id=customer.id,
            date=date(2026, 7, 1),
            status=InvoiceStatus.SENT,
            subtotal=total,
            tax_rate=Decimal("0"),
            tax_amount=Decimal("0"),
            total=total,
            amount_paid=paid,
            balance_due=total - paid,
            payment_token=f"tok-{number}",
            currency=currency,
        )
        db_session.add(inv)
        db_session.commit()
        return inv

    return _make


def _amounts_table(html: str) -> str:
    """The Invoice Total / Amount Paid / Balance Due table alone (the invoice
    number and the scripts around it are not amounts)."""
    return html.split('<table class="detail-table">', 1)[1].split("</table>", 1)[0]


def test_a_euro_invoice_pays_in_euros(unauthed_client, make_invoice):
    inv = make_invoice("PP-1", "3000.00", paid="500.00", currency="EUR")
    page = unauthed_client.get(f"/pay/{inv.payment_token}")
    assert page.status_code == 200
    table = _amounts_table(page.text)
    assert "EUR 3,000.00" in table  # Invoice Total
    assert "EUR 500.00" in table  # Amount Paid
    assert "EUR 2,500.00" in table  # Balance Due
    assert "$" not in table


def test_a_dollar_invoice_keeps_plain_dollars(unauthed_client, make_invoice):
    inv = make_invoice("PP-2", "1234.50")
    page = unauthed_client.get(f"/pay/{inv.payment_token}")
    table = _amounts_table(page.text)
    assert "$1,234.50" in table
    assert "USD" not in table


def test_the_home_currency_named_on_the_invoice_is_still_dollars(
    unauthed_client, make_invoice
):
    """An invoice that names the home currency outright prints like one
    that names none, the way the PDF does."""
    inv = make_invoice("PP-3", "10.00", currency="USD")
    page = unauthed_client.get(f"/pay/{inv.payment_token}")
    table = _amounts_table(page.text)
    assert "$10.00" in table
    assert "USD" not in table
