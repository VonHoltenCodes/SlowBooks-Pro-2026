"""A memo on each To review row (#1049).

A line waiting in To review took a category, class, job and name but no
memo, so a note about what the money was for meant reopening the entry
after it posted. The memo typed on the row is kept on the line like the
other picks (#904), Add and Add all post it as the entry's memo, a
read-only sign-in cannot write it, and the migration adds the column and
walks back."""

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.banking import BankTransaction
from app.models.transactions import TransactionLine
from app.services.ofx_import import import_transactions

import secrets

# made at run time: no password, even a made-up one, is written in this repo
_PW0 = secrets.token_urlsafe(9) + "-Aa1!"

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
REVISION = "f1049c2a7e5b"


def _ok(r, code=(200, 201)):
    assert r.status_code in code, r.text
    return r.json()


@pytest.fixture
def lines(client, db_session, seed_accounts):
    feed = _ok(
        client.post(
            "/api/banking/accounts",
            json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
        )
    )
    import_transactions(
        db_session,
        feed["id"],
        [
            {
                "fitid": f"M{i}",
                "date": date(2026, 9, 3 + i),
                "amount": Decimal(a),
                "payee": p,
                "memo": "",
            }
            for i, (a, p) in enumerate([("-40.00", "HARDWARE 1"), ("-25.00", "FUEL 2")])
        ],
    )
    db_session.commit()
    got = _ok(
        client.get(
            f"/api/banking/transactions?bank_account_id={feed['id']}&status=unmatched"
        )
    )
    return feed, {r["payee"]: r for r in got}


def _memos(db_session, line_id):
    bt = db_session.get(BankTransaction, line_id)
    db_session.refresh(bt)
    rows = db_session.query(TransactionLine).filter_by(transaction_id=bt.transaction_id)
    return {ln.description for ln in rows}, bt


def test_the_memo_is_kept_on_the_line_and_posted_by_add(
    client, db_session, seed_accounts, lines
):
    feed, got = lines
    hw = got["HARDWARE 1"]
    out = _ok(
        client.patch(
            f"/api/banking/transactions/{hw['id']}",
            json={"memo": "  Hinges for the back door  "},
        )
    )
    assert out["pick_memo"] == "Hinges for the back door"
    # a category pick leaves the memo as it was
    _ok(
        client.patch(
            f"/api/banking/transactions/{hw['id']}",
            json={"category_account_id": seed_accounts["6000"].id},
        )
    )
    again = {
        r["payee"]: r
        for r in _ok(
            client.get(
                f"/api/banking/transactions?bank_account_id={feed['id']}&status=unmatched"
            )
        )
    }
    assert again["HARDWARE 1"]["pick_memo"] == "Hinges for the back door"
    _ok(client.post(f"/api/banking/transactions/{hw['id']}/add", json={}))
    memos, bt = _memos(db_session, hw["id"])
    assert memos == {"Hinges for the back door"}
    assert bt.pick_memo is None  # cleared once the entry has it
    detail = _ok(client.get(f"/api/banking/entries/{bt.transaction_id}"))
    assert detail["description"] == "Hinges for the back door"


def test_a_memo_sent_with_add_wins_and_null_clears_a_kept_one(
    client, db_session, seed_accounts, lines
):
    _, got = lines
    hw, fuel = got["HARDWARE 1"], got["FUEL 2"]
    _ok(client.patch(f"/api/banking/transactions/{fuel['id']}", json={"memo": "x"}))
    out = _ok(
        client.patch(f"/api/banking/transactions/{fuel['id']}", json={"memo": None})
    )
    assert out["pick_memo"] is None
    _ok(client.patch(f"/api/banking/transactions/{hw['id']}", json={"memo": "kept"}))
    _ok(
        client.post(
            f"/api/banking/transactions/{hw['id']}/add",
            json={"memo": "typed", "category_account_id": seed_accounts["6000"].id},
        )
    )
    assert _memos(db_session, hw["id"])[0] == {"typed"}


def test_add_all_posts_each_rows_memo(client, db_session, seed_accounts, lines):
    feed, got = lines
    hw, fuel = got["HARDWARE 1"], got["FUEL 2"]
    for line in (hw, fuel):
        _ok(
            client.patch(
                f"/api/banking/transactions/{line['id']}",
                json={"category_account_id": seed_accounts["6000"].id},
            )
        )
    _ok(client.patch(f"/api/banking/transactions/{hw['id']}", json={"memo": "kept"}))
    out = _ok(
        client.post(
            f"/api/banking/accounts/{feed['id']}/feed/add-all",
            json={"lines": [{"id": fuel["id"], "memo": "Delivery van"}]},
        )
    )
    assert out["added"] == 2
    assert _memos(db_session, hw["id"])[0] == {"kept"}
    assert _memos(db_session, fuel["id"])[0] == {"Delivery van"}


def test_a_read_only_sign_in_cannot_write_the_memo(client, db_session, lines):
    from app.models.users import User
    from app.services import auth as auth_service

    _, got = lines
    db_session.add(
        User(
            username="readonly-1049",
            display_name="Read only",
            password_hash=auth_service.hash_password(_PW0),
            role="readonly",
            is_active=True,
        )
    )
    db_session.commit()
    client.post("/api/auth/logout")
    _ok(
        client.post(
            "/api/auth/login",
            json={"username": "readonly-1049", "password": _PW0},
        )
    )
    hw = got["HARDWARE 1"]
    r = client.patch(f"/api/banking/transactions/{hw['id']}", json={"memo": "no"})
    assert r.status_code == 403
    db_session.expire_all()
    assert db_session.get(BankTransaction, hw["id"]).pick_memo is None


def test_the_row_has_a_memo_box_kept_and_sent_like_the_other_picks():
    cells = JS[JS.index("    _reviewDimCells(t) {") :]
    cells = cells[: cells.index("\n    },\n")]
    assert 'id="rv-memo-${t.id}"' in cells
    assert 'aria-label="Memo, line ${t.id}"' in cells
    assert "t.pick_memo" in cells
    picks = JS[JS.index("    _reviewPicks(lineId) {") :]
    picks = picks[: picks.index("\n    },\n")]
    assert "rv-memo-" in picks and "picks.memo" in picks
    setter = JS[JS.index("    setPicks(lineId) {") :]
    assert "memo: null" in setter[: setter.index("\n    },\n")]


def test_the_migration_adds_the_column_and_walks_back(tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    db = tmp_path / "memo.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision(REVISION).down_revision == "e1055c0de4a1"
    assert script.get_heads() == [
        "c1128e5a7d0f"
    ]  # the estimate void (#1128) is the head
    command.upgrade(cfg, "e1055c0de4a1")  # the step below it
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO bank_accounts (id, name, is_active) VALUES (1, 'Feed', 1)"
        )
        conn.execute(
            "INSERT INTO bank_transactions (id, bank_account_id, date, amount)"
            " VALUES (1, 1, '2026-09-03', -12.5)"
        )
    command.upgrade(cfg, REVISION)  # its own step (#770)
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bank_transactions)")}
        assert "pick_memo" in cols
        row = conn.execute("SELECT pick_memo, amount FROM bank_transactions").fetchone()
        assert row == (None, -12.5)
    command.downgrade(cfg, "e1055c0de4a1")
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bank_transactions)")}
        assert "pick_memo" not in cols
        assert conn.execute("SELECT count(*) FROM bank_transactions").fetchone() == (1,)
