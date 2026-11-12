"""Estimate Closing Rate (#795): won, lost and open by count and dollars, the
closing rate as won ÷ decided, days from sent to decision, and the
decided_at stamp every status change sets."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.models.contacts import Customer
from app.models.email_log import EmailLog
from app.models.estimates import Estimate, EstimateStatus
from app.models.jobs import Job
from app.services.estimate_closing_report import estimate_closing_report

D = date(2026, 3, 2)


def _utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc)


def _est(db, n, customer, total, status, job=None, decided=None, **kw):
    e = Estimate(
        estimate_number=f"E-{n}",
        customer_id=customer.id,
        date=kw.pop("on", D),
        total=Decimal(total),
        status=status,
        job_id=job.id if job else None,
        **kw,
    )
    db.add(e)
    db.flush()
    # the status set stamped today; the test dates the decision itself
    e.decided_at = _utc(decided) if decided else None
    return e


def test_the_report_counts_won_lost_and_open_and_rates_the_decided(db_session):
    db = db_session
    acme = Customer(name="Acme", is_active=True)
    bolt = Customer(name="Bolt", is_active=True)
    db.add_all([acme, bolt])
    db.flush()
    roof = Job(customer_id=acme.id, name="Roof", job_type="Roofing")
    db.add(roof)
    db.flush()

    won = _est(db, 1, acme, "1000", EstimateStatus.ACCEPTED, roof, D + timedelta(10))
    _est(db, 2, acme, "3000", EstimateStatus.CONVERTED, roof, D + timedelta(20))
    _est(db, 3, bolt, "2000", EstimateStatus.REJECTED)  # decided, no date
    _est(db, 4, bolt, "500", EstimateStatus.PENDING, expiration_date=D)
    _est(db, 5, bolt, "9999", EstimateStatus.ACCEPTED, on=date(2025, 1, 5))
    # emailed four days after its date: the days count from the send
    db.add(
        EmailLog(
            entity_type="estimate",
            entity_id=won.id,
            recipient="a@x.test",
            status="sent",
            sent_at=_utc(D + timedelta(4)),
        )
    )
    db.commit()

    r = estimate_closing_report(db, date(2026, 1, 1), date(2026, 12, 31))
    t = r["totals"]
    assert (t["sent_count"], t["sent_amount"]) == (4, 6500.0)
    assert (t["won_count"], t["won_amount"]) == (2, 4000.0)
    assert (t["lost_count"], t["lost_amount"]) == (1, 2000.0)
    assert (t["open_count"], t["open_amount"], t["expired_count"]) == (1, 500.0, 1)
    assert t["closing_rate"] == 66.7  # 2 of 3 decided; the open bid is neither
    assert t["closing_rate_amount"] == 66.7  # 4000 of 6000
    assert t["avg_days"] == 13.0  # (6 + 20) / 2; the undated loss is left out
    assert t["undated"] == 1

    assert [c["name"] for c in r["by_customer"]] == ["Acme", "Bolt"]
    assert r["by_customer"][1]["closing_rate"] == 0.0
    assert [(j["name"], j["sent_count"]) for j in r["by_job_type"]] == [
        ("Roofing", 2),
        ("No job type", 2),
    ]

    only = estimate_closing_report(
        db, date(2026, 1, 1), date(2026, 12, 31), job_type="No job type"
    )
    assert only["totals"]["sent_count"] == 2
    assert only["totals"]["closing_rate"] == 0.0
    none = estimate_closing_report(db, date(2024, 1, 1), date(2024, 12, 31))
    assert none["totals"]["closing_rate"] is None  # nothing decided: no rate


def test_every_status_change_dates_the_decision(db_session, seed_customer):
    e = Estimate(estimate_number="E-9", customer_id=seed_customer.id, date=D, total=1)
    db_session.add(e)
    db_session.flush()
    assert e.decided_at is None
    e.status = EstimateStatus.ACCEPTED
    first = e.decided_at
    assert first is not None
    e.status = EstimateStatus.CONVERTED  # the same win keeps its date
    assert e.decided_at == first
    e.decided_at = _utc(D)
    e.status = EstimateStatus.REJECTED  # won to lost is a new decision
    assert e.decided_at != _utc(D)
    e.status = EstimateStatus.PENDING
    assert e.decided_at is None


def test_the_routes_answer_json_and_csv(client, seed_customer):
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": D.isoformat(),
            "lines": [{"description": "Work", "quantity": 1, "rate": 800}],
        },
    )
    assert est.status_code in (200, 201), est.text
    lost = client.post(f"/api/estimates/{est.json()['id']}/lost", json={})
    assert lost.json()["decided_at"] is not None

    qs = "start_date=2026-01-01&end_date=2026-12-31"
    data = client.get(f"/api/reports/estimate-closing-rate?{qs}").json()
    assert data["totals"]["lost_count"] == 1
    assert data["totals"]["avg_days"] is not None
    csv = client.get(f"/api/reports/estimate-closing-rate/csv?{qs}")
    assert csv.status_code == 200
    assert "Closing rate" in csv.text and "Test Customer" in csv.text
    bad = client.get(f"/api/reports/estimate-closing-rate?{qs}&jobtype=x")
    assert bad.status_code == 422
    huge = client.get("/api/reports/estimate-closing-rate?customer_id=" + "9" * 24)
    assert huge.status_code == 422  # not a database overflow
    back = client.get(
        "/api/reports/estimate-closing-rate?start_date=2026-12-31&end_date=2026-01-01"
    )
    assert back.status_code == 400
    assert back.json()["detail"] == "The start date is after the end date."

    # the exports carry the expired column and speak the company's words
    client.put("/api/settings", json={"company_type": "nonprofit"})
    csv = client.get(f"/api/reports/estimate-closing-rate/csv?{qs}").text
    assert "By donor" in csv and "No grant type" in csv
    assert "Expired (open)" in csv


def test_days_count_on_the_local_calendar(db_session, seed_customer):
    import os
    import time

    # an evening decision in New York is already the next day in UTC; on
    # Windows (no tzset) it runs on the machine's own zone
    old_tz = os.environ.get("TZ")
    if hasattr(time, "tzset"):
        os.environ["TZ"] = "America/New_York"
        time.tzset()
    try:
        e = _est(db_session, 7, seed_customer, "100", EstimateStatus.ACCEPTED)
        local_evening = datetime(D.year, D.month, D.day, 20).astimezone()
        e.decided_at = local_evening.astimezone(timezone.utc)
        db_session.commit()
        assert estimate_closing_report(db_session, D, D)["totals"]["avg_days"] == 0.0
    finally:
        if hasattr(time, "tzset"):
            if old_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_tz
            time.tzset()


def test_the_migration_fills_decided_at_from_what_the_estimate_shows(tmp_path):
    import sqlite3
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    db = tmp_path / "est.db"
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()

    command.upgrade(cfg, "f3b9d2e6a1c7")
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO customers (id, name, is_active) VALUES (1, 'C', 1)")
        for n, status, accepted, notes in (
            (1, "ACCEPTED", "2026-02-03 10:00:00", None),
            # lost, reopened, lost again: the last loss is the decision
            (2, "REJECTED", None, "Lost 2026-01-20: old\nLost 2026-02-09: price"),
            (3, "REJECTED", None, "no date here"),
        ):
            conn.execute(
                "INSERT INTO estimates (id, estimate_number, customer_id, status,"
                " date, accepted_at, notes) VALUES (?, ?, 1, ?, '2026-02-01', ?, ?)",
                (n, f"E-{n}", status, accepted, notes),
            )
    command.upgrade(cfg, "a4c8e2f6b0d3")  # its own step, not the chain (#770)
    with sqlite3.connect(db) as conn:
        got = dict(conn.execute("SELECT id, decided_at FROM estimates"))
    assert got[1].startswith("2026-02-03")
    assert got[2].startswith("2026-02-09")
    assert got[3] is None
    command.downgrade(cfg, "f3b9d2e6a1c7")
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(estimates)")}
    assert "decided_at" not in cols
