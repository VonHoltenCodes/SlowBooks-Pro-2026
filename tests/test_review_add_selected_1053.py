"""Tick several To review lines and add them together (#1053).

Add posted one line and Add all every line with a category; there was no
way to add a chosen few. Add all now takes the ticked lines' ids (Add
selected): only those are added, each with its own row's picks, and one
that cannot post (no category yet, excluded, already in the books, another
feed's) is named with the reason instead of being passed over. The page
has a box on each row and in the header, a bar that sets a category,
class, job or name on the ticked lines, and Add selected beside Add all.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.banking import BankTransaction
from app.models.transactions import Transaction
from app.services.ofx_import import import_transactions

import secrets

# made at run time: no password, even a made-up one, is written in this repo
_PW0 = secrets.token_urlsafe(9) + "-Aa1!"

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")


def _ok(r, code=(200, 201)):
    assert r.status_code in code, r.text
    return r.json()


@pytest.fixture
def setup(client, db_session, seed_accounts):
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
                "fitid": f"S{i}",
                "date": date(2026, 9, 3 + i),
                "amount": Decimal(a),
                "payee": p,
                "memo": "",
            }
            for i, (a, p) in enumerate(
                [
                    ("-10.00", "LUMBER 1"),
                    ("-11.00", "LUMBER 2"),
                    ("-12.00", "LUMBER 3"),
                    ("-13.00", "OTHER 4"),
                ]
            )
        ],
    )
    db_session.commit()
    lines = {
        r["payee"]: r
        for r in _ok(
            client.get(
                f"/api/banking/transactions?bank_account_id={feed['id']}&status=unmatched"
            )
        )
    }
    expense = seed_accounts["6000"].id
    for line in lines.values():
        _ok(
            client.patch(
                f"/api/banking/transactions/{line['id']}",
                json={"category_account_id": expense},
            )
        )
    return feed, lines


def _status(db_session, line):
    bt = db_session.get(BankTransaction, line["id"])
    db_session.refresh(bt)
    return bt


def test_only_the_ticked_lines_are_added_each_with_its_picks(client, db_session, setup):
    feed, lines = setup
    vendor = _ok(client.post("/api/vendors", json={"name": "Lumber Yard"}))
    ticked = [lines["LUMBER 1"]["id"], lines["LUMBER 3"]["id"]]
    out = _ok(
        client.post(
            f"/api/banking/accounts/{feed['id']}/feed/add-all",
            json={
                "line_ids": ticked,
                "lines": [{"id": i, "vendor_id": vendor["id"]} for i in ticked],
            },
        )
    )
    assert out["added"] == 2 and out["skipped"] == []
    for payee in ("LUMBER 1", "LUMBER 3"):
        bt = _status(db_session, lines[payee])
        txn = db_session.get(Transaction, bt.transaction_id)
        assert bt.match_status == "added"
        assert (txn.description, txn.vendor_id) == ("Lumber Yard", vendor["id"])
    for payee in ("LUMBER 2", "OTHER 4"):
        assert _status(db_session, lines[payee]).match_status == "unmatched"


def test_a_ticked_line_that_cannot_post_is_named_with_the_reason(
    client, db_session, setup
):
    feed, lines = setup
    bare, gone, out_line = lines["LUMBER 2"], lines["OTHER 4"], lines["LUMBER 3"]
    _ok(
        client.patch(
            f"/api/banking/transactions/{bare['id']}",
            json={"category_account_id": None},
        )
    )
    _ok(client.post(f"/api/banking/transactions/{gone['id']}/exclude"))
    _ok(client.post(f"/api/banking/transactions/{out_line['id']}/add", json={}))
    out = _ok(
        client.post(
            f"/api/banking/accounts/{feed['id']}/feed/add-all",
            json={
                "line_ids": [
                    lines["LUMBER 1"]["id"],
                    bare["id"],
                    gone["id"],
                    out_line["id"],
                    999999,
                ]
            },
        )
    )
    assert out["added"] == 1
    reasons = {s["id"]: s["reason"] for s in out["skipped"]}
    assert reasons == {
        bare["id"]: "Pick a category (account) to add this line",
        gone["id"]: "That line is excluded; restore it to add it",
        out_line["id"]: "That line is already in the books",
        999999: "That line is not on this feed's To review list",
    }


def test_add_all_without_ticks_is_as_before(client, db_session, setup):
    feed, lines = setup
    out = _ok(client.post(f"/api/banking/accounts/{feed['id']}/feed/add-all"))
    assert out["added"] == 4


def test_a_read_only_sign_in_cannot_add_selected(client, db_session, setup):
    from app.models.users import User
    from app.services import auth as auth_service

    feed, lines = setup
    db_session.add(
        User(
            username="readonly-1053",
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
            json={"username": "readonly-1053", "password": _PW0},
        )
    )
    r = client.post(
        f"/api/banking/accounts/{feed['id']}/feed/add-all",
        json={"line_ids": [lines["LUMBER 1"]["id"]]},
    )
    assert r.status_code == 403
    assert _status(db_session, lines["LUMBER 1"]).match_status == "unmatched"


def test_the_page_has_the_boxes_the_bar_and_add_selected():
    panel = JS[JS.index("    _reviewPanel(review, feedId, accountId, kind) {") :]
    panel = panel[: panel.index("\n    },\n")]
    assert 'class="review-tick"' in panel and 'aria-label="Tick the line of' in panel
    assert (
        'id="review-tick-all"' in panel
        and 'aria-label="Tick every line shown"' in panel
    )
    assert "BankingPage.addSelected(${feedId}, ${accountId})" in panel
    assert panel.index(">Add selected<") < panel.index(">Add all categorised<")
    assert "BankingPage._bulkBar(accountId)" in panel
    bar = JS[JS.index("    _bulkBar(accountId) {") :]
    bar = bar[: bar.index("\n    },\n")]
    for label in (
        'aria-label="Category for the ticked lines"',
        'for the ticked lines" style',
        'aria-label="Vendor for the ticked money-out lines"',
        "for the ticked money-in lines",
    ):
        assert label in bar
    add = JS[JS.index("    async addSelected(feedId, accountId) {") :]
    add = add[: add.index("\n    },\n")]
    assert "applyToTicked({ quiet: true })" in add
    assert "line_ids: ids" in add
