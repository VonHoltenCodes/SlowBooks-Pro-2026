"""A new bank feed's statement balance never counts the account twice.

Exploratory 2.17.3 (skytech, W-H6): Savings already held $300 from a
transfer. Banking → + New Bank Account → Savings, statement balance
$300.00 → the feed posted a second $300 against 3900 and Savings read $600,
with no warning. The ledger's own balance on the As-of date is what the
statement is compared with now: equal posts nothing, different is refused
until the user confirms, and then only the difference posts."""

import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.transactions import Transaction

JS = (Path(__file__).resolve().parents[1] / "app/static/js/banking.js").read_text(
    encoding="utf-8"
)


@pytest.fixture
def savings_with_300(client, seed_accounts):
    """$300 moved from Checking to Savings on Sep 10."""
    r = client.post(
        "/api/transfers",
        json={
            "date": "2026-09-10",
            "from_account_id": seed_accounts["1000"].id,
            "to_account_id": seed_accounts["1010"].id,
            "amount": "300",
        },
    )
    assert r.status_code in (200, 201), r.text
    return seed_accounts["1010"]


def _balance(client, account):
    return Decimal(
        str(
            client.get(f"/api/banking/check-register?account_id={account.id}").json()[
                "balance"
            ]
        )
    )


def _openings(db_session):
    return (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "opening_balance")
        .all()
    )


def _feed(client, account, balance, as_of="2026-09-26", **extra):
    return client.post(
        "/api/banking/accounts",
        json={
            "name": "First Bank Savings",
            "account_id": account.id,
            "opening_balance": balance,
            "opening_date": as_of,
            **extra,
        },
    )


def test_a_statement_that_matches_the_books_posts_nothing(
    client, db_session, savings_with_300
):
    r = _feed(client, savings_with_300, "300.00")
    assert r.status_code == 201, r.text
    assert _balance(client, savings_with_300) == Decimal("300")
    assert _openings(db_session) == []


def test_a_different_statement_is_refused_naming_the_books_balance(
    client, db_session, savings_with_300
):
    r = _feed(client, savings_with_300, "500.00")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "ledger_has_balance"
    assert "$300.00" in detail["message"] and "Sep 26, 2026" in detail["message"]
    assert Decimal(detail["ledger_balance"]) == Decimal("300.00")
    assert Decimal(detail["difference"]) == Decimal("200.00")
    # nothing posted, no feed created
    assert _balance(client, savings_with_300) == Decimal("300")
    assert _openings(db_session) == []
    assert client.get("/api/banking/accounts").json() == []


def test_a_confirmed_difference_posts_only_the_difference(
    client, db_session, savings_with_300
):
    r = _feed(client, savings_with_300, "500.00", post_difference=True)
    assert r.status_code == 201, r.text
    assert _balance(client, savings_with_300) == Decimal("500")
    [adj] = _openings(db_session)
    assert adj.description == "Opening balance adjustment: Savings"
    assert {ln.account_id: ln.debit for ln in adj.lines if ln.debit > 0} == {
        savings_with_300.id: Decimal("200.00")
    }


def test_a_lower_statement_posts_a_negative_difference(
    client, db_session, savings_with_300
):
    r = _feed(client, savings_with_300, "250.00", post_difference=True)
    assert r.status_code == 201, r.text
    assert _balance(client, savings_with_300) == Decimal("250")


def test_activity_only_after_the_as_of_date_still_takes_the_whole_figure(
    client, db_session, savings_with_300
):
    """A statement dated before the ledger's first posting is a true
    opening balance: the transfer on Sep 10 comes on top of it."""
    r = _feed(client, savings_with_300, "300.00", as_of="2026-09-01")
    assert r.status_code == 201, r.text
    assert _balance(client, savings_with_300) == Decimal("600")
    assert len(_openings(db_session)) == 1


def test_the_form_can_ask_what_the_books_say(client, savings_with_300):
    r = client.get(
        f"/api/banking/ledger-balance?account_id={savings_with_300.id}&as_of=2026-09-26"
    )
    assert r.status_code == 200, r.text
    assert r.json()["balance"] == 300.0 and r.json()["has_postings"] is True
    before = client.get(
        f"/api/banking/ledger-balance?account_id={savings_with_300.id}&as_of=2026-09-01"
    ).json()
    assert before["balance"] == 0.0 and before["has_postings"] is False


def test_the_new_account_form_shows_the_books_and_confirms_a_difference():
    form = re.search(r"async showAccountForm\(\).*?\n    },\n", JS, re.S).group(0)
    assert "_showLedgerBalance" in form and "acct-ledger-note" in form
    shower = re.search(r"async _showLedgerBalance\(form\).*?\n    },\n", JS, re.S)
    assert shower and "/banking/ledger-balance?account_id=" in shower.group(0)
    save = re.search(r"async saveAccount\(e\).*?\n    },\n", JS, re.S).group(0)
    assert "ledger_has_balance" in save and "confirm(" in save
    assert save.index("confirm(") < save.index("post_difference: true")
