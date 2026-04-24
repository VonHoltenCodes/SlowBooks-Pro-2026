"""Phase 11: Inventory tracking.

Exercises the perpetual-inventory engine end-to-end:
  - Items with track_inventory=False bypass all ledger work
  - Buying on a bill increases qty and updates weighted-avg cost
  - Selling on an invoice decreases qty and posts COGS at current avg_cost
  - Voiding an invoice reverses the sale
  - Manual adjustments change qty and post an offsetting JE
  - Low-stock endpoint returns items at or below their reorder point
"""
from decimal import Decimal

from app.models.items import Item, ItemType, InventoryMovement, MovementType
from app.models.contacts import Vendor
from app.models.invoices import Invoice
from app.models.bills import Bill
from app.models.transactions import TransactionLine


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------


def _mk_tracked_item(db_session, accounts, name="Widget", rate="25.00"):
    it = Item(
        name=name,
        item_type=ItemType.PRODUCT,
        rate=Decimal(rate),
        cost=Decimal("10.00"),
        income_account_id=accounts["4000"].id if "4000" in accounts else None,
        track_inventory=True,
        reorder_point=Decimal("5"),
        asset_account_id=accounts["1300"].id,
        is_taxable=False,
    )
    db_session.add(it)
    db_session.commit()
    db_session.refresh(it)
    return it


def _create_bill_for_inventory(client, vendor_id, item_id, qty, unit_cost, bill_number="B-1"):
    r = client.post("/api/bills", json={
        "vendor_id": vendor_id,
        "bill_number": bill_number,
        "date": "2026-04-01",
        "terms": "Net 30",
        "tax_rate": "0",
        "lines": [{
            "item_id": item_id,
            "description": "Stock receipt",
            "quantity": str(qty),
            "rate": str(unit_cost),
        }],
    })
    assert r.status_code == 201, r.text
    return r.json()


def _create_invoice_for_inventory(client, customer_id, item_id, qty, unit_rate):
    r = client.post("/api/invoices", json={
        "customer_id": customer_id,
        "date": "2026-04-15",
        "terms": "Net 30",
        "tax_rate": "0",
        "lines": [{
            "item_id": item_id,
            "description": "Sold widget",
            "quantity": str(qty),
            "rate": str(unit_rate),
        }],
    })
    assert r.status_code == 201, r.text
    return r.json()


def _seed_vendor(db_session):
    v = Vendor(name="Widget Supplier Co", is_active=True)
    db_session.add(v)
    db_session.commit()
    return v


# -------------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------------


def test_non_tracked_item_has_no_inventory_ledger(client, db_session, seed_accounts, seed_customer):
    """Services and non-inventory items don't touch the inventory ledger."""
    service = Item(
        name="Consulting",
        item_type=ItemType.SERVICE,
        rate=Decimal("100"),
        track_inventory=False,
        is_taxable=False,
    )
    db_session.add(service)
    db_session.commit()
    db_session.refresh(service)

    _create_invoice_for_inventory(client, seed_customer.id, service.id, qty=2, unit_rate=100)
    assert db_session.query(InventoryMovement).count() == 0


def test_purchase_then_sale_posts_cogs_at_avg_cost(
    client, db_session, seed_accounts, seed_customer
):
    """Classic flow: buy stock, sell it, verify COGS journal hits at avg_cost."""
    vendor = _seed_vendor(db_session)
    item = _mk_tracked_item(db_session, seed_accounts)

    _create_bill_for_inventory(client, vendor.id, item.id, qty=10, unit_cost="10.00")

    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    assert Decimal(str(item.quantity_on_hand)) == Decimal("10")
    assert Decimal(str(item.avg_cost)) == Decimal("10")

    # Sell 3 units
    _create_invoice_for_inventory(client, seed_customer.id, item.id, qty=3, unit_rate="25.00")

    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    assert Decimal(str(item.quantity_on_hand)) == Decimal("7")

    # COGS journal should exist: DR COGS 30.00, CR Inventory 30.00
    sale_mv = (
        db_session.query(InventoryMovement)
        .filter_by(item_id=item.id, movement_type=MovementType.SALE)
        .first()
    )
    assert sale_mv is not None
    assert Decimal(str(sale_mv.quantity)) == Decimal("-3")
    assert sale_mv.transaction_id is not None
    # Verify the transaction lines sum to a balanced DR/CR at 30.00
    lines = (
        db_session.query(TransactionLine)
        .filter(TransactionLine.transaction_id == sale_mv.transaction_id)
        .all()
    )
    total_dr = sum((l.debit for l in lines), Decimal("0"))
    total_cr = sum((l.credit for l in lines), Decimal("0"))
    assert total_dr == total_cr == Decimal("30.00")


def test_weighted_average_cost_updates_on_second_purchase(
    client, db_session, seed_accounts
):
    """Two purchases at different costs blend into a weighted-avg cost."""
    vendor = _seed_vendor(db_session)
    item = _mk_tracked_item(db_session, seed_accounts)

    _create_bill_for_inventory(client, vendor.id, item.id, qty=10, unit_cost="10.00", bill_number="B-1")
    _create_bill_for_inventory(client, vendor.id, item.id, qty=10, unit_cost="14.00", bill_number="B-2")

    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    # (10*10 + 10*14) / 20 = 240/20 = 12.00
    assert Decimal(str(item.quantity_on_hand)) == Decimal("20")
    assert Decimal(str(item.avg_cost)) == Decimal("12")


def test_invoice_void_reverses_inventory(
    client, db_session, seed_accounts, seed_customer
):
    """Voiding an invoice restores the shipped quantity."""
    vendor = _seed_vendor(db_session)
    item = _mk_tracked_item(db_session, seed_accounts)
    _create_bill_for_inventory(client, vendor.id, item.id, qty=10, unit_cost="10.00")

    inv = _create_invoice_for_inventory(client, seed_customer.id, item.id, qty=4, unit_rate="25.00")

    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    assert Decimal(str(item.quantity_on_hand)) == Decimal("6")

    r = client.post(f"/api/invoices/{inv['id']}/void")
    assert r.status_code == 200, r.text

    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    assert Decimal(str(item.quantity_on_hand)) == Decimal("10")


def test_manual_adjustment_up_and_down(
    client, db_session, seed_accounts
):
    """POST /adjust with positive and negative deltas updates qty + writes a JE."""
    item = _mk_tracked_item(db_session, seed_accounts, name="Stock Part")

    # First a purchase so we have a non-zero avg_cost to use for the JE valuation
    vendor = _seed_vendor(db_session)
    _create_bill_for_inventory(client, vendor.id, item.id, qty=5, unit_cost="20.00")

    # Adjust up by 3 at default (item avg_cost)
    r = client.post(f"/api/items/{item.id}/adjust", json={
        "quantity_delta": "3",
        "memo": "Stock count correction",
    })
    assert r.status_code == 200, r.text

    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    assert Decimal(str(item.quantity_on_hand)) == Decimal("8")

    # Adjust down by 2
    r = client.post(f"/api/items/{item.id}/adjust", json={
        "quantity_delta": "-2",
        "memo": "Shrinkage",
    })
    assert r.status_code == 200

    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    assert Decimal(str(item.quantity_on_hand)) == Decimal("6")


def test_adjust_rejects_non_tracked_item(
    client, db_session, seed_accounts
):
    service = Item(
        name="Labor",
        item_type=ItemType.LABOR,
        rate=Decimal("50"),
        track_inventory=False,
    )
    db_session.add(service)
    db_session.commit()

    r = client.post(f"/api/items/{service.id}/adjust", json={"quantity_delta": "5"})
    assert r.status_code == 400
    assert "not inventory-tracked" in r.json()["detail"]


def test_low_stock_endpoint_returns_items_under_reorder(
    client, db_session, seed_accounts
):
    """Items at or below reorder_point are returned worst-shortage-first."""
    # Item A: reorder 10, qty 2 → shortage 8 (worst)
    # Item B: reorder 5, qty 5  → shortage 0 (tied)
    # Item C: reorder 20, qty 30 → not low, excluded
    a = Item(name="A", item_type=ItemType.PRODUCT, rate=0, track_inventory=True,
             reorder_point=Decimal("10"), quantity_on_hand=Decimal("2"))
    b = Item(name="B", item_type=ItemType.PRODUCT, rate=0, track_inventory=True,
             reorder_point=Decimal("5"), quantity_on_hand=Decimal("5"))
    c = Item(name="C", item_type=ItemType.PRODUCT, rate=0, track_inventory=True,
             reorder_point=Decimal("20"), quantity_on_hand=Decimal("30"))
    db_session.add_all([a, b, c])
    db_session.commit()

    r = client.get("/api/items/low-stock")
    assert r.status_code == 200
    names = [row["name"] for row in r.json()]
    assert "A" in names
    assert "B" in names
    assert "C" not in names
    # Worst shortage (A) should be first
    assert r.json()[0]["name"] == "A"


def test_valuation_sums_across_tracked_items(
    client, db_session, seed_accounts
):
    vendor = _seed_vendor(db_session)
    item = _mk_tracked_item(db_session, seed_accounts)
    _create_bill_for_inventory(client, vendor.id, item.id, qty=5, unit_cost="10.00")

    r = client.get("/api/items/valuation")
    assert r.status_code == 200
    body = r.json()
    assert body["item_count"] >= 1
    # 5 * 10.00 = 50.00 (may be more if other tests in the same txn leaked)
    assert body["total_value"] >= 50.0
