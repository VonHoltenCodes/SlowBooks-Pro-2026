"""Tax forms reach the screen in the desktop app, and the 941 does its own
arithmetic (2.17.3 exploratory test, macbase1 F23).

Generate W-2 / W-3 / 940 / 941 and the New-Hire Report produced nothing in
the Mac app: the page POSTed for the PDF, wrapped it in a blob: URL and
window.open'ed that, which WKWebView ignores. Invoices open their GET URL,
which the desktop shim turns into the native PDF viewer; the forms now do the
same. The 941 printed the per-paycheck sums on lines 5a/5c ($462.10 as 12.4%
of $3,726.67); the form's column 2 is rate x wages and the paycheck rounding
belongs on line 7.
"""

import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.document_audit import DocumentAudit

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def _html(monkeypatch):
    from app.services.tax_forms import form_940, form_941, w2_w3

    for mod in (w2_w3, form_940, form_941):
        monkeypatch.setattr(mod, "render_pdf", lambda html, **kw: html.encode())


def _employee(client, **over):
    body = {
        "first_name": "Jonah",
        "last_name": "Pike",
        "pay_type": "salary",
        "pay_rate": 52000,
        "work_state": "TX",
        **over,
    }
    r = client.post("/api/employees", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _paid(client, emp_id, pay_date, gross):
    run = client.post(
        "/api/payroll",
        json={
            "period_start": pay_date,
            "period_end": pay_date,
            "pay_date": pay_date,
            "stubs": [{"employee_id": emp_id, "gross_override": gross}],
        },
    )
    assert run.status_code == 201, run.text
    r = client.post(f"/api/payroll/{run.json()['id']}/process")
    assert r.status_code == 200, r.text
    return run.json()["stubs"][0]


@pytest.mark.parametrize(
    "path, title",
    [
        ("/api/payroll/forms/w2/{emp}/pdf?year=2026", "FORM W-2"),
        ("/api/payroll/forms/w3/2026/pdf", "FORM W-3"),
        ("/api/payroll/forms/940/2026/pdf", "FORM 940"),
        ("/api/payroll/forms/941/2026/1/pdf", "FORM 941"),
    ],
)
def test_every_form_opens_by_get(
    client, db_session, seed_accounts, monkeypatch, path, title
):
    _html(monkeypatch)
    emp = _employee(client)
    _paid(client, emp["id"], "2026-03-20", 2000)
    r = client.get(path.format(emp=emp["id"]))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert title in r.text
    # the GET writes the same audit row the POST does
    assert db_session.query(DocumentAudit).count() == 1


def _js(name):
    return (JS / name).read_text(encoding="utf-8")


def test_the_pages_open_the_get_url_not_a_blob():
    forms = _js("tax_forms.js")
    onboarding = _js("onboarding.js")
    for js in (forms, onboarding):
        assert "createObjectURL" not in js
        assert "method: 'POST'" not in js
    for url in (
        "`/api/payroll/forms/w2/${empId}/pdf?year=${year}`",
        "`/api/payroll/forms/w3/${year}/pdf`",
        "`/api/payroll/forms/940/${year}/pdf`",
        "`/api/payroll/forms/941/${year}/${quarter}/pdf`",
    ):
        assert f"_openForm({url})" in forms, url
    assert "window.open(url, '_blank')" in forms
    assert (
        "window.open(`/api/onboarding/${empId}/new-hire-report/pdf`, '_blank')"
        in onboarding
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_in_the_desktop_window_each_form_reaches_the_pdf_viewer():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "tax_forms_shim_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    for line in (
        "W-2: GET /api/payroll/forms/w2/1/pdf?year=2026 -> native PDF viewer",
        "W-3: GET /api/payroll/forms/w3/2026/pdf -> native PDF viewer",
        "940: GET /api/payroll/forms/940/2026/pdf -> native PDF viewer",
        "941: GET /api/payroll/forms/941/2026/3/pdf -> native PDF viewer",
        "New-Hire: GET /api/onboarding/1/new-hire-report/pdf -> native PDF viewer",
    ):
        assert line in out.stdout, out.stdout


def _d(value):
    return Decimal(str(value))


def test_941_column_2_is_rate_times_wages_and_line_7_carries_the_cents(
    client, seed_accounts, monkeypatch
):
    emp = _employee(client)
    stub = _paid(client, emp["id"], "2026-02-20", 100.05)
    # each paycheck rounds its 6.2% and 1.45% separately
    assert stub["ss_tax"] == 6.20 and stub["medicare_tax"] == 1.45
    f = client.get("/api/tax-forms/941?year=2026&quarter=1").json()
    assert _d(f["social_security_wages"]) == Decimal("100.05")
    assert _d(f["social_security_tax"]) == Decimal("12.41")  # 12.4% of 100.05
    assert _d(f["medicare_tax"]) == Decimal("2.90")  # 2.9% of 100.05
    assert _d(f["total_fica_tax"]) == Decimal("15.31")
    assert _d(f["fractions_of_cents"]) == Decimal("-0.01")  # 15.30 withheld + matched
    fed = _d(f["federal_income_tax_withheld"])
    assert _d(f["total_tax_before_adjustments"]) == fed + Decimal("15.31")
    assert _d(f["total_tax_liability"]) == fed + Decimal("15.30")

    _html(monkeypatch)
    page = client.get("/api/payroll/forms/941/2026/1/pdf").text
    assert "$12.41" in page and "-$0.01" in page


def test_941_uses_the_wage_base_and_additional_medicare(client, seed_accounts):
    emp = _employee(client, first_name="Lena", last_name="Hart")
    _paid(client, emp["id"], "2026-03-20", 190000)  # passes the SS wage base
    _paid(client, emp["id"], "2026-04-20", 20000)  # passes $200,000
    q1 = client.get("/api/tax-forms/941?year=2026&quarter=1").json()
    assert _d(q1["social_security_wages"]) == Decimal("184500.00")
    assert _d(q1["fractions_of_cents"]) == Decimal("0.00")
    q2 = client.get("/api/tax-forms/941?year=2026&quarter=2").json()
    assert _d(q2["social_security_wages"]) == Decimal("0.00")
    assert _d(q2["medicare_wages"]) == Decimal("20000.00")
    assert _d(q2["additional_medicare_wages"]) == Decimal("10000.00")
    assert _d(q2["additional_medicare_tax"]) == Decimal("90.00")
    assert _d(q2["total_fica_tax"]) == Decimal("670.00")  # 580 + 90
    assert _d(q2["fractions_of_cents"]) == Decimal("0.00")
