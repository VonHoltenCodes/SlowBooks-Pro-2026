"""Printed addresses (2.17.3 exploratory: macbase1 F21, skytech W-L2).

The invoice, estimate and statement templates wrote "city, state zip", so an
address with no state or ZIP printed "Port Alder," with a comma hanging off
it, and a customer abroad printed with no country — Bäckerei Müller in
München read as a US address. The comma is gone, and a customer outside the
United States gets their country printed under the address.
"""

import pytest

from app.services import pdf_service


def test_the_city_line_has_no_dangling_comma():
    from app.services.addresses import city_line

    assert city_line("Port Alder", "", None) == "Port Alder"
    assert city_line("Port Alder", "OR", "97000") == "Port Alder, OR 97000"
    assert city_line("München", None, "80331") == "München, 80331"
    assert city_line("", "OR", "97000") == "OR 97000"
    assert city_line(None, None, None) == ""


def test_the_country_prints_only_when_it_is_not_home():
    from app.services.addresses import country_line

    assert country_line("DE") == "Germany"
    assert country_line("de") == "Germany"
    assert country_line("Deutschland") == "Deutschland"
    for home in ("US", "USA", "United States", "", None):
        assert country_line(home) == "", home


@pytest.fixture
def html_pdfs(monkeypatch):
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())


def _customer(client, **fields):
    r = client.post("/api/customers", json=fields)
    assert r.status_code == 201, r.text
    return r.json()


def _doc(client, path, customer_id):
    r = client.post(
        path,
        json={
            "customer_id": customer_id,
            "date": "2026-09-10",
            "tax_rate": 0,
            "lines": [{"description": "Design", "quantity": 1, "rate": 85}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_a_customer_abroad_prints_their_country(client, seed_accounts, html_pdfs):
    cust = _customer(
        client,
        name="Baeckerei Mueller",  # the statement names its file after the customer
        bill_address1="Hauptstraße 5",
        bill_city="München",
        bill_zip="80331",
        bill_country="DE",
    )
    inv = _doc(client, "/api/invoices", cust["id"])
    est = _doc(client, "/api/estimates", cust["id"])
    pages = {
        "invoice": client.get(f"/api/invoices/{inv['id']}/pdf").text,
        "invoice preview": client.get(f"/api/invoices/{inv['id']}/print-preview").text,
        "estimate": client.get(f"/api/estimates/{est['id']}/pdf").text,
        "estimate preview": client.get(
            f"/api/estimates/{est['id']}/print-preview"
        ).text,
        "statement": client.get(
            f"/api/reports/customer-statement/{cust['id']}/pdf"
        ).text,
    }
    for name, html in pages.items():
        assert "München, 80331<br>" in html, name
        assert "Germany" in html, name


def test_a_home_address_prints_no_country_and_no_dangling_comma(
    client, seed_accounts, html_pdfs
):
    cust = _customer(
        client,
        name="Salt & Pine Catering Co.",
        bill_address1="12 Dock Rd",
        bill_city="Port Alder",
    )
    inv = _doc(client, "/api/invoices", cust["id"])
    est = _doc(client, "/api/estimates", cust["id"])
    for html in (
        client.get(f"/api/invoices/{inv['id']}/pdf").text,
        client.get(f"/api/estimates/{est['id']}/pdf").text,
        client.get(f"/api/reports/customer-statement/{cust['id']}/pdf").text,
    ):
        assert "Port Alder<br>" in html
        assert "Port Alder," not in html
        assert "United States" not in html
