"""Time tracking end to end (2.17.3 exploratory test, skytech W-H8).

Every entry listed 0.00 hours (the page read field names the API does not
return), saving landed on "Page not found", Approve and Reject were offered
only for a 'pending' status no entry ever has, and Clock In / Out / Break were
collected and thrown away. A pay run with "Use approved time entries" then
paid an hourly employee $0.00 for 34 logged hours without a word.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.models.payroll import PayRun, PayStub

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"
PERIOD = {
    "period_start": "2026-09-13",
    "period_end": "2026-09-26",
    "pay_date": "2026-10-01",
}


def _hana(client):
    r = client.post(
        "/api/employees",
        json={
            "first_name": "Hana",
            "last_name": "Lee",
            "pay_type": "hourly",
            "pay_rate": 22,
            "work_state": "IL",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _log(client, emp_id, day, hours, approve=False):
    r = client.post(
        "/api/time-entries",
        json={"employee_id": emp_id, "date": day, "hours_regular": hours},
    )
    assert r.status_code == 201, r.text
    entry = r.json()
    assert entry["status"] == "draft"
    if approve:
        r = client.post(f"/api/time-entries/{entry['id']}/approve", json={})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "approved"
    return entry


def _run(client, emp_id, **stub):
    return client.post(
        "/api/payroll",
        json={**PERIOD, "stubs": [{"employee_id": emp_id, **stub}]},
    )


def test_unapproved_time_is_named_not_paid_as_zero(client, db_session, seed_accounts):
    hana = _hana(client)
    _log(client, hana["id"], "2026-09-14", 8)
    _log(client, hana["id"], "2026-09-15", 26)
    r = _run(client, hana["id"], use_time_entries=True)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "Hana Lee has no approved time" in detail
    assert "2 time entries (34.00 hours) waiting for approval" in detail
    assert db_session.query(PayRun).count() == 0
    assert db_session.query(PayStub).count() == 0


def test_approved_draft_time_is_paid_and_the_rest_is_named(client, seed_accounts):
    hana = _hana(client)
    _log(client, hana["id"], "2026-09-14", 8, approve=True)
    _log(client, hana["id"], "2026-09-15", 2)
    r = _run(client, hana["id"], use_time_entries=True)
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["stubs"][0]["gross_pay"] == 176.0  # 8 approved hours x $22
    assert run["warnings"] == [
        "Hana Lee: 1 time entry (2.00 hours) from 2026-09-13 to 2026-09-26 is "
        "not approved and not paid in this run. Approve them under Time "
        "Entries to pay them in a later run."
    ]


def test_a_stub_that_pays_nothing_is_refused(client, db_session, seed_accounts):
    hana = _hana(client)
    r = _run(client, hana["id"], hours=0)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == (
        "Hana Lee would be paid $0.00. Enter hours or an amount, or leave "
        "Hana Lee out of this run."
    )
    assert db_session.query(PayRun).count() == 0


def test_the_summary_counts_time_waiting_for_approval(client, seed_accounts):
    hana = _hana(client)
    _log(client, hana["id"], "2026-09-14", 8, approve=True)
    _log(client, hana["id"], "2026-09-15", 2.5)
    rows = client.get(
        "/api/time-entries/summary?period_start=2026-09-13&period_end=2026-09-26"
    ).json()
    assert len(rows) == 1
    assert rows[0]["total"] == 8.0 and rows[0]["entry_count"] == 1
    assert rows[0]["pending_count"] == 1 and rows[0]["pending_hours"] == 2.5


def _js(name):
    return (JS / name).read_text(encoding="utf-8")


def test_the_list_reads_the_fields_the_api_returns():
    js = _js("time_entries.js")
    listing = js[js.index("async render()") : js.index("async showForm()")]
    for field in ("hours_regular", "hours_overtime", "hours_doubletime", "notes"):
        assert f"en.{field}" in listing, field
    for stale in ("en.regular_hours", "en.overtime_hours", "en.description"):
        assert stale not in listing, stale


def test_every_action_returns_to_the_time_entries_page():
    js = _js("time_entries.js")
    assert "'#/time-entries'" not in js
    assert js.count("App.navigate('#/hr/time-entries')") == 5
    assert "'/hr/time-entries'" in _js("app.js")


def test_a_new_entry_can_be_approved_or_rejected():
    js = _js("time_entries.js")
    assert "'pending'" not in js
    assert (
        "const undecided = (en.status === 'draft' || en.status === 'submitted')"
        " && !en.pay_run_id;" in js
    )


def test_the_clock_times_feed_the_hours():
    js = _js("time_entries.js")
    form = js[js.index("async showForm()") : js.index("async approve(")]
    for name in ("clock_in", "clock_out", "break_minutes"):
        field = form[form.index(f'name="{name}"') :]
        field = field[: field.index(">")]
        assert 'oninput="TimeEntriesPage._hoursFromClock(this.form)"' in field, name


def test_the_pay_run_form_warns_about_unapproved_time():
    js = _js("payroll.js")
    assert "row.pending_count" in js
    assert "run.warnings" in js
    assert "$('#pr-error')" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_shows_hours_and_works_out_clock_times():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "time_entries_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert "draft row hours: 8.00 2.00 0.00" in out.stdout
    assert "draft row notes: true" in out.stdout
    assert "draft row approve: true" in out.stdout
    assert "approved row approve: false" in out.stdout
    assert "clock 08:00-16:30 less 30: 8.00" in out.stdout
    assert "clock 22:00-06:00: 8.00" in out.stdout
    assert "clock without an out time: 0" in out.stdout
