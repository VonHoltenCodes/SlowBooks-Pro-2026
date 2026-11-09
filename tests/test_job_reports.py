"""Construction job reports (#556): the WIP schedule with over/under billing,
cost-to-complete, the change-order log, the buyout report and job cost by
vendor.

The service math is checked with exact Decimals on hand-built books; the routes
(JSON / PDF HTML stage / CSV) are checked through the client for a 200 and the
expected header row. The Report Center cards are driven in node — they show in
construction mode only, and the pins still work.
"""

import json
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.accounts import Account, AccountType
from app.models.bills import (
    Bill,
    BillLine,
    BillPayment,
    BillPaymentAllocation,
    BillStatus,
)
from app.models.change_orders import ChangeOrder, ChangeOrderLine
from app.models.contacts import Customer, Vendor
from app.models.cost_codes import CostCode
from app.models.job_costing import JobBudget, JobCostToComplete
from app.models.jobs import Job, SovLine
from app.models.pay_applications import PayApplication, PayApplicationLine
from app.models.purchase_orders import POStatus, PurchaseOrder, PurchaseOrderLine
from app.services import job_reports
from app.services.accounting import create_journal_entry
from app.services.job_costing import ensure_default_cost_types

ROOT = Path(__file__).resolve().parents[1]
needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


# ── builders ────────────────────────────────────────────────────────────


def _customer(db, name="Owner Co"):
    c = Customer(name=name, is_active=True)
    db.add(c)
    db.flush()
    return c


def _cogs(db):
    return db.query(Account).filter(Account.account_type == AccountType.COGS).first()


def _liability(db):
    return (
        db.query(Account).filter(Account.account_type == AccountType.LIABILITY).first()
    )


def _income(db):
    return db.query(Account).filter(Account.account_type == AccountType.INCOME).first()


def _post_cost(db, job_id, account, amount, when, cost_code_id=None, vendor_id=None):
    txn = create_journal_entry(
        db,
        when,
        "job cost",
        [
            {
                "account_id": account.id,
                "debit": Decimal(str(amount)),
                "job_id": job_id,
                "cost_code_id": cost_code_id,
            },
            {"account_id": _liability(db).id, "credit": Decimal(str(amount))},
        ],
    )
    if vendor_id is not None:
        txn.vendor_id = vendor_id
        db.flush()


def _post_cost_split(db, job_amounts, vendor_id):
    """One journal entry with a COGS debit per (job_id, amount), balanced by a
    single credit, with the vendor on the header (as a bill posts)."""
    cogs = _cogs(db)
    total = sum(Decimal(str(a)) for _j, a in job_amounts)
    lines = [
        {"account_id": cogs.id, "debit": Decimal(str(a)), "job_id": jid}
        for jid, a in job_amounts
    ]
    lines.append({"account_id": _liability(db).id, "credit": total})
    txn = create_journal_entry(db, date(2026, 3, 1), "split cost", lines)
    txn.vendor_id = vendor_id
    db.flush()


def _wip_job(db, customer):
    """The spec scenario: contract 100,000 + 10,000 approved CO (revised
    110,000); budget 80,000 + 8,000 change (88,000); cost to date 44,000; one
    approved application billing 60,000; 10% retainage."""
    ensure_default_cost_types(db)
    job = Job(
        customer_id=customer.id,
        name="Tower",
        status="in_progress",
        contract_type="lump_sum",
        contract_amount=Decimal("110000"),
        original_contract_amount=Decimal("100000"),
        retainage_pct=Decimal("10"),
    )
    db.add(job)
    db.flush()
    db.add(
        JobBudget(
            job_id=job.id,
            cost_type="material",
            amount=Decimal("80000"),
            revenue_amount=Decimal("0"),
            source="manual",
        )
    )
    db.add(
        JobBudget(
            job_id=job.id,
            cost_type="material",
            amount=Decimal("8000"),
            revenue_amount=Decimal("0"),
            source="change",
            locked=True,
        )
    )
    _post_cost(db, job.id, _cogs(db), "44000", date(2026, 3, 1))

    sov = SovLine(
        job_id=job.id,
        line_no=1,
        item_no="01",
        description="Work",
        kind="work",
        scheduled_value=Decimal("110000"),
    )
    db.add(sov)
    db.flush()
    app = PayApplication(
        job_id=job.id,
        application_no=1,
        number="APP-1",
        status="approved",
        retainage_pct=Decimal("10"),
        date=date(2026, 3, 31),
    )
    db.add(app)
    db.flush()
    db.add(
        PayApplicationLine(
            pay_application_id=app.id,
            sov_line_id=sov.id,
            line_no=1,
            item_no="01",
            description="Work",
            scheduled_value=Decimal("110000"),
            this_period=Decimal("60000"),
            stored=Decimal("0"),
        )
    )
    db.commit()
    return job


# ── 1. WIP math ───────────────────────────────────────────────────────────


def test_wip_math_exact(db_session, seed_accounts):
    customer = _customer(db_session)
    job = _wip_job(db_session, customer)
    data = job_reports.wip_schedule(db_session, as_of=date(2026, 6, 1))
    assert data["method"] == "cost_to_cost"
    assert len(data["rows"]) == 1
    r = data["rows"][0]
    assert r["job_id"] == job.id
    assert r["has_contract"] is True
    assert r["billed_basis"] == "pay_applications"
    assert r["contract_original"] == 100000.0
    assert r["approved_change_orders"] == 10000.0
    assert r["revised_contract"] == 110000.0
    assert r["estimated_total_cost"] == 88000.0
    assert r["estimated_gross_profit"] == 22000.0
    assert r["margin_pct"] == 20.0
    assert r["cost_to_date"] == 44000.0
    assert r["percent_complete"] == 50.0
    assert r["earned_revenue"] == 55000.0
    assert r["billed_to_date"] == 60000.0
    assert r["over_billing"] == 5000.0
    assert r["under_billing"] == 0.0
    assert r["retainage_outstanding"] == 6000.0  # 60,000 × 10%
    assert r["cost_to_complete"] == 44000.0
    assert r["projected_gross_profit"] == 22000.0
    # totals mirror the one row
    t = data["totals"]
    assert t["revised_contract"] == 110000.0
    assert t["over_billing"] == 5000.0
    assert data["adjustment_accounts"]["over_billing"]
    assert data["adjustment_accounts"]["under_billing"]


def test_wip_percent_capped_at_100(db_session, seed_accounts):
    customer = _customer(db_session)
    ensure_default_cost_types(db_session)
    job = Job(
        customer_id=customer.id,
        name="Overrun",
        status="in_progress",
        contract_type="lump_sum",
        contract_amount=Decimal("100000"),
        original_contract_amount=Decimal("100000"),
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        JobBudget(
            job_id=job.id,
            cost_type="material",
            amount=Decimal("50000"),
            source="manual",
        )
    )
    _post_cost(db_session, job.id, _cogs(db_session), "60000", date(2026, 3, 1))
    db_session.commit()
    r = job_reports.wip_schedule(db_session, as_of=date(2026, 6, 1))["rows"][0]
    assert r["percent_complete"] == 100.0  # 60,000 / 50,000 is capped
    assert r["earned_revenue"] == 100000.0  # revised × 100%


def test_wip_job_with_no_contract_is_blank(db_session, seed_accounts):
    customer = _customer(db_session)
    job = Job(customer_id=customer.id, name="Bare", status="in_progress")
    db_session.add(job)
    db_session.commit()
    r = job_reports.wip_schedule(db_session, as_of=date(2026, 6, 1))["rows"][0]
    assert r["has_contract"] is False
    assert r["revised_contract"] is None
    assert r["percent_complete"] is None
    assert r["over_billing"] is None


def test_wip_closed_jobs_excluded_by_default_and_included_on_request(
    db_session, seed_accounts
):
    customer = _customer(db_session)
    _wip_job(db_session, customer)
    closed = Job(
        customer_id=customer.id,
        name="Done",
        status="closed",
        contract_type="lump_sum",
        contract_amount=Decimal("5000"),
        original_contract_amount=Decimal("5000"),
    )
    db_session.add(closed)
    db_session.commit()
    default = job_reports.wip_schedule(db_session, as_of=date(2026, 6, 1))
    assert [r["job_name"] for r in default["rows"]] == ["Tower"]
    with_closed = job_reports.wip_schedule(
        db_session, as_of=date(2026, 6, 1), include_closed=True
    )
    assert "Done" in [r["job_name"] for r in with_closed["rows"]]


# ── 2. cost-to-complete ─────────────────────────────────────────────────────


def test_cost_to_complete_tree_and_totals(db_session, seed_accounts):
    customer = _customer(db_session)
    ensure_default_cost_types(db_session)
    division = CostCode(
        code="03", name="Concrete", cost_type="material", level="division"
    )
    db_session.add(division)
    db_session.flush()
    section = CostCode(
        code="03 30 00",
        name="Cast-in-Place",
        cost_type="material",
        level="section",
        parent_id=division.id,
    )
    db_session.add(section)
    db_session.flush()
    job = Job(customer_id=customer.id, name="Slab", status="in_progress")
    db_session.add(job)
    db_session.flush()
    db_session.add(
        JobBudget(
            job_id=job.id,
            cost_code_id=section.id,
            cost_type="material",
            amount=Decimal("10000"),
            source="manual",
        )
    )
    # a committed (open) PO line on the section: 4,000 ordered, 1,000 billed
    vendor = Vendor(name="Concrete Co")
    db_session.add(vendor)
    db_session.flush()
    po = PurchaseOrder(
        po_number="PO-1",
        vendor_id=vendor.id,
        status=POStatus.SENT,
        date=date(2026, 2, 1),
        job_id=job.id,
    )
    db_session.add(po)
    db_session.flush()
    db_session.add(
        PurchaseOrderLine(
            purchase_order_id=po.id,
            cost_code_id=section.id,
            cost_type="material",
            amount=Decimal("4000"),
            billed_amount=Decimal("1000"),
            job_id=job.id,
        )
    )
    _post_cost(
        db_session, job.id, _cogs(db_session), "3000", date(2026, 3, 1), section.id
    )
    db_session.commit()

    data = job_reports.cost_to_complete(db_session, as_of=date(2026, 6, 1))
    jobrow = data["jobs"][0]
    # the tree is division › section
    div = jobrow["tree"][0]
    assert div["code"] == "03"
    sec = div["children"][0]
    assert sec["code"] == "03 30 00"
    f = sec["figures"]
    assert f["budget_revised"] == 10000.0
    assert f["committed"] == 3000.0  # 4,000 − 1,000 billed
    assert f["actual"] == 3000.0
    # remaining to budget = max(10000 − 3000 − 3000, 0) = 4000; + committed 3000
    assert f["cost_to_complete"] == 7000.0
    assert f["projected_final"] == 10000.0  # actual 3000 + cost to complete 7000
    assert f["variance"] == 0.0
    assert f["pct_complete"] == 30.0
    # the division rolls the section up
    assert div["figures"]["budget_revised"] == 10000.0
    assert data["totals"]["budget_revised"] == 10000.0
    assert data["totals"]["actual"] == 3000.0


# ── 3. change-order log ─────────────────────────────────────────────────────


def _co(db, job, number, kind, status, funding, lines, **extra):
    co = ChangeOrder(
        job_id=job.id,
        number=number,
        kind=kind,
        status=status,
        funding=funding,
        date=date(2026, 3, 1),
        **extra,
    )
    db.add(co)
    db.flush()
    for i, (cost, sell, ctype) in enumerate(lines):
        co.lines.append(
            ChangeOrderLine(
                line_order=i,
                cost_type=ctype,
                quantity=Decimal("1"),
                unit_cost=Decimal(str(cost)),
                amount=Decimal(str(cost)),
                sell_amount=Decimal(str(sell)),
                markup_pct=Decimal("0"),
            )
        )
    db.flush()
    return co


def test_change_order_log_running_totals(db_session, seed_accounts):
    customer = _customer(db_session)
    job = Job(
        customer_id=customer.id,
        name="Addition",
        status="in_progress",
        contract_type="lump_sum",
        contract_amount=Decimal("110000"),
        original_contract_amount=Decimal("100000"),
    )
    db_session.add(job)
    db_session.flush()
    # an approved contract-funded owner CO that grew the contract by 10,000
    _co(
        db_session,
        job,
        "CO-1",
        "owner",
        "approved",
        "contract",
        [("8000", "10000", "material")],
        approved_at=date(2026, 3, 2),
    )
    # a voided CO (no effect on the running total)
    _co(
        db_session,
        job,
        "CO-2",
        "owner",
        "void",
        "contract",
        [("5000", "6000", "labor")],
    )
    # a pending CO (counts into the pending total, not the running approved)
    _co(
        db_session,
        job,
        "CO-3",
        "owner",
        "pending",
        "contract",
        [("2000", "2000", "material")],
    )
    db_session.commit()

    data = job_reports.change_order_log(db_session)
    jobrow = data["jobs"][0]
    assert jobrow["contract"]["original"] == 100000.0
    assert jobrow["contract"]["approved_changes"] == 10000.0
    assert jobrow["contract"]["revised"] == 110000.0
    running = {r["number"]: r["running_approved"] for r in jobrow["rows"]}
    assert running["CO-1"] == 10000.0
    assert running["CO-2"] == 10000.0  # the void did not move it
    assert running["CO-3"] == 10000.0  # pending does not either
    assert jobrow["pending_total"] == 2000.0
    assert jobrow["counts"]["approved"] == 1
    assert jobrow["counts"]["void"] == 1
    assert jobrow["counts"]["pending"] == 1


# ── 4. buyout totals across jobs ────────────────────────────────────────────


def _bought_po(db, job, vendor, amount, package_id=None):
    po = PurchaseOrder(
        po_number=f"PO-{job.id}-{vendor.id}",
        vendor_id=vendor.id,
        status=POStatus.SENT,
        date=date(2026, 2, 1),
        job_id=job.id,
        subtotal=Decimal(str(amount)),
        trade_package_id=package_id,
    )
    db.add(po)
    db.flush()
    db.add(
        PurchaseOrderLine(
            purchase_order_id=po.id,
            amount=Decimal(str(amount)),
            billed_amount=Decimal("0"),
            job_id=job.id,
        )
    )
    db.flush()
    return po


def test_buyout_totals_across_two_jobs(db_session, seed_accounts):
    customer = _customer(db_session)
    vendor = Vendor(name="Sub Co")
    db_session.add(vendor)
    db_session.flush()
    job1 = Job(customer_id=customer.id, name="J1", status="in_progress")
    job2 = Job(customer_id=customer.id, name="J2", status="in_progress")
    db_session.add_all([job1, job2])
    db_session.flush()
    _bought_po(db_session, job1, vendor, "12000")
    _bought_po(db_session, job2, vendor, "8000")
    db_session.commit()

    data = job_reports.buyout_report(db_session)
    assert len(data["jobs"]) == 2
    assert data["totals"]["bought"] == 20000.0
    # each job keeps its Unassigned package row
    for jobrow in data["jobs"]:
        assert any(r["package_name"] == "Unassigned" for r in jobrow["rows"])


# ── 5. job cost by vendor ───────────────────────────────────────────────────


def test_job_cost_by_vendor(db_session, seed_accounts):
    customer = _customer(db_session)
    job = Job(customer_id=customer.id, name="Mixed", status="in_progress")
    db_session.add(job)
    db_session.flush()
    v1 = Vendor(name="Alpha Supply")
    v2 = Vendor(name="Beta Labor")
    db_session.add_all([v1, v2])
    db_session.flush()

    # vendor 1: a bill with a payment and retainage held
    bill = Bill(
        bill_number="B-1",
        vendor_id=v1.id,
        status=BillStatus.PARTIAL,
        date=date(2026, 3, 1),
        job_id=job.id,
        subtotal=Decimal("10000"),
        total=Decimal("10000"),
        amount_paid=Decimal("4000"),
        balance_due=Decimal("5000"),
        retainage_held=Decimal("1000"),
        retainage_released=Decimal("0"),
    )
    db_session.add(bill)
    db_session.flush()
    db_session.add(BillLine(bill_id=bill.id, amount=Decimal("10000"), job_id=job.id))
    payment = BillPayment(
        vendor_id=v1.id, date=date(2026, 3, 15), amount=Decimal("4000")
    )
    db_session.add(payment)
    db_session.flush()
    db_session.add(
        BillPaymentAllocation(
            bill_payment_id=payment.id, bill_id=bill.id, amount=Decimal("4000")
        )
    )
    # the posted cost for each vendor (header-level vendor_id) and a no-vendor line
    _post_cost(
        db_session,
        job.id,
        _cogs(db_session),
        "10000",
        date(2026, 3, 1),
        vendor_id=v1.id,
    )
    _post_cost(
        db_session, job.id, _cogs(db_session), "6000", date(2026, 3, 1), vendor_id=v2.id
    )
    _post_cost(db_session, job.id, _cogs(db_session), "500", date(2026, 3, 1))
    db_session.commit()

    data = job_reports.job_cost_by_vendor(
        db_session, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
    )
    rows = {r["vendor_name"]: r for r in data["jobs"][0]["rows"]}
    assert rows["Alpha Supply"]["cost"] == 10000.0
    assert rows["Alpha Supply"]["bills_count"] == 1
    assert rows["Alpha Supply"]["paid_to_date"] == 4000.0
    assert rows["Alpha Supply"]["retainage_outstanding"] == 1000.0
    assert rows["Alpha Supply"]["open_ap"] == 5000.0
    assert rows["Beta Labor"]["cost"] == 6000.0
    assert rows["Beta Labor"]["bills_count"] == 0
    assert rows["No vendor"]["cost"] == 500.0
    assert data["totals"]["cost"] == 16500.0
    # No vendor is last
    assert data["jobs"][0]["rows"][-1]["vendor_name"] == "No vendor"


# ── routes: JSON / PDF / CSV answer 200 with the header row ─────────────────


def _construction(client):
    """The WIP schedule is construction mode's (a Business company gets 409)."""
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text


def _setup_books(client, db_session, seed_accounts):
    _construction(client)
    customer = _customer(db_session)
    _wip_job(db_session, customer)
    return customer


_REPORTS = {
    "wip-schedule": "Revised",
    "cost-to-complete": "Cost code",
    "change-order-log": "Number",
    "buyout": "Package",
    "job-cost-by-vendor": "Vendor",
    "cost-to-complete-forecast": "Section",
    "commitment-status": "Commitment",
}


def test_report_routes_json_and_csv(client, db_session, seed_accounts):
    _setup_books(client, db_session, seed_accounts)
    for name, header in _REPORTS.items():
        j = client.get(f"/api/reports/{name}")
        assert j.status_code == 200, (name, j.text)
        body = j.json()
        assert "columns" in body and "totals" in body
        labels = [c["label"] for c in body["columns"]]
        assert header in labels, (name, labels)
        # CSV carries the header row
        csv = client.get(f"/api/reports/{name}/csv")
        assert csv.status_code == 200, (name, csv.text)
        assert header in csv.text, (name, csv.text[:200])


def test_report_routes_pdf(client, db_session, seed_accounts):
    # Skipped where WeasyPrint's native stack is missing (conftest), but the
    # HTML section builders still run to the render boundary.
    _setup_books(client, db_session, seed_accounts)
    for name in _REPORTS:
        pdf = client.get(f"/api/reports/{name}/pdf")
        assert pdf.status_code == 200, (name, pdf.text)


def test_existing_reports_export_csv(client, db_session, seed_accounts):
    _setup_books(client, db_session, seed_accounts)
    for name, header in (
        ("job-profitability", "Income"),
        ("job-budget-vs-actual", "Budget"),
    ):
        csv = client.get(f"/api/reports/{name}/csv")
        assert csv.status_code == 200, (name, csv.text)
        assert header in csv.text, (name, csv.text[:200])


def test_existing_reports_export_pdf(client, db_session, seed_accounts):
    _setup_books(client, db_session, seed_accounts)
    for name in ("job-profitability", "job-budget-vs-actual"):
        r = client.get(f"/api/reports/{name}/pdf")
        assert r.status_code == 200, (name, r.text)


# ── Report Center cards: construction only, pins still work ─────────────────


@needs_node
def test_cards_show_in_construction_only_and_pins_work(tmp_path):
    probe = tmp_path / "cardprobe.cjs"
    probe.write_text(
        """
const fs=require('fs'),vm=require('vm');
const src=fs.readFileSync(process.argv[2],'utf8');
function run(isCon){
  const ctx={console,URLSearchParams,JSON,Object,
    T:s=>s, Terms:{text:s=>s,isNonprofit:()=>false,isConstruction:()=>isCon},
    escapeHtml:s=>String(s==null?'':s), document:{}, window:{}, location:{hash:'#/'}};
  vm.createContext(ctx);
  vm.runInContext(src+'\\nthis.ReportsPage=ReportsPage;',ctx);
  const views=ctx.ReportsPage._cards().map(c=>c.view);
  // pins reorder: pin a card and confirm it precedes an unpinned one
  ctx.ReportsPage._pins=['job-cost-by-vendor'];
  const grid=ctx.ReportsPage._cardGrid();
  const pinned=grid.indexOf('report-card-job-cost-by-vendor');
  const unpinned=grid.indexOf('report-card-wip-schedule');
  return {views, pinned, unpinned};
}
console.log(JSON.stringify({con:run(true), biz:run(false)}));
""",
        encoding="utf-8",
    )
    out = subprocess.run(
        ["node", str(probe), str(ROOT / "app/static/js/reports.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    five = [
        "wip-schedule",
        "cost-to-complete",
        "change-order-log",
        "buyout",
        "job-cost-by-vendor",
    ]
    added = ["cost-to-complete-forecast", "commitment-status"]
    assert all(v in res["con"]["views"] for v in five + added)
    assert all(v not in res["biz"]["views"] for v in five + added)
    # the Construction group leads: three existing, then the five new
    assert (
        res["con"]["views"][:8]
        == [
            "job-profitability",
            "job-budget-vs-actual",
            "profit-loss-by-job",
        ]
        + five
    )
    # a pinned card precedes an unpinned one in the rendered grid
    assert res["con"]["pinned"] >= 0 and res["con"]["unpinned"] >= 0
    assert res["con"]["pinned"] < res["con"]["unpinned"]


# ── Fixes (#556 review round) ───────────────────────────────────────────────


def test_wip_as_of_before_billing_reads_zero(db_session, seed_accounts):
    """As of a day before the cost, the billing and the change order, the row
    shows no changes, no billing and no over-billing."""
    customer = _customer(db_session)
    ensure_default_cost_types(db_session)
    job = Job(
        customer_id=customer.id,
        name="Early",
        status="in_progress",
        contract_type="lump_sum",
        contract_amount=Decimal("110000"),  # after the CO below
        original_contract_amount=Decimal("100000"),
        retainage_pct=Decimal("10"),
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        JobBudget(
            job_id=job.id,
            cost_type="material",
            amount=Decimal("80000"),
            source="manual",
        )
    )
    _post_cost(db_session, job.id, _cogs(db_session), "44000", date(2026, 3, 1))
    # an owner, contract-funded CO approved after the as_of date
    _co(
        db_session,
        job,
        "CO-1",
        "owner",
        "approved",
        "contract",
        [("8000", "10000", "material")],
        approved_at=datetime(2026, 4, 1, 9, 0),
    )
    # an approved application billing 60,000, dated after the as_of date
    sov = SovLine(
        job_id=job.id,
        line_no=1,
        item_no="01",
        kind="work",
        scheduled_value=Decimal("110000"),
    )
    db_session.add(sov)
    db_session.flush()
    app = PayApplication(
        job_id=job.id,
        application_no=1,
        number="E-1",
        status="approved",
        retainage_pct=Decimal("10"),
        date=date(2026, 3, 31),
    )
    db_session.add(app)
    db_session.flush()
    db_session.add(
        PayApplicationLine(
            pay_application_id=app.id,
            sov_line_id=sov.id,
            line_no=1,
            scheduled_value=Decimal("110000"),
            this_period=Decimal("60000"),
            stored=Decimal("0"),
        )
    )
    db_session.commit()

    r = job_reports.wip_schedule(db_session, as_of=date(2026, 2, 1))["rows"][0]
    assert r["billed_to_date"] == 0.0
    assert r["over_billing"] == 0.0
    assert r["under_billing"] == 0.0  # nothing earned yet either
    assert r["approved_change_orders"] == 0.0
    assert r["revised_contract"] == 100000.0  # the CO is backed out as of the date
    assert r["cost_to_date"] == 0.0
    assert r["billed_basis"] == "posted_revenue"

    # and as of today it reads the full, current figures
    today = job_reports.wip_schedule(db_session)["rows"][0]
    assert today["approved_change_orders"] == 10000.0
    assert today["revised_contract"] == 110000.0
    assert today["billed_to_date"] == 60000.0
    assert today["billed_basis"] == "pay_applications"


def test_wip_overrun_with_no_forecast_estimates_actual(db_session, seed_accounts):
    """Past budget with nothing left to do (the default cost to complete is
    floored at 0) the estimate is the actual cost, so estimated and projected
    gross profit agree and the job reads 100%."""
    customer = _customer(db_session)
    ensure_default_cost_types(db_session)
    job = Job(
        customer_id=customer.id,
        name="Overrun2",
        status="in_progress",
        contract_type="lump_sum",
        contract_amount=Decimal("100000"),
        original_contract_amount=Decimal("100000"),
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        JobBudget(
            job_id=job.id,
            cost_type="material",
            amount=Decimal("50000"),
            source="manual",
        )
    )
    _post_cost(db_session, job.id, _cogs(db_session), "60000", date(2026, 3, 1))
    db_session.commit()
    r = job_reports.wip_schedule(db_session, as_of=date(2026, 6, 1))["rows"][0]
    assert r["percent_complete"] == 100.0
    assert r["cost_to_complete"] == 0.0  # floored
    assert r["estimated_total_cost"] == 60000.0  # actual + 0
    assert r["estimated_gross_profit"] == 40000.0
    assert r["projected_gross_profit"] == 40000.0
    assert r["projected_loss"] == 0.0


def test_wip_zero_budget_row(db_session, seed_accounts):
    customer = _customer(db_session)
    ensure_default_cost_types(db_session)
    job = Job(
        customer_id=customer.id,
        name="NoBudget",
        status="in_progress",
        contract_type="lump_sum",
        contract_amount=Decimal("20000"),
        original_contract_amount=Decimal("20000"),
    )
    db_session.add(job)
    db_session.flush()
    _post_cost(db_session, job.id, _cogs(db_session), "5000", date(2026, 3, 1))
    db_session.commit()
    r = job_reports.wip_schedule(db_session, as_of=date(2026, 6, 1))["rows"][0]
    # no basis and cost short of the contract: listed, nothing earned
    assert r["excluded"] is True
    assert r["percent_complete"] is None
    assert r["earned_revenue"] == 0.0
    assert r["cost_to_date"] == 5000.0
    assert r["cost_to_complete"] == 0.0  # floored, no budget to finish against


def _division(db, code, name):
    c = CostCode(code=code, name=name, cost_type="material", level="division")
    db.add(c)
    db.flush()
    return c


def _section(db, code, name, parent):
    c = CostCode(
        code=code, name=name, cost_type="material", level="section", parent_id=parent.id
    )
    db.add(c)
    db.flush()
    return c


def test_cost_to_complete_empty_sibling_and_footed_totals(db_session, seed_accounts):
    """Two divisions, one with an empty sibling section: no crash, and the job
    totals foot the clamped rows (0 + 8,000 = 8,000; variance −5,000)."""
    customer = _customer(db_session)
    ensure_default_cost_types(db_session)
    div_a = _division(db_session, "03", "Concrete")
    a1 = _section(db_session, "03 30 00", "Cast-in-Place", div_a)
    _section(db_session, "03 40 00", "Precast", div_a)  # empty sibling
    div_b = _division(db_session, "26", "Electrical")
    b1 = _section(db_session, "26 05 00", "Basic Materials", div_b)
    job = Job(customer_id=customer.id, name="Two", status="in_progress")
    db_session.add(job)
    db_session.flush()
    # division A: no budget, 5,000 actual → over, cost to complete 0
    _post_cost(db_session, job.id, _cogs(db_session), "5000", date(2026, 3, 1), a1.id)
    # division B: 8,000 budget, no actual → cost to complete 8,000
    db_session.add(
        JobBudget(
            job_id=job.id,
            cost_code_id=b1.id,
            cost_type="material",
            amount=Decimal("8000"),
            source="manual",
        )
    )
    db_session.commit()

    data = job_reports.cost_to_complete(db_session, as_of=date(2026, 6, 1))
    job_data = data["jobs"][0]
    # the empty sibling is dropped, not crashed on
    div_a_node = next(n for n in job_data["tree"] if n["code"] == "03")
    assert [c["code"] for c in div_a_node["children"]] == ["03 30 00"]
    t = job_data["totals"]
    assert t["budget_revised"] == 8000.0
    assert t["actual"] == 5000.0
    assert t["cost_to_complete"] == 8000.0  # 0 (div A) + 8,000 (div B)
    assert t["variance"] == -5000.0  # −5,000 (div A) + 0 (div B)
    # grand totals (one job) match
    assert data["totals"]["cost_to_complete"] == 8000.0
    assert data["totals"]["variance"] == -5000.0


def test_job_cost_by_vendor_prorates_a_split_bill(db_session, seed_accounts):
    customer = _customer(db_session)
    j1 = Job(customer_id=customer.id, name="J1", status="in_progress")
    j2 = Job(customer_id=customer.id, name="J2", status="in_progress")
    db_session.add_all([j1, j2])
    db_session.flush()
    vendor = Vendor(name="Split Co")
    db_session.add(vendor)
    db_session.flush()
    # one bill split 6,000 to J1 and 4,000 to J2 (header carries no job)
    bill = Bill(
        bill_number="S-1",
        vendor_id=vendor.id,
        status=BillStatus.PARTIAL,
        date=date(2026, 3, 1),
        subtotal=Decimal("10000"),
        total=Decimal("10000"),
        amount_paid=Decimal("5000"),
        balance_due=Decimal("10000"),
        retainage_held=Decimal("1000"),
        retainage_released=Decimal("0"),
    )
    db_session.add(bill)
    db_session.flush()
    db_session.add(BillLine(bill_id=bill.id, amount=Decimal("6000"), job_id=j1.id))
    db_session.add(BillLine(bill_id=bill.id, amount=Decimal("4000"), job_id=j2.id))
    payment = BillPayment(
        vendor_id=vendor.id, date=date(2026, 3, 15), amount=Decimal("5000")
    )
    db_session.add(payment)
    db_session.flush()
    db_session.add(
        BillPaymentAllocation(
            bill_payment_id=payment.id, bill_id=bill.id, amount=Decimal("5000")
        )
    )
    # posted cost, one JE with a line per job, the bill's vendor on the header
    _post_cost_split(db_session, [(j1.id, "6000"), (j2.id, "4000")], vendor.id)
    db_session.commit()

    data = job_reports.job_cost_by_vendor(
        db_session, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
    )
    by_job = {j["job_name"]: j for j in data["jobs"]}
    r1 = by_job["J1"]["rows"][0]
    r2 = by_job["J2"]["rows"][0]
    assert r1["bills_count"] == 0.6 and r2["bills_count"] == 0.4
    assert r1["paid_to_date"] == 3000.0 and r2["paid_to_date"] == 2000.0
    assert r1["retainage_outstanding"] == 600.0 and r2["retainage_outstanding"] == 400.0
    assert r1["open_ap"] == 6000.0 and r2["open_ap"] == 4000.0
    # the grand totals foot the whole bill
    g = data["totals"]
    assert g["cost"] == 10000.0
    assert g["bills_count"] == 1.0
    assert g["paid_to_date"] == 5000.0
    assert g["retainage_outstanding"] == 1000.0
    assert g["open_ap"] == 10000.0


def test_existing_report_csv_figures_agree_with_json(client, db_session, seed_accounts):
    """The Job Profitability CSV carries the same money figures the JSON does,
    including a negative-margin job (income under cost)."""
    customer = _customer(db_session)
    job = Job(customer_id=customer.id, name="Loser", status="in_progress")
    db_session.add(job)
    db_session.flush()
    # income 10,000, cost 44,000 → net −34,000, margin negative
    create_journal_entry(
        db_session,
        date(2026, 3, 1),
        "revenue",
        [
            {"account_id": _liability(db_session).id, "debit": Decimal("10000")},
            {
                "account_id": _income(db_session).id,
                "credit": Decimal("10000"),
                "job_id": job.id,
            },
        ],
    )
    _post_cost(db_session, job.id, _cogs(db_session), "44000", date(2026, 3, 1))
    db_session.commit()

    jp = client.get(
        "/api/reports/job-profitability?start_date=2026-01-01&end_date=2026-12-31"
    ).json()
    row = next(r for r in jp["jobs"] if r["job_name"] == "Loser")
    assert row["net_income"] == -34000.0
    assert row["margin_pct"] is not None and row["margin_pct"] < 0
    csv = client.get(
        "/api/reports/job-profitability/csv?start_date=2026-01-01&end_date=2026-12-31"
    ).text
    assert "-34000.00" in csv  # the net money cell, not just the header word
    assert "10000.00" in csv  # income


def test_budget_vs_actual_csv_figure_agrees_with_service(
    client, db_session, seed_accounts
):
    from app.services.job_costing import budget_vs_actual_all_jobs

    customer = _customer(db_session)
    job = Job(customer_id=customer.id, name="BVA", status="in_progress")
    db_session.add(job)
    db_session.flush()
    db_session.add(
        JobBudget(
            job_id=job.id,
            cost_type="material",
            amount=Decimal("12000"),
            source="manual",
        )
    )
    _post_cost(db_session, job.id, _cogs(db_session), "3000", date(2026, 3, 1))
    db_session.commit()

    svc = budget_vs_actual_all_jobs(db_session, date(2026, 1, 1), date(2026, 12, 31))
    row = next(r for r in svc if r["job_name"] == "BVA")
    assert row["revised"] == 12000.0 and row["actual"] == 3000.0
    csv = client.get(
        "/api/reports/job-budget-vs-actual/csv?start_date=2026-01-01&end_date=2026-12-31"
    ).text
    assert "12000.00" in csv and "3000.00" in csv


def test_budget_vs_actual_exports_honour_customer_filter(
    client, db_session, seed_accounts
):
    c1 = _customer(db_session, "C1")
    c2 = _customer(db_session, "C2")
    j1 = Job(customer_id=c1.id, name="C1 Job", status="in_progress")
    j2 = Job(customer_id=c2.id, name="C2 Job", status="in_progress")
    db_session.add_all([j1, j2])
    db_session.flush()
    db_session.add(
        JobBudget(
            job_id=j1.id, cost_type="material", amount=Decimal("7777"), source="manual"
        )
    )
    db_session.add(
        JobBudget(
            job_id=j2.id, cost_type="material", amount=Decimal("8888"), source="manual"
        )
    )
    db_session.commit()
    csv = client.get(
        f"/api/reports/job-budget-vs-actual/csv?start_date=2026-01-01&end_date=2026-12-31&customer_id={c1.id}"
    ).text
    assert "7777.00" in csv
    assert "8888.00" not in csv  # the other customer's job is filtered out


def test_job_cost_by_vendor_reversed_dates_refused(client, db_session, seed_accounts):
    _customer(db_session)
    db_session.commit()
    for ext in ("", "/csv"):
        r = client.get(
            f"/api/reports/job-cost-by-vendor{ext}?start_date=2026-12-31&end_date=2026-01-01"
        )
        assert r.status_code == 400, (ext, r.text)


# ── WIP review round: forecast, projected loss, adjusting entry, commitments ──


def _contract_job(db, customer, name, contract, budget=None, cost=None, when=None):
    """A lump-sum job with a whole-type material budget and posted cost, no
    pay applications (billing falls back to posted revenue, here 0)."""
    ensure_default_cost_types(db)
    job = Job(
        customer_id=customer.id,
        name=name,
        status="in_progress",
        contract_type="lump_sum",
        contract_amount=Decimal(str(contract)),
        original_contract_amount=Decimal(str(contract)),
    )
    db.add(job)
    db.flush()
    if budget is not None:
        db.add(
            JobBudget(
                job_id=job.id,
                cost_type="material",
                amount=Decimal(str(budget)),
                source="manual",
            )
        )
    if cost is not None:
        _post_cost(db, job.id, _cogs(db), str(cost), when or date(2026, 3, 1))
    db.commit()
    return job


def _wip(db):
    return job_reports.wip_schedule(db, as_of=date(2026, 6, 1))


def test_over_budget_job_with_cost_to_complete_is_not_100_percent(
    db_session, seed_accounts
):
    """Budget 50k, actual 60k: by default nothing is left (100%). Entering 20k
    still to spend makes the estimate 80k and the job 75% complete."""
    customer = _customer(db_session)
    job = _contract_job(db_session, customer, "Past budget", 100000, 50000, 60000)
    assert _wip(db_session)["rows"][0]["percent_complete"] == 100.0

    job_reports.set_ctc_entries(
        db_session, job, [{"cost_code_id": None, "cost_type": "", "amount": 20000}]
    )
    r = _wip(db_session)["rows"][0]
    assert r["cost_to_complete"] == 20000.0
    assert r["estimated_total_cost"] == 80000.0
    assert r["percent_complete"] == 75.0
    assert r["earned_revenue"] == 75000.0
    assert r["percent_complete"] < 100.0


def test_forecast_defaults_to_budget_less_actual_floored_per_cell(
    db_session, seed_accounts
):
    customer = _customer(db_session)
    ensure_default_cost_types(db_session)
    job = Job(customer_id=customer.id, name="Cells", status="in_progress")
    db_session.add(job)
    db_session.flush()
    div = _division(db_session, "03", "Concrete")
    sec_a = _section(db_session, "03 30 00", "Slab", div)
    sec_b = _section(db_session, "03 40 00", "Precast", div)
    sub = CostCode(
        code="03 30 00.10",
        name="Slab edge",
        cost_type="material",
        parent_id=sec_a.id,
    )
    db_session.add(sub)
    db_session.flush()
    for code, amt in ((sec_a, "10000"), (sec_b, "10000")):
        db_session.add(
            JobBudget(
                job_id=job.id,
                cost_code_id=code.id,
                cost_type="material",
                amount=Decimal(amt),
                source="manual",
            )
        )
    # section A is over (12k on a sub-code that rolls up to it), B has 2k spent
    _post_cost(db_session, job.id, _cogs(db_session), "12000", date(2026, 3, 1), sub.id)
    _post_cost(
        db_session, job.id, _cogs(db_session), "2000", date(2026, 3, 1), sec_b.id
    )
    db_session.commit()

    f = job_reports.ctc_forecast(db_session, job, date(2026, 6, 1))
    rows = {r["cost_code_id"]: r for r in f["rows"]}
    assert len(rows) == 2  # the sub-code rolls into its section
    assert rows[sec_a.id]["actual"] == 12000.0
    assert rows[sec_a.id]["default_cost_to_complete"] == 0.0  # floored, not -2,000
    assert rows[sec_b.id]["default_cost_to_complete"] == 8000.0
    assert rows[sec_b.id]["entered"] is None
    # the total is the sum of the floored cells, not budget − actual
    assert f["totals"]["cost_to_complete"] == 8000.0
    assert f["totals"]["projected_final"] == 22000.0

    # an entry replaces the default for its cell, and clearing restores it
    job_reports.set_ctc_entries(
        db_session,
        job,
        [{"cost_code_id": sec_a.id, "cost_type": "material", "amount": 1500}],
    )
    f = job_reports.ctc_forecast(db_session, job, date(2026, 6, 1))
    a = {r["cost_code_id"]: r for r in f["rows"]}[sec_a.id]
    assert a["entered"] == 1500.0 and a["cost_to_complete"] == 1500.0
    assert f["totals"]["cost_to_complete"] == 9500.0
    job_reports.set_ctc_entries(
        db_session,
        job,
        [{"cost_code_id": sec_a.id, "cost_type": "material", "amount": None}],
    )
    f = job_reports.ctc_forecast(db_session, job, date(2026, 6, 1))
    assert f["totals"]["cost_to_complete"] == 8000.0


def test_forecast_entry_is_persisted_and_upserted(db_session, seed_accounts):
    from app.models.job_costing import JobCostToComplete

    customer = _customer(db_session)
    job = _contract_job(db_session, customer, "Upsert", 10000, 5000, 1000)
    cell = [{"cost_code_id": None, "cost_type": "", "amount": 3000}]
    job_reports.set_ctc_entries(db_session, job, cell)
    job_reports.set_ctc_entries(db_session, job, [{**cell[0], "amount": 3500}])
    rows = db_session.query(JobCostToComplete).filter_by(job_id=job.id).all()
    assert len(rows) == 1 and rows[0].amount == Decimal("3500.00")


def test_forecast_routes_save_and_validate(client, db_session, seed_accounts):
    customer = _customer(db_session)
    job = _contract_job(db_session, customer, "Routes", 100000, 50000, 20000)
    div = _division(db_session, "05", "Metals")
    sec = _section(db_session, "05 12 00", "Steel", div)
    db_session.commit()
    url = f"/api/jobs/{job.id}/cost-to-complete-forecast"

    got = client.get(url)
    assert got.status_code == 200
    assert got.json()["totals"]["cost_to_complete"] == 30000.0  # 50k − 20k

    ok = client.put(
        url,
        json={"entries": [{"cost_code_id": None, "cost_type": "", "amount": "45000"}]},
    )
    assert ok.status_code == 200
    assert ok.json()["totals"]["cost_to_complete"] == 45000.0
    assert client.get(url).json()["rows"][0]["entered"] == 45000.0

    bad = [
        {"cost_code_id": None, "cost_type": "", "amount": "-1"},  # negative
        {"cost_code_id": sec.id, "cost_type": "nope", "amount": "1"},  # unknown type
        {"cost_code_id": div.id + 999, "cost_type": "material", "amount": "1"},
        {"cost_code_id": None, "cost_type": "material", "amount": "1"},
    ]
    for entry in bad:
        r = client.put(url, json={"entries": [entry]})
        assert r.status_code == 422, (entry, r.text)
    # a code under a section is refused: enter it on the section
    sub = CostCode(
        code="05 12 00.1", name="Beams", cost_type="material", parent_id=sec.id
    )
    db_session.add(sub)
    db_session.commit()
    r = client.put(
        url,
        json={
            "entries": [
                {"cost_code_id": sub.id, "cost_type": "material", "amount": "1"}
            ]
        },
    )
    assert r.status_code == 422


def test_projected_loss_is_recognised_in_full(db_session, seed_accounts):
    """Contract 100k; 60k spent and 60k still to spend: projected cost 120k, a
    20k loss. At 50% complete 10k of it is already in earned revenue (50k earned
    on 60k cost), so the provision books the other 10k and the whole 20k is
    recognised now."""
    customer = _customer(db_session)
    job = _contract_job(db_session, customer, "Loser", 100000, 90000, 60000)
    job_reports.set_ctc_entries(
        db_session, job, [{"cost_code_id": None, "cost_type": "", "amount": 60000}]
    )
    r = _wip(db_session)["rows"][0]
    assert r["estimated_total_cost"] == 120000.0
    assert r["percent_complete"] == 50.0
    assert r["earned_revenue"] == 50000.0
    assert r["projected_loss"] == 20000.0
    assert r["loss_provision"] == 10000.0
    gp_to_date = r["earned_revenue"] - r["cost_to_date"]  # -10,000 already
    assert gp_to_date - r["loss_provision"] == -r["projected_loss"]
    # and it is in the entry
    entry = _wip(db_session)["adjusting_entry"]
    accounts = {ln["account"]: ln for ln in entry["lines"]}
    assert accounts[job_reports.WIP_LOSS_EXPENSE_ACCOUNT]["debit"] == 10000.0
    assert accounts[job_reports.WIP_LOSS_ACCOUNT]["credit"] == 10000.0


def test_no_loss_when_projected_cost_is_under_the_contract(db_session, seed_accounts):
    customer = _customer(db_session)
    _contract_job(db_session, customer, "Fine", 100000, 80000, 40000)
    r = _wip(db_session)["rows"][0]
    assert r["projected_loss"] == 0.0 and r["loss_provision"] == 0.0


def test_adjusting_entry_per_job_and_net(db_session, seed_accounts):
    customer = _customer(db_session)
    _wip_job(db_session, customer)  # over-billed 5,000
    _contract_job(db_session, customer, "Under", 100000, 50000, 25000)  # under 50,000
    entry = _wip(db_session)["adjusting_entry"]
    assert entry["posted"] is False

    jobs = {j["job_name"]: j for j in entry["jobs"]}
    assert jobs["Tower"]["over_billing"] == 5000.0
    assert jobs["Tower"]["under_billing"] == 0.0
    assert jobs["Tower"]["net_under_over"] == -5000.0
    assert jobs["Under"]["under_billing"] == 50000.0
    # a job's own lines balance
    for j in jobs.values():
        assert sum(ln["debit"] for ln in j["lines"]) == sum(
            ln["credit"] for ln in j["lines"]
        )
    tower = {ln["account"]: ln for ln in jobs["Tower"]["lines"]}
    assert tower[job_reports.WIP_REVENUE_ACCOUNT]["debit"] == 5000.0
    assert tower[job_reports.WIP_OVERBILLING_ACCOUNT]["credit"] == 5000.0

    t = entry["totals"]
    assert t["under_billing"] == 50000.0 and t["over_billing"] == 5000.0
    assert t["net_under_over"] == 45000.0
    net = {ln["account"]: ln for ln in entry["lines"]}
    assert net[job_reports.WIP_UNDERBILLING_ACCOUNT]["debit"] == 50000.0
    assert net[job_reports.WIP_OVERBILLING_ACCOUNT]["credit"] == 5000.0
    assert net[job_reports.WIP_REVENUE_ACCOUNT]["credit"] == 45000.0
    assert entry["total_debits"] == entry["total_credits"] == 50000.0


def test_adjusting_entry_empty_when_nothing_to_adjust(db_session, seed_accounts):
    customer = _customer(db_session)
    _contract_job(db_session, customer, "Quiet", 100000, 50000)  # no cost, no billing
    entry = _wip(db_session)["adjusting_entry"]
    assert entry["jobs"] == [] and entry["lines"] == []
    assert entry["total_debits"] == entry["total_credits"] == 0.0


def test_wip_csv_carries_the_adjusting_entry(client, db_session, seed_accounts):
    _construction(client)
    customer = _customer(db_session)
    _wip_job(db_session, customer)
    csv = client.get("/api/reports/wip-schedule/csv?as_of=2026-06-01").text
    assert "WIP Adjusting Entry" in csv
    assert job_reports.WIP_OVERBILLING_ACCOUNT in csv
    assert "Net entry" in csv


def test_wip_pdf_sections_include_the_adjusting_entry(db_session, seed_accounts):
    """The PDF stage is checked on the section builders, which run without
    WeasyPrint's native stack."""
    from app.routes.reports import construction

    customer = _customer(db_session)
    _wip_job(db_session, customer)
    data = _wip(db_session)
    sections = construction._wip_sections(data) + construction._wip_entry_sections(data)
    assert [s["title"] for s in sections] == ["WIP Schedule", "WIP Adjusting Entry"]
    entry = sections[1]
    assert entry["columns"] == ["Job", "Account", "Debit", "Credit"]
    texts = [c for r in entry["rows"] for c in r["cells"]]
    # the account by number and name, as the chart has it (2060 is free)
    assert f"2060 {job_reports.WIP_OVERBILLING_ACCOUNT}" in texts
    assert "Net entry" in texts


def _commitment(db, job, vendor, number, subtotal, status=POStatus.SENT, when=None):
    po = PurchaseOrder(
        po_number=number,
        vendor_id=vendor.id,
        status=status,
        date=when or date(2026, 2, 1),
        job_id=job.id,
        subtotal=Decimal(str(subtotal)),
        commitment_type="subcontract",
    )
    db.add(po)
    db.flush()
    return po


def _po_bill(db, po, number, subtotal, held="0", released="0", when=None):
    db.add(
        Bill(
            bill_number=number,
            vendor_id=po.vendor_id,
            status=BillStatus.UNPAID,
            po_id=po.id,
            date=when or date(2026, 3, 1),
            job_id=po.job_id,
            subtotal=Decimal(str(subtotal)),
            total=Decimal(str(subtotal)),
            balance_due=Decimal(str(subtotal)),
            retainage_held=Decimal(held),
            retainage_released=Decimal(released),
        )
    )
    db.flush()


def test_commitment_status_per_commitment_and_totals(db_session, seed_accounts):
    customer = _customer(db_session)
    job = Job(customer_id=customer.id, name="Subs", status="in_progress")
    db_session.add(job)
    db_session.flush()
    v1, v2 = Vendor(name="Framer"), Vendor(name="Roofer")
    db_session.add_all([v1, v2])
    db_session.flush()
    sub = _commitment(db_session, job, v1, "SC-1", 10000)
    _po_bill(db_session, sub, "F-1", 4000, held="400")
    _po_bill(db_session, sub, "F-2", 2000, held="200", when=date(2026, 5, 1))
    # a retainage release pays money already billed: not new billing
    _po_bill(db_session, sub, "RET-SC-1", 300, released="300", when=date(2026, 5, 15))
    closed = _commitment(db_session, job, v2, "SC-2", 5000, status=POStatus.CLOSED)
    _po_bill(db_session, closed, "R-1", 5000, held="500")
    _commitment(db_session, job, v2, "SC-3", 9999, status=POStatus.DRAFT)
    db_session.commit()

    data = job_reports.commitment_status(db_session, as_of=date(2026, 6, 1))
    rows = {r["number"]: r for r in data["jobs"][0]["rows"]}
    assert set(rows) == {"SC-1", "SC-2"}  # the draft is not a commitment yet
    sc1 = rows["SC-1"]
    assert sc1["committed"] == 10000.0
    assert sc1["billed"] == 6000.0
    assert sc1["retainage_held"] == 300.0  # 600 held − 300 released
    assert sc1["remaining"] == 4000.0
    assert rows["SC-2"]["remaining"] == 0.0  # closed
    assert rows["SC-2"]["retainage_held"] == 500.0
    t = data["jobs"][0]["totals"]
    assert t == {
        "committed": 15000.0,
        "received": 0.0,  # no delivery tickets
        "billed": 11000.0,
        "retainage_held": 800.0,
        "remaining": 4000.0,
    }
    assert data["totals"] == t


def test_commitment_status_as_of_dates_bills_and_commitments(db_session, seed_accounts):
    customer = _customer(db_session)
    job = Job(customer_id=customer.id, name="Dated", status="in_progress")
    db_session.add(job)
    db_session.flush()
    v = Vendor(name="Late Sub")
    db_session.add(v)
    db_session.flush()
    po = _commitment(db_session, job, v, "SC-9", 8000, when=date(2026, 2, 1))
    _po_bill(db_session, po, "L-1", 3000, held="300", when=date(2026, 4, 1))
    _commitment(db_session, job, v, "SC-10", 1000, when=date(2026, 7, 1))
    db_session.commit()

    early = job_reports.commitment_status(db_session, as_of=date(2026, 3, 1))
    rows = early["jobs"][0]["rows"]
    assert [r["number"] for r in rows] == ["SC-9"]  # SC-10 is dated later
    assert rows[0]["billed"] == 0.0 and rows[0]["remaining"] == 8000.0
    late = job_reports.commitment_status(db_session, as_of=date(2026, 6, 1))
    assert late["jobs"][0]["rows"][0]["billed"] == 3000.0


def test_commitment_status_and_forecast_exports(client, db_session, seed_accounts):
    customer = _customer(db_session)
    job = _contract_job(db_session, customer, "Export", 100000, 50000, 10000)
    v = Vendor(name="Exp Sub")
    db_session.add(v)
    db_session.flush()
    po = _commitment(db_session, job, v, "SC-X", 7000)
    _po_bill(db_session, po, "X-1", 2000, held="200")
    db_session.commit()
    client.put("/api/settings", json={"company_type": "construction"})

    cs = client.get("/api/reports/commitment-status/csv?as_of=2026-06-01").text
    assert "Retainage held" in cs and "SC-X" in cs and "Total Export" in cs
    fc = client.get("/api/reports/cost-to-complete-forecast/csv?as_of=2026-06-01").text
    assert "Cost to complete" in fc and "Total Export" in fc

    from app.routes.reports import construction

    for data, title in (
        (
            job_reports.commitment_status(db_session, as_of=date(2026, 6, 1)),
            "Commitment Status",
        ),
        (
            job_reports.ctc_forecast_report(db_session, as_of=date(2026, 6, 1)),
            "Cost-to-Complete Forecast",
        ),
    ):
        section = construction._per_job_grid_sections(data, title, "x")[0]
        assert (
            section["title"] == title
            and section["rows"][-1]["cells"][0] == "Grand total"
        )


def test_wip_no_basis_loss_is_recognised_once(db_session, seed_accounts):
    """Contract 100k, no budget, no forecast, cost 120k: 100% complete, 100k
    earned, a true 20k loss already in the earned-less-cost, no provision on
    top (it used to book the loss twice)."""
    customer = _customer(db_session)
    _contract_job(db_session, customer, "No basis", 100000, None, 120000)
    data = _wip(db_session)
    r = data["rows"][0]
    assert r["percent_complete"] == 100.0
    assert r["earned_revenue"] == 100000.0
    assert r["projected_loss"] == 20000.0
    assert r["loss_provision"] == 0.0
    accounts = {ln["account"] for ln in data["adjusting_entry"]["lines"]}
    assert job_reports.WIP_LOSS_EXPENSE_ACCOUNT not in accounts
    assert job_reports.WIP_LOSS_ACCOUNT not in accounts
    # earned 100k, nothing billed: the entry books the 100k under-billing only
    assert data["adjusting_entry"]["totals"]["under_billing"] == 100000.0


def test_wip_no_basis_below_contract_is_not_earned(db_session, seed_accounts):
    """Contract 1,000,000, cost 10,000, no budget or forecast, nothing billed:
    no revenue is booked; the job is listed with a warning and out of the
    adjusting entry. Its contract, cost and billing still count in the totals,
    so those columns foot (#577); earned, over/under and the estimates do not."""
    customer = _customer(db_session)
    _contract_job(db_session, customer, "No budget yet", 1000000, None, 10000)
    _contract_job(db_session, customer, "Budgeted", 100000, 50000, 25000)
    data = _wip(db_session)
    r = next(x for x in data["rows"] if x["job_name"] == "No budget yet")
    assert r["earned_revenue"] == 0.0 and r["under_billing"] == 0.0
    assert r["percent_complete"] is None
    assert any(
        "No budget yet" in w and "no budget or forecast; nothing earned" in w
        for w in data["warnings"]
    )
    assert [j["job_name"] for j in data["adjusting_entry"]["jobs"]] == ["Budgeted"]
    t = data["totals"]
    # contract, cost and billing foot across both rows
    assert t["contract_original"] == 1100000.0
    assert t["revised_contract"] == 1100000.0
    assert t["cost_to_date"] == 35000.0
    assert t["billed_to_date"] == 0.0
    assert t["retainage_outstanding"] == 0.0
    # the earned side and the estimates carry the budgeted job only
    assert t["earned_revenue"] == 50000.0
    assert t["under_billing"] == 50000.0
    assert t["over_billing"] == 0.0
    assert t["loss_provision"] == 0.0
    assert t["estimated_total_cost"] == 50000.0
    assert t["estimated_gross_profit"] == 50000.0
    assert t["cost_to_complete"] == 25000.0
    # margin reads against the contracts that have an estimate
    assert t["margin_pct"] == 50.0
    assert data["adjusting_entry"]["totals"]["under_billing"] == 50000.0
    b = next(x for x in data["rows"] if x["job_name"] == "Budgeted")
    assert b["percent_complete"] == 50.0 and "excluded" not in b
    # every summed column's Total equals the sum of what the rows print
    for key in job_reports._WIP_SUMMED:
        printed = sum(x[key] for x in data["rows"] if x[key] is not None)
        assert t[key] == printed, key


def test_wip_warnings_print_in_screen_pdf_and_csv(client, db_session, seed_accounts):
    from app.routes.reports import construction

    _construction(client)
    customer = _customer(db_session)
    _contract_job(db_session, customer, "No budget yet", 1000000, None, 10000)
    note = "no budget or forecast; nothing earned"
    assert note in construction._wip_sections(_wip(db_session))[0]["note"]
    csv = client.get("/api/reports/wip-schedule/csv?as_of=2026-06-01").text
    assert note in csv
    js = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")
    start = js.index("ReportsPage.wipSchedule = ")
    assert "data.warnings" in js[start : js.index("ReportsPage.costToComplete", start)]


def test_wip_warns_when_forecast_entered_after_as_of(db_session, seed_accounts):
    customer = _customer(db_session)
    job = _contract_job(db_session, customer, "Late fc", 100000, 90000, 60000)
    job_reports.set_ctc_entries(
        db_session, job, [{"cost_code_id": None, "cost_type": "", "amount": 60000}]
    )
    assert job_reports.wip_schedule(db_session, as_of=date(2020, 1, 1))["warnings"]
    assert (
        job_reports.wip_schedule(db_session, as_of=date(2099, 1, 1))["warnings"] == []
    )


def test_commitment_status_ignores_sub_co_approved_after_as_of(
    db_session, seed_accounts
):
    from datetime import datetime, timezone

    from app.models.change_orders import ChangeOrder
    from app.models.purchase_orders import PurchaseOrderLine

    customer = _customer(db_session)
    job = Job(customer_id=customer.id, name="CO date", status="in_progress")
    db_session.add(job)
    db_session.flush()
    v = Vendor(name="CO Sub")
    db_session.add(v)
    db_session.flush()
    po = _commitment(db_session, job, v, "SC-CO", 10000)
    co = ChangeOrder(
        job_id=job.id,
        number="CO-SUB-1",
        kind="sub",
        status="approved",
        commitment_id=po.id,
        approved_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    db_session.add(co)
    db_session.flush()
    db_session.add_all(
        [
            PurchaseOrderLine(
                purchase_order_id=po.id, amount=Decimal("8000"), line_order=0
            ),
            PurchaseOrderLine(
                purchase_order_id=po.id,
                amount=Decimal("2000"),
                change_order_id=co.id,
                line_order=1,
            ),
        ]
    )
    db_session.commit()
    before = job_reports.commitment_status(db_session, as_of=date(2026, 6, 1))
    row = before["jobs"][0]["rows"][0]
    assert row["committed"] == 8000.0 and row["remaining"] == 8000.0
    after = job_reports.commitment_status(db_session, as_of=date(2026, 8, 1))
    row = after["jobs"][0]["rows"][0]
    assert row["committed"] == 10000.0 and row["remaining"] == 10000.0


def test_commitment_closed_by_final_bill_is_open_before_it(db_session, seed_accounts):
    customer = _customer(db_session)
    job = Job(customer_id=customer.id, name="Closed later", status="in_progress")
    db_session.add(job)
    db_session.flush()
    v = Vendor(name="Close Sub")
    db_session.add(v)
    db_session.flush()
    po = _commitment(db_session, job, v, "SC-CL", 5000, status=POStatus.CLOSED)
    _po_bill(db_session, po, "C-1", 2000, when=date(2026, 3, 1))
    _po_bill(db_session, po, "C-2", 3000, when=date(2026, 7, 1))  # closes it
    db_session.commit()
    mid = job_reports.commitment_status(db_session, as_of=date(2026, 6, 1))
    row = mid["jobs"][0]["rows"][0]
    assert row["billed"] == 2000.0 and row["remaining"] == 3000.0
    # and its status on that date is the open one its bills had made it,
    # not today's "closed" (2.30.0 gate, macbase1)
    assert row["status"] == "partial"
    before = job_reports.commitment_status(db_session, as_of=date(2026, 2, 1))
    first = before["jobs"][0]["rows"][0]
    assert first["billed"] == 0.0 and first["remaining"] == 5000.0
    assert first["status"] == "sent"
    end = job_reports.commitment_status(db_session, as_of=date(2026, 8, 1))
    assert end["jobs"][0]["rows"][0]["remaining"] == 0.0
    assert end["jobs"][0]["rows"][0]["status"] == "closed"


def test_commitment_closed_by_hand_shows_nothing_remaining(db_session, seed_accounts):
    """10,000 subcontract, 4,000 billed, closed by hand: no remaining today or
    on an earlier date with no later bills."""
    customer = _customer(db_session)
    job = Job(customer_id=customer.id, name="Hand close", status="in_progress")
    db_session.add(job)
    db_session.flush()
    v = Vendor(name="Walked Sub")
    db_session.add(v)
    db_session.flush()
    po = _commitment(db_session, job, v, "SC-HC", 10000, status=POStatus.CLOSED)
    _po_bill(db_session, po, "H-1", 4000, when=date(2026, 3, 1))
    db_session.commit()
    for as_of in (date.today(), date(2026, 6, 1)):
        row = job_reports.commitment_status(db_session, as_of=as_of)["jobs"][0]["rows"][
            0
        ]
        assert row["billed"] == 4000.0 and row["remaining"] == 0.0


# ── 7. Timestamps read on the local day (#644) ───────────────────────────

# 23:30 Central on 2026-10-28 is 04:30 UTC on 10-29: the stamp a US evening's
# work carries in the file.
_LATE_EVENING = datetime(2026, 10, 29, 4, 30, tzinfo=timezone.utc)


@pytest.fixture
def central_time(monkeypatch):
    """Pin the local calendar to Central time for one test. TZ plus tzset
    moves the C library's zone where it has one (Linux, macOS); Windows has
    no tzset, so there the report's own zone hook is set instead."""
    if hasattr(time, "tzset"):
        monkeypatch.setenv("TZ", "America/Chicago")
        time.tzset()
    else:
        monkeypatch.setattr(job_reports, "_LOCAL_TZ", timezone(timedelta(hours=-5)))
    yield
    monkeypatch.undo()
    if hasattr(time, "tzset"):
        time.tzset()


def test_local_date_reads_a_utc_stamp_on_the_local_calendar(central_time):
    naive_utc = _LATE_EVENING.replace(tzinfo=None)  # as SQLite hands it back
    assert job_reports._local_date(naive_utc) == date(2026, 10, 28)
    assert job_reports._local_date(_LATE_EVENING) == date(2026, 10, 28)
    # noon is the same day on both calendars
    assert job_reports._local_date(datetime(2026, 10, 28, 17, 0)) == date(2026, 10, 28)


def test_wip_forecast_warning_reads_the_local_day(
    central_time, client, db_session, seed_accounts
):
    """A forecast typed at 23:30 on 10-28 must not make the schedule as of
    10-28 say it was entered the next day; as of 10-27 it still does."""
    customer = _customer(db_session)
    job = _contract_job(db_session, customer, "Evening forecast", 100000, 90000, 60000)
    job_reports.set_ctc_entries(
        db_session, job, [{"cost_code_id": None, "cost_type": "", "amount": 60000}]
    )
    db_session.query(JobCostToComplete).filter(
        JobCostToComplete.job_id == job.id
    ).update({"created_at": _LATE_EVENING, "updated_at": _LATE_EVENING})
    db_session.commit()

    same_day = job_reports.wip_schedule(db_session, as_of=date(2026, 10, 28))
    assert same_day["warnings"] == []
    csv = client.get("/api/reports/wip-schedule/csv?as_of=2026-10-28").text
    assert "after 2026-10-28" not in csv
    day_before = job_reports.wip_schedule(db_session, as_of=date(2026, 10, 27))
    assert len(day_before["warnings"]) == 1
    assert "entered 2026-10-28, after 2026-10-27" in day_before["warnings"][0]


def test_commitment_status_reads_a_sub_co_approval_on_the_local_day(
    central_time, db_session, seed_accounts
):
    """A sub change order approved at 23:30 on 10-28 adds its lines to the
    commitment as of 10-28, not only from 10-29."""
    customer = _customer(db_session)
    job = Job(customer_id=customer.id, name="Evening CO", status="in_progress")
    db_session.add(job)
    db_session.flush()
    v = Vendor(name="Night Sub")
    db_session.add(v)
    db_session.flush()
    po = _commitment(db_session, job, v, "SC-EV", 10000)
    co = ChangeOrder(
        job_id=job.id,
        number="CO-SUB-EV",
        kind="sub",
        status="approved",
        commitment_id=po.id,
        approved_at=_LATE_EVENING,
    )
    db_session.add(co)
    db_session.flush()
    db_session.add_all(
        [
            PurchaseOrderLine(
                purchase_order_id=po.id, amount=Decimal("8000"), line_order=0
            ),
            PurchaseOrderLine(
                purchase_order_id=po.id,
                amount=Decimal("2000"),
                change_order_id=co.id,
                line_order=1,
            ),
        ]
    )
    db_session.commit()
    same_day = job_reports.commitment_status(db_session, as_of=date(2026, 10, 28))
    row = same_day["jobs"][0]["rows"][0]
    assert row["committed"] == 10000.0 and row["remaining"] == 10000.0
    day_before = job_reports.commitment_status(db_session, as_of=date(2026, 10, 27))
    row = day_before["jobs"][0]["rows"][0]
    assert row["committed"] == 8000.0 and row["remaining"] == 8000.0


def test_owner_co_approved_in_the_evening_counts_on_that_day(
    central_time, db_session, seed_accounts
):
    """The change order log's running total and the WIP revised contract read
    an owner change order approved at 23:30 on 10-28 as approved that day."""
    customer = _customer(db_session)
    job = _contract_job(db_session, customer, "Evening owner CO", 100000, 80000, 40000)
    job.contract_amount = Decimal("110000")  # the approve already grew it
    _co(
        db_session,
        job,
        "CO-EV",
        "owner",
        "approved",
        "contract",
        [("8000", "10000", "material")],
        approved_at=_LATE_EVENING,
    )
    db_session.commit()
    log = job_reports.change_order_log(db_session, as_of=date(2026, 10, 28))
    assert log["jobs"][0]["rows"][0]["running_approved"] == 10000.0
    assert log["jobs"][0]["contract"]["revised"] == 110000.0
    earlier = job_reports.change_order_log(db_session, as_of=date(2026, 10, 27))
    assert earlier["jobs"][0]["rows"][0]["running_approved"] == 0.0
    assert earlier["jobs"][0]["contract"]["revised"] == 100000.0
    wip = job_reports.wip_schedule(db_session, as_of=date(2026, 10, 28))["rows"][0]
    assert wip["approved_change_orders"] == 10000.0
    assert wip["revised_contract"] == 110000.0
    wip = job_reports.wip_schedule(db_session, as_of=date(2026, 10, 27))["rows"][0]
    assert wip["approved_change_orders"] == 0.0
    assert wip["revised_contract"] == 100000.0


def test_commitment_billed_in_part_reads_sent_before_its_first_bill(
    db_session, seed_accounts
):
    """A 6,000 subcontract billed 2,500 on 4-1 is partial today; as of a day
    before that bill nothing had been billed, so it reads sent with the whole
    amount remaining, the way one closed by a later bill reads open (#644)."""
    customer = _customer(db_session)
    job = Job(customer_id=customer.id, name="Partly billed", status="in_progress")
    db_session.add(job)
    db_session.flush()
    v = Vendor(name="Part Sub")
    db_session.add(v)
    db_session.flush()
    po = _commitment(db_session, job, v, "SC-PB", 6000, status=POStatus.PARTIAL)
    _po_bill(db_session, po, "PB-1", 2500, held="250", when=date(2026, 4, 1))
    db_session.commit()
    before = job_reports.commitment_status(db_session, as_of=date(2026, 3, 1))
    row = before["jobs"][0]["rows"][0]
    assert row["status"] == "sent"
    assert row["billed"] == 0.0 and row["remaining"] == 6000.0
    on = job_reports.commitment_status(db_session, as_of=date(2026, 4, 1))
    row = on["jobs"][0]["rows"][0]
    assert row["status"] == "partial"
    assert row["billed"] == 2500.0 and row["remaining"] == 3500.0


# ── as-of: a job closed after the date, and the status filter ──────────────


def _closed_later_job(db, name="Tower"):
    """A job now closed (on 2026-09-01) with a commitment and a cost."""
    customer = _customer(db)
    vendor = Vendor(name="Sub Co", is_active=True)
    db.add(vendor)
    db.flush()
    job = Job(
        customer_id=customer.id,
        name=name,
        status="closed",
        closed_on=date(2026, 9, 1),
    )
    db.add(job)
    db.flush()
    _commitment(db, job, vendor, "PO-9", "5000")
    _post_cost(db, job.id, _cogs(db), "1000", date(2026, 3, 1), vendor_id=vendor.id)
    db.commit()
    return job


_AS_OF_REPORTS = {
    "buyout": lambda db, **kw: job_reports.buyout_report(
        db, as_of=kw.pop("as_of"), **kw
    ),
    "commitments": lambda db, **kw: job_reports.commitment_status(
        db, as_of=kw.pop("as_of"), **kw
    ),
    "vendor": lambda db, **kw: job_reports.job_cost_by_vendor(
        db, end_date=kw.pop("as_of"), **kw
    ),
}


@pytest.mark.parametrize("name", sorted(_AS_OF_REPORTS))
def test_as_of_reports_keep_a_job_closed_after_the_date(
    db_session, seed_accounts, name
):
    """Buyout, Commitment Status and Job Cost by Vendor keep a job that was
    closed after the date: it was open on it, as on the WIP schedule."""
    job = _closed_later_job(db_session)
    run = _AS_OF_REPORTS[name]
    assert [j["job_id"] for j in run(db_session, as_of=date(2026, 7, 1))["jobs"]] == [
        job.id
    ]
    # after the close it is gone, as before
    assert run(db_session, as_of=date(2026, 10, 1))["jobs"] == []
    wip = job_reports.wip_schedule(db_session, as_of=date(2026, 7, 1))
    assert job.id in [r["job_id"] for r in wip["rows"]]


@pytest.mark.parametrize("name", sorted(_AS_OF_REPORTS))
def test_as_of_reports_filter_status_on_the_status_of_the_date(
    db_session, seed_accounts, name
):
    """A status filter matches the job's status on the date: a job closed
    after the date answers to in_progress and not to closed, and the other
    way round once the date is past the close."""
    job = _closed_later_job(db_session)
    run = _AS_OF_REPORTS[name]
    early, late = date(2026, 7, 1), date(2026, 10, 1)

    def ids(d, st):
        return [j["job_id"] for j in run(db_session, as_of=d, status=st)["jobs"]]

    assert ids(early, "in_progress") == [job.id]
    assert ids(early, "closed") == []
    assert ids(late, "closed") == [job.id]
    assert ids(late, "in_progress") == []
