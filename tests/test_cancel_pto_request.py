"""Cancelling a PTO request (issue #262).

An approved request could only stay approved; when the trip fell through
the hours stayed out of the bank and the relief journal stayed posted.
A cancel puts back exactly what the approval took — the hours that were
in the bank and the relief's dollars — and the request stays on the list
as cancelled. The portal lets an employee withdraw their own request while
it is still pending.
"""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import func

from app.models.transactions import Transaction, TransactionLine


def _emp(client, rate=50, **kw):
    body = {
        "first_name": kw.pop("first_name", "Pat"),
        "last_name": kw.pop("last_name", "Worker"),
        "pay_type": "hourly",
        "pay_rate": rate,
        "pay_frequency": "biweekly",
        "filing_status": "single",
        "work_state": "WA",
    }
    body.update(kw)
    r = client.post("/api/employees", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _policy(client, liability=False, pto_type="vacation"):
    body = {
        "name": "Vacation" if pto_type == "vacation" else "Sick",
        "pto_type": pto_type,
        "accrual_method": "per_pay_period",
        "accrual_rate": 8,
    }
    if liability:
        body.update({"accrue_liability": True, "valuation": "current_rate"})
    r = client.post("/api/pto/policies", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _bank(client, emp, policy, balance):
    r = client.post(
        "/api/pto/accruals",
        json={"employee_id": emp["id"], "policy_id": policy["id"], "balance": balance},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _request(client, emp, hours, start="2026-07-20", pto_type="vacation"):
    r = client.post(
        "/api/pto/requests",
        json={
            "employee_id": emp["id"],
            "start_date": start,
            "end_date": start,
            "hours": hours,
            "pto_type": pto_type,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _approve(client, req):
    r = client.post(f"/api/pto/requests/{req['id']}/approve")
    assert r.status_code == 200, r.text
    return r.json()


def _bank_now(client, emp):
    return client.get(f"/api/pto/accruals?employee_id={emp['id']}").json()[0]


def _acct_balance(db, acct_id):
    dr = (
        db.query(func.coalesce(func.sum(TransactionLine.debit), 0))
        .filter(TransactionLine.account_id == acct_id)
        .scalar()
    )
    cr = (
        db.query(func.coalesce(func.sum(TransactionLine.credit), 0))
        .filter(TransactionLine.account_id == acct_id)
        .scalar()
    )
    return Decimal(str(dr)), Decimal(str(cr))


def _pto_journals(db):
    return (
        db.query(Transaction)
        .filter(Transaction.source_type == "pto")
        .order_by(Transaction.id)
        .all()
    )


# ---------------------------------------------------------------------------
# The HR side
# ---------------------------------------------------------------------------
def test_cancelling_an_approved_request_puts_the_hours_back(client, db_session):
    emp = _emp(client)
    pol = _policy(client)
    _bank(client, emp, pol, 10)
    req = _approve(client, _request(client, emp, 8))
    assert req["hours_drawn"] == 8.0
    bank = _bank_now(client, emp)
    assert bank["balance"] == 2.0 and bank["used_ytd"] == 8.0

    r = client.post(f"/api/pto/requests/{req['id']}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    bank = _bank_now(client, emp)
    assert bank["balance"] == 10.0 and bank["used_ytd"] == 0.0
    # it stays on the list, as cancelled, and the filter finds it
    listed = client.get("/api/pto/requests?status=cancelled").json()
    assert [x["id"] for x in listed] == [req["id"]]
    # an hours-only policy never posts
    assert _pto_journals(db_session) == []


def test_cancelling_reverses_the_relief_journal(client, db_session, seed_accounts):
    emp = _emp(client, rate=50)
    pol = _policy(client, liability=True)
    acc = _bank(client, emp, pol, 0)
    r = client.post(
        f"/api/pto/accruals/{acc['id']}/accrue",
        json={"hours_worked": 0, "as_of": "2026-07-17"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["balance"] == 8.0 and r.json()["dollar_balance"] == 400.0
    expense, liability = seed_accounts["6160"].id, seed_accounts["2390"].id

    req = _approve(client, _request(client, emp, 4))
    bank = _bank_now(client, emp)
    assert bank["balance"] == 4.0 and bank["dollar_balance"] == 200.0
    assert _acct_balance(db_session, liability) == (Decimal("200"), Decimal("400"))

    r = client.post(f"/api/pto/requests/{req['id']}/cancel")
    assert r.status_code == 200, r.text
    bank = _bank_now(client, emp)
    assert bank["balance"] == 8.0 and bank["dollar_balance"] == 400.0
    assert bank["used_ytd"] == 0.0
    # accrual 400, relief 200, the reversal 200: the liability is whole again
    assert _acct_balance(db_session, liability) == (Decimal("200"), Decimal("600"))
    assert _acct_balance(db_session, expense) == (Decimal("600"), Decimal("200"))
    journals = _pto_journals(db_session)
    assert len(journals) == 3
    assert journals[-1].description.startswith("PTO request cancelled")
    assert journals[-1].date == journals[1].date  # posted as of the request


def test_an_overdrawn_approval_gives_back_only_what_it_took(client):
    emp = _emp(client)
    pol = _policy(client)
    _bank(client, emp, pol, 6)
    req = _approve(client, _request(client, emp, 8))
    assert req["hours_drawn"] == 6.0  # the bank had six; it went to zero
    bank = _bank_now(client, emp)
    assert bank["balance"] == 0.0 and bank["used_ytd"] == 8.0

    client.post(f"/api/pto/requests/{req['id']}/cancel")
    bank = _bank_now(client, emp)
    assert bank["balance"] == 6.0 and bank["used_ytd"] == 0.0


def test_a_pending_request_cancels_without_touching_the_bank(client):
    emp = _emp(client)
    pol = _policy(client)
    _bank(client, emp, pol, 10)
    req = _request(client, emp, 8)
    assert req["hours_drawn"] is None

    r = client.post(f"/api/pto/requests/{req['id']}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    bank = _bank_now(client, emp)
    assert bank["balance"] == 10.0 and bank["used_ytd"] == 0.0
    # a cancelled request can no longer be decided
    r = client.post(f"/api/pto/requests/{req['id']}/approve")
    assert r.status_code == 400


def test_denied_and_cancelled_requests_refuse_a_cancel(client):
    emp = _emp(client)
    denied = _request(client, emp, 8)
    client.post(f"/api/pto/requests/{denied['id']}/reject")
    r = client.post(f"/api/pto/requests/{denied['id']}/cancel")
    assert r.status_code == 400 and "denied" in r.json()["detail"]

    once = _request(client, emp, 8, start="2026-08-03")
    assert client.post(f"/api/pto/requests/{once['id']}/cancel").status_code == 200
    r = client.post(f"/api/pto/requests/{once['id']}/cancel")
    assert r.status_code == 400 and "already cancelled" in r.json()["detail"]

    assert client.post("/api/pto/requests/99999/cancel").status_code == 404


def test_a_request_approved_without_a_bank_has_nothing_to_give_back(client):
    emp = _emp(client)
    req = _approve(client, _request(client, emp, 8))
    assert req["hours_drawn"] == 0.0
    r = client.post(f"/api/pto/requests/{req['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


def test_a_request_approved_before_the_draw_was_recorded_assumes_its_hours(
    client, db_session, seed_accounts
):
    """Rows approved by an earlier build carry no hours_drawn / dollars_relieved.
    The cancel then puts the request's own hours back, valued the way the
    policy values the bank today."""
    from app.models.pto import PTORequest

    emp = _emp(client, rate=50)
    pol = _policy(client, liability=True)
    acc = _bank(client, emp, pol, 0)
    client.post(
        f"/api/pto/accruals/{acc['id']}/accrue",
        json={"hours_worked": 0, "as_of": "2026-07-17"},
    )
    req = _approve(client, _request(client, emp, 4))
    row = db_session.query(PTORequest).get(req["id"])
    row.hours_drawn = None
    row.dollars_relieved = None
    db_session.commit()

    r = client.post(f"/api/pto/requests/{req['id']}/cancel")
    assert r.status_code == 200, r.text
    bank = _bank_now(client, emp)
    assert bank["balance"] == 8.0 and bank["dollar_balance"] == 400.0
    liability = seed_accounts["2390"].id
    assert _acct_balance(db_session, liability) == (Decimal("200"), Decimal("600"))


# ---------------------------------------------------------------------------
# The portal side
# ---------------------------------------------------------------------------
def _sign_in(client, emp):
    token = client.post(f"/api/employees/{emp['id']}/portal-token").json()[
        "portal_token"
    ]
    r = client.get(f"/portal/{token}")  # claims the link, sets the cookie
    assert r.status_code == 200, r.text


def test_an_employee_withdraws_a_pending_request_from_the_portal(client):
    emp = _emp(client)
    pol = _policy(client)
    _bank(client, emp, pol, 10)
    req = _request(client, emp, 8)

    # no session, no withdraw
    assert client.post(f"/portal/pto/{req['id']}/withdraw").status_code == 401

    _sign_in(client, emp)
    page = client.get("/portal/pto")
    assert f"/portal/pto/{req['id']}/withdraw" in page.text  # the button
    r = client.post(f"/portal/pto/{req['id']}/withdraw")
    assert r.status_code == 200, r.text  # 303, followed to the page
    assert "has been withdrawn" in r.text
    assert "pill-cancelled" in r.text
    assert f"/portal/pto/{req['id']}/withdraw" not in r.text  # button gone
    listed = client.get(f"/api/pto/requests?employee_id={emp['id']}").json()
    assert listed[0]["status"] == "cancelled"
    assert _bank_now(client, emp)["balance"] == 10.0


def test_the_portal_only_withdraws_the_employees_own_pending_request(client):
    emp = _emp(client)
    other = _emp(client, first_name="Sam", last_name="Other")
    pol = _policy(client)
    _bank(client, emp, pol, 10)
    approved = _approve(client, _request(client, emp, 8))
    theirs = _request(client, other, 8)

    _sign_in(client, emp)
    # approved: the employer's to cancel — the page says so, nothing changes
    r = client.post(f"/portal/pto/{approved['id']}/withdraw")
    assert r.status_code == 200, r.text
    assert "still pending can be withdrawn" in r.text
    assert f"/portal/pto/{approved['id']}/withdraw" not in r.text
    listed = client.get(f"/api/pto/requests?employee_id={emp['id']}").json()
    assert listed[0]["status"] == "approved"
    assert _bank_now(client, emp)["balance"] == 2.0
    # someone else's request is not there to withdraw
    assert client.post(f"/portal/pto/{theirs['id']}/withdraw").status_code == 404
    listed = client.get(f"/api/pto/requests?employee_id={other['id']}").json()
    assert listed[0]["status"] == "pending"


def test_the_cancel_question_uses_the_readers_day_and_the_singular():
    """Gate round 2, NEW-65: the date compared was UTC's, so at 11 pm a request
    for today read as past; and "1 hours"."""
    js = (Path(__file__).resolve().parents[1] / "app/static/js/pto.js").read_text(
        encoding="utf-8"
    )
    assert "const passed = r.start_date && r.start_date < todayISO();" in js
    assert "toISOString()" not in js[js.index("async cancelRequest(id)") :]
    assert "Number(back) === 1 ? 'hour goes' : 'hours go'" in js
