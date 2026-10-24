"""First month of the fiscal year (#444).

Covers the pure date helper, the setting's validation, and that the server
defaults, the dashboard's profit card and nonprofit FY-to-date follow the
setting — while a January start reads exactly as the calendar year did.
"""

from datetime import date

import pytest

from app.services.fiscal_year import (
    _fy_bounds,
    _fy_start,
    fiscal_year_label,
    start_month,
)


def _frozen_date(frozen: date):
    """A date subclass whose today() is pinned, for monkeypatching a module's
    `date` (the pattern tests/test_dashboard_overdue_owing.py uses)."""

    class _Frozen(date):
        @classmethod
        def today(cls):
            return cls(frozen.year, frozen.month, frozen.day)

    return _Frozen


# ---------------------------------------------------------------------------
# Pure helper
# ---------------------------------------------------------------------------


def test_fy_start_for_a_july_year():
    assert _fy_start(7, date(2026, 10, 23)) == date(2026, 7, 1)
    # before the start month: the fiscal year that is still running
    assert _fy_start(7, date(2026, 3, 1)) == date(2025, 7, 1)
    # the first day of the start month is the new year
    assert _fy_start(7, date(2026, 7, 1)) == date(2026, 7, 1)


def test_fy_bounds_and_label_for_a_july_year():
    start, end = _fy_bounds(7, date(2026, 10, 23))
    assert start == date(2026, 7, 1)
    assert end == date(2027, 6, 30)
    assert fiscal_year_label(start, end) == "FY2027"


def test_a_january_year_is_the_calendar_year():
    start, end = _fy_bounds(1, date(2026, 10, 23))
    assert start == date(2026, 1, 1)
    assert end == date(2026, 12, 31)
    assert fiscal_year_label(start, end) == "2026"


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------


def test_setting_defaults_to_january(client, db_session):
    assert client.get("/api/settings").json()["fiscal_year_start_month"] == "1"
    assert start_month(db_session) == 1


def test_setting_takes_a_month_and_refuses_anything_else(client):
    r = client.put("/api/settings", json={"fiscal_year_start_month": "7"})
    assert r.status_code == 200, r.text
    assert r.json()["fiscal_year_start_month"] == "7"

    for bad in ("13", "0", "x"):
        r = client.put("/api/settings", json={"fiscal_year_start_month": bad})
        assert r.status_code == 422, bad
        assert "First month of the fiscal year must be a month, 1 to 12." in r.text
    # a refused value leaves the last good one in place
    assert client.get("/api/settings").json()["fiscal_year_start_month"] == "7"


# ---------------------------------------------------------------------------
# Server defaults follow the setting
# ---------------------------------------------------------------------------

TODAY = date(2026, 10, 23)


def test_profit_loss_default_period_follows_a_july_year(client, monkeypatch):
    from app.routes.reports import financial

    monkeypatch.setattr(financial, "date", _frozen_date(TODAY))
    client.put("/api/settings", json={"fiscal_year_start_month": "7"})
    data = client.get("/api/reports/profit-loss").json()
    assert data["start_date"] == "2026-07-01"
    assert data["end_date"] == "2026-10-23"


def test_a_january_year_leaves_the_defaults_unchanged(client, monkeypatch):
    from app.routes.reports import financial

    monkeypatch.setattr(financial, "date", _frozen_date(TODAY))
    # default setting is "1"; three routes still default to January 1
    for path in ("profit-loss", "balance-sheet", "trial-balance"):
        data = client.get(f"/api/reports/{path}").json()
        # as-of reports (balance sheet) carry no start_date
        if "start_date" in data:
            assert data["start_date"] == "2026-01-01", path


def test_possible_duplicates_default_period_follows_a_july_year(
    client, seed_accounts, monkeypatch
):
    """The 2.28.0 Possible Duplicates report shares _range_or_year; asked with
    no dates it opens on the fiscal year to date like the statements."""
    from app.routes.reports import financial

    monkeypatch.setattr(financial, "date", _frozen_date(TODAY))
    client.put("/api/settings", json={"fiscal_year_start_month": "7"})
    r = client.get(
        f"/api/reports/possible-duplicates?account_id={seed_accounts['1000'].id}"
    )
    assert r.status_code == 200, r.text
    assert r.json()["start_date"] == "2026-07-01"
    assert r.json()["end_date"] == "2026-10-23"


def test_nonprofit_default_period_follows_the_setting(client, db_session, monkeypatch):
    from app.services import nonprofit

    monkeypatch.setattr(nonprofit, "date", _frozen_date(TODAY))
    client.put("/api/settings", json={"fiscal_year_start_month": "7"})
    start, end = nonprofit.default_period(db_session)
    assert start == date(2026, 7, 1)
    assert end == TODAY


def test_dashboard_pnl_ytd_starts_at_the_fiscal_year(client, db_session, monkeypatch):
    from app.services import dashboard_widgets

    monkeypatch.setattr(dashboard_widgets, "date", _frozen_date(TODAY))
    client.put("/api/settings", json={"fiscal_year_start_month": "7"})
    data = dashboard_widgets.pnl_ytd(db_session)
    assert data["year"] == "FY2027"
    # the month strip starts in July and runs to the current month
    assert data["months"][0]["month"] == "Jul"
    assert data["months"][-1]["month"] == "Oct"


def test_dashboard_pnl_ytd_january_reads_as_the_year(client, db_session, monkeypatch):
    from app.services import dashboard_widgets

    monkeypatch.setattr(dashboard_widgets, "date", _frozen_date(TODAY))
    data = dashboard_widgets.pnl_ytd(db_session)  # default January start
    assert data["year"] == "2026"
    assert data["months"][0]["month"] == "Jan"


# ---------------------------------------------------------------------------
# The JS mirror: the four presets exist, are gated on the setting, and the
# client helper mirrors the Python one.
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

REPORTS_JS = Path(__file__).resolve().parent.parent / "app/static/js/reports.js"
SETTINGS_JS = Path(__file__).resolve().parent.parent / "app/static/js/settings.js"


def test_reports_js_has_the_four_fiscal_presets_gated_on_the_setting():
    src = REPORTS_JS.read_text(encoding="utf-8")
    for key in (
        "this_fiscal_year",
        "this_fiscal_year_to_date",
        "last_fiscal_year",
        "last_fiscal_year_to_date",
    ):
        assert f'"{key}"' in src, key
    # the gate: only when a non-January fiscal year is set
    assert "fiscal_year_start_month" in src
    assert 'String(fym) !== "1"' in src
    # the client mirror of the Python helper
    assert "_fiscalYearStart(today)" in src


def test_settings_js_renders_the_month_select():
    src = SETTINGS_JS.read_text(encoding="utf-8")
    assert 'name="fiscal_year_start_month"' in src
    assert "First month of fiscal year" in src


@pytest.mark.parametrize("month", [1, 7, 10])
def test_fy_start_is_always_on_or_before_the_date(month):
    on = date(2026, 5, 15)
    start = _fy_start(month, on)
    assert start <= on
    assert start.month == month
    assert start.day == 1


def test_analytics_year_preset_follows_the_fiscal_year(client, db_session):
    """The Analytics page's "year" period is the company's fiscal year (#444)."""
    from datetime import date

    from app.services.settings_service import set_setting

    set_setting(db_session, "fiscal_year_start_month", "7")
    db_session.commit()
    r = client.get("/api/analytics/dashboard?period=year")
    assert r.status_code == 200, r.text
    today = date.today()
    expected = date(today.year if today.month >= 7 else today.year - 1, 7, 1)
    body = r.json()
    period = body.get("period") or body
    assert str(expected) in (
        period.get("start") or period.get("start_date") or ""
    ), body.get("period")
