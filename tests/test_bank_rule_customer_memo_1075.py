"""A bank rule names a customer (for money in) and carries a memo (#1075).

A rule set a category, a vendor, a class and a job, so a regular client's
deposit matched by a rule came in under nobody, and the To review memo
(#1049) was typed on every line a rule matched. A rule now takes a customer
beside its vendor and a memo: the To review row shows them where nothing
was picked or typed, Add, Add all and Add selected post them (the customer
as the entry's payee and on its income line, the memo as the entry's memo),
a pick or a typed memo on the line wins, and Rule… from a line carries the
line's customer and memo into the new rule."""

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.banking import BankTransaction
from app.models.contacts import Customer
from app.models.transactions import Transaction, TransactionLine
from app.services.ofx_import import import_transactions
from tests.test_api_defect_regressions import _bearer, _mint

ROOT = Path(__file__).resolve().parent.parent
BANKING_JS = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
RULES_JS = (ROOT / "app/static/js/bank_rules.js").read_text(encoding="utf-8")
REVISION = "a1075c4d8e2f"
BELOW = "f1049c2a7e5b"
OCT = {"start_date": "2026-10-01", "end_date": "2026-10-31"}


def _ok(r, code=(200, 201)):
    assert r.status_code in code, r.text
    return r.json()


def _customer(db_session, name, active=True):
    c = Customer(name=name, is_active=active)
    db_session.add(c)
    db_session.commit()
    return c.id


@pytest.fixture
def setup(client, db_session, seed_accounts):
    feed = _ok(
        client.post(
            "/api/banking/accounts",
            json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
        )
    )
    cid = _customer(db_session, "Corner Client")
    vendor = _ok(client.post("/api/vendors", json={"name": "Corner Supply"}))
    import_transactions(
        db_session,
        feed["id"],
        [
            {
                "fitid": f"C{i}",
                "date": date(2026, 10, 3 + i),
                "amount": Decimal(a),
                "payee": p,
                "memo": "",
            }
            for i, (a, p) in enumerate(
                [
                    ("250.00", "CORNER DEP 1"),
                    ("120.00", "CORNER DEP 2"),
                    ("-30.00", "CORNER DEP 3"),
                    ("75.00", "OTHER DEP"),
                ]
            )
        ],
    )
    db_session.commit()
    rule = _ok(
        client.post(
            "/api/bank-rules",
            json={
                "name": "Corner",
                "pattern": "CORNER DEP*",
                "account_id": seed_accounts["4000"].id,
                "customer_id": cid,
                "vendor_id": vendor["id"],
                "memo": "  Monthly service  ",
            },
        )
    )
    _ok(client.post("/api/bank-rules/apply"))
    return feed, cid, vendor, rule


def _review(client, feed):
    got = _ok(
        client.get(
            f"/api/banking/transactions?bank_account_id={feed['id']}&status=unmatched"
        )
    )
    return {r["payee"]: r for r in got}


def _entry(db_session, line_id):
    db_session.expire_all()
    bt = db_session.get(BankTransaction, line_id)
    txn = db_session.get(Transaction, bt.transaction_id)
    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
    return bt, txn, lines


def _income_rows(client):
    body = _ok(client.get("/api/reports/income-by-customer", params=OCT))
    return {i["customer_id"]: i for i in body["items"]}


# --- the rule's API -------------------------------------------------------


def test_a_rule_keeps_its_customer_and_memo_and_lists_them(client, setup):
    _, cid, _, rule = setup
    assert rule["customer_id"] == cid
    assert rule["customer_name"] == "Corner Client"
    assert rule["memo"] == "Monthly service"  # trimmed
    listed = {r["id"]: r for r in _ok(client.get("/api/bank-rules"))}
    assert listed[rule["id"]]["customer_name"] == "Corner Client"
    edited = _ok(
        client.put(f"/api/bank-rules/{rule['id']}", json={"memo": "", "priority": 2})
    )
    assert edited["memo"] is None  # a blank memo is no memo
    assert edited["customer_id"] == cid
    off = _ok(client.put(f"/api/bank-rules/{rule['id']}", json={"customer_id": None}))
    assert off["customer_id"] is None and off["customer_name"] is None


def test_a_rule_without_them_is_as_before(client, seed_accounts):
    made = _ok(
        client.post(
            "/api/bank-rules",
            json={"name": "Plain", "pattern": "PLAIN", "account_id": None},
        )
    )
    assert made["customer_id"] is None and made["memo"] is None


def test_an_unknown_or_inactive_customer_is_refused(client, db_session, setup):
    _, cid, _, rule = setup
    r = client.post(
        "/api/bank-rules",
        json={"name": "Ghost", "pattern": "GHOST", "customer_id": 99999},
    )
    assert r.status_code == 404
    assert "customer was not found" in r.json()["detail"]
    idle = _customer(db_session, "Idle Client", active=False)
    r = client.post(
        "/api/bank-rules",
        json={"name": "Idle", "pattern": "IDLE", "customer_id": idle},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "Idle Client is inactive; pick another customer."
    # the rule's own customer, made inactive since, is kept on an edit
    db_session.get(Customer, cid).is_active = False
    db_session.commit()
    kept = _ok(
        client.put(
            f"/api/bank-rules/{rule['id']}",
            json={"customer_id": cid, "name": "Corner renamed"},
        )
    )
    assert kept["customer_id"] == cid


def test_a_memo_longer_than_the_entry_memo_is_refused(client, setup):
    _, _, _, rule = setup
    r = client.post(
        "/api/bank-rules",
        json={"name": "Long", "pattern": "LONG", "memo": "x" * 501},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == (
        "A rule's memo can be at most 500 characters; this one has 501."
    )
    r = client.put(f"/api/bank-rules/{rule['id']}", json={"memo": "y" * 600})
    assert r.status_code == 422
    assert _ok(client.get(f"/api/bank-rules/{rule['id']}"))["memo"] == (
        "Monthly service"
    )
    ok = _ok(client.put(f"/api/bank-rules/{rule['id']}", json={"memo": "z" * 500}))
    assert ok["memo"] == "z" * 500


def test_a_job_of_another_customer_is_refused(client, db_session, setup):
    _, cid, _, rule = setup
    other = _customer(db_session, "Second Client")
    job = _ok(client.post("/api/jobs", json={"customer_id": other, "name": "Roof"}))
    r = client.put(f"/api/bank-rules/{rule['id']}", json={"job_id": job["id"]})
    assert r.status_code == 400
    assert "belongs to another customer than this rule's" in r.json()["detail"]
    own = _ok(client.post("/api/jobs", json={"customer_id": cid, "name": "Porch"}))
    assert _ok(client.put(f"/api/bank-rules/{rule['id']}", json={"job_id": own["id"]}))[
        "job_id"
    ] == (own["id"])


def test_a_read_only_sign_in_cannot_write_a_rules_customer_or_memo(
    client, db_session, setup
):
    _, _, _, rule = setup
    reader = _bearer(_mint(client, label="rules-1075", role="readonly"))
    assert reader.get("/api/bank-rules").status_code == 200
    r = reader.put(f"/api/bank-rules/{rule['id']}", json={"memo": "no"})
    assert r.status_code == 403
    r = reader.post(
        "/api/bank-rules", json={"name": "No", "pattern": "NO", "memo": "no"}
    )
    assert r.status_code == 403
    assert _ok(client.get(f"/api/bank-rules/{rule['id']}"))["memo"] == (
        "Monthly service"
    )


# --- the To review row and the posted entry --------------------------------


def test_the_row_shows_the_rules_customer_and_memo_on_money_in(client, setup):
    feed, cid, vendor, _ = setup
    rows = _review(client, feed)
    dep = rows["CORNER DEP 1"]
    assert dep["rule_customer_id"] == cid
    assert dep["rule_customer_name"] == "Corner Client"
    assert dep["rule_memo"] == "Monthly service"
    # on money in the customer is the rule's name, not its vendor
    assert dep["rule_vendor_id"] is None
    out = rows["CORNER DEP 3"]
    assert out["rule_customer_id"] is None  # money out takes the vendor
    assert out["rule_vendor_id"] == vendor["id"]
    assert out["rule_memo"] == "Monthly service"
    assert rows["OTHER DEP"]["rule_customer_id"] is None


def test_add_posts_the_rules_customer_and_memo(client, db_session, setup):
    feed, cid, vendor, _ = setup
    dep = _review(client, feed)["CORNER DEP 1"]
    _ok(client.post(f"/api/banking/transactions/{dep['id']}/add", json={}))
    _, txn, lines = _entry(db_session, dep["id"])
    assert txn.description == "Corner Client"
    assert txn.vendor_id is None
    income = [ln for ln in lines if ln.credit > 0]
    assert [ln.customer_id for ln in income] == [cid]
    assert {ln.description for ln in lines} == {"Monthly service"}
    detail = _ok(client.get(f"/api/banking/entries/{txn.id}"))
    assert detail["description"] == "Monthly service"
    # the deposit is that customer's in Income by Customer (#1065)
    rows = _income_rows(client)
    assert rows[cid]["total_sales"] == 250.00


def test_money_out_takes_the_rules_vendor_and_memo_not_its_customer(
    client, db_session, setup
):
    feed, _, vendor, _ = setup
    out = _review(client, feed)["CORNER DEP 3"]
    _ok(client.post(f"/api/banking/transactions/{out['id']}/add", json={}))
    _, txn, lines = _entry(db_session, out["id"])
    assert txn.vendor_id == vendor["id"] and txn.description == "Corner Supply"
    assert all(ln.customer_id is None for ln in lines)
    assert {ln.description for ln in lines} == {"Monthly service"}


def test_a_pick_or_a_typed_memo_on_the_line_wins(client, db_session, setup):
    feed, cid, _, _ = setup
    other = _customer(db_session, "Second Client")
    rows = _review(client, feed)
    one, two = rows["CORNER DEP 1"], rows["CORNER DEP 2"]
    # kept on the line (#904, #1049)
    _ok(
        client.patch(
            f"/api/banking/transactions/{one['id']}",
            json={"customer_id": other, "memo": "Kept note"},
        )
    )
    _ok(client.post(f"/api/banking/transactions/{one['id']}/add", json={}))
    _, txn, lines = _entry(db_session, one["id"])
    assert txn.description == "Second Client"
    assert [ln.customer_id for ln in lines if ln.credit > 0] == [other]
    assert {ln.description for ln in lines} == {"Kept note"}
    # sent with Add
    _ok(
        client.post(
            f"/api/banking/transactions/{two['id']}/add",
            json={"customer_id": other, "memo": "Sent note"},
        )
    )
    _, txn, lines = _entry(db_session, two["id"])
    assert txn.description == "Second Client"
    assert {ln.description for ln in lines} == {"Sent note"}
    rows = _income_rows(client)
    assert rows[other]["total_sales"] == 370.00
    assert cid not in rows


def test_add_all_and_add_selected_post_them(client, db_session, setup):
    feed, cid, _, _ = setup
    rows = _review(client, feed)
    one, two = rows["CORNER DEP 1"], rows["CORNER DEP 2"]
    got = _ok(
        client.post(
            f"/api/banking/accounts/{feed['id']}/feed/add-all",
            json={"line_ids": [one["id"]]},
        )
    )
    assert got["added"] == 1
    got = _ok(client.post(f"/api/banking/accounts/{feed['id']}/feed/add-all", json={}))
    assert got["added"] >= 1
    for line in (one, two):
        _, txn, lines = _entry(db_session, line["id"])
        assert txn.description == "Corner Client"
        assert [ln.customer_id for ln in lines if ln.credit > 0] == [cid]
        assert {ln.description for ln in lines} == {"Monthly service"}
    assert _income_rows(client)[cid]["total_sales"] == 370.00


def test_an_inactive_rule_customer_is_passed_over(client, db_session, setup):
    feed, cid, _, _ = setup
    db_session.get(Customer, cid).is_active = False
    db_session.commit()
    dep = _review(client, feed)["CORNER DEP 1"]
    assert dep["rule_customer_id"] is None
    _ok(client.post(f"/api/banking/transactions/{dep['id']}/add", json={}))
    _, _, lines = _entry(db_session, dep["id"])
    assert all(ln.customer_id is None for ln in lines)


# --- the pages --------------------------------------------------------------


def test_the_rule_form_has_a_customer_picker_and_a_memo_box():
    assert "API.get('/customers?tree=1')" in RULES_JS
    assert '<label for="bank-rule-customer">Customer</label>' in RULES_JS
    assert 'id="bank-rule-customer" name="customer_id"' in RULES_JS
    assert '<label for="bank-rule-memo">Memo</label>' in RULES_JS
    assert 'id="bank-rule-memo" name="memo" maxlength="500"' in RULES_JS
    assert "c.is_active !== false || c.id == rule.customer_id" in RULES_JS
    assert (
        "data.customer_id = data.customer_id ? parseInt(data.customer_id) : null"
        in RULES_JS
    )
    assert "data.memo = (data.memo || '').trim() || null" in RULES_JS
    # the list names the customer once a rule has one
    assert "r.customer_name" in RULES_JS


def test_the_row_draws_the_rules_customer_and_memo_where_nothing_was_picked():
    cells = BANKING_JS[BANKING_JS.index("    _reviewDimCells(t) {") :]
    cells = cells[: cells.index("\n    },\n")]
    assert "or(t.pick_customer_id, t.rule_customer_id)" in cells
    assert "t.pick_memo || t.rule_memo || ''" in cells


def test_rule_from_a_line_carries_its_customer_and_memo():
    fn = BANKING_JS[BANKING_JS.index("async ruleFromLine(lineId, accountId) {") :]
    fn = fn[: fn.index("\n    },")]
    assert "customer_id: picks.customer_id || ''," in fn
    assert "memo: picks.memo || ''," in fn


# --- the migration ------------------------------------------------------------


def test_the_migration_adds_the_columns_and_walks_back(tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    db = tmp_path / "rules.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision(REVISION).down_revision == BELOW
    assert script.get_heads() == [REVISION]
    command.upgrade(cfg, BELOW)  # the step below it
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO bank_rules (id, name, pattern, rule_type, priority,"
            " is_active) VALUES (1, 'Old', 'OLD', 'contains', 0, 1)"
        )
    command.upgrade(cfg, REVISION)  # its own step (#770)
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bank_rules)")}
        assert {"customer_id", "memo"} <= cols
        fks = {r[2] for r in conn.execute("PRAGMA foreign_key_list(bank_rules)")}
        assert "customers" in fks
        row = conn.execute("SELECT name, customer_id, memo FROM bank_rules").fetchone()
        assert row == ("Old", None, None)  # an existing rule is unchanged
    command.downgrade(cfg, BELOW)
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bank_rules)")}
        assert "customer_id" not in cols and "memo" not in cols
        assert conn.execute("SELECT count(*) FROM bank_rules").fetchone() == (1,)
