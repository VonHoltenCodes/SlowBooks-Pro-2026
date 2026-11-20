"""An estimate made in error can be voided (#1128).

An estimate could only be edited or marked lost, and a lost one counts
against the Estimate Closing Rate (#795), so a duplicate, a wrong-customer
or a test estimate skewed the report for good. Void keeps the estimate and
its number, records who voided it, when and why, takes it out of the
closing rate on both sides, out of the job's document count and the IIF
export, and refuses every later change. An estimate something is built on
(the invoice it became, the job budget its acceptance locked) is refused.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.estimates import Estimate, EstimateStatus
from app.models.users import User
from app.services import auth as auth_service
from app.services.estimate_closing_report import estimate_closing_report

TODAY = date.today().isoformat()
USER_PW = "long-enough-pw-1128"


def _estimate(client, customer_id, rate=100, **extra):
    body = {
        "customer_id": customer_id,
        "date": TODAY,
        "tax_rate": 0,
        "lines": [{"description": "Deck boards", "quantity": 1, "rate": rate}],
        **extra,
    }
    r = client.post("/api/estimates", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _void(client, est_id, **body):
    return client.post(f"/api/estimates/{est_id}/void", json=body or None)


def _message(r):
    detail = r.json()["detail"]
    return detail["message"] if isinstance(detail, dict) else detail


def test_void_keeps_the_estimate_and_its_number_and_says_who_when_and_why(
    client, seed_customer
):
    est = _estimate(client, seed_customer.id)
    r = _void(client, est["id"], reason="Entered twice")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "void"
    assert body["estimate_number"] == est["estimate_number"]
    assert body["voided_by"]
    assert body["voided_at"]
    assert body["void_reason"] == "Entered twice"
    assert Decimal(body["total"]) == Decimal("100.00")

    # the list shows it, and status=void picks it out
    listed = client.get("/api/estimates").json()
    assert [e["status"] for e in listed if e["id"] == est["id"]] == ["void"]
    only = client.get("/api/estimates?status=void").json()
    assert [e["id"] for e in only] == [est["id"]]


def test_voiding_again_changes_nothing(client, seed_customer):
    est = _estimate(client, seed_customer.id)
    first = _void(client, est["id"], reason="Test").json()
    again = _void(client, est["id"], reason="Something else")
    assert again.status_code == 200, again.text
    assert again.json()["voided_at"] == first["voided_at"]
    assert again.json()["void_reason"] == "Test"


def test_a_lost_estimate_can_be_voided_and_the_reason_is_optional(
    client, seed_customer
):
    est = _estimate(client, seed_customer.id)
    assert client.post(f"/api/estimates/{est['id']}/lost", json={}).status_code == 200
    r = _void(client, est["id"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"
    assert r.json()["void_reason"] is None


def test_a_void_estimate_is_never_changed_again(client, seed_customer):
    est = _estimate(client, seed_customer.id)
    _void(client, est["id"])
    tries = {
        "edited": client.put(f"/api/estimates/{est['id']}", json={"notes": "x"}),
        "accepted": client.post(f"/api/estimates/{est['id']}/accept", json={}),
        "marked lost": client.post(f"/api/estimates/{est['id']}/lost", json={}),
        "converted": client.post(f"/api/estimates/{est['id']}/convert"),
        "emailed": client.post(
            f"/api/estimates/{est['id']}/email", json={"recipient": "a@example.com"}
        ),
    }
    for doing, r in tries.items():
        assert r.status_code == 409, (doing, r.text)
        assert r.json()["detail"]["code"] == "voided_estimate"
        assert _message(r).endswith(f"a void estimate can't be {doing}."), _message(r)


def test_an_edit_cannot_set_the_status_to_void(client, seed_customer):
    est = _estimate(client, seed_customer.id)
    r = client.put(f"/api/estimates/{est['id']}", json={"status": "void"})
    assert r.status_code == 409, r.text
    assert "Void" in _message(r)
    assert client.get(f"/api/estimates/{est['id']}").json()["status"] == "pending"


def test_a_converted_estimate_cannot_be_voided(client, seed_accounts, seed_customer):
    est = _estimate(client, seed_customer.id)
    inv = client.post(f"/api/estimates/{est['id']}/convert")
    assert inv.status_code == 200, inv.text
    r = _void(client, est["id"])
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "estimate_in_use"
    msg = _message(r)
    assert inv.json()["invoice_number"] in msg and "void the invoice" in msg, msg
    assert client.get(f"/api/estimates/{est['id']}").json()["status"] == "converted"


def test_an_estimate_whose_acceptance_set_a_job_budget_cannot_be_voided(
    client, seed_customer
):
    job = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Back deck"}
    )
    assert job.status_code == 201, job.text
    est = _estimate(client, seed_customer.id, job_id=job.json()["id"])
    r = client.post(f"/api/estimates/{est['id']}/accept", json={})
    assert r.status_code == 200, r.text
    r = _void(client, est["id"])
    assert r.status_code == 409, r.text
    assert "Back deck" in _message(r) and "budget" in _message(r), _message(r)


def test_an_accepted_estimate_nothing_is_built_on_can_be_voided(client, seed_customer):
    # a plain status change, no job: no budget, no contract
    est = _estimate(client, seed_customer.id)
    r = client.put(f"/api/estimates/{est['id']}", json={"status": "accepted"})
    assert r.status_code == 200, r.text
    assert _void(client, est["id"]).status_code == 200


def test_a_void_estimate_counts_neither_won_nor_lost_and_mark_lost_still_counts(
    client, db_session, seed_customer
):
    won = _estimate(client, seed_customer.id, rate=1000)
    lost = _estimate(client, seed_customer.id, rate=400)
    lost_then_void = _estimate(client, seed_customer.id, rate=700)
    open_then_void = _estimate(client, seed_customer.id, rate=900)
    won_then_void = _estimate(client, seed_customer.id, rate=300)
    for est in (won, won_then_void):
        r = client.put(f"/api/estimates/{est['id']}", json={"status": "accepted"})
        assert r.status_code == 200, r.text
    for est in (lost, lost_then_void):
        r = client.post(f"/api/estimates/{est['id']}/lost", json={"reason": "Price"})
        assert r.status_code == 200, r.text
    before = estimate_closing_report(db_session)["totals"]
    for est in (lost_then_void, open_then_void, won_then_void):
        assert _void(client, est["id"]).status_code == 200

    db_session.expire_all()
    totals = estimate_closing_report(db_session)["totals"]
    assert (totals["won_count"], totals["lost_count"], totals["open_count"]) == (
        1,
        1,
        0,
    )
    assert totals["sent_count"] == 2
    assert Decimal(str(totals["won_amount"])) == Decimal("1000")
    assert Decimal(str(totals["lost_amount"])) == Decimal("400")
    # before the voids each counted where its status put it
    assert (before["won_count"], before["lost_count"], before["open_count"]) == (
        2,
        2,
        1,
    )
    # and the route says the same
    r = client.get("/api/reports/estimate-closing-rate")
    assert r.status_code == 200, r.text
    assert r.json()["totals"]["sent_count"] == 2


def test_the_pdf_face_says_void(client, seed_customer):
    est = _estimate(client, seed_customer.id)
    live = client.get(f"/api/estimates/{est['id']}/print-preview").text
    assert 'class="void"' not in live
    _void(client, est["id"])
    page = client.get(f"/api/estimates/{est['id']}/print-preview").text
    assert '<span class="void">VOID</span>' in page


def test_a_void_estimate_leaves_the_job_count_and_the_iif_export(
    client, db_session, seed_customer
):
    from app.services.jobs_service import documents_carrying_job
    from app.services.terminology import terms_from_db

    job = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Porch"}
    ).json()
    est = _estimate(client, seed_customer.id, job_id=job["id"])

    def counted():
        return dict(
            documents_carrying_job(db_session, job["id"], terms_from_db(db_session))
        )

    assert counted().get("estimate") == 1
    exported = client.get("/api/iif/export/estimates").text
    assert est["estimate_number"] in exported
    _void(client, est["id"])
    db_session.expire_all()
    assert counted().get("estimate", 0) == 0
    assert est["estimate_number"] not in client.get("/api/iif/export/estimates").text


def test_the_activity_panel_says_who_voided_it(client, seed_customer):
    est = _estimate(client, seed_customer.id)
    _void(client, est["id"])
    r = client.get(f"/api/activity?type=estimate&id={est['id']}&tz_offset=0")
    assert r.status_code == 200, r.text
    assert "Voided by" in str(r.json())


@pytest.mark.parametrize("role", ["readonly", "accountant"])
def test_a_read_only_or_accountant_sign_in_is_refused_the_void(
    client, db_session, seed_customer, role
):
    est = _estimate(client, seed_customer.id)
    db_session.add(
        User(
            username=f"{role}-1128",
            display_name=role.title(),
            password_hash=auth_service.hash_password(USER_PW),
            role=role,
            is_active=True,
        )
    )
    db_session.commit()
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": f"{role}-1128", "password": USER_PW}
    )
    assert r.status_code == 200, r.text
    r = _void(client, est["id"])
    assert r.status_code == 403, r.text
    db_session.expire_all()
    assert db_session.get(Estimate, est["id"]).status == EstimateStatus.PENDING


def test_the_migration_adds_void_and_walks_back(tmp_path):
    import sqlite3
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    db = tmp_path / "void.db"
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["c1128e5a7d0f"]
    assert script.get_revision("c1128e5a7d0f").down_revision == "c1064a5e7b2d"
    command.upgrade(cfg, "c1064a5e7b2d")
    command.upgrade(cfg, "c1128e5a7d0f")  # its own step (#770)
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(estimates)")}
        assert {"voided_at", "voided_by", "void_reason"} <= cols
        conn.execute(
            "INSERT INTO customers (id, name, is_active) VALUES (1, 'Harbor', 1)"
        )
        conn.execute(
            "INSERT INTO estimates (id, estimate_number, customer_id, status, date)"
            " VALUES (1, 'E-1', 1, 'VOID', '2026-11-19')"
        )
    command.downgrade(cfg, "c1064a5e7b2d")
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(estimates)")}
        assert not {"voided_at", "voided_by", "void_reason"} & cols
        assert conn.execute("SELECT status FROM estimates").fetchone() == ("REJECTED",)
