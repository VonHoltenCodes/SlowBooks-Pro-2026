"""Payment history names the account a bill payment came from when Pay
From was left blank (#813): the server posts it from Checking, and the
history said nothing in its Paid from column."""


def test_a_blank_pay_from_reads_checking(client, seed_accounts):
    vendor = client.post("/api/vendors", json={"name": "Harbor Supply"}).json()
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "B-1",
            "date": "2026-09-01",
            "lines": [
                {
                    "description": "Supplies",
                    "amount": 40,
                    "account_id": seed_accounts["6000"].id,
                }
            ],
        },
    ).json()
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-09-10",
            "amount": 40,
            "method": "check",
            "allocations": [{"bill_id": bill["id"], "amount": 40}],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json().get("pay_from_account_id") in (None, seed_accounts["1000"].id)
    h = client.get(
        f"/api/vendors/{vendor['id']}/payment-history"
        "?start_date=2026-01-01&end_date=2026-12-31"
    ).json()
    (row,) = h["rows"]
    assert row["paid_from"] == seed_accounts["1000"].name
