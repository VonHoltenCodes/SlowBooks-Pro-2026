"""Estimate Closing Rate follow-ups (#795, after #861): the report can be
saved with its filters, an upgraded lost bid's date is the note's local date
in every time zone, the PDF prints the average days with one decimal, and
"Before sales tax" counts the dollars as the bid amounts."""

import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.models.contacts import Customer
from app.services.estimate_closing_report import COLUMNS, _local_date

ROOT = Path(__file__).resolve().parents[1]
QS = "start_date=2026-01-01&end_date=2026-12-31"


@contextmanager
def _zone(tz):
    """The machine's clock in `tz`; on Windows (no tzset) its own zone."""
    old = os.environ.get("TZ")
    if hasattr(time, "tzset"):
        os.environ["TZ"] = tz
        time.tzset()
    try:
        yield
    finally:
        if hasattr(time, "tzset"):
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()


# ── saved reports ─────────────────────────────────────────────────────────


def test_the_report_saves_with_its_filters(client, seed_customer):
    params = {
        "period": "custom",
        "start_date": "2026-01-01",
        "end_date": "2026-06-30",
        "customer_id": str(seed_customer.id),
        "job_type": "No job type",
        "before_tax": "true",
    }
    r = client.post(
        "/api/saved-reports",
        json={
            "name": "Bids H1",
            "report_type": "estimate_closing_rate",
            "parameters": params,
        },
    )
    assert r.status_code == 201, r.text
    saved = client.get("/api/saved-reports?report_type=estimate_closing_rate")
    assert [s["parameters"] for s in saved.json()] == [params]
    # the saved parameters are the report's own query: it runs on them
    qs = "&".join(f"{k}={v}" for k, v in params.items() if k != "period")
    again = client.get(f"/api/reports/estimate-closing-rate?{qs}")
    assert again.status_code == 200, again.text
    assert again.json()["before_tax"] is True


def test_js_view_saves_and_reopens_with_the_filters():
    from app.routes.saved_reports import _ALLOWED_TYPES

    js = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")
    assert "estimate_closing_rate" in _ALLOWED_TYPES
    assert "reportType: 'estimate_closing_rate', view: 'estimate-closing-rate'" in js
    # the view's address (which a saved report reopens through) keeps them
    assert "keep: ['customer_id', 'job_type', 'before_tax']" in js
    assert "ReportsPage.setParam('before_tax'" in js
    assert "qs.set('before_tax', 'true')" in js


# ── the upgrade's lost dates ──────────────────────────────────────────────


def _cfg(db):
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    return cfg


def _parsed(text):
    when = datetime.fromisoformat(text)
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


@pytest.mark.parametrize("tz", ["America/Chicago", "UTC", "Asia/Tokyo"])
def test_an_upgraded_loss_is_the_notes_local_date(tmp_path, tz):
    from alembic import command
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.services.estimate_closing_report import estimate_closing_report

    db = tmp_path / "lost.db"
    cfg = _cfg(db)
    with _zone(tz):
        command.upgrade(cfg, "a4c8e2f6b1d3")  # the step below #795's
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO customers (id, name, is_active) VALUES (1, 'C', 1)"
            )
            for n, notes in (
                (1, "Lost 2026-02-09: price"),  # upgraded with the fix
                (2, "Lost 2026-02-09: price"),  # upgraded by #861, before it
                (3, "Lost 2026-02-10: scope"),  # marked lost in the app
            ):
                conn.execute(
                    "INSERT INTO estimates (id, estimate_number, customer_id,"
                    " status, date, notes) VALUES (?, ?, 1, 'REJECTED',"
                    " '2026-02-01', ?)",
                    (n, f"E-{n}", notes),
                )
            # #3 is not a "Lost" note the upgrade reads until it is lost
            conn.execute("UPDATE estimates SET notes = 'x' WHERE id = 3")
        command.upgrade(cfg, "a4c8e2f6b0d3")
        with sqlite3.connect(db) as conn:
            first = conn.execute("SELECT decided_at FROM estimates WHERE id = 1")
            # #795's own step already writes the note's local date
            assert _local_date(_parsed(first.fetchone()[0])) == date(2026, 2, 9)
            # what #861's step wrote for #2: midnight UTC on the note's date;
            # #3's own stamp, an evening in the app
            conn.execute(
                "UPDATE estimates SET decided_at = '2026-02-09 00:00:00+00:00'"
                " WHERE id = 2"
            )
            conn.execute(
                "UPDATE estimates SET notes = 'Lost 2026-02-10: scope',"
                " decided_at = '2026-02-10 23:30:00.000000' WHERE id = 3"
            )
        command.upgrade(cfg, "b6e1d4a8c2f5")
        with sqlite3.connect(db) as conn:
            got = dict(conn.execute("SELECT id, decided_at FROM estimates"))
        assert _local_date(_parsed(got[1])) == date(2026, 2, 9)
        assert _local_date(_parsed(got[2])) == date(2026, 2, 9)
        assert got[3] == "2026-02-10 23:30:00.000000"  # not the upgrade's

        engine = create_engine(f"sqlite:///{db}")
        try:
            with Session(engine) as s:
                r = estimate_closing_report(s, date(2026, 1, 1), date(2026, 12, 31))
        finally:
            engine.dispose()
        # #3 counts to its own stamp's local date, whatever the zone
        third = (_local_date(_parsed(got[3])) - date(2026, 2, 1)).days
    assert r["totals"]["lost_count"] == 3
    assert r["totals"]["undated"] == 0
    # 8 days to the note's date for both upgraded losses (7 before the fix
    # west of UTC)
    assert r["totals"]["avg_days"] == round((8 + 8 + third) / 3, 1)

    command.downgrade(cfg, "a4c8e2f6b0d3")  # the correction walks back


# ── the PDF's average days ────────────────────────────────────────────────


def test_the_pdf_prints_avg_days_with_one_decimal():
    from app.routes.reports.construction import _csv_cell, _pdf_cell

    kind = {c["key"]: c["kind"] for c in COLUMNS}["avg_days"]
    assert [_pdf_cell(v, kind) for v in (15.0, 5, 11.7)] == ["15.0", "5.0", "11.7"]
    assert _pdf_cell(None, kind) == ""
    assert _csv_cell(15.0, kind) == 15.0


# ── before sales tax ──────────────────────────────────────────────────────


def _estimate(client, customer_id, rate, tax_rate, status=None):
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": customer_id,
            "date": "2026-03-02",
            "tax_rate": tax_rate,
            "lines": [{"description": "Work", "quantity": 1, "rate": rate}],
        },
    )
    assert r.status_code in (200, 201), r.text
    est = r.json()
    if status == "lost":
        assert client.post(f"/api/estimates/{est['id']}/lost", json={}).is_success
    return est


def test_before_sales_tax_counts_the_bid_amounts(client, db_session):
    taxed = Customer(name="Taxed Co", is_active=True, is_taxable=True)
    exempt = Customer(name="Exempt Org", is_active=True, is_taxable=False)
    db_session.add_all([taxed, exempt])
    db_session.commit()
    a = _estimate(client, taxed.id, 1000, "0.1")
    b = _estimate(client, exempt.id, 400, "0.1", status="lost")
    assert float(a["tax_amount"]) > 0
    assert float(b["tax_amount"]) == 0  # the exempt customer's bid has no tax
    total_a, total_b = float(a["total"]), float(b["total"])
    bid_a = total_a - float(a["tax_amount"])

    # the default is as before: the totals with sales tax
    on = client.get(f"/api/reports/estimate-closing-rate?{QS}").json()
    assert on["before_tax"] is False
    assert on["totals"]["sent_amount"] == total_a + total_b
    assert on["totals"]["open_amount"] == total_a
    assert on["totals"]["lost_amount"] == total_b

    off = client.get(f"/api/reports/estimate-closing-rate?{QS}&before_tax=true")
    t = off.json()["totals"]
    assert off.json()["before_tax"] is True
    assert bid_a == 1000.0
    assert t["sent_amount"] == bid_a + total_b
    assert (t["open_amount"], t["lost_amount"]) == (bid_a, total_b)
    rows = {r["name"]: r for r in off.json()["by_customer"]}
    assert rows["Taxed Co"]["sent_amount"] == bid_a
    assert rows["Exempt Org"]["sent_amount"] == total_b  # unchanged: no tax

    # the CSV heading says so when on, and not when off
    csv_on = client.get(f"/api/reports/estimate-closing-rate/csv?{QS}&before_tax=true")
    assert csv_on.status_code == 200
    assert "(dollars before sales tax)" in csv_on.text
    assert "1000.00" in csv_on.text or "1,000.00" in csv_on.text
    csv_off = client.get(f"/api/reports/estimate-closing-rate/csv?{QS}")
    assert "before sales tax" not in csv_off.text


def test_the_pdf_and_print_take_before_tax(client, monkeypatch):
    """The PDF route reads the option (its heading is the CSV's period line,
    shared); the print page carries the same query string."""
    from app.routes.reports import estimates as route

    seen = {}

    def fake_pdf(sections, db, filename, landscape=False):
        seen["period"] = sections[0]["period"]
        from fastapi.responses import Response

        return Response(b"%PDF", media_type="application/pdf")

    monkeypatch.setattr(route, "_pdf_response", fake_pdf)
    r = client.get(f"/api/reports/estimate-closing-rate/pdf?{QS}&before_tax=true")
    assert r.status_code == 200, r.text
    assert seen["period"].endswith("(dollars before sales tax)")
    client.get(f"/api/reports/estimate-closing-rate/pdf?{QS}")
    assert "before sales tax" not in seen["period"]
