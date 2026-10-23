"""Void a processed pay run (#432).

Voiding reverses the payroll journal entry (a dated reversing entry, both
staying in the register), reverses the burden Job Cost Entry when the run
wrote one, backs the amounts out of each employee's YTD and BenefitYTD, puts
the time entries the run paid back to approved-unpaid, and marks the stubs
void. A closed period refuses the void; a run whose NACHA file was already
made is voided with a warning, since the void does not stop the money.
"""

from decimal import Decimal
from pathlib import Path

from app.models.accounts import Account
from app.models.audit import AuditLog
from app.models.benefits import BenefitYTD
from app.models.job_costing import JobCost
from app.models.transactions import Transaction, TransactionLine

ROOT = Path(__file__).resolve().parents[1]
PAYROLL_JS = (ROOT / "app" / "static" / "js" / "payroll.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _employee(client, **overrides):
    body = {
        "first_name": "Pat",
        "last_name": "Worker",
        "pay_type": "salary",
        "pay_rate": 78000,
        "pay_frequency": "biweekly",
        "filing_status": "single",
        "work_state": "WA",
    }
    body.update(overrides)
    r = client.post("/api/employees", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _run(client, *emp_ids, start="2026-04-01", end="2026-04-15", pay="2026-04-20"):
    r = client.post(
        "/api/payroll",
        json={
            "period_start": start,
            "period_end": end,
            "pay_date": pay,
            "stubs": [{"employee_id": e} for e in emp_ids],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _process(client, run_id):
    r = client.post(f"/api/payroll/{run_id}/process")
    assert r.status_code == 200, r.text
    return r.json()


def _balances(db):
    return {a.id: Decimal(str(a.balance or 0)) for a in db.query(Account).all()}


def _lines(db, txn_id):
    return db.query(TransactionLine).filter_by(transaction_id=txn_id).all()


def _void_txn(db, run_id):
    return (
        db.query(Transaction)
        .filter(
            Transaction.source_type == "payroll_void",
            Transaction.source_id == run_id,
        )
        .first()
    )


# ---------------------------------------------------------------------------
# Journal reversal + balances
# ---------------------------------------------------------------------------
def test_void_reverses_the_journal_and_restores_balances(
    client, db_session, seed_accounts
):
    emp = _employee(client)
    run = _run(client, emp["id"])
    before = _balances(db_session)

    proc = _process(client, run["id"])
    orig_txn_id = proc["transaction_id"]
    assert orig_txn_id is not None

    r = client.post(f"/api/payroll/{run['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"

    void_txn = _void_txn(db_session, run["id"])
    assert void_txn is not None
    assert void_txn.date.isoformat() == "2026-04-20"  # the pay date

    # the void's lines mirror the original's: debit ↔ credit, same accounts,
    # and the entry balances.
    orig = _lines(db_session, orig_txn_id)
    rev = _lines(db_session, void_txn.id)
    orig_dr = {}
    orig_cr = {}
    for ln in orig:
        orig_dr[ln.account_id] = orig_dr.get(ln.account_id, Decimal("0")) + Decimal(
            str(ln.debit)
        )
        orig_cr[ln.account_id] = orig_cr.get(ln.account_id, Decimal("0")) + Decimal(
            str(ln.credit)
        )
    rev_dr = {}
    rev_cr = {}
    for ln in rev:
        rev_dr[ln.account_id] = rev_dr.get(ln.account_id, Decimal("0")) + Decimal(
            str(ln.debit)
        )
        rev_cr[ln.account_id] = rev_cr.get(ln.account_id, Decimal("0")) + Decimal(
            str(ln.credit)
        )
    assert rev_dr == orig_cr and rev_cr == orig_dr
    assert sum(rev_dr.values()) == sum(rev_cr.values()) > 0

    # account balances are back where they were before processing.
    assert _balances(db_session) == before


# ---------------------------------------------------------------------------
# Time entries back to approved-unpaid
# ---------------------------------------------------------------------------
def test_time_entries_go_back_and_a_new_run_takes_them(
    client, db_session, seed_accounts
):
    emp = _employee(client, pay_type="hourly", pay_rate=30)
    te = client.post(
        "/api/time-entries",
        json={"employee_id": emp["id"], "date": "2026-04-05", "hours_regular": 40},
    )
    assert te.status_code == 201, te.text
    te_id = te.json()["id"]
    client.post(f"/api/time-entries/{te_id}/submit")
    client.post(f"/api/time-entries/{te_id}/approve", json={"approved_by": "admin"})

    run = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-04-01",
            "period_end": "2026-04-15",
            "pay_date": "2026-04-20",
            "stubs": [{"employee_id": emp["id"], "use_time_entries": True}],
        },
    ).json()
    _process(client, run["id"])

    from app.models.time_entries import TimeEntry, TimeEntryStatus

    paid = db_session.query(TimeEntry).filter_by(id=te_id).first()
    assert paid.pay_run_id == run["id"]

    r = client.post(f"/api/payroll/{run['id']}/void")
    assert r.status_code == 200, r.text

    db_session.expire_all()
    freed = db_session.query(TimeEntry).filter_by(id=te_id).first()
    assert freed.pay_run_id is None
    assert freed.status == TimeEntryStatus.APPROVED

    # a new run for the same period takes the freed entry.
    again = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-04-01",
            "period_end": "2026-04-15",
            "pay_date": "2026-05-04",
            "stubs": [{"employee_id": emp["id"], "use_time_entries": True}],
        },
    )
    assert again.status_code == 201, again.text
    assert again.json()["stubs"][0]["gross_pay"] == 1200.0  # 40h × $30


# ---------------------------------------------------------------------------
# YTD + BenefitYTD back out
# ---------------------------------------------------------------------------
def test_void_backs_out_ytd_and_benefit_ytd(client, db_session, seed_accounts):
    emp = _employee(client)
    code = client.post(
        "/api/benefits/codes",
        json={
            "code": "DENTAL",
            "name": "Dental",
            "kind": "deduction",
            "category": "pretax",
            "calc_method": "fixed_amount",
            "reduces_federal": True,
            "reduces_state": True,
            "reduces_fica": False,
            "sequence": 100,
            "rate": {"employee_rate": 100},
        },
    )
    assert code.status_code in (200, 201), code.text
    code_id = code.json()["id"]
    client.post(
        "/api/benefits/enrollments",
        json={"employee_id": emp["id"], "benefit_code_id": code_id},
    )

    run = _run(client, emp["id"])
    _process(client, run["id"])

    ytd = client.get(f"/api/employees/{emp['id']}/ytd?year=2026").json()
    assert ytd["gross"] > 0
    row = (
        db_session.query(BenefitYTD)
        .filter_by(employee_id=emp["id"], benefit_code_id=code_id, year=2026)
        .first()
    )
    assert row is not None and Decimal(str(row.employee_amount)) == Decimal("100.00")

    r = client.post(f"/api/payroll/{run['id']}/void")
    assert r.status_code == 200, r.text

    # employee YTD excludes the voided run; with one run it returns to zero.
    ytd_after = client.get(f"/api/employees/{emp['id']}/ytd?year=2026").json()
    assert ytd_after["gross"] == 0

    # BenefitYTD is rebuilt from the surviving (non-void) snapshots: none left.
    db_session.expire_all()
    row_after = (
        db_session.query(BenefitYTD)
        .filter_by(employee_id=emp["id"], benefit_code_id=code_id, year=2026)
        .first()
    )
    assert row_after is None


# ---------------------------------------------------------------------------
# Burden job cost reversal
# ---------------------------------------------------------------------------
def test_void_reverses_the_burden_job_cost(
    client, db_session, seed_accounts, seed_customer
):
    types = {t["code"]: t for t in client.post("/api/cost-types/setup-offsets").json()}
    client.put(
        f"/api/cost-types/{types['labor']['id']}",
        json={"burden_pct": 20, "burden_method": "payroll"},
    )
    job = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Barn"}
    ).json()
    emp = _employee(client, pay_type="hourly", pay_rate=50, cost_rate=50)
    te = client.post(
        "/api/time-entries",
        json={
            "employee_id": emp["id"],
            "date": "2026-04-06",
            "hours_regular": 40,
            "job_id": job["id"],
        },
    ).json()
    client.post(f"/api/time-entries/{te['id']}/submit")
    client.post(f"/api/time-entries/{te['id']}/approve", json={"approved_by": "admin"})

    run = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-04-01",
            "period_end": "2026-04-15",
            "pay_date": "2026-04-20",
            "stubs": [{"employee_id": emp["id"], "use_time_entries": True}],
        },
    ).json()
    proc = _process(client, run["id"])
    jc_id = proc["burden_job_cost_id"]
    assert jc_id, proc

    r = client.post(f"/api/payroll/{run['id']}/void")
    assert r.status_code == 200, r.text

    db_session.expire_all()
    jc = db_session.query(JobCost).filter_by(id=jc_id).first()
    assert jc.status == "void"
    assert (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "job_cost_void",
            Transaction.source_id == jc_id,
        )
        .first()
        is not None
    )


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------
def test_void_twice_is_refused(client, seed_accounts):
    emp = _employee(client)
    run = _run(client, emp["id"])
    _process(client, run["id"])
    assert client.post(f"/api/payroll/{run['id']}/void").status_code == 200
    again = client.post(f"/api/payroll/{run['id']}/void")
    assert again.status_code == 400
    assert "already void" in again.json()["detail"]


def test_void_a_draft_is_refused(client, seed_accounts):
    emp = _employee(client)
    run = _run(client, emp["id"])  # not processed
    r = client.post(f"/api/payroll/{run['id']}/void")
    assert r.status_code == 400
    assert "draft" in r.json()["detail"].lower()


def test_void_in_a_closed_period_is_refused(client, seed_accounts):
    emp = _employee(client)
    run = _run(client, emp["id"])
    _process(client, run["id"])
    # close the period the pay date (2026-04-20) sits in.
    assert (
        client.put("/api/settings", json={"closing_date": "2026-12-31"}).status_code
        == 200
    )
    r = client.post(f"/api/payroll/{run['id']}/void")
    assert r.status_code in (400, 403)
    assert "closing date" in r.json()["detail"].lower()


def test_void_unknown_run_is_404(client, seed_accounts):
    assert client.post("/api/payroll/999/void").status_code == 404


# ---------------------------------------------------------------------------
# NACHA warning
# ---------------------------------------------------------------------------
def test_void_warns_when_a_direct_deposit_file_was_made(
    client, db_session, seed_accounts
):
    client.put(
        "/api/settings",
        json={
            "ach_bank_routing": "021000021",
            "ach_bank_name": "First Example Bank",
            "ach_company_account": "123456789",
        },
    )
    emp = _employee(client)
    client.post(
        f"/api/employees/{emp['id']}/bank-accounts",
        json={
            "account_kind": "checking",
            "routing_number": "121000248",
            "account_number": "9876543210",
            "deposit_type": "full",
        },
    )
    run = _run(client, emp["id"])
    _process(client, run["id"])
    # make the ACH file (records an EXPORT audit row).
    assert client.get(f"/api/payroll/{run['id']}/nacha").status_code == 200

    r = client.post(f"/api/payroll/{run['id']}/void")
    assert r.status_code == 200, r.text
    warnings = r.json()["warnings"]
    assert warnings, r.json()
    net = run["stubs"][0]["net_pay"]
    assert "direct deposit file" in warnings[0]
    assert f"{net:.2f}" in warnings[0]
    assert "does not stop the money" in warnings[0]

    exports = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.table_name == "pay_runs",
            AuditLog.record_id == run["id"],
            AuditLog.action == "EXPORT",
        )
        .all()
    )
    assert len(exports) == 1


# ---------------------------------------------------------------------------
# Stub PDF says VOID
# ---------------------------------------------------------------------------
def test_stub_pdf_says_void_only_for_a_void_run(client, seed_accounts, monkeypatch):
    from app.services import paystub_pdf

    # paystub_pdf binds render_pdf by name, so patch it there (WeasyPrint's
    # native stack is absent on dev Macs; the template path is what we test).
    monkeypatch.setattr(paystub_pdf, "render_pdf", lambda html, **kw: html.encode())
    emp = _employee(client)
    run = _run(client, emp["id"])
    _process(client, run["id"])
    stub_id = run["stubs"][0]["id"]

    html = client.get(f"/api/payroll/{run['id']}/paystub/{stub_id}").text
    assert "PAY STATEMENT" in html and "VOID" not in html

    assert client.post(f"/api/payroll/{run['id']}/void").status_code == 200
    html_void = client.get(f"/api/payroll/{run['id']}/paystub/{stub_id}").text
    assert "VOID" in html_void


# ---------------------------------------------------------------------------
# UI source
# ---------------------------------------------------------------------------
def test_void_button_renders_only_for_processed_and_calls_void():
    assert "PayrollPage.void(" in PAYROLL_JS
    # the list row shows it for a processed run, not a draft.
    assert "r.status === 'processed' ?" in PAYROLL_JS
    assert "async void(id)" in PAYROLL_JS
    assert "Pay run voided" in PAYROLL_JS
