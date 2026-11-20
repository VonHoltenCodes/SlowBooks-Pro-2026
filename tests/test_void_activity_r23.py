"""Round 23 (W-202, W-203): an estimate's void in words.

W-202: the API's void with a reason over 500 characters answered the
validator's own "String should have at most 500 characters"; it is a
sentence now, in the words a To review memo's refusal uses (W-191).

W-203: a void read twice in Activity: "Voided by …" and an "Edited by …"
whose details were the raw fields ("Voided at set to 2026-11-20T04:31:17…",
"Voided by set to admin"). One entry now, with the reason in words. The
construction documents that keep a voided_at (pay applications, change
orders) had the same double entry.
"""

import re
from datetime import timedelta

from app.models.audit import AuditLog
from tests.test_co_effective_date_allowance import _approve as _approve_co
from tests.test_co_effective_date_allowance import _owner_co, _setup
from tests.test_estimate_void_1128 import _estimate, _void
from tests.test_pay_applications import (
    _approve as _approve_app,
    _edit,
    _new_app,
    _two_line_job,
)

ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
RAW = ("Voided at", "Voided by set", "Void reason")


def _age(db, minutes=1):
    for row in db.query(AuditLog).all():
        if row.timestamp is not None:
            row.timestamp = row.timestamp - timedelta(minutes=minutes)
    db.commit()


def _events(client, type_, id_):
    r = client.get(f"/api/activity?type={type_}&id={id_}&tz_offset=0")
    assert r.status_code == 200, r.text
    return r.json()["events"]


def _no_raw_fields(events):
    for e in events:
        for said in [e["text"], *e["details"]]:
            assert not ISO.search(said), said
            assert not any(word in said for word in RAW), said


# -- W-202 ----------------------------------------------------------------------


def test_a_void_reason_over_500_characters_is_refused_in_a_sentence(
    client, seed_customer
):
    est = _estimate(client, seed_customer.id)
    r = _void(client, est["id"], reason="x" * 501)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == (
        "A void reason can be at most 500 characters; this one has 501."
    )
    assert client.get(f"/api/estimates/{est['id']}").json()["status"] != "void"
    # 500 is taken, and the spaces round it are not counted
    r = _void(client, est["id"], reason="  " + "y" * 500 + "  ")
    assert r.status_code == 200, r.text
    assert r.json()["void_reason"] == "y" * 500


# -- W-203 ----------------------------------------------------------------------


def test_an_estimates_void_is_one_entry_with_its_reason(
    client, seed_customer, db_session
):
    est = _estimate(client, seed_customer.id)
    _age(db_session)
    assert _void(client, est["id"], reason="Entered twice").status_code == 200
    events = _events(client, "estimate", est["id"])
    kinds = [e["kind"] for e in events]
    assert kinds == ["voided", "created"], events
    voided = events[0]
    assert voided["text"] == f"Voided by {voided['who']}: Entered twice."
    assert voided["details"] == []
    _no_raw_fields(events)


def test_a_void_with_no_reason_and_in_the_same_second_as_the_creation(
    client, seed_customer
):
    est = _estimate(client, seed_customer.id)
    assert _void(client, est["id"]).status_code == 200
    events = _events(client, "estimate", est["id"])
    assert sorted(e["kind"] for e in events) == ["created", "voided"], events
    voided = next(e for e in events if e["kind"] == "voided")
    assert voided["text"] == f"Voided by {voided['who']}."
    _no_raw_fields(events)


def test_a_pay_applications_void_is_one_entry(
    client, seed_accounts, seed_customer, db_session
):
    job, sov = _two_line_job(client, seed_customer.id)
    app = _new_app(client, job["id"])
    _edit(
        client,
        app["id"],
        lines=[{"sov_line_id": sov[0]["id"], "this_period": 30000, "stored": 0}],
    )
    assert _approve_app(client, app["id"]).status_code == 200
    _age(db_session)
    assert client.post(f"/api/pay-applications/{app['id']}/void").status_code == 200
    events = _events(client, "pay_application", app["id"])
    newest = [e for e in events if e["kind"] in ("voided", "edited")][:2]
    assert newest[0]["kind"] == "voided", events
    assert not any(
        e["kind"] == "edited" and e["when"] == newest[0]["when"] for e in events
    ), events
    _no_raw_fields(events)


def test_a_change_orders_void_is_one_entry(client, seed_customer, db_session):
    job, _sov = _setup(client, seed_customer.id, [], 100000)
    co = _owner_co(client, job["id"], 1000)
    assert _approve_co(client, co["id"]).status_code == 200
    _age(db_session)
    assert client.post(f"/api/change-orders/{co['id']}/void").status_code == 200
    events = _events(client, "change_order", co["id"])
    assert events[0]["kind"] == "voided", events
    assert not any(
        e["kind"] == "edited" and e["when"] == events[0]["when"] for e in events
    ), events
    _no_raw_fields(events)
