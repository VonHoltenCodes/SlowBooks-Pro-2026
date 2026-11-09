"""Skeptic round 1 on #731, the WIP workstream: a posted month never moves.

Two posts of one date cannot both land (a lock row, not a re-check); a
change order's budget is as of the day it takes effect, so a later CO leaves
the posted schedule alone; Budget vs Actual's committed is as of its date; a
deduct alternate is no overrun; a job closed later is still in an earlier
WIP; a bad date gets a sentence; Post is construction mode's; the entry's
lines carry their jobs, so P&L by Job and Job Profitability tie to the
schedule while the schedule itself never reads them; revenue sits beside
cost by section; and the Change Order Log's export says what it documents.
"""

import csv
import importlib.util
import io
import threading
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.accounts import Account
from app.models.bills import Bill, BillLine, BillStatus
from app.models.change_orders import ChangeOrder, ChangeOrderLine
from app.models.contacts import Vendor
from app.models.job_costing import JobBudget
from app.models.jobs import Job
from app.models.purchase_orders import POStatus, PurchaseOrder, PurchaseOrderLine
from app.models.settings import Settings
from app.models.transactions import Transaction, TransactionLine
from app.services import job_reports, wip_posting
from app.services.job_costing import budget_vs_actual_all_jobs, job_cost_tree

from tests.test_wip_schedule_post import (
    _acct,
    _bill_owner,
    _budget,
    _cost,
    _job,
    _section,
)

ROOT = Path(__file__).resolve().parents[1]
SEP30 = date(2026, 9, 30)
OCT15 = date(2026, 10, 15)


def _construction(db):
    db.add(Settings(key="company_type", value="construction"))
    db.commit()


def _setup(client, db):
    assert client.post("/api/construction/setup").status_code == 200
    _construction(db)


def _over_billed_job(db, name="Clinic"):
    """Contract 100,000, budget 80,000 (material), cost 40,000 by 3/1,
    billed 60,000 by 4/1: earned 50,000, over-billed 10,000."""
    job = _job(db, name=name)
    _budget(db, job, None, "material", "80000", source="manual")
    _cost(db, job, None, "material", "40000")
    _bill_owner(db, job, "60000")
    db.commit()
    return job


def _wip_row(db, job_id, as_of, **kw):
    data = job_reports.wip_schedule(db, as_of=as_of, **kw)
    return next((r for r in data["rows"] if r["job_id"] == job_id), None), data


def _approve_co(client, job_id, unit_cost, approved_on, cost_type="material"):
    r = client.post(
        "/api/change-orders",
        json={
            "job_id": job_id,
            "kind": "owner",
            "funding": "contract",
            "lines": [
                {
                    "description": "Added scope",
                    "cost_type": cost_type,
                    "quantity": 1,
                    "unit_cost": unit_cost,
                    "markup_pct": 0,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    co = r.json()
    assert client.post(f"/api/change-orders/{co['id']}/submit").status_code == 200
    r = client.post(
        f"/api/change-orders/{co['id']}/approve",
        json={"approved_on": approved_on.isoformat()},
    )
    assert r.status_code == 200, r.text
    return co


def _migration():
    path = ROOT / "migrations/versions/b5d2e8f4a6c3_wip_as_of_budgets_and_lock.py"
    spec = importlib.util.spec_from_file_location("r1_migration", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 1. two posts of one date ─────────────────────────────────────────────


def test_a_second_post_that_passed_the_check_meets_the_lock_row(
    client, db_session, seed_accounts, monkeypatch
):
    """Both requests read "nothing posted" before either wrote: the lock
    row's unique key, not the re-check, refuses the second."""
    _setup(client, db_session)
    _over_billed_job(db_session)
    monkeypatch.setattr(wip_posting, "posted_entry", lambda db, as_of: None)
    url = f"/api/reports/wip-schedule/post?as_of={SEP30}"
    assert client.post(url).status_code == 200
    again = client.post(url)
    assert again.status_code == 409, again.text
    assert "already posted for 2026-09-30" in again.json()["detail"]
    assert (
        db_session.query(Transaction).filter_by(source_type="wip_adjustment").count()
        == 1
    )


def test_void_frees_the_date_for_a_new_post(client, db_session, seed_accounts):
    _setup(client, db_session)
    _over_billed_job(db_session)
    url = f"/api/reports/wip-schedule/post?as_of={SEP30}"
    assert client.post(url).status_code == 200
    assert (
        client.post(f"/api/reports/wip-schedule/void?as_of={SEP30}").status_code == 200
    )
    assert client.post(url).status_code == 200


def _file_books(tmp_path):
    """A company file on disk, so two threads can each have a session."""
    from app.database import Base, enable_sqlite_tuning
    from app.models.accounts import Account, AccountType
    from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS
    from app.services.accounting import ensure_construction_accounts

    eng = create_engine(
        f"sqlite:///{tmp_path / 'race.db'}", connect_args={"check_same_thread": False}
    )
    enable_sqlite_tuning(eng)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        for data in CHART_OF_ACCOUNTS:
            s.add(
                Account(
                    account_number=data["account_number"],
                    name=data["name"],
                    account_type=AccountType(data["account_type"]),
                    detail_type=data.get("detail_type"),
                    is_system=True,
                    balance=Decimal("0"),
                )
            )
        s.commit()
        ensure_construction_accounts(s)
        _construction(s)
        _over_billed_job(s)
    return eng


def test_two_posts_at_once_only_one_lands(tmp_path, monkeypatch):
    eng = _file_books(tmp_path)
    # both requests read "nothing posted" (the race), so only the lock row
    # can stop the second
    monkeypatch.setattr(wip_posting, "posted_entry", lambda db, as_of: None)
    Local = sessionmaker(bind=eng)
    go = threading.Event()
    results = []

    def post():
        with Local() as s:
            go.wait()
            try:
                wip_posting.post_wip_entry(s, SEP30, "admin")
                s.commit()
                results.append(200)
            except Exception as e:  # HTTPException carries the status
                s.rollback()
                results.append(getattr(e, "status_code", repr(e)))

    threads = [threading.Thread(target=post) for _ in range(2)]
    for t in threads:
        t.start()
    go.set()
    for t in threads:
        t.join(30)
    assert sorted(results, key=str) == [200, 409], results
    with Session(eng) as s:
        assert s.query(Transaction).filter_by(source_type="wip_adjustment").count() == 1
    eng.dispose()


# ── 2. budgets as of their date ──────────────────────────────────────────


def test_a_later_change_order_leaves_the_posted_month_alone(
    client, db_session, seed_accounts
):
    """The reviewers' case: post 9/30, approve a CO effective 10/15, and the
    9/30 schedule still reproduces the posted entry."""
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    before, _ = _wip_row(db_session, job.id, SEP30)
    posted = client.post(f"/api/reports/wip-schedule/post?as_of={SEP30}")
    assert posted.status_code == 200, posted.text

    _approve_co(client, job.id, 20000, OCT15)
    db_session.expire_all()

    row, data = _wip_row(db_session, job.id, SEP30)
    assert row["estimated_total_cost"] == before["estimated_total_cost"] == 80000.0
    assert row["over_billing"] == before["over_billing"] == 10000.0
    assert data["adjusting_entry"]["posted"] is True
    assert data["adjusting_entry"]["total_debits"] == posted.json()["total_debits"]
    # on and after the CO's day its budget counts
    later, _ = _wip_row(db_session, job.id, OCT15)
    assert later["estimated_total_cost"] == 100000.0


def test_cost_tree_budget_vs_actual_and_ctc_read_the_change_as_of(
    client, db_session, seed_accounts
):
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    _approve_co(client, job.id, 20000, OCT15)
    db_session.expire_all()

    assert job_cost_tree(db_session, job.id, end_date=SEP30)["totals"]["changes"] == 0
    assert (
        job_cost_tree(db_session, job.id, end_date=OCT15)["totals"]["changes"]
        == 20000.0
    )
    bva = {r["job_id"]: r for r in budget_vs_actual_all_jobs(db_session, None, SEP30)}
    assert bva[job.id]["changes"] == 0
    ctc = job_reports.cost_to_complete(db_session, as_of=SEP30, job_id=job.id)
    assert ctc["jobs"][0]["totals"]["budget_revised"] == 80000.0
    forecast = job_reports.ctc_forecast(db_session, job, SEP30)
    assert forecast["totals"]["budget"] == 80000.0


def test_each_change_order_writes_its_own_rows(client, db_session, seed_accounts):
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    a = _approve_co(client, job.id, 1000, date(2026, 8, 10))
    b = _approve_co(client, job.id, -400, OCT15)
    rows = (
        db_session.query(JobBudget)
        .filter(JobBudget.job_id == job.id, JobBudget.source == "change")
        .all()
    )
    assert {r.change_order_id: Decimal(str(r.amount)) for r in rows} == {
        a["id"]: Decimal("1000.00"),
        b["id"]: Decimal("-400.00"),
    }
    # void takes the CO's own row only
    assert client.post(f"/api/change-orders/{a['id']}/void").status_code == 200
    db_session.expire_all()
    left = db_session.query(JobBudget).filter(JobBudget.source == "change").all()
    assert [r.change_order_id for r in left] == [b["id"]]


def _approved_co(db, job, number, lines, on, funding="contract", **extra):
    co = ChangeOrder(
        job_id=job.id,
        number=number,
        kind="owner",
        status="approved",
        funding=funding,
        approved_on=on,
        **extra,
    )
    db.add(co)
    db.flush()
    for i, (code_id, ctype, cost, sell) in enumerate(lines):
        db.add(
            ChangeOrderLine(
                change_order_id=co.id,
                line_order=i,
                description="x",
                cost_type=ctype,
                cost_code_id=code_id,
                quantity=1,
                unit_cost=Decimal(cost),
                amount=Decimal(cost),
                sell_amount=Decimal(sell),
            )
        )
    db.flush()
    return co


def test_the_backfill_splits_a_shared_row_that_adds_up(db_session, seed_accounts):
    job = _job(db_session)
    a = _approved_co(
        db_session, job, "CO-A", [(None, "material", "1000", "1100")], SEP30
    )
    b = _approved_co(
        db_session, job, "CO-B", [(None, "material", "-400", "-440")], OCT15
    )
    # the merged row the old code wrote for both, and one that no longer adds up
    db_session.add(
        JobBudget(
            job_id=job.id,
            cost_type="material",
            amount=Decimal("600"),
            revenue_amount=Decimal("660"),
            source="change",
            locked=True,
        )
    )
    _approved_co(db_session, job, "CO-C", [(None, "labor", "500", "500")], SEP30)
    db_session.add(
        JobBudget(
            job_id=job.id,
            cost_type="labor",
            amount=Decimal("700"),  # edited since: 500 cannot be recovered
            revenue_amount=Decimal("500"),
            source="change",
            locked=True,
        )
    )
    db_session.commit()

    _migration()._link_change_budgets(db_session.connection())
    db_session.commit()
    db_session.expire_all()
    rows = db_session.query(JobBudget).filter(JobBudget.source == "change").all()
    material = {
        r.change_order_id: (Decimal(str(r.amount)), Decimal(str(r.revenue_amount)))
        for r in rows
        if r.cost_type == "material"
    }
    assert material == {
        a.id: (Decimal("1000.00"), Decimal("1100.00")),
        b.id: (Decimal("-400.00"), Decimal("-440.00")),
    }
    labor = [r for r in rows if r.cost_type == "labor"]
    assert len(labor) == 1 and labor[0].change_order_id is None


# ── 2b. committed as of the date ─────────────────────────────────────────


def _po_billed_later(db, job):
    """A 50,000 subcontract issued 8/1, 20,000 of it billed 9/15."""
    vendor = Vendor(name="Sparks Electric", is_active=True)
    db.add(vendor)
    db.flush()
    po = PurchaseOrder(
        po_number="PO-R1",
        vendor_id=vendor.id,
        status=POStatus.PARTIAL,
        date=date(2026, 8, 1),
        job_id=job.id,
        commitment_type="subcontract",
        subtotal=Decimal("50000"),
        total=Decimal("50000"),
    )
    db.add(po)
    db.flush()
    line = PurchaseOrderLine(
        purchase_order_id=po.id,
        description="Electrical",
        quantity=1,
        rate=Decimal("50000"),
        amount=Decimal("50000"),
        billed_amount=Decimal("20000"),
        cost_type="subcontract",
    )
    db.add(line)
    db.flush()
    bill = Bill(
        bill_number="B-1",
        vendor_id=vendor.id,
        status=BillStatus.UNPAID,
        po_id=po.id,
        date=date(2026, 9, 15),
        subtotal=Decimal("20000"),
        total=Decimal("20000"),
        balance_due=Decimal("20000"),
    )
    db.add(bill)
    db.flush()
    db.add(
        BillLine(
            bill_id=bill.id,
            description="Electrical",
            amount=Decimal("20000"),
            job_id=job.id,
            cost_type="subcontract",
            purchase_order_line_id=line.id,
        )
    )
    db.commit()


def test_committed_reads_as_of_the_date(client, db_session, seed_accounts):
    _setup(client, db_session)
    job = _job(db_session)
    _po_billed_later(db_session, job)

    aug = job_cost_tree(db_session, job.id, end_date=date(2026, 8, 31))["totals"]
    assert aug["committed"] == 50000.0
    assert aug["committed_total"] == 50000.0 and aug["billed_to_date"] == 0.0
    assert aug["commitments_all_time"] is False
    now = job_cost_tree(db_session, job.id)["totals"]
    assert now["committed"] == 30000.0
    # before the commitment was issued there is none
    jul = job_cost_tree(db_session, job.id, end_date=date(2026, 7, 31))["totals"]
    assert jul["committed"] == 0.0

    bva = {
        r["job_id"]: r
        for r in budget_vs_actual_all_jobs(db_session, None, date(2026, 8, 31))
    }
    assert bva[job.id]["committed"] == 50000.0
    status = job_reports.commitment_status(db_session, date(2026, 8, 31), job.id)
    assert status["totals"]["remaining"] == bva[job.id]["committed"]


def test_a_business_company_keeps_todays_commitments(client, db_session, seed_accounts):
    job = _job(db_session)
    _po_billed_later(db_session, job)
    aug = job_cost_tree(db_session, job.id, end_date=date(2026, 8, 31))["totals"]
    assert aug["committed"] == 30000.0 and aug["commitments_all_time"] is True


# ── 3. a deduct alternate is no overrun ──────────────────────────────────


def _deduct_job(db):
    """Electrical 100,000 (subcontract) and an accepted −6,000 deduct
    alternate on a section of its own."""
    job = _job(db)
    elec = _section(db, "81 05 00", "Electrical")
    tile = _section(db, "82 30 00", "Tiling")
    _budget(db, job, elec, "subcontract", "100000")
    _budget(db, job, tile, "material", "-6000")
    db.commit()
    return job, elec, tile


def test_an_accepted_deduct_shows_no_variance_and_no_extra_cost(
    client, db_session, seed_accounts
):
    _setup(client, db_session)
    job, elec, tile = _deduct_job(db_session)
    _cost(db_session, job, elec, "subcontract", "47000")
    db_session.commit()

    row, _ = _wip_row(db_session, job.id, SEP30)
    assert row["estimated_total_cost"] == 94000.0  # 100,000 − 6,000, not 100,000
    assert row["cost_to_complete"] == 47000.0  # 53,000 still to come less 6,000

    tree = job_cost_tree(db_session, job.id, end_date=SEP30)
    assert tree["totals"]["variance"] == 0.0
    material = next(t for t in tree["types"] if t["cost_type"] == "material")
    assert material["figures"]["variance"] == 0.0
    bva = {r["job_id"]: r for r in budget_vs_actual_all_jobs(db_session, None, SEP30)}
    assert bva[job.id]["variance"] == 0.0

    ctc = job_reports.cost_to_complete(db_session, as_of=SEP30, job_id=job.id)
    assert ctc["jobs"][0]["totals"]["variance"] == 0.0


def test_a_deduct_cannot_hide_cost_already_spent(client, db_session, seed_accounts):
    """Electrical spent its whole 100,000: the job is 6,000 over its 94,000,
    and the deduct takes nothing off cost already posted."""
    _setup(client, db_session)
    job, elec, _tile = _deduct_job(db_session)
    _cost(db_session, job, elec, "subcontract", "100000")
    db_session.commit()
    row, _ = _wip_row(db_session, job.id, SEP30)
    assert row["estimated_total_cost"] == 100000.0 and row["cost_to_complete"] == 0.0
    forecast = job_reports.ctc_forecast(db_session, job, SEP30)
    assert forecast["totals"]["cost_to_complete"] == 0.0
    bva = {r["job_id"]: r for r in budget_vs_actual_all_jobs(db_session, None, SEP30)}
    assert bva[job.id]["variance"] == -6000.0


# ── 4. a closed job in a back-dated WIP ─────────────────────────────────


def test_a_job_closed_in_november_is_in_septembers_wip(
    client, db_session, seed_accounts
):
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    job.status, job.closed_on = "closed", date(2026, 11, 5)
    db_session.commit()

    row, data = _wip_row(db_session, job.id, SEP30)
    assert row is not None and row["percent_complete"] == 50.0  # open on 9/30
    assert [j["job_id"] for j in data["adjusting_entry"]["jobs"]] == [job.id]
    # from the day it closed it is off the board (and 100% with include_closed)
    assert _wip_row(db_session, job.id, date(2026, 11, 30))[0] is None
    closed, _ = _wip_row(db_session, job.id, date(2026, 11, 30), include_closed=True)
    assert closed["percent_complete"] == 100.0


def test_closing_a_job_stamps_the_day_and_reopening_clears_it(
    client, db_session, seed_accounts, seed_customer
):
    r = client.post("/api/jobs", json={"customer_id": seed_customer.id, "name": "J"})
    job_id = r.json()["id"]
    client.put(f"/api/jobs/{job_id}", json={"status": "closed"})
    db_session.expire_all()
    assert db_session.get(Job, job_id).closed_on == date.today()
    client.put(f"/api/jobs/{job_id}", json={"status": "in_progress"})
    db_session.expire_all()
    assert db_session.get(Job, job_id).closed_on is None


# ── 5. a bad date ───────────────────────────────────────────────────────


def test_the_last_day_of_the_calendar_gets_a_sentence(
    client, db_session, seed_accounts
):
    _setup(client, db_session)
    _over_billed_job(db_session)
    r = client.post("/api/reports/wip-schedule/post?as_of=9999-12-31")
    assert r.status_code == 422, r.text
    assert "last day the calendar has" in r.json()["detail"]
    assert client.get("/api/reports/compliance?as_of=9999-12-31").status_code == 200


# ── 6. construction mode only ────────────────────────────────────────────


def test_post_is_refused_outside_construction(client, db_session, seed_accounts):
    client.post("/api/construction/setup")
    _over_billed_job(db_session)
    for mode in ("business", "nonprofit"):
        db_session.query(Settings).filter_by(key="company_type").delete()
        db_session.add(Settings(key="company_type", value=mode))
        db_session.commit()
        r = client.post(f"/api/reports/wip-schedule/post?as_of={SEP30}")
        assert r.status_code == 409, r.text
        assert "part of construction mode" in r.json()["detail"]
    assert (
        db_session.query(Transaction).filter_by(source_type="wip_adjustment").count()
        == 0
    )


# ── 7. the entry carries its jobs ────────────────────────────────────────


def _two_jobs(db):
    """One over-billed job and one under-billed: cost 40,000 of 80,000 on a
    100,000 contract, billed 30,000 (earned 50,000)."""
    over = _over_billed_job(db, "Clinic")
    under = _job(db, name="Library")
    _budget(db, under, None, "material", "80000", source="manual")
    _cost(db, under, None, "material", "40000")
    _bill_owner(db, under, "30000")
    db.commit()
    return over, under


def test_posting_ties_job_reports_to_the_schedule(client, db_session, seed_accounts):
    _setup(client, db_session)
    over, under = _two_jobs(db_session)
    before = client.get(f"/api/reports/wip-schedule?as_of={SEP30}").json()
    tree_before = job_cost_tree(db_session, over.id, end_date=SEP30)["totals"]
    assert (
        client.post(f"/api/reports/wip-schedule/post?as_of={SEP30}").status_code == 200
    )

    after = client.get(f"/api/reports/wip-schedule?as_of={SEP30}").json()
    assert after["rows"] == before["rows"]  # posting moves nothing the WIP reads
    tree_after = job_cost_tree(db_session, over.id, end_date=SEP30)["totals"]
    assert (tree_after["actual"], tree_after["act_revenue"]) == (
        tree_before["actual"],
        tree_before["act_revenue"],
    )

    gp = {r["job_id"]: r["gross_profit_to_date"] for r in after["rows"]}
    by_job = client.get(
        f"/api/reports/profit-loss-by-job?start_date=2026-01-01&end_date={SEP30}"
    ).json()
    cols = {c["job_id"]: c for c in by_job["jobs"]}
    for job in (over, under):
        assert cols[job.id]["gross_profit"] == gp[job.id], job.name

    prof = client.get(
        f"/api/reports/job-profitability?start_date=2026-01-01&end_date={SEP30}"
    ).json()["jobs"]
    assert [r["job_id"] for r in prof if r["job_id"] is None] == []  # no No-job row
    by_id = {r["job_id"]: r for r in prof}
    assert by_id[over.id]["gross_profit"] == gp[over.id] == 10000.0
    assert by_id[under.id]["gross_profit"] == gp[under.id] == 10000.0


def test_a_loss_provision_is_not_the_jobs_cost(client, db_session, seed_accounts):
    """A loss job's provision posts to 5600 on the job; its cost to date,
    forecast and Budget vs Actual stay the job's own cost."""
    _setup(client, db_session)
    job = _job(db_session)
    _budget(db_session, job, None, "material", "120000", source="manual")
    _cost(db_session, job, None, "material", "60000")
    _bill_owner(db_session, job, "50000")
    db_session.commit()
    row, data = _wip_row(db_session, job.id, SEP30)
    assert row["loss_provision"] > 0
    assert (
        client.post(f"/api/reports/wip-schedule/post?as_of={SEP30}").status_code == 200
    )
    db_session.expire_all()
    loss = (
        db_session.query(TransactionLine)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(Account.name == job_reports.WIP_LOSS_EXPENSE_ACCOUNT)
        .all()
    )
    assert len(loss) == 2  # the entry and its reversal
    assert all(ln.job_id == job.id for ln in loss)
    again, _ = _wip_row(db_session, job.id, SEP30)
    assert again == row
    assert (
        job_reports.ctc_forecast(db_session, job, SEP30)["totals"]["actual"] == 60000.0
    )
    bva = {r["job_id"]: r for r in budget_vs_actual_all_jobs(db_session, None, SEP30)}
    assert bva[job.id]["actual"] == 60000.0


# ── 8. revenue beside cost, by section ───────────────────────────────────


def _revenue_on(db, job, code, amount, when=date(2026, 4, 1)):
    """Pay-application-style revenue: on the job and section, no cost type."""
    from app.services.accounting import create_journal_entry

    create_journal_entry(
        db,
        when,
        "owner billing",
        [
            {"account_id": _acct(db, "1100").id, "debit": Decimal(amount)},
            {
                "account_id": _acct(db, "4000").id,
                "credit": Decimal(amount),
                "job_id": job.id,
                "cost_code_id": code.id,
            },
        ],
    )


def test_a_sections_revenue_sits_beside_its_cost(client, db_session, seed_accounts):
    _setup(client, db_session)
    job = _job(db_session)
    elec = _section(db_session, "81 05 00", "Electrical")
    db_session.add(
        JobBudget(
            job_id=job.id,
            cost_code_id=elec.id,
            cost_type="subcontract",
            amount=Decimal("88000"),
            revenue_amount=Decimal("95000"),
            source="estimate",
            locked=True,
        )
    )
    _cost(db_session, job, elec, "subcontract", "40000")
    _revenue_on(db_session, job, elec, "45000")
    db_session.commit()

    tree = job_cost_tree(db_session, job.id)
    row = next(r for r in tree["revenue_sections"] if r["cost_code_id"] == elec.id)
    assert (row["budget"], row["actual"]) == (88000.0, 40000.0)
    assert (row["est_revenue"], row["act_revenue"]) == (95000.0, 45000.0)
    # the type branches carry cost only: no revenue under Other
    for t in tree["types"]:
        assert t["figures"]["act_revenue"] == 0 and t["figures"]["est_revenue"] == 0
    assert tree["totals"]["act_revenue"] == 45000.0
    assert tree["totals"]["est_revenue"] == 95000.0


def test_a_business_tree_keeps_revenue_on_its_types(db_session, seed_accounts):
    job = _job(db_session)
    elec = _section(db_session, "81 05 00", "Electrical")
    _revenue_on(db_session, job, elec, "45000")
    db_session.commit()
    tree = job_cost_tree(db_session, job.id)
    assert "revenue_sections" not in tree
    assert sum(t["figures"]["act_revenue"] for t in tree["types"]) == 45000.0


def test_the_cost_tree_exports_carry_revenue_by_section(
    client, db_session, seed_accounts
):
    _setup(client, db_session)
    job = _job(db_session)
    elec = _section(db_session, "81 05 00", "Electrical")
    _revenue_on(db_session, job, elec, "45000")
    db_session.commit()
    text = client.get(f"/api/jobs/{job.id}/cost-tree/csv").text
    assert "Revenue by section" in text
    assert any("81 05 00" in ln and "45000.00" in ln for ln in text.splitlines())


# ── 9. the Change Order Log's export ─────────────────────────────────────


def test_the_change_order_log_export_and_counts(client, db_session, seed_accounts):
    _setup(client, db_session)
    job = _over_billed_job(db_session)
    _approve_co(client, job.id, 1000, date(2026, 8, 10))
    _approve_co(client, job.id, 500, OCT15)

    data = client.get(f"/api/reports/change-order-log?as_of={SEP30}").json()
    rows = data["jobs"][0]["rows"]
    assert [r["approved_on"] for r in rows] == ["2026-08-10", "2026-10-15"]
    # counts as of the day: the 10/15 CO was still pending on 9/30
    assert data["totals"]["counts"] == {"approved": 1, "pending": 1}

    text = client.get(f"/api/reports/change-order-log/csv?as_of={SEP30}").text
    table = list(csv.reader(io.StringIO(text)))
    header = next(r for r in table if r and r[0] == "Job" and "Number" in r)
    for label in ("Approved on", "Voided on", "Source PCO", "Commitment"):
        assert label in header, label
    first = table[table.index(header) + 1]
    assert first[header.index("Approved on")] == "2026-08-10"
    assert first[header.index("Sell")] == "1000.00"
    contract_head = next(
        r for r in table if r and r[:2] == ["Job", "Original contract"]
    )
    contract = table[table.index(contract_head) + 1]
    assert contract[contract_head.index("Approved changes")] == "1000.00"
    assert contract[contract_head.index("Revised contract")] == "101000.00"


# ── the migration on a file at the previous head ─────────────────────────


def test_the_migration_up_down_and_up(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text

    db = tmp_path / "r1.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    command.upgrade(cfg, "c7e3a1f5d9b2")
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO customers (id, name, is_active) VALUES (1, 'C', 1)")
        )
        conn.execute(
            text(
                "INSERT INTO jobs (id, customer_id, name, status, is_active, updated_at) "
                "VALUES (1, 1, 'J', 'closed', 1, '2026-11-05 14:00:00')"
            )
        )
        for co_id, number, cost, on in (
            (1, "CO-1", 1000, "2026-08-10"),
            (2, "CO-2", -400, "2026-10-15"),
        ):
            conn.execute(
                text(
                    "INSERT INTO change_orders (id, job_id, number, kind, status, funding, "
                    "apply_markup, revision, allowance_return, approved_on) VALUES "
                    "(:i, 1, :n, 'owner', 'approved', 'contract', 1, 1, 0, :on)"
                ),
                {"i": co_id, "n": number, "on": on},
            )
            conn.execute(
                text(
                    "INSERT INTO change_order_lines (change_order_id, line_order, cost_type, "
                    "quantity, unit_cost, amount, sell_amount) VALUES "
                    "(:i, 0, 'material', 1, :c, :c, :c)"
                ),
                {"i": co_id, "c": cost},
            )
        conn.execute(
            text(
                "INSERT INTO job_budgets (job_id, cost_type, amount, revenue_amount, "
                "source, locked) VALUES (1, 'material', 600, 600, 'change', 1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO transactions (id, date, description, source_type, source_id) "
                "VALUES (7, '2026-09-30', 'WIP', 'wip_adjustment', 20260930)"
            )
        )
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT change_order_id, amount FROM job_budgets ORDER BY change_order_id"
            )
        ).fetchall()
        assert [(r[0], float(r[1])) for r in rows] == [(1, 1000.0), (2, -400.0)]
        assert conn.execute(text("SELECT closed_on FROM jobs")).scalar() is None
        assert conn.execute(
            text("SELECT as_of, transaction_id FROM wip_entry_locks")
        ).fetchall() == [("2026-09-30", 7)]
    names = {i["name"] for i in inspect(engine).get_indexes("job_budgets")}
    assert {"uq_job_budget", "uq_job_budget_change_order"} <= names

    command.downgrade(cfg, "c7e3a1f5d9b2")
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT amount FROM job_budgets")).fetchall()
        assert [float(r[0]) for r in rows] == [600.0]  # folded back into one row
    assert "wip_entry_locks" not in inspect(engine).get_table_names()
    command.upgrade(cfg, "head")
    engine.dispose()
