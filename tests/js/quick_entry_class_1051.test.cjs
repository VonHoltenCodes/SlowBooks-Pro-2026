// Quick Entry and Bill Quick Entry carry a Class on each line (#1051),
// drawn where the company has a class of its own (Nonprofit.lineClassShown,
// the rule the invoice and bill forms use). There is no header class here,
// so the empty choice reads "No class". Each line sends its class, and a
// new line, and the next document's first line, start with the class of
// the line above: the last one chosen.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');
const read = (f) => fs.readFileSync(path.join(JS, f), 'utf8');

const CLASSES = [
    { id: 1, name: 'Uncategorized', is_system_default: true, is_archived: false },
    { id: 7, name: 'North Building', is_system_default: false, is_archived: false },
    { id: 8, name: 'South Building', is_system_default: false, is_archived: false },
];

function row(values) {
    const els = {};
    for (const [sel, v] of Object.entries(values)) {
        els[sel] = { value: v, checked: false, disabled: false, dataset: {}, textContent: '' };
    }
    return { querySelector: (sel) => els[sel] || null, els, dataset: {} };
}

function newState() {
    const state = { rows: [], cells: {}, added: [] };
    state.tbody = {
        querySelectorAll: () => state.rows,
        insertAdjacentHTML: (_where, html) => state.added.push(html),
        set innerHTML(html) { state.added.push(html); },
    };
    return state;
}

function context(classes, state) {
    const ctx = {
        console, setTimeout, clearTimeout, Promise, URLSearchParams,
        window: {},
        document: { addEventListener() {}, querySelectorAll: () => [], getElementById: () => null },
        location: { hash: '#/quick-entry' },
        history: { state: null, pushState() {}, replaceState() {} },
        escapeHtml: (s) => String(s ?? ''),
        formatCurrency: (v) => `$${Number(v || 0).toFixed(2)}`,
        todayISO: () => '2026-11-18',
        customerLabel: (c) => c.name,
        toast() {},
        focusFor() {},
        T: (s) => s,
        Terms: { text: (s) => s, isNonprofit: () => false, isConstruction: () => false },
        PaymentTerms: { load: async () => [], optionsHtml: () => '' },
        API: {
            get: async (url) => (url.startsWith('/classes') ? classes : url === '/settings' ? {} : []),
        },
    };
    ctx.PendingFiles = { reset() {}, stripHtml: () => '', ids: () => [], saved() {}, render() {}, discard() {} };
    ctx.$ = (sel) => (sel === '#qe-lines' || sel === '#bqe-lines' ? state.tbody : state.cells[sel] || null);
    ctx.$$ = (sel) => (sel === '#qe-lines tr' || sel === '#bqe-lines tr' ? state.rows : []);
    vm.createContext(ctx);
    // the tortoise helper the forms' teaching text sits behind (#977)
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/hint.js'), 'utf8'), ctx);
    // Nonprofit alone: the rest of utils.js brings its own $ and $$
    const utils = read('utils.js');
    const np = utils.slice(utils.indexOf('const Nonprofit = {'), utils.indexOf("// Normalize a form's class_id"));
    vm.runInContext(np + '\nthis.Nonprofit = Nonprofit;', ctx);
    return ctx;
}

function invoicePage(classes) {
    const state = newState();
    const ctx = context(classes, state);
    const utils = read('utils.js');
    const exempt = utils.slice(utils.indexOf('const TaxExempt = {'), utils.indexOf('window.TaxExempt = TaxExempt;'));
    vm.runInContext(exempt + '\nthis.TaxExempt = TaxExempt;', ctx);
    vm.runInContext(read('item_quick_add.js') + '\nthis.ItemQuickAdd = ItemQuickAdd;', ctx);
    vm.runInContext(read('invoices.js'), ctx);
    ctx.SalesLines = ctx.window.SalesLines;
    ctx.TaxDefault = ctx.window.TaxDefault;
    vm.runInContext(read('app.js') + '\nthis.App = App;', ctx);
    ctx.App.isLocked = () => false;
    return { App: ctx.App, state };
}

function billPage(classes) {
    const state = newState();
    const ctx = context(classes, state);
    ctx.PurchaseAccounts = { withBalanceSheet: (a) => a, options: () => '' };
    ctx.ItemQuickAdd = { options: () => '', itemId: (sel) => (sel && sel.value ? Number(sel.value) : null) };
    ctx.AccountQuickAdd = { picker: (html) => html, register() {}, PURCHASE_TYPES: [] };
    ctx.VendorQuickAdd = { html: () => '' };
    ctx.PurchaseLines = { cents: (x) => Math.round((Number(x) || 0) * 100) / 100 };
    vm.runInContext(read('bill_quick_entry.js') + '\nthis.BillQuickEntry = BillQuickEntry;', ctx);
    return { B: ctx.BillQuickEntry, state };
}

const invoiceForm = () => ({
    customer_id: { value: '3' },
    date: { value: '2026-11-18' },
    terms: { value: 'Net 30' },
    po_number: { value: '' },
    tax_rate: { value: '0' },
});

test('invoice Quick Entry draws a Class cell where the company has a class of its own', async () => {
    const { App } = invoicePage(CLASSES);
    const html = await App.renderQuickEntry();
    assert.match(html, /<th scope="col">Description<\/th><th scope="col">Class<\/th>/);
    assert.match(html, /<select class="line-class-fund" data-no-search><option value="">No class<\/option>/);
    assert.match(html, /<option value="7" >North Building<\/option>/);
});

test('with Uncategorized alone there is no Class cell', async () => {
    const { App } = invoicePage([CLASSES[0]]);
    const html = await App.renderQuickEntry();
    assert.doesNotMatch(html, /line-class-fund/);
    assert.doesNotMatch(html, /<th scope="col">Class<\/th>/);
});

test('each invoice line sends its class, and a new line takes the last one chosen', async () => {
    const { App, state } = invoicePage(CLASSES);
    await App.renderQuickEntry();
    state.rows = [
        row({ '.line-item': '', '.line-desc': 'Rent', '.line-qty': '1', '.line-rate': '1500', '.line-class-fund': '7' }),
        row({ '.line-item': '', '.line-desc': 'Repairs', '.line-qty': '1', '.line-rate': '80', '.line-class-fund': '8' }),
    ];
    const data = App.qeInvoiceData(invoiceForm());
    assert.deepEqual(Array.from(data.lines, (l) => l.class_id), [7, 8]);
    App.qeAddLine();
    assert.match(state.added.pop(), /<option value="8" selected>South Building<\/option>/);
});

test('with no Class cell an invoice line sends no class', async () => {
    const { App, state } = invoicePage([CLASSES[0]]);
    await App.renderQuickEntry();
    state.rows = [row({ '.line-item': '', '.line-desc': 'Rent', '.line-qty': '1', '.line-rate': '1500' })];
    assert.equal(App.qeInvoiceData(invoiceForm()).lines[0].class_id, null);
});

test('Save & Next starts the next invoice with the last class chosen', () => {
    const src = read('app.js');
    const save = src.slice(src.indexOf('async saveQuickEntry(e)'), src.indexOf('settings: {},   // one cached copy'));
    assert.ok(save.indexOf('App._qeClassId = App.qeLastClass();') < save.indexOf("$('#qe-lines').innerHTML = App._qeLineHtml(0);"));
    assert.ok(save.includes('App._qeClassId = App.qeLastClass();'));
});

test('Bill Quick Entry draws the Class cell, sends each line its class and keeps the last one', async () => {
    const { B, state } = billPage(CLASSES);
    const html = await B.render();
    assert.match(html, /<th scope="col">Description<\/th><th scope="col">Class<\/th>/);
    assert.match(html, /<option value="">No class<\/option>/);
    state.rows = [
        row({ '.line-item': '', '.line-account': '60', '.line-desc': 'Plumbing', '.line-amt': '200', '.line-class-fund': '8' }),
    ];
    assert.equal(B._lines().lines[0].class_id, 8);
    B.addLine();
    assert.match(state.added.pop(), /<option value="8" selected>South Building<\/option>/);
    // the next bill's first line starts from it too
    state.cells['#bqe-total'] = {};
    B._next({ bill_number: { value: 'A-1' } });
    assert.match(state.added.pop(), /<option value="8" selected>South Building<\/option>/);
});

test('Bill Quick Entry with Uncategorized alone has no Class cell and sends none', async () => {
    const { B, state } = billPage([CLASSES[0]]);
    const html = await B.render();
    assert.doesNotMatch(html, /line-class-fund/);
    state.rows = [row({ '.line-item': '', '.line-account': '60', '.line-desc': 'Plumbing', '.line-amt': '200' })];
    assert.equal(B._lines().lines[0].class_id, null);
});

test('the shared cell keeps "Same as header" where a form has a header class', () => {
    const utils = read('utils.js');
    assert.ok(utils.includes('classCellHtml(cls, fundSelected) {'));
    assert.ok(utils.includes('<option value="">Same as header</option>${funds}'));
});
