"""Job costing (milestone 3): cost types + offset setup, cost-code tree and
import, Job Cost Entries (post / void / accounts resolution), time entries
posted to jobs at a loaded rate with burden, allocations, budgets seeded
from estimates, and the budget-vs-actual drill-down tree."""

from decimal import Decimal

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction


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


def test_cost_types_seed_edit_and_offset_setup(client, seed_accounts):
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
    assert (fram["id"], None) in by_key  # estimate rows kept

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
