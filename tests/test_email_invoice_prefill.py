"""Email Invoice (2.17.3 exploratory, skytech W-L3).

The dialog's recipient box read `customer_email` from the invoice, a field
the invoice never had, so it was always empty; and with the default invoice
notes ("Thank you for your business.") the preview thanked the customer
twice, once in the notes and once in the closing line.
"""

from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


@pytest.fixture
def customer(client):
    r = client.post(
        "/api/customers", json={"name": "Acme Diner", "email": "ap@acme.test"}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _invoice(client, customer_id, notes):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-09-01",
            "tax_rate": 0,
            "notes": notes,
            "lines": [{"description": "Banner", "quantity": 1, "rate": 120}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _preview(client, inv):
    r = client.post(f"/api/invoices/{inv['id']}/email-preview", json={})
    assert r.status_code == 200, r.text
    return r.json()["html_body"]


def test_the_default_notes_and_the_closing_line_thank_once(
    client, seed_accounts, customer
):
    body = _preview(
        client, _invoice(client, customer["id"], "Thank you for your business.")
    )
    assert body.count("Thank you for your business.") == 1


def test_other_notes_still_get_the_closing_thanks(client, seed_accounts, customer):
    for notes in ("Please pay by check.", None):
        body = _preview(client, _invoice(client, customer["id"], notes))
        assert body.count("Thank you for your business.") == 1, notes


def test_the_dialog_fills_in_the_customers_address():
    src = (JS / "invoices.js").read_text(encoding="utf-8")
    dialog = src[src.index("async emailInvoice(id) {") :]
    dialog = dialog[: dialog.index("openModal(")]
    assert "inv.customer_email" not in dialog
    assert "(await API.get(`/customers/${inv.customer_id}`)).email" in dialog
