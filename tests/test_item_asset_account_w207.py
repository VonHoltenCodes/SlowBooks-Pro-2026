"""An item's inventory asset account is checked like its income and expense
accounts (W-207, 2.32.0 gate round 23).

Round 22 (W-196) refused, for an item's income and expense accounts, every
account a Books Check row ties to a sub-ledger. The item's third account,
the inventory asset account, was left out: the form offered 1100 Accounts
Receivable and 1200 Undeposited Funds there and the API took them, and one
bill of 10 at 20.00 and one invoice of 2 at 50.00 on such an item put Books
Check out (A/R -160.00, Inventory -200.00). The test walks every account of
a construction company's chart through an item's asset account, create and
update: each refused one is refused in a sentence and is on the form's
list; each allowed one (an Other Current Asset account no sub-ledger but
the stock keeps, 1300 Inventory among them) takes that bill and invoice
with the trial balance even and Books Check clean."""

import pytest

from app.models.accounts import Account
from app.models.items import Item, ItemType
from app.services.books_check import tied_accounts
from tests.test_inventory import _seed_vendor
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
    # A second stock account, as a business keeping raw materials apart.
    _ok(
        client.post(
            "/api/accounts",
            json={
                "account_number": "1310",
                "name": "Inventory - Raw Materials",
                "account_type": "asset",
                "detail_type": "other_current_asset",
            },
        ),
        201,
    )
    db_session.commit()
    return db_session.query(Account).order_by(Account.account_number).all()


def _stock_item(client, name, asset_id):
    return client.post(
        "/api/items",
        json={
            "name": name,
            "item_type": "product",
            "is_taxable": False,
            "rate": "50.00",
            "cost": "20.00",
            "track_inventory": True,
            "asset_account_id": asset_id,
        },
    )


def _buy_and_sell(client, vendor_id, customer_id, item_id, n):
    _ok(
        client.post(
            "/api/bills",
            json={
                "vendor_id": vendor_id,
                "bill_number": f"W207-{n}",
                "date": "2026-10-01",
                "tax_rate": "0",
                "lines": [{"item_id": item_id, "quantity": "10", "rate": "20.00"}],
            },
        ),
        201,
    )
    _ok(
        client.post(
            "/api/invoices",
            json={
                "customer_id": customer_id,
                "date": "2026-10-02",
                "tax_rate": 0,
                "lines": [{"item_id": item_id, "quantity": 2, "rate": "50.00"}],
            },
        ),
        201,
    )


def test_every_account_as_an_items_asset_account(
    client, db_session, chart, seed_customer
):
    vendor = _seed_vendor(db_session)
    listed = {
        r["account_id"]: r["reason"]
        for r in _ok(client.get("/api/items/refused-accounts?field=asset_account_id"))
    }
    plain = _ok(
        client.post("/api/items", json={"name": "Plain", "item_type": "product"}),
        201,
    )
    allowed, refused = [], []
    for n, account in enumerate(chart):
        label = f"{account.account_number} {account.name}"
        r = _stock_item(client, f"Stock on {label}", account.id)
        put = client.put(
            f"/api/items/{plain['id']}", json={"asset_account_id": account.id}
        )
        if r.status_code == 422:
            detail = r.json()["detail"]
            assert detail.startswith("An item's inventory asset account can't be ")
            assert f"can't be {account.account_number} " in detail, detail
            # the form's list gives the same reason (the name aside)
            reason = listed.get(account.id) or ""
            assert reason.split(": ")[-1] == detail.split(": ")[-1], (label, reason)
            assert put.status_code == 422 and put.json()["detail"] == detail
            refused.append(account.account_number)
            continue
        assert r.status_code == 201, (label, r.text)
        assert account.id not in listed, label
        assert put.status_code == 200, (label, put.text)
        allowed.append(account.account_number)
        _buy_and_sell(client, vendor.id, seed_customer.id, r.json()["id"], n)
        books_agree(client)
    # What the round found, and the rest of what Books Check ties.
    assert {"1000", "1100", "1150", "1200", "1500", "2000", "2200", "3000"} <= set(
        refused
    ), refused
    assert {"1300", "1310"} <= set(allowed), allowed
    for number in allowed:
        acct = next(a for a in chart if a.account_number == number)
        detail = getattr(acct.detail_type, "value", acct.detail_type)
        assert acct.account_type.value == "asset" and detail == "other_current_asset"
        assert not acct.bank_kind


def test_an_account_an_item_sells_through_is_not_a_stock_account(
    client, db_session, chart
):
    other_asset = _ok(
        client.post(
            "/api/accounts",
            json={
                "account_number": "1410",
                "name": "Customer Deposits Held",
                "account_type": "asset",
                "detail_type": "other_current_asset",
            },
        ),
        201,
    )
    _ok(
        client.post(
            "/api/items",
            json={
                "name": "Deposit Line",
                "item_type": "service",
                "income_account_id": other_asset["id"],
            },
        ),
        201,
    )
    r = _stock_item(client, "Widget", other_asset["id"])
    assert r.status_code == 422
    assert "Deposit Line" in r.json()["detail"]


def test_an_item_an_import_gave_receivables_keeps_it(client, db_session, chart):
    ar = next(a for a in chart if a.account_number == "1100")
    item = Item(
        name="Imported widget",
        item_type=ItemType.PRODUCT,
        track_inventory=True,
        asset_account_id=ar.id,
    )
    db_session.add(item)
    db_session.commit()
    kept = _ok(
        client.put(
            f"/api/items/{item.id}",
            json={"name": "Imported widget (kept)", "asset_account_id": ar.id},
        )
    )
    assert kept["asset_account_id"] == ar.id
    # still listed as refused for a new choice; the form keeps the item's own
    listed = _ok(client.get("/api/items/refused-accounts?field=asset_account_id"))
    assert ar.id in {r["account_id"] for r in listed}
    assert tied_accounts(db_session)[ar.id][0] == "Accounts Receivable"
