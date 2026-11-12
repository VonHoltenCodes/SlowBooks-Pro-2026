"""Invoice Quick Entry charges sales tax as the New Invoice form does (#802).

Quick Entry sent tax_rate 0 with every invoice, so a quick-entered invoice
with a taxable line billed and reported no sales tax. It now sends what the
invoice form sends: the company's default rate (Settings, Default tax rate)
as a fraction, which the operator can change for the batch, and each line's
Tax box. There is no per-customer rate in SlowBooks; a customer marked
non-taxable pays no tax on any line, whatever the boxes say, and the server
holds to that.

These post the body Quick Entry builds (App.qeInvoiceData in app.js; the
client side is tests/js/quick_entry_tax_802.test.cjs) and read back the
invoice and its journal entry.
"""

from decimal import Decimal

import pytest


def _quick_entry_body(customer_id, tax_pct, lines):
    """The invoice App.qeInvoiceData sends: the typed percent over 100, as
    a JavaScript number, and each line's Tax box."""
    return {
        "customer_id": customer_id,
        "date": "2026-11-02",
        "terms": "Net 30",
        "po_number": None,
        "tax_rate": (float(tax_pct) if tax_pct else 0) / 100,
        "notes": None,
        "lines": [
            {
                "item_id": None,
                "description": desc,
                "quantity": qty,
                "rate": rate,
                "line_order": i,
                "is_taxable": taxable,
            }
            for i, (desc, qty, rate, taxable) in enumerate(lines)
        ],
    }


def _default_rate(client, pct):
    r = client.put("/api/settings", json={"default_tax_rate": pct})
    assert r.status_code == 200, r.text
    # the panel reads it back from /api/settings, as the invoice form does
    return client.get("/api/settings").json()["default_tax_rate"]


def _customer(client, name, taxable=True):
    r = client.post("/api/customers", json={"name": name, "is_taxable": taxable})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _sales_tax_credit(db_session, txn_id):
    from app.models.accounts import Account
    from app.models.transactions import TransactionLine

    acct = db_session.query(Account).filter_by(account_number="2200").one()
    lines = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=txn_id, account_id=acct.id)
        .all()
    )
    return sum((Decimal(str(ln.credit or 0)) for ln in lines), Decimal("0"))


@pytest.fixture
def lines():
    return [("Sheet cake", 4, 25, True), ("Greeting cards", 1, 10, False)]


def test_a_taxable_customer_is_charged_the_company_rate_on_taxable_lines(
    client, db_session, seed_accounts, lines
):
    pct = _default_rate(client, "8.25")
    cust = _customer(client, "Corner Cafe")
    r = client.post("/api/invoices", json=_quick_entry_body(cust, pct, lines))
    assert r.status_code == 201, r.text
    inv = r.json()
    assert Decimal(inv["tax_rate"]) == Decimal("0.0825")
    assert Decimal(inv["subtotal"]) == Decimal("110.00")
    # 8.25% of the $100.00 taxable line; the unticked $10.00 line is untaxed
    assert Decimal(inv["tax_amount"]) == Decimal("8.25")
    assert Decimal(inv["total"]) == Decimal("118.25")
    assert Decimal(inv["balance_due"]) == Decimal("118.25")
    assert [ln["is_taxable"] for ln in inv["lines"]] == [True, False]

    from app.models.invoices import Invoice

    txn = db_session.get(Invoice, inv["id"]).transaction_id
    assert _sales_tax_credit(db_session, txn) == Decimal("8.25")


def test_a_rate_typed_on_the_panel_is_the_one_charged(
    client, db_session, seed_accounts, lines
):
    _default_rate(client, "8.25")
    cust = _customer(client, "Corner Cafe")
    r = client.post("/api/invoices", json=_quick_entry_body(cust, "6.875", lines))
    assert r.status_code == 201, r.text
    inv = r.json()
    assert Decimal(inv["tax_rate"]) == Decimal("0.06875")
    assert Decimal(inv["tax_amount"]) == Decimal("6.88")


def test_a_company_with_no_default_rate_charges_no_tax(
    client, db_session, seed_accounts, lines
):
    pct = _default_rate(client, "0")
    cust = _customer(client, "Corner Cafe")
    r = client.post("/api/invoices", json=_quick_entry_body(cust, pct, lines))
    assert r.status_code == 201, r.text
    assert Decimal(r.json()["tax_amount"]) == Decimal("0.00")
    assert Decimal(r.json()["total"]) == Decimal("110.00")


def test_a_non_taxable_customer_is_charged_no_tax_on_any_line(
    client, db_session, seed_accounts, lines
):
    pct = _default_rate(client, "8.25")
    cust = _customer(client, "Resale Shop", taxable=False)
    # even with a Tax box sent ticked, the customer's exemption holds
    ticked = [(d, q, r, True) for d, q, r, _ in lines]
    r = client.post("/api/invoices", json=_quick_entry_body(cust, pct, ticked))
    assert r.status_code == 201, r.text
    inv = r.json()
    assert Decimal(inv["tax_amount"]) == Decimal("0.00")
    assert Decimal(inv["total"]) == Decimal("110.00")
    assert [ln["is_taxable"] for ln in inv["lines"]] == [False, False]

    from app.models.invoices import Invoice

    txn = db_session.get(Invoice, inv["id"]).transaction_id
    assert _sales_tax_credit(db_session, txn) == Decimal("0")
