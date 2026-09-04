"""Benefits engine: a benefit is a code with a rule.

The five hard-won rules from docs/design/benefits-engine.md, each with a
test that would fail without it: sequence changes the taxable base for the
next code; the three limits are three rules; rates are effective-dated and
resolve against the period end; posted runs snapshot the rule set; a
balance-tracking code stops at zero. Plus the employer side (match tiers,
GL mapping, remittance), groups vs assignments, YTD accumulators, PTO
dollar liability, and the job-costing burden seam.
"""

from decimal import Decimal

from sqlalchemy import func

from app.models.transactions import Transaction, TransactionLine


def _emp(client, rate=50, **kw):
    body = {
        "first_name": kw.pop("first_name", "Pat"),
        "last_name": kw.pop("last_name", "Payroll"),
        "pay_type": "hourly",
        "pay_rate": rate,
        "pay_frequency": "biweekly",
        "filing_status": "single",
        "work_state": "WA",
    }
    body.update(kw)
    r = client.post("/api/employees", json=body)
    assert r.status_code in (200, 201), r.text
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
    rate = kw.pop("rate", None)
    body.update(kw)
    if rate is not None:
        body["rate"] = rate
    r = client.post("/api/benefits/codes", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _enroll(client, emp_id, code_id, **kw):
    r = client.post(
        "/api/benefits/enrollments",
        json={"employee_id": emp_id, "benefit_code_id": code_id, **kw},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def _run(
    client,
    emp_ids,
    start="2026-07-01",
    end="2026-07-14",
    pay="2026-07-17",
    hours=80,
    process=False,
    **stub_extra,
):
    if isinstance(emp_ids, int):
        emp_ids = [emp_ids]
    r = client.post(
        "/api/payroll",
        json={
            "period_start": start,
            "period_end": end,
            "pay_date": pay,
            "stubs": [
                {"employee_id": e, "hours": hours, **stub_extra} for e in emp_ids
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    run = r.json()
    if process:
        p = client.post(f"/api/payroll/{run['id']}/process")
        assert p.status_code == 200, p.text
        run = client.get(f"/api/payroll/{run['id']}").json()
        run["_process"] = p.json()
    return run


def _benefit(stub, code):
    return next((b for b in stub["benefits"] if b["code"] == code), None)


def _je_lines(db, txn_id):
    return (
        db.query(TransactionLine).filter(TransactionLine.transaction_id == txn_id).all()
    )


# ---------------------------------------------------------------------------
# Rule 1: sequence
# ---------------------------------------------------------------------------
def test_sequence_changes_the_taxable_base_for_the_next_code(client, seed_accounts):
    emp = _emp(client)  # 80h × $50 = $4,000
    fixed = _code(client, "FIX", sequence=10, rate={"employee_rate": 500})
    pct = _code(
        client,
        "PCT",
        calc_method="percent_of_taxable",
        sequence=20,
        rate={"employee_rate": 10},
    )
    _enroll(client, emp["id"], fixed["id"])
    _enroll(client, emp["id"], pct["id"])
    stub = _run(client, emp["id"])["stubs"][0]
    assert _benefit(stub, "FIX")["employee_amount"] == 500.0
    # 10% of the base AFTER the $500 came out
    assert _benefit(stub, "PCT")["employee_amount"] == 350.0
    assert stub["pretax_deductions"] == 850.0
    assert [b["code"] for b in stub["benefits"]] == ["FIX", "PCT"]

    # swap the order: the percent sees the full gross
    client.put(f"/api/benefits/codes/{pct['id']}", json={"sequence": 5})
    stub = _run(
        client, emp["id"], start="2026-07-15", end="2026-07-28", pay="2026-07-31"
    )["stubs"][0]
    assert [b["code"] for b in stub["benefits"]] == ["PCT", "FIX"]
    assert _benefit(stub, "PCT")["employee_amount"] == 400.0


def test_pretax_flags_reduce_the_right_wage_bases(client, seed_accounts):
    """A 401(k)-style code reduces income tax but not FICA; a Section 125
    code reduces all three. Net still drops by the full deduction."""
    plain = _emp(client, first_name="Plain")
    k = _emp(client, first_name="Kay")
    s125 = _emp(client, first_name="Sam")
    k401 = _code(client, "K401", reduces_fica=False, rate={"employee_rate": 400})
    sec = _code(client, "SEC", reduces_fica=True, rate={"employee_rate": 400})
    _enroll(client, k["id"], k401["id"])
    _enroll(client, s125["id"], sec["id"])
    stubs = {
        s["employee_id"]: s
        for s in _run(client, [plain["id"], k["id"], s125["id"]])["stubs"]
    }
    base, sk, ss = stubs[plain["id"]], stubs[k["id"]], stubs[s125["id"]]
    assert sk["federal_tax"] < base["federal_tax"]
    assert sk["ss_tax"] == base["ss_tax"]  # FICA untouched by the 401(k)
    assert ss["ss_tax"] < base["ss_tax"]  # Section 125 reduces FICA wages
    assert round(ss["ss_tax"], 2) == round((4000 - 400) * 0.062, 2)
    for s in (sk, ss):
        # the check drops by the deduction less the tax it saved
        assert base["net_pay"] - 400 < s["net_pay"] < base["net_pay"] - 250


# ---------------------------------------------------------------------------
# Rule 2: limits are plural
# ---------------------------------------------------------------------------
def test_per_period_cap_annual_cap_and_wage_base_ceiling_are_three_rules(
    client, seed_accounts
):
    emp = _emp(client)
    per = _code(
        client,
        "PER",
        calc_method="percent_of_gross",
        rate={"employee_rate": 10, "per_period_cap": 300},
    )
    ann = _code(client, "ANN", rate={"employee_rate": 300, "annual_cap": 500})
    wb = _code(
        client,
        "WB",
        calc_method="percent_of_gross",
        rate={"employee_rate": 6, "wage_base_ceiling": 5000},
    )
    for c in (per, ann, wb):
        _enroll(client, emp["id"], c["id"])
    periods = [
        ("2026-07-01", "2026-07-14", "2026-07-17"),
        ("2026-07-15", "2026-07-28", "2026-07-31"),
        ("2026-07-29", "2026-08-11", "2026-08-14"),
    ]
    got = []
    for s, e, p in periods:
        stub = _run(client, emp["id"], start=s, end=e, pay=p)["stubs"][0]
        got.append(
            {
                k: _benefit(stub, k)["employee_amount"] if _benefit(stub, k) else 0.0
                for k in ("PER", "ANN", "WB")
            }
        )
    assert [g["PER"] for g in got] == [300.0, 300.0, 300.0]  # 10% of 4000 capped
    assert [g["ANN"] for g in got] == [300.0, 200.0, 0.0]  # 500 annual cap
    # 6% of wages up to $5,000 YTD: 4,000 then 1,000 then nothing
    assert [g["WB"] for g in got] == [240.0, 60.0, 0.0]

    ytd = client.get(f"/api/benefits/ytd?employee_id={emp['id']}&year=2026").json()
    by = {r["code"]: r for r in ytd}
    assert (
        by["ANN"]["employee_amount"] == 500.0 and by["WB"]["employee_amount"] == 300.0
    )


# ---------------------------------------------------------------------------
# Rule 3 + 4: effective dating and posted-run snapshots
# ---------------------------------------------------------------------------
def test_rates_resolve_against_period_end_and_posted_runs_keep_their_rule(
    client, seed_accounts
):
    emp = _emp(client)
    code = _code(
        client, "PREM", rate={"effective_from": "2000-01-01", "employee_rate": 100}
    )
    _enroll(client, emp["id"], code["id"])
    r = client.post(
        f"/api/benefits/codes/{code['id']}/rates",
        json={"effective_from": "2026-07-01", "employee_rate": 150},
    )
    assert r.status_code == 201, r.text
    rates = client.get(f"/api/benefits/codes/{code['id']}/rates").json()
    assert rates[0]["effective_to"] == "2026-06-30"  # the old row closed itself

    june = _run(
        client, emp["id"], start="2026-06-15", end="2026-06-28", pay="2026-07-02"
    )["stubs"][0]
    july = _run(
        client, emp["id"], start="2026-07-01", end="2026-07-14", pay="2026-07-17"
    )["stubs"][0]
    assert _benefit(june, "PREM")["employee_amount"] == 100.0
    assert _benefit(july, "PREM")["employee_amount"] == 150.0
    assert _benefit(july, "PREM")["employee_rate"] == 150.0

    # a later change never rewrites history
    client.post(
        f"/api/benefits/codes/{code['id']}/rates",
        json={"effective_from": "2026-08-01", "employee_rate": 999},
    )
    client.put(
        f"/api/benefits/codes/{code['id']}", json={"name": "Renamed", "sequence": 1}
    )
    runs = client.get("/api/payroll").json()
    july_again = next(s for r in runs for s in r["stubs"] if s["id"] == july["id"])
    snap = _benefit(july_again, "PREM")
    assert snap["employee_amount"] == 150.0 and snap["name"] == "Prem"

    # same start date twice is a conflict, not a silent overwrite
    dup = client.post(
        f"/api/benefits/codes/{code['id']}/rates",
        json={"effective_from": "2026-08-01", "employee_rate": 1},
    )
    assert dup.status_code == 409


def test_code_with_no_rate_in_force_does_not_apply(client, seed_accounts):
    emp = _emp(client)
    code = _code(
        client, "FUT", rate={"effective_from": "2027-01-01", "employee_rate": 100}
    )
    _enroll(client, emp["id"], code["id"])
    stub = _run(client, emp["id"])["stubs"][0]
    assert _benefit(stub, "FUT") is None
    assert stub["pretax_deductions"] == 0.0


# ---------------------------------------------------------------------------
# Rule 5: arbitrary balances
# ---------------------------------------------------------------------------
def test_loan_balance_runs_down_and_stops(client, seed_accounts):
    emp = _emp(client)
    loan = _code(
        client,
        "LOAN",
        category="posttax",
        tracks_balance=True,
        rate={"employee_rate": 300},
    )
    missing = client.post(
        "/api/benefits/enrollments",
        json={"employee_id": emp["id"], "benefit_code_id": loan["id"]},
    )
    assert missing.status_code == 400  # a balance code needs its starting balance
    enr = _enroll(client, emp["id"], loan["id"], balance_remaining=500)
    amounts = []
    for s, e, p in [
        ("2026-07-01", "2026-07-14", "2026-07-17"),
        ("2026-07-15", "2026-07-28", "2026-07-31"),
        ("2026-07-29", "2026-08-11", "2026-08-14"),
    ]:
        stub = _run(client, emp["id"], start=s, end=e, pay=p)["stubs"][0]
        b = _benefit(stub, "LOAN")
        amounts.append(b["employee_amount"] if b else 0.0)
    assert amounts == [300.0, 200.0, 0.0]
    after = next(
        x
        for x in client.get(f"/api/benefits/enrollments?employee_id={emp['id']}").json()
        if x["id"] == enr["id"]
    )
    assert after["balance_remaining"] == 0.0


# ---------------------------------------------------------------------------
# Employer side: match tiers, GL mapping, remittance
# ---------------------------------------------------------------------------
def test_standard_401k_tiered_match(client, seed_accounts):
    codes = client.post("/api/benefits/codes/seed-standard").json()
    k = next(c for c in codes if c["code"] == "401K")
    assert k["employer_calc_method"] == "match_percent"
    emp = _emp(client)
    _enroll(client, emp["id"], k["id"], employee_rate=5)  # 5% of $4,000
    stub = _run(client, emp["id"])["stubs"][0]
    b = _benefit(stub, "401K")
    assert b["employee_amount"] == 200.0
    # 100% of the first 3% (120) + 50% of the next 2% (40)
    assert b["employer_amount"] == 160.0
    assert stub["employer_benefits"] == 160.0


def test_employer_benefits_post_to_their_own_accounts_and_balance(
    client, db_session, seed_accounts
):
    ben_liab = seed_accounts["2380"].id
    ben_exp = seed_accounts["6150"].id
    emp = _emp(client)
    sec = _code(
        client,
        "SEC125",
        kind="both",
        reduces_fica=True,
        expense_account_id=ben_exp,
        liability_account_id=ben_liab,
        rate={"employee_rate": 100, "employer_rate": 300},
    )
    _enroll(client, emp["id"], sec["id"])
    run = _run(client, emp["id"], process=True)
    assert run["total_employer_benefits"] == 300.0
    lines = _je_lines(db_session, run["_process"]["transaction_id"])
    dr = sum(Decimal(str(ln.debit or 0)) for ln in lines)
    cr = sum(Decimal(str(ln.credit or 0)) for ln in lines)
    assert dr == cr and dr > 0
    by_acct = {}
    for ln in lines:
        by_acct.setdefault(ln.account_id, [Decimal("0"), Decimal("0")])
        by_acct[ln.account_id][0] += Decimal(str(ln.debit or 0))
        by_acct[ln.account_id][1] += Decimal(str(ln.credit or 0))
    assert by_acct[ben_liab][1] == Decimal("400.00")  # withheld 100 + employer 300
    assert by_acct[ben_exp][0] == Decimal("300.00")
    assert by_acct[seed_accounts["6110"].id][0] == Decimal("4000.00")
    # nothing leaked into the umbrella "other deductions" payable: it only
    # carries WA's state premiums (PFML / Cares), no benefit amounts
    stub = run["stubs"][0]
    assert by_acct[seed_accounts["2370"].id][1] == Decimal(
        str(round(stub["state_other_employee"] + stub["state_other_employer"], 2))
    )


def test_remittance_report_and_vendor_bill(client, db_session, seed_accounts):
    vendor = client.post("/api/vendors", json={"name": "Blue Shield"}).json()
    emp = _emp(client)
    sec = _code(
        client,
        "HLTH",
        kind="both",
        remittance_vendor_id=vendor["id"],
        liability_account_id=seed_accounts["2380"].id,
        expense_account_id=seed_accounts["6150"].id,
        rate={"employee_rate": 100, "employer_rate": 300},
    )
    _enroll(client, emp["id"], sec["id"])
    _run(client, emp["id"], process=True)
    _run(
        client,
        emp["id"],
        start="2026-07-15",
        end="2026-07-28",
        pay="2026-07-31",
        process=True,
    )
    _run(
        client, emp["id"], start="2026-07-29", end="2026-08-11", pay="2026-08-14"
    )  # draft: not remittable

    rep = client.get(
        "/api/benefits/remittance?start_date=2026-07-01&end_date=2026-07-31"
    ).json()
    assert len(rep["rows"]) == 1
    row = rep["rows"][0]
    assert row["vendor_name"] == "Blue Shield" and row["stub_count"] == 2
    assert row["employee_amount"] == 200.0 and row["employer_amount"] == 600.0
    assert rep["total_employer"] == 600.0

    bill = client.post(
        "/api/benefits/remittance/bill",
        json={
            "vendor_id": vendor["id"],
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
        },
    )
    assert bill.status_code == 201, bill.text
    assert bill.json()["total"] == 800.0
    detail = client.get(f"/api/bills/{bill.json()['bill_id']}").json()
    assert detail["lines"][0]["account_id"] == seed_accounts["2380"].id
    # the bill relieves the liability the runs credited: 2380 nets to zero
    liab = seed_accounts["2380"].id
    bal = (
        db_session.query(
            func.sum(TransactionLine.credit) - func.sum(TransactionLine.debit)
        )
        .filter(TransactionLine.account_id == liab)
        .scalar()
    )
    assert Decimal(str(bal)) == Decimal("0.00")

    empty = client.post(
        "/api/benefits/remittance/bill",
        json={
            "vendor_id": vendor["id"],
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
        },
    )
    assert empty.status_code == 400


# ---------------------------------------------------------------------------
# Groups vs assignments
# ---------------------------------------------------------------------------
def test_group_provides_defaults_and_assignment_wins(client, seed_accounts):
    sec = _code(
        client, "SEC", kind="both", rate={"employee_rate": 0, "employer_rate": 0}
    )
    dues = _code(client, "DUES", category="posttax", rate={"employee_rate": 25})
    g = client.post(
        "/api/benefits/groups",
        json={
            "name": "Field crew",
            "codes": [
                {
                    "benefit_code_id": sec["id"],
                    "employee_rate": 100,
                    "employer_rate": 300,
                },
                {"benefit_code_id": dues["id"]},
            ],
        },
    ).json()
    a = _emp(client, first_name="Ann")
    b = _emp(client, first_name="Bob")
    c = _emp(client, first_name="Cal")
    r = client.put(
        f"/api/benefits/groups/{g['id']}/members",
        json={"employee_ids": [a["id"], b["id"]]},
    )
    assert r.status_code == 200 and r.json()["member_count"] == 2
    # Bob elects a lower premium; employer side still comes from the group
    _enroll(client, b["id"], sec["id"], employee_rate=50)

    resolved = client.get(f"/api/benefits/employee/{b['id']}/resolved").json()
    by = {x["code"]: x for x in resolved}
    assert by["SEC"]["source"] == "assignment" and by["SEC"]["employee_rate"] == 50.0
    assert by["SEC"]["employer_rate"] == 300.0
    assert by["DUES"]["source"] == "group"

    stubs = {
        s["employee_id"]: s for s in _run(client, [a["id"], b["id"], c["id"]])["stubs"]
    }
    assert _benefit(stubs[a["id"]], "SEC")["employee_amount"] == 100.0
    assert _benefit(stubs[a["id"]], "SEC")["employer_amount"] == 300.0
    assert _benefit(stubs[a["id"]], "DUES")["employee_amount"] == 25.0
    assert _benefit(stubs[b["id"]], "SEC")["employee_amount"] == 50.0
    assert _benefit(stubs[b["id"]], "SEC")["employer_amount"] == 300.0
    assert stubs[c["id"]]["benefits"] == []  # not in the group, no enrollments

    # leaving the group stops the codes
    client.put(
        f"/api/benefits/groups/{g['id']}/members", json={"employee_ids": [b["id"]]}
    )
    stub = _run(
        client, a["id"], start="2026-07-15", end="2026-07-28", pay="2026-07-31"
    )["stubs"][0]
    assert stub["benefits"] == []


def test_ended_enrollment_and_retired_code_stop_applying(client, seed_accounts):
    emp = _emp(client)
    one = _code(client, "ONE", rate={"employee_rate": 10})
    two = _code(client, "TWO", rate={"employee_rate": 20})
    e1 = _enroll(client, emp["id"], one["id"])
    _enroll(client, emp["id"], two["id"])
    assert len(_run(client, emp["id"])["stubs"][0]["benefits"]) == 2
    client.delete(f"/api/benefits/enrollments/{e1['id']}?end_date=2026-07-20")
    client.delete(f"/api/benefits/codes/{two['id']}")
    stub = _run(
        client, emp["id"], start="2026-07-21", end="2026-08-03", pay="2026-08-07"
    )["stubs"][0]
    assert stub["benefits"] == []
    # the ended one is still on file for history
    hist = client.get(
        f"/api/benefits/enrollments?employee_id={emp['id']}&include_inactive=true"
    ).json()
    assert any(x["id"] == e1["id"] and x["end_date"] == "2026-07-20" for x in hist)


# ---------------------------------------------------------------------------
# YTD accumulators
# ---------------------------------------------------------------------------
def test_ytd_accumulators_match_a_rebuild_from_snapshots(client, seed_accounts):
    emp = _emp(client)
    code = _code(
        client, "ACC", kind="both", rate={"employee_rate": 40, "employer_rate": 60}
    )
    _enroll(client, emp["id"], code["id"])
    _run(client, emp["id"])
    _run(client, emp["id"], start="2026-07-15", end="2026-07-28", pay="2026-07-31")
    live = client.get(f"/api/benefits/ytd?employee_id={emp['id']}&year=2026").json()
    assert live[0]["employee_amount"] == 80.0 and live[0]["employer_amount"] == 120.0
    r = client.post("/api/benefits/ytd/rebuild?year=2026")
    assert r.status_code == 200 and r.json()["rows"] == 1
    rebuilt = client.get(f"/api/benefits/ytd?employee_id={emp['id']}&year=2026").json()
    assert rebuilt == live


# ---------------------------------------------------------------------------
# Ad-hoc amounts still work alongside codes
# ---------------------------------------------------------------------------
def test_adhoc_stub_amounts_post_to_the_umbrella_payable(
    client, db_session, seed_accounts
):
    emp = _emp(client)
    sec = _code(
        client,
        "SEC",
        liability_account_id=seed_accounts["2380"].id,
        rate={"employee_rate": 100},
    )
    _enroll(client, emp["id"], sec["id"])
    run = _run(
        client, emp["id"], process=True, pretax_deductions=50, posttax_deductions=30
    )
    stub = run["stubs"][0]
    assert stub["pretax_deductions"] == 150.0 and stub["posttax_deductions"] == 30.0
    lines = _je_lines(db_session, run["_process"]["transaction_id"])
    credits = {}
    for ln in lines:
        credits[ln.account_id] = credits.get(ln.account_id, Decimal("0")) + Decimal(
            str(ln.credit or 0)
        )
    assert credits[seed_accounts["2380"].id] == Decimal("100.00")
    state_other = round(stub["state_other_employee"] + stub["state_other_employer"], 2)
    assert credits[seed_accounts["2370"].id] == Decimal(str(round(80 + state_other, 2)))


# ---------------------------------------------------------------------------
# PTO dollar liability
# ---------------------------------------------------------------------------
def _acct_balance(db, acct_id):
    dr = (
        db.query(func.coalesce(func.sum(TransactionLine.debit), 0))
        .filter(TransactionLine.account_id == acct_id)
        .scalar()
    )
    cr = (
        db.query(func.coalesce(func.sum(TransactionLine.credit), 0))
        .filter(TransactionLine.account_id == acct_id)
        .scalar()
    )
    return Decimal(str(dr)), Decimal(str(cr))


def test_pto_bank_carries_dollars_and_posts_the_liability(
    client, db_session, seed_accounts
):
    emp = _emp(client, rate=50)
    pol = client.post(
        "/api/pto/policies",
        json={
            "name": "Vacation",
            "pto_type": "vacation",
            "accrual_method": "per_pay_period",
            "accrual_rate": 8,
            "max_carryover": 40,
            "accrue_liability": True,
            "valuation": "current_rate",
        },
    )
    assert pol.status_code == 201, pol.text
    acc = client.post(
        "/api/pto/accruals",
        json={"employee_id": emp["id"], "policy_id": pol.json()["id"], "balance": 0},
    ).json()
    r = client.post(
        f"/api/pto/accruals/{acc['id']}/accrue",
        json={"hours_worked": 0, "as_of": "2026-07-17"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["balance"] == 8.0 and r.json()["dollar_balance"] == 400.0
    exp_dr, _ = _acct_balance(db_session, seed_accounts["6160"].id)
    _, liab_cr = _acct_balance(db_session, seed_accounts["2390"].id)
    assert exp_dr == Decimal("400.00") and liab_cr == Decimal("400.00")

    # taking 4 hours relieves half
    req = client.post(
        "/api/pto/requests",
        json={
            "employee_id": emp["id"],
            "start_date": "2026-07-20",
            "end_date": "2026-07-20",
            "hours": 4,
            "pto_type": "vacation",
        },
    ).json()
    client.post(f"/api/pto/requests/{req['id']}/approve", json={})
    acc2 = next(
        a
        for a in client.get(f"/api/pto/accruals?employee_id={emp['id']}").json()
        if a["id"] == acc["id"]
    )
    assert acc2["balance"] == 4.0 and acc2["dollar_balance"] == 200.0
    liab_dr, liab_cr = _acct_balance(db_session, seed_accounts["2390"].id)
    assert liab_cr - liab_dr == Decimal("200.00")

    # a raise: revalue restates the 4 hours at $60
    client.put(f"/api/employees/{emp['id']}", json={"pay_rate": 60})
    r = client.post(
        f"/api/pto/accruals/{acc['id']}/revalue", json={"as_of": "2026-08-01"}
    )
    assert r.json()["dollar_balance"] == 240.0
    liab_dr, liab_cr = _acct_balance(db_session, seed_accounts["2390"].id)
    assert liab_cr - liab_dr == Decimal("240.00")

    # year-end cap forfeits hours AND their dollars
    for _ in range(6):
        client.post(
            f"/api/pto/accruals/{acc['id']}/accrue",
            json={"hours_worked": 0, "as_of": "2026-12-15"},
        )
    acc3 = next(
        a
        for a in client.get(f"/api/pto/accruals?employee_id={emp['id']}").json()
        if a["id"] == acc["id"]
    )
    assert acc3["balance"] == 52.0 and acc3["dollar_balance"] == 240.0 + 6 * 8 * 60
    client.post("/api/pto/accruals/year-end-carryover?target_year=2026")
    acc4 = next(
        a
        for a in client.get(f"/api/pto/accruals?employee_id={emp['id']}").json()
        if a["id"] == acc["id"]
    )
    assert acc4["balance"] == 40.0 and acc4["dollar_balance"] == 40 * 60
    liab_dr, liab_cr = _acct_balance(db_session, seed_accounts["2390"].id)
    assert liab_cr - liab_dr == Decimal("2400.00")


def test_pto_average_rate_valuation_relieves_at_historical_cost(client, seed_accounts):
    emp = _emp(client, rate=50)
    pol = client.post(
        "/api/pto/policies",
        json={
            "name": "Sick",
            "pto_type": "sick",
            "accrual_method": "per_pay_period",
            "accrual_rate": 8,
            "accrue_liability": True,
            "valuation": "average_rate",
        },
    ).json()
    acc = client.post(
        "/api/pto/accruals",
        json={"employee_id": emp["id"], "policy_id": pol["id"], "balance": 0},
    ).json()
    client.post(f"/api/pto/accruals/{acc['id']}/accrue", json={"hours_worked": 0})
    client.put(f"/api/employees/{emp['id']}", json={"pay_rate": 100})
    client.post(f"/api/pto/accruals/{acc['id']}/accrue", json={"hours_worked": 0})
    a = next(
        x
        for x in client.get(f"/api/pto/accruals?employee_id={emp['id']}").json()
        if x["id"] == acc["id"]
    )
    assert a["balance"] == 16.0 and a["dollar_balance"] == 1200.0  # 8×50 + 8×100
    req = client.post(
        "/api/pto/requests",
        json={
            "employee_id": emp["id"],
            "start_date": "2026-08-03",
            "end_date": "2026-08-03",
            "hours": 8,
            "pto_type": "sick",
        },
    ).json()
    client.post(f"/api/pto/requests/{req['id']}/approve", json={})
    a = next(
        x
        for x in client.get(f"/api/pto/accruals?employee_id={emp['id']}").json()
        if x["id"] == acc["id"]
    )
    assert a["balance"] == 8.0 and a["dollar_balance"] == 600.0  # 8 × $75 average


def test_pto_policy_without_liability_keeps_hours_only(
    client, db_session, seed_accounts
):
    emp = _emp(client)
    pol = client.post(
        "/api/pto/policies",
        json={
            "name": "Personal",
            "pto_type": "personal",
            "accrual_method": "per_pay_period",
            "accrual_rate": 4,
        },
    ).json()
    acc = client.post(
        "/api/pto/accruals",
        json={"employee_id": emp["id"], "policy_id": pol["id"], "balance": 0},
    ).json()
    r = client.post(
        f"/api/pto/accruals/{acc['id']}/accrue", json={"hours_worked": 0}
    ).json()
    assert r["balance"] == 4.0 and r["dollar_balance"] == 200.0  # valued, not posted
    assert (
        db_session.query(Transaction).filter(Transaction.source_type == "pto").count()
        == 0
    )


# ---------------------------------------------------------------------------
# Job costing seam: actual burden distributed to jobs
# ---------------------------------------------------------------------------
def test_payroll_burden_distributes_to_jobs_by_hours(
    client, db_session, seed_accounts, seed_customer
):
    types = {t["code"]: t for t in client.post("/api/cost-types/setup-offsets").json()}
    r = client.put(
        f"/api/cost-types/{types['labor']['id']}",
        json={"burden_pct": 20, "burden_method": "payroll"},
    )
    assert r.status_code == 200 and r.json()["burden_method"] == "payroll"
    job_a = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Barn"}
    ).json()
    job_b = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Shed"}
    ).json()
    emp = _emp(client, rate=50)
    health = _code(
        client,
        "HEALTH_ER",
        kind="benefit",
        burden_routing="job_burden",
        expense_account_id=seed_accounts["6150"].id,
        liability_account_id=seed_accounts["2380"].id,
        rate={"employer_rate": 300},
    )
    pool = _code(
        client,
        "GTL",
        kind="benefit",
        expense_account_id=seed_accounts["6150"].id,
        rate={"employer_rate": 20},
    )
    _enroll(client, emp["id"], health["id"])
    _enroll(client, emp["id"], pool["id"])

    ids = []
    for day, hrs, job in (
        ("2026-07-06", 30, job_a),
        ("2026-07-07", 10, job_b),
        ("2026-07-08", 40, None),
    ):
        body = {"employee_id": emp["id"], "date": day, "hours_regular": hrs}
        if job:
            body["job_id"] = job["id"]
        te = client.post("/api/time-entries", json=body).json()
        client.post(f"/api/time-entries/{te['id']}/submit")
        client.post(
            f"/api/time-entries/{te['id']}/approve", json={"approved_by": "admin"}
        )
        ids.append(te["id"])
    # with burden_method=payroll the time entry posts base labor only
    posted = client.post(f"/api/time-entries/{ids[0]}/post-to-job").json()
    assert posted["total"] == 1500.0  # 30h × $50, no flat 20%

    run = _run(client, emp["id"], use_time_entries=True, process=True)
    stub = run["stubs"][0]
    assert stub["hours"] == 80.0
    taxes = round(
        stub["employer_ss_tax"]
        + stub["employer_medicare_tax"]
        + stub["futa_tax"]
        + stub["suta_tax"]
        + stub["state_other_employer"],
        2,
    )
    assert taxes > 0
    assert run["burden_job_cost_id"], run
    jc = client.get(f"/api/job-costs/{run['burden_job_cost_id']}").json()
    assert jc["source"] == "payroll"
    by_job = {}
    for ln in jc["lines"]:
        assert ln["is_burden"]
        by_job[ln["job_id"]] = by_job.get(ln["job_id"], 0) + float(ln["amount"])
    # 30/80 and 10/80 of (taxes + 300); the 40 no-job hours stay in the pool
    assert round(by_job[job_a["id"]], 2) == round((taxes + 300) * 30 / 80, 2)
    assert round(by_job[job_b["id"]], 2) == round((taxes + 300) * 10 / 80, 2)
    assert round(sum(by_job.values()), 2) == round((taxes + 300) * 0.5, 2)
    assert not any(
        "GTL" in (ln["description"] or "") for ln in jc["lines"]
    )  # fringe pool stays put

    # P&L neutral: the distribution credits the accounts payroll expensed
    lines = _je_lines(db_session, jc["transaction_id"])
    credits = {}
    for ln in lines:
        if ln.credit:
            credits[ln.account_id] = credits.get(ln.account_id, Decimal("0")) + Decimal(
                str(ln.credit)
            )
    assert credits[seed_accounts["6150"].id] == Decimal("150.00")
    assert credits[seed_accounts["6120"].id] == Decimal(str(round(taxes * 0.5, 2)))
    tree = client.get(f"/api/jobs/{job_a['id']}/cost-tree").json()
    labor = next(t for t in tree["types"] if t["cost_type"] == "labor")
    assert round(labor["figures"]["actual"], 2) == round(
        1500 + (taxes + 300) * 30 / 80, 2
    )


def test_flat_burden_method_leaves_the_pay_run_alone(
    client, seed_accounts, seed_customer
):
    types = {t["code"]: t for t in client.post("/api/cost-types/setup-offsets").json()}
    client.put(f"/api/cost-types/{types['labor']['id']}", json={"burden_pct": 20})
    job = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Barn"}
    ).json()
    emp = _emp(client)
    te = client.post(
        "/api/time-entries",
        json={
            "employee_id": emp["id"],
            "date": "2026-07-06",
            "hours_regular": 8,
            "job_id": job["id"],
        },
    ).json()
    client.post(f"/api/time-entries/{te['id']}/submit")
    client.post(f"/api/time-entries/{te['id']}/approve", json={"approved_by": "admin"})
    assert (
        client.post(f"/api/time-entries/{te['id']}/post-to-job").json()["total"]
        == 480.0
    )  # 400 + 20%
    run = _run(client, emp["id"], use_time_entries=True, process=True)
    assert run["burden_job_cost_id"] is None


# ---------------------------------------------------------------------------
# Review fixes (macOS lap of the v2.8 stage, 2026-09-04)
# ---------------------------------------------------------------------------


def test_posttax_deduction_takes_what_is_left_never_a_negative_check(
    client, db_session, seed_accounts
):
    """A post-tax code bigger than the check used to produce a negative net
    and an unbalanced payroll entry (500 on process). It now takes what is
    left after taxes, says so on the stub, and the run posts."""
    emp = _emp(client, rate=20)
    dues = _code(
        client,
        "DUES",
        category="posttax",
        reduces_federal=False,
        reduces_state=False,
        rate={"employee_rate": 5000},
    )
    _enroll(client, emp["id"], dues["id"])
    run = _run(client, emp["id"], hours=10, process=True)
    stub = run["stubs"][0]
    gross = Decimal(str(stub["gross_pay"]))
    taxes = (
        gross - Decimal(str(stub["net_pay"])) - Decimal(str(stub["posttax_deductions"]))
    )
    assert gross == Decimal("200")
    assert taxes > 0
    assert Decimal(str(stub["net_pay"])) == Decimal("0")
    assert Decimal(str(stub["posttax_deductions"])) == gross - taxes
    assert _benefit(stub, "DUES")["employee_amount"] == float(gross - taxes)
    import json

    from app.models.payroll import PayStub

    detail = json.loads(db_session.get(PayStub, stub["id"]).detail_json or "{}")
    assert "not enough net pay" in detail["benefit:DUES:note"]
    lines = _je_lines(db_session, run["_process"]["transaction_id"])
    assert sum(Decimal(str(ln.debit)) for ln in lines) == sum(
        Decimal(str(ln.credit)) for ln in lines
    )


def test_remittance_follows_the_codes_vendor_when_the_snapshot_has_none(
    client, seed_accounts
):
    """Assigning a remittance vendor after a run was processed changes who
    gets paid, not what was withheld, so the report and the bill pick it up."""
    emp = _emp(client)
    plan = _code(client, "PLAN", rate={"employee_rate": 50})
    _enroll(client, emp["id"], plan["id"])
    _run(client, emp["id"], process=True)
    vendor = client.post("/api/vendors", json={"name": "Plan Admin"}).json()
    r = client.put(
        f"/api/benefits/codes/{plan['id']}", json={"remittance_vendor_id": vendor["id"]}
    )
    assert r.status_code == 200, r.text
    rows = client.get(
        "/api/benefits/remittance",
        params={"start_date": "2026-07-17", "end_date": "2026-07-17"},
    ).json()["rows"]
    row = next(x for x in rows if x["code"] == "PLAN")
    assert row["vendor_id"] == vendor["id"] and row["total"] == 50.0
    bill = client.post(
        "/api/benefits/remittance/bill",
        json={
            "vendor_id": vendor["id"],
            "start_date": "2026-07-17",
            "end_date": "2026-07-17",
        },
    )
    assert bill.status_code == 201, bill.text
    assert bill.json()["total"] == 50.0
