"""A sales receipt goes to QuickBooks once (found while fixing W-M14).

SlowBooks keeps a sales receipt as an invoice plus its own payment;
QuickBooks keeps it as one CASH SALE, which the IIF export writes. The
receipt's payment was exported too — a PAYMENT applied to the receipt's
number — so an import took the money twice and left the customer a
phantom credit. The payment block now leaves out what a receipt covers.
"""

from decimal import Decimal


def _rows(content: bytes):
    return [line.split("\t") for line in content.decode("cp1252").splitlines()]


def test_a_sales_receipt_is_one_cash_sale_and_no_payment(
    client, seed_accounts, seed_customer
):
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-04",
            "tax_rate": "0",
            "method": "Cash",
            "lines": [{"description": "banner", "quantity": 1, "rate": "50.00"}],
        },
    )
    assert r.status_code == 201, r.text
    number = r.json()["invoice"]["invoice_number"]
    # an ordinary payment still goes out
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-05",
            "lines": [{"description": "sign", "quantity": 1, "rate": "80.00"}],
        },
    ).json()
    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-06",
            "amount": "80.00",
            "allocations": [{"invoice_id": inv["id"], "amount": "80.00"}],
        },
    )
    assert r.status_code == 201, r.text

    rows = _rows(client.get("/api/iif/export/all").content)
    sales = [r for r in rows if r[:2] == ["TRNS", "CASH SALE"]]
    payments = [r for r in rows if r[:2] == ["TRNS", "PAYMENT"]]
    applied = [r[6] for r in rows if r[:2] == ["SPL", "PAYMENT"]]
    assert len(sales) == 1 and Decimal(sales[0][5]) == Decimal("50.00")
    assert [Decimal(p[5]) for p in payments] == [Decimal("80.00")]
    assert number not in applied and inv["invoice_number"] in applied
