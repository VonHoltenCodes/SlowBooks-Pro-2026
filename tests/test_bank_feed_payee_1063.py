"""The name a bank-feed line is given is the entry's payee (#1063).

Reported by a bookkeeper testing 2.31.14: a deposit added with a customer
picked, a payment with a vendor picked, and a line a rule names a vendor
for all posted with the bank's text as the payee. The customer was kept
only on the income line, the vendor only on the entry's link, so the
register and the reopened entry showed the bank's words. Now the name
picked, or the rule's vendor, is the payee; the bank's text is the memo;
the statement line keeps its own text; Add all posts the same way."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.banking import BankTransaction
from app.models.transactions import Transaction, TransactionLine
from app.services.books_check import books_check
from app.services.ofx_import import import_transactions


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
def vendor(client):
    return _ok(client.post("/api/vendors", json={"name": "T-Mobile"}))


def _lines(client, db_session, feed, rows):
    import_transactions(
        db_session,
        feed["id"],
        [
            {
                "fitid": f"N{i}",
                "date": date(2026, 9, 3 + i),
                "amount": Decimal(a),
                "payee": p,
                "memo": "",
            }
            for i, (a, p) in enumerate(rows)
        ],
    )
    db_session.commit()
    got = _ok(
        client.get(
            f"/api/banking/transactions?bank_account_id={feed['id']}&status=unmatched"
        )
    )
    return {r["payee"]: r for r in got}


def _entry(db_session, line):
    bt = db_session.get(BankTransaction, line["id"])
    db_session.refresh(bt)
    txn = db_session.get(Transaction, bt.transaction_id)
    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
    return bt, txn, lines


def _register(client, seed_accounts):
    reg = _ok(
        client.get(f"/api/banking/check-register?account_id={seed_accounts['1000'].id}")
    )
    return {e["transaction_id"]: e for e in reg["entries"]}


def _books_agree(db_session):
    rows = books_check(db_session)["rows"]
    assert [r["name"] for r in rows if r["ok"] is False] == []


def test_a_deposit_with_a_customer_picked_is_the_customers(
    client, db_session, seed_accounts, seed_customer, feed
):
    line = _lines(client, db_session, feed, [("300.00", "DEPOSIT 1234")])[
        "DEPOSIT 1234"
    ]
    income = seed_accounts["4000"].id
    _ok(
        client.patch(
            f"/api/banking/transactions/{line['id']}",
            json={"customer_id": seed_customer.id, "category_account_id": income},
        )
    )
    _ok(client.post(f"/api/banking/transactions/{line['id']}/add", json={}))
    bt, txn, lines = _entry(db_session, line)
    assert txn.description == "Test Customer"
    by_account = {ln.account_id: ln for ln in lines}
    assert by_account[income].customer_id == seed_customer.id
    # the bank's words stay on the entry, and on the statement line
    assert {ln.description for ln in lines} == {"DEPOSIT 1234"}
    assert bt.payee == "DEPOSIT 1234"
    assert _register(client, seed_accounts)[txn.id]["payee"] == "Test Customer"
    _books_agree(db_session)


def test_a_payment_with_a_vendor_picked_is_the_vendors(
    client, db_session, seed_accounts, feed, vendor
):
    line = _lines(client, db_session, feed, [("-50.00", "ACH VENDOR PMT")])[
        "ACH VENDOR PMT"
    ]
    _ok(
        client.post(
            f"/api/banking/transactions/{line['id']}/add",
            json={
                "vendor_id": vendor["id"],
                "category_account_id": seed_accounts["6000"].id,
            },
        )
    )
    bt, txn, lines = _entry(db_session, line)
    assert (txn.description, txn.vendor_id) == ("T-Mobile", vendor["id"])
    assert {ln.description for ln in lines} == {"ACH VENDOR PMT"}
    assert bt.payee == "ACH VENDOR PMT"
    assert _register(client, seed_accounts)[txn.id]["payee"] == "T-Mobile"
    listed = _ok(client.get(f"/api/vendors/{vendor['id']}/bank-entries"))
    assert [e["transaction_id"] for e in listed] == [txn.id]
    # the entry reopens with the vendor as its payee
    detail = _ok(client.get(f"/api/banking/entries/{txn.id}"))
    assert detail["payee"] == "T-Mobile" and detail["vendor_id"] == vendor["id"]
    _books_agree(db_session)


def test_a_typed_name_is_the_payee_and_a_memo_typed_is_kept(
    client, db_session, seed_accounts, feed
):
    line = _lines(client, db_session, feed, [("-60.00", "POS PURCHASE")])[
        "POS PURCHASE"
    ]
    _ok(
        client.post(
            f"/api/banking/transactions/{line['id']}/add",
            json={
                "payee": "Corner Store",
                "memo": "Shop supplies",
                "category_account_id": seed_accounts["6000"].id,
            },
        )
    )
    _, txn, lines = _entry(db_session, line)
    assert txn.description == "Corner Store" and txn.vendor_id is None
    assert {ln.description for ln in lines} == {"Shop supplies"}


def test_a_rule_naming_a_vendor_gives_the_entry_the_vendors_name(
    client, db_session, seed_accounts, feed, vendor
):
    shop = _ok(client.post("/api/classes", json={"name": "Shop"}))
    line = _lines(client, db_session, feed, [("-91.00", "T-MOBILE, PCS SVC   ,91.")])[
        "T-MOBILE, PCS SVC   ,91."
    ]
    _ok(
        client.post(
            "/api/bank-rules",
            json={
                "name": "Phone",
                "pattern": "T-MOBILE",
                "account_id": seed_accounts["6000"].id,
                "vendor_id": vendor["id"],
                "class_id": shop["id"],
            },
        )
    )
    _ok(client.post("/api/bank-rules/apply"))
    added = _ok(client.post(f"/api/banking/transactions/{line['id']}/add", json={}))
    assert added["vendor_name"] == "T-Mobile"
    _, txn, lines = _entry(db_session, line)
    assert (txn.description, txn.vendor_id, txn.class_id) == (
        "T-Mobile",
        vendor["id"],
        shop["id"],
    )
    assert {ln.description for ln in lines} == {"T-MOBILE, PCS SVC   ,91."}
    _books_agree(db_session)


def test_add_all_posts_each_line_with_the_payee_add_would(
    client, db_session, seed_accounts, seed_customer, feed, vendor
):
    got = _lines(
        client,
        db_session,
        feed,
        [("300.00", "DEPOSIT 77"), ("-50.00", "ACH 88"), ("-20.00", "POS 99")],
    )
    income, expense = seed_accounts["4000"].id, seed_accounts["6000"].id
    for payee, body in (
        (
            "DEPOSIT 77",
            {"category_account_id": income, "customer_id": seed_customer.id},
        ),
        ("ACH 88", {"category_account_id": expense, "vendor_id": vendor["id"]}),
        ("POS 99", {"category_account_id": expense, "payee": "Corner Store"}),
    ):
        _ok(client.patch(f"/api/banking/transactions/{got[payee]['id']}", json=body))
    out = _ok(client.post(f"/api/banking/accounts/{feed['id']}/feed/add-all"))
    assert out["added"] == 3 and out["skipped"] == []
    names = {
        p: _entry(db_session, got[p])[1].description
        for p in ("DEPOSIT 77", "ACH 88", "POS 99")
    }
    assert names == {
        "DEPOSIT 77": "Test Customer",
        "ACH 88": "T-Mobile",
        "POS 99": "Corner Store",
    }
    _books_agree(db_session)


def test_a_line_with_no_name_still_posts_the_banks_text(
    client, db_session, seed_accounts, feed
):
    line = _lines(client, db_session, feed, [("-12.00", "SERVICE FEE")])["SERVICE FEE"]
    _ok(
        client.post(
            f"/api/banking/transactions/{line['id']}/add",
            json={"category_account_id": seed_accounts["6000"].id},
        )
    )
    _, txn, lines = _entry(db_session, line)
    assert txn.description == "SERVICE FEE"
    assert {ln.description for ln in lines} == {"SERVICE FEE"}
