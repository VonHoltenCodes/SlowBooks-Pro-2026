"""Construction-overhaul leftovers (workstream L): each small fix, pinned."""

from decimal import Decimal

from tests.test_co_effective_date_allowance import (
    _app,
    _approve,
    _contract,
    _owner_co,
    _setup,
)
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


def test_voiding_a_change_order_in_a_closed_period_is_refused(client, seed_customer):
    job, _sov = _setup(client, seed_customer.id, [], 100000)
    co = _owner_co(client, job["id"], 1000, date="2026-08-10")
    assert _approve(client, co["id"]).status_code == 200
    assert (
        client.put("/api/settings", json={"closing_date": "2026-09-30"}).status_code
        == 200
    )
    r = client.post(f"/api/change-orders/{co['id']}/void")
    assert r.status_code == 403, r.text
    assert "on or before the closing date" in r.json()["detail"]
    assert client.get(f"/api/change-orders/{co['id']}").json()["status"] == "approved"
    assert _contract(client, job["id"])["contract"]["contract_amount"] == 101000.0
    # an open period still voids
    client.put("/api/settings", json={"closing_date": ""})
    assert client.post(f"/api/change-orders/{co['id']}/void").status_code == 200


def test_a_pay_application_before_a_change_order_leaves_its_line_off(
    client, db_session, seed_accounts, seed_customer
):
    """The schedule on an application dated before the change order takes
    effect equals the contract sum to date on that application, on screen and
    on the PDF; one dated after lists the change order's line."""
    from app.models.pay_applications import PayApplication
    from app.routes.pay_applications import _out
    from app.services.pdf_service import render_pay_application_html
    from app.services.settings_service import get_all_settings

    job, sov = _setup(
        client,
        seed_customer.id,
        [{"description": "Base", "scheduled_value": 100000}],
        100000,
    )
    co = _owner_co(client, job["id"], 13640, date="2026-08-10")
    assert _approve(client, co["id"]).status_code == 200

    july = _app(client, job["id"], "2026-07-31")
    got = client.get(f"/api/pay-applications/{july['id']}").json()
    assert [ln["description"] for ln in got["lines"]] == ["Base"]
    assert got["totals"]["scheduled_total"] == 100000.0
    # a refresh (the tab calls it on opening a draft) does not add it back
    assert (
        client.post(f"/api/pay-applications/{july['id']}/refresh-lines").status_code
        == 200
    )
    got = client.get(f"/api/pay-applications/{july['id']}").json()
    assert got["totals"]["scheduled_total"] == 100000.0

    db_session.expire_all()
    pa = db_session.get(PayApplication, july["id"])
    html = render_pay_application_html(
        pa, _out(db_session, pa), pa.job, get_all_settings(db_session)
    )
    assert "Deck" not in html and "113,640.00" not in html

    # moving the date past the change order's brings its line in
    r = client.put(f"/api/pay-applications/{july['id']}", json={"date": "2026-08-31"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert len(got["lines"]) == 2
    assert got["totals"]["scheduled_total"] == 113640.0

    assert client.delete(f"/api/pay-applications/{july['id']}").status_code == 200
    august = _app(client, job["id"], "2026-08-31")
    got = client.get(f"/api/pay-applications/{august['id']}").json()
    assert got["totals"]["scheduled_total"] == 113640.0
