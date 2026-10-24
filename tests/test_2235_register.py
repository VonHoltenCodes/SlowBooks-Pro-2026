"""A bookkeeper's first hour (2.23.5), the register and the sidebar: a
register entry split across accounts (#330); the register in place and in
order (#329); the reconcile view through a day (#331); the menu items a
company hides (#334)."""

import json
from decimal import Decimal
from pathlib import Path

from app.models.transactions import Transaction, TransactionLine

ROOT = Path(__file__).resolve().parents[1]


def _lines(db, txn_id):
    return {
        ln.account_id: (Decimal(str(ln.debit or 0)), Decimal(str(ln.credit or 0)))
        for ln in db.query(TransactionLine).filter(
            TransactionLine.transaction_id == txn_id
        )
    }


# ---------------------------------------------------------------- #330


def test_a_register_entry_splits_across_accounts(client, db_session, seed_accounts):
    bank = seed_accounts["1000"]
    # a loan payment: principal to the loan, interest to expense, one check
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": bank.id,
            "date": "2026-03-01",
            "amount": "-500.00",
            "payee": "Harbor Bank",
            "check_number": "1041",
            "splits": [
                {
                    "category_account_id": seed_accounts["6300"].id,
                    "amount": "-120.00",
                    "description": "interest",
                },
                {
                    "category_account_id": seed_accounts["6000"].id,
                    "amount": "-380.00",
                    "description": "principal",
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    txn_id = r.json()["id"]
    lines = _lines(db_session, txn_id)
    assert lines[seed_accounts["6300"].id] == (Decimal("120.00"), Decimal("0"))
    assert lines[seed_accounts["6000"].id] == (Decimal("380.00"), Decimal("0"))
    assert lines[bank.id] == (Decimal("0"), Decimal("500.00"))
    txn = db_session.get(Transaction, txn_id)
    assert txn.source_type == "bank_entry" and txn.reference == "1041"
    memos = {
        ln.description
        for ln in db_session.query(TransactionLine).filter(
            TransactionLine.transaction_id == txn_id
        )
    }
    assert {"interest", "principal"} <= memos
    # money in, split two ways
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": bank.id,
            "date": "2026-03-02",
            "amount": "300.00",
            "splits": [
                {"category_account_id": seed_accounts["4000"].id, "amount": "200.00"},
                {"category_account_id": seed_accounts["4100"].id, "amount": "100.00"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    lines = _lines(db_session, r.json()["id"])
    assert lines[bank.id] == (Decimal("300.00"), Decimal("0"))
    assert lines[seed_accounts["4000"].id] == (Decimal("0"), Decimal("200.00"))


def test_split_lines_must_add_up_and_take_the_sign_and_no_bank_among_them(
    client, db_session, seed_accounts
):
    bank = seed_accounts["1000"]
    base = {"account_id": bank.id, "date": "2026-03-01", "amount": "-500.00"}
    short = client.post(
        "/api/banking/transactions",
        json={
            **base,
            "splits": [
                {"category_account_id": seed_accounts["6300"].id, "amount": "-120.00"},
                {"category_account_id": seed_accounts["6000"].id, "amount": "-300.00"},
            ],
        },
    )
    assert short.status_code == 400 and "must agree" in short.json()["detail"]
    # a line against the entry's sign is allowed since #387 (a fee on a
    # deposit, a refund netted against a check): test_register_split_deposits
    transfer = client.post(
        "/api/banking/transactions",
        json={
            **base,
            "splits": [
                {"category_account_id": seed_accounts["1010"].id, "amount": "-500.00"},
            ],
        },
    )
    assert transfer.status_code == 400 and "transfer" in transfer.json()["detail"]
    assert db_session.query(Transaction).count() == 0
    # without splits the category is still required
    none = client.post("/api/banking/transactions", json=base)
    assert none.status_code == 422


def test_the_entry_dialog_offers_a_split():
    js = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    assert "Split this entry" in js and "entry-split-rows" in js
    assert "...(splits ? { splits } : {})" in js


# ---------------------------------------------------------------- #329


def test_the_register_reads_oldest_first_and_takes_entries_in_place():
    js = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    assert "reg.entries.slice().reverse()" not in js
    assert 'id="reg-quick-entry"' in js and "postQuickEntry" in js
    assert "API.post('/banking/transactions', data)" in js


# ---------------------------------------------------------------- #331


def test_the_reconcile_view_can_show_only_lines_through_a_day():
    js = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    assert "reconShowThrough" in js and "Show only lines through that date" in js
    assert "later line" in js  # the hidden count


# ---------------------------------------------------------------- #334


def test_hidden_menu_items_are_a_setting_the_shell_applies(client, db_session):
    from app.models.settings import DEFAULT_SETTINGS

    assert DEFAULT_SETTINGS["hidden_menu"] == "[]"
    hidden = ["payroll", "section:Payroll & HR"]
    r = client.put("/api/settings", json={"hidden_menu": json.dumps(hidden)})
    assert r.status_code == 200, r.text
    assert json.loads(client.get("/api/settings").json()["hidden_menu"]) == hidden
    app_js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    assert (
        "applyHiddenMenu(s)" in app_js and "App.applyHiddenMenu(App.settings)" in app_js
    )
    assert "section:${li.textContent.trim()}" in app_js
    settings_js = (ROOT / "app/static/js/settings.js").read_text(encoding="utf-8")
    assert (
        "data-show-menu" in settings_js
        and "hidden_menu = JSON.stringify" in settings_js
    )


def test_the_hidden_list_round_trips_and_a_save_keeps_earlier_choices(
    client, db_session
):
    """#457: the setting is the list of HIDDEN keys (its meaning since #334, so
    a list saved before the upgrade still works); a second save with more
    items stores the whole list the Settings page now sends, and showing one
    again takes only that one off."""
    first = ["reseller-permits", "estimates", "purchase-orders", "hr-deductions"]
    r = client.put("/api/settings", json={"hidden_menu": json.dumps(first)})
    assert r.status_code == 200, r.text
    assert json.loads(client.get("/api/settings").json()["hidden_menu"]) == first
    both = first + ["vendor-credits", "batch-payments"]
    r = client.put("/api/settings", json={"hidden_menu": json.dumps(both)})
    assert r.status_code == 200, r.text
    assert json.loads(client.get("/api/settings").json()["hidden_menu"]) == both
    less = [k for k in both if k != "estimates"]
    client.put("/api/settings", json={"hidden_menu": json.dumps(less)})
    assert json.loads(client.get("/api/settings").json()["hidden_menu"]) == less
    client.put("/api/settings", json={"hidden_menu": "[]"})
    settings_js = (ROOT / "app/static/js/settings.js").read_text(encoding="utf-8")
    # the list offers the menu-hidden items (unticked: a tick means shown),
    # and Save keeps the stored keys the page did not offer
    assert "if (d.hiddenKind || d.hiddenRole) return;" in settings_js
    assert "_hiddenMenuToSave(menuBoxes)" in settings_js
    assert "set.has(key) ? '' : 'checked'" in settings_js
