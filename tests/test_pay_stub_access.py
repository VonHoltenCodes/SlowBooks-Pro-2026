"""Pay stubs from the Payroll screen, naming the right employer (2.17.3
exploratory tests: skytech W-M10, macbase1 S-h).

GET /api/payroll/{run}/paystub/{stub} rendered a stub, but nothing on the
Payroll screen linked to it, and the PDF named the employer "My Company" (the
environment default) instead of the company in Settings.
"""

from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"
COMPANY = "Explore Signs & Co"


@pytest.fixture
def run(client, seed_accounts):
    r = client.put(
        "/api/settings",
        json={
            "company_name": COMPANY,
            "company_city": "Peoria",
            "company_tax_id": "12-3456789",
        },
    )
    assert r.status_code == 200, r.text
    emp = client.post(
        "/api/employees",
        json={
            "first_name": "Sam",
            "last_name": "Ortiz",
            "pay_type": "salary",
            "pay_rate": 62400,
        },
    ).json()
    r = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-09-13",
            "period_end": "2026-09-26",
            "pay_date": "2026-10-01",
            "stubs": [{"employee_id": emp["id"]}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_the_stub_names_the_company_in_settings(client, run, monkeypatch):
    from app.services import paystub_pdf

    monkeypatch.setattr(paystub_pdf, "render_pdf", lambda html, **kw: html.encode())
    r = client.get(f"/api/payroll/{run['id']}/paystub/{run['stubs'][0]['id']}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert "Explore Signs &amp; Co" in r.text
    assert "EIN: 12-3456789" in r.text
    assert "My Company" not in r.text


def test_the_new_hire_report_names_the_company_in_settings(client, run, monkeypatch):
    from app.services import new_hire_report

    monkeypatch.setattr(new_hire_report, "render_pdf", lambda html, **kw: html.encode())
    emp_id = run["stubs"][0]["employee_id"]
    assert (
        client.get(f"/api/onboarding/{emp_id}/new-hire-report").json()["employer"][
            "name"
        ]
        == COMPANY
    )
    page = client.get(f"/api/onboarding/{emp_id}/new-hire-report/pdf").text
    assert "Explore Signs &amp; Co" in page and "My Company" not in page


def test_each_employee_on_a_pay_run_has_a_stub_link():
    js = (JS / "payroll.js").read_text(encoding="utf-8")
    view = js[js.index("async view(id)") : js.index("async process(id)")]
    assert "window.open('/api/payroll/${run.id}/paystub/${s.id}','_blank')" in view
    assert '<th scope="col">Stub</th>' in view
