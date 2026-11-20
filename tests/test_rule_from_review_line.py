"""A bank rule from a statement line in To review (#259, 2.22.2).

Rules suggested categories for imported lines, but making one was a
separate form where the pattern and the account were typed from scratch,
though the review row already had the payee and the category chosen. Now
"Rule…" on a review line opens the rule form with both filled in; saved,
the rules run over the queue and the register comes back.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.banking import BankTransaction
from app.services.ofx_import import import_transactions


@pytest.fixture
def feed(client, seed_accounts):
    r = client.post(
        "/api/banking/accounts",
        json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _lines(*rows):
    return [
        {
            "fitid": f"F{i}",
            "date": date(2026, 9, d),
            "amount": Decimal(a),
            "payee": p,
            "memo": "",
        }
        for i, (d, a, p) in enumerate(rows)
    ]


def test_a_rule_made_from_a_line_categorizes_every_line_of_that_payee(
    client, db_session, seed_accounts, feed
):
    import_transactions(
        db_session,
        feed["id"],
        _lines(
            (3, "-42.50", "TELEPHONE CO"),
            (17, "-42.50", "Telephone Co #2"),
            (20, "-9.00", "PARKING"),
        ),
    )
    db_session.commit()
    # what the review's "Rule…" sends: the payee as the pattern, the category chosen
    r = client.post(
        "/api/bank-rules",
        json={
            "name": "TELEPHONE CO",
            "pattern": "TELEPHONE CO",
            "account_id": seed_accounts["6000"].id,
        },
    )
    assert r.status_code == 201, r.text
    r = client.post("/api/bank-rules/apply")
    assert r.status_code == 200, r.text
    assert r.json()["matched"] == 2  # Contains, any case: both phone lines
    db_session.expire_all()
    by_payee = {t.payee: t for t in db_session.query(BankTransaction).all()}
    assert by_payee["TELEPHONE CO"].category_account_id == seed_accounts["6000"].id
    assert by_payee["Telephone Co #2"].category_account_id == seed_accounts["6000"].id
    assert by_payee["PARKING"].category_account_id is None
    # the category only: nothing was added to the books
    assert all(t.match_status == "unmatched" for t in by_payee.values())


def test_the_review_row_hands_the_payee_and_category_to_the_rule_form():
    banking = open("app/static/js/banking.js", encoding="utf-8").read()
    assert "BankingPage.ruleFromLine(${t.id}, ${accountId})" in banking
    fn = banking[banking.index("async ruleFromLine(lineId, accountId) {") :]
    fn = fn[: fn.index("\n    },")]
    assert "BankRulesPage.showForm(null, {" in fn
    # the payee as written: a star the bank prints is a star (NEW-259)
    assert "pattern: BankRulesPage.escapePattern(payee)," in fn
    assert "account_id: accountChosen" in fn
    assert "API.post('/bank-rules/apply')" in fn and "App.refresh();" in fn
    rules = open("app/static/js/bank_rules.js", encoding="utf-8").read()
    assert "async showForm(id = null, prefill = null)" in rules
    assert "BankRulesPage._afterSave = afterSave || null;" in rules
    assert "if (after) { await after(); return; }" in rules
    assert "A shorter pattern catches other spellings" in rules
