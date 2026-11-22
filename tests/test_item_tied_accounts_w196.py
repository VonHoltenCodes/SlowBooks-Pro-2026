"""An item may not post to an account Books Check ties to a sub-ledger
(W-196, 2.32.0 gate round 22).

#1038 let an item post to a balance-sheet account and refused Accounts
Receivable, Accounts Payable, Undeposited Funds, Inventory and the bank and
card accounts. 2200 Sales Tax Payable and 2010 Accrued Job Costs slipped
through, and one invoice line on such an item put Books Check's sales-tax
row (-77.00) or accrued-job-costs row (-55.00) out by the line. The set is
now read from Books Check itself (books_check.tied_accounts): every account
one of its rows sets beside a sub-ledger. The test walks every account of a
construction company's chart through an item and an invoice: each refused
one is refused in a sentence (and an item an import gave it still invoices,
as #1038 keeps it), and each allowed one invoices with Books Check clean."""

from decimal import Decimal

import pytest

from app.models.accounts import Account
from app.models.items import Item, ItemType
from app.routes.items import _ITEM_ACCOUNT_CONTROLS
from app.services import control_accounts
from app.services.books_check import tied_accounts
from tests.test_void_revenue_cost_codes_new262 import books_agree


def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


@pytest.fixture
def chart(client, db_session, seed_accounts, seed_customer):
    _ok(client.put("/api/settings", json={"company_type": "construction"}))
    _ok(client.post("/api/construction/setup"))
    from app.services.accrued_job_costs import ensure_account

    ensure_account(db_session)
    db_session.commit()
    return db_session.query(Account).order_by(Account.account_number).all()


def _refused_by_rule(account: Account) -> bool:
    detail = getattr(account.detail_type, "value", account.detail_type)
    number = str(account.account_number or "")
    return bool(
        account.bank_kind
        or detail in ("accounts_receivable", "accounts_payable")
        or (
            number in _ITEM_ACCOUNT_CONTROLS
            and control_accounts.is_control(number, account.account_type)
        )
    )


def _invoice(client, customer_id, item_id, rate="55.00"):
    return _ok(
        client.post(
            "/api/invoices",
            json={
                "customer_id": customer_id,
                "date": "2026-10-01",
                "tax_rate": 0,
                "lines": [{"item_id": item_id, "quantity": 1, "rate": rate}],
            },
        ),
        201,
    )


def test_the_tied_set_is_books_checks_rows(client, db_session, chart):
    tied = tied_accounts(db_session)
    by_number = {
        db_session.get(Account, aid).account_number: label
        for aid, (label, _keeper) in tied.items()
    }
    for number, label in (
        ("1100", "Accounts Receivable"),
        ("2000", "Accounts Payable"),
        ("1200", "Undeposited Funds"),
        ("1300", "Inventory"),
        ("2200", "Sales Tax Payable"),
        ("2010", "Accrued Job Costs"),
        ("1150", "Retainage Receivable"),
        ("2050", "Retainage Payable"),
    ):
        assert by_number.get(number) == label, (number, by_number)
    assert "Payroll Liabilities" in by_number.values()
    # the form reads the same list
    listed = _ok(client.get("/api/items/refused-accounts"))
    assert {r["account_id"] for r in listed} == set(tied)


def test_every_account_is_refused_or_invoices_with_books_check_clean(
    client, db_session, chart, seed_customer
):
    tied = tied_accounts(db_session)
    refused, allowed = [], []
    for account in chart:
        label = f"{account.account_number} {account.name}"
        must_refuse = _refused_by_rule(account) or account.id in tied
        for field in ("income_account_id", "expense_account_id"):
            r = client.post(
                "/api/items",
                json={
                    "name": f"On {label} {field}",
                    "item_type": "service",
                    "is_taxable": False,
                    field: account.id,
                },
            )
            if must_refuse:
                assert r.status_code == 422, (label, field, r.text)
                detail = r.json()["detail"]
                assert detail.startswith("An item's ") and label in detail, detail
                if account.id in tied and not _refused_by_rule(account):
                    assert "Books Check ties it to" in detail, detail
            else:
                assert r.status_code == 201, (label, field, r.text)
        if must_refuse:
            refused.append(account)
            continue
        allowed.append(account)
        item = (
            db_session.query(Item)
            .filter(Item.name == f"On {label} income_account_id")
            .one()
        )
        _invoice(client, seed_customer.id, item.id)
        books_agree(client)
    assert {a.account_number for a in refused} >= {
        "1000",
        "1100",
        "1150",
        "1200",
        "1300",
        "2000",
        "2010",
        "2050",
        "2100",
        "2200",
    }
    assert {a.account_number for a in allowed} >= {"2210", "3000", "4000"}

    # An item an import already gave a tied account still invoices (#1038
    # keeps it), and that line is exactly what Books Check then flags; the
    # void takes it back off.
    for account in refused:
        if account.id not in tied or _refused_by_rule(account):
            continue
        item = Item(
            name=f"Imported on {account.account_number}",
            item_type=ItemType.SERVICE,
            rate=Decimal("0"),
            is_taxable=False,
            income_account_id=account.id,
        )
        db_session.add(item)
        db_session.commit()
        kept = _ok(
            client.put(
                f"/api/items/{item.id}",
                json={"name": f"Imported on {account.account_number} (kept)"},
            )
        )
        assert kept["income_account_id"] == account.id
        inv = _invoice(client, seed_customer.id, item.id)
        check = _ok(client.get("/api/reports/books-check"))
        assert check["differences"] >= 1, account.account_number
        _ok(client.post(f"/api/invoices/{inv['id']}/void"))
        books_agree(client)
