"""Construction-overhaul leftovers (workstream L): each small fix, pinned."""

from decimal import Decimal

from tests.test_cash_basis_construction import (
    _commitment,
    _job,
    _release,
    _sub,
    _sub_bill,
)

D = Decimal


def test_a_retainage_release_bill_cannot_be_re_lined(
    client, db_session, seed_accounts, seed_customer
):
    """Editing the release bill's line re-posted it to an expense account
    instead of debiting 2050; it is refused, and its note still saves."""
    sub = _sub(client, "Framer")
    job, _sov = _job(client, seed_customer.id)
    po = _commitment(client, sub["id"], job["id"], 8000, None)
    _sub_bill(client, po, "2026-07-01", 8000, seed_accounts["5100"].id)
    bill_id, amount = _release(client, po, "2026-08-01")
    assert amount > 0

    bill = client.get(f"/api/bills/{bill_id}").json()
    r = client.put(
        f"/api/bills/{bill_id}",
        json={
            "lines": [
                {
                    "account_id": seed_accounts["5100"].id,
                    "description": "x",
                    "quantity": 1,
                    "rate": float(amount),
                }
            ]
        },
    )
    assert r.status_code == 409, r.text
    assert "can't be re-lined" in r.json()["detail"]

    r = client.put(f"/api/bills/{bill_id}", json={"notes": "per the sub"})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/bills/{bill_id}").json()["total"] == bill["total"]
