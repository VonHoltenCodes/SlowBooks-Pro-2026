"""A bank rule's `*` after the wildcard (#1064): a star a rule had before
the upgrade stays the star the bank prints (NEW-259), and a pattern with no
text to match is refused in a sentence (W-193).

#1064 made `*` stand for any run of characters ("T-MOBILE*"). A rule made
before it with a literal star, Contains `SQ *` for "SQ *JOES DELI", then
also filed "MSQ LLC PARTS". The migration c1064a5e7b2d writes each existing
rule's stars as `\\*`, the way a star the bank prints is now typed, so every
rule files exactly what it filed before; `*` stays the wildcard for rules
written with it. A pattern of only `*` or spaces saved and matched nothing:
the API and the form refuse it."""

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.bank_rules_engine import (
    PATTERN_EMPTY,
    _matches,
    escape_pattern,
    pattern_parts,
)
from app.services.ofx_import import import_transactions

ROOT = Path(__file__).resolve().parent.parent
REVISION = "c1064a5e7b2d"
BELOW = "a1075c4d8e2f"

# what the bank prints, and what a rule made on round 21's build (a pattern
# was plain text) filed of it
PAYEES = [
    "SQ *JOES DELI",
    "MSQ LLC PARTS",
    "SQ JOES DELI",
    "AMZN Mktp US*2X3",
    "AMZN Mktp US 2X3",
    "T-MOBILE AUTOPAY 1188",
    "a\\*b",
    "a*b",
]
OLD_RULES = [
    ("contains", "SQ *"),
    ("contains", "AMZN Mktp US*"),
    ("starts_with", "SQ *JOES"),
    ("exact", "AMZN Mktp US*2X3"),
    ("contains", "T-MOBILE"),
    ("contains", "a\\*b"),  # a backslash before a star, as plain text
    ("contains", "*"),  # a star alone: the star itself, before
]


def _plain_text_match(rule_type, pattern, payee):
    """How round 21's build matched: the pattern as plain text, any case."""
    pattern = pattern.strip().lower()
    payee = payee.lower()
    if not pattern:
        return False
    return {
        "contains": pattern in payee,
        "starts_with": payee.startswith(pattern),
        "exact": payee == pattern,
    }[rule_type]


def _ok(r, code=(200, 201)):
    assert r.status_code in code, r.text
    return r.json()


# --- the engine --------------------------------------------------------------


def test_a_star_the_bank_prints_is_written_escaped_and_matches_only_itself():
    assert not _matches("contains", "SQ \\*", "msq llc parts")
    assert _matches("contains", "SQ \\*", "sq *joes deli")
    assert not _matches("contains", "SQ \\*", "sq joes deli")


def test_the_wildcard_of_1064_still_works():
    assert _matches("contains", "T-MOBILE*", "t-mobile autopay 1188")
    assert _matches("starts_with", "t-mob*svc", "t-mobile, pcs svc")
    assert _matches("contains", "*PCS SVC*", "t-mobile, pcs svc   ,91.")
    # a wildcard rule matches more than its escaped twin: that is the choice
    assert _matches("contains", "SQ *", "msq llc parts")


def test_escaping_round_trips_any_text():
    for text in ["SQ *JOES", "a\\*b", "C:\\TEMP", "\\\\x", "*", "**", "plain"]:
        assert pattern_parts(escape_pattern(text)) == [text.strip()]


def test_a_vendors_own_name_is_matched_as_written():
    from app.services.bank_rules_engine import _hit

    class Rule:
        rule_type, pattern, vendor_id = "contains", "NOTHING HERE", 7

    names = {7: "AMZN*"}
    assert _hit(Rule, "amzn* 2x3", names)
    assert not _hit(Rule, "amzn 2x3", names)


@pytest.mark.parametrize("pattern", ["*", "  ", "* *", "***", " * "])
def test_a_pattern_with_no_text_matches_nothing(pattern):
    assert not _matches("contains", pattern, "anything at all")


# --- the API -----------------------------------------------------------------


@pytest.mark.parametrize("pattern", ["*", "   ", "* *", "**"])
def test_a_pattern_of_only_stars_or_spaces_is_refused_in_a_sentence(client, pattern):
    r = client.post("/api/bank-rules", json={"name": "Star", "pattern": pattern})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == PATTERN_EMPTY
    assert _ok(client.get("/api/bank-rules")) == []


def test_an_edit_to_a_pattern_of_only_stars_is_refused(client):
    rule = _ok(client.post("/api/bank-rules", json={"name": "Ok", "pattern": "OK"}))
    r = client.put(f"/api/bank-rules/{rule['id']}", json={"pattern": " * "})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == PATTERN_EMPTY
    assert _ok(client.get(f"/api/bank-rules/{rule['id']}"))["pattern"] == "OK"
    # a star itself is text to match
    _ok(client.put(f"/api/bank-rules/{rule['id']}", json={"pattern": "\\*"}))


def test_apply_rules_files_by_the_escaped_and_the_wildcard_rule(
    client, db_session, seed_accounts
):
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
                "date": date(2026, 11, 2 + i),
                "amount": Decimal("-10.00"),
                "payee": p,
                "memo": "",
            }
            for i, p in enumerate(
                ["SQ *JOES DELI", "MSQ LLC PARTS", "T-MOBILE AUTOPAY 1188"]
            )
        ],
    )
    db_session.commit()
    misc, phone = seed_accounts["6000"].id, seed_accounts["6100"].id
    for name, pattern, acct in (
        ("Square", "SQ \\*", misc),
        ("Phone", "T-MOBILE*", phone),
    ):
        _ok(
            client.post(
                "/api/bank-rules",
                json={"name": name, "pattern": pattern, "account_id": acct},
            )
        )
    _ok(client.post("/api/bank-rules/apply"))
    got = {
        t["payee"]: t["category_account_id"]
        for t in _ok(
            client.get(f"/api/banking/transactions?bank_account_id={feed['id']}")
        )
    }
    assert got == {
        "SQ *JOES DELI": misc,
        "MSQ LLC PARTS": None,
        "T-MOBILE AUTOPAY 1188": phone,
    }


def test_a_vendors_bank_rule_writes_the_star_in_its_name_as_a_star(
    client, seed_accounts
):
    vendor = _ok(
        client.post(
            "/api/vendors",
            json={
                "name": "SQ *JOES",
                "default_expense_account_id": seed_accounts["6000"].id,
            },
        )
    )
    _ok(client.put(f"/api/vendors/{vendor['id']}", json={"bank_feed_rule": True}))
    (rule,) = _ok(client.get("/api/bank-rules"))
    assert rule["pattern"] == "SQ \\*JOES"


# --- the migration -----------------------------------------------------------


def _cfg(db):
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + Path(db).as_posix()
    return cfg


def test_rules_made_before_the_upgrade_file_exactly_what_they_filed(tmp_path):
    from alembic import command
    from alembic.script import ScriptDirectory

    db = tmp_path / "star.db"
    cfg = _cfg(db)
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision(REVISION).down_revision == BELOW
    assert script.get_heads() == ["c1128e5a7d0f"]  # #1128 is the head above it
    command.upgrade(cfg, BELOW)  # the step below it
    with sqlite3.connect(db) as conn:
        for i, (rule_type, pattern) in enumerate(OLD_RULES, start=1):
            conn.execute(
                "INSERT INTO bank_rules (id, name, pattern, rule_type, priority,"
                " is_active) VALUES (?, ?, ?, ?, 0, 1)",
                (i, f"Old {i}", pattern, rule_type),
            )
    command.upgrade(cfg, REVISION)  # its own step (#770)
    with sqlite3.connect(db) as conn:
        after = dict(conn.execute("SELECT id, pattern FROM bank_rules").fetchall())
    assert after[1] == "SQ \\*"
    assert after[5] == "T-MOBILE"  # no star, untouched
    for i, (rule_type, old) in enumerate(OLD_RULES, start=1):
        for payee in PAYEES:
            assert _matches(rule_type, after[i], payee.lower()) == _plain_text_match(
                rule_type, old, payee
            ), (old, after[i], payee)
    # "SQ *" made on round 21 does not file "MSQ LLC PARTS" after it
    assert not _matches("contains", after[1], "msq llc parts")
    command.downgrade(cfg, BELOW)
    with sqlite3.connect(db) as conn:
        back = dict(conn.execute("SELECT id, pattern FROM bank_rules").fetchall())
    assert back == {i: p for i, (_, p) in enumerate(OLD_RULES, start=1)}


# --- the window's own copy of the rules (bank_rules.js) ---------------------


def test_the_form_explains_the_star_behind_the_tortoise_and_refuses_an_empty_one():
    js = (ROOT / "app/static/js/bank_rules.js").read_text(encoding="utf-8")
    form = js[js.index("bank-rule-pattern") :]
    assert "A <code>*</code> stands for anything" in form
    assert "A star the bank prints is typed <code>\\\\*</code>" in form
    # the sentence is the server's, word for word
    assert PATTERN_EMPTY.replace("\\", "\\\\") in js
    # Rule… from a statement line writes the bank's star as a star
    banking = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    assert "pattern: BankRulesPage.escapePattern(payee)" in banking
