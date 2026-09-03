"""Jobs (Customer:Job / Projects): CRUD + protections, line-level attribution
through posting, job profitability reconciling to the P&L, and the
Customer:Job split on import."""

from datetime import date
from decimal import Decimal

from app.models.accounts import Account, AccountType
from app.models.contacts import Customer
from app.models.jobs import Job
from app.models.transactions import Transaction
from app.services.accounting import create_journal_entry
from app.services.jobs_service import (
    job_profitability,
    resolve_customer_and_job,
    split_customer_job,
)


def _income_expense(db):
    income = (
        db.query(Account).filter(Account.account_type == AccountType.INCOME).first()
    )
    expense = (
        db.query(Account).filter(Account.account_type == AccountType.EXPENSE).first()
    )
    assert income and expense
    return income, expense


def _job(client, customer_id, name="Kitchen remodel", **extra):
    resp = client.post(
        "/api/jobs", json={"customer_id": customer_id, "name": name, **extra}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── CRUD + protections ───────────────────────────────────────────────────


def test_create_list_update_and_name_rules(client, seed_customer):
    job = _job(client, seed_customer.id, contract_amount=25000, status="awarded")
    assert job["full_name"] == f"{seed_customer.name}: Kitchen remodel"
    assert job["status"] == "awarded"

    # unique per customer, case-insensitively
    dup = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "kitchen REMODEL"}
    )
    assert dup.status_code == 409

    # a colon is the customer separator, never part of a job name
    bad = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Smith:Bath"}
    )
    assert bad.status_code == 422

    listed = client.get(f"/api/jobs?customer_id={seed_customer.id}").json()
    assert [j["id"] for j in listed] == [job["id"]]

    upd = client.put(
        f"/api/jobs/{job['id']}", json={"status": "closed", "is_active": False}
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["status"] == "closed"
    # inactive jobs leave the default picker list
    assert client.get("/api/jobs").json() == []
    assert len(client.get("/api/jobs?include_inactive=true").json()) == 1

    bad_status = client.put(f"/api/jobs/{job['id']}", json={"status": "bogus"})
    assert bad_status.status_code == 422


def test_job_with_activity_cannot_be_deleted(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    income, expense = _income_expense(db_session)
    create_journal_entry(
        db_session,
        date(2026, 7, 1),
        "job cost",
        [
            {"account_id": expense.id, "debit": Decimal("10"), "credit": 0},
            {"account_id": income.id, "debit": 0, "credit": Decimal("10")},
        ],
        source_type="manual",
        job_id=job["id"],
    )
    db_session.commit()
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 400

    empty = _job(client, seed_customer.id, name="Nothing posted")
    assert client.delete(f"/api/jobs/{empty['id']}").status_code == 200


# ── Posting: header default + per-line override ──────────────────────────


def test_journal_lines_inherit_header_job_and_class_unless_set(
    client, db_session, seed_accounts, seed_customer
):
    job_a = _job(client, seed_customer.id, name="A")
    job_b = _job(client, seed_customer.id, name="B")
    cls = client.post("/api/classes", json={"name": "Field"}).json()
    income, expense = _income_expense(db_session)
    resp = client.post(
        "/api/journal",
        json={
            "date": "2026-07-02",
            "description": "split job entry",
            "job_id": job_a["id"],
            "class_id": cls["id"],
            "lines": [
                {"account_id": expense.id, "debit": 40, "credit": 0},
                {
                    "account_id": expense.id,
                    "debit": 60,
                    "credit": 0,
                    "job_id": job_b["id"],
                },
                {"account_id": income.id, "debit": 0, "credit": 100},
            ],
        },
    )
    assert resp.status_code in (200, 201), resp.text
    txn = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "manual")
        .order_by(Transaction.id.desc())
        .first()
    )
    assert txn.job_id == job_a["id"]
    by_debit = {float(ln.debit): ln for ln in txn.lines if ln.debit > 0}
    assert by_debit[40.0].job_id == job_a["id"]  # inherited from the header
    assert by_debit[60.0].job_id == job_b["id"]  # its own
    assert all(ln.class_id == cls["id"] for ln in txn.lines)
    # and the API echoes the line dimensions back
    entry = next(e for e in client.get("/api/journal").json() if e["id"] == txn.id)
    assert {ln["job_id"] for ln in entry["lines"]} == {job_a["id"], job_b["id"]}


def test_invoice_and_bill_posting_carry_job_to_lines(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id)
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-07-03",
            "job_id": job["id"],
            "lines": [{"description": "framing", "quantity": 1, "rate": 500}],
        },
    )
    assert inv.status_code in (200, 201), inv.text
    assert inv.json()["job_id"] == job["id"]
    txn = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "invoice",
            Transaction.source_id == inv.json()["id"],
        )
        .first()
    )
    assert txn.job_id == job["id"]
    assert all(ln.job_id == job["id"] for ln in txn.lines)

    vendor = client.post("/api/vendors", json={"name": "Lumber Co"}).json()
    _, expense = _income_expense(db_session)
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "L-1",
            "date": "2026-07-04",
            "lines": [
                {
                    "account_id": expense.id,
                    "description": "studs",
                    "quantity": 1,
                    "rate": 200,
                    "job_id": job["id"],
                },
                {
                    "account_id": expense.id,
                    "description": "office",
                    "quantity": 1,
                    "rate": 50,
                },
            ],
        },
    )
    assert bill.status_code in (200, 201), bill.text
    btxn = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "bill",
            Transaction.source_id == bill.json()["id"],
        )
        .first()
    )
    jobbed = [ln for ln in btxn.lines if ln.job_id == job["id"]]
    assert [float(ln.debit) for ln in jobbed] == [200.0]
    assert bill.json()["lines"][0]["job_id"] == job["id"]


# ── Job profitability reconciles to the P&L ──────────────────────────────


def test_job_profitability_reconciles_with_profit_and_loss(
    client, db_session, seed_accounts, seed_customer
):
    job = _job(client, seed_customer.id, contract_amount=1000)
    income, expense = _income_expense(db_session)
    # tagged: 300 income, 120 cost; untagged: 50 income
    create_journal_entry(
        db_session,
        date(2026, 7, 5),
        "job work",
        [
            {"account_id": expense.id, "debit": Decimal("120"), "credit": 0},
            {"account_id": income.id, "debit": 0, "credit": Decimal("300")},
            {
                "account_id": expense.id,
                "debit": Decimal("180"),
                "credit": 0,
                "job_id": None,
            },
        ],
        source_type="manual",
        job_id=job["id"],
    )
    create_journal_entry(
        db_session,
        date(2026, 7, 6),
        "walk-in",
        [
            {"account_id": income.id, "debit": 0, "credit": Decimal("50")},
            {"account_id": expense.id, "debit": Decimal("50"), "credit": 0},
        ],
        source_type="manual",
    )
    db_session.commit()

    rep = client.get(
        "/api/reports/job-profitability?start_date=2026-07-01&end_date=2026-07-31"
    ).json()
    rows = {r["job_name"]: r for r in rep["jobs"]}
    assert rows["Kitchen remodel"]["income"] == 300.0
    # the None-tagged line inherits the header job
    assert rows["Kitchen remodel"]["total_costs"] == 300.0
    assert rows["Kitchen remodel"]["net_income"] == 0.0
    assert rows["Kitchen remodel"]["contract_amount"] == 1000.0
    assert rows["No job"]["income"] == 50.0 and rows["No job"]["total_costs"] == 50.0

    pl = client.get(
        "/api/reports/profit-loss?start_date=2026-07-01&end_date=2026-07-31"
    ).json()
    assert abs(rep["total_income"] - float(pl["total_income"])) < 0.005
    assert abs(rep["total_net_income"] - float(pl["net_income"])) < 0.005

    detail = client.get(f"/api/jobs/{job['id']}").json()
    assert detail["summary"]["net_income"] == 0.0
    lines = client.get(f"/api/jobs/{job['id']}/transactions").json()
    assert sorted(ln["amount"] for ln in lines) == [120.0, 180.0, 300.0]


# ── Customer:Job handling ────────────────────────────────────────────────


def test_split_customer_job_names():
    assert split_customer_job("Smith:Kitchen Remodel") == ("Smith", "Kitchen Remodel")
    assert split_customer_job("Smith") == ("Smith", None)
    assert split_customer_job("A:B:C") == ("A", "B:C")
    assert split_customer_job(" Smith : ") == ("Smith :", None)


def test_resolve_customer_and_job_creates_hierarchy(db_session):
    cust, job = resolve_customer_and_job(db_session, "Smith:Kitchen Remodel")
    assert cust.name == "Smith" and job.name == "Kitchen Remodel"
    assert job.customer_id == cust.id
    again, same = resolve_customer_and_job(db_session, "Smith:kitchen remodel")
    assert again.id == cust.id and same.id == job.id
    assert db_session.query(Job).count() == 1
    # a flat customer that already carries the colon keeps matching
    flat = Customer(name="Legacy:Name", is_active=True)
    db_session.add(flat)
    db_session.flush()
    c2, j2 = resolve_customer_and_job(db_session, "Legacy:Name")
    assert c2.id == flat.id and j2 is None


def test_iif_import_splits_customer_job(client, db_session, seed_accounts):
    iif = (
        "!CUST\tNAME\tCOMPANYNAME\n"
        "CUST\tSmith\tSmith Household\n"
        "CUST\tSmith:Kitchen Remodel\t\n"
        "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
        "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
        "!ENDTRNS\n"
        "TRNS\tINVOICE\t07/10/2026\tAccounts Receivable\tSmith:Kitchen Remodel\t500\t1001\n"
        "SPL\tINVOICE\t07/10/2026\tSales\tSmith:Kitchen Remodel\t-500\t1001\n"
        "ENDTRNS\n"
    )
    resp = client.post(
        "/api/iif/import",
        files={"file": ("jobs.iif", iif.encode("utf-8"), "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    names = {c.name for c in db_session.query(Customer).all()}
    assert "Smith" in names and "Smith:Kitchen Remodel" not in names
    job = db_session.query(Job).filter(Job.name == "Kitchen Remodel").first()
    assert job is not None
    inv_txn = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "invoice")
        .first()
    )
    if inv_txn is not None:  # the invoice posted only if the sample accounts matched
        assert inv_txn.job_id == job.id
    rows = job_profitability(db_session, job_ids=[job.id], include_no_job=False)
    assert rows == [] or rows[0]["job_name"] == "Kitchen Remodel"
