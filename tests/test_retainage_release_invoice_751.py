"""A pay application that only releases retainage reads as one (#751).

The last application on a job often bills no new work and only releases the
retainage held back. Its invoice posts no lines, a $0.00 total and a balance
due equal to the release, so the PDF printed an empty line table under a
$0.00 Total. The PDF and GET /invoices/{id} now name the release.
"""

import io

import pytest

from tests.test_pay_applications import (
    _approve,
    _edit,
    _new_app,
    _two_line_job,
)


def _release_only_invoice(client, customer_id):
    """Bill 30,000 with 10% held, then release the 3,000 with no new work."""
    job, sov = _two_line_job(client, customer_id)
    a1 = _new_app(client, job["id"])
    _edit(
        client,
        a1["id"],
        lines=[
            {"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0},
            {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
        ],
    )
    assert _approve(client, a1["id"]).status_code == 200
    a2 = _new_app(client, job["id"])
    _edit(client, a2["id"], release_retainage=3000)
    r = _approve(client, a2["id"])
    assert r.status_code == 200, r.text
    return r.json()["invoice_id"], a2["number"]


def test_the_invoice_names_its_pay_application(client, seed_accounts, seed_customer):
    inv_id, number = _release_only_invoice(client, seed_customer.id)
    inv = client.get(f"/api/invoices/{inv_id}").json()
    assert inv["lines"] == []
    assert inv["total"] == "0.00"
    assert inv["retainage_released"] == "3000.00"
    assert inv["pay_application_number"] == number


def test_an_ordinary_invoice_has_no_pay_application_number(
    client, seed_accounts, seed_customer
):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-01",
            "lines": [{"description": "Banner", "quantity": 1, "rate": 120}],
        },
    )
    assert r.status_code == 201, r.text
    inv = client.get(f"/api/invoices/{r.json()['id']}").json()
    assert inv["pay_application_number"] is None


def test_the_pdf_names_the_release_and_drops_the_zero_total(
    client, seed_accounts, seed_customer
):
    pypdf = pytest.importorskip("pypdf")
    inv_id, number = _release_only_invoice(client, seed_customer.id)
    r = client.get(f"/api/invoices/{inv_id}/pdf")
    assert r.status_code == 200, r.text[:200]
    text = pypdf.PdfReader(io.BytesIO(r.content)).pages[0].extract_text()
    assert f"Retainage release, {number}" in text, text[:600]
    assert "Current payment due" in text.replace("\n", " ")
    assert "Subtotal" not in text
    assert "$0.00\nTotal" not in text and "Total $0.00" not in text


def test_an_invoice_with_work_still_shows_its_total(
    client, seed_accounts, seed_customer
):
    pypdf = pytest.importorskip("pypdf")
    job, sov = _two_line_job(client, seed_customer.id)
    a1 = _new_app(client, job["id"])
    _edit(
        client,
        a1["id"],
        lines=[
            {"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0},
            {"sov_line_id": sov[1]["id"], "this_period": 0, "stored": 0},
        ],
    )
    r = _approve(client, a1["id"])
    assert r.status_code == 200, r.text
    pdf = client.get(f"/api/invoices/{r.json()['invoice_id']}/pdf")
    text = pypdf.PdfReader(io.BytesIO(pdf.content)).pages[0].extract_text()
    assert "Subtotal" in text and "Retainage release" not in text
