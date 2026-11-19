// #977: the "Turn into a bill" dialog's three teaching lines sit behind the
// tortoise: the vendor's terms on Bill date, the tax on Account, the
// retainage on Retainage. Each shows only when it did before. Drives the real
// PurchaseOrdersPage.convertToBill and the real hint().
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');

async function dialog({ construction, tax, retainage }) {
    let shown = '';
    const po = {
        id: 3, po_number: 'PO-3', vendor_id: 9, tax_amount: tax, retainage_pct: retainage,
        lines: [{ id: 1, description: 'Rebar', amount: 500, billed_amount: 0, quantity: 1, rate: 500 }],
    };
    const context = {
        console, window: {}, document: { addEventListener() {} },
        API: { get: async (url) => (url.startsWith('/purchase-orders/') ? po : url.startsWith('/vendors/') ? { name: 'Steel & Co', terms: 'Net 15' } : []) },
        Terms: { isConstruction: () => construction },
        CostCodes: { load: async () => {} },
        CostTypes: { load: async () => {}, accountFor: () => '' },
        PurchaseAccounts: { filter: (a) => a, options: () => '' },
        AccountQuickAdd: { picker: (html) => html, register() {} },
        escapeHtml: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;'),
        formatCurrency: (v) => '$' + v,
        todayISO: () => '2026-11-19',
        toast() {},
        openModal: (_title, html) => { shown = html; },
        $$: () => [],
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'hint.js'), 'utf8'), context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'purchase_orders.js'), 'utf8') + '\nthis.PurchaseOrdersPage = PurchaseOrdersPage;', context);
    await context.PurchaseOrdersPage.convertToBill(3);
    return shown;
}

const note = (html, about) => {
    const m = html.match(new RegExp(`<strong class="hint-pop-title">${about}</strong>(.*?)</span>`));
    return m && m[1];
};

test('the terms line is the Bill date tortoise, not a paragraph', async () => {
    const html = await dialog({ construction: false, tax: 0, retainage: 0 });
    assert.match(html, /<label for="po-conv-date">Bill date <span class="hint-wrap">/);
    assert.equal(note(html, 'Bill date'), "Choose where each line is recorded. The bill takes Steel &amp; Co's terms (Net 15) from the date below.");
    assert.doesNotMatch(html, /<p[^>]*>Choose where/);
    assert.equal(note(html, 'Tax on this order'), null);
    assert.equal(note(html, 'Retainage'), null);
});

test('tax and retainage get their tortoises on their column heads', async () => {
    const html = await dialog({ construction: true, tax: 40, retainage: 10 });
    assert.match(html, /<th scope="col">Account <span class="hint-wrap">/);
    assert.equal(note(html, 'Tax on this order'), "The $40 of tax on this order is part of what these goods cost: it is added to each line's account, not to Sales Tax Payable.");
    assert.match(html, /<th scope="col" class="amount">Retainage <span class="hint-wrap">/);
    assert.equal(note(html, 'Retainage'), '10% retainage is held back on this bill and posted to Retainage Payable.');
    assert.doesNotMatch(html, /class="form-hint">The|class="form-hint">10%/);
});
