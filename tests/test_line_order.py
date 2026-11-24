"""Line order (#604): a document's lines keep the order they were sent in
-- on create, on edit, in the print view -- and the line grids let the
user move a line up or down, drag it, and insert one below any line.

The saved order is the order of the lines in the request. Every document
type that carries lines is checked through the API, and the grids are
checked for wiring the shared module.
"""

import re
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"
NAMES = ["Charlie row", "Alpha row", "Delta row", "Bravo row"]


def _lines(names, **extra):
    # a stale line_order on every line, running backwards: the position in
    # the list wins
    return [
        {
            "description": n,
            "quantity": 1,
            "rate": 10 + i,
            "line_order": len(names) - i,
            **extra,
        }
        for i, n in enumerate(names)
    ]


def _order(lines):
    return [ln["description"] for ln in lines]


def _in_order(html, names):
    at = [html.index(n) for n in names]
    assert at == sorted(at), (names, at)


@pytest.fixture
def acct(seed_accounts):
    return seed_accounts["6000"].id


# --- sales side ---------------------------------------------------------


def test_an_invoice_keeps_its_line_order_and_a_reorder_persists(
    client, seed_accounts, seed_customer
):
    body = {
        "customer_id": seed_customer.id,
        "date": "2026-09-01",
        "lines": _lines(NAMES),
    }
    inv = client.post("/api/invoices", json=body).json()
    assert _order(inv["lines"]) == NAMES
    html = client.get(f"/api/invoices/{inv['id']}/print-preview").text
    _in_order(html, NAMES)

    flipped = list(reversed(NAMES))
    r = client.put(f"/api/invoices/{inv['id']}", json={"lines": _lines(flipped)})
    assert r.status_code == 200, r.text
    got = client.get(f"/api/invoices/{inv['id']}").json()
    assert _order(got["lines"]) == flipped
    _in_order(client.get(f"/api/invoices/{inv['id']}/print-preview").text, flipped)


def test_a_sales_receipt_keeps_its_line_order(client, seed_accounts, seed_customer):
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-01",
            "method": "Cash",
            "lines": _lines(NAMES),
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()["invoice"]
    assert _order(inv["lines"]) == NAMES
    _in_order(client.get(f"/api/invoices/{inv['id']}/print-preview").text, NAMES)


def test_an_estimate_keeps_its_line_order_and_a_reorder_persists(
    client, seed_accounts, seed_customer
):
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-01",
            "lines": _lines(NAMES),
        },
    ).json()
    assert _order(est["lines"]) == NAMES
    _in_order(client.get(f"/api/estimates/{est['id']}/print-preview").text, NAMES)

    # the same lines in another order is a change, not "nothing to save"
    moved = [NAMES[1], NAMES[0], NAMES[2], NAMES[3]]
    r = client.put(f"/api/estimates/{est['id']}", json={"lines": _lines(moved)})
    assert r.status_code == 200, r.text
    got = client.get(f"/api/estimates/{est['id']}").json()
    assert _order(got["lines"]) == moved
    _in_order(client.get(f"/api/estimates/{est['id']}/print-preview").text, moved)


def test_a_credit_memo_keeps_its_line_order(client, seed_accounts, seed_customer):
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-01",
            "tax_rate": 0,
            "lines": _lines(NAMES),
        },
    )
    assert r.status_code == 201, r.text
    cm = r.json()
    assert _order(cm["lines"]) == NAMES
    assert _order(client.get(f"/api/credit-memos/{cm['id']}").json()["lines"]) == NAMES
    _in_order(client.get(f"/api/credit-memos/{cm['id']}/print-preview").text, NAMES)


def test_a_recurring_schedule_keeps_its_line_order_and_a_reorder_persists(
    client, seed_accounts, seed_customer
):
    body = {
        "customer_id": seed_customer.id,
        "frequency": "monthly",
        "start_date": "2026-10-01",
        "lines": _lines(NAMES),
    }
    rec = client.post("/api/recurring", json=body).json()
    assert _order(rec["lines"]) == NAMES

    flipped = list(reversed(NAMES))
    r = client.put(f"/api/recurring/{rec['id']}", json={"lines": _lines(flipped)})
    assert r.status_code == 200, r.text
    assert _order(client.get(f"/api/recurring/{rec['id']}").json()["lines"]) == flipped


# --- purchase side ------------------------------------------------------


def test_a_bill_keeps_its_line_order_and_a_reorder_persists(client, acct):
    v = client.post("/api/vendors", json={"name": "Harbor Supply"}).json()
    body = {
        "vendor_id": v["id"],
        "date": "2026-09-01",
        "lines": _lines(NAMES, account_id=acct),
    }
    r = client.post("/api/bills", json=body)
    assert r.status_code == 201, r.text
    bill = r.json()
    assert _order(bill["lines"]) == NAMES
    _in_order(client.get(f"/api/bills/{bill['id']}/print-preview").text, NAMES)

    flipped = list(reversed(NAMES))
    r = client.put(
        f"/api/bills/{bill['id']}", json={"lines": _lines(flipped, account_id=acct)}
    )
    assert r.status_code == 200, r.text
    got = client.get(f"/api/bills/{bill['id']}").json()
    assert _order(got["lines"]) == flipped
    _in_order(client.get(f"/api/bills/{bill['id']}/print-preview").text, flipped)


def test_a_purchase_order_keeps_its_line_order_and_a_reorder_persists(client):
    v = client.post("/api/vendors", json={"name": "Ace Concrete"}).json()
    r = client.post(
        "/api/purchase-orders",
        json={"vendor_id": v["id"], "date": "2026-09-01", "lines": _lines(NAMES)},
    )
    assert r.status_code == 201, r.text
    po = r.json()
    assert _order(po["lines"]) == NAMES
    _in_order(client.get(f"/api/purchase-orders/{po['id']}/print-preview").text, NAMES)

    # an edit that moves lines it already has, by id
    ids = {ln["description"]: ln["id"] for ln in po["lines"]}
    moved = [NAMES[3], NAMES[2], NAMES[1], NAMES[0]]
    lines = [dict(ln, id=ids[ln["description"]]) for ln in _lines(moved)]
    r = client.put(f"/api/purchase-orders/{po['id']}", json={"lines": lines})
    assert r.status_code == 200, r.text
    assert _order(r.json()["lines"]) == moved
    got = client.get(f"/api/purchase-orders/{po['id']}").json()
    assert _order(got["lines"]) == moved
    _in_order(client.get(f"/api/purchase-orders/{po['id']}/print-preview").text, moved)


def test_a_vendor_credit_keeps_its_line_order(client, acct):
    v = client.post("/api/vendors", json={"name": "Acme Supply"}).json()
    r = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": v["id"],
            "date": "2026-09-01",
            "lines": _lines(NAMES, account_id=acct),
        },
    )
    assert r.status_code == 201, r.text
    vc = r.json()
    assert _order(vc["lines"]) == NAMES
    assert (
        _order(client.get(f"/api/vendor-credits/{vc['id']}").json()["lines"]) == NAMES
    )


# --- the grids ------------------------------------------------------------

GRIDS = {
    "invoices.js": "#inv-lines",
    "estimates.js": "#est-lines",
    "sales_receipts.js": "#sr-lines",
    "credit_memos.js": "#cm-lines",
    "bills.js": "#bill-lines",
    "vendor_credits.js": "#vc-lines",
    "purchase_orders.js": "#po-lines",
    "recurring.js": "#rec-lines",
}


@pytest.mark.parametrize("page,grid", GRIDS.items())
def test_each_line_grid_opts_into_the_shared_module(page, grid):
    src = (JS / page).read_text(encoding="utf-8")
    assert re.search(rf"LineOrder\.attach\('{grid}', \{{ add: .*changed: .*recalc", src)
    # the saved order is the row position
    assert "line_order: i" in src


def test_the_module_moves_drags_and_inserts():
    src = (JS / "line_order.js").read_text(encoding="utf-8")
    for part in (
        "moveRow(",
        "moveNextTo(",
        "insertBelow(",
        "Move line up",
        "Move line down",
        "Insert line below",
        'draggable="true"',
        "dragstart",
        "drop",
    ):
        assert part in src, part
    index = (JS.parents[2] / "index.html").read_text(encoding="utf-8")
    at = index.index('<script src="/static/js/line_order.js"></script>')
    assert at < index.index('<script src="/static/js/invoices.js"></script>')


# NEW-278: the purchase order's form could move and insert a line but not
# remove one. Each grid removes a line: with its own button, or with the
# shared module's (remove: true), never both.
OWN_REMOVE = {"invoices.js", "estimates.js", "sales_receipts.js", "credit_memos.js"}
OWN_REMOVE |= {"recurring.js"}


# #1189: the bill's grid joined them once the wide dialog's cap grew by a
# control's width to carry the fifth control at the construction bill.
CAN_REMOVE = dict(GRIDS)


@pytest.mark.parametrize("page,grid", CAN_REMOVE.items())
def test_each_line_grid_can_remove_a_line(page, grid):
    src = (JS / page).read_text(encoding="utf-8")
    shared = re.search(rf"LineOrder\.attach\('{grid}', \{{[^}}]*remove: true", src)
    own = 'aria-label="Remove line"' in src
    assert own != bool(shared), page
    assert own == (page in OWN_REMOVE), page


def test_the_module_removes_a_line():
    src = (JS / "line_order.js").read_text(encoding="utf-8")
    assert 'data-line-order="remove" aria-label="Remove line"' in src
    assert "removeRow(" in src


# #1189: bill quick entry is a page, not a modal, so it opts in on mount
# rather than beside its render; remove: true since its grid, like the bill
# form's, has no Remove line button of its own.
def test_bill_quick_entry_can_remove_a_line():
    src = (JS / "bill_quick_entry.js").read_text(encoding="utf-8")
    assert re.search(r"LineOrder\.attach\('#bqe-lines', \{[^}]*remove: true", src)
    assert 'aria-label="Remove line"' not in src  # the shared module's button
