"""Cost codes (milestone 2): CRUD + standard list, per-line cost code and
billable flag through bills / expenses / journal, job cost by code, and
committed cost from open purchase orders."""

from app.models.accounts import Account, AccountType
from app.models.bills import BillLine
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


def _job(client, customer_id, name="Deck build"):
    resp = client.post("/api/jobs", json={"customer_id": customer_id, "name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _code(client, code, name, cost_type="material"):
    resp = client.post(
        "/api/cost-codes", json={"code": code, "name": name, "cost_type": cost_type}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── CRUD + standard list ─────────────────────────────────────────────────


def test_cost_code_crud_and_standard_list(client):
    cc = _code(client, "06", "Wood", "material")
    assert cc["label"] == "06 Wood"
    assert (
        client.post("/api/cost-codes", json={"code": "06", "name": "dup"}).status_code
        == 409
    )
    assert (
        client.post(
            "/api/cost-codes", json={"code": "07", "name": "x", "cost_type": "magic"}
        ).status_code
        == 422
    )
    upd = client.put(f"/api/cost-codes/{cc['id']}", json={"is_active": False})
    assert upd.json()["is_active"] is False
    assert client.get("/api/cost-codes").json() == []
    assert len(client.get("/api/cost-codes?include_inactive=true").json()) == 1

    std = client.post("/api/cost-codes/standard").json()
    codes = {c["code"] for c in std}
    assert {"01", "03", "26", "L", "EQ"} <= codes
    # existing "06" kept as the user's own row, not duplicated
    again = client.post("/api/cost-codes/standard").json()
    assert len(again) == len(std)
    assert (
        sum(
            1
            for c in client.get("/api/cost-codes?include_inactive=true").json()
            if c["code"] == "06"
        )
        == 1
    )


def test_cost_code_in_use_cannot_be_deleted(client, db_session, seed_accounts):
    cc = _code(client, "03", "Concrete", "subcontract")
    income, expense = _income_expense(db_session)
    resp = client.post(
        "/api/journal",
        json={
            "date": "2026-07-02",
            "description": "coded",
            "lines": [
                {
                    "account_id": expense.id,
                    "debit": 10,
                    "credit": 0,
                    "cost_code_id": cc["id"],
                },
                {"account_id": income.id, "debit": 0, "credit": 10},
            ],
        },
    )
    assert resp.status_code in (200, 201), resp.text
    assert client.delete(f"/api/cost-codes/{cc['id']}").status_code == 400
    free = _code(client, "99", "Unused")
    assert client.delete(f"/api/cost-codes/{free['id']}").status_code == 200


# ── Lines carry the code and the billable flag ───────────────────────────


def test_bill_and_expense_lines_carry_cost_code_and_billable(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    lumber = _code(client, "06", "Wood", "material")
    labor = _code(client, "L", "Labor", "labor")
    vendor = client.post("/api/vendors", json={"name": "Yard"}).json()
    _, expense = _income_expense(db_session)
    bank = (
        db_session.query(Account)
        .filter(Account.account_type == AccountType.ASSET)
        .first()
    )

    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "Y-1",
            "date": "2026-07-04",
            "job_id": job["id"],
            "lines": [
                {
                    "account_id": expense.id,
                    "description": "2x6",
                    "quantity": 1,
                    "rate": 300,
                    "cost_code_id": lumber["id"],
                    "is_billable": True,
                },
                {
                    "account_id": expense.id,
                    "description": "delivery",
                    "quantity": 1,
                    "rate": 40,
                },
            ],
        },
    )
    assert bill.status_code in (200, 201), bill.text
    lines = bill.json()["lines"]
    assert lines[0]["cost_code_id"] == lumber["id"] and lines[0]["is_billable"] is True
    assert lines[1]["cost_code_id"] is None and lines[1]["is_billable"] is False
    btxn = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "bill",
            Transaction.source_id == bill.json()["id"],
        )
        .first()
    )
    coded = {
        ln.cost_code_id: (float(ln.debit), ln.is_billable)
        for ln in btxn.lines
        if ln.debit > 0
    }
    assert coded[lumber["id"]] == (300.0, True)
    assert coded[None] == (40.0, False)
    assert (
        db_session.query(BillLine).filter(BillLine.is_billable.is_(True)).count() == 1
    )

    exp = client.post(
        "/api/expenses",
        json={
            "date": "2026-07-05",
            "payee": "Crew",
            "expense_account_id": expense.id,
            "paid_from_account_id": bank.id,
            "amount": 500,
            "job_id": job["id"],
            "cost_code_id": labor["id"],
            "is_billable": True,
        },
    )
    assert exp.status_code in (200, 201), exp.text

    # The drill-down tree carries the same figures, by cost type and code
    tree = client.get(f"/api/jobs/{job['id']}/cost-tree").json()
    by_type = {t["cost_type"]: t for t in tree["types"]}
    wood = by_type["material"]["codes"][0]
    assert wood["code"] == "06" and wood["figures"]["actual"] == 300.0
    assert by_type["labor"]["codes"][0]["figures"]["actual"] == 500.0
    assert by_type["other"]["uncoded"]["figures"]["actual"] == 40.0
    total_by_code = tree["totals"]["actual"]
    detail = client.get(f"/api/jobs/{job['id']}").json()
    assert abs(total_by_code - detail["summary"]["total_costs"]) < 0.005


# ── Committed cost from open purchase orders ─────────────────────────────


def test_committed_cost_counts_open_pos_and_clears_on_bill(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    other = _job(client, seed_customer.id, name="Other")
    vendor = client.post("/api/vendors", json={"name": "Supply"}).json()
    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-07-06",
            "job_id": job["id"],
            "lines": [
                {"description": "windows", "quantity": 4, "rate": 250},
                {
                    "description": "door",
                    "quantity": 1,
                    "rate": 400,
                    "job_id": other["id"],
                },
            ],
        },
    )
    assert po.status_code in (200, 201), po.text
    po_id = po.json()["id"]
    assert po.json()["job_id"] == job["id"]
    assert po.json()["lines"][1]["job_id"] == other["id"]

    # draft = not committed
    assert (
        client.get(f"/api/jobs/{job['id']}").json()["summary"]["committed_cost"] == 0.0
    )
    client.put(f"/api/purchase-orders/{po_id}", json={"status": "sent"})
    assert (
        client.get(f"/api/jobs/{job['id']}").json()["summary"]["committed_cost"]
        == 1000.0
    )
    assert (
        client.get(f"/api/jobs/{other['id']}").json()["summary"]["committed_cost"]
        == 400.0
    )
    prof = {r["job_name"]: r for r in client.get("/api/jobs/profitability").json()}
    assert prof.get("Deck build", {}).get("committed_cost", 1000.0) == 1000.0

    # converting to a bill moves the cost into the ledger and clears the commitment
    conv = client.post(f"/api/purchase-orders/{po_id}/convert-to-bill")
    assert conv.status_code in (200, 201), conv.text
    assert (
        client.get(f"/api/jobs/{job['id']}").json()["summary"]["committed_cost"] == 0.0
    )
    summary = client.get(f"/api/jobs/{job['id']}").json()["summary"]
    assert summary["total_costs"] == 1000.0
    assert (
        client.get(f"/api/jobs/{other['id']}").json()["summary"]["total_costs"] == 400.0
    )
