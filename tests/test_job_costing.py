"""Job costing (milestone 3): cost types + offset setup, cost-code tree and
import, Job Cost Entries (post / void / accounts resolution), time entries
posted to jobs at a loaded rate with burden, allocations, budgets seeded
from estimates, and the budget-vs-actual drill-down tree."""

import csv
import io
from datetime import date
from decimal import Decimal

from app.models.accounts import Account, AccountType
from app.models.bills import Bill, BillLine, BillStatus
from app.models.contacts import Vendor
from app.models.job_costing import JobBudget
from app.models.purchase_orders import POStatus, PurchaseOrder, PurchaseOrderLine
from app.models.settings import Settings
from app.models.transactions import Transaction
from app.services import job_reports
from app.services.job_costing import (
    budget_vs_actual_all_jobs,
    ensure_default_cost_types,
    job_cost_tree,
)
from tests.test_sov_mapping import _accepted_build, _section
from tests.test_wip_schedule_post import _acct, _bill_owner, _budget, _cost
from tests.test_wip_schedule_post import _job as _wip_job
from tests.test_wip_schedule_post import _section as _db_section
from tests.test_wip_schedule_post import _section as _wip_section


def _income_expense(db):
    income = (
        db.query(Account).filter(Account.account_type == AccountType.INCOME).first()
    )
    expense = (
        db.query(Account).filter(Account.account_type == AccountType.EXPENSE).first()
    )
    assert income and expense
    return income, expense


def _job(client, customer_id, name="Barn", **extra):
    resp = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _setup(client):
    types = client.post("/api/cost-types/setup-offsets")
    assert types.status_code == 200, types.text
    return {t["code"]: t for t in types.json()}


def _code(client, code, name, cost_type="material", parent_id=None):
    resp = client.post(
        "/api/cost-codes",
        json={
            "code": code,
            "name": name,
            "cost_type": cost_type,
            "parent_id": parent_id,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Cost types ───────────────────────────────────────────────────────────


def test_cost_types_seed_edit_and_offset_setup(client, db_session, seed_accounts):
    # the list is a read and never seeds them (#662); a company file has the
    # five from its migration, which the service seeds here
    assert client.get("/api/cost-types").json() == []
    ensure_default_cost_types(db_session)
    db_session.commit()
    listed = client.get("/api/cost-types").json()
    assert [t["code"] for t in listed] == [
        "labor",
        "material",
        "subcontract",
        "equipment",
        "other",
    ]
    assert next(t for t in listed if t["code"] == "labor")["is_labor"] is True

    permits = client.post(
        "/api/cost-types", json={"code": "Permits & Fees", "name": "Permits"}
    )
    assert permits.status_code == 201, permits.text
    assert permits.json()["code"] == "permits_&_fees"
    dup = client.post("/api/cost-types", json={"code": "labor", "name": "x"})
    assert dup.status_code == 409

    types = _setup(client)
    assert types["labor"]["offset_account_name"] == "Applied Labor Cost"
    assert types["labor"]["burden_offset_account_name"] == "Applied Labor Burden"
    assert types["equipment"]["offset_account_name"] == "Applied Equipment Cost"
    assert types["material"]["offset_account_name"] == "Applied Overhead"
    # every offset is a P&L contra, never a balance-sheet account
    accounts = {a["name"]: a for a in client.get("/api/accounts").json()}
    for name in (
        "Applied Labor Cost",
        "Applied Labor Burden",
        "Applied Equipment Cost",
        "Applied Overhead",
    ):
        assert accounts[name]["account_type"] == "expense", name
    assert "Payroll Clearing" not in accounts and "Job Costs" not in accounts
    # cost accounts follow the seed chart's COGS split, not one lump
    assert types["material"]["default_account_name"] == "Materials Cost"
    assert types["labor"]["default_account_name"] == "Labor Cost"
    assert types["subcontract"]["default_account_name"] == "Subcontractor Costs"
    assert types["equipment"]["default_account_name"] == "Cost of Goods Sold"
    assert types["other"]["default_account_name"] == "Cost of Goods Sold"
    # idempotent, and it respects a choice already made
    labor_id = types["labor"]["id"]
    other_acct = client.get("/api/accounts").json()[0]["id"]
    client.put(
        f"/api/cost-types/{labor_id}",
        json={"burden_pct": 22.5, "offset_account_id": other_acct},
    )
    again = {t["code"]: t for t in client.post("/api/cost-types/setup-offsets").json()}
    assert again["labor"]["offset_account_id"] == other_acct
    assert float(again["labor"]["burden_pct"]) == 22.5
    assert client.delete(f"/api/cost-types/{labor_id}").status_code == 400


# ── Cost-code tree + import ──────────────────────────────────────────────


def test_cost_code_tree_and_csv_import(client):
    div = _code(client, "03", "Concrete", "subcontract")
    sub = _code(client, "03-300", "Cast-in-place", "subcontract", parent_id=div["id"])
    assert sub["parent_code"] == "03" and sub["depth"] == 1
    cycle = client.put(f"/api/cost-codes/{div['id']}", json={"parent_id": sub["id"]})
    assert cycle.status_code == 422

    imp = client.post(
        "/api/cost-codes/import",
        json={
            "csv": "code,name,cost_type,parent_code\n"
            "03-310,Footings,subcontract,03-300\n"
            "03-320,Slabs,subcontract,03-300\n"
            "26,Electrical,subcontract,\n"
            "26-050,Rough-in,labor,26\n"
            "99,Bad type,nope,\n"
        },
    )
    assert imp.status_code == 200, imp.text
    body = imp.json()
    assert body["created"] == 4 and any("nope" in e for e in body["errors"])

    tree = client.get("/api/cost-codes/tree").json()
    top = {n["code"]: n for n in tree}
    assert set(top) >= {"03", "26"}
    assert [c["code"] for c in top["03"]["children"]] == ["03-300"]
    assert [c["code"] for c in top["03"]["children"][0]["children"]] == [
        "03-310",
        "03-320",
    ]
    assert top["26"]["children"][0]["cost_type"] == "labor"


# ── Job Cost Entry ───────────────────────────────────────────────────────


def test_job_cost_entry_posts_and_voids(
    client, db_session, seed_accounts, seed_customer
):
    _setup(client)
    job = _job(client, seed_customer.id)
    fuel = _code(client, "EQ-F", "Fuel", "equipment")
    eq = client.post(
        "/api/equipment", json={"name": "Skid steer", "code": "SS1", "hourly_rate": 65}
    ).json()

    missing = client.post(
        "/api/job-costs",
        json={"date": "2026-07-10", "lines": [{"description": "no job", "amount": 5}]},
    )
    assert missing.status_code == 422

    resp = client.post(
        "/api/job-costs",
        json={
            "date": "2026-07-10",
            "job_id": job["id"],
            "memo": "Site work",
            "lines": [
                {
                    "equipment_id": eq["id"],
                    "quantity": 6,
                    "description": "Skid steer 6 hrs",
                },
                {
                    "cost_code_id": fuel["id"],
                    "quantity": 40,
                    "rate": 3.5,
                    "description": "Diesel",
                },
                {
                    "cost_type": "other",
                    "amount": 120,
                    "description": "Dump fees",
                    "is_billable": True,
                },
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    jc = resp.json()
    assert jc["number"].startswith("JC-") and jc["status"] == "posted"
    assert float(jc["total"]) == 6 * 65 + 40 * 3.5 + 120
    lines = {ln["description"]: ln for ln in jc["lines"]}
    assert float(lines["Skid steer 6 hrs"]["rate"]) == 65.0
    assert lines["Skid steer 6 hrs"]["cost_type"] == "equipment"
    assert lines["Diesel"]["cost_code_id"] == fuel["id"]
    assert lines["Dump fees"]["is_billable"] is True

    txn = db_session.get(Transaction, jc["transaction_id"])
    debits = [ln for ln in txn.lines if ln.debit > 0]
    assert all(ln.job_id == job["id"] for ln in debits)
    assert {ln.cost_type for ln in debits} == {"equipment", "other"}
    credits = [ln for ln in txn.lines if ln.credit > 0]
    assert all(ln.job_id is None for ln in credits)
    assert sum(ln.debit for ln in debits) == sum(ln.credit for ln in credits)

    summary = client.get(f"/api/jobs/{job['id']}").json()["summary"]
    assert summary["total_costs"] == float(jc["total"])

    voided = client.post(f"/api/job-costs/{jc['id']}/void")
    assert voided.status_code == 200 and voided.json()["status"] == "void"
    assert client.post(f"/api/job-costs/{jc['id']}/void").status_code == 400
    assert client.get(f"/api/jobs/{job['id']}").json()["summary"]["total_costs"] == 0.0
    listed = client.get(f"/api/job-costs?job_id={job['id']}").json()
    assert [j["status"] for j in listed] == ["void"]


def test_job_cost_entry_needs_accounts_or_says_where_to_set_them(
    client, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    resp = client.post(
        "/api/job-costs",
        json={
            "date": "2026-07-10",
            "job_id": job["id"],
            "lines": [{"amount": 10, "cost_type": "other"}],
        },
    )
    assert resp.status_code == 422
    assert "Settings" in resp.json()["detail"]


# ── Time entries → labor cost with burden ────────────────────────────────


def test_time_entry_posts_labor_at_loaded_rate_with_burden(
    client, db_session, seed_accounts, seed_customer
):
    types = _setup(client)
    client.put(f"/api/cost-types/{types['labor']['id']}", json={"burden_pct": 20})
    job = _job(client, seed_customer.id)
    framing = _code(client, "06-100", "Framing labor", "labor")
    emp = client.post(
        "/api/employees",
        json={
            "first_name": "Ann",
            "last_name": "Crew",
            "pay_type": "hourly",
            "pay_rate": 30,
            "cost_rate": 40,
        },
    )
    assert emp.status_code in (200, 201), emp.text
    emp_id = emp.json()["id"]
    te = client.post(
        "/api/time-entries",
        json={
            "employee_id": emp_id,
            "date": "2026-07-11",
            "hours_regular": 8,
            "hours_overtime": 2,
            "job_id": job["id"],
            "cost_code_id": framing["id"],
        },
    )
    assert te.status_code == 201, te.text
    te_id = te.json()["id"]
    assert te.json()["job_name"].endswith("Barn")

    early = client.post(f"/api/time-entries/{te_id}/post-to-job")
    assert early.status_code == 400  # draft entries don't post
    client.post(f"/api/time-entries/{te_id}/submit")
    client.post(f"/api/time-entries/{te_id}/approve?approved_by=admin")

    posted = client.post(f"/api/time-entries/{te_id}/post-to-job")
    assert posted.status_code == 200, posted.text
    # 8 + 2*1.5 = 11 cost hours × 40 = 440 base, +20% burden = 88
    assert posted.json()["total"] == 528.0
    jc = client.get(f"/api/job-costs/{posted.json()['job_cost_id']}").json()
    assert jc["source"] == "time_entry"
    burden = [ln for ln in jc["lines"] if ln["is_burden"]]
    assert len(burden) == 1 and float(burden[0]["amount"]) == 88.0
    assert all(ln["cost_code_id"] == framing["id"] for ln in jc["lines"])
    assert client.post(f"/api/time-entries/{te_id}/post-to-job").status_code == 400

    entry = next(e for e in client.get("/api/time-entries").json() if e["id"] == te_id)
    assert entry["job_cost_id"] == jc["id"]

    tree = client.get(f"/api/jobs/{job['id']}/cost-tree").json()
    labor = next(t for t in tree["types"] if t["cost_type"] == "labor")
    assert labor["figures"]["actual"] == 528.0
    node = labor["codes"][0]
    assert node["code"] == "06-100" and node["figures"]["actual"] == 528.0
    assert len(node["lines"]) == 2

    # bulk endpoint reports per-entry outcome
    bulk = client.post("/api/time-entries/post-to-job", json={"ids": [te_id, 999]})
    assert bulk.json()["posted"] == 0
    assert [r["ok"] for r in bulk.json()["results"]] == [False, False]


# ── Allocations ──────────────────────────────────────────────────────────


def test_allocation_spreads_by_hours_and_percent(client, seed_accounts, seed_customer):
    _setup(client)
    a = _job(client, seed_customer.id, name="A")
    b = _job(client, seed_customer.id, name="B")
    emp = client.post(
        "/api/employees",
        json={
            "first_name": "Bo",
            "last_name": "Hours",
            "pay_type": "hourly",
            "pay_rate": 20,
        },
    ).json()
    for job, hrs in ((a, 30), (b, 10)):
        client.post(
            "/api/time-entries",
            json={
                "employee_id": emp["id"],
                "date": "2026-07-12",
                "hours_regular": hrs,
                "job_id": job["id"],
            },
        )
    resp = client.post(
        "/api/job-costs/allocate",
        json={
            "date": "2026-07-31",
            "amount": 1000,
            "method": "hours",
            "memo": "July small tools",
            "cost_type": "other",
        },
    )
    assert resp.status_code == 201, resp.text
    jc = resp.json()
    assert jc["source"] == "allocation" and jc["job_id"] is None
    shares = {ln["job_id"]: float(ln["amount"]) for ln in jc["lines"]}
    assert shares == {a["id"]: 750.0, b["id"]: 250.0}

    pct = client.post(
        "/api/job-costs/allocate",
        json={
            "date": "2026-07-31",
            "amount": 100.01,
            "method": "percent",
            "cost_type": "other",
            "targets": [
                {"job_id": a["id"], "weight": 2},
                {"job_id": b["id"], "weight": 1},
            ],
        },
    )
    assert pct.status_code == 201, pct.text
    amounts = sorted(float(ln["amount"]) for ln in pct.json()["lines"])
    assert amounts == [33.34, 66.67] and sum(amounts) == 100.01
    assert client.get(f"/api/jobs/{a['id']}").json()["summary"]["total_costs"] == 816.67


# ── Budgets, estimate seeding, drill-down tree ───────────────────────────


def test_budget_from_estimate_and_cost_tree_columns(
    client, db_session, seed_accounts, seed_customer
):
    _setup(client)
    job = _job(client, seed_customer.id, contract_amount=20000)
    div = _code(client, "06", "Wood", "material")
    fram = _code(client, "06-100", "Framing", "material", parent_id=div["id"])
    trim = _code(client, "06-200", "Trim", "material", parent_id=div["id"])
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-01",
            "job_id": job["id"],
            "lines": [
                {
                    "description": "Framing",
                    "quantity": 10,
                    "rate": 300,
                    "unit_cost": 200,
                    "cost_code_id": fram["id"],
                },
                {
                    "description": "Trim",
                    "quantity": 1,
                    "rate": 1500,
                    "cost_code_id": trim["id"],
                },
                {"description": "Misc", "quantity": 1, "rate": 500},
            ],
        },
    )
    assert est.status_code in (200, 201), est.text
    seeded = client.post(
        f"/api/jobs/{job['id']}/budgets/from-estimate/{est.json()['id']}"
    )
    assert seeded.status_code == 200, seeded.text
    rows = {r["cost_code_id"]: r for r in seeded.json()}
    assert (
        float(rows[fram["id"]]["amount"]) == 2000.0
        and float(rows[fram["id"]]["revenue_amount"]) == 3000.0
    )
    assert float(rows[trim["id"]]["amount"]) == 0.0  # no unit cost → unknown, not price
    assert float(rows[None]["amount"]) == 0.0  # uncoded, no unit cost → whole-job row
    assert float(rows[None]["revenue_amount"]) == 500.0

    # manual override on trim, plus a type-level budget
    saved = client.put(
        f"/api/jobs/{job['id']}/budgets",
        json={
            "rows": [
                {"cost_code_id": trim["id"], "amount": 1200, "revenue_amount": 1500},
                {"cost_type": "labor", "amount": 4000},
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    by_key = {(r["cost_code_id"], r["cost_type"]): r for r in saved.json()}
    assert by_key[(trim["id"], None)]["source"] == "manual"
    # the estimate row carries the line's type (filled from the code) (#550)
    assert (fram["id"], "material") in by_key  # estimate rows kept

    # actuals + committed
    vendor = client.post("/api/vendors", json={"name": "Lumber"}).json()
    _, expense = _income_expense(db_session)
    client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "B1",
            "date": "2026-07-05",
            "job_id": job["id"],
            "lines": [
                {
                    "account_id": expense.id,
                    "quantity": 1,
                    "rate": 800,
                    "cost_code_id": fram["id"],
                }
            ],
        },
    )
    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-07-06",
            "job_id": job["id"],
            "lines": [
                {
                    "description": "studs",
                    "quantity": 1,
                    "rate": 600,
                    "cost_code_id": fram["id"],
                }
            ],
        },
    ).json()
    client.put(f"/api/purchase-orders/{po['id']}", json={"status": "sent"})
    client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-07",
            "job_id": job["id"],
            "lines": [
                {
                    "description": "Framing draw",
                    "quantity": 1,
                    "rate": 1000,
                    "cost_code_id": fram["id"],
                }
            ],
        },
    )

    tree = client.get(f"/api/jobs/{job['id']}/cost-tree").json()
    material = next(t for t in tree["types"] if t["cost_type"] == "material")
    wood = material["codes"][0]
    assert wood["code"] == "06"
    framing = next(c for c in wood["children"] if c["code"] == "06-100")
    f = framing["figures"]
    assert f["original"] == 2000.0 and f["revised"] == 2000.0
    assert f["actual"] == 800.0 and f["committed"] == 600.0
    assert f["projected"] == 1400.0 and f["variance"] == 600.0
    assert round(f["pct_used"], 1) == 70.0
    assert (
        f["est_revenue"] == 3000.0
        and f["act_revenue"] == 1000.0
        and f["revenue_diff"] == -2000.0
    )
    assert [ln["kind"] for ln in framing["lines"]] == ["cost", "income"]
    # division rolls its children up
    assert wood["figures"]["original"] == 3200.0 and wood["figures"]["actual"] == 800.0
    assert material["figures"]["projected"] == 1400.0
    labor = next(t for t in tree["types"] if t["cost_type"] == "labor")
    assert labor["figures"]["original"] == 4000.0 and labor["figures"]["actual"] == 0.0
    assert tree["job_level_budget"]["original"] == 0.0
    assert tree["totals"]["original"] == 3200.0 + 4000.0

    # headline report row
    bva = {r["job_id"]: r for r in client.get("/api/jobs/budget-vs-actual").json()}
    row = bva[job["id"]]
    assert (
        row["revised"] == 7200.0
        and row["actual"] == 800.0
        and row["committed"] == 600.0
    )
    assert row["variance"] == 7200.0 - 1400.0 and row["act_revenue"] == 1000.0

    # period filter: actuals honour it, budgets don't
    later = client.get(f"/api/jobs/{job['id']}/cost-tree?start_date=2026-08-01").json()
    assert (
        later["totals"]["actual"] == 0.0
        and later["totals"]["original"] == tree["totals"]["original"]
    )


def test_estimate_unit_cost_round_trips(client, seed_accounts, seed_customer):
    resp = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-01",
            "lines": [
                {"description": "x", "quantity": 2, "rate": 50, "unit_cost": 30.5}
            ],
        },
    )
    assert resp.status_code in (200, 201), resp.text
    assert Decimal(str(resp.json()["lines"][0]["unit_cost"])) == Decimal("30.5")


# ── Review fixes (first Mac lap, 2026-09-03) ─────────────────────────────


def _pl(client, start="2026-07-01", end="2026-07-31"):
    resp = client.get(
        "/api/reports/profit-loss", params={"start_date": start, "end_date": end}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _hourly(client, first="Ray", last="Crew", **extra):
    resp = client.post(
        "/api/employees",
        json={
            "first_name": first,
            "last_name": last,
            "pay_type": "hourly",
            "pay_rate": 30,
            **extra,
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _posted_entry(client, job_id, emp_id, day="2026-07-11", hours=8):
    te = client.post(
        "/api/time-entries",
        json={
            "employee_id": emp_id,
            "date": day,
            "hours_regular": hours,
            "job_id": job_id,
        },
    ).json()
    client.post(f"/api/time-entries/{te['id']}/submit")
    posted = client.post(f"/api/time-entries/{te['id']}/post-to-job")
    assert posted.status_code == 200, posted.text
    return te["id"], posted.json()["job_cost_id"]


def test_labor_posting_is_pnl_neutral(client, seed_accounts, seed_customer):
    """Time → job moves labor into COGS and credits a P&L contra, so net
    income is unchanged; the pay run is what actually expenses the wages.
    (A balance-sheet offset would have counted the labor twice.)"""
    types = _setup(client)
    client.put(f"/api/cost-types/{types['labor']['id']}", json={"burden_pct": 20})
    job = _job(client, seed_customer.id)
    emp_id = _hourly(client)
    _posted_entry(client, job["id"], emp_id)

    pl = _pl(client)
    assert pl["net_income"] == 0
    cogs = {c["account_name"]: c["amount"] for c in pl["cogs"]}
    expenses = {e["account_name"]: e["amount"] for e in pl["expenses"]}
    assert cogs["Labor Cost"] == 288.0  # 8h × 30 = 240 base + 20% burden
    assert expenses["Applied Labor Cost"] == -240.0
    assert expenses["Applied Labor Burden"] == -48.0

    # the job carries the cost, "No job" carries the offsets, sum = P&L
    prof = client.get(
        "/api/jobs/profitability",
        params={"start_date": "2026-07-01", "end_date": "2026-07-31"},
    ).json()
    by_name = {r["job_name"]: r for r in prof}
    assert by_name["Barn"]["total_costs"] == 288.0
    assert sum(r["income"] - r["total_costs"] for r in prof) == pl["net_income"]


def test_rejecting_a_posted_time_entry_voids_its_job_cost(
    client, seed_accounts, seed_customer
):
    _setup(client)
    job = _job(client, seed_customer.id)
    emp_id = _hourly(client, "Ann")
    te_id, jc_id = _posted_entry(client, job["id"], emp_id)
    assert (
        client.get(f"/api/jobs/{job['id']}").json()["summary"]["total_costs"] == 240.0
    )

    rej = client.post(f"/api/time-entries/{te_id}/reject")
    assert rej.status_code == 200, rej.text
    assert rej.json()["status"] == "rejected" and rej.json()["job_cost_id"] is None
    assert client.get(f"/api/job-costs/{jc_id}").json()["status"] == "void"
    assert client.get(f"/api/jobs/{job['id']}").json()["summary"]["total_costs"] == 0
    # an entry that was never posted still rejects cleanly
    te2 = client.post(
        "/api/time-entries",
        json={"employee_id": emp_id, "date": "2026-07-12", "hours_regular": 2},
    ).json()
    assert client.post(f"/api/time-entries/{te2['id']}/reject").status_code == 200


def test_reseeding_budget_keeps_manual_rows_and_zero_costs_unknown(
    client, seed_accounts, seed_customer
):
    _setup(client)
    job = _job(client, seed_customer.id)
    fram = _code(client, "06-100", "Framing", "material")
    trim = _code(client, "06-200", "Trim", "material")
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-01",
            "job_id": job["id"],
            "lines": [
                {
                    "description": "Framing",
                    "quantity": 10,
                    "rate": 300,
                    "unit_cost": 200,
                    "cost_code_id": fram["id"],
                },
                {
                    "description": "Trim",
                    "quantity": 1,
                    "rate": 1500,
                    "cost_code_id": trim["id"],
                },
            ],
        },
    ).json()
    seed = f"/api/jobs/{job['id']}/budgets/from-estimate/{est['id']}"
    rows = {r["cost_code_id"]: r for r in client.post(seed).json()}
    assert float(rows[fram["id"]]["amount"]) == 2000.0
    # no unit cost entered: the cost is unknown, not equal to the sale price
    assert float(rows[trim["id"]]["amount"]) == 0.0
    assert float(rows[trim["id"]]["revenue_amount"]) == 1500.0

    # hand-edit framing, re-seed: the manual row wins, trim is re-seeded
    client.put(
        f"/api/jobs/{job['id']}/budgets",
        json={"rows": [{"cost_code_id": fram["id"], "amount": 2500}]},
    )
    client.post(seed)
    rows = {
        r["cost_code_id"]: r
        for r in client.get(f"/api/jobs/{job['id']}/budgets").json()
    }
    assert float(rows[fram["id"]]["amount"]) == 2500.0
    assert rows[fram["id"]]["source"] == "manual"
    assert rows[trim["id"]]["source"] == "estimate"
    assert sum(1 for r in rows.values() if r["cost_code_id"] == fram["id"]) == 1


def _partly_bought_job(db):
    """Budget 100,000 over two sections: Concrete 40,000 (cost 30,000 on
    3/1), Electrical 60,000 not bought yet. Projected final cost counts the
    budget nothing has taken up (10,000 + 60,000): 100,000, variance 0,
    where actual + committed alone reads 30,000."""
    job = _wip_job(db, name="Annex", contract="150000")
    conc = _db_section(db, "97 30 00", "Concrete")
    elec = _db_section(db, "98 05 00", "Electrical")
    _budget(db, job, conc, "subcontract", "40000")
    _budget(db, job, elec, "subcontract", "60000")
    _cost(db, job, conc, "subcontract", "30000")
    db.commit()
    return job


def _construction(db):
    db.add(Settings(key="company_type", value="construction"))
    db.commit()


def _setup_2(client, db):
    assert client.post("/api/construction/setup").status_code == 200
    _construction(db)


def test_budget_vs_actual_and_the_cost_tree_project_like_the_overview(
    client, db_session, seed_accounts
):
    """The Overview (the undated tree) read Projected 377,525.55,
    Variance 0; BvA, its exports and the dated tree left out the budget not
    yet committed."""
    _setup_2(client, db_session)
    job = _partly_bought_job(db_session)
    end = date(2026, 12, 31)
    overview = job_cost_tree(db_session, job.id)["totals"]
    assert overview["projected"] == 100000.0 and overview["variance"] == 0.0

    start = date(2026, 1, 1)  # the Report Center's default period
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
    _setup_2(client, db_session)
    job = _partly_bought_job(db_session)
    end = date(2026, 12, 31)
    start = date(2026, 6, 1)  # after the 3/1 cost
    dated = job_cost_tree(db_session, job.id, start, end)["totals"]
    assert dated["actual"] == 0.0
    assert dated["cost_before_period"] == 30000.0
    assert dated["projected"] == 100000.0 and dated["variance"] == 0.0
    bva = budget_vs_actual_all_jobs(db_session, start, end)
    assert {r["job_id"]: r["projected"] for r in bva}[job.id] == 100000.0


def test_a_business_budget_vs_actual_projects_as_before(db_session, seed_accounts):
    job = _partly_bought_job(db_session)  # no company type: Business
    end = date(2026, 12, 31)
    row = {
        r["job_id"]: r
        for r in budget_vs_actual_all_jobs(db_session, date(2026, 1, 1), end)
    }[job.id]
    assert row["projected"] == 30000.0 and row["variance"] == 70000.0
    assert "uncommitted_budget" not in row
    totals = job_cost_tree(db_session, job.id)["totals"]
    assert totals["projected"] == 30000.0


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
    """The accepted −6,000 deduct got no share and HVAC absorbed
    −975.12. Here the −10,000 deduct takes −1,000 of the 9,000 fee."""
    _setup_2(client, db_session)
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
    """Demo billed 100% read est 20,925.36 vs act 18,000, and the
    markup lines' 45,635.61 sat under Unassigned."""
    _setup_2(client, db_session)
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


OCT15 = date(2026, 10, 15)


SEP30 = date(2026, 9, 30)


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


def _over_billed_job(db, name="Clinic"):
    """Contract 100,000, budget 80,000 (material), cost 40,000 by 3/1,
    billed 60,000 by 4/1: earned 50,000, over-billed 10,000."""
    job = _wip_job(db, name=name)
    _budget(db, job, None, "material", "80000", source="manual")
    _cost(db, job, None, "material", "40000")
    _bill_owner(db, job, "60000")
    db.commit()
    return job


def test_cost_tree_budget_vs_actual_and_ctc_read_the_change_as_of(
    client, db_session, seed_accounts
):
    _setup_2(client, db_session)
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
    _setup_2(client, db_session)
    job = _wip_job(db_session)
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
    job = _wip_job(db_session)
    _po_billed_later(db_session, job)
    aug = job_cost_tree(db_session, job.id, end_date=date(2026, 8, 31))["totals"]
    assert aug["committed"] == 30000.0 and aug["commitments_all_time"] is True


def _deduct_job(db):
    """Electrical 100,000 (subcontract) and an accepted −6,000 deduct
    alternate on a section of its own."""
    job = _wip_job(db)
    elec = _wip_section(db, "81 05 00", "Electrical")
    tile = _wip_section(db, "82 30 00", "Tiling")
    _budget(db, job, elec, "subcontract", "100000")
    _budget(db, job, tile, "material", "-6000")
    db.commit()
    return job, elec, tile


def _wip_row(db, job_id, as_of, **kw):
    data = job_reports.wip_schedule(db, as_of=as_of, **kw)
    return next((r for r in data["rows"] if r["job_id"] == job_id), None), data


def test_an_accepted_deduct_shows_no_variance_and_no_extra_cost(
    client, db_session, seed_accounts
):
    _setup_2(client, db_session)
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
    _setup_2(client, db_session)
    job, elec, _tile = _deduct_job(db_session)
    _cost(db_session, job, elec, "subcontract", "100000")
    db_session.commit()
    row, _ = _wip_row(db_session, job.id, SEP30)
    assert row["estimated_total_cost"] == 100000.0 and row["cost_to_complete"] == 0.0
    forecast = job_reports.ctc_forecast(db_session, job, SEP30)
    assert forecast["totals"]["cost_to_complete"] == 0.0
    bva = {r["job_id"]: r for r in budget_vs_actual_all_jobs(db_session, None, SEP30)}
    assert bva[job.id]["variance"] == -6000.0


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
    _setup_2(client, db_session)
    job = _wip_job(db_session)
    elec = _wip_section(db_session, "81 05 00", "Electrical")
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
    job = _wip_job(db_session)
    elec = _wip_section(db_session, "81 05 00", "Electrical")
    _revenue_on(db_session, job, elec, "45000")
    db_session.commit()
    tree = job_cost_tree(db_session, job.id)
    assert "revenue_sections" not in tree
    assert sum(t["figures"]["act_revenue"] for t in tree["types"]) == 45000.0


def test_the_cost_tree_exports_carry_revenue_by_section(
    client, db_session, seed_accounts
):
    _setup_2(client, db_session)
    job = _wip_job(db_session)
    elec = _wip_section(db_session, "81 05 00", "Electrical")
    _revenue_on(db_session, job, elec, "45000")
    db_session.commit()
    text = client.get(f"/api/jobs/{job.id}/cost-tree/csv").text
    assert "Revenue by section" in text
    assert any("81 05 00" in ln and "45000.00" in ln for ln in text.splitlines())
