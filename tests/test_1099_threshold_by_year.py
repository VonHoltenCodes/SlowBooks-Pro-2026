"""The 1099-NEC reporting threshold follows the year the payments were made:
$600 through 2025, $2,000 from 2026 (the One Big Beautiful Bill Act,
section 70433, for payments after December 31, 2025). Before 2.31.3 the
code and docs/banking.md said $600 for every year, so a vendor paid $700
in 2026 was listed as reportable when no form is due.
"""

from decimal import Decimal

from app.services.form_1099 import nec_threshold


def test_the_threshold_by_year():
    assert nec_threshold(2024) == Decimal("600.00")
    assert nec_threshold(2025) == Decimal("600.00")
    assert nec_threshold(2026) == Decimal("2000.00")
    assert nec_threshold(2027) == Decimal(
        "2000.00"
    )  # until the IRS publishes the indexed figure


def _pay(client, seed_accounts, vendor_id, amount, when):
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "bill_number": f"{vendor_id}-{when}",
            "date": when,
            "lines": [
                {
                    "account_id": seed_accounts["6000"].id,
                    "description": "Install",
                    "quantity": 1,
                    "rate": amount,
                }
            ],
        },
    )
    assert bill.status_code == 201, bill.text
    pay = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor_id,
            "date": when,
            "amount": amount,
            "allocations": [{"bill_id": bill.json()["id"], "amount": amount}],
        },
    )
    assert pay.status_code == 201, pay.text


def test_seven_hundred_dollars_is_reportable_in_2025_and_not_in_2026(
    client, seed_accounts
):
    v = client.post(
        "/api/vendors", json={"name": "Harbor Signs", "is_1099_vendor": True}
    ).json()
    _pay(client, seed_accounts, v["id"], 700, "2025-06-10")
    _pay(client, seed_accounts, v["id"], 700, "2026-06-10")

    for year, reportable, threshold in ((2025, True, 600.0), (2026, False, 2000.0)):
        nec = client.get(f"/api/tax-forms/1099?year={year}").json()["vendors"]
        row = next(r for r in nec if r["vendor_id"] == v["id"])
        assert Decimal(str(row["total_paid"])) == Decimal("700.00")
        assert row["reportable"] is reportable, year
        summary = client.get(f"/api/reports/1099-summary?year={year}").json()
        assert summary["threshold"] == threshold
        item = next(i for i in summary["items"] if i["vendor_id"] == v["id"])
        assert item["above_threshold"] is reportable, year
        assert summary["vendors_above_threshold"] == (1 if reportable else 0)
