"""A read never changes the database (#662).

GET routes used to save a default the first time they were asked for it:
the Uncategorized class (the classes list, Classes activity, P&L by Class,
every class filter, the nonprofit statements), the nonprofit net-asset
accounts (the statements, the release suggestion), the five starting cost
types (the list, the job cost tree, the forecast, the WIP schedule), the
default fixed asset type and an employee's onboarding checklist. A read-only
token could change the books through them. Each answers what is saved now,
or the default without saving it, and the default is saved where it is first
written: a class's POST, the company's setup, the migration, the checklist's
own POST.
"""

import re
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import event, inspect, text

from app.models.classes import TxnClass
from app.models.hr import OnboardingTask
from app.models.job_costing import CostType
from app.services.accounting import create_journal_entry
from tests.test_api_tokens import _bearer, _mint

ROOT = Path(__file__).resolve().parents[1]
YEAR = "start_date=2026-01-01&end_date=2026-12-31"


def _counts(db_session) -> dict:
    """The rows in every table."""
    conn = db_session.connection()
    return {
        name: conn.execute(text(f'SELECT count(*) FROM "{name}"')).scalar()
        for name in inspect(conn).get_table_names()
    }


def _changed(before: dict, after: dict) -> dict:
    return {k: (before[k], after[k]) for k in before if before[k] != after[k]}


_WRITE = re.compile(r"\s*(INSERT|UPDATE|DELETE)\b", re.IGNORECASE)


@contextmanager
def _statements_written(engine):
    """The INSERT, UPDATE and DELETE statements sent inside the block, even
    one a request flushes and then rolls back."""
    seen = []

    def note(_conn, _cursor, statement, *_args):
        if _WRITE.match(statement):
            seen.append(" ".join(statement.split()[:3]))

    event.listen(engine, "before_cursor_execute", note)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", note)


def _read(db_session, db_engine, api, url, status=200):
    """GET url: it answers `status`, sends no write and leaves every table
    with the rows it had."""
    before = _counts(db_session)
    with _statements_written(db_engine) as written:
        r = api.get(url)
    assert r.status_code == status, (url, r.status_code, r.text[:300])
    assert written == [], url
    assert _changed(before, _counts(db_session)) == {}, url
    return r


def _reads_change_nothing(db_session, db_engine, api, urls):
    api.get("/api/customers")  # a token's first request stamps it used
    for url in urls:
        _read(db_session, db_engine, api, url)


def _reader(client):
    return _bearer(_mint(client, "reader", "readonly")["token"])


def _post(db_session, day, debit, credit, amount, class_id=None, **line):
    create_journal_entry(
        db_session,
        day,
        "entry",
        [
            {"account_id": debit, "debit": Decimal(amount), "credit": Decimal("0")},
            {
                "account_id": credit,
                "debit": Decimal("0"),
                "credit": Decimal(amount),
                **line,
            },
        ],
        class_id=class_id,
    )


@pytest.fixture
def classed(db_session, seed_accounts):
    """A class of the user's own and no system default, as in a database no
    migration made: 300 of income tagged Retail, 200 untagged."""
    retail = TxnClass(name="Retail")
    db_session.add(retail)
    db_session.commit()
    bank, income = seed_accounts["1000"].id, seed_accounts["4000"].id
    _post(db_session, date(2026, 3, 2), bank, income, "300", class_id=retail.id)
    _post(db_session, date(2026, 3, 3), bank, income, "200")
    db_session.commit()
    return {"retail": retail.id, "bank": bank, "income": income}


# ── the Uncategorized class and the net-asset accounts ─────────────────────


def test_class_reads_make_no_default_class_and_no_accounts(
    client, db_session, db_engine, classed
):
    rule = client.post(
        "/api/nonprofit/allocation-rules",
        json={
            "name": "Rent split",
            "basis": "percent",
            "targets": [
                {"function": "program", "weight": 70},
                {"function": "management", "weight": 30},
            ],
        },
    )
    assert rule.status_code == 201, rule.text
    rid, retail, income = rule.json()["id"], classed["retail"], classed["income"]
    _reads_change_nothing(
        db_session,
        db_engine,
        _reader(client),
        [
            "/api/classes",
            "/api/classes?include_archived=true",
            f"/api/classes/activity?{YEAR}",
            f"/api/classes/{retail}/summary?{YEAR}",
            f"/api/classes/{retail}/transactions?{YEAR}",
            f"/api/reports/profit-loss-by-class?{YEAR}",
            f"/api/reports/profit-loss-by-class?{YEAR}&include_empty=true",
            f"/api/reports/profit-loss-by-class/csv?{YEAR}",
            f"/api/reports/profit-loss?{YEAR}&class_id={retail}",
            f"/api/reports/profit-loss/csv?{YEAR}&class_id={retail}",
            f"/api/reports/general-ledger?{YEAR}&class_id={retail}",
            f"/api/reports/profit-loss-detail?{YEAR}&class_id={retail}",
            f"/api/reports/account-transactions?{YEAR}&account_id={income}"
            f"&class_id={retail}",
            f"/api/banking/check-register?{YEAR}&class_id={retail}",
            f"/api/reports/functional-expenses?{YEAR}",
            f"/api/reports/fund-balances?{YEAR}",
            f"/api/reports/statement-of-activities?{YEAR}",
            "/api/reports/statement-of-financial-position?as_of_date=2026-12-31",
            f"/api/nonprofit/releases/suggest?class_id={retail}&{YEAR}",
            f"/api/nonprofit/allocation-rules/{rid}/preview?{YEAR}",
        ],
    )
    assert [c.name for c in db_session.query(TxnClass)] == ["Retail"]


def test_untagged_activity_is_still_the_uncategorized_column(
    client, db_session, classed
):
    """With no default class saved, P&L by Class and the Classes list still
    show the untagged 200 as Uncategorized, first, without an id; once the
    first class made through the API brings the default, it has one."""
    grid = client.get(f"/api/reports/profit-loss-by-class?{YEAR}").json()
    assert [(c["class_name"], c["class_id"], c["income"]) for c in grid["classes"]] == [
        ("Uncategorized", None, 200.0),
        ("Retail", classed["retail"], 300.0),
    ]
    assert grid["total_income"] == 500.0
    rows = client.get(f"/api/classes/activity?{YEAR}").json()["classes"]
    assert [
        (r["name"], r["id"], r["is_system_default"], r["income"]) for r in rows
    ] == [
        ("Uncategorized", None, True, 200.0),
        ("Retail", classed["retail"], False, 300.0),
    ]
    # the untagged lines are not Retail's
    mine = client.get(f"/api/classes/{classed['retail']}/transactions?{YEAR}").json()
    assert mine["net_income"] == 300.0

    made = client.post("/api/classes", json={"name": "Wholesale"})
    assert made.status_code == 201, made.text
    default = db_session.query(TxnClass).filter(TxnClass.is_system_default).one()
    assert default.name == "Uncategorized"
    grid = client.get(f"/api/reports/profit-loss-by-class?{YEAR}").json()
    assert (grid["classes"][0]["class_name"], grid["classes"][0]["class_id"]) == (
        "Uncategorized",
        default.id,
    )
    assert grid["classes"][0]["income"] == 200.0
    rows = client.get(f"/api/classes/activity?{YEAR}").json()["classes"]
    assert (rows[0]["id"], rows[0]["income"]) == (default.id, 200.0)
    # a class named Uncategorized meets the default, not a second row
    assert client.post("/api/classes", json={"name": "uncategorized"}).status_code == (
        409
    )


def test_the_nonprofit_statements_show_the_net_assets_the_chart_does_not_have_yet(
    client, db_session, classed
):
    sofp = client.get(
        "/api/reports/statement-of-financial-position?as_of_date=2026-12-31"
    ).json()
    rows = {r["account_name"]: r for r in sofp["net_assets"]}
    without = rows["Net Assets Without Donor Restrictions"]
    assert (without["account_id"], without["account_number"]) == (None, "3300")
    assert without["amount"] == 500.0
    assert rows["Net Assets With Donor Restrictions"]["amount"] == 0.0
    assert sofp["total_net_assets"] == 500.0
    funds = client.get(f"/api/reports/fund-balances?{YEAR}").json()
    assert funds["funds"] == [] and funds["unassigned"] is None
    soa = client.get(f"/api/reports/statement-of-activities?{YEAR}").json()
    assert soa["totals"]["releases"] == 0.0
    assert soa["totals"]["change_total"] == 500.0


def test_untagged_program_spending_is_named_uncategorized(
    client, db_session, seed_accounts
):
    bank, expense = seed_accounts["1000"].id, seed_accounts["6000"].id
    create_journal_entry(
        db_session,
        date(2026, 4, 1),
        "outreach",
        [
            {
                "account_id": expense,
                "debit": Decimal("80"),
                "credit": Decimal("0"),
                "function": "program",
            },
            {"account_id": bank, "debit": Decimal("0"), "credit": Decimal("80")},
        ],
    )
    db_session.commit()
    data = client.get(f"/api/reports/functional-expenses?{YEAR}").json()
    assert data["programs"] == [
        {"class_id": None, "class_name": "Uncategorized", "amount": 80.0}
    ]
    assert db_session.query(TxnClass).count() == 0


def test_the_functional_allocation_saves_the_default_class_its_lines_carry(
    client, db_session, db_engine, seed_accounts
):
    """The preview is a read; running the allocation is the write, and it
    makes the default class the untagged pool's lines are tagged to."""
    bank, expense = seed_accounts["1000"].id, seed_accounts["6000"].id
    create_journal_entry(
        db_session,
        date(2026, 7, 5),
        "July rent",
        [
            {
                "account_id": expense,
                "debit": Decimal("1000"),
                "credit": Decimal("0"),
                "function": None,
            },
            {"account_id": bank, "debit": Decimal("0"), "credit": Decimal("1000")},
        ],
    )
    db_session.commit()
    rule = client.post(
        "/api/nonprofit/allocation-rules",
        json={
            "name": "Rent split",
            "basis": "percent",
            "source_account_id": expense,
            "targets": [
                {"function": "program", "weight": 60},
                {"function": "management", "weight": 40},
            ],
        },
    ).json()
    period = "start_date=2026-07-01&end_date=2026-07-31"
    preview = _read(
        db_session,
        db_engine,
        client,
        f"/api/nonprofit/allocation-rules/{rule['id']}/preview?{period}",
    ).json()
    assert Decimal(str(preview["total"])) == Decimal("1000")
    assert db_session.query(TxnClass).count() == 0
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
    default = db_session.query(TxnClass).filter(TxnClass.is_system_default).one()
    assert {ln["class_id"] for ln in r.json()["lines"]} == {default.id}


# ── cost types ─────────────────────────────────────────────────────────────


def test_cost_type_reads_seed_nothing(client, db_session, db_engine, seed_accounts):
    customer = client.post("/api/customers", json={"name": "Builder Co"}).json()
    job = client.post(
        "/api/jobs",
        json={
            "customer_id": customer["id"],
            "name": "Kitchen",
            "contract_amount": "10000",
        },
    )
    assert job.status_code == 201, job.text
    db_session.query(CostType).delete()
    db_session.commit()
    jid = job.json()["id"]
    api = _reader(client)
    _reads_change_nothing(
        db_session,
        db_engine,
        api,
        [
            "/api/cost-types",
            "/api/cost-types?include_inactive=true",
            f"/api/jobs/{jid}",
            f"/api/jobs/{jid}/cost-tree",
            f"/api/jobs/{jid}/contract",
            f"/api/jobs/{jid}/cost-to-complete-forecast",
            "/api/reports/wip-schedule",
            "/api/reports/cost-to-complete-forecast",
            "/api/reports/cost-to-complete",
        ],
    )
    assert api.get("/api/cost-types").json() == []
    assert db_session.query(CostType).count() == 0


def test_a_read_takes_the_starting_cost_types_unsaved(db_session):
    from app.services.job_costing import read_cost_type_map

    types = read_cost_type_map(db_session)
    assert list(types) == ["labor", "material", "subcontract", "equipment", "other"]
    assert types["labor"].is_labor and not types["material"].is_labor
    assert all(t.id is None for t in types.values())
    assert not db_session.new and db_session.query(CostType).count() == 0


def test_the_first_cost_type_write_seeds_the_starting_five(client, seed_accounts):
    r = client.post("/api/cost-types", json={"code": "permits", "name": "Permits"})
    assert r.status_code == 201, r.text
    codes = {t["code"] for t in client.get("/api/cost-types").json()}
    assert codes == {
        "labor",
        "material",
        "subcontract",
        "equipment",
        "other",
        "permits",
    }


# ── the default fixed asset type ───────────────────────────────────────────


def test_the_asset_type_list_makes_no_default(
    client, db_session, db_engine, seed_accounts
):
    api = _reader(client)
    _reads_change_nothing(
        db_session,
        db_engine,
        api,
        ["/api/fixed-assets/types", "/api/fixed-assets/types?include_inactive=true"],
    )
    assert api.get("/api/fixed-assets/types").json() == []


def _alembic(db_path):
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db_path.as_posix()
    return cfg


def _books_before_the_type(tmp_path, name, accounts, types=()):
    """A company file at the revision before 407d52f22d78, with these
    accounts (name, number, type) and asset types (name)."""
    from alembic import command
    from sqlalchemy import create_engine

    db = tmp_path / f"{name}.db"
    cfg = _alembic(db)
    command.upgrade(cfg, "d7b3f9a1c5e2")
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        for acct_name, number, kind in accounts:
            conn.execute(
                text(
                    "INSERT INTO accounts (name, account_number, account_type, "
                    "is_active, is_system, balance) VALUES (:n, :num, :t, 1, 1, 0)"
                ),
                {"n": acct_name, "num": number, "t": kind},
            )
        for type_name in types:
            conn.execute(
                text("INSERT INTO fixed_asset_types (name) VALUES (:n)"),
                {"n": type_name},
            )
    engine.dispose()
    return db, cfg


def _after_upgrade(db):
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        types = [
            dict(r._mapping)
            for r in conn.execute(
                text(
                    "SELECT name, description, asset_account_id, "
                    "accumulated_depreciation_account_id, "
                    "depreciation_expense_account_id, depreciation_method, "
                    "effective_life_years, is_active FROM fixed_asset_types"
                )
            )
        ]
        accounts = {
            r.name: dict(r._mapping)
            for r in conn.execute(
                text(
                    "SELECT id, name, account_number, account_type, is_system, "
                    "detail_type FROM accounts"
                )
            )
        }
    engine.dispose()
    return types, accounts


CHART = [
    ("Equipment", "1500", "ASSET"),
    ("Accumulated Depreciation", "1510", "ASSET"),
]


def test_books_from_before_2_9_get_the_default_asset_type_at_the_upgrade(tmp_path):
    from alembic import command
    from alembic.script import ScriptDirectory

    db, cfg = _books_before_the_type(tmp_path, "old", CHART)
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("407d52f22d78").down_revision == "d7b3f9a1c5e2"
    assert script.get_heads() == [
        "a3d8f1c6b9e4"
    ]  # the job's sales tax treatment and use tax are the head above it
    command.upgrade(cfg, "407d52f22d78")
    types, accounts = _after_upgrade(db)
    expense = accounts["Depreciation Expense"]
    assert (expense["account_number"], expense["account_type"]) == ("6810", "EXPENSE")
    assert expense["is_system"] and expense["detail_type"] == "expense"
    assert types == [
        {
            "name": "Equipment",
            "description": "General equipment, straight-line over five years",
            "asset_account_id": accounts["Equipment"]["id"],
            "accumulated_depreciation_account_id": accounts["Accumulated Depreciation"][
                "id"
            ],
            "depreciation_expense_account_id": expense["id"],
            "depreciation_method": "STRAIGHT_LINE",
            "effective_life_years": 5,
            "is_active": 1,
        }
    ]
    # downgrade leaves the rows (assets may use the type); up again adds none
    command.downgrade(cfg, "d7b3f9a1c5e2")
    command.upgrade(cfg, "407d52f22d78")
    assert len(_after_upgrade(db)[0]) == 1


def test_the_upgrade_maps_an_existing_expense_account_and_yields_a_taken_number(
    tmp_path,
):
    from alembic import command

    db, cfg = _books_before_the_type(
        tmp_path, "mapped", CHART + [("Depreciation Expense", "7100", "EXPENSE")]
    )
    command.upgrade(cfg, "407d52f22d78")
    types, accounts = _after_upgrade(db)
    assert types[0]["depreciation_expense_account_id"] == (
        accounts["Depreciation Expense"]["id"]
    )
    assert len(accounts) == 3

    db, cfg = _books_before_the_type(
        tmp_path, "taken", CHART + [("Vehicle Expense", "6810", "EXPENSE")]
    )
    command.upgrade(cfg, "407d52f22d78")
    types, accounts = _after_upgrade(db)
    assert accounts["Depreciation Expense"]["account_number"] is None
    assert types[0]["depreciation_expense_account_id"] == (
        accounts["Depreciation Expense"]["id"]
    )


def test_the_upgrade_leaves_books_with_a_type_or_without_the_accounts(tmp_path):
    from alembic import command

    db, cfg = _books_before_the_type(tmp_path, "typed", CHART, types=["Vehicles"])
    command.upgrade(cfg, "407d52f22d78")
    types, accounts = _after_upgrade(db)
    assert [t["name"] for t in types] == ["Vehicles"]
    assert "Depreciation Expense" not in accounts

    db, cfg = _books_before_the_type(
        tmp_path, "imported", [("Office Machines", "1600", "ASSET")]
    )
    command.upgrade(cfg, "407d52f22d78")
    types, accounts = _after_upgrade(db)
    assert types == [] and set(accounts) == {"Office Machines"}


def test_a_new_company_still_gets_one_default_type(tmp_path):
    """Setup seeds the chart after the migrations, so the upgrade finds no
    Equipment account and the setup's own seed is the one type."""
    from app.services.company_service import _init_company_db

    db = tmp_path / "fresh.db"
    _init_company_db("sqlite:///" + db.as_posix(), company_name="Fresh Books")
    types, accounts = _after_upgrade(db)
    assert [t["name"] for t in types] == ["Equipment"]
    assert accounts["Depreciation Expense"]["account_number"] == "6810"


# ── the onboarding checklist ───────────────────────────────────────────────


def _employee(client, db_session, keep_checklist=False):
    r = client.post(
        "/api/employees",
        json={
            "first_name": "Pat",
            "last_name": "Worker",
            "pay_type": "salary",
            "pay_rate": 52000,
            "pay_frequency": "biweekly",
            "filing_status": "single",
            "work_state": "WA",
        },
    )
    assert r.status_code == 201, r.text
    emp = r.json()
    if not keep_checklist:
        # an employee from before the checklist, or brought in by an import
        db_session.query(OnboardingTask).delete()
        db_session.commit()
    return emp


def test_the_checklist_read_answers_the_default_unsaved(client, db_session, db_engine):
    emp = _employee(client, db_session)
    cl = _read(db_session, db_engine, client, f"/api/onboarding/{emp['id']}").json()
    assert (cl["complete"], cl["total"], cl["percent_complete"]) == (0, 8, 0.0)
    assert [t["task_type"] for t in cl["tasks"]][:2] == ["w4", "i9_section1"]
    assert {(t["id"], t["status"], t["signed"]) for t in cl["tasks"]} == {
        (None, "pending", False)
    }
    # HR is an administrator's: a read-only token is not let in at all
    assert _reader(client).get(f"/api/onboarding/{emp['id']}").status_code == 403

    # the checklist's own POST saves it, and the read then has the ids
    seeded = client.post(f"/api/onboarding/{emp['id']}/seed").json()
    assert all(t["id"] for t in seeded["tasks"]) and seeded["total"] == 8
    again = client.get(f"/api/onboarding/{emp['id']}").json()
    assert [t["id"] for t in again["tasks"]] == [t["id"] for t in seeded["tasks"]]


def test_a_new_hire_still_gets_the_checklist_saved(client, db_session):
    emp = _employee(client, db_session, keep_checklist=True)
    assert (
        db_session.query(OnboardingTask).filter_by(employee_id=emp["id"]).count() == 8
    )


# ── the pay stub ───────────────────────────────────────────────────────────


def test_the_pay_stub_writes_nothing(
    client, db_session, db_engine, seed_accounts, monkeypatch
):
    # the stub's figures and template, without WeasyPrint (absent on the
    # Windows runner, where a render would be a skip)
    monkeypatch.setattr(
        "app.services.paystub_pdf.render_pdf", lambda html: b"%PDF-1.7 stub"
    )
    emp = _employee(client, db_session, keep_checklist=True)
    run = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-04-01",
            "period_end": "2026-04-15",
            "pay_date": "2026-04-20",
            "stubs": [{"employee_id": emp["id"]}],
        },
    )
    assert run.status_code == 201, run.text
    run = run.json()
    assert client.post(f"/api/payroll/{run['id']}/process").status_code == 200
    url = f"/api/payroll/{run['id']}/paystub/{run['stubs'][0]['id']}"
    r = _read(db_session, db_engine, client, url)
    assert r.headers["content-type"] == "application/pdf"
    assert _reader(client).get(url).status_code == 403
