"""A purchase line never posts to a control account (#785).

2.31.13 (#766) gave the Account picker on a bill line, an expense line, a
card charge, Bill Quick Entry and a memorized bill a group of Other
accounts and left Accounts Receivable, Accounts Payable, Undeposited Funds
and Inventory out of it. The server now refuses them too, on create and on
edit, in one sentence: a bill line on 1200 turned up in Make Deposits as a
payment waiting to be deposited, and one on 1300 raised the Balance Sheet's
Inventory with no item received. Bill Quick Entry posts /api/bills, so the
bill tests are its tests.

A bill or memorized bill already saved on one of them stays as it is and
can still be edited; only a line newly set to a control is refused. An
expense or card charge is changed by Void and Replace, which posts a new
record, so its replacement is refused like a new one. Purchase orders keep
their own accounts.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.bills import Bill, BillLine
from app.models.contacts import Vendor
from app.models.memorized import MemorizedTransaction
from app.models.transactions import Transaction, TransactionLine

TODAY = date.today().isoformat()

CONTROLS = ["1100", "2000", "1200", "1300"]

WHERE = {
    "1100": (
        "1100 Accounts Receivable, which only invoices and customer payments "
        "post to; choose another account."
    ),
    "2000": (
        "2000 Accounts Payable, which only bills and bill payments post to; "
        "choose another account."
    ),
    "1200": (
        "1200 Undeposited Funds, which holds customer payments until they are "
        "deposited; choose another account."
    ),
    "1300": (
        "1300 Inventory, which only an inventory item's purchases and sales "
        "post to; put the item on the line, or choose another account."
    ),
}


def _sentence(label, number):
    return f"{label} can't post to {WHERE[number]}"


@pytest.fixture
def vendor(db_session, seed_accounts):
    v = Vendor(
        name="Ridge Supply",
        is_active=True,
        default_expense_account_id=seed_accounts["6000"].id,
    )
    db_session.add(v)
    db_session.commit()
    return v


def _bill(client, vendor, lines, **over):
    body = {"vendor_id": vendor.id, "date": TODAY, "lines": lines}
    body.update(over)
    return client.post("/api/bills", json=body)


def _expense(client, seed_accounts, **over):
    body = {
        "date": TODAY,
        "payee": "Ridge Supply",
        "paid_from_account_id": seed_accounts["1000"].id,
        "amount": "40.00",
        "expense_account_id": seed_accounts["6000"].id,
    }
    body.update(over)
    return client.post("/api/expenses", json=body)


def _charge(client, seed_accounts, **over):
    body = {
        "date": TODAY,
        "payee": "Ridge Supply",
        "amount": "40",
        "account_id": seed_accounts["6000"].id,
    }
    body.update(over)
    return client.post("/api/cc-charges", json=body)


def _memorized(client, vendor, lines, **over):
    body = {
        "kind": "bill",
        "name": "Monthly loan payment",
        "mode": "remind",
        "frequency": "monthly",
        "start_date": TODAY,
        "vendor_id": vendor.id,
        "lines": lines,
    }
    body.update(over)
    return client.post("/api/memorized", json=body)


def _line(account, amount=100, description="Deposit"):
    return {
        "description": description,
        "account_id": account.id,
        "quantity": 1,
        "rate": amount,
    }


# --- bills (and Bill Quick Entry, which posts the same route) ---------------


@pytest.mark.parametrize("number", CONTROLS)
def test_a_bill_line_on_a_control_account_is_refused(
    client, db_session, seed_accounts, vendor, number
):
    r = _bill(
        client,
        vendor,
        [
            _line(seed_accounts["6000"], 20, "Interest"),
            _line(seed_accounts[number]),
        ],
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == _sentence("Line 2 (Deposit)", number)
    assert db_session.query(Bill).count() == 0


@pytest.mark.parametrize("number", CONTROLS)
def test_a_bill_edit_that_moves_a_line_to_a_control_account_is_refused(
    client, db_session, seed_accounts, vendor, number
):
    bill = _bill(client, vendor, [_line(seed_accounts["6000"])]).json()
    line_id = bill["lines"][0]["id"]
    moved = dict(_line(seed_accounts[number]), id=line_id)
    r = client.put(f"/api/bills/{bill['id']}", json={"lines": [moved]})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == _sentence("Line 1 (Deposit)", number)
    got = client.get(f"/api/bills/{bill['id']}").json()
    assert [ln["account_id"] for ln in got["lines"]] == [seed_accounts["6000"].id]


@pytest.mark.parametrize("number", ["1700", "2400", "3000"])
def test_a_bill_line_on_an_escrow_loan_or_equity_account_still_posts(
    client, seed_accounts, vendor, number
):
    r = _bill(client, vendor, [_line(seed_accounts[number], 500, "Principal")])
    assert r.status_code == 201, r.text
    assert r.json()["lines"][0]["account_id"] == seed_accounts[number].id


def _saved_on_undeposited_funds(client, db_session, seed_accounts, vendor):
    """A bill saved on 1200 before 2.31.14: entered on 6000, then its line and
    journal moved to 1200 with the ORM, the way an older release or the API
    left it."""
    bill = _bill(
        client,
        vendor,
        [
            _line(seed_accounts["6000"], 20, "Interest"),
            _line(seed_accounts["6100"], 100, "Deposit"),
        ],
    ).json()
    old, new = seed_accounts["6100"].id, seed_accounts["1200"].id
    db_session.query(BillLine).filter_by(bill_id=bill["id"], account_id=old).update(
        {"account_id": new}
    )
    txn_ids = [
        t.id for t in db_session.query(Transaction).filter_by(source_type="bill")
    ]
    db_session.query(TransactionLine).filter(
        TransactionLine.transaction_id.in_(txn_ids),
        TransactionLine.account_id == old,
    ).update({"account_id": new}, synchronize_session=False)
    db_session.commit()
    return client.get(f"/api/bills/{bill['id']}").json()


def test_a_bill_already_saved_on_undeposited_funds_is_still_readable_and_editable(
    client, db_session, seed_accounts, vendor
):
    bill = _saved_on_undeposited_funds(client, db_session, seed_accounts, vendor)
    undeposited = seed_accounts["1200"].id
    assert undeposited in [ln["account_id"] for ln in bill["lines"]]
    # a field that posts nothing
    r = client.put(f"/api/bills/{bill['id']}", json={"notes": "Call about it"})
    assert r.status_code == 200, r.text
    # a header change that re-posts the bill from its stored lines
    r = client.put(f"/api/bills/{bill['id']}", json={"bill_number": "RS-2"})
    assert r.status_code == 200, r.text
    # the edit window sends every line back with its id: the old line keeps
    # its account, the other line's amount changes
    lines = [
        {
            "id": ln["id"],
            "description": ln["description"],
            "account_id": ln["account_id"],
            "quantity": 1,
            "rate": 25 if ln["description"] == "Interest" else ln["rate"],
        }
        for ln in bill["lines"]
    ]
    r = client.put(f"/api/bills/{bill['id']}", json={"lines": lines})
    assert r.status_code == 200, r.text
    got = client.get(f"/api/bills/{bill['id']}").json()
    assert Decimal(str(got["total"])) == Decimal("125")
    assert {ln["description"]: ln["account_id"] for ln in got["lines"]} == {
        "Interest": seed_accounts["6000"].id,
        "Deposit": undeposited,
    }
    # a new line on it is refused like any other
    r = client.put(
        f"/api/bills/{bill['id']}",
        json={"lines": lines + [_line(seed_accounts["1200"], 5, "More")]},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == _sentence("Line 3 (More)", "1200")


# --- expenses ----------------------------------------------------------------


@pytest.mark.parametrize("number", CONTROLS)
def test_an_expense_on_a_control_account_is_refused(
    client, db_session, seed_accounts, number
):
    before = db_session.query(Transaction).count()
    r = _expense(client, seed_accounts, expense_account_id=seed_accounts[number].id)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == _sentence("The expense", number)
    r = _expense(
        client,
        seed_accounts,
        expense_account_id=None,
        lines=[
            {"account_id": seed_accounts["6000"].id, "amount": "10.00"},
            {
                "account_id": seed_accounts[number].id,
                "amount": "30.00",
                "description": "Deposit",
            },
        ],
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == _sentence("Line 2 (Deposit)", number)
    assert db_session.query(Transaction).count() == before


@pytest.mark.parametrize("number", CONTROLS)
def test_replacing_an_expense_with_a_line_on_a_control_account_is_refused(
    client, db_session, seed_accounts, number
):
    exp = _expense(client, seed_accounts).json()
    r = client.post(
        f"/api/expenses/{exp['id']}/replace",
        json={
            "date": TODAY,
            "payee": "Ridge Supply",
            "paid_from_account_id": seed_accounts["1000"].id,
            "amount": "40.00",
            "expense_account_id": seed_accounts[number].id,
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == _sentence("The expense", number)
    # nothing changed: the expense is not void and not replaced
    got = client.get(f"/api/expenses/{exp['id']}").json()
    assert got["expense_account_id"] == seed_accounts["6000"].id
    assert not got.get("replaced_by_id")


@pytest.mark.parametrize("number", ["1700", "2400", "3000"])
def test_an_expense_on_an_escrow_loan_or_equity_account_still_posts(
    client, seed_accounts, number
):
    r = _expense(client, seed_accounts, expense_account_id=seed_accounts[number].id)
    assert r.status_code == 201, r.text


# --- card charges ------------------------------------------------------------


@pytest.mark.parametrize("number", CONTROLS)
def test_a_card_charge_on_a_control_account_is_refused(
    client, db_session, seed_accounts, number
):
    before = db_session.query(Transaction).count()
    r = _charge(client, seed_accounts, account_id=seed_accounts[number].id)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == _sentence("The card charge", number)
    assert db_session.query(Transaction).count() == before


@pytest.mark.parametrize("number", CONTROLS)
def test_replacing_a_card_charge_with_a_control_account_is_refused(
    client, db_session, seed_accounts, number
):
    cid = _charge(client, seed_accounts).json()["transaction_id"]
    r = client.post(
        f"/api/cc-charges/{cid}/replace",
        json={
            "date": TODAY,
            "payee": "Ridge Supply",
            "amount": "40",
            "account_id": seed_accounts[number].id,
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == _sentence("The card charge", number)
    assert {x["id"]: x["status"] for x in client.get("/api/cc-charges").json()}[
        cid
    ] != "void"


@pytest.mark.parametrize("number", ["1700", "2400", "3000"])
def test_a_card_charge_on_an_escrow_loan_or_equity_account_still_posts(
    client, seed_accounts, number
):
    r = _charge(client, seed_accounts, account_id=seed_accounts[number].id)
    assert r.status_code == 201, r.text


# --- memorized bills ---------------------------------------------------------


@pytest.mark.parametrize("number", CONTROLS)
def test_a_memorized_bill_line_on_a_control_account_is_refused(
    client, db_session, seed_accounts, vendor, number
):
    r = _memorized(
        client, vendor, [{"account_id": seed_accounts[number].id, "amount": 100}]
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == _sentence("Line 1", number)
    assert db_session.query(MemorizedTransaction).count() == 0


@pytest.mark.parametrize("number", CONTROLS)
def test_a_memorized_bill_edit_that_moves_a_line_to_a_control_is_refused(
    client, seed_accounts, vendor, number
):
    mem = _memorized(
        client, vendor, [{"account_id": seed_accounts["6000"].id, "amount": 100}]
    ).json()
    r = client.put(
        f"/api/memorized/{mem['id']}",
        json={
            "lines": [
                {
                    "account_id": seed_accounts[number].id,
                    "amount": 100,
                    "description": "Deposit",
                }
            ]
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == _sentence("Line 1 (Deposit)", number)
    got = client.get(f"/api/memorized/{mem['id']}").json()
    assert [ln["account_id"] for ln in got["lines"]] == [seed_accounts["6000"].id]


def test_a_memorized_loan_payment_still_splits_between_the_loan_and_interest(
    client, seed_accounts, vendor
):
    r = _memorized(
        client,
        vendor,
        [
            {"account_id": seed_accounts["2400"].id, "amount": 500},
            {"account_id": seed_accounts["6000"].id, "amount": 20},
        ],
    )
    assert r.status_code == 201, r.text


def test_a_memorized_bill_already_saved_on_undeposited_funds_can_still_be_edited(
    client, db_session, seed_accounts, vendor
):
    mem = _memorized(
        client, vendor, [{"account_id": seed_accounts["6100"].id, "amount": 100}]
    ).json()
    row = db_session.get(MemorizedTransaction, mem["id"])
    row.lines[0].account_id = seed_accounts["1200"].id
    db_session.commit()
    r = client.put(f"/api/memorized/{mem['id']}", json={"name": "Renamed"})
    assert r.status_code == 200, r.text
    # the window sends its lines back as they are
    r = client.put(
        f"/api/memorized/{mem['id']}",
        json={"lines": [{"account_id": seed_accounts["1200"].id, "amount": 120}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Renamed"


# --- purchase orders ---------------------------------------------------------


def test_a_purchase_order_still_turns_into_a_bill_on_its_cost_account(
    client, seed_accounts, vendor
):
    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor.id,
            "date": TODAY,
            "lines": [{"description": "Lumber", "quantity": 2, "rate": 50}],
        },
    )
    assert po.status_code == 201, po.text
    po = po.json()
    r = client.post(
        f"/api/purchase-orders/{po['id']}/convert-to-bill",
        json={
            "lines": [
                {
                    "line_id": po["lines"][0]["id"],
                    "account_id": seed_accounts["5100"].id,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    bill = client.get(f"/api/bills/{r.json()['bill_id']}").json()
    assert [ln["account_id"] for ln in bill["lines"]] == [seed_accounts["5100"].id]
