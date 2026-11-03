"""Logo only: the logo without the company-name heading on the customer's
documents (#682).

A company whose logo already carries its name printed it twice, the logo
and then the heading. With Settings -> Company -> Logo only on and a logo
set, the invoice, sales receipt, estimate, statement and credit memo leave
the heading out and keep the address block; with no logo the heading prints,
so a document never goes out nameless.
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.contacts import Customer
from app.models.credit_memos import CreditMemo, CreditMemoLine
from app.models.estimates import Estimate, EstimateLine
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.settings import DEFAULT_SETTINGS
from app.routes.settings import ENUM_SETTINGS
from app.services import file_store, pdf_service
from app.services.settings_service import set_setting

ROOT = Path(__file__).resolve().parents[1]
NAME = "Navy Builders"
STREET = "12 Harbor Road"
LOGO = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60">'
    b'<rect width="200" height="60" fill="navy"/></svg>'
)


@pytest.fixture
def books(db_session, monkeypatch):
    set_setting(db_session, "company_name", NAME)
    set_setting(db_session, "company_address1", STREET)
    set_setting(db_session, "invoice_logo_only", "true")
    db_session.commit()
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    customer = Customer(name="Ada Customer", is_active=True)
    db_session.add(customer)
    db_session.commit()
    return customer


def _with_logo(db_session):
    file_store.replace_logo(db_session, LOGO, "image/svg+xml")
    db_session.commit()


def _invoice(db_session, customer, sales_receipt=False):
    inv = Invoice(
        invoice_number="LO-1",
        customer_id=customer.id,
        date=date(2026, 10, 1),
        due_date=date(2026, 10, 31),
        status=InvoiceStatus.SENT,
        subtotal=Decimal("40"),
        total=Decimal("40"),
        amount_paid=Decimal("0"),
        balance_due=Decimal("40"),
        is_sales_receipt=sales_receipt,
        lines=[InvoiceLine(description="Services", quantity=1, rate=40, amount=40)],
    )
    db_session.add(inv)
    db_session.commit()
    return inv


def _invoice_pdf(db_session, customer):
    from app.routes.invoices import documents

    inv = _invoice(db_session, customer)
    return documents.invoice_pdf(inv.id, db_session).body.decode()


def _sales_receipt_pdf(db_session, customer):
    from app.routes.invoices import documents

    inv = _invoice(db_session, customer, sales_receipt=True)
    return documents.invoice_pdf(inv.id, db_session).body.decode()


def _estimate_pdf(db_session, customer):
    from app.routes.estimates import estimate_pdf

    est = Estimate(
        estimate_number="E-LO",
        customer_id=customer.id,
        date=date(2026, 10, 1),
        subtotal=Decimal("40"),
        total=Decimal("40"),
        lines=[EstimateLine(description="Bid", quantity=1, rate=40, amount=40)],
    )
    db_session.add(est)
    db_session.commit()
    return estimate_pdf(est.id, db_session).body.decode()


def _credit_memo_pdf(db_session, customer):
    from app.routes.credit_memos import credit_memo_pdf

    cm = CreditMemo(
        memo_number="CM-LO",
        customer_id=customer.id,
        date=date(2026, 10, 1),
        subtotal=Decimal("10"),
        total=Decimal("10"),
        balance_remaining=Decimal("10"),
        lines=[CreditMemoLine(description="Refund", quantity=1, rate=10, amount=10)],
    )
    db_session.add(cm)
    db_session.commit()
    return credit_memo_pdf(cm.id, db_session).body.decode()


def _statement_pdf(db_session, customer):
    from app.routes.reports.receivables import customer_statement_pdf

    _invoice(db_session, customer)
    return customer_statement_pdf(
        customer.id, date(2026, 10, 31), db_session
    ).body.decode()


DOCUMENTS = {
    "invoice": _invoice_pdf,
    "sales_receipt": _sales_receipt_pdf,
    "estimate": _estimate_pdf,
    "statement": _statement_pdf,
    "credit_memo": _credit_memo_pdf,
}


def test_logo_only_is_off_by_default_and_a_switch():
    assert DEFAULT_SETTINGS["invoice_logo_only"] == "false"
    assert ENUM_SETTINGS["invoice_logo_only"] == frozenset({"true", "false"})


@pytest.mark.parametrize("doc", DOCUMENTS)
def test_with_a_logo_the_heading_is_left_out_and_the_address_kept(
    db_session, books, doc
):
    _with_logo(db_session)
    html = DOCUMENTS[doc](db_session, books)
    assert '<img class="company-logo"' in html
    assert '<div class="company-name">' not in html
    assert NAME not in html
    assert STREET in html


@pytest.mark.parametrize("doc", DOCUMENTS)
def test_without_a_logo_the_heading_prints(db_session, books, doc):
    html = DOCUMENTS[doc](db_session, books)
    assert '<img class="company-logo"' not in html
    assert f'<div class="company-name">{NAME}</div>' in html
    assert STREET in html


@pytest.mark.parametrize("doc", DOCUMENTS)
def test_off_the_heading_prints_under_the_logo(db_session, books, doc):
    set_setting(db_session, "invoice_logo_only", "false")
    _with_logo(db_session)
    html = DOCUMENTS[doc](db_session, books)
    assert '<img class="company-logo"' in html
    assert f'<div class="company-name">{NAME}</div>' in html


def test_the_invoice_with_its_logo_switched_off_keeps_the_heading(db_session, books):
    # "Show logo on invoices" off: no logo prints, so the name must
    set_setting(db_session, "invoice_show_logo", "false")
    _with_logo(db_session)
    html = _invoice_pdf(db_session, books)
    assert '<img class="company-logo"' not in html
    assert f'<div class="company-name">{NAME}</div>' in html


def test_with_a_display_name_neither_name_prints(db_session, books):
    # works with #679: the display name is the heading it leaves out
    set_setting(db_session, "display_name", "Navy")
    _with_logo(db_session)
    html = _invoice_pdf(db_session, books)
    assert '<div class="company-name">' not in html
    assert NAME not in html


def test_internal_documents_are_unchanged(db_session, books):
    _with_logo(db_session)
    html = pdf_service.render_deposit_html(
        {"lines": [], "total": 0, "date": "2026-10-01", "account_name": "Bank"},
        {"company_name": NAME, "invoice_logo_only": "true"},
    )
    assert NAME in html


def test_the_settings_field_is_named_and_marked_data_write():
    js = (ROOT / "app" / "static" / "js" / "settings.js").read_text()
    tag = re.search(r'<input[^>]*name="invoice_logo_only"[^>]*>', js).group(0)
    assert 'id="invoice-logo-only"' in tag and "data-write" in tag
    assert "data-bool" in tag  # an unticked box saves "false"
    assert re.search(r'<label for="invoice-logo-only"[^>]*>\s*<input', js)
    assert "Logo only" in js
    assert (
        "Print the logo without the company name heading on invoices, estimates, "
        "statements and receipts. Only applies when a logo is set." in js
    )
