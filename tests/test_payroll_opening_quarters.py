"""Opening YTD by quarter, wage bases from the wage fields, every form (#433).

A company that switches mid-May enters what the old payroll paid in Q1 and
in Q2 to date. The old system filed the Q1 941, so SlowBooks' Q1 941 is
exactly those figures; the Q2 941 carries the Q2 opening and the June runs;
the liability schedule agrees with line 12. The paychecks' caps (Social
Security, Additional Medicare, FUTA, SUTA per state) start from the old
system's taxable-wage figures, and the 941, 940, W-2/W-3 and SUI report
measure the same way.
"""

import csv
import io
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.payroll_service import SS_WAGE_BASE

ROOT = Path(__file__).resolve().parents[1]
D = Decimal


@pytest.fixture(autouse=True)
def _chart(seed_accounts):
    return seed_accounts


def _employee(client, first="Sam", last="Ortiz", **kw):
    body = {
        "first_name": first,
        "last_name": last,
        "pay_type": "hourly",
        "pay_rate": 50,
        "pay_frequency": "biweekly",
        "filing_status": "single",
        "work_state": "GA",
    }
    body.update(kw)
    r = client.post("/api/employees", json=body)
    assert r.status_code == 201, r.text
    return r.json()


Q1 = {
    "quarter": 1,
    "gross": 18000,
    "federal_tax": 1860,
    "ss_wages": 17100,
    "ss_tax": 1060.20,
    "employer_ss_tax": 1060.20,
    "medicare_wages": 17100,
    "medicare_tax": 247.95,
    "employer_medicare_tax": 247.95,
    "futa_wages": 7000,
    "futa_tax": 42,
    "pretax_deductions": 900,
    "net": 13931.85,
    "states": [
        {
            "state": "GA",
            "wages": 12000,
            "withholding": 480,
            "suta_wages": 9500,
            "suta_tax": 256.50,
        },
        {
            "state": "AL",
            "wages": 5100,
            "withholding": 204,
            "suta_wages": 5100,
            "suta_tax": 137.70,
        },
    ],
}
Q2 = {
    "quarter": 2,
    "gross": 9000,
    "federal_tax": 930,
    "ss_wages": 8550,
    "ss_tax": 530.10,
    "employer_ss_tax": 530.10,
    "medicare_wages": 8550,
    "medicare_tax": 123.98,
    "employer_medicare_tax": 123.98,
    "pretax_deductions": 450,
    "net": 6965.92,
    "states": [{"state": "GA", "wages": 8550, "withholding": 342}],
}


def _may_switch(**kw):
    body = {"year": 2026, "as_of": "2026-05-15", "quarters": [Q1, Q2]}
    body.update(kw)
    return body


def _put(client, emp_id, body):
    return client.put(f"/api/employees/{emp_id}/opening-ytd", json=body)


def _run(client, emp_id, pay, gross=4000, process=True, **stub):
    r = client.post(
        "/api/payroll",
        json={
            "period_start": f"{pay[:8]}01",
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


def _stub(run):
    return run["stubs"][0]


def _m(x):
    return D(str(x))


# ---------------------------------------------------------------------------
# The record, by quarter
# ---------------------------------------------------------------------------
def test_quarters_are_stored_and_the_year_is_their_sum(client):
    emp = _employee(client)
    r = _put(client, emp["id"], _may_switch())
    assert r.status_code == 200, r.text
    o = r.json()["opening"]
    assert [q["quarter"] for q in o["quarters"]] == [1, 2]
    assert o["gross"] == 27000.0 and o["ss_wages"] == 25650.0
    q1_states = {s["state"]: s for s in o["quarters"][0]["states"]}
    assert q1_states["GA"]["suta_wages"] == 9500.0
    # the year's state totals, each state over its quarters
    totals = {s["state"]: s for s in o["states"]}
    assert totals["GA"]["wages"] == 20550.0 and totals["GA"]["withholding"] == 822.0
    assert totals["AL"]["suta_tax"] == 137.70


@pytest.mark.parametrize(
    "change, words",
    [
        ({"quarters": []}, "quarters is required"),
        ({"quarters": [Q1, {**Q2, "quarter": 3}]}, "after as_of"),
        ({"quarters": [Q1, {**Q2, "quarter": 1}]}, "Q1 is listed twice"),
        ({"quarters": [{**Q1, "quarter": 5}]}, "not 1 to 4"),
        (
            {"quarters": [{**Q1, "ss_wages": float(SS_WAGE_BASE) + 1}]},
            "Social Security wages for the year",
        ),
        ({"quarters": [Q1, {**Q2, "futa_wages": 1}]}, "FUTA wages for the year"),
        (
            {"quarters": [{**Q1, "states": [{"state": "GA"}, {"state": "ga"}]}]},
            "GA is listed twice",
        ),
    ],
)
def test_quarter_validation(client, change, words):
    emp = _employee(client)
    r = _put(client, emp["id"], _may_switch(**change))
    assert r.status_code == 422, r.text
    assert words in r.text


# ---------------------------------------------------------------------------
# A May switch: Q1 and Q2 to date, then June and July runs
# ---------------------------------------------------------------------------
def test_may_switch_941_by_quarter_and_the_liability_schedule_agrees(
    client, db_session
):
    from app.services.tax_forms.form_941 import compute_941
    from app.services.tax_forms.tax_liability import compute_tax_liability

    emp = _employee(client)
    assert _put(client, emp["id"], _may_switch()).status_code == 200
    q1_before_runs = compute_941(db_session, 2026, 1)
    june = _stub(_run(client, emp["id"], "2026-06-05"))
    july = _stub(_run(client, emp["id"], "2026-07-17"))

    # Q1: the old system filed it; SlowBooks' is exactly its figures
    q1 = compute_941(db_session, 2026, 1)
    assert q1 == q1_before_runs
    assert q1["total_wages"] == D("18000.00") and q1["num_stubs"] == 0
    assert q1["federal_income_tax_withheld"] == D("1860.00")
    assert q1["social_security_wages"] == D("17100.00")
    assert q1["social_security_tax_employee"] == D("1060.20")
    assert q1["total_tax_liability"] == D("1860") + 2 * D("1060.20") + 2 * D("247.95")

    # Q2: only Q2's opening and the June run, never January through May
    q2 = compute_941(db_session, 2026, 2)
    assert q2["total_wages"] == D("13000.00")
    assert q2["num_stubs"] == 1 and q2["num_employees"] == 1
    assert q2["federal_income_tax_withheld"] == D("930") + _m(june["federal_tax"])
    assert q2["social_security_wages"] == D("12550.00")
    assert q2["social_security_tax_employee"] == D("530.10") + _m(june["ss_tax"])
    assert q2["medicare_wages"] == D("12550.00")

    # Q3: the July run alone
    q3 = compute_941(db_session, 2026, 3)
    assert q3["total_wages"] == D("4000.00")
    assert q3["federal_income_tax_withheld"] == _m(july["federal_tax"])

    # line 12 and the schedule's 941 line agree, quarter by quarter; the
    # old payroll's share is named, and what is due is SlowBooks' own
    for q, form in ((1, q1), (2, q2), (3, q3)):
        sched = compute_tax_liability(db_session, 2026, q)
        line = next(x for x in sched["liabilities"] if x["form"] == "941")
        assert D(str(line["amount"])) == form["total_tax_liability"], q
    q2_sched = compute_tax_liability(db_session, 2026, 2)
    line = next(x for x in q2_sched["liabilities"] if x["form"] == "941")
    assert line["prior_system"] == 2238.16  # 930 + 2 x 530.10 + 2 x 123.98
    assert D(str(line["due"])) == _m(june["federal_tax"]) + 2 * _m(june["ss_tax"]) + _m(
        june["medicare_tax"]
    ) + _m(june["employer_medicare_tax"])
    wh = next(x for x in q2_sched["liabilities"] if x["form"] == "State WH")
    assert wh["prior_system"] == 342.0


def test_may_switch_w2_is_openings_plus_runs_every_box(client, db_session):
    from app.services.tax_forms.w2_w3 import compute_w2, compute_w3

    emp = _employee(client)
    assert _put(client, emp["id"], _may_switch()).status_code == 200
    june = _stub(_run(client, emp["id"], "2026-06-05"))
    july = _stub(_run(client, emp["id"], "2026-07-17"))
    stubs = (june, july)

    def runs(field):
        return sum((_m(s[field]) for s in stubs), D("0"))

    w2 = compute_w2(db_session, 2026, emp["id"])
    assert w2["gross_pay"] == D("35000.00")
    assert w2["box1_federal_wages"] == D("35000") - D("1350")
    assert w2["box2_federal_tax_withheld"] == D("2790") + runs("federal_tax")
    # boxes 3 and 5 from the old system's taxable wages plus the runs
    assert w2["box3_ss_wages"] == D("25650") + D("8000")
    assert w2["box4_ss_tax_withheld"] == D("1590.30") + runs("ss_tax")
    assert w2["box5_medicare_wages"] == D("25650") + D("8000")
    assert w2["box6_medicare_tax_withheld"] == D("371.93") + runs("medicare_tax")
    # box 16 from the state rows of both quarters, plus the runs' wages
    assert w2["box16_state_wages"] == D("12000") + D("5100") + D("8550") + D("8000")
    assert w2["box17_state_income_tax"] == D("1026") + runs("state_tax")
    w3 = compute_w3(db_session, 2026)
    assert w3["num_w2"] == 1
    for k in ("box1_federal_wages", "box3_ss_wages", "box16_state_wages"):
        assert w3[k] == w2[k]


def test_may_switch_940_does_not_refill_the_futa_base(client, db_session):
    from app.services.tax_forms.form_940 import compute_940

    emp = _employee(client)
    assert _put(client, emp["id"], _may_switch()).status_code == 200
    june = _stub(_run(client, emp["id"], "2026-06-05"))
    # the old system already counted the first $7,000 toward FUTA
    assert june["futa_tax"] == 0.0
    f = compute_940(db_session, 2026)
    assert f["total_payments"] == D("31000.00")
    assert f["futa_taxable_wages"] == D("7000.00")
    assert f["exempt_payments"] == D("24000.00")
    assert f["total_futa_tax"] == D("42.00")


def test_suta_base_is_per_state(client, db_session):
    """GA's $9,500 base was filled in Q1; AL's $8,000 has $2,900 left."""
    from app.services.tax_forms.state_sui import compute_sui

    emp = _employee(client)
    assert _put(client, emp["id"], _may_switch()).status_code == 200
    ga = _stub(_run(client, emp["id"], "2026-06-05"))
    assert ga["work_state"] == "GA"
    assert ga["suta_tax"] == 0.0
    al = _stub(_run(client, emp["id"], "2026-06-19", work_state="AL"))
    # 1.2% on the $2,900 left of AL's base; all-states gross would say 0
    assert al["suta_tax"] == 34.80
    al2 = _stub(_run(client, emp["id"], "2026-07-02", work_state="AL"))
    assert al2["suta_tax"] == 0.0

    q1_ga = compute_sui(db_session, 2026, 1, "GA")
    assert (q1_ga["total_wages"], q1_ga["total_suta_taxable_wages"]) == (
        D("12000.00"),
        D("9500.00"),
    )
    assert q1_ga["total_suta_tax"] == D("256.50")
    q1_al = compute_sui(db_session, 2026, 1, "AL")
    assert q1_al["employees"][0]["work_state"] == "AL"
    assert q1_al["total_suta_tax"] == D("137.70")
    q2_ga = compute_sui(db_session, 2026, 2, "GA")
    # the Q2 opening's GA wages and the June GA run; the AL run is AL's
    assert q2_ga["total_wages"] == D("8550") + D("4000")
    assert q2_ga["total_suta_tax"] == D("0.00")
    q2_al = compute_sui(db_session, 2026, 2, "AL")
    assert q2_al["total_wages"] == D("4000.00")
    assert q2_al["total_suta_tax"] == D("34.80")


def test_caps_measure_the_wage_fields_not_gross(client, db_session):
    """Gross of $190,000 before the switch, of which $180,000 was Social
    Security and Medicare wages (a cafeteria plan): the July run still owes
    Social Security on $4,500, and Additional Medicare starts at $200,000
    of Medicare wages, not of gross."""
    from app.services.tax_forms.form_941 import compute_941
    from app.services.tax_forms.w2_w3 import compute_w2

    emp = _employee(client, work_state="TX")
    base = {
        "employer_ss_tax": 0,
        "medicare_tax": 0,
        "employer_medicare_tax": 0,
        "futa_wages": 0,
        "net": 50000,
    }
    body = {
        "year": 2026,
        "as_of": "2026-06-30",
        "quarters": [
            {"quarter": 1, "gross": 100000, "ss_wages": 95000, "medicare_wages": 95000}
            | base,
            {"quarter": 2, "gross": 90000, "ss_wages": 85000, "medicare_wages": 85000}
            | base,
        ],
    }
    assert _put(client, emp["id"], body).status_code == 200, _put(
        client, emp["id"], body
    ).text
    stub = _stub(_run(client, emp["id"], "2026-07-17", gross=25000))
    gap = SS_WAGE_BASE - D("180000")
    assert _m(stub["ss_tax"]) == (gap * D("0.062")).quantize(D("0.01"))
    # 1.45% on 25,000 + 0.9% on the 5,000 above 200,000 of Medicare wages
    assert _m(stub["medicare_tax"]) == D("362.50") + D("45.00")
    assert _m(stub["employer_medicare_tax"]) == D("362.50")
    q3 = compute_941(db_session, 2026, 3)
    assert q3["social_security_wages"] == gap
    assert q3["additional_medicare_wages"] == D("5000.00")
    w2 = compute_w2(db_session, 2026, emp["id"])
    assert w2["box3_ss_wages"] == SS_WAGE_BASE
    assert w2["box5_medicare_wages"] == D("205000.00")


def test_a_run_before_as_of_does_not_count_the_opening(client):
    emp = _employee(client)
    assert _put(client, emp["id"], _may_switch()).status_code == 200
    early = _stub(_run(client, emp["id"], "2026-05-01", process=False))
    assert early["futa_tax"] == 24.0  # 0.6% of 4,000: the base starts empty


# ---------------------------------------------------------------------------
# The lock: as_of stays before the first processed run
# ---------------------------------------------------------------------------
def test_a_correction_cannot_move_as_of_past_the_first_processed_run(client):
    emp = _employee(client)
    assert _put(client, emp["id"], _may_switch()).status_code == 200
    _run(client, emp["id"], "2026-06-05")
    for as_of in ("2026-06-05", "2026-06-30"):
        r = _put(client, emp["id"], _may_switch(as_of=as_of, reason="moved"))
        assert r.status_code == 422, r.text
        assert "must be before the first processed pay run" in r.text
    got = client.get(f"/api/employees/{emp['id']}/opening-ytd?year=2026").json()
    assert got["opening"]["as_of"] == "2026-05-15"
    r = _put(client, emp["id"], _may_switch(as_of="2026-06-04", reason="date"))
    assert r.status_code == 200, r.text


def test_the_import_refuses_moving_as_of_past_the_first_run(client):
    emp = _employee(client)
    assert _put(client, emp["id"], _may_switch()).status_code == 200
    _run(client, emp["id"], "2026-06-05")
    _code_401k(client)
    text = _template_from_example(client).replace("2026-05-15", "2026-06-30")
    plan = _import(client, text, reason="re-run").json()
    assert plan["rows"][0]["action"] == "error"
    assert "must be before the first processed pay run" in plan["rows"][0]["note"]


# ---------------------------------------------------------------------------
# Drafts figured from the old figures
# ---------------------------------------------------------------------------
def test_a_draft_is_flagged_when_the_opening_changes(client):
    emp = _employee(client)
    other = _employee(client, first="Hana", last="Lee")
    assert _put(client, emp["id"], _may_switch()).status_code == 200
    draft = _run(client, emp["id"], "2026-06-05", process=False)
    theirs = _run(client, other["id"], "2026-06-05", process=False)
    assert draft["stale_reason"] is None
    # saving the same figures again changes nothing
    assert _put(client, emp["id"], _may_switch()).status_code == 200
    assert client.get(f"/api/payroll/{draft['id']}").json()["stale_reason"] is None

    changed = _may_switch(quarters=[Q1, {**Q2, "gross": 9500, "net": 7000}])
    assert _put(client, emp["id"], changed).status_code == 200
    got = client.get(f"/api/payroll/{draft['id']}").json()
    assert "Sam Ortiz" in got["stale_reason"]
    assert "wage bases came from the old figures" in got["stale_reason"]
    # another employee's draft is not touched
    assert client.get(f"/api/payroll/{theirs['id']}").json()["stale_reason"] is None

    r = client.post(f"/api/payroll/{draft['id']}/process")
    assert r.status_code == 409 and "acknowledge_stale=1" in r.text
    r = client.post(f"/api/payroll/{draft['id']}/process?acknowledge_stale=1")
    assert r.status_code == 200, r.text
    # a draft figured after the change is not flagged
    fresh = _run(client, emp["id"], "2026-06-19", process=False)
    assert fresh["stale_reason"] is None


def test_deleting_the_opening_flags_the_draft(client):
    emp = _employee(client)
    assert _put(client, emp["id"], _may_switch()).status_code == 200
    draft = _run(client, emp["id"], "2026-06-05", process=False)
    r = client.delete(f"/api/employees/{emp['id']}/opening-ytd?year=2026")
    assert r.status_code == 200, r.text
    assert client.get(f"/api/payroll/{draft['id']}").json()["stale_reason"]


# ---------------------------------------------------------------------------
# The template, by quarter
# ---------------------------------------------------------------------------
def _import(client, text, dry_run=1, **query):
    q = "&".join(f"{k}={v}" for k, v in query.items())
    return client.post(
        f"/api/payroll/opening-ytd/import?dry_run={dry_run}" + (f"&{q}" if q else ""),
        files={"file": ("opening.csv", text.encode(), "text/csv")},
    )


def _code_401k(client):
    code = client.post(
        "/api/benefits/codes",
        json={
            "code": "401K",
            "name": "401(k)",
            "kind": "deduction",
            "category": "pretax",
            "calc_method": "fixed_amount",
            "reduces_federal": True,
            "reduces_state": True,
            "reduces_fica": False,
            "rate": {"employee_rate": 0},
        },
    )
    assert code.status_code == 201, code.text
    return code.json()


def _template_from_example(client):
    r = client.get("/api/payroll/opening-ytd/template")
    assert r.status_code == 200
    return r.text.lstrip("﻿").replace("Example, Pat", "Ortiz, Sam")


def test_template_has_quarter_and_per_state_suta_columns(client):
    from app.services.payroll_opening_import import TEMPLATE_COLUMNS

    assert "Quarter" in TEMPLATE_COLUMNS
    assert TEMPLATE_COLUMNS.index("SUTA wages") > TEMPLATE_COLUMNS.index("State")
    header = next(csv.reader(io.StringIO(_template_from_example(client))))
    assert header[: len(TEMPLATE_COLUMNS)] == TEMPLATE_COLUMNS


def test_template_import_by_quarter(client):
    emp = _employee(client)
    _code_401k(client)
    text = _template_from_example(client)
    plan = _import(client, text).json()
    assert plan["format"] == "template", plan
    assert [(r["employee"], r["action"]) for r in plan["rows"]] == [
        ("Ortiz, Sam", "create")
    ], plan
    row = plan["rows"][0]
    assert [q["quarter"] for q in row["quarters"]] == [1, 2]
    assert [s["state"] for s in row["quarters"][0]["states"]] == ["GA", "AL"]
    assert row["gross"] == 27000.0

    done = _import(client, text, dry_run=0).json()
    assert done["applied"] == 1
    o = client.get(f"/api/employees/{emp['id']}/opening-ytd?year=2026").json()
    o = o["opening"]
    assert o["as_of"] == "2026-05-15"
    assert [q["quarter"] for q in o["quarters"]] == [1, 2]
    assert o["quarters"][0]["futa_wages"] == 7000.0
    ga = next(s for s in o["quarters"][0]["states"] if s["state"] == "GA")
    assert (ga["suta_wages"], ga["suta_tax"]) == (9500.0, 256.5)
    # a code's amounts on both quarter rows add up to the year's
    assert o["benefits"][0]["employee_amount"] == 1350.0
    assert o["benefits"][0]["employer_amount"] == 675.0


def test_a_blank_quarter_is_the_quarter_of_as_of(client):
    from app.services.payroll_opening_import import TEMPLATE_COLUMNS

    emp = _employee(client)
    row = {"Employee": "Sam Ortiz", "As of": "2026-08-14", "Gross": "1000"}
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(TEMPLATE_COLUMNS)
    w.writerow([row.get(c, "") for c in TEMPLATE_COLUMNS])
    done = _import(client, out.getvalue(), dry_run=0).json()
    assert done["applied"] == 1, done
    o = client.get(f"/api/employees/{emp['id']}/opening-ytd?year=2026").json()
    assert [q["quarter"] for q in o["opening"]["quarters"]] == [3]


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------
def _check_migration(url):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    import sqlalchemy as sa

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = url
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["a4330b1c2d3e"]
    assert script.get_revision("a4330b1c2d3e").down_revision == "4ac01efbd72a"
    command.upgrade(cfg, "head")
    engine = sa.create_engine(url)
    try:
        insp = sa.inspect(engine)
        assert {
            "payroll_opening_balances",
            "payroll_opening_quarters",
            "payroll_opening_state_balances",
            "payroll_opening_benefits",
        } <= set(insp.get_table_names())
        state_cols = {
            c["name"] for c in insp.get_columns("payroll_opening_state_balances")
        }
        assert {"quarter", "state", "suta_wages", "suta_tax"} <= state_cols
        assert "stale_reason" in {c["name"] for c in insp.get_columns("pay_runs")}
        uniques = {
            u["name"]: u["column_names"]
            for u in insp.get_unique_constraints("payroll_opening_state_balances")
        }
        assert uniques["uq_payroll_opening_state"] == ["opening_id", "quarter", "state"]
        command.downgrade(cfg, "4ac01efbd72a")
        insp = sa.inspect(engine)
        assert not [t for t in insp.get_table_names() if t.startswith("payroll_open")]
        assert "stale_reason" not in {c["name"] for c in insp.get_columns("pay_runs")}
        # and up again
        command.upgrade(cfg, "head")
    finally:
        engine.dispose()


def test_the_migration_on_sqlite(tmp_path):
    _check_migration("sqlite:///" + (tmp_path / "fresh.db").as_posix())


@pytest.fixture
def postgres_url():
    import os
    import uuid

    import sqlalchemy as sa

    admin_url = os.environ.get("SLOWBOOKS_TEST_POSTGRES_URL")
    if not admin_url:
        pytest.skip("SLOWBOOKS_TEST_POSTGRES_URL is not set")
    pytest.importorskip("psycopg2")
    name = "slowbooks_opening_" + uuid.uuid4().hex[:12]
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # pragma: no cover - depends on the machine
        admin.dispose()
        pytest.skip(f"cannot create a scratch PostgreSQL database: {exc}")
    url = sa.engine.make_url(admin_url).set(database=name)
    try:
        yield url.render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def test_the_migration_on_postgresql(postgres_url):
    _check_migration(postgres_url)


# ---------------------------------------------------------------------------
# The pages
# ---------------------------------------------------------------------------
def test_employee_page_enters_the_opening_by_quarter():
    js = (ROOT / "app/static/js/employees.js").read_text()
    assert "emp-opening-quarters-${id}" in js
    assert "data-opening-quarter" in js and "<fieldset" in js
    assert "suta_wages" in js and "SUTA wages" in js
    assert "body.quarters" in js


def test_payroll_page_shows_a_stale_draft_and_asks_before_processing():
    js = (ROOT / "app/static/js/payroll.js").read_text()
    assert "stale_reason" in js
    assert "acknowledge_stale=1" in js
