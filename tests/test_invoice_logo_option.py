"""The invoice logo preference is shared by PDF, Print, and attachments."""

from datetime import date

import pytest
from starlette.requests import Request

from app.models.invoices import Invoice, InvoiceLine
from app.routes import settings as settings_routes
from app.routes.invoices import documents
from app.services import email_service, pdf_service
from app.services.settings_service import get_all_settings, set_setting


@pytest.fixture
def branded_invoice(db_session, seed_customer, tmp_path, monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "company_logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60"><rect width="200" height="60" fill="navy"/></svg>'
    )
    set_setting(db_session, "company_logo_path", "/static/uploads/company_logo.svg")
    invoice = Invoice(
        invoice_number="LOGO-1",
        customer_id=seed_customer.id,
        date=date(2026, 9, 26),
        subtotal=40,
        total=40,
        balance_due=40,
        lines=[InvoiceLine(description="Services", quantity=1, rate=40, amount=40)],
    )
    db_session.add(invoice)
    db_session.flush()
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html: html.encode())
    return invoice


def _request():
    return Request(
        {
            "type": "http",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "path": "/",
        }
    )


@pytest.mark.parametrize(
    "preference, expected", [(None, True), ("true", True), ("false", False)]
)
def test_preference_controls_pdf_print_and_email_attachment(
    db_session, branded_invoice, monkeypatch, preference, expected
):
    if preference is not None:
        settings_routes.update_settings(
            settings_routes.SettingsUpdate(invoice_show_logo=preference),
            _request(),
            db_session,
        )
    captured = {}

    def send(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(email_service, "send_email", send)
    pdf = documents.invoice_pdf(branded_invoice.id, db_session).body.decode()
    preview = documents.invoice_print_preview(
        branded_invoice.id, db_session
    ).body.decode()
    result = documents.email_invoice(
        branded_invoice.id,
        documents._EmailInvoiceRequest(recipient="customer@example.test"),
        _request(),
        db_session,
    )
    assert result == {"status": "sent"}
    attachment = captured["attachment_bytes"].decode()
    for html in [pdf, preview, attachment]:
        assert ('<img class="company-logo"' in html) is expected
        assert "LOGO-1" in html
        assert "Test Customer" in html
    assert pdf == attachment
    assert (
        preview.replace(
            "<script>window.onload=function(){window.print();}</script>", ""
        )
        == pdf
    )
    assert captured["attachment_name"] == "Invoice_LOGO-1.pdf"


@pytest.mark.parametrize("kind", ["sales_receipt", "pledge"])
def test_logo_setting_applies_to_invoice_document_variants(
    db_session, branded_invoice, kind
):
    branded_invoice.is_sales_receipt = kind == "sales_receipt"
    branded_invoice.is_pledge = kind == "pledge"
    company = get_all_settings(db_session)
    if kind == "pledge":
        company["company_type"] = "nonprofit"
    with_logo = pdf_service.render_invoice_html(branded_invoice, company)
    assert '<img class="company-logo"' in with_logo
    company["invoice_show_logo"] = "false"
    without_logo = pdf_service.render_invoice_html(branded_invoice, company)
    assert '<img class="company-logo"' not in without_logo
    assert ("PLEDGE" if kind == "pledge" else "SALES RECEIPT") in without_logo


def test_settings_default_and_saved_values_survive_unrelated_updates(db_session):
    assert settings_routes.get_settings(db_session)["invoice_show_logo"] == "true"
    for preference in ["false", "true"]:
        result = settings_routes.update_settings(
            settings_routes.SettingsUpdate(invoice_show_logo=preference),
            _request(),
            db_session,
        )
        assert result["invoice_show_logo"] == preference
        result = settings_routes.update_settings(
            settings_routes.SettingsUpdate(company_phone="555-0100"),
            _request(),
            db_session,
        )
        assert result["invoice_show_logo"] == preference


def test_disabling_invoice_logo_keeps_statement_logo(
    db_session, branded_invoice, seed_customer
):
    company = get_all_settings(db_session)
    company["invoice_show_logo"] = "false"
    assert '<img class="company-logo"' not in pdf_service.render_invoice_html(
        branded_invoice, company
    )
    statement = pdf_service.generate_statement_pdf(
        seed_customer, [], [], company
    ).decode()
    assert '<img class="company-logo"' in statement


@pytest.mark.parametrize("fault", ["not_configured", "missing_file", "unreadable_file"])
def test_unavailable_logo_does_not_break_invoice(
    db_session, branded_invoice, monkeypatch, fault
):
    company = get_all_settings(db_session)
    if fault == "not_configured":
        company["company_logo_path"] = ""
    elif fault == "missing_file":
        company["company_logo_path"] = "/static/uploads/missing.svg"
    else:
        from pathlib import Path

        def unavailable(path):
            raise OSError("Cannot read logo")

        monkeypatch.setattr(Path, "read_bytes", unavailable)
    html = pdf_service.render_invoice_html(branded_invoice, company)
    assert '<img class="company-logo"' not in html
    assert "LOGO-1" in html
