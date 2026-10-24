"""Sales by Item and Purchases by Item (#435).

Two QuickBooks reports: what sold, by item, and what was bought, by item.
The fixture builds a tracked product (stock bought through a bill, so
avg_cost and inventory movements exist), a service item, invoices (one a
sales receipt), a voided invoice, a credit memo returning the tracked item,
a no-item line, bills for both items and a vendor credit, with a class and a
job on some lines — enough to pin the rows, the totals, the COGS/margin from
movements, the narrowing and the drill-down.
"""

from decimal import Decimal

import pytest

from app.models.contacts import Customer, Vendor
from app.models.items import Item, ItemType

PERIOD = "start_date=2026-07-01&end_date=2026-07-31"


def _item(db_session, seed_accounts, name, item_type, *, tracked, taxable=False):
    it = Item(
        name=name,
        item_type=item_type,
        rate=Decimal("0"),
        track_inventory=tracked,
        asset_account_id=seed_accounts["1300"].id if tracked else None,
        income_account_id=seed_accounts["4000"].id,
        expense_account_id=seed_accounts["5000"].id,
        is_taxable=taxable,
    )
    db_session.add(it)
    db_session.commit()
    db_session.refresh(it)
    return it


def _bill(client, vendor_id, number, date, lines, status_ok=(201,)):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "bill_number": number,
            "date": date,
            "terms": "Net 30",
            "tax_rate": "0",
            "lines": lines,
        },
    )
    assert r.status_code in status_ok, r.text
    return r.json()


def _invoice(client, customer_id, date, lines, **header):
    body = {"customer_id": customer_id, "date": date, "tax_rate": "0", "lines": lines}
    body.update(header)
    r = client.post("/api/invoices", json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def data(client, db_session, seed_accounts):
    customer_a = Customer(name="Acme", is_active=True)
    customer_b = Customer(name="Beta", is_active=True)
    vendor = Vendor(name="Widget Supplier", is_active=True)
    vendor2 = Vendor(name="Other Supplier", is_active=True)
    db_session.add_all([customer_a, customer_b, vendor, vendor2])
    db_session.commit()
    for obj in (customer_a, customer_b, vendor, vendor2):
        db_session.refresh(obj)

    tracked = _item(db_session, seed_accounts, "Gadget", ItemType.PRODUCT, tracked=True)
    # A formula-shaped name proves the CSV guard.
    service = _item(
        db_session, seed_accounts, "=Install", ItemType.SERVICE, tracked=False
    )

    cls1 = client.post("/api/classes", json={"name": "Retail"}).json()
    cls2 = client.post("/api/classes", json={"name": "Wholesale"}).json()
    job1 = client.post(
        "/api/jobs", json={"customer_id": customer_a.id, "name": "Kitchen"}
    ).json()

    # ---- Purchases: stock bought through bills, plus a vendor credit ----
    # Seed 10 Gadgets @ $10 (gives avg_cost 10 and a purchase movement).
    _bill(
        client,
        vendor.id,
        "B-SEED",
        "2026-07-01",
        [
            {
                "item_id": tracked.id,
                "description": "Stock",
                "quantity": "10",
                "rate": "10",
            }
        ],
    )
    # Service bought on a bill (no inventory movement — untracked).
    _bill(
        client,
        vendor.id,
        "B-SVC",
        "2026-07-02",
        [
            {
                "item_id": service.id,
                "description": "Install",
                "quantity": "2",
                "rate": "40",
            }
        ],
    )
    # Same service from another vendor, to prove the vendor_id filter.
    _bill(
        client,
        vendor2.id,
        "B-SVC2",
        "2026-07-02",
        [
            {
                "item_id": service.id,
                "description": "Install",
                "quantity": "1",
                "rate": "40",
            }
        ],
    )
    # A vendor credit returns one Gadget @ $10.
    vc = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": vendor.id,
            "date": "2026-07-03",
            "tax_rate": "0",
            "lines": [
                {
                    "item_id": tracked.id,
                    "description": "Return",
                    "quantity": "1",
                    "rate": "10",
                }
            ],
        },
    )
    assert vc.status_code == 201, vc.text

    # ---- Sales ----
    # INV1: Gadget qty 2 @ 50, line class Retail, header class Wholesale.
    inv1 = _invoice(
        client,
        customer_a.id,
        "2026-07-05",
        [
            {
                "item_id": tracked.id,
                "description": "Sold gadgets",
                "quantity": "2",
                "rate": "50",
                "class_id": cls1["id"],
            }
        ],
        class_id=cls2["id"],
    )
    # Invoice B: service qty 1 @ 80 for customer B.
    _invoice(
        client,
        customer_b.id,
        "2026-07-08",
        [
            {
                "item_id": service.id,
                "description": "Install",
                "quantity": "1",
                "rate": "80",
            }
        ],
    )
    # Sales receipt: service qty 1 @ 80, header job Kitchen.
    sr = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": customer_a.id,
            "date": "2026-07-10",
            "tax_rate": "0",
            "deposit_to_account_id": seed_accounts["1010"].id,
            "job_id": job1["id"],
            "lines": [
                {
                    "item_id": service.id,
                    "description": "Install",
                    "quantity": "1",
                    "rate": "80",
                }
            ],
        },
    )
    assert sr.status_code in (200, 201), sr.text
    # INV2: a line with no item, header class Wholesale (line falls back to it).
    _invoice(
        client,
        customer_a.id,
        "2026-07-12",
        [{"description": "Misc charge", "quantity": "1", "rate": "30"}],
        class_id=cls2["id"],
    )
    # A voided invoice must be left out entirely.
    void = _invoice(
        client,
        customer_a.id,
        "2026-07-15",
        [{"item_id": tracked.id, "description": "Oops", "quantity": "5", "rate": "50"}],
    )
    vr = client.post(f"/api/invoices/{void['id']}/void")
    assert vr.status_code == 200, vr.text
    # Credit memo: one Gadget returned @ 50, customer A.
    cm = client.post(
        "/api/credit-memos",
        json={
            "customer_id": customer_a.id,
            "date": "2026-07-20",
            "tax_rate": "0",
            "lines": [
                {
                    "item_id": tracked.id,
                    "description": "Returned gadget",
                    "quantity": "1",
                    "rate": "50",
                }
            ],
        },
    )
    assert cm.status_code == 201, cm.text

    return {
        "customer_a": customer_a.id,
        "customer_b": customer_b.id,
        "vendor": vendor.id,
        "vendor2": vendor2.id,
        "tracked": tracked.id,
        "service": service.id,
        "cls1": cls1["id"],
        "cls2": cls2["id"],
        "job1": job1["id"],
        "inv1": inv1["id"],
    }


def _row(items, item_id):
    return next(r for r in items if r["item_id"] == item_id)


# ── Sales ────────────────────────────────────────────────────────────────


def test_sales_rows_totals_void_and_credit_memo(client, data):
    d = client.get(f"/api/reports/sales-by-item?{PERIOD}").json()
    items = d["items"]

    gadget = _row(items, data["tracked"])
    # 2 sold on INV1 less 1 returned on the credit memo; the voided invoice
    # (5 gadgets) is left out.
    assert gadget["quantity"] == 1.0
    assert gadget["amount"] == 50.0
    assert gadget["average_price"] == 50.0

    service = _row(items, data["service"])
    assert service["quantity"] == 2.0  # customer B invoice + the sales receipt
    assert service["amount"] == 160.0

    # Total amount ties to the P&L income for the same period.
    assert d["total_amount"] == 240.0
    pl = client.get(f"/api/reports/profit-loss?{PERIOD}").json()
    assert d["total_amount"] == pl["total_income"]


def test_no_item_row_is_present_and_last(client, data):
    items = client.get(f"/api/reports/sales-by-item?{PERIOD}").json()["items"]
    no_item = items[-1]
    assert no_item["item_id"] is None
    assert no_item["item_name"] == "No item"
    assert no_item["amount"] == 30.0


def test_cogs_and_margin_from_movements_for_tracked_only(client, data):
    d = client.get(f"/api/reports/sales-by-item?{PERIOD}").json()
    gadget = _row(d["items"], data["tracked"])
    # Sold 2 @ cost 10 = 20 COGS. The credit memo puts one back on the shelf
    # with no COGS entry (post_return_for_credit_memo), so the P&L's COGS
    # stays 20 and so does the report's. Margin = 50 amount − 20 = 30, 60%.
    assert gadget["cogs"] == 20.0
    assert gadget["gross_margin"] == 30.0
    assert round(gadget["margin_pct"], 1) == 60.0
    assert d["total_cogs"] == 20.0

    service = _row(d["items"], data["service"])
    assert service["cogs"] is None
    assert service["gross_margin"] is None
    assert service["margin_pct"] is None


def test_sales_sorted_by_amount_desc(client, data):
    items = client.get(f"/api/reports/sales-by-item?{PERIOD}").json()["items"]
    with_items = [r for r in items if r["item_id"] is not None]
    amounts = [r["amount"] for r in with_items]
    assert amounts == sorted(amounts, reverse=True)


# ── Narrowing ──────────────────────────────────────────────────────────────


def test_customer_id_narrows(client, data):
    d = client.get(
        f"/api/reports/sales-by-item?{PERIOD}&customer_id={data['customer_a']}"
    ).json()
    service = _row(d["items"], data["service"])
    assert service["quantity"] == 1.0  # only the sales receipt, not customer B
    assert service["amount"] == 80.0


def test_class_id_line_wins_over_header(client, data):
    # INV1's gadget line carries Retail; the header is Wholesale.
    retail = client.get(
        f"/api/reports/sales-by-item?{PERIOD}&class_id={data['cls1']}"
    ).json()
    gadget = _row(retail["items"], data["tracked"])
    assert gadget["amount"] == 100.0  # the credit memo has no class, so not here

    # INV2's no-item line has no class of its own and falls back to the
    # header's Wholesale.
    wholesale = client.get(
        f"/api/reports/sales-by-item?{PERIOD}&class_id={data['cls2']}"
    ).json()
    no_item = _row(wholesale["items"], None)
    assert no_item["amount"] == 30.0


def test_job_id_narrows(client, data):
    d = client.get(f"/api/reports/sales-by-item?{PERIOD}&job_id={data['job1']}").json()
    # Only the sales receipt carries the Kitchen job.
    assert len(d["items"]) == 1
    assert _row(d["items"], data["service"])["amount"] == 80.0


def test_unknown_narrowing_id_is_404(client, data):
    for q in ("customer_id=99999", "class_id=99999", "job_id=99999"):
        r = client.get(f"/api/reports/sales-by-item?{PERIOD}&{q}")
        assert r.status_code == 404, q
    r = client.get(f"/api/reports/purchases-by-item?{PERIOD}&vendor_id=99999")
    assert r.status_code == 404


# ── Purchases ────────────────────────────────────────────────────────────


def test_purchases_rows_vendor_credit_and_average_cost(client, data):
    d = client.get(f"/api/reports/purchases-by-item?{PERIOD}").json()
    gadget = _row(d["items"], data["tracked"])
    # 10 bought less 1 returned on the vendor credit.
    assert gadget["quantity"] == 9.0
    assert gadget["amount"] == 90.0
    assert gadget["average_cost"] == 10.0

    service = _row(d["items"], data["service"])
    assert service["quantity"] == 3.0  # both suppliers
    assert service["amount"] == 120.0
    assert d["total_amount"] == 210.0


def test_purchases_vendor_id_filter(client, data):
    d = client.get(
        f"/api/reports/purchases-by-item?{PERIOD}&vendor_id={data['vendor']}"
    ).json()
    service = _row(d["items"], data["service"])
    assert service["quantity"] == 2.0  # only the first supplier
    assert service["amount"] == 80.0


# ── Lines endpoint ───────────────────────────────────────────────────────


def test_sales_lines_behind_a_row(client, data):
    d = client.get(
        f"/api/reports/sales-by-item/lines?item_id={data['tracked']}&{PERIOD}"
    ).json()
    kinds = sorted(line["doc_type"] for line in d["lines"])
    assert kinds == ["Credit Memo", "Invoice"]
    cm = next(line for line in d["lines"] if line["doc_type"] == "Credit Memo")
    assert cm["quantity"] == -1.0 and cm["amount"] == -50.0
    assert cm["link"]["type"] == "credit_memo"
    inv = next(line for line in d["lines"] if line["doc_type"] == "Invoice")
    assert inv["link"] == {"type": "invoice", "id": data["inv1"]}


def test_sales_lines_for_no_item(client, data):
    d = client.get(f"/api/reports/sales-by-item/lines?item_id=none&{PERIOD}").json()
    assert len(d["lines"]) == 1
    assert d["lines"][0]["amount"] == 30.0


def test_purchases_lines_behind_a_row(client, data):
    d = client.get(
        f"/api/reports/purchases-by-item/lines?item_id={data['tracked']}&{PERIOD}"
    ).json()
    kinds = sorted(line["doc_type"] for line in d["lines"])
    assert kinds == ["Bill", "Vendor Credit"]
    vc = next(line for line in d["lines"] if line["doc_type"] == "Vendor Credit")
    assert vc["amount"] == -10.0 and vc["link"]["type"] == "vendor_credit"


# ── CSV / PDF ──────────────────────────────────────────────────────────────


def test_sales_csv_header_and_formula_guard(client, data):
    csv = client.get(f"/api/reports/sales-by-item/csv?{PERIOD}").text
    header = next(
        line for line in csv.splitlines() if line.startswith("Item,Type,Quantity")
    )
    assert "Amount" in header and "Margin %" in header
    # The "=Install" item name is neutralized against spreadsheet formulas.
    assert any(line.startswith("'=Install") for line in csv.splitlines())


def test_pdf_layout_rows_without_weasyprint(client, data):
    from app.services.ledger_exports import (
        purchases_by_item_sections,
        sales_by_item_sections,
    )

    sales = client.get(f"/api/reports/sales-by-item?{PERIOD}").json()
    sections = sales_by_item_sections(sales)
    assert len(sections) == 1
    section = sections[0]
    assert section["title"] == "Sales by Item"
    assert len(section["columns"]) == 8
    total = section["rows"][-1]
    assert total["cells"][0] == "TOTAL"
    assert all(len(r["cells"]) == 8 for r in section["rows"])

    purchases = client.get(f"/api/reports/purchases-by-item?{PERIOD}").json()
    psec = purchases_by_item_sections(purchases)[0]
    assert psec["title"] == "Purchases by Item"
    assert psec["rows"][-1]["cells"][0] == "TOTAL"


# ── Saved reports ──────────────────────────────────────────────────────────


def test_saved_report_types_allowed(client, seed_accounts):
    for rt in ("sales_by_item", "purchases_by_item"):
        r = client.post(
            "/api/saved-reports",
            json={"name": rt, "report_type": rt, "parameters": {}},
        )
        assert r.status_code == 201, r.text


def test_js_cards_views_and_saved_types_present():
    from pathlib import Path

    from app.routes.saved_reports import _ALLOWED_TYPES

    js = (
        Path(__file__).resolve().parent.parent / "app/static/js/reports.js"
    ).read_text(encoding="utf-8")
    for view in ("sales-by-item", "purchases-by-item"):
        assert f"card('{view}', " in js, f"missing card for {view}"
        assert f"'{view}':" in js, f"missing view for {view}"
    for rt in ("sales_by_item", "purchases_by_item"):
        assert rt in _ALLOWED_TYPES


# ── #435 review: an edited sale, income-only lines, fiscal default ────────


@pytest.fixture
def stocked(client, db_session, seed_accounts):
    """A tracked Widget with 10 in stock at $4 and a customer."""
    customer = Customer(name="Gamma", is_active=True)
    vendor = Vendor(name="Widget Works", is_active=True)
    db_session.add_all([customer, vendor])
    db_session.commit()
    widget = _item(db_session, seed_accounts, "Widget", ItemType.PRODUCT, tracked=True)
    _bill(
        client,
        vendor.id,
        "W-1",
        "2026-07-01",
        [{"item_id": widget.id, "description": "Stock", "quantity": "10", "rate": "4"}],
    )
    return {"customer": customer.id, "widget": widget.id}


def _sold(client, stocked, qty):
    return _invoice(
        client,
        stocked["customer"],
        "2026-07-06",
        [
            {
                "item_id": stocked["widget"],
                "description": "Widgets",
                "quantity": qty,
                "rate": "10",
            }
        ],
    )


def _edit_quantity(client, inv, item_id, qty):
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={
            "lines": [
                {
                    "item_id": item_id,
                    "description": "Widgets",
                    "quantity": qty,
                    "rate": "10",
                }
            ]
        },
    )
    assert r.status_code == 200, r.text


def test_an_edit_that_lowers_the_quantity_lowers_cogs(client, stocked):
    """Sell 5, edit to 3: the edit's reversal ("invoice_edit") is counted,
    so COGS is the cost of 3 and equals the P&L's COGS."""
    inv = _sold(client, stocked, "5")
    _edit_quantity(client, inv, stocked["widget"], "3")
    d = client.get(f"/api/reports/sales-by-item?{PERIOD}").json()
    widget = _row(d["items"], stocked["widget"])
    assert widget["quantity"] == 3.0
    assert widget["amount"] == 30.0
    assert widget["cogs"] == 12.0  # 3 @ $4, not 5
    assert widget["gross_margin"] == 18.0
    pl = client.get(f"/api/reports/profit-loss?{PERIOD}").json()
    assert d["total_cogs"] == pl["total_cogs"] == 12.0
    assert d["total_amount"] == pl["total_income"]


def test_an_edit_that_raises_the_quantity_raises_cogs(client, stocked):
    inv = _sold(client, stocked, "2")
    _edit_quantity(client, inv, stocked["widget"], "6")
    d = client.get(f"/api/reports/sales-by-item?{PERIOD}").json()
    widget = _row(d["items"], stocked["widget"])
    assert widget["quantity"] == 6.0
    assert widget["cogs"] == 24.0
    pl = client.get(f"/api/reports/profit-loss?{PERIOD}").json()
    assert d["total_cogs"] == pl["total_cogs"]


def test_cogs_reads_the_movements_once_for_every_item(
    client, db_session, seed_accounts, stocked
):
    """Two tracked items: one read of inventory_movements, not one per item
    (each item's whole history)."""
    from sqlalchemy import event

    other = _item(db_session, seed_accounts, "Sprocket", ItemType.PRODUCT, tracked=True)
    vendor = db_session.query(Vendor).filter(Vendor.name == "Widget Works").one()
    _bill(
        client,
        vendor.id,
        "S-1",
        "2026-07-01",
        [{"item_id": other.id, "description": "Stock", "quantity": "5", "rate": "2"}],
    )
    _invoice(
        client,
        stocked["customer"],
        "2026-07-07",
        [
            {
                "item_id": stocked["widget"],
                "description": "W",
                "quantity": "1",
                "rate": "10",
            },
            {"item_id": other.id, "description": "S", "quantity": "2", "rate": "5"},
        ],
    )
    engine = db_session.get_bind()
    seen = []

    def count(conn, cursor, statement, *args):
        if "FROM inventory_movements" in statement:
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        d = client.get(f"/api/reports/sales-by-item?{PERIOD}").json()
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(seen) == 1, seen
    assert _row(d["items"], stocked["widget"])["cogs"] == 4.0
    assert _row(d["items"], other.id)["cogs"] == 4.0


def test_only_lines_posting_to_income_count(client, db_session, seed_accounts, stocked):
    """An item pointed at a liability (a deposit held) posts there, not to
    income: it is not a sale, and the report still ties to the P&L income."""
    deposit = Item(
        name="Deposit held",
        item_type=ItemType.SERVICE,
        rate=Decimal("0"),
        track_inventory=False,
        income_account_id=seed_accounts["2500"].id,
        is_taxable=False,
    )
    db_session.add(deposit)
    db_session.commit()
    _invoice(
        client,
        stocked["customer"],
        "2026-07-09",
        [
            {
                "item_id": deposit.id,
                "description": "Deposit",
                "quantity": "1",
                "rate": "500",
            },
            {
                "item_id": stocked["widget"],
                "description": "W",
                "quantity": "1",
                "rate": "10",
            },
            {"description": "Freight", "quantity": "1", "rate": "15"},
        ],
    )
    client.post(
        "/api/credit-memos",
        json={
            "customer_id": stocked["customer"],
            "date": "2026-07-11",
            "tax_rate": "0",
            "lines": [
                {
                    "item_id": deposit.id,
                    "description": "Deposit back",
                    "quantity": "1",
                    "rate": "200",
                }
            ],
        },
    ).raise_for_status()
    d = client.get(f"/api/reports/sales-by-item?{PERIOD}").json()
    assert deposit.id not in [r["item_id"] for r in d["items"]]
    assert d["total_amount"] == 25.0  # the widget and the no-item freight
    pl = client.get(f"/api/reports/profit-loss?{PERIOD}").json()
    assert d["total_amount"] == pl["total_income"]
    lines = client.get(
        f"/api/reports/sales-by-item/lines?item_id={deposit.id}&{PERIOD}"
    ).json()["lines"]
    assert lines == []


def test_a_write_off_is_not_a_sales_return(client, stocked):
    """A write-off credit memo posts to bad debt, not income: the sale it
    forgives stays in Sales by Item, as it stays in the P&L's income."""
    inv = _invoice(
        client,
        stocked["customer"],
        "2026-07-09",
        [{"description": "Consulting", "quantity": "1", "rate": "100"}],
    )
    r = client.post(
        f"/api/invoices/{inv['id']}/write-off",
        json={"date": "2026-07-20", "amount": "40"},
    )
    assert r.status_code == 201, r.text
    d = client.get(f"/api/reports/sales-by-item?{PERIOD}").json()
    assert _row(d["items"], None)["amount"] == 100.0
    pl = client.get(f"/api/reports/profit-loss?{PERIOD}").json()
    assert d["total_amount"] == pl["total_income"] == 100.0
    lines = client.get(f"/api/reports/sales-by-item/lines?item_id=none&{PERIOD}").json()
    assert [line["doc_type"] for line in lines["lines"]] == ["Invoice"]


def test_a_foreign_invoice_counts_at_its_booked_rate(client, stocked):
    """Each line at the invoice's rate, rounded per line as the journal
    converts it; the sales total is the P&L's income."""
    _invoice(
        client,
        stocked["customer"],
        "2026-07-09",
        [
            {"description": "A", "quantity": "1", "rate": "10.01"},
            {"description": "B", "quantity": "1", "rate": "10.01"},
            {"description": "C", "quantity": "1", "rate": "10.01"},
        ],
        currency="CAD",
        exchange_rate="0.7333",
    )
    d = client.get(f"/api/reports/sales-by-item?{PERIOD}").json()
    pl = client.get(f"/api/reports/profit-loss?{PERIOD}").json()
    assert d["total_amount"] == pl["total_income"] == 22.02  # 3 × 7.34


@pytest.mark.parametrize("path", ["sales-by-item", "purchases-by-item"])
def test_no_dates_is_the_fiscal_year_to_date(client, seed_accounts, monkeypatch, path):
    from datetime import date

    from app.routes.reports import items as items_route

    class _Frozen(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 23)

    monkeypatch.setattr(items_route, "date", _Frozen)
    client.put("/api/settings", json={"fiscal_year_start_month": "7"})
    d = client.get(f"/api/reports/{path}").json()
    assert (d["start_date"], d["end_date"]) == ("2026-07-01", "2026-10-23")
