"""Skeptic round 2 on #731, the reports workstream (S1).

A back-dated change order approval names the posted WIP months it moves,
and the schedule says when a posted entry no longer matches it; every job
report and list refuses a filter it does not read; Budget vs Actual, its
exports and the cost tree project like the job's Overview; markup revenue
sits on the sections the budget spread it over (a deduct alternate takes
its share); the Change Order Log and the WIP read a change order in the
state it had on their date; as-of reports read the contract as of then.
"""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.change_orders import ChangeOrder
from app.models.jobs import Job
from app.models.job_costing import JobBudget
from app.services import job_reports
from app.services.job_costing import budget_vs_actual_all_jobs, job_cost_tree

from tests.test_sov_mapping import _accepted_build, _section
from tests.test_wip_as_of_r1 import _over_billed_job, _setup, _wip_row
from tests.test_wip_schedule_post import _budget, _cost, _job
from tests.test_wip_schedule_post import _section as _db_section

AUG31 = date(2026, 8, 31)
SEP30 = date(2026, 9, 30)


def _co(client, job_id, unit_cost, when=None):
    """A pending owner change order (contract-funded, material, no markup)."""
    body = {
        "job_id": job_id,
        "kind": "owner",
        "funding": "contract",
        "lines": [
            {
                "description": "Added scope",
                "cost_type": "material",
                "quantity": 1,
                "unit_cost": unit_cost,
                "markup_pct": 0,
            }
        ],
    }
    if when is not None:
        body["date"] = when.isoformat()
    r = client.post("/api/change-orders", json=body)
    assert r.status_code == 201, r.text
    co = r.json()
    assert client.post(f"/api/change-orders/{co['id']}/submit").status_code == 200
    return co


def _approve(client, co_id, on=None):
    body = {"approved_on": on.isoformat()} if on else {}
    r = client.post(f"/api/change-orders/{co_id}/approve", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _post(client, as_of):
    r = client.post(f"/api/reports/wip-schedule/post?as_of={as_of}")
    assert r.status_code == 200, r.text
    return r.json()


# ── 1. a back-dated approval and the posted WIP months ──────────────────


def test_a_back_dated_approval_names_the_posted_wip_months_it_moves(
    client, db_session, seed_accounts
):
    """L bug 2 / M weakness 3: post 8/31 and 9/30, then approve a CO dated
    8/10 (+2,000 cost): the approval says which posted months it changes."""
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    _post(client, AUG31)
    _post(client, SEP30)

    co = _co(client, job.id, 2000, when=date(2026, 8, 10))
    out = _approve(client, co["id"], date(2026, 8, 10))
    assert out["warnings"] == [
        f"{co['number']} takes effect on 2026-08-10, so it changes the WIP "
        "entries posted for 2026-08-31 and 2026-09-30; void and re-post them "
        "on the WIP schedule."
    ]


def test_an_approval_after_the_posted_months_says_nothing_of_them(
    client, db_session, seed_accounts
):
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    _post(client, AUG31)
    _post(client, SEP30)
    # a voided month is not on the books: only 9/30 is named
    assert (
        client.post(f"/api/reports/wip-schedule/void?as_of={AUG31}").status_code == 200
    )
    co = _co(client, job.id, 2000, when=date(2026, 8, 10))
    out = _approve(client, co["id"], date(2026, 8, 20))
    assert "posted for 2026-09-30; void and re-post it" in out["warnings"][0]
    assert "2026-08-31" not in out["warnings"][0]

    later = _co(client, job.id, 500, when=date(2026, 10, 1))
    assert _approve(client, later["id"], date(2026, 10, 1))["warnings"] == []


def test_the_schedule_shows_the_posted_entry_and_says_when_it_differs(
    client, db_session, seed_accounts
):
    """After the back-dated CO the 8/31 schedule reads 10,243.90 over-billed
    while 10,000 is posted: the entry shown is the posted one, with the
    schedule's own figures and a sentence beside it."""
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    _post(client, AUG31)
    data = job_reports.wip_schedule(db_session, AUG31)
    assert data["adjusting_entry"]["posted"] is True
    assert data["adjusting_entry"]["posted_differs"] is False
    assert not any("WIP entry posted" in w for w in data["warnings"])

    co = _co(client, job.id, 2000, when=date(2026, 8, 10))
    _approve(client, co["id"], date(2026, 8, 10))
    db_session.expire_all()

    row, data = _wip_row(db_session, job.id, AUG31)
    entry = data["adjusting_entry"]
    assert row["over_billing"] == 10243.9  # 60,000 − 102,000 × 40/82
    assert entry["posted"] is True and entry["posted_differs"] is True
    # the lines shown are what was posted
    assert entry["totals"]["over_billing"] == 10000.0
    assert entry["jobs"][0]["over_billing"] == 10000.0
    assert entry["total_debits"] == 10000.0
    assert entry["computed"]["totals"]["over_billing"] == 10243.9
    sentence = next(w for w in data["warnings"] if "WIP entry posted" in w)
    assert sentence == entry["posted_warning"]
    assert "books over-billings $10,000.00" in sentence
    assert "the schedule now reads $10,243.90" in sentence
    assert "Void the posted entry and post it again" in sentence

    csv_text = client.get(f"/api/reports/wip-schedule/csv?as_of={AUG31}").text
    assert "books over-billings $10,000.00" in csv_text

    # void and re-post: the books and the schedule agree again
    assert (
        client.post(f"/api/reports/wip-schedule/void?as_of={AUG31}").status_code == 200
    )
    _post(client, AUG31)
    db_session.expire_all()
    entry = job_reports.wip_schedule(db_session, AUG31)["adjusting_entry"]
    assert entry["posted_differs"] is False
    assert entry["totals"]["over_billing"] == 10243.9


def test_the_wip_screen_shows_the_difference_beside_void():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/reports.js").read_text(
        encoding="utf-8"
    )
    assert "entry.posted_differs" in js and "data-posted-differs" in js
    jobs_js = (Path(__file__).resolve().parents[1] / "app/static/js/jobs.js").read_text(
        encoding="utf-8"
    )
    assert "co-approve-warnings" in jobs_js


# ── 2. misspelled filters ───────────────────────────────────────────────

_ROUTES = (
    "/api/reports/wip-schedule",
    "/api/reports/wip-schedule/csv",
    "/api/reports/wip-schedule/pdf",
    "/api/reports/wip-schedule/print",
    "/api/jobs/1/cost-tree",
    "/api/jobs/1/cost-tree/csv",
    "/api/jobs/1/cost-tree/pdf",
    "/api/reports/profit-loss-by-job",
    "/api/reports/profit-loss-by-job/csv",
    "/api/reports/profit-loss-by-job/pdf",
    "/api/reports/profit-loss-by-job/print",
    "/api/reports/job-profitability",
    "/api/reports/job-profitability/csv",
    "/api/reports/job-profitability/pdf",
    "/api/reports/job-profitability/print",
    "/api/jobs/budget-vs-actual",
    "/api/reports/job-budget-vs-actual/csv",
    "/api/reports/job-budget-vs-actual/pdf",
    "/api/reports/job-budget-vs-actual/print",
    "/api/reports/ar-aging",
    "/api/reports/ar-aging/csv",
    "/api/reports/ap-aging",
    "/api/reports/ap-aging/csv",
    "/api/field-tickets/purchase-orders",
    "/api/change-orders",
    "/api/pay-applications",
)


@pytest.mark.parametrize("route", _ROUTES)
def test_a_misspelled_filter_is_refused(client, seed_accounts, route):
    """L bug 4 / M bug 1: `asof` on the WIP gave today's schedule. Each of
    these refuses it (and `jobid`) with the filters it does read; the
    agings in every mode."""
    for mode in ("construction", "business"):
        assert (
            client.put("/api/settings", json={"company_type": mode}).status_code == 200
        )
        for bad in ("asof=2026-09-30", "jobid=1"):
            r = client.get(f"{route}?{bad}")
            assert r.status_code == 422, (mode, route, bad, r.status_code)
            name = bad.split("=")[0]
            assert r.json()["detail"].startswith(f"{name} is not a filter here."), (
                route,
                r.json(),
            )


_FRONTEND_QUERIES = (
    "/api/reports/wip-schedule?as_of=2026-09-30",
    "/api/reports/wip-schedule/csv?as_of=2026-09-30",
    "/api/jobs/{job}/cost-tree?start_date=2026-01-01&end_date=2026-09-30",
    "/api/jobs/{job}/cost-tree/csv?start_date=2026-01-01&end_date=2026-09-30",
    "/api/reports/profit-loss-by-job?start_date=2026-01-01&end_date=2026-09-30"
    "&job_ids={job}&basis=accrual&customer_id={customer}&show=all"
    "&include_empty=true",
    "/api/reports/profit-loss-by-job/csv?start_date=2026-01-01"
    "&end_date=2026-09-30&layout=long",
    "/api/reports/job-profitability?start_date=2026-01-01&end_date=2026-09-30"
    "&customer_id={customer}&basis=cash",
    "/api/reports/job-profitability/csv?start_date=2026-01-01"
    "&end_date=2026-09-30&customer_id={customer}",
    "/api/jobs/budget-vs-actual?start_date=2026-01-01&end_date=2026-09-30"
    "&include_inactive=true&customer_id={customer}",
    "/api/reports/job-budget-vs-actual/csv?start_date=2026-01-01"
    "&end_date=2026-09-30&include_inactive=true",
    "/api/reports/ar-aging?as_of_date=2026-09-30",
    "/api/reports/ar-aging/csv?as_of_date=2026-09-30",
    "/api/reports/ap-aging?as_of_date=2026-09-30",
    "/api/reports/ap-aging/csv?as_of_date=2026-09-30",
    "/api/field-tickets/purchase-orders?job_id={job}",
    "/api/change-orders?job_id={job}&history=true",
    "/api/pay-applications?job_id={job}",
)


@pytest.mark.parametrize("query", _FRONTEND_QUERIES)
def test_the_filters_the_screens_send_still_work(
    client, db_session, seed_accounts, query
):
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    r = client.get(query.format(job=job.id, customer=job.customer_id))
    assert r.status_code == 200, (query, r.text)


def test_the_agings_still_read_their_as_of_date(client, seed_accounts):
    r = client.get("/api/reports/ar-aging?as_of_date=2026-09-30")
    assert r.status_code == 200 and r.json()["as_of_date"] == "2026-09-30"
    r = client.get("/api/reports/ap-aging?as_of_date=2026-09-30")
    assert r.status_code == 200 and r.json()["as_of_date"] == "2026-09-30"


# ── 3. Overview = Budget vs Actual = cost tree ──────────────────────────


def _partly_bought_job(db):
    """Budget 100,000 over two sections: Concrete 40,000 (cost 30,000 on
    3/1), Electrical 60,000 not bought yet. Projected final cost counts the
    budget nothing has taken up (10,000 + 60,000): 100,000, variance 0,
    where actual + committed alone reads 30,000."""
    job = _job(db, name="Annex", contract="150000")
    conc = _db_section(db, "97 30 00", "Concrete")
    elec = _db_section(db, "98 05 00", "Electrical")
    _budget(db, job, conc, "subcontract", "40000")
    _budget(db, job, elec, "subcontract", "60000")
    _cost(db, job, conc, "subcontract", "30000")
    db.commit()
    return job


def test_budget_vs_actual_and_the_cost_tree_project_like_the_overview(
    client, db_session, seed_accounts
):
    """M bug 3: the Overview (the undated tree) read Projected 377,525.55,
    Variance 0; BvA, its exports and the dated tree left out the budget not
    yet committed."""
    _setup(client, db_session)
    job = _partly_bought_job(db_session)
    end = date.today()
    overview = job_cost_tree(db_session, job.id)["totals"]
    assert overview["projected"] == 100000.0 and overview["variance"] == 0.0

    start = date(end.year, 1, 1)  # the Report Center's default period
    dated = job_cost_tree(db_session, job.id, start, end)["totals"]
    bva = {r["job_id"]: r for r in budget_vs_actual_all_jobs(db_session, start, end)}[
        job.id
    ]
    assert dated["projected"] == bva["projected"] == overview["projected"]
    assert dated["variance"] == bva["variance"] == overview["variance"]

    r = client.get(
        f"/api/reports/job-budget-vs-actual/csv?start_date={start}&end_date={end}"
    )
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))
    head = next(row for row in rows if row and row[0] == "Customer")
    annex = next(row for row in rows if len(row) > 1 and row[1] == "Annex")
    assert annex[head.index("Projected")] == "100000.00"
    assert annex[head.index("Variance")] == "0.00"
    tree = client.get(
        f"/api/jobs/{job.id}/cost-tree?start_date={start}&end_date={end}"
    ).json()
    assert tree["totals"]["projected"] == 100000.0


def test_a_period_after_the_first_cost_still_projects_the_whole_job(
    client, db_session, seed_accounts
):
    _setup(client, db_session)
    job = _partly_bought_job(db_session)
    end = date.today()
    start = date(2026, 6, 1)  # after the 3/1 cost
    dated = job_cost_tree(db_session, job.id, start, end)["totals"]
    assert dated["actual"] == 0.0
    assert dated["cost_before_period"] == 30000.0
    assert dated["projected"] == 100000.0 and dated["variance"] == 0.0
    bva = budget_vs_actual_all_jobs(db_session, start, end)
    assert {r["job_id"]: r["projected"] for r in bva}[job.id] == 100000.0


def test_a_business_budget_vs_actual_projects_as_before(db_session, seed_accounts):
    job = _partly_bought_job(db_session)  # no company type: Business
    end = date.today()
    row = {
        r["job_id"]: r
        for r in budget_vs_actual_all_jobs(db_session, date(end.year, 1, 1), end)
    }[job.id]
    assert row["projected"] == 30000.0 and row["variance"] == 70000.0
    assert "uncommitted_budget" not in row
    totals = job_cost_tree(db_session, job.id)["totals"]
    assert totals["projected"] == 30000.0


# ── 4. markup revenue by section ────────────────────────────────────────


def _markup_job(client, customer_id):
    """Concrete 40,000 and Electrical 60,000, an accepted −10,000 deduct on
    Sitework, fee 10%: the 9,000 fee spreads 4,000 / 6,000 / −1,000."""
    conc = _section(client, "03 30 00", "Concrete")
    elec = _section(client, "26 05 00", "Electrical")
    site = _section(client, "31 00 00", "Earthwork")
    lines = [
        {
            "description": "Slab",
            "quantity": 1,
            "rate": 40000,
            "unit_cost": 40000,
            "cost_code_id": conc["id"],
        },
        {
            "description": "Wiring",
            "quantity": 1,
            "rate": 60000,
            "unit_cost": 60000,
            "cost_code_id": elec["id"],
        },
        {
            "description": "Deduct: owner does the grading",
            "quantity": 1,
            "rate": -10000,
            "unit_cost": -10000,
            "cost_code_id": site["id"],
            "line_kind": "alternate",
            "alternate_accepted": True,
        },
    ]
    job, sov, _est = _accepted_build(client, customer_id, lines, fee_pct=10)
    return job, sov, conc, elec, site


def test_a_deduct_alternate_takes_its_share_of_the_markups(
    client, db_session, seed_accounts, seed_customer
):
    """M bug 5: the accepted −6,000 deduct got no share and HVAC absorbed
    −975.12. Here the −10,000 deduct takes −1,000 of the 9,000 fee."""
    _setup(client, db_session)
    job, _sov, conc, elec, site = _markup_job(client, seed_customer.id)
    revenue = {
        b.cost_code_id: Decimal(str(b.revenue_amount))
        for b in db_session.query(JobBudget).filter(JobBudget.job_id == job["id"])
    }
    assert revenue[conc["id"]] == Decimal("44000.00")
    assert revenue[elec["id"]] == Decimal("66000.00")
    assert revenue[site["id"]] == Decimal("-11000.00")


def test_markup_billing_sits_on_the_sections_a_full_section_reads_its_budget(
    client, db_session, seed_accounts, seed_customer
):
    """M bug 5: Demo billed 100% read est 20,925.36 vs act 18,000, and the
    markup lines' 45,635.61 sat under Unassigned."""
    _setup(client, db_session)
    job, sov, conc, elec, site = _markup_job(client, seed_customer.id)
    fee = next(s for s in sov if s["kind"] == "fee")
    assert fee["cost_code_id"] is None
    billed = {s["id"]: 0 for s in sov}
    for s in sov:
        if s["cost_code_id"] == conc["id"]:
            billed[s["id"]] = 40000  # all of Concrete
        elif s["cost_code_id"] == elec["id"]:
            billed[s["id"]] = 30000  # half of Electrical
    billed[fee["id"]] = 7000  # the fee on the work billed
    r = client.post(
        "/api/pay-applications", json={"job_id": job["id"], "date": "2026-07-31"}
    )
    assert r.status_code == 201, r.text
    app_id = r.json()["id"]
    lines = [
        {"sov_line_id": sid, "this_period": amt, "stored": 0}
        for sid, amt in billed.items()
    ]
    r = client.put(f"/api/pay-applications/{app_id}", json={"lines": lines})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/pay-applications/{app_id}/approve")
    assert r.status_code == 200, r.text

    tree = client.get(f"/api/jobs/{job['id']}/cost-tree").json()
    by = {r["cost_code_id"]: r for r in tree["revenue_sections"]}
    assert by[conc["id"]]["est_revenue"] == by[conc["id"]]["act_revenue"] == 44000.0
    assert by[elec["id"]]["act_revenue"] == 33000.0
    assert None not in by or by[None]["act_revenue"] == 0.0
    assert tree["totals"]["act_revenue"] == 77000.0


# ── 5. a change order as of a date ───────────────────────────────────────


def _void_later(db, co_id, on):
    """Mark a CO voided on `on` (the void route stamps today)."""
    from datetime import datetime, timezone

    co = db.get(ChangeOrder, co_id)
    co.voided_at = datetime(on.year, on.month, on.day, 12, tzinfo=timezone.utc)
    db.commit()


def test_the_log_and_the_wip_read_a_change_order_as_it_stood(
    client, db_session, seed_accounts
):
    """M bug 6: as of 9/30 a CO approved 8/12 and voided 11/08 counted as
    approved but its sell was left out of approved changes, and the log
    listed COs dated after 9/30. The WIP at 9/30 now includes it too."""
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    kept = _co(client, job.id, 1000, when=date(2026, 8, 1))
    _approve(client, kept["id"], date(2026, 8, 1))
    gone = _co(client, job.id, 5000, when=date(2026, 8, 12))
    _approve(client, gone["id"], date(2026, 8, 12))
    db_session.expire_all()
    before_void, _ = _wip_row(db_session, job.id, SEP30)
    assert client.post(f"/api/change-orders/{gone['id']}/void").status_code == 200
    _void_later(db_session, gone["id"], date(2026, 11, 8))
    _co(client, job.id, 700, when=date(2026, 10, 5))  # pending, dated after
    db_session.expire_all()

    log = job_reports.change_order_log(db_session, SEP30)
    j = log["jobs"][0]
    assert [r["number"] for r in j["rows"]] == [kept["number"], gone["number"]]
    assert [r["status"] for r in j["rows"]] == ["approved", "approved"]
    assert j["rows"][1]["status_today"] == "void"
    assert j["counts"] == {"approved": 2}
    assert j["contract"]["approved_changes"] == 6000.0
    assert j["contract"]["revised"] == 106000.0
    assert j["rows"][-1]["running_approved"] == 6000.0

    row, _ = _wip_row(db_session, job.id, SEP30)
    assert row["revised_contract"] == 106000.0
    assert row["approved_change_orders"] == 6000.0
    # its budget stood on the day too: the 9/30 schedule reads as before the void
    assert row["estimated_total_cost"] == before_void["estimated_total_cost"]
    assert row["over_billing"] == before_void["over_billing"]

    # after the void it is gone from both
    log = job_reports.change_order_log(db_session, date(2026, 11, 30))
    j = log["jobs"][0]
    assert j["counts"] == {"approved": 1, "void": 1, "pending": 1}
    assert j["contract"]["approved_changes"] == 1000.0
    row, _ = _wip_row(db_session, job.id, date(2026, 11, 30))
    assert row["revised_contract"] == 101000.0


# ── 6. the smaller report items ──────────────────────────────────────────


def test_job_profitability_and_bva_read_the_contract_as_of_their_end_date(
    client, db_session, seed_accounts
):
    """L weakness: at end_date 9/30 both reported today's contract while the
    WIP's revised contract on that day was higher."""
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    a = _co(client, job.id, 4000, when=date(2026, 8, 1))
    _approve(client, a["id"], date(2026, 8, 1))
    b = _co(client, job.id, 3000, when=date(2026, 10, 15))
    _approve(client, b["id"], date(2026, 10, 15))
    db_session.expire_all()
    assert db_session.get(Job, job.id).contract_amount == Decimal("107000.00")

    q = "start_date=2026-01-01&end_date=2026-09-30"
    prof = client.get(f"/api/reports/job-profitability?{q}").json()
    assert {r["job_id"]: r["contract_amount"] for r in prof["jobs"]}[job.id] == (
        104000.0
    )
    bva = client.get(f"/api/jobs/budget-vs-actual?{q}").json()
    assert {r["job_id"]: r["contract_amount"] for r in bva}[job.id] == 104000.0
    row, _ = _wip_row(db_session, job.id, SEP30)
    assert row["revised_contract"] == 104000.0
    # no end date: today's
    bva = client.get("/api/jobs/budget-vs-actual").json()
    assert {r["job_id"]: r["contract_amount"] for r in bva}[job.id] == 107000.0


def test_a_job_contracted_after_the_date_is_not_on_that_wip(
    client, db_session, seed_accounts
):
    """L weakness: WIP as of 7/01 listed a job whose contract is dated 7/15
    with its contract and backlog."""
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    job.contract_date = date(2026, 7, 15)
    db_session.commit()
    row, data = _wip_row(db_session, job.id, date(2026, 7, 1))
    assert row is None
    assert data["totals"]["revised_contract"] == 0.0
    row, _ = _wip_row(db_session, job.id, date(2026, 7, 15))
    assert row is not None and row["revised_contract"] == 100000.0


def test_an_earlier_wip_shows_the_status_the_job_had_then(
    client, db_session, seed_accounts
):
    """M weakness 12: a job closed 11/08 read "closed" on the 9/30 WIP."""
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    job.status = "closed"
    job.closed_on = date(2026, 11, 8)
    db_session.commit()
    row, _ = _wip_row(db_session, job.id, SEP30, include_closed=True)
    assert row["status"] == "in_progress"
    row, _ = _wip_row(
        db_session, job.id, date(2026, 11, 8) + timedelta(days=1), include_closed=True
    )
    assert row["status"] == "closed"


@pytest.mark.parametrize("mode", ("business", "nonprofit"))
def test_the_jobs_buyout_is_construction_modes(client, db_session, seed_accounts, mode):
    """L weakness: GET /api/jobs/{id}/buyout answered in Business and
    Nonprofit while the Report Center's Buyout Report is gated."""
    job = _job(db_session)
    db_session.commit()
    assert client.put("/api/settings", json={"company_type": mode}).status_code == 200
    r = client.get(f"/api/jobs/{job.id}/buyout")
    assert r.status_code == 409
    assert "part of construction mode" in r.json()["detail"]
    # the cost tree (job costing) stays open
    assert client.get(f"/api/jobs/{job.id}/cost-tree").status_code == 200
    client.put("/api/settings", json={"company_type": "construction"})
    assert client.get(f"/api/jobs/{job.id}/buyout").status_code == 200
