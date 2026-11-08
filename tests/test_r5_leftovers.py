"""Round-5 leftovers: the older construction reports are construction-mode
reports (409 elsewhere) and refuse a filter they do not read (422)."""

import pytest

_GATED = (
    "cost-to-complete",
    "cost-to-complete-forecast",
    "commitment-status",
    "change-order-log",
    "buyout",
)
_SUFFIXES = ("", "/pdf", "/csv", "/print")


def _mode(client, company_type):
    r = client.put("/api/settings", json={"company_type": company_type})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("suffix", _SUFFIXES)
@pytest.mark.parametrize("name", _GATED)
def test_the_older_construction_reports_answer_409_outside_construction_mode(
    client, seed_accounts, name, suffix
):
    _mode(client, "business")
    r = client.get(f"/api/reports/{name}{suffix}?as_of=2026-09-30")
    assert r.status_code == 409, (name, suffix, r.status_code)
    assert "part of construction mode" in r.json()["detail"]


@pytest.mark.parametrize("suffix", ("", "/csv"))
@pytest.mark.parametrize("name", _GATED + ("job-cost-by-vendor",))
def test_the_older_construction_reports_refuse_a_misspelled_filter(
    client, seed_accounts, name, suffix
):
    for mode in ("construction", "business"):
        _mode(client, mode)
        r = client.get(f"/api/reports/{name}{suffix}?jobid=1")
        assert r.status_code == 422, (mode, name, suffix, r.status_code)
        assert "jobid is not a filter here" in r.json()["detail"]


@pytest.mark.parametrize("name", _GATED)
def test_the_declared_filters_still_work_in_construction_mode(
    client, seed_accounts, name
):
    _mode(client, "construction")
    r = client.get(
        f"/api/reports/{name}?as_of=2026-09-30&job_id=9999&include_closed=true"
    )
    assert r.status_code == 200, (name, r.text)


def test_job_cost_by_vendor_stays_open_to_a_business_company(client, seed_accounts):
    _mode(client, "business")
    r = client.get("/api/reports/job-cost-by-vendor?start_date=2026-01-01")
    assert r.status_code == 200, r.text


def test_the_report_center_routes_the_gated_reports_by_mode():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/reports.js").read_text()
    for name in _GATED:
        line = next(ln for ln in js.splitlines() if f"'{name}':" in ln)
        assert "construction: true" in line, name
    line = next(ln for ln in js.splitlines() if "'job-cost-by-vendor':" in ln)
    assert "construction: true" not in line
