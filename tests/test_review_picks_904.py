"""The To review row's Class, Job and Name are kept on the statement line
as they are picked (#904), the way its category is (W-M12).

A user filled in several review lines (a category and a name on each, a
class or a job on some) and pressed Add on one: the page drew the list
again and every other line lost its name, class and job. Only the category
had been saved. Now the PATCH that keeps the category keeps the rest too
(a field sent is saved, one left out is left as it was), the list returns
them, Add and Add all categorised post with them and clear them, and a
read-only or accountant sign-in cannot write them. Rule… carries the row's
vendor, class and job into the new rule; the migration adds the columns
and walks back."""

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.users import User
from app.models.banking import BankTransaction
from app.models.transactions import Transaction
from app.services import auth as auth_service
from app.services.ofx_import import import_transactions

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
REVISION = "e904b7c3a1d5"
USER_PW = "Picks-904-pass!"


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
    kitchen = _ok(
        client.post(
            "/api/jobs", json={"customer_id": seed_customer.id, "name": "Kitchen"}
        )
    )
    acme = _ok(client.post("/api/vendors", json={"name": "Acme Lumber"}))
    return {"shop": shop, "kitchen": kitchen, "acme": acme}


@pytest.fixture
def lines(client, db_session, feed):
    import_transactions(
        db_session,
        feed["id"],
        [
            {
                "fitid": f"P{i}",
                "date": date(2026, 9, 3 + i),
                "amount": Decimal(a),
                "payee": p,
                "memo": "",
            }
            for i, (a, p) in enumerate(
                [("-120.00", "ACME #22"), ("-45.00", "HOME DEPOT"), ("300.00", "DEP")]
            )
        ],
    )
    db_session.commit()
    return {r["payee"]: r for r in _review(client, feed)}


def _review(client, feed):
    return _ok(
        client.get(
            f"/api/banking/transactions?bank_account_id={feed['id']}&status=unmatched"
        )
    )


def _patch(client, line, **body):
    return client.patch(f"/api/banking/transactions/{line['id']}", json=body)


def test_picks_are_kept_on_the_line_and_a_reload_returns_them(
    client, seed_accounts, seed_customer, feed, dims, lines
):
    acme, depot, dep = lines["ACME #22"], lines["HOME DEPOT"], lines["DEP"]
    out = _ok(
        _patch(
            client,
            acme,
            class_id=dims["shop"]["id"],
            job_id=dims["kitchen"]["id"],
            vendor_id=dims["acme"]["id"],
        )
    )
    assert out["pick_class_id"] == dims["shop"]["id"]
    assert out["pick_job_id"] == dims["kitchen"]["id"]
    assert out["pick_vendor_id"] == dims["acme"]["id"]
    assert out["pick_vendor_name"] == "Acme Lumber"
    _ok(_patch(client, depot, payee="  Depot Cash Sale  "))
    _ok(_patch(client, dep, customer_id=seed_customer.id))
    # a category pick and a name pick never undo each other
    _ok(_patch(client, acme, category_account_id=seed_accounts["6000"].id))

    again = {r["payee"]: r for r in _review(client, feed)}
    a = again["ACME #22"]
    assert a["category_account_id"] == seed_accounts["6000"].id
    assert (a["pick_class_id"], a["pick_job_id"], a["pick_vendor_id"]) == (
        dims["shop"]["id"],
        dims["kitchen"]["id"],
        dims["acme"]["id"],
    )
    assert again["HOME DEPOT"]["pick_payee"] == "Depot Cash Sale"
    assert again["DEP"]["pick_customer_id"] == seed_customer.id

    # sent as null, a pick is cleared; one left out stays
    _ok(_patch(client, acme, class_id=None))
    a = {r["payee"]: r for r in _review(client, feed)}["ACME #22"]
    assert a["pick_class_id"] is None and a["pick_job_id"] == dims["kitchen"]["id"]
    assert a["category_account_id"] == seed_accounts["6000"].id


def test_the_picks_are_checked_as_add_checks_them(
    client, db_session, seed_customer, dims, lines
):
    acme = lines["ACME #22"]
    assert _patch(client, acme, class_id=999999).status_code == 404
    assert _patch(client, acme, job_id=999999).status_code == 404
    assert _patch(client, acme, vendor_id=999999).status_code == 404
    assert _patch(client, acme, customer_id=999999).status_code == 404
    assert _patch(client, acme, payee="x" * 201).status_code == 422
    seed_customer.is_active = False
    db_session.commit()
    r = _patch(client, lines["DEP"], customer_id=seed_customer.id)
    assert r.status_code == 422 and "inactive" in r.json()["detail"]
    db_session.expire_all()
    bt = db_session.get(BankTransaction, acme["id"])
    assert (bt.pick_class_id, bt.pick_job_id, bt.pick_vendor_id) == (None, None, None)


def test_add_posts_with_the_kept_picks_and_clears_them(
    client, db_session, seed_accounts, dims, lines
):
    acme = lines["ACME #22"]
    _ok(
        _patch(
            client,
            acme,
            category_account_id=seed_accounts["6000"].id,
            class_id=dims["shop"]["id"],
            job_id=dims["kitchen"]["id"],
            vendor_id=dims["acme"]["id"],
        )
    )
    # Add with no body: what the line keeps is what posts
    added = _ok(client.post(f"/api/banking/transactions/{acme['id']}/add"))
    assert added["vendor_name"] == "Acme Lumber"
    assert added["pick_class_id"] is None and added["pick_vendor_id"] is None
    txn = db_session.get(Transaction, added["transaction_id"])
    assert txn.vendor_id == dims["acme"]["id"]
    entry = _ok(client.get(f"/api/journal/{added['transaction_id']}"))
    assert entry["class_id"] == dims["shop"]["id"]
    assert entry["job_id"] == dims["kitchen"]["id"]
    # in the books, the line's picks can't change
    r = _patch(client, acme, class_id=dims["shop"]["id"])
    assert r.status_code == 400 and "already in the books" in r.json()["detail"]


def test_what_add_sends_wins_over_the_kept_pick(client, seed_accounts, dims, lines):
    depot = lines["HOME DEPOT"]
    _ok(_patch(client, depot, payee="Depot Cash Sale"))
    added = _ok(
        client.post(
            f"/api/banking/transactions/{depot['id']}/add",
            json={
                "category_account_id": seed_accounts["6000"].id,
                "vendor_id": dims["acme"]["id"],
                "payee": "Acme Lumber",
            },
        )
    )
    assert added["vendor_name"] == "Acme Lumber"
    assert added["pick_payee"] is None


def test_add_all_posts_every_lines_kept_picks(
    client, db_session, seed_accounts, seed_customer, feed, dims, lines
):
    acme, depot, dep = lines["ACME #22"], lines["HOME DEPOT"], lines["DEP"]
    for line in (acme, depot):
        _ok(_patch(client, line, category_account_id=seed_accounts["6000"].id))
    _ok(_patch(client, dep, category_account_id=seed_accounts["4000"].id))
    _ok(
        _patch(client, acme, vendor_id=dims["acme"]["id"], job_id=dims["kitchen"]["id"])
    )
    _ok(_patch(client, depot, payee="Depot Cash Sale", class_id=dims["shop"]["id"]))
    _ok(_patch(client, dep, customer_id=seed_customer.id))
    out = _ok(client.post(f"/api/banking/accounts/{feed['id']}/feed/add-all"))
    assert out["added"] == 3 and out["skipped"] == []
    db_session.expire_all()
    rows = {bt.payee: bt for bt in db_session.query(BankTransaction).all()}
    acme_txn = db_session.get(Transaction, rows["ACME #22"].transaction_id)
    assert acme_txn.vendor_id == dims["acme"]["id"]
    assert _ok(client.get(f"/api/journal/{acme_txn.id}"))["job_id"] == (
        dims["kitchen"]["id"]
    )
    depot_txn = db_session.get(Transaction, rows["HOME DEPOT"].transaction_id)
    assert depot_txn.description == "Depot Cash Sale"
    assert _ok(client.get(f"/api/journal/{depot_txn.id}"))["class_id"] == (
        dims["shop"]["id"]
    )
    dep_lines = db_session.get(Transaction, rows["DEP"].transaction_id).lines
    assert seed_customer.id in {ln.customer_id for ln in dep_lines}
    for bt in rows.values():
        assert bt.match_status == "added"
        assert (bt.pick_class_id, bt.pick_job_id, bt.pick_vendor_id) == (
            None,
            None,
            None,
        )
        assert bt.pick_customer_id is None and bt.pick_payee is None


def test_a_kept_pick_whose_record_is_gone_is_passed_over(
    client, db_session, seed_accounts, lines
):
    acme = lines["ACME #22"]
    _ok(_patch(client, acme, category_account_id=seed_accounts["6000"].id))
    bt = db_session.get(BankTransaction, acme["id"])
    bt.pick_vendor_id, bt.pick_class_id, bt.pick_job_id = 987654, 987655, 987656
    db_session.commit()
    added = _ok(client.post(f"/api/banking/transactions/{acme['id']}/add"))
    assert added["match_status"] == "added"
    entry = _ok(client.get(f"/api/journal/{added['transaction_id']}"))
    assert entry["class_id"] is None and entry["job_id"] is None


def _users(db_session):
    for role in ("readonly", "accountant"):
        db_session.add(
            User(
                username=role + "-904",
                display_name=role.title(),
                password_hash=auth_service.hash_password(USER_PW),
                role=role,
                is_active=True,
            )
        )
    db_session.commit()


@pytest.mark.parametrize("role", ["readonly", "accountant"])
def test_a_read_only_or_accountant_sign_in_cannot_write_the_picks(
    client, db_session, dims, lines, role
):
    _users(db_session)
    client.post("/api/auth/logout")
    _ok(
        client.post(
            "/api/auth/login", json={"username": role + "-904", "password": USER_PW}
        )
    )
    r = _patch(client, lines["ACME #22"], class_id=dims["shop"]["id"])
    assert r.status_code == 403
    db_session.expire_all()
    assert (
        db_session.get(BankTransaction, lines["ACME #22"]["id"]).pick_class_id is None
    )


def test_a_rule_from_the_line_names_its_vendor_class_and_job(
    client, db_session, seed_accounts, feed, dims, lines
):
    """What Rule… sends from a row with a vendor, class and job picked: the
    rule keeps them, and the lines it catches post with them."""
    body = {
        "name": "ACME #22",
        "pattern": "ACME",
        "account_id": seed_accounts["6000"].id,
        "vendor_id": dims["acme"]["id"],
        "class_id": dims["shop"]["id"],
        "job_id": dims["kitchen"]["id"],
    }
    rule = _ok(client.post("/api/bank-rules", json=body))
    assert (rule["vendor_id"], rule["class_id"], rule["job_id"]) == (
        dims["acme"]["id"],
        dims["shop"]["id"],
        dims["kitchen"]["id"],
    )
    _ok(client.post("/api/bank-rules/apply"))
    added = _ok(client.post(f"/api/banking/transactions/{lines['ACME #22']['id']}/add"))
    assert added["vendor_name"] == "Acme Lumber"
    entry = _ok(client.get(f"/api/journal/{added['transaction_id']}"))
    assert entry["class_id"] == dims["shop"]["id"]


def test_the_review_row_keeps_its_picks_and_hands_them_to_the_rule_form():
    cells = JS[JS.index("    _reviewDimCells(t) {") :]
    cells = cells[: cells.index("\n    },")]
    assert 'const save = `onchange="BankingPage.setPicks(${t.id})"`;' in cells
    for kept in ("t.pick_class_id", "t.pick_job_id", "t.pick_customer_id"):
        assert kept in cells
    assert "t.pick_vendor_name || t.pick_payee" in cells
    setter = JS[JS.index("    setPicks(lineId) {") :]
    setter = setter[: setter.index("\n    },")]
    assert "API.request('PATCH', `/banking/transactions/${lineId}`" in setter
    assert "BankingPage._pendingCategories.add(saving);" in setter
    rule = JS[JS.index("    async ruleFromLine(lineId, accountId) {") :]
    rule = rule[: rule.index("\n    },")]
    for key in ("vendor_id: picks.vendor_id", "class_id: picks.class_id"):
        assert key in rule
    assert "job_id: picks.job_id" in rule
    # one line's Add, Exclude and Match take out only its row
    for fn in ("addLine(lineId, accountId)", "excludeLine(lineId, accountId)"):
        body = JS[JS.index(f"    async {fn} {{") :]
        body = body[: body.index("\n    },")]
        assert "BankingPage._dropReviewRow(lineId, accountId);" in body
        assert "_pendingCategories" in body


def test_the_migration_adds_the_columns_and_walks_back(tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    db = tmp_path / "picks.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision(REVISION).down_revision == "d897a2c4e6f1"
    assert script.get_heads() == ["c1128e5a7d0f"]  # #1128 is the head above it
    command.upgrade(cfg, "d897a2c4e6f1")  # the step below it
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO bank_accounts (id, name, is_active) VALUES (1, 'Feed', 1)"
        )
        conn.execute(
            "INSERT INTO bank_transactions (id, bank_account_id, date, amount)"
            " VALUES (1, 1, '2026-09-03', -12.5)"
        )
    command.upgrade(cfg, REVISION)  # its own step (#770)
    picks = {
        "pick_class_id",
        "pick_job_id",
        "pick_vendor_id",
        "pick_customer_id",
        "pick_payee",
    }
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bank_transactions)")}
        assert picks <= cols
        row = conn.execute(
            "SELECT pick_class_id, pick_payee, amount FROM bank_transactions"
        ).fetchone()
        assert row == (None, None, -12.5)
    command.downgrade(cfg, "d897a2c4e6f1")
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bank_transactions)")}
        assert not picks & cols
        assert conn.execute("SELECT count(*) FROM bank_transactions").fetchone() == (1,)
