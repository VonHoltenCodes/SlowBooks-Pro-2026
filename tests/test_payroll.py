"""Tests for the Tier 1 payroll system.

Covers the IRS Pub 15-T withholding calculator, the per-state tax engines,
overtime classification, PTO accrual, and the full pay-run posting path —
including the four roadmap bug fixes (YTD threading, modern W-4, per-employee
pay frequency, balanced payroll journal).
"""

from decimal import Decimal


# ---------------------------------------------------------------------------
# Unit — federal withholding (Pub 15-T Worksheet 1A)
# ---------------------------------------------------------------------------
def test_federal_withholding_single_biweekly():
    from app.services.payroll_service import federal_income_tax

    # $2,000 biweekly, single -> annual 52,000; adjusted 43,400 after the
    # $8,600 standard add-back; tax 4,256/yr -> 163.69 per period.
    tax = federal_income_tax(Decimal("2000"), 26, "single")
    assert tax == Decimal("163.69")


def test_dependents_credit_reduces_withholding():
    from app.services.payroll_service import federal_income_tax

    plain = federal_income_tax(Decimal("2000"), 26, "married")
    with_kids = federal_income_tax(
        Decimal("2000"), 26, "married", dependents_amount=Decimal("4000")
    )
    assert with_kids < plain
    assert with_kids == Decimal("0")  # the credit fully absorbs the withholding


def test_extra_withholding_is_added():
    from app.services.payroll_service import federal_income_tax

    base = federal_income_tax(Decimal("2000"), 26, "single")
    extra = federal_income_tax(
        Decimal("2000"), 26, "single", extra_withholding=Decimal("50")
    )
    assert extra == base + Decimal("50")


def test_multiple_jobs_checkbox_increases_withholding():
    from app.services.payroll_service import federal_income_tax

    single = federal_income_tax(Decimal("2000"), 26, "single")
    both = federal_income_tax(Decimal("2000"), 26, "single", multiple_jobs=True)
    assert both > single


# ---------------------------------------------------------------------------
# Unit — FICA
# ---------------------------------------------------------------------------
def test_social_security_wage_base_cap():
    from app.services.payroll_service import social_security, SS_WAGE_BASE

    # Already over the cap -> no SS this period.
    emp, empr = social_security(Decimal("5000"), ytd_gross=SS_WAGE_BASE + 1)
    assert emp == Decimal("0") and empr == Decimal("0")
    # Straddling the cap -> only the under-cap slice is taxed.
    emp2, _ = social_security(Decimal("5000"), ytd_gross=SS_WAGE_BASE - Decimal("1000"))
    assert emp2 == (Decimal("1000") * Decimal("0.062")).quantize(Decimal("0.01"))


def test_additional_medicare_over_200k():
    from app.services.payroll_service import medicare

    emp, empr = medicare(Decimal("10000"), ytd_gross=Decimal("250000"))
    # Employee pays 1.45% + 0.9% additional; employer pays only 1.45%.
    assert emp == Decimal("10000") * Decimal("0.0235")
    assert empr == Decimal("10000") * Decimal("0.0145")


# ---------------------------------------------------------------------------
# Unit — state tax engines
# ---------------------------------------------------------------------------
def test_wa_engine_no_income_tax_but_pfml_and_cares():
    from app.services.state_tax import get_engine

    r = get_engine("WA").calculate(
        gross=Decimal("2000"),
        taxable=Decimal("2000"),
        ytd_gross=Decimal("0"),
        pay_periods=26,
        hours=Decimal("80"),
        filing_status="single",
        wc_class_code="5206",
    )
    assert r.income_tax == Decimal("0")  # WA has no state income tax
    assert r.employee_other > 0  # but PFML + WA Cares + L&I apply


def test_unknown_state_falls_back_to_generic():
    from app.services.state_tax import get_engine

    r = get_engine("ZZ").calculate(
        gross=Decimal("2000"),
        taxable=Decimal("2000"),
        ytd_gross=Decimal("0"),
        pay_periods=26,
        hours=Decimal("80"),
        filing_status="single",
        wc_class_code=None,
    )
    assert r.income_tax == Decimal("0")


# ---------------------------------------------------------------------------
# Unit — overtime classification
# ---------------------------------------------------------------------------
def test_overtime_flsa_weekly_over_40():
    from app.services.overtime import classify_week

    r = classify_week([Decimal("9")] * 5, "WA")  # 45 hours
    assert r["regular"] == Decimal("40.00")
    assert r["overtime"] == Decimal("5.00")
    assert r["doubletime"] == Decimal("0.00")


def test_overtime_ca_daily_rule():
    from app.services.overtime import classify_week

    r = classify_week([Decimal("13")], "CA")  # one 13-hour day
    assert r["regular"] == Decimal("8.00")
    assert r["overtime"] == Decimal("4.00")  # hours 9-12
    assert r["doubletime"] == Decimal("1.00")  # hours over 12


# ---------------------------------------------------------------------------
# Unit — PTO accrual
# ---------------------------------------------------------------------------
def test_wa_sick_leave_accrual_rate():
    from app.services.pto_accrual import wa_sick_accrual

    assert wa_sick_accrual(Decimal("80")) == Decimal("2.00")  # 1 hr per 40 worked


def test_pto_carryover_cap():
    from app.services.pto_accrual import apply_carryover

    assert apply_carryover(Decimal("60"), Decimal("40")) == Decimal("40.00")
    assert apply_carryover(Decimal("25"), Decimal("40")) == Decimal("25.00")
    assert apply_carryover(Decimal("999"), None) == Decimal("999.00")


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------
def _create_employee(client, **overrides):
    body = {
        "first_name": "Pat",
        "last_name": "Worker",
        "pay_type": "hourly",
        "pay_rate": 25,
        "pay_frequency": "biweekly",
        "filing_status": "single",
        "work_state": "WA",
    }
    body.update(overrides)
    r = client.post("/api/employees", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _sum_debits_credits(db_session, txn_id):
    from app.models.transactions import TransactionLine

    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn_id).all()
    return (
        sum((Decimal(str(l.debit)) for l in lines), Decimal("0")),
        sum((Decimal(str(l.credit)) for l in lines), Decimal("0")),
    )


# ---------------------------------------------------------------------------
# Integration — employees and the modern W-4 (Bug 2)
# ---------------------------------------------------------------------------
def test_employee_uses_modern_w4_fields(client):
    emp = _create_employee(
        client, dependents_amount=2000, multiple_jobs=True, extra_withholding=25
    )
    assert emp["dependents_amount"] == 2000
    assert emp["multiple_jobs"] is True
    assert emp["extra_withholding"] == 25
    assert "allowances" not in emp  # the pre-2020 field is gone


# ---------------------------------------------------------------------------
# Integration — pay frequency drives the salary divisor (Bug 3)
# ---------------------------------------------------------------------------
def test_pay_frequency_drives_salary_divisor(client):
    monthly = _create_employee(
        client,
        first_name="Mona",
        pay_type="salary",
        pay_rate=120000,
        pay_frequency="monthly",
    )
    biweekly = _create_employee(
        client,
        first_name="Bea",
        pay_type="salary",
        pay_rate=120000,
        pay_frequency="biweekly",
    )
    r = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-04-01",
            "period_end": "2026-04-15",
            "pay_date": "2026-04-20",
            "stubs": [{"employee_id": monthly["id"]}, {"employee_id": biweekly["id"]}],
        },
    )
    assert r.status_code == 201, r.text
    stubs = {s["employee_id"]: s for s in r.json()["stubs"]}
    assert stubs[monthly["id"]]["gross_pay"] == 10000.0  # 120000 / 12
    assert stubs[biweekly["id"]]["gross_pay"] == 4615.38  # 120000 / 26


# ---------------------------------------------------------------------------
# Integration — pay run posts a balanced journal (Bug 4 / overall correctness)
# ---------------------------------------------------------------------------
def test_pay_run_process_posts_balanced_journal(client, db_session, seed_accounts):
    emp = _create_employee(
        client, pay_type="salary", pay_rate=78000, pay_frequency="biweekly"
    )
    r = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-04-01",
            "period_end": "2026-04-15",
            "pay_date": "2026-04-20",
            "stubs": [{"employee_id": emp["id"]}],
        },
    )
    assert r.status_code == 201, r.text
    run = r.json()
    stub = run["stubs"][0]
    # gross less every employee deduction equals net.
    employee_tax = (
        stub["federal_tax"]
        + stub["state_tax"]
        + stub["state_other_employee"]
        + stub["ss_tax"]
        + stub["medicare_tax"]
    )
    assert round(stub["gross_pay"] - employee_tax, 2) == stub["net_pay"]
    # employer-side taxes were captured separately.
    assert stub["employer_ss_tax"] > 0 and stub["futa_tax"] > 0

    pr = client.post(f"/api/payroll/{run['id']}/process")
    assert pr.status_code == 200, pr.text
    txn_id = pr.json()["transaction_id"]
    assert txn_id is not None

    dr, cr = _sum_debits_credits(db_session, txn_id)
    assert dr == cr
    assert dr > 0


def test_pay_run_double_process_rejected(client, db_session, seed_accounts):
    emp = _create_employee(client)
    run = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-05-01",
            "period_end": "2026-05-15",
            "pay_date": "2026-05-20",
            "stubs": [{"employee_id": emp["id"], "hours": 80}],
        },
    ).json()
    assert client.post(f"/api/payroll/{run['id']}/process").status_code == 200
    again = client.post(f"/api/payroll/{run['id']}/process")
    assert again.status_code == 400


# ---------------------------------------------------------------------------
# Integration — YTD is threaded into the calculator (Bug 1)
# ---------------------------------------------------------------------------
def test_ytd_endpoint_accumulates_across_pay_runs(client, db_session, seed_accounts):
    emp = _create_employee(
        client, pay_type="salary", pay_rate=52000, pay_frequency="biweekly"
    )
    for pay_date in ("2026-01-15", "2026-01-31"):
        run = client.post(
            "/api/payroll",
            json={
                "period_start": "2026-01-01",
                "period_end": "2026-01-14",
                "pay_date": pay_date,
                "stubs": [{"employee_id": emp["id"]}],
            },
        ).json()
        client.post(f"/api/payroll/{run['id']}/process")

    r = client.get(f"/api/employees/{emp['id']}/ytd?year=2026")
    assert r.status_code == 200, r.text
    ytd = r.json()
    # Two pay runs of $2,000 each.
    assert round(ytd["gross"], 2) == 4000.00
    assert ytd["ss"] > 0 and ytd["federal"] > 0


def test_ytd_threads_into_ss_cap(client, db_session, seed_accounts):
    # An employee already paid above the SS wage base pays no further SS.
    from app.services.payroll_service import SS_WAGE_BASE

    emp = _create_employee(
        client,
        pay_type="salary",
        pay_rate=float(SS_WAGE_BASE * 4),  # huge salary
        pay_frequency="monthly",
    )
    last_stub = None
    for month in range(1, 13):
        run = client.post(
            "/api/payroll",
            json={
                "period_start": f"2026-{month:02d}-01",
                "period_end": f"2026-{month:02d}-28",
                "pay_date": f"2026-{month:02d}-28",
                "stubs": [{"employee_id": emp["id"]}],
            },
        ).json()
        client.post(f"/api/payroll/{run['id']}/process")
        last_stub = run["stubs"][0]
    # By December, YTD wages are far past the cap -> SS withholding is zero.
    assert last_stub["ss_tax"] == 0.0


# ---------------------------------------------------------------------------
# Integration — direct deposit bank accounts (encryption)
# ---------------------------------------------------------------------------
def test_bank_account_numbers_stored_encrypted(client, db_session):
    emp = _create_employee(client)
    r = client.post(
        f"/api/employees/{emp['id']}/bank-accounts",
        json={
            "account_kind": "checking",
            "routing_number": "123456789",
            "account_number": "9876543210",
            "deposit_type": "full",
        },
    )
    assert r.status_code == 201, r.text
    ba = r.json()
    assert ba["account_last_four"] == "3210"
    # The API response never exposes the raw numbers.
    assert "account_number" not in ba and "routing_number" not in ba

    from app.models.bank_accounts import EmployeeBankAccount
    from app.services.encryption import decrypt

    row = db_session.query(EmployeeBankAccount).filter_by(id=ba["id"]).first()
    assert row.account_number_enc != "9876543210"  # ciphertext at rest
    assert decrypt(row.account_number_enc) == "9876543210"  # round-trips
    assert decrypt(row.routing_number_enc) == "123456789"


def test_bank_account_rejects_bad_routing_number(client):
    emp = _create_employee(client)
    r = client.post(
        f"/api/employees/{emp['id']}/bank-accounts",
        json={
            "routing_number": "12345",
            "account_number": "9876543210",
        },
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Integration — time entries
# ---------------------------------------------------------------------------
def test_time_entry_approval_workflow(client):
    emp = _create_employee(client)
    te = client.post(
        "/api/time-entries",
        json={
            "employee_id": emp["id"],
            "date": "2026-04-01",
            "hours_regular": 8,
        },
    ).json()
    assert te["status"] == "draft"
    approved = client.post(
        f"/api/time-entries/{te['id']}/approve", json={"approved_by": "boss"}
    ).json()
    assert approved["status"] == "approved"
    assert approved["approved_by"] == "boss"


def test_time_entries_feed_pay_run(client, db_session, seed_accounts):
    emp = _create_employee(client, pay_type="hourly", pay_rate=30)
    for d in ("2026-06-01", "2026-06-02"):
        te = client.post(
            "/api/time-entries",
            json={
                "employee_id": emp["id"],
                "date": d,
                "hours_regular": 8,
            },
        ).json()
        client.post(
            f"/api/time-entries/{te['id']}/approve", json={"approved_by": "boss"}
        )
    run = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-06-01",
            "period_end": "2026-06-14",
            "pay_date": "2026-06-20",
            "stubs": [{"employee_id": emp["id"], "use_time_entries": True}],
        },
    ).json()
    # 16 approved hours x $30 = $480 gross.
    assert run["stubs"][0]["gross_pay"] == 480.0
    assert run["stubs"][0]["regular_hours"] == 16.0


# ---------------------------------------------------------------------------
# Integration — PTO
# ---------------------------------------------------------------------------
def test_pto_policy_accrual_and_request(client):
    emp = _create_employee(client)
    policy = client.post(
        "/api/pto/policies",
        json={
            "name": "Vacation",
            "pto_type": "vacation",
            "accrual_method": "per_pay_period",
            "accrual_rate": 4,
        },
    ).json()
    accrual = client.post(
        "/api/pto/accruals",
        json={
            "employee_id": emp["id"],
            "policy_id": policy["id"],
            "balance": 10,
        },
    ).json()
    # One accrual cycle adds 4 hours.
    after = client.post(
        f"/api/pto/accruals/{accrual['id']}/accrue", json={"hours_worked": 80}
    ).json()
    assert after["balance"] == 14.0

    # Approving a request draws the balance back down.
    req = client.post(
        "/api/pto/requests",
        json={
            "employee_id": emp["id"],
            "start_date": "2026-07-01",
            "end_date": "2026-07-01",
            "hours": 8,
            "pto_type": "vacation",
        },
    ).json()
    decided = client.post(
        f"/api/pto/requests/{req['id']}/decision", json={"status": "approved"}
    ).json()
    assert decided["status"] == "approved"
    balances = client.get(f"/api/pto/accruals?employee_id={emp['id']}").json()
    assert balances[0]["balance"] == 6.0  # 14 - 8


# ---------------------------------------------------------------------------
# Integration — tax forms
# ---------------------------------------------------------------------------
def test_form_941_aggregates_quarter(client, db_session, seed_accounts):
    emp = _create_employee(client, pay_type="salary", pay_rate=78000)
    run = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-04-01",
            "period_end": "2026-04-15",
            "pay_date": "2026-04-20",
            "stubs": [{"employee_id": emp["id"]}],
        },
    ).json()
    client.post(f"/api/payroll/{run['id']}/process")

    r = client.get("/api/tax-forms/941?year=2026&quarter=2")
    assert r.status_code == 200, r.text
    data = r.json()
    # The processed pay run shows up in the Q2 941 totals.
    assert data["quarter"] == 2
    stub = run["stubs"][0]
    # Total wages should reflect the one pay stub's gross.
    assert any(
        abs(float(v) - stub["gross_pay"]) < 0.01
        for v in data.values()
        if isinstance(v, (int, float))
    )
