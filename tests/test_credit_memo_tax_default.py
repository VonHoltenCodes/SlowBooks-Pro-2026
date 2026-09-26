"""A credit memo's tax (2.17.3 exploratory: skytech W-L14, macbase1 F14).

The Credit Memo form defaulted the tax rate to 0% where a new invoice
defaults to the company's rate, so returned taxable goods were credited
without their tax unless the user remembered. The form now starts from the
company's rate and, when the memo credits a particular invoice, from that
invoice's rate. Tax falls on the lines an invoice would tax: an item's own
flag, and nothing for a non-taxable customer.
"""

from decimal import Decimal
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


def _item(client, name, rate, taxable):
    r = client.post(
        "/api/items",
        json={
            "name": name,
            "item_type": "product",
            "rate": rate,
            "is_taxable": taxable,
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.fixture
def invoice(client, seed_accounts, seed_customer):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-01",
            "tax_rate": 0.0825,
            "lines": [{"description": "Sourdough Loaf", "quantity": 4, "rate": 8.5}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _memo(client, customer_id, **extra):
    body = {
        "customer_id": customer_id,
        "date": "2026-09-26",
        "lines": [{"description": "Sourdough Loaf", "quantity": 2, "rate": 8.5}],
    }
    body.update(extra)
    return client.post("/api/credit-memos", json=body)


def test_crediting_an_invoice_takes_its_tax_rate(client, seed_customer, invoice):
    r = _memo(client, seed_customer.id, original_invoice_id=invoice["id"])
    assert r.status_code == 201, r.text
    cm = r.json()
    assert Decimal(cm["tax_rate"]) == Decimal("0.0825")
    assert Decimal(cm["tax_amount"]) == Decimal("1.40")
    assert Decimal(cm["total"]) == Decimal("18.40")


def test_a_rate_the_user_states_wins(client, seed_customer, invoice):
    r = _memo(client, seed_customer.id, original_invoice_id=invoice["id"], tax_rate=0)
    assert r.status_code == 201, r.text
    assert Decimal(r.json()["tax_amount"]) == Decimal("0.00")


def test_without_an_invoice_the_api_default_is_still_no_tax(
    client, seed_accounts, seed_customer
):
    r = _memo(client, seed_customer.id)
    assert r.status_code == 201, r.text
    assert Decimal(r.json()["tax_amount"]) == Decimal("0.00")


def test_the_invoice_must_be_this_customers(client, db_session, seed_customer, invoice):
    from app.models.credit_memos import CreditMemo

    other = client.post("/api/customers", json={"name": "Blue Heron"}).json()
    r = _memo(client, other["id"], original_invoice_id=invoice["id"])
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == (
        f"Invoice {invoice['invoice_number']} belongs to a different customer "
        "than this credit memo."
    )
    r = _memo(client, seed_customer.id, original_invoice_id=999999)
    assert r.status_code == 404, r.text
    assert db_session.query(CreditMemo).count() == 0


def test_tax_falls_on_the_lines_an_invoice_would_tax(
    client, seed_accounts, seed_customer
):
    labor = _item(client, "Delivery", 25, False)
    loaf = _item(client, "Sourdough Loaf", 8.5, True)
    r = _memo(
        client,
        seed_customer.id,
        tax_rate=0.0825,
        lines=[
            {"item_id": labor["id"], "quantity": 1, "rate": 25},
            {"item_id": loaf["id"], "quantity": 2, "rate": 8.5},
        ],
    )
    assert r.status_code == 201, r.text
    # 8.25% of the $17.00 of bread, not of the $42.00 memo
    assert Decimal(r.json()["tax_amount"]) == Decimal("1.40")

    exempt = client.post(
        "/api/customers", json={"name": "Church Pantry", "is_taxable": False}
    ).json()
    r = _memo(client, exempt["id"], tax_rate=0.0825)
    assert r.status_code == 201, r.text
    assert Decimal(r.json()["tax_amount"]) == Decimal("0.00")


def test_the_form_starts_from_the_company_rate_and_follows_the_invoice():
    src = (JS / "credit_memos.js").read_text(encoding="utf-8")
    assert (
        "const defaultPct = parseFloat(settings.default_tax_rate || '0') || 0;" in src
    )
    assert 'name="tax_rate" type="number" step="0.01" value="${defaultPct}"' in src
    assert (
        'id="cm-invoice-select" onchange="CreditMemosPage.invoiceSelected(this.value)"'
        in src
    )
    picked = src[src.index("invoiceSelected(invoiceId) {") :]
    picked = picked[: picked.index("\n    },")]
    assert "rate.value = " in picked and "inv.tax_rate" in picked
    save = src[src.index("async save(e) {") :]
    assert "original_invoice_id: form.original_invoice_id.value" in save
    assert "is_taxable: row.querySelector('.line-taxable')" in save
    assert (
        "TaxExempt.enforce(CreditMemosPage._customers, $('#cm-customer-select')?.value"
        in src
    )
