"""A vendor credit keeps Inventory at quantity x average cost (W-74, from
the 2.31.1 gate; the vendor side of #646 and #647).

A vendor credit takes the stock it returns off Inventory at the line's rate
with its share of the tax, the figure its entry credits Inventory with. The
return was booked with nothing posted for what the pool could not carry,
and the void put each line's quantity back at the rate alone, without tax,
so after either the ledger's Inventory and quantity x average cost parted:
a credit of 1 at 14.00 into a count of -3 left -44.00 against -40.00; a
credit voided back into a count below zero left -4.50 against -9.50; 5 at
12.00 against 5 bought at 10.00 left -10.00 with nothing on hand; a taxed
credit voided with stock on hand left 100.00 against 98.00. The return now
posts what the pool cannot carry to the item's cost of goods account under
the credit, and the void reverses each return at the cost it was booked
at, one reversal movement naming it, its residue under the void. A credit
that returns more than is on hand says so, or is refused with the setting
on, the way a sale past the stock is (#642)."""

from decimal import Decimal

from app.models.items import InventoryMovement, MovementType
from app.models.vendor_credits import VendorCredit

from tests.test_bill_reversal_cost import (
    _average,
    _bill,
    _inventory_is_the_stock,
    _movements,
    _sell_out,
    _stock,
    _void_shape,
)
from tests.test_credit_memo_return_cogs import (
    _entries,
    _on_hand,
    _sides,
)
from tests.test_inventory import _create_invoice_for_inventory, _seed_vendor
from tests.test_item_cogs_account import _item
from tests.test_oversell_signal import _refuse, _source
from tests.test_receipt_below_zero import _cogs, _three_sold_ahead


def _returned(item_id, qty, rate):
    return {
        "item_id": item_id,
        "description": "Returned to vendor",
        "quantity": str(qty),
        "rate": str(rate),
    }


def _credit_body(vendor_id, lines, tax_rate="0"):
    return {
        "vendor_id": vendor_id,
        "date": "2026-04-20",
        "tax_rate": tax_rate,
        "lines": lines,
    }


def _credit(client, vendor_id, lines, tax_rate="0"):
    r = client.post(
        "/api/vendor-credits", json=_credit_body(vendor_id, lines, tax_rate)
    )
    assert r.status_code == 201, r.text
    return r.json()


def _void(client, credit_id):
    r = client.post(f"/api/vendor-credits/{credit_id}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"
    return r.json()


def _ap_and_inventory(accounts, amount):
    """The sides of an entry that moves `amount` between A/P (debited) and
    Inventory (credited), as a credit's own entry does."""
    return (
        {accounts["2000"].id: Decimal(amount)},
        {accounts["1300"].id: Decimal(amount)},
    )


def _residue_memo(memo):
    return f"{memo} - Inventory set to quantity x average cost: Widget"


# -------------------------------------------------------------------------
# The four cases the gate found
# -------------------------------------------------------------------------


def test_a_credit_into_a_count_below_zero_posts_its_distance_from_the_average(
    client, db_session, seed_accounts, seed_customer
):
    """From -3 at 10.00 (bill B-1 5 at 10.00, invoice 8), a credit returns 1
    at 14.00: the credit takes 14.00 off Inventory and the unit owed is
    carried at the kept average of 10.00, so the 4.00 between them comes
    back out of cost of goods under the credit (Dr Inventory / Cr Cost of
    Goods Sold). The count is -4 at 10.00 and Inventory reads -40.00 (it
    read -44.00). Bill B-2 bringing the four at 10.00 leaves 0 on hand and
    0.00, with 76.00 in cost of goods: the 90.00 the nine units cost, less
    the 14.00 the vendor gave back for one."""
    vendor, item, _invoice = _three_sold_ahead(
        client, db_session, seed_accounts, seed_customer
    )

    credit = _credit(client, vendor.id, [_returned(item.id, 1, "14.00")])

    db_session.expire_all()
    assert credit["warnings"] == ["Widget: -3 on hand; this credit takes it to -4."]
    assert _on_hand(db_session, item.id) == Decimal("-4")
    assert _average(db_session, item.id) == Decimal("10.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "-40.00"
    )
    (move,) = _movements(db_session, item.id, MovementType.RETURN_OUT)
    assert (Decimal(str(move.quantity)), Decimal(str(move.unit_cost))) == (
        Decimal("-1"),
        Decimal("14.0000"),
    )
    own, residue = _entries(db_session, "vendor_credit", credit["id"])
    assert move.transaction_id == own.id
    assert _sides(own) == _ap_and_inventory(seed_accounts, "14.00")
    assert residue.description == _residue_memo("Vendor credit VC-0001 line 1")
    assert str(residue.date) == "2026-04-20"  # the credit's date
    debits, credits = _sides(residue)
    assert debits == {seed_accounts["1300"].id: Decimal("4.00")}
    assert credits == {seed_accounts["5000"].id: Decimal("4.00")}
    assert _cogs(db_session, seed_accounts) == Decimal("76.00")

    _bill(client, vendor.id, [_stock(item.id, 4, "10.00")], bill_number="B-2")

    db_session.expire_all()
    assert _on_hand(db_session, item.id) == Decimal("0")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "0.00"
    )
    assert _cogs(db_session, seed_accounts) == Decimal("76.00")
    assert client.get("/api/items/valuation").json()["total_value"] == 0.0


def test_a_credit_voided_back_into_a_count_below_zero_fills_the_hole_at_its_cost(
    client, db_session, seed_accounts, seed_customer
):
    """Bill B-1 brings 10 at 10.00 and a credit returns 2 at 12.00: the
    eight left carry what is left of the value, 9.50 each (Inventory
    76.00), and nothing more posts. Invoice 11 takes the count to -3 at
    9.50 (Inventory -28.50). Voiding the credit brings the two back at the
    12.00 they went out at, into the hole: each cost 2.50 more than the
    units sold ahead were costed at, so 5.00 goes to cost of goods under the
    void, and the count is -1 at the kept 9.50, Inventory -9.50 (it read
    -4.50). Bill B-2 bringing the one at 10.00 leaves 0 on hand, 0.00 in
    Inventory and 110.00 in cost of goods: eleven at 10.00."""
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, None)
    _bill(client, vendor.id, [_stock(item.id, 10, "10.00")])
    credit = _credit(client, vendor.id, [_returned(item.id, 2, "12.00")])
    db_session.expire_all()
    assert credit["warnings"] == []
    assert _average(db_session, item.id) == Decimal("9.5000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "76.00"
    )
    (own,) = _entries(db_session, "vendor_credit", credit["id"])  # nothing more
    assert _sides(own) == _ap_and_inventory(seed_accounts, "24.00")
    _create_invoice_for_inventory(
        client, seed_customer.id, item.id, qty=11, unit_rate="25.00"
    )
    db_session.expire_all()
    assert _on_hand(db_session, item.id) == Decimal("-3")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "-28.50"
    )
    assert _cogs(db_session, seed_accounts) == Decimal("104.50")

    voided = _void(client, credit["id"])

    db_session.expire_all()
    assert voided["warnings"] == []  # stock coming back is never short
    assert _on_hand(db_session, item.id) == Decimal("-1")
    assert _average(db_session, item.id) == Decimal("9.5000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "-9.50"
    )
    reversal, residue = _entries(db_session, "vendor_credit_void", credit["id"])
    debits, credits = _sides(reversal)  # the mirror of the credit's own entry
    assert (credits, debits) == _ap_and_inventory(seed_accounts, "24.00")
    assert residue.description == _residue_memo("VOID Vendor credit VC-0001 line 1")
    assert str(residue.date) == "2026-04-20"
    debits, credits = _sides(residue)
    assert debits == {seed_accounts["5000"].id: Decimal("5.00")}
    assert credits == {seed_accounts["1300"].id: Decimal("5.00")}
    (move,) = _movements(db_session, item.id, MovementType.RETURN_OUT)
    (void,) = _movements(db_session, item.id, MovementType.VOID)
    assert _void_shape(void) == (
        Decimal("2"),
        Decimal("12.0000"),
        move.id,
        "vendor_credit_void",
    )
    assert void.transaction_id == reversal.id
    assert _cogs(db_session, seed_accounts) == Decimal("109.50")

    _bill(client, vendor.id, [_stock(item.id, 1, "10.00")], bill_number="B-2")

    db_session.expire_all()
    assert _on_hand(db_session, item.id) == Decimal("0")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "0.00"
    )
    assert _cogs(db_session, seed_accounts) == Decimal("110.00")


def test_a_credit_that_empties_the_shelf_for_more_than_it_cost_leaves_inventory_at_zero(
    client, db_session, seed_accounts
):
    """Bill B-1 brings 5 at 10.00 and a credit returns all five at 12.00:
    the credit takes 60.00 off Inventory for stock that held 50.00, so the
    10.00 between them comes back out of cost of goods under the credit,
    and Inventory reads 0.00 with nothing on hand (it read -10.00). A
    credit that lands the count exactly on zero is not past the stock."""
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, None)
    _bill(client, vendor.id, [_stock(item.id, 5, "10.00")])

    credit = _credit(client, vendor.id, [_returned(item.id, 5, "12.00")])

    db_session.expire_all()
    assert credit["warnings"] == []
    assert _on_hand(db_session, item.id) == Decimal("0")
    assert _average(db_session, item.id) == Decimal("0.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "0.00"
    )
    own, residue = _entries(db_session, "vendor_credit", credit["id"])
    assert _sides(own) == _ap_and_inventory(seed_accounts, "60.00")
    assert residue.description == _residue_memo("Vendor credit VC-0001 line 1")
    debits, credits = _sides(residue)
    assert debits == {seed_accounts["1300"].id: Decimal("10.00")}
    assert credits == {seed_accounts["5000"].id: Decimal("10.00")}
    assert _cogs(db_session, seed_accounts) == Decimal("-10.00")
    assert client.get("/api/items/valuation").json()["total_value"] == 0.0


def test_a_taxed_credits_void_puts_the_stock_back_at_the_cost_it_went_out_at(
    client, db_session, seed_accounts, seed_customer
):
    """Bill B-1 brings 10 at 10.00; a credit returns 2 at 10.00 taxed 10%,
    taking 22.00 off Inventory, 11.00 a unit, and the eight left carry
    9.75 (Inventory 78.00). The void brings the two back at 11.00, not the
    line's 10.00, naming the return and the reversing entry, and nothing
    is left over: 10 at 10.00 and Inventory 100.00 (it read 100.00 against
    98.00, an average of 9.80). Selling the ten leaves 0.00, and cost of
    goods is the 100.00 the bill cost."""
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, None)
    _bill(client, vendor.id, [_stock(item.id, 10, "10.00")])
    credit = _credit(
        client, vendor.id, [_returned(item.id, 2, "10.00")], tax_rate="0.10"
    )
    db_session.expire_all()
    assert Decimal(credit["tax_amount"]) == Decimal("2.00")
    (move,) = _movements(db_session, item.id, MovementType.RETURN_OUT)
    assert Decimal(str(move.unit_cost)) == Decimal("11.0000")
    (own,) = _entries(db_session, "vendor_credit", credit["id"])
    assert _sides(own) == _ap_and_inventory(seed_accounts, "22.00")
    assert _average(db_session, item.id) == Decimal("9.7500")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "78.00"
    )

    _void(client, credit["id"])

    db_session.expire_all()
    (reversal,) = _entries(db_session, "vendor_credit_void", credit["id"])
    debits, credits = _sides(reversal)
    assert (credits, debits) == _ap_and_inventory(seed_accounts, "22.00")
    (void,) = _movements(db_session, item.id, MovementType.VOID)
    assert _void_shape(void) == (
        Decimal("2"),
        Decimal("11.0000"),
        move.id,
        "vendor_credit_void",
    )
    assert void.transaction_id == reversal.id
    assert _on_hand(db_session, item.id) == Decimal("10")
    assert _average(db_session, item.id) == Decimal("10.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "100.00"
    )
    assert client.get("/api/items/valuation").json()["total_value"] == 100.0

    _sell_out(client, db_session, seed_accounts, seed_customer, item.id)
    assert _cogs(db_session, seed_accounts) == Decimal("100.00")


# -------------------------------------------------------------------------
# Tax, and the void naming each return
# -------------------------------------------------------------------------


def test_a_taxed_credit_and_its_void_post_their_residue_at_the_cost_with_tax(
    client, db_session, seed_accounts, seed_customer
):
    """From -3 at 10.00, a credit returns 2 at 10.00 taxed 8.25%: 21.65
    comes off Inventory, 10.8250 a unit, and the two owed are carried at
    the kept 10.00, so the 1.65 of tax comes back out of cost of goods under
    the credit: -5 at 10.00, Inventory -50.00. The void puts the 21.65 back
    and the two back into the hole at 10.8250, the 1.65 going to cost of
    goods under the void: -3 at 10.00, Inventory -30.00 and cost of goods
    80.00, where they were before the credit. Bill B-2 bringing the three at
    10.00 leaves 0.00."""
    vendor, item, _invoice = _three_sold_ahead(
        client, db_session, seed_accounts, seed_customer
    )

    credit = _credit(
        client, vendor.id, [_returned(item.id, 2, "10.00")], tax_rate="0.0825"
    )

    db_session.expire_all()
    assert Decimal(credit["tax_amount"]) == Decimal("1.65")
    assert Decimal(credit["total"]) == Decimal("21.65")
    assert credit["warnings"] == ["Widget: -3 on hand; this credit takes it to -5."]
    (move,) = _movements(db_session, item.id, MovementType.RETURN_OUT)
    assert (Decimal(str(move.quantity)), Decimal(str(move.unit_cost))) == (
        Decimal("-2"),
        Decimal("10.8250"),
    )
    own, residue = _entries(db_session, "vendor_credit", credit["id"])
    assert _sides(own) == _ap_and_inventory(seed_accounts, "21.65")
    debits, credits = _sides(residue)
    assert debits == {seed_accounts["1300"].id: Decimal("1.65")}
    assert credits == {seed_accounts["5000"].id: Decimal("1.65")}
    assert _on_hand(db_session, item.id) == Decimal("-5")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "-50.00"
    )
    assert _cogs(db_session, seed_accounts) == Decimal("78.35")

    _void(client, credit["id"])

    db_session.expire_all()
    reversal, residue = _entries(db_session, "vendor_credit_void", credit["id"])
    debits, credits = _sides(reversal)
    assert (credits, debits) == _ap_and_inventory(seed_accounts, "21.65")
    debits, credits = _sides(residue)
    assert debits == {seed_accounts["5000"].id: Decimal("1.65")}
    assert credits == {seed_accounts["1300"].id: Decimal("1.65")}
    (void,) = _movements(db_session, item.id, MovementType.VOID)
    assert _void_shape(void) == (
        Decimal("2"),
        Decimal("10.8250"),
        move.id,
        "vendor_credit_void",
    )
    assert _on_hand(db_session, item.id) == Decimal("-3")
    assert _average(db_session, item.id) == Decimal("10.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "-30.00"
    )
    assert _cogs(db_session, seed_accounts) == Decimal("80.00")

    _bill(client, vendor.id, [_stock(item.id, 3, "10.00")], bill_number="B-2")
    db_session.expire_all()
    assert _on_hand(db_session, item.id) == Decimal("0")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "0.00"
    )


def test_the_void_names_each_return_it_reverses(
    client, db_session, seed_accounts, seed_customer
):
    """A credit puts the same item on two lines, 2 at 10.00 and 4 at 13.00:
    two returns, leaving four at 7.00 (Inventory 28.00). The void reverses
    each by itself at its own cost, oldest first, one VOID movement naming
    each, both linked to the reversing entry, and the movements API carries
    reverses_movement_id: 10 on hand at 10.00 again, Inventory 100.00, and
    selling them leaves 0.00."""
    vendor = _seed_vendor(db_session)
    item = _item(db_session, seed_accounts, None)
    _bill(client, vendor.id, [_stock(item.id, 10, "10.00")])
    credit = _credit(
        client,
        vendor.id,
        [_returned(item.id, 2, "10.00"), _returned(item.id, 4, "13.00")],
    )
    db_session.expire_all()
    first, second = _movements(db_session, item.id, MovementType.RETURN_OUT)
    assert [
        (Decimal(str(m.quantity)), Decimal(str(m.unit_cost))) for m in (first, second)
    ] == [(Decimal("-2"), Decimal("10.0000")), (Decimal("-4"), Decimal("13.0000"))]
    assert _average(db_session, item.id) == Decimal("7.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "28.00"
    )

    _void(client, credit["id"])

    db_session.expire_all()
    (reversal,) = _entries(db_session, "vendor_credit_void", credit["id"])
    debits, credits = _sides(reversal)
    assert (credits, debits) == _ap_and_inventory(seed_accounts, "72.00")
    voids = _movements(db_session, item.id, MovementType.VOID)
    assert [_void_shape(v) for v in voids] == [
        (Decimal("2"), Decimal("10.0000"), first.id, "vendor_credit_void"),
        (Decimal("4"), Decimal("13.0000"), second.id, "vendor_credit_void"),
    ]
    assert [v.transaction_id for v in voids] == [reversal.id, reversal.id]
    rows = client.get(f"/api/items/{item.id}/movements").json()
    rows.reverse()  # the API lists newest first
    assert [
        (r["movement_type"], r["memo"], r["reverses_movement_id"]) for r in rows
    ] == [
        ("purchase", "Bill B-1 line 1", None),
        ("return_out", "Vendor credit VC-0001 line 1", None),
        ("return_out", "Vendor credit VC-0001 line 2", None),
        ("void", "VOID Vendor credit VC-0001 line 1", first.id),
        ("void", "VOID Vendor credit VC-0001 line 2", second.id),
    ]
    assert _on_hand(db_session, item.id) == Decimal("10")
    assert _average(db_session, item.id) == Decimal("10.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "100.00"
    )

    _sell_out(client, db_session, seed_accounts, seed_customer, item.id)


# -------------------------------------------------------------------------
# Returning more than is on hand (the #642 / W-67 shape)
# -------------------------------------------------------------------------


def _two_on_hand(client, db, accounts, customer):
    """Bill B-1 brings 5 at 10.00 and an invoice sells 3: two on hand."""
    vendor = _seed_vendor(db)
    item = _item(db, accounts, None)
    _bill(client, vendor.id, [_stock(item.id, 5, "10.00")])
    _create_invoice_for_inventory(client, customer.id, item.id, qty=3, unit_rate="25")
    db.expire_all()
    assert _on_hand(db, item.id) == Decimal("2")
    return vendor, item


def test_a_credit_that_returns_more_than_is_on_hand_posts_and_says_so(
    client, db_session, seed_accounts, seed_customer
):
    """Two on hand: a credit returning five posts as before, -3 on hand at
    10.00 and Inventory -30.00, and the response says so. Read back, the
    credit carries no warning: it is said once, by the create."""
    vendor, item = _two_on_hand(client, db_session, seed_accounts, seed_customer)

    credit = _credit(client, vendor.id, [_returned(item.id, 5, "10.00")])

    assert credit["status"] == "issued"
    assert credit["warnings"] == ["Widget: 2 on hand; this credit takes it to -3."]
    db_session.expire_all()
    assert _on_hand(db_session, item.id) == Decimal("-3")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "-30.00"
    )
    assert client.get(f"/api/vendor-credits/{credit['id']}").json()["warnings"] == []


def test_with_the_setting_on_a_credit_past_the_stock_is_refused_and_nothing_posts(
    client, db_session, seed_accounts, seed_customer
):
    """Two on hand and Refuse a sale of more than is on hand turned on: a
    credit returning five is refused with a 422 naming the item and the
    shortfall, and nothing is written. Returning the two is within the
    stock, and the credit's void, stock coming back, is never refused, even
    into a count below zero."""
    vendor, item = _two_on_hand(client, db_session, seed_accounts, seed_customer)
    _refuse(client, True)
    before = db_session.query(InventoryMovement).filter_by(item_id=item.id).count()

    r = client.post(
        "/api/vendor-credits",
        json=_credit_body(vendor.id, [_returned(item.id, 5, "10.00")]),
    )

    assert r.status_code == 422, r.text
    assert r.json()["detail"] == (
        "Widget: 2 on hand; this credit takes it to -3, 3 short. Return no more "
        "than is on hand, enter the bill that received the stock first, or turn "
        "off Refuse a sale of more than is on hand under Settings."
    )
    db_session.expire_all()
    assert db_session.query(VendorCredit).count() == 0
    assert db_session.query(InventoryMovement).filter_by(item_id=item.id).count() == (
        before
    )
    assert _entries(db_session, "vendor_credit", 1) == []
    assert _on_hand(db_session, item.id) == Decimal("2")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "20.00"
    )

    credit = _credit(client, vendor.id, [_returned(item.id, 2, "10.00")])
    assert credit["credit_number"] == "VC-0001"  # the refused one took no number
    assert credit["warnings"] == []
    db_session.expire_all()
    assert _on_hand(db_session, item.id) == Decimal("0")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "0.00"
    )

    # one more sold with the setting off takes the count to -1 at 0.00
    _refuse(client, False)
    _create_invoice_for_inventory(
        client, seed_customer.id, item.id, qty=1, unit_rate="25"
    )
    _refuse(client, True)

    voided = _void(client, credit["id"])

    assert voided["warnings"] == []
    db_session.expire_all()
    assert _on_hand(db_session, item.id) == Decimal("1")
    assert _average(db_session, item.id) == Decimal("10.0000")
    assert _inventory_is_the_stock(db_session, seed_accounts, item.id) == Decimal(
        "10.00"
    )
    assert _cogs(db_session, seed_accounts) == Decimal("40.00")


def test_the_vendor_credits_page_shows_the_warnings():
    src = _source("js/vendor_credits.js")
    # a new credit posts to /vendor-credits; one changed from a report line
    # to its /replace (#689), with the same warnings
    assert (
        "const saved = await API.post('/vendor-credits' + (typeof Replace === "
        "'undefined' ? '' : Replace.suffix(form)), {" in src
    )
    assert (
        "for (const w of (saved && saved.warnings) || []) toast(w, 'warning');" in src
    )
