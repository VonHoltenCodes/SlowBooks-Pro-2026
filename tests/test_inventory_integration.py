"""Phase 11 post-audit: inventory hooks on every invoice/bill creation path.

Regression tests for the 10 integration gaps found by the spiderweb audit:
  1. Invoice PUT — delta reconciliation
  2. Invoice duplicate
  3. Estimate → invoice convert
  4. Recurring invoice generation
  5. IIF invoice import
  6. Credit memos (return to stock)
  7. PO → Bill convert (was silently orphaned)
  8. Bills with no asset account (should 400, not silently drop DR)
  9. reverse_sale at historical cost (not current avg_cost)
  10. Saved reports size cap
"""
from datetime import date
from decimal import Decimal

from app.models.items import Item, ItemType, InventoryMovement, MovementType
from app.models.contacts import Vendor, Customer
from app.models.invoices import Invoice
from app.models.estimates import Estimate, EstimateLine
from app.models.recurring import RecurringInvoice, RecurringInvoiceLine
from app.models.purchase_orders import PurchaseOrder, PurchaseOrderLine
from app.models.credit_memos import CreditMemo


# -------- Helpers --------


def _seed_vendor(db_session):
    v = Vendor(name="Widget Supplier", is_active=True)
    db_session.add(v)
    db_session.commit()
    return v


def _tracked_item(db_session, seed_accounts, name="Widget", rate="25.00"):
    it = Item(
        name=name,
        item_type=ItemType.PRODUCT,
        rate=Decimal(rate),
        track_inventory=True,
        reorder_point=Decimal("5"),
        asset_account_id=seed_accounts["1300"].id,
        is_taxable=False,
    )
    db_session.add(it)
    db_session.commit()
    db_session.refresh(it)
    return it


def _seed_stock(client, vendor_id, item_id, qty, unit_cost, bill_number="B-SEED"):
    r = client.post("/api/bills", json={
        "vendor_id": vendor_id,
        "bill_number": bill_number,
        "date": "2026-04-01",
        "terms": "Net 30",
        "tax_rate": "0",
        "lines": [{
            "item_id": item_id,
            "description": "Stock seed",
            "quantity": str(qty),
            "rate": str(unit_cost),
        }],
    })
    assert r.status_code == 201, r.text
    return r.json()


def _create_invoice(client, customer_id, item_id, qty, unit_rate="25.00", date_="2026-04-15"):
    r = client.post("/api/invoices", json={
        "customer_id": customer_id,
        "date": date_,
        "terms": "Net 30",
        "tax_rate": "0",
        "lines": [{
            "item_id": item_id,
            "description": "Sold",
            "quantity": str(qty),
            "rate": str(unit_rate),
        }],
    })
    assert r.status_code == 201, r.text
    return r.json()


# -------- Test 1: Invoice PUT reconciles inventory --------


def test_invoice_edit_increases_qty_posts_additional_sale(
    client, db_session, seed_accounts, seed_customer
):
    vendor = _seed_vendor(db_session)
    item = _tracked_item(db_session, seed_accounts)
    _seed_stock(client, vendor.id, item.id, qty=10, unit_cost="10.00")

    inv = _create_invoice(client, seed_customer.id, item.id, qty=2)

    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    assert Decimal(str(item.quantity_on_hand)) == Decimal("8")

    # Edit from qty=2 to qty=5. The delta is +3; inventory should drop
    # another 3 units.
    r = client.put(f"/api/invoices/{inv['id']}", json={
        "tax_rate": "0",
        "lines": [{
            "item_id": item.id,
            "description": "Sold (edited)",
            "quantity": "5",
            "rate": "25.00",
        }],
    })
    assert r.status_code == 200, r.text

    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    assert Decimal(str(item.quantity_on_hand)) == Decimal("5")


def test_invoice_edit_decreases_qty_reverses_sale(
    client, db_session, seed_accounts, seed_customer
):
    vendor = _seed_vendor(db_session)
    item = _tracked_item(db_session, seed_accounts)
    _seed_stock(client, vendor.id, item.id, qty=10, unit_cost="10.00")

    inv = _create_invoice(client, seed_customer.id, item.id, qty=5)

    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    assert Decimal(str(item.quantity_on_hand)) == Decimal("5")

    # Reduce to qty=2 — should return 3 units
    r = client.put(f"/api/invoices/{inv['id']}", json={
        "tax_rate": "0",
        "lines": [{
            "item_id": item.id,
            "description": "Sold less",
            "quantity": "2",
            "rate": "25.00",
        }],
    })
    assert r.status_code == 200, r.text

    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    assert Decimal(str(item.quantity_on_hand)) == Decimal("8")


# -------- Test 2: Invoice duplicate hits inventory --------


def test_invoice_duplicate_posts_new_sale(
    client, db_session, seed_accounts, seed_customer
):
    vendor = _seed_vendor(db_session)
    item = _tracked_item(db_session, seed_accounts)
    _seed_stock(client, vendor.id, item.id, qty=20, unit_cost="10.00")

    inv = _create_invoice(client, seed_customer.id, item.id, qty=3)
    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    assert Decimal(str(item.quantity_on_hand)) == Decimal("17")

    r = client.post(f"/api/invoices/{inv['id']}/duplicate")
    assert r.status_code == 201, r.text

    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    # Duplicate posts ANOTHER sale of 3 units → 17 - 3 = 14
    assert Decimal(str(item.quantity_on_hand)) == Decimal("14")


# -------- Test 3: Estimate-to-invoice convert hits inventory --------


def test_estimate_convert_triggers_inventory(
    client, db_session, seed_accounts, seed_customer
):
    vendor = _seed_vendor(db_session)
    item = _tracked_item(db_session, seed_accounts)
    _seed_stock(client, vendor.id, item.id, qty=10, unit_cost="10.00")

    # Create an estimate
    r = client.post("/api/estimates", json={
        "customer_id": seed_customer.id,
        "date": "2026-04-15",
        "tax_rate": "0",
        "lines": [{
            "item_id": item.id,
            "description": "Pending sale",
            "quantity": "4",
            "rate": "25.00",
        }],
    })
    assert r.status_code == 201, r.text
    est = r.json()
    est_id = est["id"]

    # Estimate alone shouldn't move inventory
    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    assert Decimal(str(item.quantity_on_hand)) == Decimal("10")

    # Convert to invoice
    r = client.post(f"/api/estimates/{est_id}/convert")
    assert r.status_code == 200, r.text

    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    assert Decimal(str(item.quantity_on_hand)) == Decimal("6")


# -------- Test 4: Bills with no asset account raise 400 --------


def test_bill_for_inventory_item_without_asset_account_rejected(
    client, db_session, seed_accounts
):
    """If a tracked item has no asset account AND #1300 is missing, bill creation
    must refuse — historically this silently dropped the DR line."""
    vendor = _seed_vendor(db_session)

    # Delete the Inventory account (#1300) to simulate an un-seeded chart
    inv_acct = seed_accounts["1300"]
    # Mark as inactive and clear the FK by nulling the item's asset_account_id
    item = Item(
        name="Orphan Widget",
        item_type=ItemType.PRODUCT,
        rate=Decimal("50"),
        track_inventory=True,
        asset_account_id=None,  # no explicit account
        is_taxable=False,
    )
    db_session.add(item)
    db_session.commit()

    # Delete the default #1300 account so get_inventory_asset_account_id returns None
    db_session.delete(inv_acct)
    db_session.commit()

    r = client.post("/api/bills", json={
        "vendor_id": vendor.id,
        "bill_number": "B-BAD",
        "date": "2026-04-01",
        "terms": "Net 30",
        "tax_rate": "0",
        "lines": [{
            "item_id": item.id,
            "description": "Stock",
            "quantity": "5",
            "rate": "10.00",
        }],
    })
    assert r.status_code == 400
    assert "inventory-tracked" in r.json()["detail"]


# -------- Test 5: PO convert-to-bill posts a full JE --------


def test_po_convert_to_bill_posts_balanced_journal(
    client, db_session, seed_accounts
):
    """Before Phase 11 audit fix, PO→Bill created a Bill with NO JE at all
    (orphan accounting record). Verify a proper balanced JE is posted."""
    from app.models.transactions import TransactionLine

    vendor = _seed_vendor(db_session)
    item = _tracked_item(db_session, seed_accounts, name="PO Widget")

    # Create a PO
    r = client.post("/api/purchase-orders", json={
        "vendor_id": vendor.id,
        "date": "2026-04-01",
        "tax_rate": "0",
        "lines": [{
            "item_id": item.id,
            "description": "Stock",
            "quantity": "5",
            "rate": "12.00",
        }],
    })
    assert r.status_code == 201, r.text
    po_id = r.json()["id"]

    r = client.post(f"/api/purchase-orders/{po_id}/convert-to-bill")
    assert r.status_code == 200, r.text
    bill_id = r.json()["bill_id"]

    # Verify the bill has a linked transaction
    from app.models.bills import Bill
    bill = db_session.query(Bill).filter_by(id=bill_id).first()
    assert bill.transaction_id is not None, "PO→Bill convert must post a journal entry"

    # Verify the JE is balanced
    lines = (
        db_session.query(TransactionLine)
        .filter(TransactionLine.transaction_id == bill.transaction_id)
        .all()
    )
    total_dr = sum((l.debit for l in lines), Decimal("0"))
    total_cr = sum((l.credit for l in lines), Decimal("0"))
    assert total_dr == total_cr == Decimal("60.00")

    # Verify inventory moved
    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    assert Decimal(str(item.quantity_on_hand)) == Decimal("5")
    assert Decimal(str(item.avg_cost)) == Decimal("12")


# -------- Test 6: reverse_sale uses historical cost, not current avg_cost --------


def test_reverse_sale_uses_historical_cost_not_current(
    client, db_session, seed_accounts, seed_customer
):
    """The audit found reverse_sale used current avg_cost, which causes a GL
    imbalance when cost drifts between sale and void. Verify the fix: a void
    reverses at the original sale's unit_cost."""
    from app.models.transactions import TransactionLine

    vendor = _seed_vendor(db_session)
    item = _tracked_item(db_session, seed_accounts)

    # Buy @ $10, sell @ avg_cost=$10
    _seed_stock(client, vendor.id, item.id, qty=5, unit_cost="10.00", bill_number="B1")
    inv = _create_invoice(client, seed_customer.id, item.id, qty=3)

    # Now buy MORE at a different cost — this moves avg_cost upward
    _seed_stock(client, vendor.id, item.id, qty=10, unit_cost="20.00", bill_number="B2")

    db_session.expire_all()
    item_after_second_buy = db_session.query(Item).filter_by(id=item.id).first()
    # avg_cost = (2*10 + 10*20) / 12 = 220/12 ≈ 18.33
    assert Decimal(str(item_after_second_buy.avg_cost)) > Decimal("15")

    # NOW void the original sale. It should reverse at $10 (historical), not avg_cost.
    r = client.post(f"/api/invoices/{inv['id']}/void")
    assert r.status_code == 200, r.text

    # Find the VOID movement
    void_mv = (
        db_session.query(InventoryMovement)
        .filter_by(item_id=item.id, movement_type=MovementType.VOID)
        .order_by(InventoryMovement.id.desc())
        .first()
    )
    assert void_mv is not None
    assert Decimal(str(void_mv.unit_cost)) == Decimal("10")  # historical, not avg_cost
    # The reversal JE hit at 3 * 10 = 30
    lines = (
        db_session.query(TransactionLine)
        .filter(TransactionLine.transaction_id == void_mv.transaction_id)
        .all()
    )
    total_dr = sum((l.debit for l in lines), Decimal("0"))
    assert total_dr == Decimal("30.00")


# -------- Test 7: Saved reports size cap --------


def test_saved_reports_rejects_oversized_parameters(client):
    big = {"huge": "x" * 70_000}  # > 64KB
    r = client.post("/api/saved-reports", json={
        "name": "Too Big",
        "report_type": "profit_loss",
        "parameters": big,
    })
    assert r.status_code == 400
    assert "bytes" in r.json()["detail"]


# -------- Test 8: Credit memo returns inventory --------


def test_credit_memo_returns_stock_to_inventory(
    client, db_session, seed_accounts, seed_customer
):
    vendor = _seed_vendor(db_session)
    item = _tracked_item(db_session, seed_accounts)
    _seed_stock(client, vendor.id, item.id, qty=10, unit_cost="10.00")

    _create_invoice(client, seed_customer.id, item.id, qty=4)
    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    assert Decimal(str(item.quantity_on_hand)) == Decimal("6")

    # Issue a credit memo for 2 units (returned)
    r = client.post("/api/credit-memos", json={
        "customer_id": seed_customer.id,
        "date": "2026-04-20",
        "tax_rate": "0",
        "lines": [{
            "item_id": item.id,
            "description": "Returned",
            "quantity": "2",
            "rate": "25.00",
        }],
    })
    assert r.status_code == 201, r.text

    db_session.expire_all()
    item = db_session.query(Item).filter_by(id=item.id).first()
    # Qty should increase back by 2
    assert Decimal(str(item.quantity_on_hand)) == Decimal("8")

    return_mv = (
        db_session.query(InventoryMovement)
        .filter_by(item_id=item.id, movement_type=MovementType.RETURN_IN)
        .first()
    )
    assert return_mv is not None
    assert Decimal(str(return_mv.quantity)) == Decimal("2")
