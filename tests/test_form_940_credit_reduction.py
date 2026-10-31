"""Form 940 credit reduction (Schedule A).

A company in a credit-reduction state owes extra FUTA on its FUTA taxable
wages: for the 2025 return, California 1.2% and the U.S. Virgin Islands
4.5% (Schedule A (Form 940) for 2025). The company's state comes from
Settings (company_state), else the EMPLOYER_STATE setting; any other
employer's 940 keeps the figures it always had.
"""

from decimal import Decimal

import pytest

from app.services.settings_service import set_setting
from app.services.tax_forms.form_940 import (
    FUTA_CREDIT_REDUCTION,
    compute_940,
    credit_reduction_rate,
)

D = Decimal


@pytest.fixture(autouse=True)
def _chart(seed_accounts):
    return seed_accounts


def _employee(client, state):
    r = client.post(
        "/api/employees",
        json={
            "first_name": "Ines",
            "last_name": "Moreno",
            "pay_type": "salary",
            "pay_rate": 104000,
            "pay_frequency": "biweekly",
            "filing_status": "single",
            "work_state": state,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _run(client, emp_id, pay, gross):
    r = client.post(
        "/api/payroll",
        json={
            "period_start": f"{pay[:8]}01",
            "period_end": pay,
            "pay_date": pay,
            "stubs": [{"employee_id": emp_id, "gross_override": gross}],
        },
    )
    assert r.status_code == 201, r.text
    p = client.post(f"/api/payroll/{r.json()['id']}/process")
    assert p.status_code == 200, p.text


def test_schedule_a_2025_rates():
    assert FUTA_CREDIT_REDUCTION[2025] == {"CA": D("0.012"), "VI": D("0.045")}
    assert credit_reduction_rate(2025, "ca") == D("0.012")
    assert credit_reduction_rate(2025, "WA") == D("0")
    assert credit_reduction_rate(2024, "CA") == D("0")
    assert credit_reduction_rate(2025, None) == D("0")


def test_california_employer_940_carries_the_credit_reduction(client, db_session):
    set_setting(db_session, "company_state", "CA")
    db_session.commit()
    emp = _employee(client, "CA")
    # Two $4,000 checks: FUTA wages 7,000 (the base), 0.6% = 42.00 before
    # adjustments; Schedule A line CA: 7,000 x 0.012 = 84.00; line 12 = 126.00
    _run(client, emp["id"], "2025-11-14", 4000)
    _run(client, emp["id"], "2025-11-28", 4000)

    f = compute_940(db_session, 2025)
    assert f["futa_taxable_wages"] == D("7000.00")
    assert f["total_futa_tax"] == D("42.00")
    assert f["credit_reduction_state"] == "CA"
    assert f["credit_reduction_rate"] == D("0.012")
    assert f["credit_reduction"] == D("84.00")
    assert f["total_futa_tax_after_adjustments"] == D("126.00")


def test_other_employers_940_is_unchanged(client, db_session):
    set_setting(db_session, "company_state", "TX")
    db_session.commit()
    emp = _employee(client, "TX")
    _run(client, emp["id"], "2025-11-14", 4000)
    _run(client, emp["id"], "2025-11-28", 4000)

    f = compute_940(db_session, 2025)
    assert f["total_futa_tax"] == D("42.00")
    # No credit-reduction lines at all, so the form's content hash is the
    # one it always had.
    assert "credit_reduction" not in f
    assert "total_futa_tax_after_adjustments" not in f


def test_a_year_without_a_schedule_a_row_has_no_reduction(client, db_session):
    set_setting(db_session, "company_state", "CA")
    db_session.commit()
    emp = _employee(client, "CA")
    _run(client, emp["id"], "2026-01-09", 4000)
    f = compute_940(db_session, 2026)
    assert f["total_futa_tax"] == D("24.00")
    assert "credit_reduction" not in f
