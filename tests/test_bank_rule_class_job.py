"""A bank rule names a class and a job (#675): the rule keeps them, the
Bank Rules page names them, and a statement line the rule matches is added
classed and on its job, by one Add or by Add all categorised. A class or
job the caller names wins; an archived class or inactive job is refused on
the rule and left off the line. The migration adds the two columns and
walks back."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.banking import BankTransaction
from app.models.transactions import Transaction, TransactionLine
from app.services.ofx_import import import_transactions

ROOT = Path(__file__).resolve().parent.parent
REVISION = "95209e8d293c"


def _ok(r, code=(200, 201)):
    assert r.status_code in code, r.text
    return r.json()


@pytest.fixture
def feed(client, seed_accounts):
    return _ok(
        client.post(
            "/api/banking/accounts",
            json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
        )
    )


@pytest.fixture
def dims(client, seed_customer):
    shop = _ok(client.post("/api/classes", json={"name": "Shop"}))
    field = _ok(client.post("/api/classes", json={"name": "Field"}))
    kitchen = _ok(
        client.post(
            "/api/jobs", json={"customer_id": seed_customer.id, "name": "Kitchen"}
        )
    )
    return {"shop": shop, "field": field, "kitchen": kitchen}


def _import(db, feed, *rows):
    import_transactions(
        db,
        feed["id"],
        [
            {
                "fitid": f"CJ{i}-{p}",
                "date": date(2026, 9, d),
                "amount": Decimal(a),
                "payee": p,
                "memo": "",
            }
            for i, (d, a, p) in enumerate(rows)
        ],
    )
    return {bt.payee: bt for bt in db.query(BankTransaction).all()}


def _rule(client, seed_accounts, dims, **extra):
    return _ok(
        client.post(
            "/api/bank-rules",
            json={
                "name": "Lumber",
                "pattern": "LUMBER",
                "account_id": seed_accounts["6000"].id,
                "class_id": dims["shop"]["id"],
                "job_id": dims["kitchen"]["id"],
                **extra,
            },
        )
    )


def _category_line(db, bt):
    db.expire_all()
    bt = db.get(BankTransaction, bt.id)
    feed_line = db.get(TransactionLine, bt.transaction_line_id)
    txn = db.get(Transaction, feed_line.transaction_id)
    return next(ln for ln in txn.lines if ln.id != feed_line.id), txn


def test_the_rule_keeps_its_class_and_job(client, db_session, seed_accounts, dims):
    rule = _rule(client, seed_accounts, dims)
    assert rule["class_id"] == dims["shop"]["id"]
    assert rule["job_id"] == dims["kitchen"]["id"]
    assert rule["class_name"] == "Shop"
    assert rule["job_name"].endswith("Kitchen")
    [listed] = _ok(client.get("/api/bank-rules"))
    assert listed["class_name"] == "Shop" and listed["job_name"].endswith("Kitchen")
    # changed, then taken off
    changed = _ok(
        client.put(
            f"/api/bank-rules/{rule['id']}", json={"class_id": dims["field"]["id"]}
        )
    )
    assert changed["class_name"] == "Field"
    assert changed["job_id"] == dims["kitchen"]["id"]
    cleared = _ok(
        client.put(
            f"/api/bank-rules/{rule['id']}", json={"class_id": None, "job_id": None}
        )
    )
    assert cleared["class_id"] is None and cleared["job_id"] is None
    assert cleared["class_name"] is None and cleared["job_name"] is None


def test_an_unknown_archived_or_inactive_target_is_refused(
    client, seed_accounts, dims, seed_customer
):
    base = {"name": "R", "pattern": "X"}
    assert (
        client.post("/api/bank-rules", json={**base, "class_id": 99999}).status_code
        == 404
    )
    assert (
        client.post("/api/bank-rules", json={**base, "job_id": 99999}).status_code
        == 404
    )
    _ok(client.put(f"/api/classes/{dims['field']['id']}", json={"is_archived": True}))
    r = client.post("/api/bank-rules", json={**base, "class_id": dims["field"]["id"]})
    assert r.status_code == 400 and "archived" in r.json()["detail"]
    porch = _ok(
        client.post(
            "/api/jobs", json={"customer_id": seed_customer.id, "name": "Porch"}
        )
    )
    _ok(client.put(f"/api/jobs/{porch['id']}", json={"is_active": False}))
    r = client.post("/api/bank-rules", json={**base, "job_id": porch["id"]})
    assert r.status_code == 400 and "inactive" in r.json()["detail"]


def test_a_matched_line_comes_in_classed_and_on_its_job(
    client, db_session, seed_accounts, feed, dims
):
    _rule(client, seed_accounts, dims)
    lines = _import(db_session, feed, (3, "-80.00", "MENARDS LUMBER 33"))
    bt = lines["MENARDS LUMBER 33"]
    db_session.refresh(bt)
    # the import categorised it by the rule
    assert bt.category_account_id == seed_accounts["6000"].id
    _ok(client.post(f"/api/banking/transactions/{bt.id}/add", json={}))
    line, _txn = _category_line(db_session, bt)
    assert line.account_id == seed_accounts["6000"].id
    assert line.class_id == dims["shop"]["id"]
    assert line.job_id == dims["kitchen"]["id"]
    # and the job's figures carry it
    rows = {r["job_id"]: r for r in _ok(client.get("/api/jobs/profitability"))}
    assert rows[dims["kitchen"]["id"]]["expenses"] == 80.0


def test_add_all_categorised_applies_them_too(
    client, db_session, seed_accounts, feed, dims
):
    _rule(client, seed_accounts, dims)
    lines = _import(
        db_session,
        feed,
        (4, "-20.00", "LUMBER YARD"),
        (5, "-30.00", "BIG LUMBER CO"),
    )
    out = _ok(client.post(f"/api/banking/accounts/{feed['id']}/feed/add-all"))
    assert out["added"] == 2
    for bt in lines.values():
        line, _ = _category_line(db_session, bt)
        assert (line.class_id, line.job_id) == (
            dims["shop"]["id"],
            dims["kitchen"]["id"],
        )


def test_the_callers_class_wins_and_an_archived_one_is_left_off(
    client, db_session, seed_accounts, feed, dims
):
    _rule(client, seed_accounts, dims)
    lines = _import(
        db_session, feed, (6, "-10.00", "LUMBER ONE"), (7, "-12.00", "LUMBER TWO")
    )
    one = lines["LUMBER ONE"]
    _ok(
        client.post(
            f"/api/banking/transactions/{one.id}/add",
            json={"class_id": dims["field"]["id"]},
        )
    )
    line, _ = _category_line(db_session, one)
    assert line.class_id == dims["field"]["id"]
    assert line.job_id == dims["kitchen"]["id"]
    # the rule's class archived since: the line comes in without it
    _ok(client.put(f"/api/classes/{dims['shop']['id']}", json={"is_archived": True}))
    two = lines["LUMBER TWO"]
    _ok(client.post(f"/api/banking/transactions/{two.id}/add", json={}))
    line, _ = _category_line(db_session, two)
    assert line.job_id == dims["kitchen"]["id"]
    assert line.class_id != dims["shop"]["id"]


def test_a_rule_without_them_adds_as_before(client, db_session, seed_accounts, feed):
    _ok(
        client.post(
            "/api/bank-rules",
            json={
                "name": "Gas",
                "pattern": "SHELL",
                "account_id": seed_accounts["6000"].id,
            },
        )
    )
    lines = _import(db_session, feed, (8, "-40.00", "SHELL OIL"))
    bt = lines["SHELL OIL"]
    _ok(client.post(f"/api/banking/transactions/{bt.id}/add", json={}))
    line, _ = _category_line(db_session, bt)
    assert line.job_id is None


def test_the_rule_form_offers_class_and_job():
    src = (ROOT / "app/static/js/bank_rules.js").read_text(encoding="utf-8")
    assert "API.get('/classes?include_archived=true')" in src
    assert "API.get('/jobs?include_inactive=true')" in src
    assert 'id="bank-rule-class" name="class_id"' in src
    assert 'id="bank-rule-job" name="job_id"' in src
    # shown only once the company has classes, or jobs, under the vendor
    assert "const classGroup = classOpts ?" in src
    assert "const jobGroup = jobOpts ?" in src
    assert src.index("${classGroup}") > src.index('id="bank-rule-vendor"')
    assert src.index("${jobGroup}") > src.index("${classGroup}")
    # sent as ids, or null to take them off
    assert "data.class_id = data.class_id ? parseInt(data.class_id) : null" in src
    assert "data.job_id = data.job_id ? parseInt(data.job_id) : null" in src
    # the list names them once a rule has one
    assert "r.class_name" in src and "r.job_name" in src


def _cfg(db):
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + Path(db).as_posix()
    return cfg


def test_the_migration_adds_the_columns_and_walks_back(tmp_path):
    from alembic import command
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect

    db = tmp_path / "rules.db"
    cfg = _cfg(db)
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == [
        "b8640c3e4a5b"
    ]  # the documents' Tax exempt switch (#864) is the head above it
    assert script.get_revision(REVISION).down_revision == "4810bd59e481"
    command.upgrade(cfg, REVISION)  # its own step, not the chain (#770)
    engine = create_engine(f"sqlite:///{db}")
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("bank_rules")}
    assert {"class_id", "job_id"} <= cols
    referred = {f["referred_table"] for f in insp.get_foreign_keys("bank_rules")}
    assert {"classes", "jobs"} <= referred
    engine.dispose()
    command.downgrade(cfg, "4810bd59e481")
    engine = create_engine(f"sqlite:///{db}")
    cols = {c["name"] for c in inspect(engine).get_columns("bank_rules")}
    assert "class_id" not in cols and "job_id" not in cols
    engine.dispose()
    # and up again; an upgrade over books that already have the columns
    # (stamped back, as create_all books are) is a no-op, not an error
    command.upgrade(cfg, REVISION)  # its own step, not the chain (#770)
    command.stamp(cfg, "4810bd59e481")
    command.upgrade(cfg, REVISION)  # its own step, not the chain (#770)
    engine = create_engine(f"sqlite:///{db}")
    assert {"class_id", "job_id"} <= {
        c["name"] for c in inspect(engine).get_columns("bank_rules")
    }
    engine.dispose()
