"""An item's account may be a balance-sheet account (#1038).

A rental business invoices a security deposit through an item, and the
deposit must credit a liability, not income. The item form's Income and
Expense pickers now offer, under Other accounts, the balance-sheet accounts
a bill line offers (#766); the server already posted an item's line to the
account it names. An item's account may not be one a sub-ledger keeps
(Accounts Receivable, Accounts Payable, Undeposited Funds, Inventory) nor a
bank or card account: those are refused in a sentence. An item that came in
on one by import keeps it when edited."""

from decimal import Decimal

import pytest

from app.models.accounts import Account, AccountType
from app.models.invoices import Invoice
from app.models.items import Item, ItemType
from app.models.transactions import LEDGER_BASIS_OPTION, READ_ALL, TransactionLine

PERIOD = "start_date=2026-10-01&end_date=2026-10-31"


@pytest.fixture
def deposits(db_session, seed_accounts):
    account = Account(
        account_number="2450",
        name="Security Deposits Held",
        account_type=AccountType.LIABILITY,
        detail_type="other_current_liability",
        balance=Decimal("0"),
    )
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def deposit_item(client, deposits):
    r = client.post(
        "/api/items",
        json={
            "name": "Security deposit",
            "item_type": "service",
            "rate": 1000,
            "is_taxable": False,
            "income_account_id": deposits.id,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["income_account_id"] == deposits.id
    return r.json()


def _posted(db_session, transaction_id):
    lines = (
        db_session.query(TransactionLine)
        .filter(TransactionLine.transaction_id == transaction_id)
        .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
        .all()
    )
    return {
        (ln.account_id, Decimal(str(ln.debit)), Decimal(str(ln.credit))) for ln in lines
    }


def _lines(item_id):
    return [
        {"description": "Rent, October", "quantity": 1, "rate": 1500, "line_order": 0},
        {
            "item_id": item_id,
            "description": "Security deposit",
            "quantity": 1,
            "rate": 1000,
            "line_order": 1,
        },
    ]


def test_an_invoice_line_on_the_item_credits_the_liability(
    client, db_session, seed_accounts, seed_customer, deposits, deposit_item
):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-01",
            "tax_rate": 0,
            "lines": _lines(deposit_item["id"]),
        },
    )
    assert r.status_code == 201, r.text
    inv = db_session.get(Invoice, r.json()["id"])
    posted = _posted(db_session, inv.transaction_id)
    assert (deposits.id, Decimal("0"), Decimal("1000.00")) in posted
    assert (seed_accounts["4000"].id, Decimal("0"), Decimal("1500.00")) in posted
    assert (seed_accounts["1100"].id, Decimal("2500.00"), Decimal("0")) in posted

    # Neither the Sales by Item report nor the P&L counts the deposit as income.
    sales = client.get(f"/api/reports/sales-by-item?{PERIOD}").json()
    assert all(row["item_id"] != deposit_item["id"] for row in sales["items"])
    assert sales["total_amount"] == 1500.0
    pl = client.get(f"/api/reports/profit-loss?{PERIOD}").json()
    assert pl["total_income"] == 1500.0
    assert sales["total_amount"] == pl["total_income"]


def test_a_sales_receipt_line_on_the_item_credits_the_liability(
    client, db_session, seed_accounts, seed_customer, deposits, deposit_item
):
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-02",
            "tax_rate": 0,
            "method": "Cash",
            "lines": _lines(deposit_item["id"]),
        },
    )
    assert r.status_code == 201, r.text
    inv = db_session.get(Invoice, r.json()["invoice"]["id"])
    assert (deposits.id, Decimal("0"), Decimal("1000.00")) in _posted(
        db_session, inv.transaction_id
    )
    pl = client.get(f"/api/reports/profit-loss?{PERIOD}").json()
    assert pl["total_income"] == 1500.0
    cash = client.get(f"/api/reports/profit-loss?{PERIOD}&basis=cash").json()
    assert cash["total_income"] == 1500.0


def test_a_credit_memo_line_on_the_item_debits_the_liability(
    client, db_session, seed_accounts, seed_customer, deposits, deposit_item
):
    from app.models.credit_memos import CreditMemo

    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-10-20",
            "tax_rate": 0,
            "lines": [
                {
                    "item_id": deposit_item["id"],
                    "description": "Deposit returned",
                    "quantity": 1,
                    "rate": 1000,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    memo = db_session.get(CreditMemo, r.json()["id"])
    assert (deposits.id, Decimal("1000.00"), Decimal("0")) in _posted(
        db_session, memo.transaction_id
    )
    pl = client.get(f"/api/reports/profit-loss?{PERIOD}").json()
    assert pl["total_income"] == 0.0


@pytest.mark.parametrize(
    "number, words",
    [
        ("1100", "which only invoices and customer payments post to"),
        ("2000", "which only bills and bill payments post to"),
        ("1200", "which holds customer payments until they are deposited"),
        ("1300", "which only an inventory item's purchases and sales post to"),
        ("1000", "a bank account"),
        ("2100", "a card account"),
    ],
)
def test_a_sub_ledger_control_or_a_bank_account_is_refused(
    client, seed_accounts, number, words
):
    account = seed_accounts[number]
    for field, what in (
        ("income_account_id", "income"),
        ("expense_account_id", "expense or cost of goods"),
    ):
        r = client.post(
            "/api/items",
            json={
                "name": f"Item {number} {field}",
                "item_type": "service",
                field: account.id,
            },
        )
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail.startswith(
            f"An item's {what} account can't be {number} {account.name}"
        )
        assert words in detail
    assert client.get("/api/items").json() == []


def test_a_change_to_a_refused_account_is_refused_on_edit(
    client, seed_accounts, deposit_item
):
    r = client.put(
        f"/api/items/{deposit_item['id']}",
        json={"income_account_id": seed_accounts["1100"].id},
    )
    assert r.status_code == 422
    assert "Accounts Receivable" in r.json()["detail"]


def test_sales_tax_payable_and_equity_are_allowed(client, seed_accounts):
    for number in ("2200", "3000"):
        r = client.post(
            "/api/items",
            json={
                "name": f"On {number}",
                "item_type": "service",
                "income_account_id": seed_accounts[number].id,
            },
        )
        assert r.status_code == 201, r.text


def test_an_imported_item_keeps_its_account_when_edited(
    client, db_session, seed_accounts
):
    item = Item(
        name="Imported deposit",
        item_type=ItemType.SERVICE,
        rate=Decimal("0"),
        income_account_id=seed_accounts["1100"].id,
    )
    db_session.add(item)
    db_session.commit()
    r = client.put(
        f"/api/items/{item.id}",
        json={
            "name": "Imported deposit (renamed)",
            "income_account_id": seed_accounts["1100"].id,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["income_account_id"] == seed_accounts["1100"].id


def test_an_unknown_account_is_refused_in_a_sentence(client, seed_accounts):
    r = client.post(
        "/api/items",
        json={"name": "Nowhere", "item_type": "service", "income_account_id": 99999},
    )
    assert r.status_code == 422
    assert "not in the chart of accounts" in r.json()["detail"]
