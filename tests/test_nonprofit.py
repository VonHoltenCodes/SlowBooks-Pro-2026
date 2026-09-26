"""Nonprofit mode — the ledger side.

Funds are classes with a restriction and a default function; posted lines
carry a function (program / management / fundraising) defaulted from the
class; every by-class report groups on the line's class first. The
reconciliation promise is the same one P&L by Class makes: the fund and
function views always add up to the plain Profit & Loss."""

from datetime import date
from decimal import Decimal

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction, TransactionLine
from app.services.accounting import create_journal_entry


def _accts(db_session):
    income = (
        db_session.query(Account)
        .filter(Account.account_type == AccountType.INCOME)
        .first()
    )
    expense = (
        db_session.query(Account)
        .filter(Account.account_type == AccountType.EXPENSE)
        .first()
    )
    return income, expense


# ---------------------------------------------------------------------------
# Classes as funds
# ---------------------------------------------------------------------------


def test_class_restriction_and_function_fields_round_trip(client):
    r = client.post(
        "/api/classes",
        json={
            "name": "Youth Program",
            "restriction": "temporarily_restricted",
            "default_function": "program",
            "donor_name": "Riverbend County Community Foundation",
            "purpose": "After-school music instruction",
        },
    )
    assert r.status_code == 201, r.text
    cls = r.json()
    assert cls["restriction"] == "temporarily_restricted"
    assert cls["default_function"] == "program"
    assert cls["donor_name"].startswith("Riverbend")

    plain = client.post("/api/classes", json={"name": "General"}).json()
    assert plain["restriction"] == "unrestricted"
    assert plain["default_function"] is None

    r = client.put(f"/api/classes/{cls['id']}", json={"default_function": None})
    assert r.status_code == 200 and r.json()["default_function"] is None
    r = client.put(f"/api/classes/{cls['id']}", json={"restriction": "banana"})
    assert r.status_code == 422
    r = client.post(
        "/api/classes", json={"name": "Bad", "default_function": "overhead"}
    )
    assert r.status_code == 422

    listed = {c["name"]: c for c in client.get("/api/classes").json()}
    assert listed["Youth Program"]["restriction"] == "temporarily_restricted"
    uncat = listed["Uncategorized"]
    r = client.put(
        f"/api/classes/{uncat['id']}", json={"restriction": "permanently_restricted"}
    )
    assert r.status_code == 400  # the untagged bucket stays unrestricted
    r = client.put(
        f"/api/classes/{uncat['id']}", json={"default_function": "management"}
    )
    assert r.status_code == 200 and r.json()["default_function"] == "management"


# ---------------------------------------------------------------------------
# The function dimension on posted lines
# ---------------------------------------------------------------------------


def test_function_defaults_from_class_and_explicit_none_wins(
    client, db_session, seed_accounts
):
    program = client.post(
        "/api/classes", json={"name": "Programs", "default_function": "program"}
    ).json()
    income, expense = _accts(db_session)

    txn = create_journal_entry(
        db_session,
        date(2026, 5, 1),
        "function defaulting",
        [
            # class only -> function from the class
            {
                "account_id": expense.id,
                "debit": Decimal("100"),
                "credit": Decimal("0"),
                "class_id": program["id"],
            },
            # explicit function wins over the class default
            {
                "account_id": expense.id,
                "debit": Decimal("50"),
                "credit": Decimal("0"),
                "class_id": program["id"],
                "function": "fundraising",
            },
            # explicit None stays NULL even though the class has a default
            {
                "account_id": expense.id,
                "debit": Decimal("25"),
                "credit": Decimal("0"),
                "class_id": program["id"],
                "function": None,
            },
            {"account_id": income.id, "debit": Decimal("0"), "credit": Decimal("175")},
        ],
    )
    db_session.commit()
    lines = sorted(
        db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all(),
        key=lambda ln: ln.id,
    )
    assert [ln.function for ln in lines] == ["program", "fundraising", None, None]

    # header class inherits too
    txn2 = create_journal_entry(
        db_session,
        date(2026, 5, 2),
        "header class",
        [
            {"account_id": expense.id, "debit": Decimal("10"), "credit": Decimal("0")},
            {"account_id": income.id, "debit": Decimal("0"), "credit": Decimal("10")},
        ],
        class_id=program["id"],
    )
    db_session.commit()
    assert {
        ln.function
        for ln in db_session.query(TransactionLine).filter_by(transaction_id=txn2.id)
    } == {"program"}


def test_journal_and_bill_lines_accept_function_and_voids_carry_it(
    client, db_session, seed_accounts
):
    fund = client.post("/api/classes", json={"name": "Gala"}).json()
    income, expense = _accts(db_session)
    r = client.post(
        "/api/journal",
        json={
            "date": "2026-06-01",
            "description": "gala costs",
            "lines": [
                {
                    "account_id": expense.id,
                    "debit": "300",
                    "class_id": fund["id"],
                    "function": "fundraising",
                },
                {"account_id": income.id, "credit": "300"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    je = r.json()
    posted = {ln["account_id"]: ln for ln in je["lines"]}
    assert posted[expense.id]["function"] == "fundraising"
    assert posted[expense.id]["class_id"] == fund["id"]

    r = client.post(f"/api/journal/{je['id']}/void")
    assert r.status_code == 200, r.text
    void_txn = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "manual_void", Transaction.source_id == je["id"]
        )
        .one()
    )
    reversed_expense = [ln for ln in void_txn.lines if ln.account_id == expense.id][0]
    assert reversed_expense.credit == Decimal("300")
    assert reversed_expense.class_id == fund["id"]
    assert reversed_expense.function == "fundraising"

    # After the void the fund nets to zero on P&L by Class
    data = client.get(
        "/api/reports/profit-loss-by-class?start_date=2026-06-01&end_date=2026-06-30"
    ).json()
    by_name = {c["class_name"]: c for c in data["classes"]}
    assert by_name["Gala"]["expenses"] == 0.0


# ---------------------------------------------------------------------------
# P&L by Class groups on the line's class
# ---------------------------------------------------------------------------


def test_profit_loss_by_class_uses_line_class(client, db_session, seed_accounts):
    a = client.post("/api/classes", json={"name": "Fund A"}).json()
    b = client.post("/api/classes", json={"name": "Fund B"}).json()
    income, expense = _accts(db_session)

    # header-less entry, each expense line tagged to a different fund
    create_journal_entry(
        db_session,
        date(2026, 8, 10),
        "split rent",
        [
            {
                "account_id": expense.id,
                "debit": Decimal("70"),
                "credit": Decimal("0"),
                "class_id": a["id"],
            },
            {
                "account_id": expense.id,
                "debit": Decimal("30"),
                "credit": Decimal("0"),
                "class_id": b["id"],
            },
            {"account_id": income.id, "debit": Decimal("0"), "credit": Decimal("100")},
        ],
    )
    # header class with one line overriding it
    create_journal_entry(
        db_session,
        date(2026, 8, 11),
        "mostly A",
        [
            {"account_id": expense.id, "debit": Decimal("20"), "credit": Decimal("0")},
            {
                "account_id": expense.id,
                "debit": Decimal("5"),
                "credit": Decimal("0"),
                "class_id": b["id"],
            },
            {"account_id": income.id, "debit": Decimal("0"), "credit": Decimal("25")},
        ],
        class_id=a["id"],
    )
    db_session.commit()

    data = client.get(
        "/api/reports/profit-loss-by-class?start_date=2026-08-01&end_date=2026-08-31"
    ).json()
    by_name = {c["class_name"]: c for c in data["classes"]}
    assert by_name["Fund A"]["expenses"] == 90.0  # 70 + 20
    assert by_name["Fund B"]["expenses"] == 35.0  # 30 + 5
    assert by_name["Uncategorized"]["income"] == 100.0  # header-less credit
    assert by_name["Fund A"]["income"] == 25.0

    plain = client.get(
        "/api/reports/profit-loss?start_date=2026-08-01&end_date=2026-08-31"
    ).json()
    assert abs(data["total_income"] - plain["total_income"]) < 0.01
    assert abs(data["total_expenses"] - plain["total_expenses"]) < 0.01
    assert abs(data["total_net_income"] - plain["net_income"]) < 0.01


# ---------------------------------------------------------------------------
# Release from restriction
# ---------------------------------------------------------------------------


def _ledger_balanced(db_session) -> bool:
    dr = db_session.query(TransactionLine).with_entities(TransactionLine.debit).all()
    cr = db_session.query(TransactionLine).with_entities(TransactionLine.credit).all()
    return sum(Decimal(str(d[0])) for d in dr) == sum(Decimal(str(c[0])) for c in cr)


def _restricted_fund(client, name="Youth Program"):
    return client.post(
        "/api/classes",
        json={
            "name": name,
            "restriction": "temporarily_restricted",
            "default_function": "program",
        },
    ).json()


def test_release_suggest_equals_class_expenses_less_prior_releases(
    client, db_session, seed_accounts
):
    fund = _restricted_fund(client)
    income, expense = _accts(db_session)
    create_journal_entry(
        db_session,
        date(2026, 3, 10),
        "grant spending",
        [
            {
                "account_id": expense.id,
                "debit": Decimal("3300"),
                "credit": Decimal("0"),
            },
            {"account_id": income.id, "debit": Decimal("0"), "credit": Decimal("3300")},
        ],
        class_id=fund["id"],
    )
    db_session.commit()

    s = client.get(
        f"/api/nonprofit/releases/suggest?class_id={fund['id']}"
        "&start_date=2026-01-01&end_date=2026-06-30"
    ).json()
    assert Decimal(s["expenses"]) == Decimal("3300")
    assert Decimal(s["released"]) == Decimal("0")
    assert Decimal(s["suggested"]) == Decimal("3300")

    # release part of it, then the suggestion drops by that much
    r = client.post(
        "/api/nonprofit/releases",
        json={
            "date": "2026-04-30",
            "class_id": fund["id"],
            "amount": "1000",
            "period_start": "2026-01-01",
            "period_end": "2026-04-30",
        },
    )
    assert r.status_code == 201, r.text
    s = client.get(
        f"/api/nonprofit/releases/suggest?class_id={fund['id']}"
        "&start_date=2026-01-01&end_date=2026-06-30"
    ).json()
    assert Decimal(s["released"]) == Decimal("1000")
    assert Decimal(s["suggested"]) == Decimal("2300")

    # amount omitted = the suggestion
    r = client.post(
        "/api/nonprofit/releases",
        json={
            "date": "2026-06-30",
            "class_id": fund["id"],
            "period_start": "2026-01-01",
            "period_end": "2026-06-30",
        },
    )
    assert r.status_code == 201, r.text
    assert Decimal(r.json()["amount"]) == Decimal("2300")
    assert r.json()["number"].startswith("RL-")
    assert r.json()["class_name"] == "Youth Program"

    # nothing left -> 422
    r = client.post(
        "/api/nonprofit/releases",
        json={"date": "2026-06-30", "class_id": fund["id"], "period_end": "2026-06-30"},
    )
    assert r.status_code == 422
    # an unrestricted fund cannot release
    plain = client.post("/api/classes", json={"name": "General"}).json()
    r = client.post(
        "/api/nonprofit/releases",
        json={"date": "2026-06-30", "class_id": plain["id"], "amount": "5"},
    )
    assert r.status_code == 422


def test_release_posts_and_voids_symmetrically(client, db_session, seed_accounts):
    from app.models.accounts import Account as _A

    fund = _restricted_fund(client, "Scholarship")
    r = client.post(
        "/api/nonprofit/releases",
        json={"date": "2026-06-30", "class_id": fund["id"], "amount": "750.25"},
    )
    assert r.status_code == 201, r.text
    rel = r.json()
    with_acct = (
        db_session.query(_A).filter_by(name="Net Assets With Donor Restrictions").one()
    )
    without_acct = (
        db_session.query(_A)
        .filter_by(name="Net Assets Without Donor Restrictions")
        .one()
    )
    txn = db_session.get(Transaction, rel["transaction_id"])
    assert txn.source_type == "restriction_release"
    by_acct = {ln.account_id: ln for ln in txn.lines}
    assert by_acct[with_acct.id].debit == Decimal("750.25")
    assert by_acct[without_acct.id].credit == Decimal("750.25")
    assert all(ln.class_id == fund["id"] for ln in txn.lines)
    assert all(ln.function is None for ln in txn.lines)
    assert _ledger_balanced(db_session)

    listed = client.get("/api/nonprofit/releases").json()
    assert [x["number"] for x in listed] == [rel["number"]]

    r = client.post(f"/api/nonprofit/releases/{rel['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"
    void_txn = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "restriction_release_void",
            Transaction.source_id == rel["id"],
        )
        .one()
    )
    rev = {ln.account_id: ln for ln in void_txn.lines}
    assert rev[with_acct.id].credit == Decimal("750.25")
    assert rev[with_acct.id].class_id == fund["id"]
    assert _ledger_balanced(db_session)
    # the fund's released total nets to zero
    s = client.get(
        f"/api/nonprofit/releases/suggest?class_id={fund['id']}&end_date=2026-12-31"
    ).json()
    assert Decimal(s["released"]) == Decimal("0")
    # second void refused
    assert client.post(f"/api/nonprofit/releases/{rel['id']}/void").status_code == 400


def test_release_respects_closing_date(client, db_session, seed_accounts):
    fund = _restricted_fund(client, "Endowment")
    r = client.post(
        "/api/nonprofit/releases",
        json={"date": "2026-02-15", "class_id": fund["id"], "amount": "10"},
    )
    assert r.status_code == 201, r.text
    assert (
        client.put("/api/settings", json={"closing_date": "2026-03-31"}).status_code
        == 200
    )
    blocked = client.post(
        "/api/nonprofit/releases",
        json={"date": "2026-03-01", "class_id": fund["id"], "amount": "10"},
    )
    assert blocked.status_code == 403
    assert (
        client.post(f"/api/nonprofit/releases/{r.json()['id']}/void").status_code == 403
    )


# ---------------------------------------------------------------------------
# Allocation rules, Split, and the period-end functional allocation
# ---------------------------------------------------------------------------


def _rule(client, name="Rent by square footage", **extra):
    body = {
        "name": name,
        "basis": "percent",
        "targets": [
            {"function": "program", "weight": 70},
            {"function": "management", "weight": 20},
            {"function": "fundraising", "weight": 10},
        ],
    }
    body.update(extra)
    r = client.post("/api/nonprofit/allocation-rules", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_allocation_rule_crud_and_validation(client, seed_accounts):
    rule = _rule(client)
    assert [t["function"] for t in rule["targets"]] == [
        "program",
        "management",
        "fundraising",
    ]
    assert rule["basis"] == "percent" and rule["is_active"] is True

    # duplicate name, bad basis, empty targets, target with nothing
    assert (
        client.post(
            "/api/nonprofit/allocation-rules",
            json={
                "name": "rent BY square footage",
                "targets": [{"function": "program"}],
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/nonprofit/allocation-rules",
            json={"name": "x", "basis": "moon", "targets": [{"function": "program"}]},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/nonprofit/allocation-rules", json={"name": "y", "targets": []}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/nonprofit/allocation-rules",
            json={"name": "z", "targets": [{"weight": 1}]},
        ).status_code
        == 422
    )
    # hours basis needs a job on every target
    assert (
        client.post(
            "/api/nonprofit/allocation-rules",
            json={"name": "h", "basis": "hours", "targets": [{"function": "program"}]},
        ).status_code
        == 422
    )

    # update replaces targets; deactivate hides from the default list
    r = client.put(
        f"/api/nonprofit/allocation-rules/{rule['id']}",
        json={
            "name": "Rent",
            "basis": "square_feet",
            "is_active": False,
            "targets": [
                {"function": "program", "weight": 1200},
                {"function": "management", "weight": 300},
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["targets"]) == 2 and r.json()["basis"] == "square_feet"
    assert client.get("/api/nonprofit/allocation-rules").json() == []
    assert (
        len(client.get("/api/nonprofit/allocation-rules?include_inactive=true").json())
        == 1
    )
    assert (
        client.delete(f"/api/nonprofit/allocation-rules/{rule['id']}").status_code
        == 200
    )
    assert (
        client.get(f"/api/nonprofit/allocation-rules/{rule['id']}").status_code == 404
    )


def test_split_is_cents_exact_and_hours_basis_reads_time_entries(
    client, db_session, seed_accounts, seed_customer
):
    rule = _rule(client)
    r = client.get(f"/api/nonprofit/allocation-rules/{rule['id']}/split?amount=100.01")
    assert r.status_code == 200, r.text
    lines = r.json()["lines"]
    amounts = {ln["function"]: Decimal(ln["amount"]) for ln in lines}
    assert sum(amounts.values()) == Decimal("100.01")
    assert amounts["management"] == Decimal("20.00")
    assert amounts["fundraising"] == Decimal("10.00")
    assert amounts["program"] == Decimal("70.01")  # remainder to the largest weight
    assert (
        client.get(
            f"/api/nonprofit/allocation-rules/{rule['id']}/split?amount=0"
        ).status_code
        == 422
    )

    # hours: two grants (jobs), 30 h and 10 h in July
    a = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Grant A"}
    ).json()
    b = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Grant B"}
    ).json()
    fa = client.post("/api/classes", json={"name": "Fund A"}).json()
    fb = client.post("/api/classes", json={"name": "Fund B"}).json()
    emp = client.post(
        "/api/employees",
        json={
            "first_name": "Bo",
            "last_name": "Hours",
            "pay_type": "hourly",
            "pay_rate": 20,
        },
    ).json()
    for job, hrs in ((a, 30), (b, 10)):
        client.post(
            "/api/time-entries",
            json={
                "employee_id": emp["id"],
                "date": "2026-07-12",
                "hours_regular": hrs,
                "job_id": job["id"],
            },
        )
    hours_rule = _rule(
        client,
        "Wages by grant hours",
        basis="hours",
        targets=[
            {"class_id": fa["id"], "function": "program", "job_id": a["id"]},
            {"class_id": fb["id"], "function": "program", "job_id": b["id"]},
        ],
    )
    r = client.get(
        f"/api/nonprofit/allocation-rules/{hours_rule['id']}/split?amount=1000&start_date=2026-07-01&end_date=2026-07-31"
    )
    assert r.status_code == 200, r.text
    shares = {ln["class_name"]: Decimal(ln["amount"]) for ln in r.json()["lines"]}
    assert shares == {"Fund A": Decimal("750.00"), "Fund B": Decimal("250.00")}
    # no hours in June -> nothing to split on
    r = client.get(
        f"/api/nonprofit/allocation-rules/{hours_rule['id']}/split?amount=1000&start_date=2026-06-01&end_date=2026-06-30"
    )
    assert r.status_code == 422


def test_functional_allocation_reclasses_and_is_idempotent_for_period(
    client, db_session, seed_accounts
):
    general = client.post(
        "/api/classes", json={"name": "General Fund", "default_function": "management"}
    ).json()
    rent = seed_accounts["6100"] if "6100" in seed_accounts else None
    if rent is None:
        rent = client.post(
            "/api/accounts",
            json={"name": "Rent", "account_number": "6100", "account_type": "expense"},
        ).json()
        rent_id = rent["id"]
    else:
        rent_id = rent.id
    checking = seed_accounts["1010"]
    # rent posted with NO function (explicit None) -> the unassigned pool
    create_journal_entry(
        db_session,
        date(2026, 7, 5),
        "July rent",
        [
            {
                "account_id": rent_id,
                "debit": Decimal("1000"),
                "credit": Decimal("0"),
                "class_id": general["id"],
                "function": None,
            },
            {
                "account_id": checking.id,
                "debit": Decimal("0"),
                "credit": Decimal("1000"),
            },
        ],
    )
    db_session.commit()
    rule = _rule(client, source_account_id=rent_id)

    pl_before = client.get(
        "/api/reports/profit-loss?start_date=2026-07-01&end_date=2026-07-31"
    ).json()
    byc_before = client.get(
        "/api/reports/profit-loss-by-class?start_date=2026-07-01&end_date=2026-07-31"
    ).json()

    preview = client.get(
        f"/api/nonprofit/allocation-rules/{rule['id']}/preview?start_date=2026-07-01&end_date=2026-07-31"
    ).json()
    assert Decimal(preview["total"]) == Decimal("1000")
    assert [Decimal(ln["amount"]) for ln in preview["lines"]] == [
        Decimal("700"),
        Decimal("200"),
        Decimal("100"),
    ]

    r = client.post(
        "/api/nonprofit/allocations",
        json={
            "date": "2026-07-31",
            "rule_id": rule["id"],
            "period_start": "2026-07-01",
            "period_end": "2026-07-31",
        },
    )
    assert r.status_code == 201, r.text
    fa = r.json()
    assert fa["number"].startswith("FA-") and Decimal(fa["total"]) == Decimal("1000")
    assert {ln["function"]: Decimal(ln["amount"]) for ln in fa["lines"]} == {
        "program": Decimal("700"),
        "management": Decimal("200"),
        "fundraising": Decimal("100"),
    }
    txn = db_session.get(Transaction, fa["transaction_id"])
    assert txn.source_type == "functional_allocation"
    by_fn = {}
    for ln in txn.lines:
        by_fn[ln.function] = by_fn.get(ln.function, Decimal("0")) + ln.debit - ln.credit
    assert by_fn == {
        "program": Decimal("700"),
        "management": Decimal("200"),
        "fundraising": Decimal("100"),
        None: Decimal("-1000"),
    }
    assert all(ln.class_id == general["id"] for ln in txn.lines)  # fund-neutral reclass
    assert _ledger_balanced(db_session)

    # P&L and P&L by Class unchanged by the reclass
    pl_after = client.get(
        "/api/reports/profit-loss?start_date=2026-07-01&end_date=2026-07-31"
    ).json()
    assert pl_after["total_expenses"] == pl_before["total_expenses"]
    byc_after = client.get(
        "/api/reports/profit-loss-by-class?start_date=2026-07-01&end_date=2026-07-31"
    ).json()
    assert {c["class_name"]: c["expenses"] for c in byc_after["classes"]} == {
        c["class_name"]: c["expenses"] for c in byc_before["classes"]
    }

    # the pool is empty now: a second run for the period is refused
    preview = client.get(
        f"/api/nonprofit/allocation-rules/{rule['id']}/preview?start_date=2026-07-01&end_date=2026-07-31"
    ).json()
    assert preview["pool"] == [] and Decimal(preview["total"]) == Decimal("0")
    again = client.post(
        "/api/nonprofit/allocations",
        json={
            "date": "2026-07-31",
            "rule_id": rule["id"],
            "period_start": "2026-07-01",
            "period_end": "2026-07-31",
        },
    )
    assert again.status_code == 422
    # a rule with posted runs cannot be deleted
    assert (
        client.delete(f"/api/nonprofit/allocation-rules/{rule['id']}").status_code
        == 409
    )

    # void puts the cost back in the pool
    r = client.post(f"/api/nonprofit/allocations/{fa['id']}/void")
    assert r.status_code == 200 and r.json()["status"] == "void"
    assert _ledger_balanced(db_session)
    preview = client.get(
        f"/api/nonprofit/allocation-rules/{rule['id']}/preview?start_date=2026-07-01&end_date=2026-07-31"
    ).json()
    assert Decimal(preview["total"]) == Decimal("1000")
    assert client.post(f"/api/nonprofit/allocations/{fa['id']}/void").status_code == 400
    listed = client.get("/api/nonprofit/allocations").json()
    assert [x["status"] for x in listed] == ["void"]


# ---------------------------------------------------------------------------
# The four statements reconcile to the plain P&L and balance sheet
# ---------------------------------------------------------------------------


def _je(db, d, desc, lines, **kw):
    create_journal_entry(db, d, desc, lines, **kw)


def _riverbend(client, db_session, seed_accounts):
    """A condensed Riverbend Community Arts: four funds, a grant, an
    endowment, unrestricted gifts, a gala, program and admin spending,
    shared rent allocated 70/20/10, and a release for what the grant
    spent. Amounts chosen so every equality is checkable by hand."""
    client.put("/api/settings", json={"company_type": "nonprofit"})
    client.post("/api/nonprofit/setup-accounts")

    def mk(name, restriction, fn):
        return client.post(
            "/api/classes",
            json={"name": name, "restriction": restriction, "default_function": fn},
        ).json()

    general = mk("General Fund", "unrestricted", "management")
    youth = mk("Youth Program", "temporarily_restricted", "program")
    endow = mk("Scholarship Endowment", "permanently_restricted", "program")
    gala = mk("Spring Gala", "unrestricted", "fundraising")
    checking = seed_accounts["1010"].id
    income = seed_accounts["4000"].id
    rent = seed_accounts["6100"].id if "6100" in seed_accounts else None
    if rent is None:
        rent = client.post(
            "/api/accounts",
            json={
                "name": "Rent Expense",
                "account_number": "6100",
                "account_type": "expense",
            },
        ).json()["id"]
    supplies = seed_accounts["6000"].id if "6000" in seed_accounts else rent
    d = date(2026, 3, 15)

    def dr(a, amt, **k):
        return {"account_id": a, "debit": Decimal(amt), "credit": Decimal("0"), **k}

    def cr(a, amt, **k):
        return {"account_id": a, "debit": Decimal("0"), "credit": Decimal(amt), **k}

    # revenue
    _je(
        db_session,
        d,
        "grant award",
        [dr(checking, "24000"), cr(income, "24000")],
        class_id=youth["id"],
    )
    _je(
        db_session,
        d,
        "endowment gift",
        [dr(checking, "50000"), cr(income, "50000")],
        class_id=endow["id"],
    )
    _je(
        db_session,
        d,
        "annual fund",
        [dr(checking, "1500"), cr(income, "1500")],
        class_id=general["id"],
    )
    _je(
        db_session,
        d,
        "gala tickets",
        [dr(checking, "1200"), cr(income, "1200")],
        class_id=gala["id"],
    )
    # spending
    _je(
        db_session,
        date(2026, 4, 10),
        "youth supplies",
        [dr(supplies, "3300"), cr(checking, "3300")],
        class_id=youth["id"],
    )
    _je(
        db_session,
        date(2026, 4, 12),
        "gala catering",
        [dr(supplies, "200"), cr(checking, "200")],
        class_id=gala["id"],
    )
    _je(
        db_session,
        date(2026, 4, 14),
        "admin",
        [dr(supplies, "400"), cr(checking, "400")],
        class_id=general["id"],
    )
    _je(
        db_session,
        date(2026, 4, 1),
        "April rent",
        [dr(rent, "1000", class_id=general["id"], function=None), cr(checking, "1000")],
    )
    db_session.commit()
    rule = _rule(client, source_account_id=rent)
    r = client.post(
        "/api/nonprofit/allocations",
        json={
            "date": "2026-04-30",
            "rule_id": rule["id"],
            "period_start": "2026-04-01",
            "period_end": "2026-04-30",
        },
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/nonprofit/releases",
        json={
            "date": "2026-04-30",
            "class_id": youth["id"],
            "period_start": "2026-01-01",
            "period_end": "2026-04-30",
        },
    )
    assert r.status_code == 201, r.text
    assert Decimal(r.json()["amount"]) == Decimal("3300")
    return {"general": general, "youth": youth, "endow": endow, "gala": gala}


def test_statement_of_activities_change_equals_pnl_net_income(
    client, db_session, seed_accounts
):
    _riverbend(client, db_session, seed_accounts)
    qs = "start_date=2026-01-01&end_date=2026-06-30"
    soa = client.get(f"/api/reports/statement-of-activities?{qs}").json()
    pl = client.get(f"/api/reports/profit-loss?{qs}").json()
    t = soa["totals"]
    assert abs(t["change_total"] - pl["net_income"]) < 0.005
    assert t["revenue"] == pl["total_income"]
    assert t["expenses"] == pl["total_expenses"] + pl["total_cogs"]
    assert soa["releases"] == {"without": 3300.0, "with": -3300.0, "total": 0.0}
    assert t["revenue_with"] == 74000.0 and t["revenue_without"] == 2700.0
    assert t["change_with"] == 74000.0 - 3300.0
    assert t["change_without"] == 2700.0 + 3300.0 - 4900.0
    # every expense sits in the "without" column
    assert all(r["with"] == 0.0 for r in soa["expenses"])


def test_functional_expenses_total_equals_pnl_expenses(
    client, db_session, seed_accounts
):
    _riverbend(client, db_session, seed_accounts)
    qs = "start_date=2026-01-01&end_date=2026-06-30"
    sfe = client.get(f"/api/reports/functional-expenses?{qs}").json()
    pl = client.get(f"/api/reports/profit-loss?{qs}").json()
    t = sfe["totals"]
    assert abs(t["total"] - (pl["total_expenses"] + pl["total_cogs"])) < 0.005
    assert t["program"] == 3300.0 + 700.0
    assert t["management"] == 400.0 + 200.0
    assert t["fundraising"] == 200.0 + 100.0
    assert t["unassigned"] == 0.0
    assert abs(t["program"] + t["management"] + t["fundraising"] - t["total"]) < 0.005
    programs = {p["class_name"]: p["amount"] for p in sfe["programs"]}
    assert programs["Youth Program"] == 3300.0
    assert sum(programs.values()) == t["program"]
    for r in sfe["rows"]:
        assert (
            abs(
                r["program"]
                + r["management"]
                + r["fundraising"]
                + r["unassigned"]
                - r["total"]
            )
            < 0.005
        )


def test_financial_position_balances_and_matches_balance_sheet(
    client, db_session, seed_accounts
):
    _riverbend(client, db_session, seed_accounts)
    sofp = client.get(
        "/api/reports/statement-of-financial-position?as_of_date=2026-06-30"
    ).json()
    bs = client.get("/api/reports/balance-sheet?as_of_date=2026-06-30").json()
    assert (
        abs(
            sofp["total_assets"]
            - (
                sofp["total_liabilities"]
                + sofp["net_assets_without"]
                + sofp["net_assets_with"]
            )
        )
        < 0.005
    )
    assert abs(sofp["total_net_assets"] - bs["total_equity"]) < 0.005
    assert sofp["total_assets"] == bs["total_assets"]
    assert sofp["net_assets_with"] == 74000.0 - 3300.0
    assert sofp["net_assets_without"] == 2700.0 + 3300.0 - 4900.0
    names = [r["account_name"] for r in sofp["net_assets"]]
    assert names[-2:] == [
        "Net Assets Without Donor Restrictions",
        "Net Assets With Donor Restrictions",
    ]
    assert "Net Income (current period)" not in names


def test_fund_balances_reconcile_to_financial_position(
    client, db_session, seed_accounts
):
    funds = _riverbend(client, db_session, seed_accounts)
    fb = client.get(
        "/api/reports/fund-balances?start_date=2026-01-01&end_date=2026-06-30"
    ).json()
    by = {f["class_name"]: f for f in fb["funds"]}
    assert set(by) == {"Youth Program", "Scholarship Endowment"}  # restricted only
    y = by["Youth Program"]
    assert (
        y["beginning"],
        y["contributions"],
        y["expenses"],
        y["releases"],
        y["ending"],
        y["unreleased"],
    ) == (0.0, 24000.0, 3300.0, 3300.0, 20700.0, 0.0)
    e = by["Scholarship Endowment"]
    assert e["ending"] == 50000.0 and e["restriction"] == "permanently_restricted"
    sofp = client.get(
        "/api/reports/statement-of-financial-position?as_of_date=2026-06-30"
    ).json()
    assert abs(fb["totals"]["ending"] - sofp["net_assets_with"]) < 0.005
    assert fb["unassigned"] is None

    # the next period starts where this one ended
    fb2 = client.get(
        "/api/reports/fund-balances?start_date=2026-07-01&end_date=2026-12-31"
    ).json()
    assert {f["class_name"]: f["beginning"] for f in fb2["funds"]} == {
        "Youth Program": 20700.0,
        "Scholarship Endowment": 50000.0,
    }
    assert funds["youth"]["id"] == y["class_id"]


def test_nonprofit_statement_pdfs_and_csvs_render(client, db_session, seed_accounts):
    _riverbend(client, db_session, seed_accounts)
    qs = "start_date=2026-01-01&end_date=2026-06-30"
    for path, q in (
        ("statement-of-activities", qs),
        ("statement-of-financial-position", "as_of_date=2026-06-30"),
        ("fund-balances", qs),
        ("functional-expenses", qs),
    ):
        pdf = client.get(f"/api/reports/{path}/pdf?{q}")
        assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-", path
        assert f"{path}_" in pdf.headers["content-disposition"]
        assert pdf.headers["content-disposition"].endswith('.pdf"')
        csv_r = client.get(f"/api/reports/{path}/csv?{q}")
        assert csv_r.status_code == 200 and csv_r.headers["content-type"].startswith(
            "text/csv"
        ), path
        text = csv_r.text
        assert text.splitlines()[0]  # a header row
        for line in text.splitlines():
            for cell in line.split(","):
                assert (
                    not cell or cell[0] not in "=+@" or cell.startswith("'")
                ), f"{path}: unsafe cell {cell!r}"
    # utf-8-sig: every CSV opens with the byte-order mark Excel needs
    sfe_csv = client.get(f"/api/reports/functional-expenses/csv?{qs}").content.decode(
        "utf-8-sig"
    )
    assert sfe_csv.startswith(
        "Expense,Total (A),Program services (B),Management and general (C),Fundraising (D),Unassigned"
    )
    # the statements pack switches to nonprofit sections
    pack = client.get(f"/api/reports/financial-statements/pdf?{qs}")
    assert pack.status_code == 200 and pack.content[:5] == b"%PDF-"


def test_saved_reports_accept_nonprofit_types(client, seed_accounts):
    for rt in (
        "statement_of_financial_position",
        "statement_of_activities",
        "fund_balances",
        "functional_expenses",
    ):
        r = client.post(
            "/api/saved-reports",
            json={
                "name": rt,
                "report_type": rt,
                "parameters": {"period": "this_year_to_date"},
            },
        )
        assert r.status_code in (200, 201), r.text


def test_bill_line_explicit_null_function_stays_unassigned(
    client, db_session, seed_accounts
):
    """An agent posting a shared cost says `function: null` to keep the line
    out of every column until a rule allocates it; omitting the key takes
    the fund's default."""
    general = client.post(
        "/api/classes", json={"name": "General", "default_function": "management"}
    ).json()
    vendor = client.post("/api/vendors", json={"name": "Landlord"}).json()
    rent = (
        seed_accounts["6100"].id
        if "6100" in seed_accounts
        else seed_accounts["6000"].id
    )
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-07-01",
            "terms": "Net 30",
            "class_id": general["id"],
            "lines": [
                {
                    "account_id": rent,
                    "description": "rent",
                    "quantity": 1,
                    "rate": "1000",
                    "function": None,
                },
                {
                    "account_id": rent,
                    "description": "admin share",
                    "quantity": 1,
                    "rate": "50",
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    txn = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "bill", Transaction.source_id == r.json()["id"]
        )
        .one()
    )
    by_desc = {ln.description: ln.function for ln in txn.lines if ln.account_id == rent}
    assert by_desc == {"rent": None, "admin share": "management"}


# ---------------------------------------------------------------------------
# Year over year: the prior-year column on the two statements
# ---------------------------------------------------------------------------


def test_prior_year_comparison_on_activities_and_functional_expenses(
    client, db_session, seed_accounts
):
    client.put("/api/settings", json={"company_type": "nonprofit"})
    client.post("/api/nonprofit/setup-accounts")
    program = client.post(
        "/api/classes", json={"name": "Programs", "default_function": "program"}
    ).json()
    income, expense = _accts(db_session)
    for d, inc, exp in (
        (date(2025, 4, 1), "1000", "400"),
        (date(2026, 4, 1), "1500", "700"),
    ):
        create_journal_entry(
            db_session,
            d,
            f"year {d.year}",
            [
                {
                    "account_id": seed_accounts["1010"].id,
                    "debit": Decimal(inc),
                    "credit": Decimal("0"),
                },
                {
                    "account_id": income.id,
                    "debit": Decimal("0"),
                    "credit": Decimal(inc),
                },
                {
                    "account_id": expense.id,
                    "debit": Decimal(exp),
                    "credit": Decimal("0"),
                },
                {
                    "account_id": seed_accounts["1010"].id,
                    "debit": Decimal("0"),
                    "credit": Decimal(exp),
                },
            ],
            class_id=program["id"],
        )
    db_session.commit()
    qs = "start_date=2026-01-01&end_date=2026-12-31"
    plain = client.get(f"/api/reports/statement-of-activities?{qs}").json()
    assert "prior" not in plain
    soa = client.get(
        f"/api/reports/statement-of-activities?{qs}&compare=prior_year"
    ).json()
    assert soa["compare"] == "prior_year"
    assert (
        soa["prior"]["start_date"] == "2025-01-01"
        and soa["prior"]["end_date"] == "2025-12-31"
    )
    assert (
        soa["totals"]["revenue"] == 1500.0
        and soa["prior"]["totals"]["revenue"] == 1000.0
    )
    rev = soa["revenue"][0]
    assert (
        rev["total"] == 1500.0
        and rev["prior_total"] == 1000.0
        and rev["change"] == 500.0
    )
    exp_row = soa["expenses"][0]
    assert exp_row["prior_total"] == 400.0 and exp_row["change"] == 300.0

    sfe = client.get(f"/api/reports/functional-expenses?{qs}&compare=prior_year").json()
    assert sfe["prior"]["totals"]["total"] == 400.0 and sfe["totals"]["total"] == 700.0
    assert sfe["rows"][0]["prior_total"] == 400.0 and sfe["rows"][0]["change"] == 300.0

    for path in ("statement-of-activities", "functional-expenses"):
        pdf = client.get(f"/api/reports/{path}/pdf?{qs}&compare=prior_year")
        assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
        csv_text = client.get(f"/api/reports/{path}/csv?{qs}&compare=prior_year").text
        header = csv_text.splitlines()[0]
        assert "Prior year (2025)" in header and header.endswith("Change")
        assert (
            csv_text.splitlines()[0].count(",")
            == client.get(f"/api/reports/{path}/csv?{qs}")
            .text.splitlines()[0]
            .count(",")
            + 2
        )


def test_report_pdfs_are_named_by_their_period_and_land_in_documents():
    """The desktop shell saves a report PDF under Documents/SlowBooks Pro/
    Reports and tells the user where; a period-stamped filename means two
    runs never overwrite each other."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    launcher = (root / "desktop_launcher.py").read_text(encoding="utf-8")
    # Reports by default; an invoice or statement goes to a Documents folder
    # beside it (F24, tests/test_desktop_pdf_names.py saves both for real)
    assert '"SlowBooks Pro" / folder' in launcher
    assert 'folder: str = "Reports"' in launcher
    assert "def reveal_path" in launcher
    assert 'return {"success": True, "path": str(dest)}' in launcher
    shim = (root / "app/static/js/desktop_shim.js").read_text(encoding="utf-8")
    assert "reveal_path" in shim and "Saved to" in shim
    utils = (root / "app/static/js/utils.js").read_text(encoding="utf-8")
    assert "function toastAction" in utils
