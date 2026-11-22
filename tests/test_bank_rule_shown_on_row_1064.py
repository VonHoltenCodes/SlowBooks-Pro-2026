"""A bank rule's vendor, class and job show on the To review row, and a
`*` in a rule's pattern stands for any run of characters (#1064).

Reported by a bookkeeper testing 2.31.14: a rule naming a vendor and a
class posted the class, but the row showed neither, and "T-MOBILE*"
matched nothing by its pattern. Now the review list carries what the
line's rule gives it and the row is drawn with it where nothing was
picked; a pick still wins."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.bank_rules_engine import _matches
from app.services.ofx_import import import_transactions

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
BANK_TEXT = "T-MOBILE, PCS SVC   ,91."


def _ok(r, code=(200, 201)):
    assert r.status_code in code, r.text
    return r.json()


@pytest.mark.parametrize("pattern", ["T-MOBILE", "T-MOBILE,", "T-MOBILE*", "*PCS SVC*"])
def test_the_patterns_people_type_match_the_bank_text(pattern):
    assert _matches("contains", pattern, BANK_TEXT.lower())


def test_a_star_is_any_run_of_characters_and_keeps_the_rule_type():
    payee = BANK_TEXT.lower()
    assert _matches("starts_with", "t-mob*svc", payee)
    assert not _matches("starts_with", "pcs*", payee)
    assert _matches("exact", "t-mobile*,91.", payee)
    assert not _matches("exact", "t-mobile*,91", payee + " extra")
    assert not _matches("exact", "pcs*", payee)
    # a star alone, or nothing, is no pattern
    assert not _matches("contains", "*", payee)
    assert not _matches("contains", "  ", payee)
    # without a star the pattern is plain text: a dot is a dot
    assert not _matches("contains", "t.mobile", payee)


@pytest.fixture
def setup(client, db_session, seed_accounts):
    feed = _ok(
        client.post(
            "/api/banking/accounts",
            json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
        )
    )
    vendor = _ok(client.post("/api/vendors", json={"name": "T-Mobile"}))
    shop = _ok(client.post("/api/classes", json={"name": "Shop"}))
    other = _ok(client.post("/api/classes", json={"name": "Office"}))
    import_transactions(
        db_session,
        feed["id"],
        [
            {
                "fitid": f"T{i}",
                "date": date(2026, 9, 3 + i),
                "amount": Decimal(a),
                "payee": p,
                "memo": "",
            }
            for i, (a, p) in enumerate([("-91.00", BANK_TEXT), ("-5.00", "COFFEE")])
        ],
    )
    db_session.commit()
    _ok(
        client.post(
            "/api/bank-rules",
            json={
                "name": "Phone",
                "pattern": "T-MOBILE*",
                "account_id": seed_accounts["6000"].id,
                "vendor_id": vendor["id"],
                "class_id": shop["id"],
            },
        )
    )
    _ok(client.post("/api/bank-rules/apply"))
    return {"feed": feed, "vendor": vendor, "shop": shop, "other": other}


def _rows(client, feed):
    got = _ok(
        client.get(
            f"/api/banking/transactions?bank_account_id={feed['id']}&status=unmatched"
        )
    )
    return {r["payee"]: r for r in got}


def test_the_review_list_says_what_the_rule_gives(client, seed_accounts, setup):
    rows = _rows(client, setup["feed"])
    phone, coffee = rows[BANK_TEXT], rows["COFFEE"]
    assert phone["category_account_id"] == seed_accounts["6000"].id
    assert phone["rule_vendor_id"] == setup["vendor"]["id"]
    assert phone["rule_vendor_name"] == "T-Mobile"
    assert phone["rule_class_id"] == setup["shop"]["id"]
    assert phone["rule_job_id"] is None
    assert (coffee["rule_vendor_id"], coffee["rule_class_id"]) == (None, None)


def test_a_pick_on_the_row_wins_over_the_rule(client, db_session, setup):
    from app.models.transactions import Transaction

    phone = _rows(client, setup["feed"])[BANK_TEXT]
    _ok(
        client.patch(
            f"/api/banking/transactions/{phone['id']}",
            json={"class_id": setup["other"]["id"]},
        )
    )
    added = _ok(client.post(f"/api/banking/transactions/{phone['id']}/add", json={}))
    txn = db_session.get(Transaction, added["transaction_id"])
    assert txn.class_id == setup["other"]["id"]
    assert txn.description == "T-Mobile"


def test_an_archived_class_on_the_rule_is_not_shown(client, setup):
    _ok(
        client.put(f"/api/classes/{setup['shop']['id']}", json={"is_archived": True}),
    )
    phone = _rows(client, setup["feed"])[BANK_TEXT]
    assert phone["rule_class_id"] is None


def test_the_row_is_drawn_with_the_rules_picks_where_none_was_made():
    cells = JS[JS.index("    _reviewDimCells(t) {") :]
    cells = cells[: cells.index("\n    },\n")]
    assert "or(t.pick_class_id, t.rule_class_id)" in cells
    assert "or(t.pick_job_id, t.rule_job_id)" in cells
    assert "t.rule_vendor_name" in cells
