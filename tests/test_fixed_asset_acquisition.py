"""A registered fixed asset's purchase is in the books.

Exploratory 2.17.3 (skytech, W-M19): registering a $12,000 asset posted
nothing to 1500 Equipment, but depreciation then credited 1510, so the
balance sheet showed negative net equipment; and a salvage value greater
than the cost was accepted. Registering now says how the purchase reached
the books — paid from a bank or card account, owned before the books began
(against Opening Balance Equity), bought on a bill or expense already
entered (its cost moves from the expense account), or already in the books
— and salvage above cost is refused."""

import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.transactions import Transaction
from app.services.bank_register import gl_balance

JS = (Path(__file__).resolve().parents[1] / "app/static/js/fixed_assets.js").read_text(
    encoding="utf-8"
)


@pytest.fixture
def equipment_type(client, seed_accounts):
    """The default type a new company gets: 1500 / 1510 / 6810, five years."""
    [t] = client.get("/api/fixed-assets/types").json()
    assert t["asset_account_id"] == seed_accounts["1500"].id
    return t


def _register(client, equipment_type, acquisition=None, **fields):
    body = {
        "name": "Vinyl plotter",
        "asset_type_id": equipment_type["id"],
        "purchase_date": "2026-01-15",
        "purchase_price": "12000",
        "salvage_value": "2000",
        **fields,
    }
    if acquisition is not None:
        body["acquisition"] = acquisition
    return client.post("/api/fixed-assets", json=body)


def _bal(db_session, account):
    db_session.expire_all()
    return gl_balance(db_session, account.id)


def _postings(db_session, asset_id):
    return (
        db_session.query(Transaction)
        .filter(
            Transaction.source_id == asset_id,
            Transaction.source_type.in_(("asset_acquisition", "opening_balance")),
        )
        .all()
    )


def test_paid_from_checking_posts_the_purchase_and_net_equipment_stays_positive(
    client, db_session, seed_accounts, equipment_type
):
    r = _register(
        client,
        equipment_type,
        {
            "method": "paid_from",
            "account_id": seed_accounts["1000"].id,
            "reference": "1052",
        },
    )
    assert r.status_code == 201, r.text
    asset = r.json()
    assert asset["posted"] is True
    [txn] = _postings(db_session, asset["id"])
    assert txn.source_type == "asset_acquisition" and txn.reference == "1052"
    assert _bal(db_session, seed_accounts["1500"]) == Decimal("12000")
    assert _bal(db_session, seed_accounts["1000"]) == Decimal("-12000")
    # the register shows the check number
    reg = client.get(
        f"/api/banking/check-register?account_id={seed_accounts['1000'].id}"
    ).json()
    assert reg["entries"][0]["reference"] == "1052"

    # (12,000 - 2,000) / 60 x 8 = 1,333.33, as the tester worked it out
    r = client.post(
        "/api/fixed-assets/run-depreciation", json={"run_date": "2026-09-15"}
    )
    assert r.json()["total"] == 1333.33
    net = _bal(db_session, seed_accounts["1500"]) + _bal(
        db_session, seed_accounts["1510"]
    )
    assert net == Decimal("10666.67")
    bs = client.get("/api/reports/balance-sheet?as_of_date=2026-09-30").json()
    assert (
        abs(bs["total_assets"] - (bs["total_liabilities"] + bs["total_equity"])) < 0.01
    )


def test_salvage_above_cost_and_a_zero_price_are_refused(
    client, db_session, seed_accounts, equipment_type
):
    r = _register(client, equipment_type, salvage_value="12500")
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "Salvage value can't be more than the purchase price ($12,000.00)."
    )
    r = _register(client, equipment_type, purchase_price="0", salvage_value="0")
    assert r.status_code == 400 and "more than $0.00" in r.json()["detail"]
    r = _register(client, equipment_type, salvage_value="-1")
    assert r.status_code == 400
    ok = _register(client, equipment_type).json()
    r = client.put(f"/api/fixed-assets/{ok['id']}", json={"salvage_value": "13000"})
    assert r.status_code == 400
    csv = (
        "name,asset_type,purchase_date,purchase_price,salvage_value,description\n"
        "Router,Equipment,2026-02-01,500,900,\n"
        "Switch,Equipment,2026-02-01,300,0,\n"
    )
    r = client.post(
        "/api/fixed-assets/import-csv",
        files={"file": ("assets.csv", csv.encode(), "text/csv")},
    )
    out = r.json()
    assert out["imported"] == 1
    assert (
        out["errors"][0]["row"] == 2 and "salvage_value" in out["errors"][0]["message"]
    )


def test_owned_before_the_books_began_posts_an_opening_balance(
    client, db_session, seed_accounts, equipment_type
):
    r = _register(
        client,
        equipment_type,
        {
            "method": "opening_balance",
            "as_of": "2026-01-01",
            "accumulated_depreciation": "12000",
        },
        name="Box truck",
        purchase_date="2022-03-01",
        purchase_price="30000",
        salvage_value="0",
    )
    assert r.status_code == 201, r.text
    asset = r.json()
    assert (
        asset["accumulated_depreciation"] == 12000.0 and asset["book_value"] == 18000.0
    )
    [txn] = _postings(db_session, asset["id"])
    assert txn.source_type == "opening_balance" and txn.date.isoformat() == "2026-01-01"
    credits = {ln.account.account_number: ln.credit for ln in txn.lines if ln.credit}
    assert credits == {"1510": Decimal("12000"), "3900": Decimal("18000")}
    # the books depreciate it from the day they began, not from 2022
    r = client.post(
        "/api/fixed-assets/run-depreciation", json={"run_date": "2026-07-01"}
    )
    assert r.json()["total"] == 3000.0  # 30,000 / 60 x 6
    # refusals
    too_much = _register(
        client,
        equipment_type,
        {"method": "opening_balance", "accumulated_depreciation": "31000"},
        purchase_price="30000",
        salvage_value="0",
    )
    assert too_much.status_code == 400 and "$30,000.00" in too_much.json()["detail"]
    backwards = _register(
        client,
        equipment_type,
        {"method": "opening_balance", "as_of": "2025-01-01"},
        purchase_date="2026-01-15",
    )
    assert backwards.status_code == 400


def test_bought_on_a_bill_moves_the_cost_out_of_the_expense_account(
    client, db_session, seed_accounts, equipment_type
):
    vendor = client.post("/api/vendors", json={"name": "Sign Supply"}).json()
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "SS-900",
            "date": "2026-01-15",
            "lines": [
                {
                    "description": "Vinyl plotter",
                    "quantity": 1,
                    "rate": 12000,
                    "account_id": seed_accounts["6800"].id,
                }
            ],
        },
    ).json()
    assert _bal(db_session, seed_accounts["6800"]) == Decimal("12000")
    r = _register(client, equipment_type, {"method": "bill", "bill_id": bill["id"]})
    assert r.status_code == 201, r.text
    [txn] = _postings(db_session, r.json()["id"])
    assert txn.date.isoformat() == "2026-01-15" and txn.reference == "SS-900"
    assert _bal(db_session, seed_accounts["6800"]) == Decimal("0")
    assert _bal(db_session, seed_accounts["1500"]) == Decimal("12000")
    # the bill has nothing left to capitalize
    again = _register(
        client,
        equipment_type,
        {"method": "bill", "bill_id": bill["id"]},
        purchase_price="10",
        salvage_value="0",
    )
    assert again.status_code == 400 and "$0.00" in again.json()["detail"]


def test_one_expense_can_buy_two_assets_but_not_more_than_it_cost(
    client, db_session, seed_accounts, equipment_type
):
    expense = client.post(
        "/api/expenses",
        json={
            "date": "2026-02-10",
            "payee": "Computer Depot",
            "expense_account_id": seed_accounts["6800"].id,
            "paid_from_account_id": seed_accounts["1000"].id,
            "amount": "2000",
            "reference": "4411",
        },
    ).json()
    link = {"method": "expense", "expense_id": expense["id"]}
    first = _register(
        client, equipment_type, link, purchase_price="1500", salvage_value="0"
    )
    assert first.status_code == 201, first.text
    over = _register(
        client, equipment_type, link, purchase_price="600", salvage_value="0"
    )
    assert over.status_code == 400 and "$500.00" in over.json()["detail"]
    second = _register(
        client, equipment_type, link, purchase_price="500", salvage_value="0"
    )
    assert second.status_code == 201, second.text
    assert _bal(db_session, seed_accounts["6800"]) == Decimal("0")
    assert _bal(db_session, seed_accounts["1500"]) == Decimal("2000")


def test_already_in_the_books_is_checked_against_the_ledger(
    client, db_session, seed_accounts, equipment_type
):
    r = _register(client, equipment_type, {"method": "in_books"})
    assert r.status_code == 400
    assert "holds $0.00 in the books" in r.json()["detail"]
    assert client.get("/api/fixed-assets").json() == []  # nothing left behind
    ob = client.post(
        "/api/opening-balances",
        json={
            "date": "2025-12-31",
            "lines": [{"account_id": seed_accounts["1500"].id, "amount": "12000"}],
            "auto_balance_account_id": seed_accounts["3000"].id,
        },
    )
    assert ob.status_code == 200, ob.text
    r = _register(client, equipment_type, {"method": "in_books"})
    assert r.status_code == 201, r.text
    assert r.json()["posted"] is False
    assert _bal(db_session, seed_accounts["1500"]) == Decimal("12000")
    # and it can't be posted a second time by mistake
    r2 = client.post(
        f"/api/fixed-assets/{r.json()['id']}/post-purchase",
        json={"method": "paid_from", "account_id": seed_accounts["1000"].id},
    )
    assert r2.status_code == 400 and "count it twice" in r2.json()["detail"]


def test_an_asset_registered_before_this_can_have_its_purchase_posted(
    client, db_session, seed_accounts, equipment_type
):
    legacy = _register(client, equipment_type).json()  # no acquisition: as before
    assert legacy["posted"] is False and _postings(db_session, legacy["id"]) == []
    r = client.post(
        f"/api/fixed-assets/{legacy['id']}/post-purchase",
        json={"method": "paid_from", "account_id": seed_accounts["1000"].id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["posted"] is True
    assert _bal(db_session, seed_accounts["1500"]) == Decimal("12000")
    again = client.post(
        f"/api/fixed-assets/{legacy['id']}/post-purchase",
        json={"method": "paid_from", "account_id": seed_accounts["1000"].id},
    )
    assert again.status_code == 400 and "already in the books" in again.json()["detail"]
    # a posted purchase keeps its price; the name can still change
    r = client.put(f"/api/fixed-assets/{legacy['id']}", json={"purchase_price": "9000"})
    assert r.status_code == 400
    r = client.put(f"/api/fixed-assets/{legacy['id']}", json={"name": "Plotter #1"})
    assert r.status_code == 200 and r.json()["name"] == "Plotter #1"


def test_paid_from_needs_a_bank_or_card_account(client, seed_accounts, equipment_type):
    r = _register(
        client,
        equipment_type,
        {"method": "paid_from", "account_id": seed_accounts["6000"].id},
    )
    assert r.status_code == 400
    assert (
        r.json()["detail"] == "Pick the bank or card account the asset was paid from."
    )


def test_the_register_form_asks_how_it_was_paid_for():
    fields = re.search(
        r"async _acquisitionFields\(includeInBooks\).*?\n    },\n", JS, re.S
    ).group(0)
    for method in ("paid_from", "document", "opening_balance", "in_books"):
        assert f'value="{method}"' in fields, method
    save = re.search(r"async saveAsset\(e\).*?\n    },\n", JS, re.S).group(0)
    assert "acquisition," in save and "Salvage value can't be more" in save
    assert "FixedAssetsPage.showPostPurchaseForm(${a.id})" in JS
    assert "/post-purchase`" in JS
