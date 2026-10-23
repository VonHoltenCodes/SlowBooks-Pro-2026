"""Opening YTD balances per employee for a mid-year switch (#433).

A company that switches to SlowBooks mid-year enters what the old system
already paid, once per employee per year. The readers named in the issue
count it on purpose (employee_ytd and the wage bases, the stub's YTD, the
941 for its quarter, the 940's FUTA wages, the W-2/W-3, the benefit caps);
the readers that move money (deposits, remittances, the pay-run list) never
see it. Locked once a pay run in the year is processed: a change then is a
correction with a reason, in the audit trail.
"""

import csv
import io
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.payroll_service import SS_WAGE_BASE

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _chart(seed_accounts):
    """Processing a run posts to the chart of accounts."""
    return seed_accounts


def _employee(client, first="Sam", last="Ortiz", **kw):
    body = {
        "first_name": first,
        "last_name": last,
        "pay_type": "hourly",
        "pay_rate": 50,
        "pay_frequency": "biweekly",
        "filing_status": "single",
        "work_state": "WA",
    }
    body.update(kw)
    r = client.post("/api/employees", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _opening(**kw):
    body = {
        "year": 2026,
        "as_of": "2026-06-30",
        "gross": 30000,
        "federal_tax": 3100,
        "ss_wages": 29000,
        "ss_tax": 1798,
        "employer_ss_tax": 1798,
        "medicare_wages": 29000,
        "medicare_tax": 420.50,
        "employer_medicare_tax": 420.50,
        "futa_wages": 7000,
        "futa_tax": 42,
        "suta_wages": 7000,
        "suta_tax": 189,
        "pretax_deductions": 1500,
        "posttax_deductions": 0,
        "garnishments": 0,
        "net": 22000,
        "states": [
            {"state": "GA", "wages": 20000, "withholding": 800},
            {"state": "AL", "wages": 8500, "withholding": 340},
        ],
    }
    body.update(kw)
    return body


def _put(client, emp_id, body):
    return client.put(f"/api/employees/{emp_id}/opening-ytd", json=body)


def _run(client, emp_id, pay="2026-07-17", gross=5000, process=True, **stub):
    start = f"{pay[:8]}01"
    r = client.post(
        "/api/payroll",
        json={
            "period_start": start,
            "period_end": pay,
            "pay_date": pay,
            "stubs": [{"employee_id": emp_id, "gross_override": gross, **stub}],
        },
    )
    assert r.status_code == 201, r.text
    run = r.json()
    if process:
        p = client.post(f"/api/payroll/{run['id']}/process")
        assert p.status_code == 200, p.text
    return run


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------
def test_put_creates_get_returns_it_and_put_again_replaces(client):
    emp = _employee(client)
    r = _put(client, emp["id"], _opening())
    assert r.status_code == 200, r.text
    got = client.get(f"/api/employees/{emp['id']}/opening-ytd?year=2026").json()
    o = got["opening"]
    assert got["locked"] is False and got["first_pay_date"] is None
    assert o["gross"] == 30000.0 and o["as_of"] == "2026-06-30"
    assert [(s["state"], s["withholding"]) for s in o["states"]] == [
        ("AL", 340.0),
        ("GA", 800.0),
    ]

    r = _put(
        client,
        emp["id"],
        _opening(gross=31000, net=23000, states=[{"state": "ga", "wages": 1}]),
    )
    assert r.status_code == 200, r.text
    o = r.json()["opening"]
    assert o["gross"] == 31000.0
    assert [s["state"] for s in o["states"]] == ["GA"]
    listed = client.get("/api/payroll/opening-ytd?year=2026").json()
    assert [(x["employee_name"], x["gross"]) for x in listed] == [
        ("Sam Ortiz", 31000.0)
    ]
    # no year yet: null, not a 404
    other = client.get(f"/api/employees/{emp['id']}/opening-ytd?year=2025").json()
    assert other["opening"] is None


@pytest.mark.parametrize(
    "change, words",
    [
        ({"as_of": "2025-12-31"}, "not in 2026"),
        ({"federal_tax": -1}, "cannot be negative"),
        ({"net": 40000}, "cannot be more than gross"),
        ({"states": [{"state": "Georgia"}]}, "two-letter"),
        ({"benefits": [{"benefit_code_id": 9999}]}, "does not exist"),
    ],
)
def test_validation(client, change, words):
    emp = _employee(client)
    r = _put(client, emp["id"], _opening(**change))
    assert r.status_code == 422, r.text
    assert words in r.text


def test_the_opening_balance_paths_are_admin_only():
    from app.main import _is_hr_sensitive

    assert _is_hr_sensitive("/api/employees/3/opening-ytd")
    assert _is_hr_sensitive("/api/payroll/opening-ytd/import")


# ---------------------------------------------------------------------------
# Readers that count it
# ---------------------------------------------------------------------------
def test_employee_ytd_counts_it_and_before_means_before(client, db_session):
    from app.routes.payroll.ytd import employee_ytd

    emp = _employee(client)
    assert _put(client, emp["id"], _opening()).status_code == 200
    ytd = client.get(f"/api/employees/{emp['id']}/ytd?year=2026").json()
    assert ytd["gross"] == 30000.0 and ytd["state"] == 1140.0
    assert ytd["federal"] == 3100.0 and ytd["pretax_deductions"] == 1500.0
    # a run dated on or before as_of was paid before the opening's figures
    assert employee_ytd(db_session, emp["id"], 2026, before=date(2026, 6, 30))[
        "gross"
    ] == Decimal("0")
    assert employee_ytd(db_session, emp["id"], 2026, before=date(2026, 7, 1))[
        "gross"
    ] == Decimal("30000.00")


def test_a_run_after_the_switch_starts_from_the_opening_wage_base(client):
    emp = _employee(client)
    body = _opening(gross=float(SS_WAGE_BASE - 1000), net=100000, states=[])
    body["ss_wages"] = float(SS_WAGE_BASE - 1000)
    assert _put(client, emp["id"], body).status_code == 200
    run = _run(client, emp["id"], process=False)
    # Social Security on the $1,000 left of the wage base, not the $5,000
    assert run["stubs"][0]["ss_tax"] == 62.0
    # a run dated before as_of does not count the opening
    early = _run(client, emp["id"], pay="2026-06-15", process=False)
    assert early["stubs"][0]["ss_tax"] == 310.0


def test_the_stub_ytd_gross_and_net_agree_with_employee_ytd(client, monkeypatch):
    from app.services import paystub_pdf

    seen = {}

    def fake_pdf(stub, emp, run, company, ytd, ytd_stubs=None):
        seen.update(ytd)
        return b"%PDF-1.4 stub"

    monkeypatch.setattr(paystub_pdf, "generate_paystub_pdf", fake_pdf)
    emp = _employee(client)
    assert _put(client, emp["id"], _opening()).status_code == 200
    run = _run(client, emp["id"], pay="2026-07-17")
    stub = run["stubs"][0]
    r = client.get(f"/api/payroll/{run['id']}/paystub/{stub['id']}")
    assert r.status_code == 200, r.text
    ytd = client.get(f"/api/employees/{emp['id']}/ytd?year=2026").json()
    assert Decimal(seen["gross"]) == Decimal("35000.00") == Decimal(str(ytd["gross"]))
    assert float(seen["net"]) == ytd["net"]


def test_941_quarter_of_as_of_reports_it_later_quarters_seed_the_wage_base(
    client, db_session
):
    from app.services.tax_forms.form_941 import compute_941

    emp = _employee(client)
    body = _opening(
        as_of="2026-05-31",
        gross=float(SS_WAGE_BASE - 1000),
        ss_wages=float(SS_WAGE_BASE - 1000),
        medicare_wages=float(SS_WAGE_BASE - 1000),
        net=100000,
        states=[],
    )
    assert _put(client, emp["id"], body).status_code == 200
    _run(client, emp["id"], pay="2026-07-17")

    q1 = compute_941(db_session, 2026, 1)
    assert q1["total_wages"] == Decimal("0") and q1["num_employees"] == 0
    q2 = compute_941(db_session, 2026, 2)
    assert q2["total_wages"] == SS_WAGE_BASE - 1000
    assert q2["num_employees"] == 1 and q2["num_stubs"] == 0
    assert q2["federal_income_tax_withheld"] == Decimal("3100.00")
    assert q2["social_security_wages"] == SS_WAGE_BASE - 1000
    assert q2["social_security_tax_employee"] == Decimal("1798.00")
    assert q2["medicare_tax_employer"] == Decimal("420.50")
    q3 = compute_941(db_session, 2026, 3)
    assert q3["total_wages"] == Decimal("5000.00")
    # only the $1,000 left of the wage base is Social Security wages
    assert q3["social_security_wages"] == Decimal("1000.00")
    assert q3["medicare_wages"] == Decimal("5000.00")


def test_940_counts_the_opening_futa_wages_toward_the_7000(client, db_session):
    from app.services.tax_forms.form_940 import compute_940

    emp = _employee(client)
    assert _put(client, emp["id"], _opening(gross=20000, net=15000)).status_code == 200
    _run(client, emp["id"], pay="2026-07-17")
    f = compute_940(db_session, 2026)
    assert f["futa_taxable_wages"] == Decimal("7000.00")
    assert f["total_payments"] == Decimal("25000.00")
    assert f["exempt_payments"] == Decimal("18000.00")
    assert f["total_futa_tax"] == Decimal("42.00")  # the stub owed none
    assert f["num_employees"] == 1


def test_w2_and_w3_include_the_opening(client, db_session):
    from app.services.tax_forms.w2_w3 import compute_all_w2, compute_w2, compute_w3

    emp = _employee(client)
    assert _put(client, emp["id"], _opening()).status_code == 200
    run = _run(client, emp["id"], pay="2026-07-17")
    stub = run["stubs"][0]
    w2 = compute_w2(db_session, 2026, emp["id"])
    assert w2["box1_federal_wages"] == Decimal("33500.00")
    assert w2["box2_federal_tax_withheld"] == Decimal("3100") + Decimal(
        str(stub["federal_tax"])
    )
    assert w2["box3_ss_wages"] == Decimal("34000.00")
    assert w2["box4_ss_tax_withheld"] == Decimal("1798") + Decimal(str(stub["ss_tax"]))
    assert w2["box5_medicare_wages"] == Decimal("34000.00")
    assert w2["box6_medicare_tax_withheld"] == Decimal("420.50") + Decimal(
        str(stub["medicare_tax"])
    )
    # the opening's two states, and the WA stub's wages (no income tax)
    assert w2["box16_state_wages"] == Decimal("33500.00")
    assert w2["box17_state_income_tax"] == Decimal("1140.00")

    # someone who left before the switch still gets a W-2
    gone = _employee(client, first="Hana", last="Lee")
    assert (
        _put(
            client, gone["id"], _opening(gross=8000, net=6000, states=[], ss_wages=8000)
        ).status_code
        == 200
    )
    names = [w["employee"]["name"] for w in compute_all_w2(db_session, 2026)]
    assert names == ["Sam Ortiz", "Hana Lee"]
    w3 = compute_w3(db_session, 2026)
    assert w3["num_w2"] == 2
    assert w3["box1_federal_wages"] == Decimal("33500.00") + Decimal("6500.00")
    assert w3["box3_ss_wages"] == Decimal("42000.00")


def test_opening_benefit_amounts_count_toward_the_annual_cap(client):
    emp = _employee(client)
    code = client.post(
        "/api/benefits/codes",
        json={
            "code": "ANN",
            "name": "Annual capped",
            "kind": "deduction",
            "category": "pretax",
            "calc_method": "fixed_amount",
            "reduces_federal": True,
            "reduces_state": True,
            "reduces_fica": False,
            "rate": {"employee_rate": 300, "annual_cap": 500},
        },
    )
    assert code.status_code == 201, code.text
    code = code.json()
    r = client.post(
        "/api/benefits/enrollments",
        json={"employee_id": emp["id"], "benefit_code_id": code["id"]},
    )
    assert r.status_code in (200, 201), r.text
    body = _opening(benefits=[{"benefit_code_id": code["id"], "employee_amount": 400}])
    r = _put(client, emp["id"], body)
    assert r.status_code == 200, r.text
    assert r.json()["opening"]["benefits"][0]["code"] == "ANN"
    ytd = client.get(f"/api/benefits/ytd?employee_id={emp['id']}&year=2026").json()
    assert [(y["code"], y["employee_amount"]) for y in ytd] == [("ANN", 400.0)]
    run = _run(client, emp["id"], process=False)
    ann = next(b for b in run["stubs"][0]["benefits"] if b["code"] == "ANN")
    assert ann["employee_amount"] == 100.0  # 500 cap less the 400 already taken
    # a rebuild keeps both: the stub's 100 and the opening's 400
    client.post("/api/benefits/ytd/rebuild?year=2026")
    ytd = client.get(f"/api/benefits/ytd?employee_id={emp['id']}&year=2026").json()
    assert ytd[0]["employee_amount"] == 500.0


# ---------------------------------------------------------------------------
# Readers that must not see it
# ---------------------------------------------------------------------------
def test_deposits_remittances_and_the_run_list_do_not_see_it(client, db_session):
    from app.services.benefits_engine import remittance_rows
    from app.services.tax_forms.tax_liability import compute_tax_liability

    emp = _employee(client)
    before = compute_tax_liability(db_session, 2026, 2)
    assert _put(client, emp["id"], _opening()).status_code == 200
    assert compute_tax_liability(db_session, 2026, 2) == before
    assert remittance_rows(db_session, date(2026, 1, 1), date(2026, 12, 31)) == []
    assert client.get("/api/payroll").json() == []


# ---------------------------------------------------------------------------
# The lock
# ---------------------------------------------------------------------------
def test_locked_after_a_processed_run_a_change_is_a_correction(client, db_session):
    from app.models.audit import AuditLog

    emp = _employee(client)
    assert _put(client, emp["id"], _opening()).status_code == 200
    _run(client, emp["id"], pay="2026-07-17")
    got = client.get(f"/api/employees/{emp['id']}/opening-ytd?year=2026").json()
    assert got["locked"] is True and got["first_pay_date"] == "2026-07-17"

    r = _put(client, emp["id"], _opening(gross=30500))
    assert r.status_code == 409 and "Locked: a pay run dated 2026-07-17" in r.text
    r = _put(
        client, emp["id"], _opening(gross=30500, reason="June bonus left off the copy")
    )
    assert r.status_code == 200, r.text
    assert r.json()["opening"]["gross"] == 30500.0
    oid = r.json()["opening"]["id"]
    rows = (
        db_session.query(AuditLog)
        .filter_by(table_name="payroll_opening_balances", action="CORRECT")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].record_id == oid
    assert rows[0].new_values["reason"] == "June bonus left off the copy"
    assert rows[0].old_values["gross"] == "30000.00"
    assert rows[0].new_values["gross"] == "30500.00"

    assert (
        client.delete(f"/api/employees/{emp['id']}/opening-ytd?year=2026").status_code
        == 409
    )
    r = client.delete(
        f"/api/employees/{emp['id']}/opening-ytd?year=2026&reason=entered twice"
    )
    assert r.status_code == 200, r.text
    assert r.json()["opening"] is None
    rows = (
        db_session.query(AuditLog)
        .filter_by(table_name="payroll_opening_balances", action="CORRECT")
        .order_by(AuditLog.id)
        .all()
    )
    assert rows[-1].new_values == {"deleted": True, "reason": "entered twice"}


def test_a_new_opening_after_a_processed_run_must_be_dated_before_it(client):
    emp = _employee(client)
    _run(client, emp["id"], pay="2026-07-17")
    r = _put(client, emp["id"], _opening(as_of="2026-07-31", reason="late"))
    assert r.status_code == 422 and "must be before" in r.text
    r = _put(client, emp["id"], _opening(reason="entered after the first run"))
    assert r.status_code == 200, r.text


def test_an_unlocked_delete_needs_no_reason(client):
    emp = _employee(client)
    assert _put(client, emp["id"], _opening()).status_code == 200
    r = client.delete(f"/api/employees/{emp['id']}/opening-ytd?year=2026")
    assert r.status_code == 200 and r.json()["opening"] is None


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
def _import(client, text, dry_run=1, **query):
    q = "&".join(f"{k}={v}" for k, v in query.items())
    return client.post(
        f"/api/payroll/opening-ytd/import?dry_run={dry_run}" + (f"&{q}" if q else ""),
        files={"file": ("opening.csv", text.encode(), "text/csv")},
    )


def _template_csv(sam_gross="30000"):
    from app.services.payroll_opening_import import TEMPLATE_COLUMNS

    def pad(cells):
        return [cells.get(c, "") for c in TEMPLATE_COLUMNS]

    sam = pad(
        {
            "Employee": "Ortiz, Sam",
            "Year": "2026",
            "As of": "2026-06-30",
            "Gross": sam_gross,
            "Federal withholding": "3100",
            "SS wages": "29000",
            "SS tax": "1798",
            "Net": "22000",
            "State": "GA",
            "State wages": "20000",
            "State withholding": "800",
        }
    )
    sam_al = pad(
        {
            "Employee": "Ortiz, Sam",
            "State": "AL",
            "State wages": "8500",
            "State withholding": "340",
        }
    )
    hana = pad(
        {
            "Employee": "hana lee",
            "As of": "06/30/2026",
            "Gross": "20000",
            "Net": "15000",
        }
    )
    nobody = pad({"Employee": "Nobody, Known", "Year": "2026", "As of": "2026-06-30"})
    out = io.StringIO()
    csv.writer(out).writerows([TEMPLATE_COLUMNS, sam, sam_al, hana, nobody])
    return out.getvalue()


def test_template_import_plans_applies_and_replaces(client, db_session):
    sam = _employee(client)
    hana = _employee(client, first="Hana", last="Lee")
    plan = _import(client, _template_csv()).json()
    assert plan["format"] == "template"
    assert [(r["employee"], r["action"]) for r in plan["rows"]] == [
        ("Ortiz, Sam", "create"),
        ("hana lee", "create"),
        ("Nobody, Known", "error"),
    ]
    assert "No employee named" in plan["rows"][2]["note"]
    assert {s["state"] for s in plan["rows"][0]["states"]} == {"GA", "AL"}
    # a dry run writes nothing
    assert client.get("/api/payroll/opening-ytd?year=2026").json() == []

    done = _import(client, _template_csv(), dry_run=0).json()
    assert done["applied"] == 2
    o = client.get(f"/api/employees/{sam['id']}/opening-ytd?year=2026").json()
    assert o["opening"]["gross"] == 30000.0
    assert len(o["opening"]["states"]) == 2
    h = client.get(f"/api/employees/{hana['id']}/opening-ytd?year=2026").json()
    assert h["opening"]["as_of"] == "2026-06-30"

    again = _import(client, _template_csv(sam_gross="31000"), dry_run=0).json()
    assert [r["action"] for r in again["rows"][:2]] == ["replace", "replace"]
    o = client.get(f"/api/employees/{sam['id']}/opening-ytd?year=2026").json()
    assert o["opening"]["gross"] == 31000.0

    # once Sam has a processed run, his row is an error naming the lock
    _run(client, sam["id"], pay="2026-07-17")
    locked = _import(client, _template_csv()).json()
    assert locked["rows"][0]["action"] == "error"
    assert "Locked: a pay run dated 2026-07-17" in locked["rows"][0]["note"]
    assert locked["rows"][1]["action"] == "replace"
    fixed = _import(client, _template_csv(), dry_run=0, reason="QB re-run").json()
    assert fixed["rows"][0]["action"] == "replace" and fixed["applied"] == 2


def test_template_download_is_a_csv_the_importer_reads(client):
    r = client.get("/api/payroll/opening-ytd/template")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    plan = _import(client, r.text.lstrip("﻿")).json()
    assert plan["format"] == "template"
    # the example matches no one, so it imports nothing
    assert [r["action"] for r in plan["rows"]] == ["error"]


def test_quickbooks_payroll_summary_imports_what_it_can_name(client):
    sam = _employee(client)
    _employee(client, first="Hana", last="Lee")
    text = (FIXTURES / "qb_payroll_summary.csv").read_text()
    plan = _import(client, text).json()
    assert plan["format"] == "quickbooks_payroll_summary", plan
    assert plan["errors"] == []
    rows = {r["employee"]: r for r in plan["rows"]}
    assert set(rows) == {"Ortiz, Sam", "Lee, Hana"}
    s = rows["Ortiz, Sam"]
    assert s["action"] == "create" and s["employee_id"] == sam["id"]
    assert s["as_of"] == "2026-06-30" and s["year"] == 2026
    assert (s["gross"], s["federal_tax"], s["net"]) == (30000.0, 3100.0, 22095.0)
    assert (s["ss_tax"], s["medicare_tax"]) == (1860.0, 435.0)
    assert (s["employer_ss_tax"], s["employer_medicare_tax"]) == (1860.0, 435.0)
    assert (s["futa_tax"], s["suta_tax"]) == (42.0, 189.0)
    assert (s["pretax_deductions"], s["posttax_deductions"]) == (1500.0, 200.0)
    assert s["states"] == [
        {
            "state": "GA",
            "wages": 0.0,
            "withholding": 800.0,
            "other_employee": 0.0,
            "other_employer": 0.0,
        }
    ]
    # nothing silently dropped: the items it could not place are named
    assert "GA - Special Local Tax" in s["note"]
    assert "401(k) Company Match" in s["note"]
    assert "no taxable-wage lines" in s["note"]
    done = _import(client, text, dry_run=0).json()
    assert done["applied"] == 2


def test_a_quickbooks_report_it_cannot_read_is_refused_not_guessed(client):
    _employee(client)
    text = (
        ",,Payroll Summary,,\n"
        ',,"January 1 through June 30, 2026",,\n'
        ',,"Ortiz, Sam",TOTAL\n'
        ",Something else,100.00,100.00\n"
    )
    plan = _import(client, text).json()
    assert plan["rows"] == []
    assert "cannot be read with confidence" in plan["errors"][0]
    assert "template" in plan["errors"][0]
    done = _import(client, text, dry_run=0).json()
    assert done["applied"] == 0


def test_a_payroll_summary_without_a_through_date_needs_as_of(client):
    _employee(client)
    _employee(client, first="Hana", last="Lee")
    text = (FIXTURES / "qb_payroll_summary.csv").read_text()
    text = text.replace("January 1 through June 30, 2026", "Year to date")
    plan = _import(client, text).json()
    assert plan["errors"] and "as_of" in plan["errors"][0]
    plan = _import(client, text, as_of="2026-05-31").json()
    assert plan["errors"] == []
    assert {r["as_of"] for r in plan["rows"]} == {"2026-05-31"}


def test_an_unknown_file_is_refused(client):
    plan = _import(client, "Date,Amount\n2026-01-01,5\n").json()
    assert plan["rows"] == [] and "template" in plan["errors"][0]


# ---------------------------------------------------------------------------
# The pages
# ---------------------------------------------------------------------------
def test_employee_page_has_the_opening_ytd_block_and_the_reason_path():
    js = (ROOT / "app/static/js/employees.js").read_text()
    assert "emp-opening-section-${id}" in js
    assert "<h4>Opening YTD</h4>" in js
    assert "of which opening balance" in js
    assert "/opening-ytd" in js
    # the reason is an inline field, never window.prompt
    assert "emp-opening-reason-${id}" in js and "body.reason = r.reason" in js
    assert "prompt(" not in js
    assert "Locked: a pay run dated" in js


def test_payroll_page_has_the_import_button_and_the_template_link():
    js = (ROOT / "app/static/js/payroll.js").read_text()
    assert "PayrollPage.showOpeningImport()" in js
    assert "Import opening balances" in js
    assert "/api/payroll/opening-ytd/template" in js
    assert "/api/payroll/opening-ytd/import?" in js
    assert "PayrollPage.applyOpeningImport()" in js
