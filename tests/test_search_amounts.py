"""Global search finds a document by its amount (2.17.3 exploratory test,
W-L8): searching 612.30 found nothing, though an invoice, a bill and a
payment carried that amount. A query that reads as an amount now matches
documents to the cent — never a neighbouring cent, never 612.00 for 612.30.
"""

from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "app.js"


def _post(client, path, body):
    r = client.post(path, json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _line(rate, desc="work"):
    return {"description": desc, "quantity": 1, "rate": rate, "line_order": 0}


@pytest.fixture
def books(client, db_session, seed_accounts, seed_customer):
    from app.models.contacts import Vendor

    cid = seed_customer.id
    exact = _post(
        client,
        "/api/invoices",
        {
            "customer_id": cid,
            "date": "2026-09-01",
            "lines": [_line("592.50"), {**_line("19.80"), "line_order": 1}],
        },
    )
    # 1,000.00 with 387.70 paid: 612.30 still owed
    owing = _post(
        client,
        "/api/invoices",
        {"customer_id": cid, "date": "2026-09-02", "lines": [_line("1000.00")]},
    )
    _post(
        client,
        "/api/payments",
        {
            "customer_id": cid,
            "date": "2026-09-03",
            "amount": "387.70",
            "allocations": [{"invoice_id": owing["id"], "amount": "387.70"}],
        },
    )
    receipt = _post(  # the receipt's invoice and its own payment
        client,
        "/api/sales-receipts",
        {
            "customer_id": cid,
            "date": "2026-09-04",
            "tax_rate": "0",
            "method": "Cash",
            "lines": [_line("612.30")],
        },
    )
    payment = _post(
        client,
        "/api/payments",
        {
            "customer_id": cid,
            "date": "2026-09-05",
            "amount": "612.30",
            "reference": "WIRE-77",
            "allocations": [{"invoice_id": exact["id"], "amount": "612.30"}],
        },
    )
    memo = _post(
        client,
        "/api/credit-memos",
        {"customer_id": cid, "date": "2026-09-06", "lines": [_line("612.30")]},
    )
    vendor = Vendor(name="Sign Supply", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    bill = _post(
        client,
        "/api/bills",
        {
            "vendor_id": vendor.id,
            "bill_number": "SS-9",
            "date": "2026-09-07",
            "lines": [
                {
                    "account_id": seed_accounts["6400"].id,
                    "description": "panels",
                    "quantity": 1,
                    "rate": "612.30",
                }
            ],
        },
    )
    # a neighbouring cent, which must not match
    _post(
        client,
        "/api/invoices",
        {"customer_id": cid, "date": "2026-09-08", "lines": [_line("612.31")]},
    )
    return {
        "invoices": {exact["id"], owing["id"]},
        "sales_receipts": {receipt["invoice"]["id"]},
        "payments": {payment["id"], receipt["payment"]["id"]},
        "credit_memos": {memo["id"]},
        "bills": {bill["id"]},
        "wire": payment["id"],
    }


DOCS = ("invoices", "sales_receipts", "payments", "credit_memos", "bills")


def _ids(result, key):
    return {r["id"] for r in result.get(key, [])}


@pytest.mark.parametrize("query", ["612.30", "612.3", "$612.30"])
def test_an_amount_finds_every_document_for_it(client, books, query):
    found = client.get("/api/search", params={"q": query}).json()
    for key in DOCS:
        assert _ids(found, key) == books[key], (key, found.get(key))


def test_a_different_amount_finds_none_of_them(client, books):
    for query in ("612.00", "612", "612.29"):
        found = client.get("/api/search", params={"q": query}).json()
        for key in DOCS:
            assert not (_ids(found, key) & books[key]), (query, key)


def test_numbers_and_names_are_still_found(client, books):
    found = client.get("/api/search", params={"q": "WIRE-77"}).json()
    assert _ids(found, "payments") == {books["wire"]}
    found = client.get("/api/search", params={"q": "SS-9"}).json()
    assert _ids(found, "bills") == books["bills"]
    found = client.get("/api/search", params={"q": "Test Cust"}).json()
    assert found["customers"][0]["name"] == "Test Customer"


def test_the_dropdown_names_the_matching_document():
    js = APP_JS.read_text(encoding="utf-8")
    for key in ("sales_receipts", "credit_memos", "bills"):
        assert f"key: '{key}'" in js, key
    assert "text: (i) => doc(i.invoice_number, i.customer_name, i.total)" in js
    assert "PaymentsPage.view(${item.id})" in js
