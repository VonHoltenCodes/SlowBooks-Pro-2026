"""Vendor credits — the AP-side counterpart of a customer credit memo
(issue #129, CimarronSiteServices).

A bill posts DR Expense / CR A/P. A vendor credit is its mirror. Applying
one to a bill posts nothing: A/P already moved when the credit was issued.

The tests that matter here are the ones that tie the vendor sub-ledger to
account 2000, because the reporter's own objection to using a journal entry
was that a JE moves one and not the other.
"""

from decimal import Decimal

import pytest

from app.models.accounts import Account
from app.models.items import InventoryMovement, MovementType
from app.models.transactions import Transaction, TransactionLine


@pytest.fixture
def vendor(client):
    r = client.post("/api/vendors", json={"name": "Acme Supply"})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _bill(client, vendor, seed_accounts, amount="1000.00", day=1):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": f"B-{day:03d}",
            "date": f"2026-04-{day:02d}",
            "due_date": f"2026-04-{day:02d}",
            "lines": [
                {
                    "description": "materials",
                    "quantity": 1,
                    "rate": amount,
                    "account_id": seed_accounts["6000"].id,
                    "line_order": 0,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _credit(client, vendor, seed_accounts, amount="300.00", day=5, **extra):
    body = {
        "vendor_id": vendor["id"],
        "date": f"2026-04-{day:02d}",
        "lines": [
            {
                "description": "returned materials",
                "quantity": 1,
                "rate": amount,
                "account_id": seed_accounts["6000"].id,
                "line_order": 0,
            }
        ],
    }
    body.update(extra)
    r = client.post("/api/vendor-credits", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _gl(db, account_id):
    """Signed balance of one account: debits minus credits."""
    lines = (
        db.query(TransactionLine).filter(TransactionLine.account_id == account_id).all()
    )
    return sum(Decimal(str(x.debit or 0)) - Decimal(str(x.credit or 0)) for x in lines)


# ── The document posts the mirror of a bill ──────────────────────────────


def test_a_vendor_credit_debits_ap_and_credits_the_expense(
    client, db_session, seed_accounts, vendor
):
    vc = _credit(client, vendor, seed_accounts, "300.00")
    assert vc["credit_number"].startswith("VC-")
    assert vc["status"] == "issued"
    assert Decimal(vc["balance_remaining"]) == Decimal("300.00")

    txn = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "vendor_credit",
            Transaction.source_id == vc["id"],
        )
        .one()
    )
    by_acct = {ln.account_id: ln for ln in txn.lines}
    assert by_acct[seed_accounts["2000"].id].debit == Decimal("300.00")
    assert by_acct[seed_accounts["6000"].id].credit == Decimal("300.00")
    assert sum(ln.debit for ln in txn.lines) == sum(ln.credit for ln in txn.lines)


def test_a_bill_and_a_credit_that_reverses_it_net_to_nothing(
    client, db_session, seed_accounts, vendor
):
    _bill(client, vendor, seed_accounts, "1000.00")
    _credit(client, vendor, seed_accounts, "1000.00")
    assert _gl(db_session, seed_accounts["2000"].id) == Decimal("0")
    assert _gl(db_session, seed_accounts["6000"].id) == Decimal("0")


def test_the_tax_on_a_credit_comes_off_the_lines_not_sales_tax_payable(
    client, db_session, seed_accounts, vendor
):
    # The mirror of a bill: tax paid on a purchase is part of what the goods
    # cost, so the tax coming back reduces the same account. Crediting 2200
    # would add to the sales tax the business owes the state.
    vc = _credit(client, vendor, seed_accounts, "100.00", tax_rate=0.10)
    assert Decimal(vc["tax_amount"]) == Decimal("10.00")
    assert Decimal(vc["total"]) == Decimal("110.00")
    txn = (
        db_session.query(Transaction)
        .filter(Transaction.source_id == vc["id"])
        .filter(Transaction.source_type == "vendor_credit")
        .one()
    )
    by_acct = {ln.account_id: ln for ln in txn.lines}
    assert seed_accounts["2200"].id not in by_acct
    assert by_acct[seed_accounts["6000"].id].credit == Decimal("110.00")
    assert by_acct[seed_accounts["2000"].id].debit == Decimal("110.00")


def test_a_line_with_no_account_takes_the_vendors_default(
    client, db_session, seed_accounts
):
    flour = client.post(
        "/api/vendors",
        json={
            "name": "Cascade Flour Mill",
            "default_expense_account_id": seed_accounts["5100"].id,
        },
    ).json()
    r = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": flour["id"],
            "date": "2026-04-05",
            "lines": [{"description": "credit", "quantity": 1, "rate": "50.00"}],
        },
    )
    assert r.status_code == 201, r.text
    txn = (
        db_session.query(Transaction)
        .filter(Transaction.source_id == r.json()["id"])
        .filter(Transaction.source_type == "vendor_credit")
        .one()
    )
    by_acct = {ln.account_id: ln for ln in txn.lines}
    assert by_acct[seed_accounts["5100"].id].credit == Decimal("50.00")
    assert seed_accounts["6000"].id not in by_acct


def test_a_line_nothing_names_an_account_for_is_refused_not_booked_to_6000(
    client, db_session, seed_accounts, vendor
):
    # 6000 is Advertising & Marketing in the seeded chart; a credit for
    # returned flour landed there (2.17.3 exploratory, F8 / W-H5).
    from app.models.vendor_credits import VendorCredit

    r = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-04-05",
            "lines": [{"description": "credit", "quantity": 1, "rate": "50.00"}],
        },
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "Line 1 (credit)" in detail and "Acme Supply" in detail
    assert "default expense account" in detail
    assert db_session.query(VendorCredit).count() == 0
    assert (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "vendor_credit")
        .count()
        == 0
    )


def test_a_zero_credit_is_refused(client, seed_accounts, vendor):
    r = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-04-05",
            "lines": [{"description": "nothing", "quantity": 1, "rate": 0}],
        },
    )
    assert r.status_code == 400
    assert "more than zero" in r.json()["detail"]


def test_a_negative_line_is_refused_by_the_schema(client, vendor):
    r = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-04-05",
            "lines": [{"description": "backwards", "quantity": 1, "rate": -50}],
        },
    )
    assert r.status_code == 422


def test_an_unknown_vendor_is_a_404(client, seed_accounts):
    r = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": 999999,
            "date": "2026-04-05",
            "lines": [{"description": "x", "quantity": 1, "rate": 10}],
        },
    )
    assert r.status_code == 404


def test_a_credit_cannot_name_another_vendors_bill(client, seed_accounts, vendor):
    other = client.post("/api/vendors", json={"name": "Other Supply"}).json()
    bill = _bill(client, other, seed_accounts, "100.00")
    r = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-04-05",
            "original_bill_id": bill["id"],
            "lines": [{"description": "x", "quantity": 1, "rate": 10}],
        },
    )
    assert r.status_code == 400
    assert "different vendor" in r.json()["detail"]


# ── Applying ─────────────────────────────────────────────────────────────


def test_applying_a_credit_pays_down_the_bill_and_posts_nothing(
    client, db_session, seed_accounts, vendor
):
    bill = _bill(client, vendor, seed_accounts, "1000.00")
    vc = _credit(client, vendor, seed_accounts, "300.00")
    before = db_session.query(Transaction).count()

    r = client.post(
        f"/api/vendor-credits/{vc['id']}/apply",
        json={"bill_id": bill["id"], "amount": 300.0},
    )
    assert r.status_code == 200, r.text
    assert db_session.query(Transaction).count() == before, "apply must not post"

    db_session.expire_all()
    got = client.get(f"/api/vendor-credits/{vc['id']}").json()
    assert got["status"] == "applied"
    assert Decimal(got["balance_remaining"]) == Decimal("0")
    b = client.get(f"/api/bills/{bill['id']}").json()
    assert Decimal(str(b["balance_due"])) == Decimal("700.00")
    assert b["status"] == "partial"


def test_a_credit_that_settles_a_bill_exactly_marks_it_paid(
    client, seed_accounts, vendor
):
    bill = _bill(client, vendor, seed_accounts, "300.00")
    vc = _credit(client, vendor, seed_accounts, "300.00")
    client.post(
        f"/api/vendor-credits/{vc['id']}/apply",
        json={"bill_id": bill["id"], "amount": 300.0},
    )
    assert client.get(f"/api/bills/{bill['id']}").json()["status"] == "paid"


def test_a_credit_cannot_be_applied_beyond_its_balance(client, seed_accounts, vendor):
    bill = _bill(client, vendor, seed_accounts, "1000.00")
    vc = _credit(client, vendor, seed_accounts, "300.00")
    r = client.post(
        f"/api/vendor-credits/{vc['id']}/apply",
        json={"bill_id": bill["id"], "amount": 400.0},
    )
    assert r.status_code == 400
    assert "exceeds credit balance" in r.json()["detail"]


def test_a_credit_cannot_be_applied_beyond_the_bill(client, seed_accounts, vendor):
    bill = _bill(client, vendor, seed_accounts, "100.00")
    vc = _credit(client, vendor, seed_accounts, "300.00")
    r = client.post(
        f"/api/vendor-credits/{vc['id']}/apply",
        json={"bill_id": bill["id"], "amount": 200.0},
    )
    assert r.status_code == 400
    assert "exceeds bill balance" in r.json()["detail"]


def test_a_credit_cannot_be_applied_to_another_vendors_bill(
    client, seed_accounts, vendor
):
    other = client.post("/api/vendors", json={"name": "Other Supply"}).json()
    bill = _bill(client, other, seed_accounts, "1000.00")
    vc = _credit(client, vendor, seed_accounts, "300.00")
    r = client.post(
        f"/api/vendor-credits/{vc['id']}/apply",
        json={"bill_id": bill["id"], "amount": 100.0},
    )
    assert r.status_code == 400
    assert "different vendor" in r.json()["detail"]


def test_two_partial_applications_spend_the_credit_once(client, seed_accounts, vendor):
    b1 = _bill(client, vendor, seed_accounts, "200.00", day=1)
    b2 = _bill(client, vendor, seed_accounts, "200.00", day=2)
    vc = _credit(client, vendor, seed_accounts, "300.00")

    assert (
        client.post(
            f"/api/vendor-credits/{vc['id']}/apply",
            json={"bill_id": b1["id"], "amount": 200.0},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/vendor-credits/{vc['id']}/apply",
            json={"bill_id": b2["id"], "amount": 100.0},
        ).status_code
        == 200
    )
    over = client.post(
        f"/api/vendor-credits/{vc['id']}/apply",
        json={"bill_id": b2["id"], "amount": 1.0},
    )
    assert over.status_code == 400


# ── The thing the reporter actually asked for ────────────────────────────


def test_ap_aging_ties_to_account_2000_with_an_unapplied_credit(
    client, db_session, seed_accounts, vendor
):
    """This is the whole point of #129.

    A manual journal entry against A/P was rejected by the reporter because
    it moves the general ledger and not the vendor sub-ledger. A vendor
    credit that did the same would be no better, so: with a 1,000 bill and
    an unapplied 300 credit, A/P aging must read 700, the same as GL 2000.
    """
    _bill(client, vendor, seed_accounts, "1000.00")
    _credit(client, vendor, seed_accounts, "300.00")

    gl = _gl(db_session, seed_accounts["2000"].id)
    aging = client.get("/api/reports/ap-aging?as_of_date=2026-04-30").json()
    sub = Decimal(str(aging["totals"]["total"]))
    assert -gl == Decimal("700.00")
    assert sub == Decimal("700.00"), f"aging {sub} does not tie to GL {-gl}"

    row = [i for i in aging["items"] if i["vendor_id"] == vendor["id"]][0]
    assert Decimal(str(row["total"])) == Decimal("700.00")
    assert Decimal(str(row["unapplied_credits"])) == Decimal("300.00")


def test_applying_the_credit_does_not_change_the_aging_total(
    client, seed_accounts, vendor
):
    """Applying moves the credit from 'unapplied' to 'off the bill'. The
    vendor still owes the same 700 either way, which is what proves the
    unapplied case was being counted correctly."""
    bill = _bill(client, vendor, seed_accounts, "1000.00")
    vc = _credit(client, vendor, seed_accounts, "300.00")
    before = client.get("/api/reports/ap-aging?as_of_date=2026-04-30").json()
    client.post(
        f"/api/vendor-credits/{vc['id']}/apply",
        json={"bill_id": bill["id"], "amount": 300.0},
    )
    after = client.get("/api/reports/ap-aging?as_of_date=2026-04-30").json()
    assert Decimal(str(before["totals"]["total"])) == Decimal("700.00")
    assert Decimal(str(after["totals"]["total"])) == Decimal("700.00")
    assert Decimal(str(after["totals"]["unapplied_credits"])) == Decimal("0")


def test_ar_aging_ties_to_1100_with_an_unapplied_credit_memo(
    client, db_session, seed_accounts
):
    """The same hole existed on the receivable side and is fixed with it.
    An unapplied credit memo credited GL 1100 and was invisible to A/R
    aging, so the report overstated what customers owed."""
    cust = client.post("/api/customers", json={"name": "Probe Co"}).json()
    client.post(
        "/api/invoices",
        json={
            "customer_id": cust["id"],
            "date": "2026-03-01",
            "due_date": "2026-03-31",
            "lines": [
                {"description": "work", "quantity": 1, "rate": 1000, "line_order": 0}
            ],
        },
    )
    client.post(
        "/api/credit-memos",
        json={
            "customer_id": cust["id"],
            "date": "2026-03-05",
            "lines": [
                {"description": "return", "quantity": 1, "rate": 300, "line_order": 0}
            ],
        },
    )
    gl = _gl(db_session, seed_accounts["1100"].id)
    aging = client.get("/api/reports/ar-aging?as_of_date=2026-03-31").json()
    assert gl == Decimal("700.00")
    assert Decimal(str(aging["totals"]["total"])) == Decimal("700.00")
    assert Decimal(str(aging["totals"]["unapplied_credits"])) == Decimal("300.00")


# ── Void ─────────────────────────────────────────────────────────────────


def test_voiding_reverses_the_entry_and_puts_the_credit_back_on_the_bill(
    client, db_session, seed_accounts, vendor
):
    bill = _bill(client, vendor, seed_accounts, "1000.00")
    vc = _credit(client, vendor, seed_accounts, "300.00")
    client.post(
        f"/api/vendor-credits/{vc['id']}/apply",
        json={"bill_id": bill["id"], "amount": 300.0},
    )

    r = client.post(f"/api/vendor-credits/{vc['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"

    rev = (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == "vendor_credit_void",
            Transaction.source_id == vc["id"],
        )
        .one()
    )
    by_acct = {ln.account_id: ln for ln in rev.lines}
    assert by_acct[seed_accounts["2000"].id].credit == Decimal("300.00")
    assert by_acct[seed_accounts["6000"].id].debit == Decimal("300.00")

    db_session.expire_all()
    b = client.get(f"/api/bills/{bill['id']}").json()
    assert Decimal(str(b["balance_due"])) == Decimal("1000.00")
    assert b["status"] == "unpaid"

    dr = sum(Decimal(str(x[0] or 0)) for x in db_session.query(TransactionLine.debit))
    cr = sum(Decimal(str(x[0] or 0)) for x in db_session.query(TransactionLine.credit))
    assert dr == cr


def test_voiding_twice_is_refused(client, seed_accounts, vendor):
    vc = _credit(client, vendor, seed_accounts, "300.00")
    assert client.post(f"/api/vendor-credits/{vc['id']}/void").status_code == 200
    r = client.post(f"/api/vendor-credits/{vc['id']}/void")
    assert r.status_code == 400
    assert "already void" in r.json()["detail"]


def test_a_voided_credit_cannot_be_applied(client, seed_accounts, vendor):
    bill = _bill(client, vendor, seed_accounts, "1000.00")
    vc = _credit(client, vendor, seed_accounts, "300.00")
    client.post(f"/api/vendor-credits/{vc['id']}/void")
    r = client.post(
        f"/api/vendor-credits/{vc['id']}/apply",
        json={"bill_id": bill["id"], "amount": 100.0},
    )
    assert r.status_code == 400


def test_a_voided_credit_leaves_the_books_where_it_found_them(
    client, db_session, seed_accounts, vendor
):
    _bill(client, vendor, seed_accounts, "1000.00")
    ap_before = _gl(db_session, seed_accounts["2000"].id)
    vc = _credit(client, vendor, seed_accounts, "300.00")
    client.post(f"/api/vendor-credits/{vc['id']}/void")
    db_session.expire_all()
    assert _gl(db_session, seed_accounts["2000"].id) == ap_before


# ── Inventory ────────────────────────────────────────────────────────────


def _inventory_item(db_session, seed_accounts, name="Widget"):
    from app.models.items import Item as ItemModel, ItemType

    it = ItemModel(
        name=name,
        item_type=ItemType.PRODUCT,
        rate=Decimal("25.00"),
        track_inventory=True,
        asset_account_id=seed_accounts["1300"].id,
        is_taxable=False,
    )
    db_session.add(it)
    db_session.commit()
    db_session.refresh(it)
    return it


def test_returning_stock_credits_inventory_not_an_expense(
    client, db_session, seed_accounts, vendor
):
    """A bill receives stock into Inventory 1300. The credit that returns it
    must take it back out of 1300, not out of an expense account — otherwise
    the asset stays on the balance sheet for goods that are gone."""
    item = _inventory_item(db_session, seed_accounts)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "B-INV",
            "date": "2026-04-01",
            "lines": [
                {
                    "item_id": item.id,
                    "quantity": 10,
                    "rate": "10.00",
                    "line_order": 0,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    assert _gl(db_session, seed_accounts["1300"].id) == Decimal("100.00")

    vc = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-04-05",
            "lines": [
                {"item_id": item.id, "quantity": 4, "rate": "10.00", "line_order": 0}
            ],
        },
    )
    assert vc.status_code == 201, vc.text
    db_session.expire_all()
    assert _gl(db_session, seed_accounts["1300"].id) == Decimal("60.00")

    move = (
        db_session.query(InventoryMovement)
        .filter(
            InventoryMovement.source_type == "vendor_credit",
            InventoryMovement.source_id == vc.json()["id"],
        )
        .one()
    )
    assert move.movement_type == MovementType.RETURN_OUT
    assert Decimal(str(move.quantity)) == Decimal("-4")

    on_hand = client.get(f"/api/items/{item.id}").json()["quantity_on_hand"]
    assert Decimal(str(on_hand)) == Decimal("6")


def test_an_inventory_item_with_nowhere_to_post_is_refused(
    client, db_session, seed_accounts, vendor
):
    item = _inventory_item(db_session, seed_accounts)
    db_session.query(Account).filter(Account.id == seed_accounts["1300"].id).update(
        {"account_number": "1399"}
    )
    db_session.commit()
    from app.models.items import Item as ItemModel

    db_session.query(ItemModel).filter(ItemModel.id == item.id).update(
        {"asset_account_id": None}
    )
    db_session.commit()

    r = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-04-05",
            "lines": [
                {"item_id": item.id, "quantity": 1, "rate": "10.00", "line_order": 0}
            ],
        },
    )
    assert r.status_code == 400
    assert "inventory-tracked" in r.json()["detail"]


# ── Housekeeping ─────────────────────────────────────────────────────────


def test_the_closing_date_is_enforced(client, seed_accounts, vendor):
    r = client.put("/api/settings", json={"closing_date": "2026-12-31"})
    assert r.status_code in (200, 201), r.text
    r = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-04-05",
            "lines": [{"description": "x", "quantity": 1, "rate": 10}],
        },
    )
    assert r.status_code == 403


def test_credits_are_numbered_in_their_own_series(client, seed_accounts, vendor):
    a = _credit(client, vendor, seed_accounts, "10.00")
    b = _credit(client, vendor, seed_accounts, "20.00")
    assert a["credit_number"] == "VC-0001"
    assert b["credit_number"] == "VC-0002"


def test_the_list_filters_by_vendor_and_status(client, seed_accounts, vendor):
    other = client.post("/api/vendors", json={"name": "Other Supply"}).json()
    _credit(client, vendor, seed_accounts, "10.00")
    _credit(client, other, seed_accounts, "20.00")
    mine = client.get(f"/api/vendor-credits?vendor_id={vendor['id']}").json()
    assert len(mine) == 1
    assert mine[0]["vendor_name"] == "Acme Supply"
    assert client.get("/api/vendor-credits?status=issued").json().__len__() == 2


def test_unknown_credit_is_a_404(client):
    assert client.get("/api/vendor-credits/999999").status_code == 404
