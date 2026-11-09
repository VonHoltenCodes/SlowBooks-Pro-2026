"""A/R and A/P Aging read what was open on the report's date, not what is
open today: an invoice or bill paid after the date still
counts, one paid before it does not, and the total equals account 1100 / 2000
on the Balance Sheet for the same date."""

from decimal import Decimal

import pytest

from app.models.contacts import Customer, Vendor


def _balances(client, end):
    tb = client.get(
        f"/api/reports/trial-balance?start_date=2000-01-01&end_date={end}"
    ).json()
    return {i["account_number"]: Decimal(str(i["net_balance"])) for i in tb["items"]}


def _invoice(client, cid, amount, day):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": day,
            "tax_rate": 0,
            "lines": [{"description": "Work", "quantity": 1, "rate": amount}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _bill(client, vid, amount, day, **extra):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vid,
            "date": day,
            "lines": [{"description": "Work", "quantity": 1, "rate": amount}],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def parties(db_session, seed_accounts):
    c = Customer(name="Lakeside Homes", is_active=True)
    v = Vendor(
        name="Sparks Electric",
        is_active=True,
        default_expense_account_id=seed_accounts["6000"].id,
    )
    db_session.add_all([c, v])
    db_session.commit()
    return c, v


def test_ar_aging_counts_an_invoice_paid_after_the_date(client, parties):
    c, _ = parties
    paid_later = _invoice(client, c.id, 1000, "2026-08-01")
    paid_before = _invoice(client, c.id, 500, "2026-08-10")
    open_still = _invoice(client, c.id, 300, "2026-08-15")
    for inv, amount, day in (
        (paid_before, 500, "2026-08-20"),
        (paid_later, 1000, "2026-09-15"),
    ):
        r = client.post(
            "/api/payments",
            json={
                "customer_id": c.id,
                "date": day,
                "amount": amount,
                "allocations": [{"invoice_id": inv["id"], "amount": amount}],
            },
        )
        assert r.status_code == 201, r.text
    # a credit memo dated after the first date, applied to the open invoice
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": c.id,
            "date": "2026-10-05",
            "lines": [{"description": "Allowance", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    memo = r.json()
    r = client.post(
        f"/api/credit-memos/{memo['id']}/apply",
        json={"invoice_id": open_still["id"], "amount": 100},
    )
    assert r.status_code == 200, r.text

    expect = {
        "2026-08-18": 1800,  # the 500 is not paid until the 20th
        "2026-09-01": 1300,  # the 500 is paid; the 1000 is not until the 15th
        "2026-09-30": 300,  # both paid; the credit is dated 10-05
        "2026-10-31": 200,  # the credit applied to the 300
    }
    for day, owed in expect.items():
        aging = client.get(f"/api/reports/ar-aging?as_of_date={day}").json()
        assert Decimal(str(aging["totals"]["total"])) == Decimal(owed), day
        assert _balances(client, day).get("1100", 0) == Decimal(owed), day


def test_ap_aging_counts_a_bill_paid_after_the_date(client, parties):
    _, v = parties
    paid_later = _bill(client, v.id, 1000, "2026-08-01")
    paid_before = _bill(client, v.id, 500, "2026-08-10")
    for bill, amount, day in (
        (paid_before, 500, "2026-08-20"),
        (paid_later, 1000, "2026-09-15"),
    ):
        r = client.post(
            "/api/bill-payments",
            json={
                "vendor_id": v.id,
                "date": day,
                "amount": amount,
                "allocations": [{"bill_id": bill["id"], "amount": amount}],
            },
        )
        assert r.status_code == 201, r.text
    for day, owed in (
        ("2026-08-15", 1500),
        ("2026-09-01", 1000),
        ("2026-09-30", 0),
    ):
        aging = client.get(f"/api/reports/ap-aging?as_of_date={day}").json()
        assert Decimal(str(aging["totals"]["total"])) == Decimal(owed), day
        # account 2000 is a credit balance, so the trial balance reads it negative
        assert abs(_balances(client, day).get("2000", Decimal(0))) == Decimal(owed), day
