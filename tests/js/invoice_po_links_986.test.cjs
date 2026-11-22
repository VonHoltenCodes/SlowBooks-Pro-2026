// An invoice and the purchase orders that fill it (#986): the invoice's
// Linked purchase orders panel (each PO opens, × unlinks, Create PO and
// Link a PO… on a live invoice, every control that changes a link marked
// data-write so a read-only or accountant sign-in sees the list only), the
// orders Link a PO… offers, the PO's "Fills Invoice #…" line, and the PO
// form's Fills invoice picker. invoices.js and purchase_orders.js run as the
// page has them, with the page helpers stubbed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');

function pages() {
    const context = {
        window: {}, console,
        App: { settings: {} },
        T: (w) => w, Terms: { text: (w) => w, isConstruction: () => false, isNonprofit: () => false },
        escapeHtml: (v) => String(v ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`),
        formatCurrency: (v) => `$${Number(v || 0).toFixed(2)}`,
        formatDate: (v) => v, statusBadge: (v) => v,
        $: () => null, $$: () => [],
    };
    vm.createContext(context);
    for (const f of ['invoices.js', 'purchase_orders.js']) {
        vm.runInContext(fs.readFileSync(path.join(JS, f), 'utf8'), context);
    }
    vm.runInContext('this.PurchaseOrdersPage = PurchaseOrdersPage;', context);
    return { Inv: context.window.InvoicesPage, PO: context.PurchaseOrdersPage };
}

const linked = [
    { id: 7, po_number: 'PO-0007', vendor_name: 'Mouse Supply', status: 'draft', total: '20.00' },
    { id: 9, po_number: 'PO-0009', vendor_name: 'Screen <Depot>', status: 'sent', total: '140.00' },
];

test('the panel lists each linked PO, opening it, with × to unlink', () => {
    const { Inv } = pages();
    const html = Inv.linkedPOsPanelHtml({ id: 42, status: 'sent', linked_purchase_orders: linked });
    assert.match(html, /Linked purchase orders/);
    assert.match(html, /onclick="PurchaseOrdersPage\.view\(7\); return false;">PO-0007</);
    assert.match(html, /Screen &#60;Depot&#62;/);
    assert.match(html, /\$140\.00/);
    assert.match(html, /data-write[^>]*onclick="InvoicesPage\.unlinkPO\(42, 9\)"/);
    assert.match(html, /data-write[^>]*onclick="InvoicesPage\.createLinkedPO\(42\)">Create PO</);
    assert.match(html, /data-write[^>]*onclick="InvoicesPage\.linkPOPicker\(42\)">Link a PO…</);
    // every button in it changes a link, so every one is marked data-write
    const buttons = html.match(/<button[^>]*>/g);
    assert.equal(buttons.length, 4);
    for (const b of buttons) assert.match(b, /data-write/);
});

test('none linked says so; a void invoice keeps its links and offers no new one', () => {
    const { Inv } = pages();
    const empty = Inv.linkedPOsPanelHtml({ id: 3, status: 'draft', linked_purchase_orders: [] });
    assert.match(empty, /No purchase orders fill this invoice\./);
    assert.match(empty, /Create PO/);
    const voided = Inv.linkedPOsPanelHtml({ id: 3, status: 'void', linked_purchase_orders: linked });
    assert.match(voided, /PO-0007/);
    assert.doesNotMatch(voided, /Create PO|Link a PO/);
    assert.equal(Inv.linkedPOsPanelHtml({ id: 3, is_sales_receipt: true, linked_purchase_orders: linked }), '');
});

test('Link a PO… offers open orders that fill no invoice, by number', () => {
    const { Inv } = pages();
    const offered = Inv.linkablePOs([
        { id: 1, po_number: 'PO-0010', status: 'draft', invoice_id: null },
        { id: 2, po_number: 'PO-0002', status: 'sent', invoice_id: null },
        { id: 3, po_number: 'PO-0003', status: 'closed', invoice_id: null },
        { id: 4, po_number: 'PO-0004', status: 'draft', invoice_id: 8 },
    ]);
    assert.deepEqual(offered.map((p) => p.id), [2, 1]);
});

test('the PO view says which invoice it fills and opens it', () => {
    const { PO } = pages();
    assert.equal(PO.fillsInvoiceHtml({ id: 5, invoice_id: null }), '');
    const html = PO.fillsInvoiceHtml({ id: 5, invoice_id: 42, invoice_number: '1042' });
    assert.match(html, /Fills Invoice<\/strong> <a href="#" onclick="InvoicesPage\.view\(42\); return false;">#1042<\/a>/);
    assert.match(html, /data-write[^>]*onclick="PurchaseOrdersPage\.unlinkInvoice\(5, 42\)"/);
});

test('the Fills invoice picker: blank allowed, no void ones, the current one kept', () => {
    const { PO } = pages();
    const invoices = [
        { id: 1, invoice_number: '1001', customer_name: 'Barrs', status: 'sent' },
        { id: 2, invoice_number: '1002', status: 'void' },
        { id: 3, invoice_number: '1003', status: 'paid', is_sales_receipt: true },
        { id: 4, invoice_number: '1004', status: 'draft' },
    ];
    assert.deepEqual(PO.fillableInvoices(invoices).map((i) => i.id), [1, 4]);
    assert.deepEqual(PO.fillableInvoices(invoices, 2).map((i) => i.id), [1, 2, 4]);
    const blank = PO.fillsInvoiceOptions(invoices, { invoice_id: null });
    assert.match(blank, /^<option value="">None<\/option>/);
    assert.match(blank, /<option value="1" >#1001 · Barrs<\/option>/);
    assert.doesNotMatch(blank, /selected/);
    const older = PO.fillsInvoiceOptions(invoices, { invoice_id: 77, invoice_number: '0977' });
    assert.match(older, /<option value="77" selected>#0977<\/option>/);
});
