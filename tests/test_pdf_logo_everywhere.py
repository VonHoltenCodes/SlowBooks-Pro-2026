"""Discussion #108: the company logo rendered on the analytics PDF and the
new-hire report only. Every customer-facing document gets it now — the
render helper attaches the logo, each template's header shows it."""

import base64

import pytest

from app.services import pdf_service

# a whole PNG: the upload decodes a raster logo before keeping it (NEW-201)
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)
DATA_URI = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"


@pytest.fixture
def logo(monkeypatch, client):
    # kept in the company's own database since 2.18.0 (macbase1 NEW-14)
    r = client.post("/api/uploads/logo", files={"file": ("logo.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    # capture the HTML the PDF engine would have rendered
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    return DATA_URI


def _invoice(client, cid):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": "2026-04-01",
            "tax_rate": 0,
            "lines": [{"description": "x", "quantity": 1, "rate": 40, "line_order": 0}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_invoice_estimate_statement_and_report_pdfs_carry_the_logo(
    client, seed_accounts, seed_customer, logo
):
    inv = _invoice(client, seed_customer.id)
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "tax_rate": 0,
            "lines": [{"description": "x", "quantity": 1, "rate": 40, "line_order": 0}],
        },
    )
    assert est.status_code == 201, est.text
    pages = {
        "invoice": client.get(f"/api/invoices/{inv['id']}/pdf"),
        "estimate": client.get(f"/api/estimates/{est.json()['id']}/pdf"),
        "statement": client.get(
            f"/api/reports/customer-statement/{seed_customer.id}/pdf"
        ),
        "report": client.get(
            "/api/reports/profit-loss/pdf?start_date=2026-01-01&end_date=2026-12-31"
        ),
    }
    missing = [
        name for name, r in pages.items() if r.status_code != 200 or logo not in r.text
    ]
    assert not missing, {
        n: (pages[n].status_code, pages[n].text[:120]) for n in missing
    }


def test_no_logo_set_means_no_logo_tag(
    client, seed_accounts, seed_customer, monkeypatch
):
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    inv = _invoice(client, seed_customer.id)
    html = client.get(f"/api/invoices/{inv['id']}/pdf").text
    assert 'class="company-logo"' not in html and "data:image" not in html


def test_collection_letter_and_giving_statement_carry_the_logo(
    client, db_session, seed_accounts, seed_customer, logo
):
    """The collection-letter route writes files and emails rather than
    returning the PDF, so the generator is exercised directly."""
    from app.models.invoices import Invoice
    from app.services.settings_service import get_all_settings

    inv = _invoice(client, seed_customer.id)
    invoice = db_session.query(Invoice).filter(Invoice.id == inv["id"]).first()
    html = pdf_service.generate_collection_letter_pdf(
        seed_customer,
        [invoice],
        get_all_settings(db_session),
        "30",
        invoice.balance_due,
    ).decode()
    assert logo in html, html[:300]
    giving = client.get(
        f"/api/donors/{seed_customer.id}/giving-statement/pdf?year=2026"
    )
    assert giving.status_code == 200, giving.text[:200]
    assert logo in giving.text, giving.text[:300]
