"""Opening YTD, finished (#433 / PR #475): the year's own Social Security
wage base, a draft that can be discarded, a W-2 line per state, and
paychecks that count their taxable wages toward the caps.

1. The Social Security wage base is the year's: a 2025 opening, paycheck
   and W-2 are measured against 2025's $176,100, not 2026's $184,500. A
   year SlowBooks has no base for is measured against the nearest year's
   and says so; it is not refused.
2. Discard draft deletes a draft run and its stubs and puts back what
   creating it changed (benefit year-to-date, loan balances, time entries).
3. Boxes 15-17 go one line per state; a W-2 holds two, a third goes on an
   additional form. A one-state W-2 keeps its shape, so its Form history
   hash is the one it always had.
4. A paycheck counts its FICA wages (gross less Section 125 and the like)
   toward the Social Security base, the Additional Medicare threshold,
   FUTA and SUTA, as the openings do.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from app.models.benefits import BenefitYTD, EmployeeBenefit, PayStubBenefit
from app.models.document_audit import DocumentAudit
from app.models.payroll import PayRun, PayStub
from app.models.time_entries import TimeEntry
from app.services.payroll_service import SS_WAGE_BASES, ss_wage_base, wage_base_note

ROOT = Path(__file__).resolve().parents[1]
D = Decimal
CENT = D("0.01")

# fixture sign-ins: module constants, never a literal beside a username
FIXTURE_PW = "test-password-123"


@pytest.fixture(autouse=True)
def _chart(seed_accounts):
    return seed_accounts


def _employee(client, first="Rae", last="Okafor", **kw):
    body = {
        "first_name": first,
        "last_name": last,
        "pay_type": "hourly",
        "pay_rate": 50,
        "pay_frequency": "biweekly",
        "filing_status": "single",
        "work_state": "TX",
    }
    body.update(kw)
    r = client.post("/api/employees", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _code(client, code, **kw):
    body = {
        "code": code,
        "name": kw.pop("name", code.title()),
        "kind": "deduction",
        "category": "pretax",
        "calc_method": "fixed_amount",
        "reduces_federal": True,
        "reduces_state": True,
        "reduces_fica": False,
        "sequence": 100,
    }
    body.update(kw)
    r = client.post("/api/benefits/codes", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _section125(client, amount=500):
    return _code(
        client,
        "S125",
        name="Cafeteria plan",
        reduces_fica=True,
        rate={"employee_rate": amount},
    )


def _enroll(client, emp_id, code_id, **kw):
    r = client.post(
        "/api/benefits/enrollments",
        json={"employee_id": emp_id, "benefit_code_id": code_id, **kw},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _run(client, emp_id, pay, gross=4000, process=True, start=None, **stub):
    body = {"employee_id": emp_id, **stub}
    if gross is not None:
        body["gross_override"] = gross
    r = client.post(
        "/api/payroll",
        json={
            "period_start": start or f"{pay[:8]}01",
            "period_end": pay,
            "pay_date": pay,
            "stubs": [body],
        },
    )
    assert r.status_code == 201, r.text
    run = r.json()
    if process:
        p = client.post(f"/api/payroll/{run['id']}/process")
        assert p.status_code == 200, p.text
    return run


def _stub(run):
    return run["stubs"][0]


def _m(x):
    return D(str(x)).quantize(CENT)


def _opening(year, as_of, ss_wages, **q):
    gross = q.pop("gross", ss_wages)
    quarter = {
        "quarter": (int(as_of[5:7]) - 1) // 3 + 1,
        "gross": gross,
        "ss_wages": ss_wages,
        "medicare_wages": ss_wages,
        "futa_wages": 7000,
        "net": gross / 2,
        **q,
    }
    return {"year": year, "as_of": as_of, "quarters": [quarter]}


def _put(client, emp_id, body):
    return client.put(f"/api/employees/{emp_id}/opening-ytd", json=body)


# ---------------------------------------------------------------------------
# 1. The year's own Social Security wage base
# ---------------------------------------------------------------------------
def test_the_wage_base_is_the_years():
    assert ss_wage_base(2025) == D("176100")
    assert ss_wage_base(2026) == D("184500")
    assert wage_base_note(2025) is None and wage_base_note(2026) is None
    # a year with no base here: the nearest one's, and a sentence saying so
    newest = max(SS_WAGE_BASES)
    assert ss_wage_base(newest + 1) == SS_WAGE_BASES[newest]
    note = wage_base_note(newest + 1)
    assert f"not for {newest + 1}" in note and f"{newest}'s" in note


def test_a_2025_opening_is_checked_against_2025s_base(client):
    emp = _employee(client)
    # $180,000 fits under 2026's $184,500 but not under 2025's $176,100
    r = _put(client, emp["id"], _opening(2025, "2025-06-30", 180000))
    assert r.status_code == 422, r.text
    assert "2025 Social Security wage base (176100)" in r.text
    ok = _put(client, emp["id"], _opening(2025, "2025-06-30", 176100))
    assert ok.status_code == 200, ok.text
    assert ok.json()["warnings"] == []
    r = _put(client, emp["id"], _opening(2026, "2026-06-30", 180000))
    assert r.status_code == 200, r.text


def test_a_2026_opening_over_2026s_base_is_refused(client):
    emp = _employee(client)
    r = _put(client, emp["id"], _opening(2026, "2026-06-30", 184501))
    assert r.status_code == 422, r.text
    assert "2026 Social Security wage base (184500)" in r.text


def test_a_year_with_no_base_is_saved_and_says_so(client):
    emp = _employee(client)
    year = max(SS_WAGE_BASES) + 1
    r = _put(client, emp["id"], _opening(year, f"{year}-03-31", 20000))
    assert r.status_code == 200, r.text
    [warning] = r.json()["warnings"]
    assert f"not for {year}" in warning
    # and a pay run that year runs, measured against the nearest base, and
    # says so too
    run = _run(client, emp["id"], f"{year}-04-10", process=False)
    assert any(f"not for {year}" in w for w in run["warnings"])


def test_a_2025_paycheck_and_w2_use_2025s_base(client, db_session):
    from app.services.tax_forms.form_941 import compute_941
    from app.services.tax_forms.w2_w3 import compute_w2

    emp = _employee(client)
    assert (
        _put(client, emp["id"], _opening(2025, "2025-06-30", 175000)).status_code == 200
    )
    stub = _stub(_run(client, emp["id"], "2025-07-15", gross=5000))
    # $1,100 left of 2025's base, not $9,500 of 2026's
    assert _m(stub["ss_tax"]) == _m(D("1100") * D("0.062"))
    assert _stub(_run(client, emp["id"], "2025-07-29", gross=5000))["ss_tax"] == 0.0
    assert compute_941(db_session, 2025, 3)["social_security_wages"] == D("1100.00")
    w2 = compute_w2(db_session, 2025, emp["id"])
    assert w2["box3_ss_wages"] == D("176100.00")
    assert w2["box5_medicare_wages"] == D("185000.00")


# ---------------------------------------------------------------------------
# 4. A paycheck counts its taxable wages toward the caps
# ---------------------------------------------------------------------------
def test_stub_taxable_wages_follows_each_taxs_treatment():
    from types import SimpleNamespace as NS

    from app.services.payroll_service import stub_taxable_wages

    def code(amount, fed, state, fica):
        return NS(
            category="pretax",
            employee_amount=D(amount),
            reduces_federal=fed,
            reduces_state=state,
            reduces_fica=fica,
        )

    stub = NS(
        gross_pay=D("5000"),
        # $500 Section 125 + $300 401(k) + $50 typed on the run
        pretax_deductions=D("850"),
        benefits=[code("500", True, True, True), code("300", True, True, False)],
    )
    assert stub_taxable_wages(stub) == {
        "gross": D("5000"),
        "federal": D("4150"),
        "state": D("4150"),
        "fica": D("4500"),
    }
    # a stub with no codes: the typed pre-tax amount never reduces FICA
    bare = NS(gross_pay=D("2000"), pretax_deductions=D("100"), benefits=[])
    assert stub_taxable_wages(bare)["fica"] == D("2000")
    assert stub_taxable_wages(bare)["federal"] == D("1900")


def test_a_section_125_employee_near_the_social_security_base(client, db_session):
    """$181,000 paid with $500 of it a cafeteria plan: $180,500 counted
    toward the $184,500, so the next check owes Social Security on $4,000
    (gross would have said $3,500), and Additional Medicare starts at
    $200,000 of Medicare wages."""
    from app.services.tax_forms.form_941 import compute_941
    from app.services.tax_forms.w2_w3 import compute_w2

    emp = _employee(client)
    _enroll(client, emp["id"], _section125(client)["id"])
    first = _stub(_run(client, emp["id"], "2026-07-10", gross=181000))
    assert _m(first["ss_tax"]) == _m(D("180500") * D("0.062"))
    second = _stub(_run(client, emp["id"], "2026-07-24", gross=6000))
    assert _m(second["ss_tax"]) == D("248.00")  # 6.2% of 4,000
    third = _stub(_run(client, emp["id"], "2026-08-07", gross=20500))
    assert third["ss_tax"] == 0.0
    # 186,000 counted before; 20,000 of Medicare wages, 6,000 above 200,000
    assert _m(third["medicare_tax"]) == D("290.00") + D("54.00")

    q3 = compute_941(db_session, 2026, 3)
    assert q3["social_security_wages"] == D("184500.00")
    assert q3["medicare_wages"] == D("206000.00")
    assert q3["additional_medicare_wages"] == D("6000.00")
    w2 = compute_w2(db_session, 2026, emp["id"])
    assert w2["box1_federal_wages"] == D("206000.00")
    assert w2["box3_ss_wages"] == D("184500.00")
    assert w2["box5_medicare_wages"] == D("206000.00")
    assert w2["gross_pay"] == D("207500.00")


def test_futa_and_suta_count_the_section_125_wages_too(client, db_session):
    from app.config import SUTA_RATE
    from app.services.state_tax import get_engine
    from app.services.tax_forms.form_940 import compute_940
    from app.services.tax_forms.state_sui import compute_sui

    emp = _employee(client)
    _enroll(client, emp["id"], _section125(client)["id"])
    stubs = [
        _stub(_run(client, emp["id"], pay, gross=4000))
        for pay in ("2026-01-09", "2026-01-23", "2026-02-06")
    ]
    # $3,500 of FUTA wages a check: the second fills the $7,000 exactly
    assert [s["futa_tax"] for s in stubs] == [21.0, 21.0, 0.0]
    base = D(str(get_engine("TX").suta_wage_base))
    left = max(D("0"), min(D("3500"), base - D("7000")))
    assert _m(stubs[2]["suta_tax"]) == _m(left * D(str(SUTA_RATE)))

    f = compute_940(db_session, 2026)
    assert f["futa_taxable_wages"] == D("7000.00")
    assert f["total_payments"] == D("12000.00")
    # the cafeteria plan and the wages over $7,000 are both exempt payments
    assert f["exempt_payments"] == D("5000.00")
    sui = compute_sui(db_session, 2026, 1, "TX")
    assert sui["total_wages"] == D("12000.00")
    assert sui["total_suta_taxable_wages"] == D("7000.00") + left


# ---------------------------------------------------------------------------
# 3. A W-2 line per state, two to a form
# ---------------------------------------------------------------------------
_W2_KEYS = {
    "year",
    "employee_id",
    "num_stubs",
    "employee",
    "box1_federal_wages",
    "box2_federal_tax_withheld",
    "box3_ss_wages",
    "box4_ss_tax_withheld",
    "box5_medicare_wages",
    "box6_medicare_tax_withheld",
    "box16_state_wages",
    "box17_state_income_tax",
    "gross_pay",
    "pretax_deductions",
}


def _render(db, year, emp_id):
    from app.services.tax_forms.w2_w3 import render_w2_html

    return render_w2_html(db, year, emp_id, {"name": "Test Co"})


def test_a_one_state_w2_keeps_its_shape_and_its_hash(client, db_session):
    from app.services.document_audit import form_content_hash
    from app.services.payroll_documents import employer_block
    from app.services.tax_forms.w2_w3 import compute_w2

    emp = _employee(client, work_state="GA")
    _run(client, emp["id"], "2026-03-13")
    _run(client, emp["id"], "2026-03-27")
    w2 = compute_w2(db_session, 2026, emp["id"])
    assert set(w2) == _W2_KEYS  # no `states`: the shape it always had
    # figured the way it was before this change, the payload is the same,
    # so a one-state W-2 generated then hashes as it did
    before = compute_w2(db_session, 2026, emp["id"], legacy=True)
    company = employer_block(db_session)
    assert form_content_hash(company, w2) == form_content_hash(company, before)
    html = _render(db_session, 2026, emp["id"])
    assert "Form 1 of" not in html and html.count("FORM W-2") == 1
    assert ">GA<" in html


def test_a_two_state_w2_has_two_lines_on_one_form(client, db_session):
    from app.services.tax_forms.w2_w3 import compute_w2, compute_w3

    emp = _employee(client, work_state="GA")
    ga = _stub(_run(client, emp["id"], "2026-03-13"))
    al = _stub(_run(client, emp["id"], "2026-03-27", work_state="AL"))
    w2 = compute_w2(db_session, 2026, emp["id"])
    assert [ln["state"] for ln in w2["states"]] == ["AL", "GA"]
    by = {ln["state"]: ln for ln in w2["states"]}
    assert by["GA"]["box16_state_wages"] == D("4000.00")
    assert by["GA"]["box17_state_income_tax"] == _m(ga["state_tax"])
    assert by["AL"]["box17_state_income_tax"] == _m(al["state_tax"])
    # the totals stay, and are what the W-3 adds
    assert w2["box16_state_wages"] == D("8000.00")
    assert w2["box17_state_income_tax"] == _m(ga["state_tax"]) + _m(al["state_tax"])
    w3 = compute_w3(db_session, 2026)
    assert w3["box16_state_wages"] == D("8000.00")
    assert w3["box17_state_income_tax"] == w2["box17_state_income_tax"]
    html = _render(db_session, 2026, emp["id"])
    assert html.count("FORM W-2") == 1
    assert ">AL<" in html and ">GA<" in html


def test_a_three_state_w2_goes_on_two_forms(client, db_session):
    from app.services.tax_forms.w2_w3 import compute_w2, w2_forms, w2_state_lines

    emp = _employee(client, work_state="GA")
    assert (
        _put(
            client,
            emp["id"],
            {
                "year": 2026,
                "as_of": "2026-02-28",
                "quarters": [
                    {
                        "quarter": 1,
                        "gross": 6000,
                        "ss_wages": 6000,
                        "medicare_wages": 6000,
                        "futa_wages": 6000,
                        "net": 4500,
                        "states": [{"state": "FL", "wages": 6000, "suta_wages": 6000}],
                    }
                ],
            },
        ).status_code
        == 200
    )
    _run(client, emp["id"], "2026-03-13")
    _run(client, emp["id"], "2026-03-27", work_state="AL")
    w2 = compute_w2(db_session, 2026, emp["id"])
    assert [ln["state"] for ln in w2["states"]] == ["AL", "FL", "GA"]
    assert w2["box16_state_wages"] == D("14000.00")
    forms = w2_forms(w2, w2_state_lines(db_session, 2026, emp["id"]))
    assert [
        (f["number"], f["federal"], [ln["state"] for ln in f["states"]]) for f in forms
    ] == [
        (1, True, ["AL", "FL"]),
        (2, False, ["GA"]),
    ]
    html = _render(db_session, 2026, emp["id"])
    assert html.count("FORM W-2") == 2
    assert "Form 1 of 2" in html and "Form 2 of 2" in html
    # the federal boxes are reported once, on the first form
    assert html.count("Box 1 &mdash;") == 1
    assert "Boxes 1&ndash;14 are reported on Form 1 of 2" in html


def _check(client, row_id):
    r = client.get(f"/api/document-audits/{row_id}/check")
    assert r.status_code == 200, r.text
    return r.json()


def _generated_before(db, emp_id, year=2026):
    """A W-2 history row as the earlier code wrote it: the hash over the
    payload figured the way SlowBooks figured it then."""
    from app.services.document_audit import form_content_hash
    from app.services.payroll_documents import employer_block
    from app.services.tax_forms.w2_w3 import compute_w2

    row = DocumentAudit(
        doc_type="w2",
        doc_key=f"emp{emp_id}-yr{year}",
        content_hash=form_content_hash(
            employer_block(db), compute_w2(db, year, emp_id, legacy=True)
        ),
    )
    db.add(row)
    db.commit()
    return row.id


def test_a_two_state_w2_generated_before_still_checks(client, db_session):
    emp = _employee(client, work_state="GA")
    _run(client, emp["id"], "2026-03-13")
    _run(client, emp["id"], "2026-03-27", work_state="AL")
    row_id = _generated_before(db_session, emp["id"])
    body = _check(client, row_id)
    assert body["matches"] is True and body["figured_before_2_29"] is True
    assert "one state line per state" in body["message"]
    assert body["current_hash"] != body["generated_hash"]
    # and once the books change, it no longer matches either way
    _run(client, emp["id"], "2026-04-10")
    body = _check(client, row_id)
    assert body["matches"] is False and body["figured_before_2_29"] is False
    assert "the pay runs behind it changed" in body["message"]


def test_a_section_125_w2_generated_before_still_checks(client, db_session):
    emp = _employee(client)
    _enroll(client, emp["id"], _section125(client)["id"])
    _run(client, emp["id"], "2026-03-13")
    body = _check(client, _generated_before(db_session, emp["id"]))
    assert body["matches"] is True and body["figured_before_2_29"] is True


def test_a_w2_generated_now_checks_plainly(client, db_session, monkeypatch):
    from app.services.tax_forms import w2_w3

    monkeypatch.setattr(w2_w3, "render_pdf", lambda html, **kw: html.encode())
    emp = _employee(client, work_state="GA")
    _run(client, emp["id"], "2026-03-13")
    _run(client, emp["id"], "2026-03-27", work_state="AL")
    r = client.get(f"/api/payroll/forms/w2/{emp['id']}/pdf?year=2026")
    assert r.status_code == 200, r.text
    assert b"Box 15" in r.content and b">AL<" in r.content
    row = db_session.query(DocumentAudit).order_by(DocumentAudit.id.desc()).first()
    body = _check(client, row.id)
    assert body["matches"] is True and body["figured_before_2_29"] is False
    assert body["current_hash"] == body["generated_hash"]


# ---------------------------------------------------------------------------
# 2. Discard draft
# ---------------------------------------------------------------------------
def _time(client, emp_id, day, hours=8):
    te = client.post(
        "/api/time-entries",
        json={"employee_id": emp_id, "date": day, "hours_regular": hours},
    )
    assert te.status_code == 201, te.text
    r = client.post(
        f"/api/time-entries/{te.json()['id']}/approve", json={"approved_by": "x"}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _books(db, emp_id):
    """Everything a draft pay run can move, as plain values."""
    from app.routes.payroll.ytd import employee_ytd, wage_base_ytd

    db.expire_all()
    return {
        "runs": sorted(r.id for r in db.query(PayRun).all()),
        "stubs": db.query(PayStub).count(),
        "stub_benefits": db.query(PayStubBenefit).count(),
        "benefit_ytd": sorted(
            (
                y.employee_id,
                y.benefit_code_id,
                y.year,
                y.employee_amount,
                y.employer_amount,
            )
            for y in db.query(BenefitYTD).all()
        ),
        "balances": sorted(
            (a.id, a.balance_remaining) for a in db.query(EmployeeBenefit).all()
        ),
        "time": sorted(
            (t.id, t.pay_run_id, str(t.status)) for t in db.query(TimeEntry).all()
        ),
        "ytd": employee_ytd(db, emp_id, 2026),
        "bases": wage_base_ytd(db, emp_id, 2026, "TX"),
    }


def test_discarding_a_draft_leaves_the_books_as_they_were(client, db_session):
    emp = _employee(client)
    s125 = _section125(client, amount=200)
    loan = _code(
        client,
        "LOAN",
        category="posttax",
        tracks_balance=True,
        rate={"employee_rate": 300},
    )
    _enroll(client, emp["id"], s125["id"])
    loan_enr = _enroll(client, emp["id"], loan["id"], balance_remaining=1000)
    assert (
        _put(client, emp["id"], _opening(2026, "2026-03-31", 30000)).status_code == 200
    )
    # a processed run first, so the year-to-date rows already exist
    for day in ("2026-04-02", "2026-04-03"):
        _time(client, emp["id"], day)
    _run(
        client,
        emp["id"],
        "2026-04-10",
        gross=None,
        start="2026-04-01",
        use_time_entries=True,
    )
    # an employer-paid code enrolled since: only the draft makes its row
    hsa = _code(
        client,
        "HSAER",
        kind="benefit",
        rate={"employer_rate": 75},
    )
    _enroll(client, emp["id"], hsa["id"])
    for day in ("2026-04-14", "2026-04-15"):
        _time(client, emp["id"], day)
    before = _books(db_session, emp["id"])

    draft = _run(
        client,
        emp["id"],
        "2026-04-24",
        gross=None,
        start="2026-04-13",
        process=False,
        use_time_entries=True,
    )
    stub = _stub(draft)
    assert stub["gross_pay"] == 800.0
    during = _books(db_session, emp["id"])
    assert during != before
    assert len(during["benefit_ytd"]) == len(before["benefit_ytd"]) + 1
    assert dict(during["balances"])[loan_enr["id"]] == D("400.00")
    assert sum(1 for t in during["time"] if t[1] == draft["id"]) == 2

    r = client.delete(f"/api/payroll/{draft['id']}")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "status": "discarded",
        "pay_run_id": draft["id"],
        "released_time_entries": 2,
        "flagged_runs": [],
    }
    assert _books(db_session, emp["id"]) == before
    assert client.get(f"/api/payroll/{draft['id']}").status_code == 404
    # the released time is paid by the next run
    again = _run(
        client,
        emp["id"],
        "2026-04-24",
        gross=None,
        start="2026-04-13",
        process=False,
        use_time_entries=True,
    )
    assert _stub(again)["gross_pay"] == 800.0


def test_a_processed_run_is_not_discarded(client, db_session):
    emp = _employee(client)
    run = _run(client, emp["id"], "2026-04-10")
    r = client.delete(f"/api/payroll/{run['id']}")
    assert r.status_code == 409
    assert "is processed" in r.text and "voided" in r.text
    assert client.get(f"/api/payroll/{run['id']}").json()["status"] == "processed"
    assert client.delete("/api/payroll/999999").status_code == 404


def test_discarding_flags_a_later_draft_that_counted_it(client):
    emp = _employee(client)
    other = _employee(client, first="Lio", last="Park")
    first = _run(client, emp["id"], "2026-04-10", process=False)
    later = _run(client, emp["id"], "2026-04-24", process=False)
    theirs = _run(client, other["id"], "2026-04-24", process=False)
    r = client.delete(f"/api/payroll/{first['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["flagged_runs"] == [later["id"]]
    reason = client.get(f"/api/payroll/{later['id']}").json()["stale_reason"]
    assert f"draft pay run #{first['id']}" in reason and "Rae Okafor" in reason
    assert client.get(f"/api/payroll/{theirs['id']}").json()["stale_reason"] is None
    # a flagged draft asks before it is processed, and can be discarded too
    assert client.post(f"/api/payroll/{later['id']}/process").status_code == 409
    assert client.delete(f"/api/payroll/{later['id']}").status_code == 200


def test_discard_is_admin_only_like_the_rest_of_payroll(client, db_session):
    from app.models.users import User
    from app.services import auth as auth_service

    emp = _employee(client)
    draft = _run(client, emp["id"], "2026-04-10", process=False)
    db_session.add(
        User(
            username="books",
            display_name="Books",
            password_hash=auth_service.hash_password(FIXTURE_PW),
            role="bookkeeper",
            is_active=True,
        )
    )
    db_session.commit()
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": "books", "password": FIXTURE_PW}
    )
    assert r.status_code == 200, r.text
    assert client.delete(f"/api/payroll/{draft['id']}").status_code == 403


def test_the_payroll_page_offers_discard_for_a_draft():
    js = (ROOT / "app/static/js/payroll.js").read_text()
    assert "async discard(id, question)" in js
    assert "API.del(`/payroll/${id}`)" in js
    assert "PayrollPage.discard(${r.id})" in js  # the list row
    assert "PayrollPage.discard(${run.id})" in js  # the run's view
    process = js[js.index("async process(id)") :]
    process = process[: process.index("\n    },")]
    # a stale draft: process as figured, or discard it
    assert "acknowledge_stale=1" in process
    assert "PayrollPage.discard(id," in process
