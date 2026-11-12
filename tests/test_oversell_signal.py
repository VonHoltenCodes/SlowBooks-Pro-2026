"""A sale past what is on hand says so (#642).

A sale posts whatever the count is: the inventory service allows a count
below zero on purpose (some businesses enter the receiving bill after the
sale), and the only place a negative count showed was the Items page's To
reorder list. Nothing said so at the point of sale. The posting stays as it
was; creating or editing an invoice, or entering a sales receipt, that takes
a stock item past what is on hand now answers with a ``warnings`` list of
plain sentences, and the setting ``inventory_refuse_oversell`` (off by
default) turns the warning into a 422 that names the item and the shortfall,
with nothing posted.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from app.models.invoices import Invoice
from app.models.items import InventoryMovement, Item, ItemType, MovementType

ROOT = Path(__file__).resolve().parents[1]


def _source(name):
    return (ROOT / "app" / "static" / name).read_text(encoding="utf-8")


def _stock_item(db_session, seed_accounts, on_hand, name="Widget", tracked=True):
    item = Item(
        name=name,
        item_type=ItemType.PRODUCT if tracked else ItemType.SERVICE,
        rate=Decimal("25.00"),
        cost=Decimal("10.00"),
        track_inventory=tracked,
        quantity_on_hand=Decimal(str(on_hand)),
        asset_account_id=seed_accounts["1300"].id,
        is_taxable=False,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def _lines(*pairs):
    return [
        {"item_id": item_id, "description": "Sold", "quantity": str(qty), "rate": "25"}
        for item_id, qty in pairs
    ]


def _sell(client, customer_id, lines):
    return client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-04-15",
            "terms": "Net 30",
            "tax_rate": "0",
            "lines": lines,
        },
    )


def _on_hand(db_session, item_id):
    db_session.expire_all()
    return Decimal(str(db_session.get(Item, item_id).quantity_on_hand))


def _sales(db_session, item_id):
    return (
        db_session.query(InventoryMovement)
        .filter_by(item_id=item_id, movement_type=MovementType.SALE)
        .count()
    )


def _refuse(client, on):
    r = client.put(
        "/api/settings", json={"inventory_refuse_oversell": "true" if on else "false"}
    )
    assert r.status_code == 200, r.text


# -------------------------------------------------------------------------
# The signal
# -------------------------------------------------------------------------


def test_a_sale_past_the_stock_posts_and_says_so(
    client, db_session, seed_accounts, seed_customer
):
    item = _stock_item(db_session, seed_accounts, on_hand=2)
    r = _sell(client, seed_customer.id, _lines((item.id, 6)))
    assert r.status_code == 201, r.text
    assert r.json()["warnings"] == ["Widget: 2 on hand; this sale takes it to -4."]
    # posted as before: the stock moved and the invoice is there
    assert _on_hand(db_session, item.id) == Decimal("-4")
    assert _sales(db_session, item.id) == 1
    assert db_session.get(Invoice, r.json()["id"]) is not None


@pytest.mark.parametrize("qty, left", [("3", "2"), ("5", "0")])
def test_a_sale_within_the_stock_has_no_warning(
    client, db_session, seed_accounts, seed_customer, qty, left
):
    """Landing exactly on zero is not past the stock."""
    item = _stock_item(db_session, seed_accounts, on_hand=5)
    r = _sell(client, seed_customer.id, _lines((item.id, qty)))
    assert r.status_code == 201, r.text
    assert r.json()["warnings"] == []
    assert _on_hand(db_session, item.id) == Decimal(left)


def test_two_lines_of_one_item_count_together(
    client, db_session, seed_accounts, seed_customer
):
    item = _stock_item(db_session, seed_accounts, on_hand=3)
    r = _sell(client, seed_customer.id, _lines((item.id, 2), (item.id, 2)))
    assert r.status_code == 201, r.text
    assert r.json()["warnings"] == ["Widget: 3 on hand; this sale takes it to -1."]


def test_an_untracked_item_is_never_short(
    client, db_session, seed_accounts, seed_customer
):
    service = _stock_item(
        db_session, seed_accounts, on_hand=0, name="Consulting", tracked=False
    )
    r = _sell(client, seed_customer.id, _lines((service.id, 4)))
    assert r.status_code == 201, r.text
    assert r.json()["warnings"] == []


def test_a_count_already_below_zero_warns_with_the_count_it_has(
    client, db_session, seed_accounts, seed_customer
):
    item = _stock_item(db_session, seed_accounts, on_hand=-2)
    r = _sell(client, seed_customer.id, _lines((item.id, 3)))
    assert r.status_code == 201, r.text
    assert r.json()["warnings"] == ["Widget: -2 on hand; this sale takes it to -5."]


# -------------------------------------------------------------------------
# The setting: refuse instead
# -------------------------------------------------------------------------


def test_with_the_setting_on_the_sale_is_refused_and_nothing_posts(
    client, db_session, seed_accounts, seed_customer
):
    item = _stock_item(db_session, seed_accounts, on_hand=2)
    _refuse(client, True)
    before = db_session.query(Invoice).count()
    r = _sell(client, seed_customer.id, _lines((item.id, 6)))
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail.startswith("Widget: 2 on hand; this sale takes it to -4, 4 short.")
    assert "Settings" in detail
    assert db_session.query(Invoice).count() == before
    assert _on_hand(db_session, item.id) == Decimal("2")
    assert _sales(db_session, item.id) == 0
    # within the stock, the setting changes nothing
    r = _sell(client, seed_customer.id, _lines((item.id, 2)))
    assert r.status_code == 201, r.text
    assert r.json()["warnings"] == []


def test_the_refusal_names_every_short_item(
    client, db_session, seed_accounts, seed_customer
):
    widget = _stock_item(db_session, seed_accounts, on_hand=1)
    gadget = _stock_item(db_session, seed_accounts, on_hand=0, name="Gadget")
    _refuse(client, True)
    r = _sell(client, seed_customer.id, _lines((widget.id, 2), (gadget.id, 1)))
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "Widget: 1 on hand; this sale takes it to -1, 1 short." in detail
    assert "Gadget: 0 on hand; this sale takes it to -1, 1 short." in detail


# -------------------------------------------------------------------------
# Editing an invoice: only what the edit adds counts
# -------------------------------------------------------------------------


def test_an_edit_counts_only_what_it_adds(
    client, db_session, seed_accounts, seed_customer
):
    item = _stock_item(db_session, seed_accounts, on_hand=5)
    inv = _sell(client, seed_customer.id, _lines((item.id, 3))).json()
    assert inv["warnings"] == []
    assert _on_hand(db_session, item.id) == Decimal("2")

    def edit(qty):
        return client.put(
            f"/api/invoices/{inv['id']}",
            json={"tax_rate": "0", "lines": _lines((item.id, qty))},
        )

    # 3 -> 4 takes one more; there are 2
    r = edit(4)
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == []
    assert _on_hand(db_session, item.id) == Decimal("1")
    # 4 -> 7 takes three more; there is 1
    r = edit(7)
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == ["Widget: 1 on hand; this sale takes it to -2."]
    assert _on_hand(db_session, item.id) == Decimal("-2")
    # an edit that gives stock back never warns
    r = edit(1)
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == []
    assert _on_hand(db_session, item.id) == Decimal("4")


def test_with_the_setting_on_an_edit_past_the_stock_keeps_the_invoice_as_it_was(
    client, db_session, seed_accounts, seed_customer
):
    item = _stock_item(db_session, seed_accounts, on_hand=5)
    inv = _sell(client, seed_customer.id, _lines((item.id, 3))).json()
    _refuse(client, True)
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={"tax_rate": "0", "lines": _lines((item.id, 9))},
    )
    assert r.status_code == 422, r.text
    assert "Widget: 2 on hand; this sale takes it to -4, 4 short." in r.json()["detail"]
    assert _on_hand(db_session, item.id) == Decimal("2")
    stored = client.get(f"/api/invoices/{inv['id']}").json()
    assert [line["quantity"] for line in stored["lines"]] == ["3.00"]
    assert stored["warnings"] == []  # a read carries none


# -------------------------------------------------------------------------
# Sales receipts
# -------------------------------------------------------------------------


def test_a_sales_receipt_carries_the_warning_and_the_refusal(
    client, db_session, seed_accounts, seed_customer
):
    item = _stock_item(db_session, seed_accounts, on_hand=1)
    body = {
        "customer_id": seed_customer.id,
        "date": "2026-04-15",
        "tax_rate": "0",
        "method": "Cash",
        "lines": _lines((item.id, 3)),
    }
    r = client.post("/api/sales-receipts", json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["warnings"] == ["Widget: 1 on hand; this sale takes it to -2."]
    assert data["invoice"]["is_sales_receipt"] is True
    assert _on_hand(db_session, item.id) == Decimal("-2")

    _refuse(client, True)
    before = db_session.query(Invoice).count()
    r = client.post("/api/sales-receipts", json=body)
    assert r.status_code == 422, r.text
    # "short" is this sale's three, not the five below zero (#813)
    assert (
        "Widget: -2 on hand; this sale takes it to -5, 3 short." in r.json()["detail"]
    )
    assert db_session.query(Invoice).count() == before
    assert _on_hand(db_session, item.id) == Decimal("-2")


# -------------------------------------------------------------------------
# The Items page and To reorder
# -------------------------------------------------------------------------


def test_to_reorder_keeps_a_count_below_zero(client, db_session, seed_accounts):
    item = _stock_item(db_session, seed_accounts, on_hand=-4)
    rows = client.get("/api/items/low-stock").json()
    assert [(r["id"], Decimal(r["shortage"])) for r in rows] == [(item.id, Decimal(4))]


def test_the_items_page_marks_a_count_below_zero():
    src = _source("js/items.js")
    # the count in the warning colour, with the title saying what it means
    assert 'class="qty-below-zero" style="color:var(--text-warning);"' in src
    assert "Below zero: more has been sold than received." in src
    assert 'title="${escapeHtml(ItemsPage.belowZeroTitle)}"' in src
    # the item's form, Adjust and History say it in words
    assert "more of this item has been sold than received" in src
    assert src.count("${ItemsPage.belowZeroHtml(item)}") == 3


def test_the_forms_show_the_warnings_and_the_count_on_hand():
    invoices = _source("js/invoices.js")
    receipts = _source("js/sales_receipts.js")
    for src, saved in ((invoices, "saved"), (receipts, "result")):
        assert (
            f"for (const w of ({saved} && {saved}.warnings) || []) toast(w, 'warning');"
            in src
        )
        assert "${SalesLines.onHandHtml(line.item_id, items)}" in src
    assert "SalesLines.showOnHand(row, InvoicesPage._items)" in invoices
    assert "SalesLines.showOnHand(row, SalesReceiptsPage._items)" in receipts
    assert "on hand`" in invoices  # the note's words
    assert ".toast-warning" in _source("css/style.css")
    assert ".toast-warning" in _source("css/dark.css")


# -------------------------------------------------------------------------
# The setting round-trips
# -------------------------------------------------------------------------


def test_the_setting_round_trips(client):
    assert client.get("/api/settings").json()["inventory_refuse_oversell"] == "false"
    _refuse(client, True)
    assert client.get("/api/settings").json()["inventory_refuse_oversell"] == "true"
    _refuse(client, False)
    assert client.get("/api/settings").json()["inventory_refuse_oversell"] == "false"
    r = client.put("/api/settings", json={"inventory_refuse_oversell": "banana"})
    assert r.status_code == 422
    settings_js = _source("js/settings.js")
    assert 'name="inventory_refuse_oversell" value="true" data-bool' in settings_js
    assert "Refuse a sale of more than is on hand" in settings_js


# -------------------------------------------------------------------------
# A bill's void or edit after its stock has sold (W-67, 2.31.1 gate)
# -------------------------------------------------------------------------


def _received(client, db_session, seed_accounts, qty=5, bill_number="B-1"):
    """A stock item with no history, and a bill that brings `qty` at 10.00."""
    from tests.test_inventory import _create_bill_for_inventory, _seed_vendor

    vendor = _seed_vendor(db_session)
    item = _stock_item(db_session, seed_accounts, on_hand=0)
    bill = _create_bill_for_inventory(
        client, vendor.id, item.id, qty=qty, unit_cost="10.00", bill_number=bill_number
    )
    return vendor, item, bill


def _bill_lines(item_id, qty):
    return [
        {
            "item_id": item_id,
            "description": "Stock receipt",
            "quantity": str(qty),
            "rate": "10.00",
        }
    ]


def _movement_count(db_session, item_id):
    return db_session.query(InventoryMovement).filter_by(item_id=item_id).count()


def test_a_bill_void_after_its_stock_has_sold_says_so(
    client, db_session, seed_accounts, seed_customer
):
    """Five received, three sold, two on hand: the void takes the five back
    out, posts as before (-3 on hand), and the response says so."""
    _vendor, item, bill = _received(client, db_session, seed_accounts)
    assert _sell(client, seed_customer.id, _lines((item.id, 3))).status_code == 201
    r = client.post(f"/api/bills/{bill['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"
    assert r.json()["warnings"] == ["Widget: 2 on hand; this void takes it to -3."]
    assert _on_hand(db_session, item.id) == Decimal("-3")


def test_a_bill_void_within_the_stock_has_no_warning(
    client, db_session, seed_accounts, seed_customer
):
    from tests.test_inventory import _create_bill_for_inventory

    vendor, item, first = _received(client, db_session, seed_accounts)
    _create_bill_for_inventory(
        client, vendor.id, item.id, qty=5, unit_cost="10.00", bill_number="B-2"
    )
    assert _sell(client, seed_customer.id, _lines((item.id, 3))).status_code == 201
    r = client.post(f"/api/bills/{first['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == []
    assert _on_hand(db_session, item.id) == Decimal("2")


def test_a_bill_edit_below_what_has_sold_says_so(
    client, db_session, seed_accounts, seed_customer
):
    """Five received, three sold: editing the bill to one takes four back
    out of the two on hand. Posted, and said."""
    _vendor, item, bill = _received(client, db_session, seed_accounts)
    assert _sell(client, seed_customer.id, _lines((item.id, 3))).status_code == 201
    r = client.put(f"/api/bills/{bill['id']}", json={"lines": _bill_lines(item.id, 1)})
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == ["Widget: 2 on hand; this edit takes it to -2."]
    assert _on_hand(db_session, item.id) == Decimal("-2")


def test_a_bill_edit_that_keeps_what_has_sold_says_nothing(
    client, db_session, seed_accounts, seed_customer
):
    """Editing five down to three with three sold lands on zero: not short.
    A header-only edit moves no stock at all."""
    _vendor, item, bill = _received(client, db_session, seed_accounts)
    assert _sell(client, seed_customer.id, _lines((item.id, 3))).status_code == 201
    r = client.put(f"/api/bills/{bill['id']}", json={"lines": _bill_lines(item.id, 3)})
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == []
    assert _on_hand(db_session, item.id) == Decimal("0")
    r = client.put(f"/api/bills/{bill['id']}", json={"notes": "same stock"})
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == []
    assert _on_hand(db_session, item.id) == Decimal("0")


def test_with_the_setting_on_a_bill_void_past_the_stock_is_refused_and_nothing_posts(
    client, db_session, seed_accounts, seed_customer
):
    from tests.test_credit_memo_return_cogs import _entries

    _vendor, item, bill = _received(client, db_session, seed_accounts)
    sale = _sell(client, seed_customer.id, _lines((item.id, 3)))
    assert sale.status_code == 201
    _refuse(client, True)
    before = _movement_count(db_session, item.id)

    r = client.post(f"/api/bills/{bill['id']}/void")

    assert r.status_code == 422, r.text
    assert r.json()["detail"] == (
        "Widget: 2 on hand; this void takes it to -3, 3 short. Void the sale "
        "that took the stock first, or turn off Refuse a sale of more than is "
        "on hand under Settings."
    )
    assert client.get(f"/api/bills/{bill['id']}").json()["status"] == "unpaid"
    assert _on_hand(db_session, item.id) == Decimal("2")
    assert _movement_count(db_session, item.id) == before
    assert _entries(db_session, "bill_void", bill["id"]) == []
    # the sale voided first, the bill's void is within the stock
    assert client.post(f"/api/invoices/{sale.json()['id']}/void").status_code == 200
    r = client.post(f"/api/bills/{bill['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == []
    assert _on_hand(db_session, item.id) == Decimal("0")


def test_with_the_setting_on_a_bill_edit_past_the_stock_keeps_the_bill_as_it_was(
    client, db_session, seed_accounts, seed_customer
):
    _vendor, item, bill = _received(client, db_session, seed_accounts)
    assert _sell(client, seed_customer.id, _lines((item.id, 3))).status_code == 201
    _refuse(client, True)
    before = _movement_count(db_session, item.id)

    r = client.put(f"/api/bills/{bill['id']}", json={"lines": _bill_lines(item.id, 1)})

    assert r.status_code == 422, r.text
    assert r.json()["detail"] == (
        "Widget: 2 on hand; this edit takes it to -2, 2 short. Keep at least "
        "what has sold on the bill, void the sale that took it, or turn off "
        "Refuse a sale of more than is on hand under Settings."
    )
    kept = client.get(f"/api/bills/{bill['id']}").json()
    assert [Decimal(ln["quantity"]) for ln in kept["lines"]] == [Decimal("5")]
    assert Decimal(kept["total"]) == Decimal("50.00")
    assert _on_hand(db_session, item.id) == Decimal("2")
    assert _movement_count(db_session, item.id) == before
    # down to what has sold is within the stock
    r = client.put(f"/api/bills/{bill['id']}", json={"lines": _bill_lines(item.id, 3)})
    assert r.status_code == 200, r.text
    assert r.json()["warnings"] == []
    assert _on_hand(db_session, item.id) == Decimal("0")


def test_the_bills_page_shows_the_warnings():
    src = _source("js/bills.js")
    assert (
        "for (const w of (saved && saved.warnings) || []) toast(w, 'warning');" in src
    )
    assert (
        "for (const w of (voided && voided.warnings) || []) toast(w, 'warning');" in src
    )
