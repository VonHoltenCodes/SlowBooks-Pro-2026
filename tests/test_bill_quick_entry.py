"""Bill Quick Entry (#487): the page's wiring, source-checked. The browser
walks it in test_browser_bill_quick_entry.py; the bill it posts is an
ordinary POST /api/bills with amount-only lines (#391), tested here once.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app/static/js"
BQE = (JS / "bill_quick_entry.js").read_text(encoding="utf-8")
APP = (JS / "app.js").read_text(encoding="utf-8")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")


def test_the_page_has_a_route_a_sidebar_link_and_a_top_bar_button():
    assert (
        "'/bill-entry':    { page: 'bill-entry',      label: 'Bill Quick Entry',"
        "   render: () => BillQuickEntry.render()," in APP
    )
    assert (
        '<button class="tb-btn" data-nav="#/bill-entry">Bill Quick Entry</button>'
        in INDEX
    )
    # a page that only enters things: the sidebar's link is a write control
    assert '<li data-write><a href="#/bill-entry"' in INDEX
    assert '#topbar .tb-btn[data-nav="#/bill-entry"]' in APP
    at = INDEX.index('<script src="/static/js/bill_quick_entry.js"></script>')
    for before in (
        "vendors.js",
        "item_quick_add.js",
        "account_quick_add.js",
        "bills.js",
    ):
        assert INDEX.index(f'<script src="/static/js/{before}"></script>') < at


def test_ctrl_enter_saves_the_bill_and_the_lines_are_amounts():
    assert "const qeForm = $('#qe-form') || $('#bqe-form');" in APP
    assert '<form id="bqe-form"' in BQE
    assert "await API.post('/bills', {" in BQE
    lines = BQE[BQE.index("_lines() {") :]
    lines = lines[: lines.index("async save(e) {")]
    assert "amount, line_order: lines.length" in lines
    assert "quantity" not in lines and "rate" not in lines


def test_it_uses_the_shared_quick_adds_on_the_purchase_side():
    assert "VendorQuickAdd.html(vendors, { id: 'bqe-vendor'" in BQE
    assert "ItemQuickAdd.selected(select, BillQuickEntry._items" in BQE
    assert "'purchase')) return;" in BQE
    assert "AccountQuickAdd.register('bill-entry', {" in BQE
    assert "types: AccountQuickAdd.PURCHASE_TYPES," in BQE


def test_an_amount_alone_bill_posts(client, seed_accounts):
    vendor = client.post("/api/vendors", json={"name": "Harbor Ice"}).json()
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "HI-7",
            "date": "2026-10-01",
            "terms": "Net 15",
            "lines": [
                {
                    "account_id": seed_accounts["6900"].id,
                    "description": "Ice",
                    "amount": 42.5,
                    "line_order": 0,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    assert bill["bill_number"] == "HI-7" and float(bill["total"]) == 42.5
    assert bill["vendor_name"] == "Harbor Ice" and bill["due_date"] == "2026-10-16"
