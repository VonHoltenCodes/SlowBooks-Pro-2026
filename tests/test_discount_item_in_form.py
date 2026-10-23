"""A Discount item's line takes a negative price in the form (2.18.0).

A QuickBooks Online discount comes in on a Discount item, and the server
takes a negative price on its lines, but the invoice form held every new
line's price at 0 or more: picking the Discount item on a new line left
no way to enter the discount. Items now say which are Discount items, and
the form lets their price go negative (and gives the floor back when the
line's item changes to another)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.models.items import Item, ItemType

ROOT = Path(__file__).resolve().parents[1]


def _discount_item(db):
    from app.services.qbo_common import create_mapping

    item = Item(name="Discount", item_type=ItemType.SERVICE, is_active=True)
    other = Item(name="Sourdough", item_type=ItemType.SERVICE, is_active=True)
    db.add_all([item, other])
    db.flush()
    create_mapping(db, "discount_item", item.id, "99")
    db.commit()
    return item, other


def test_items_say_which_are_discounts(client, db_session):
    item, other = _discount_item(db_session)
    rows = {i["id"]: i for i in client.get("/api/items").json()}
    assert rows[item.id]["is_discount"] is True
    assert rows[other.id]["is_discount"] is False
    assert client.get(f"/api/items/{item.id}").json()["is_discount"] is True


def test_a_discount_line_saves_negative_from_the_api(
    client, db_session, seed_accounts, seed_customer
):
    item, _ = _discount_item(db_session)
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-09-01",
            "tax_rate": 0,
            "lines": [
                {"description": "Loaf", "quantity": 1, "rate": 100},
                {
                    "item_id": item.id,
                    "description": "Discount",
                    "quantity": 1,
                    "rate": -10,
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["total"] in (90, "90.00", 90.0)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_form_lets_a_discount_line_go_negative():
    probe = r"""
const fs = require('fs'), vm = require('vm');
function input(attrs) {
  const a = Object.assign({}, attrs);
  return { value: '', placeholder: '', dataset: {},
    getAttribute: (k) => (k in a ? a[k] : null), setAttribute: (k, v) => { a[k] = String(v); },
    removeAttribute: (k) => { delete a[k]; }, attrs: a };
}
const rate = input({ min: '0' }), desc = input({}), tax = { checked: true, disabled: false, dataset: {} };
let chosen = '5';
const row = { querySelector: (sel) => ({ '.line-item': { value: chosen }, '.line-desc': desc,
  '.line-rate': rate, '.line-taxable': tax })[sel] || null };
const items = [{ id: 5, name: 'Discount', rate: '0.00', is_discount: true },
               { id: 6, name: 'Sourdough', rate: '8.50', is_discount: false }];
const ctx = { console, window: {}, document: { addEventListener: () => {} }, App: { settings: {} },
  escapeHtml: (s) => String(s), formatCurrency: (n) => '$' + (Number(n) || 0).toFixed(2) };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/item_quick_add.js', 'utf8'), ctx);  // the picker (#445)
vm.runInContext(fs.readFileSync('app/static/js/invoices.js', 'utf8')
  + '\nthis.SalesLines = SalesLines; this.InvoicesPage = InvoicesPage;', ctx);
ctx.SalesLines.fillFromItem(row, items);
const onDiscount = { min: rate.getAttribute('min'), placeholder: rate.placeholder };
chosen = '6';
ctx.SalesLines.fillFromItem(row, items);
const back = { min: rate.getAttribute('min'), value: rate.value };
const html = ctx.InvoicesPage.lineRowHtml(0, { item_id: 5, rate: '0', quantity: 1 }, items);
const plain = ctx.InvoicesPage.lineRowHtml(1, { item_id: 6, rate: '8.5', quantity: 1 }, items);
console.log(JSON.stringify({ onDiscount, back, discountRowHasMin: html.includes('min="0"'),
  plainRowHasMin: plain.includes('min="0"') }));
"""
    out = subprocess.run(
        ["node", "-e", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["onDiscount"] == {"min": None, "placeholder": "-10.00"}
    assert got["back"] == {"min": "0", "value": "8.50"}
    assert got["discountRowHasMin"] is False and got["plainRowHasMin"] is True
