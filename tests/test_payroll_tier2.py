"""Tests for the Tier 2 payroll layer.

Covers pre-tax deduction FICA treatment, post-tax deductions and CCPA
garnishments, supplemental (bonus) withholding methods, net-to-gross,
non-taxable reimbursements, multi-state withholding, and the quarterly tax
liability report.
"""
from decimal import Decimal


# ---------------------------------------------------------------------------
# Unit — pre-tax deduction tax treatment
# ---------------------------------------------------------------------------
def test_401k_reduces_income_tax_but_not_fica():
    from app.services.payroll_service import calculate_withholdings
    plain = calculate_withholdings(Decimal("3000"), work_state="WA")
    k401 = calculate_withholdings(Decimal("3000"), pretax_deductions=Decimal("500"),
                                  pretax_fica=Decimal("0"), work_state="WA")
    assert k401["federal"] < plain["federal"]   # 401(k) defers income tax
    assert k401["ss"] == plain["ss"]            # ...but not Social Security


def test_section125_reduces_income_tax_and_fica():
    from app.services.payroll_service import calculate_withholdings
    k401 = calculate_withholdings(Decimal("3000"), pretax_deductions=Decimal("500"),
                                  pretax_fica=Decimal("0"), work_state="WA")
    sec125 = calculate_withholdings(Decimal("3000"), pretax_deductions=Decimal("500"),
                                    pretax_fica=Decimal("500"), work_state="WA")
    assert sec125["federal"] == k401["federal"]  # same income-tax reduction
    assert sec125["ss"] < k401["ss"]             # cafeteria plan also cuts FICA


# ---------------------------------------------------------------------------
# Unit — supplemental wage methods
# ---------------------------------------------------------------------------
def test_supplemental_flat_rate_is_22_percent():
    from app.services.payroll_service import supplemental_federal_tax
    assert supplemental_federal_tax(Decimal("1000")) == Decimal("220.00")


def test_supplemental_aggregate_differs_from_flat():
    from app.services.payroll_service import (
        supplemental_aggregate_tax, supplemental_federal_tax,
    )
    agg = supplemental_aggregate_tax(Decimal("1000"), Decimal("2000"), 26, "single")
    flat = supplemental_federal_tax(Decimal("1000"))
    assert agg >= 0 and agg != flat


# ---------------------------------------------------------------------------
# Unit — garnishment (CCPA)
# ---------------------------------------------------------------------------
def test_disposable_earnings_excludes_only_mandatory():
    from app.services.garnishment import compute_disposable_earnings
    assert compute_disposable_earnings(Decimal("2000"), Decimal("400")) == Decimal("1600.00")


def test_creditor_garnishment_capped_at_25_percent():
    from app.services.garnishment import GarnishmentSpec, apply_garnishments
    specs = [GarnishmentSpec(1, "creditor", "percent_disposable", Decimal("50"))]
    results = apply_garnishments(Decimal("1000"), specs, weeks_in_period=2)
    # A creditor order can never exceed 25% of disposable earnings.
    assert results[0].amount <= Decimal("250.00")
    assert results[0].capped is True


def test_child_support_takes_priority():
    from app.services.garnishment import GarnishmentSpec, apply_garnishments
    specs = [
        GarnishmentSpec(1, "creditor", "fixed", Decimal("300"), priority=5),
        GarnishmentSpec(2, "child_support", "percent_disposable", Decimal("60"),
                        priority=1),
    ]
    results = apply_garnishments(Decimal("1000"), specs, weeks_in_period=2)
    # Child support is processed first regardless of priority number.
    assert results[0].garnishment_type == "child_support"


# ---------------------------------------------------------------------------
# Unit — gross-up + reciprocity
# ---------------------------------------------------------------------------
def test_gross_up_solves_for_target_net():
    from app.services.gross_up import gross_up
    gross = gross_up(Decimal("1000"), lambda g: g * Decimal("0.8"))
    assert abs(gross * Decimal("0.8") - Decimal("1000")) <= Decimal("0.01")


def test_reciprocity_resolves_withholding_state():
    from app.services.state_tax.reciprocity import withholding_state, has_reciprocity
    assert has_reciprocity("OH", "KY") is True
    assert withholding_state("OH", "KY") == "KY"   # agreement -> residence state
    assert withholding_state("CA", "NY") == "CA"   # no agreement -> work state
    assert withholding_state("WA", None) == "WA"


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------
def _create_employee(client, **overrides):
    body = {
        "first_name": "Sam", "last_name": "Earner",
        "pay_type": "hourly", "pay_rate": 40, "pay_frequency": "biweekly",
        "filing_status": "single", "work_state": "WA",
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


def _run_and_process(client, emp_id, **stub_extra):
    stub = {"employee_id": emp_id, "hours": 80}
    stub.update(stub_extra)
    run = client.post("/api/payroll", json={
        "period_start": "2026-04-01", "period_end": "2026-04-15",
        "pay_date": "2026-04-20", "stubs": [stub],
    }).json()
    proc = client.post(f"/api/payroll/{run['id']}/process")
    assert proc.status_code == 200, proc.text
    return run, proc.json()


# ---------------------------------------------------------------------------
# Integration — deduction types and employee deductions
# ---------------------------------------------------------------------------
def test_seed_standard_deduction_types(client):
    r = client.post("/api/deductions/types/seed-standard")
    assert r.status_code == 200, r.text
    codes = {t["code"] for t in r.json()}
    assert {"401K", "HSA", "SEC125", "ROTH401K"} <= codes


def test_employee_401k_deduction_applied_to_pay_run(client, db_session, seed_accounts):
    emp = _create_employee(client)
    types = client.post("/api/deductions/types/seed-standard").json()
    k401 = next(t for t in types if t["code"] == "401K")
    client.post("/api/deductions/employee", json={
        "employee_id": emp["id"], "deduction_type_id": k401["id"],
        "calc_method": "fixed", "amount": 300,
    })
    run, _ = _run_and_process(client, emp["id"])
    stub = run["stubs"][0]
    assert stub["pretax_deductions"] == 300.0
    # net = gross - employee tax - pre-tax deduction
    assert stub["net_pay"] < stub["gross_pay"] - 300


# ---------------------------------------------------------------------------
# Integration — garnishments
# ---------------------------------------------------------------------------
def test_garnishment_order_withheld_on_pay_run(client, db_session, seed_accounts):
    emp = _create_employee(client)
    client.post("/api/deductions/garnishments", json={
        "employee_id": emp["id"], "garnishment_type": "creditor",
        "calc_method": "percent_disposable", "amount": 25, "priority": 1,
    })
    run, proc = _run_and_process(client, emp["id"])
    stub = run["stubs"][0]
    assert stub["garnishments"] > 0
    # The journal still balances with the garnishment payable.
    dr, cr = _sum_debits_credits(db_session, proc["transaction_id"])
    assert dr == cr and dr > 0


# ---------------------------------------------------------------------------
# Integration — reimbursements
# ---------------------------------------------------------------------------
def test_nontaxable_reimbursement_adds_to_net_only(client, db_session, seed_accounts):
    emp = _create_employee(client)
    base_run, _ = _run_and_process(client, emp["id"])
    base_net = base_run["stubs"][0]["net_pay"]

    run, proc = _run_and_process(client, emp["id"], reimbursements=150)
    stub = run["stubs"][0]
    assert stub["reimbursements"] == 150.0
    # Reimbursement is not taxed: net rises by exactly the reimbursement.
    assert round(stub["net_pay"] - base_net, 2) == 150.0
    dr, cr = _sum_debits_credits(db_session, proc["transaction_id"])
    assert dr == cr


# ---------------------------------------------------------------------------
# Integration — gross-up
# ---------------------------------------------------------------------------
def test_gross_up_endpoint(client):
    emp = _create_employee(client)
    r = client.post("/api/payroll/gross-up", json={
        "employee_id": emp["id"], "target_net": 2000, "supplemental": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["gross"] > 2000               # gross must exceed the target net
    assert abs(body["net"] - 2000) <= 1.0      # solved net lands on the target


# ---------------------------------------------------------------------------
# Integration — multi-state
# ---------------------------------------------------------------------------
def test_per_stub_work_state_override(client, db_session, seed_accounts):
    # Employee's home state is CA, but this stub's work was performed in WA.
    emp = _create_employee(client, work_state="CA")
    run, _ = _run_and_process(client, emp["id"], work_state="WA")
    stub = run["stubs"][0]
    assert stub["work_state"] == "WA"
    assert stub["state_tax"] == 0.0   # WA has no state income tax


# ---------------------------------------------------------------------------
# Integration — full Tier 2 stack stays balanced
# ---------------------------------------------------------------------------
def test_pay_run_with_deduction_garnishment_reimbursement_balances(
        client, db_session, seed_accounts):
    emp = _create_employee(client)
    types = client.post("/api/deductions/types/seed-standard").json()
    k401 = next(t for t in types if t["code"] == "401K")
    client.post("/api/deductions/employee", json={
        "employee_id": emp["id"], "deduction_type_id": k401["id"],
        "calc_method": "fixed", "amount": 200,
    })
    client.post("/api/deductions/garnishments", json={
        "employee_id": emp["id"], "garnishment_type": "creditor",
        "calc_method": "fixed", "amount": 100, "priority": 1,
    })
    run, proc = _run_and_process(client, emp["id"], reimbursements=75,
                                 posttax_deductions=50)
    stub = run["stubs"][0]
    assert stub["pretax_deductions"] == 200.0
    assert stub["garnishments"] > 0
    assert stub["reimbursements"] == 75.0
    dr, cr = _sum_debits_credits(db_session, proc["transaction_id"])
    assert dr == cr and dr > 0


# ---------------------------------------------------------------------------
# Integration — quarterly tax liability report
# ---------------------------------------------------------------------------
def test_quarterly_tax_liability_report(client, db_session, seed_accounts):
    emp = _create_employee(client, pay_type="salary", pay_rate=78000)
    run = client.post("/api/payroll", json={
        "period_start": "2026-04-01", "period_end": "2026-04-15",
        "pay_date": "2026-04-20",
        "stubs": [{"employee_id": emp["id"]}],
    }).json()
    client.post(f"/api/payroll/{run['id']}/process")

    r = client.get("/api/tax-forms/liability?year=2026&quarter=2")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total_due"] > 0
    forms = {item["form"] for item in data["liabilities"]}
    assert {"941", "940", "State SUI", "State WH"} <= forms
    # Q2 federal employment tax is due July 31.
    federal = next(i for i in data["liabilities"] if i["form"] == "941")
    assert federal["due_date"] == "2026-07-31"
