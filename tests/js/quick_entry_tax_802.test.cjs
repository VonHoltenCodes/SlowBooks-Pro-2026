// Invoice Quick Entry charges sales tax as the New Invoice form does (#802):
// it starts from the company's rate, each line is taxed by its Tax box (the
// item's flag when one is picked), a non-taxable customer's lines carry
// none, and the panel shows each line's tax before Save & Next posts it.
// Before, every quick-entered invoice went out with tax_rate 0.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');
const read = (f) => fs.readFileSync(path.join(JS, f), 'utf8');

function row(qty, rate, taxable, desc = 'Catering') {
    const els = {
        '.line-item': { value: '', closest: () => null, parentElement: null },
        '.line-desc': { value: desc },
        '.line-qty': { value: String(qty) },
        '.line-rate': { value: String(rate), dataset: {}, getAttribute: () => null },
        '.line-taxable': { checked: taxable, disabled: false, dataset: {}, title: '' },
        '.line-amount': { textContent: '' },
        '.line-tax': { textContent: '' },
    };
    return { querySelector: (sel) => els[sel] || null, els };
}

function page({ customers = [], settings = {}, rows = [] } = {}) {
    const state = {
        rows,
        drawn: true,
        rate: { value: '' },
        customer: { value: '' },
        cells: { '#qe-subtotal': {}, '#qe-tax': {}, '#qe-total': {} },
    };
    const tbody = {
        querySelectorAll: (sel) => (sel === '.line-taxable'
            ? state.rows.map((r) => r.els['.line-taxable']) : state.rows),
    };
    const $ = (sel) => {
        if (sel === '#qe-lines') return tbody;
        // not on the page until the page is drawn
        if (sel === '#qe-tax-rate') return state.drawn ? state.rate : null;
        if (sel === '#qe-customer') return state.customer;
        const line = /^\[data-qeline="(\d+)"\]$/.exec(sel);
        if (line) return state.rows[Number(line[1])] || null;
        return state.cells[sel] || null;
    };
    const ctx = {
        console, setTimeout, clearTimeout, Promise, URLSearchParams,
        window: {},
        document: { addEventListener() {}, querySelectorAll: () => [], getElementById: () => null },
        location: { hash: '#/quick-entry' },
        history: { state: null, pushState() {}, replaceState() {} },
        $, $$: (sel) => (sel === '#qe-lines tr' ? state.rows : []),
        escapeHtml: (s) => String(s ?? ''),
        formatCurrency: (v) => `$${Number(v || 0).toFixed(2)}`,
        todayISO: () => '2026-11-11',
        customerLabel: (c) => c.name,
        toast() {},
        T: (s) => s,
        Terms: { text: (s) => s, isNonprofit: () => false, isConstruction: () => false },
        PaymentTerms: { load: async () => [], optionsHtml: () => '' },
        API: {
            get: async (url) => (url === '/settings' ? settings
                : url.startsWith('/customers') ? customers : []),
        },
    };
    vm.createContext(ctx);
    // TaxExempt alone: the rest of utils.js brings its own $ and $$
    const utils = read('utils.js');
    const exempt = utils.slice(utils.indexOf('const TaxExempt = {'), utils.indexOf('window.TaxExempt = TaxExempt;'));
    vm.runInContext(exempt + '\nthis.TaxExempt = TaxExempt;', ctx);
    vm.runInContext(read('item_quick_add.js') + '\nthis.ItemQuickAdd = ItemQuickAdd;', ctx);
    vm.runInContext(read('invoices.js'), ctx);
    ctx.SalesLines = ctx.window.SalesLines;
    ctx.TaxDefault = ctx.window.TaxDefault;
    vm.runInContext(read('app.js') + '\nthis.App = App;', ctx);
    ctx.App.isLocked = () => false;
    const form = {
        customer_id: state.customer,
        date: { value: '2026-11-11' },
        terms: { value: 'Net 30' },
        po_number: { value: '' },
        tax_rate: state.rate,
    };
    return { App: ctx.App, state, form };
}

const taxable = { id: 1, name: 'Corner Cafe', is_taxable: true };
const reseller = { id: 2, name: 'Resale Shop', is_taxable: false };

test('the panel starts from the company rate and shows each line its tax', async () => {
    const { App, state } = page({ settings: { default_tax_rate: '8.25' } });
    state.drawn = false;
    const html = await App.renderQuickEntry();
    state.drawn = true;
    assert.match(html, /<input name="tax_rate" id="qe-tax-rate"[^>]* value="8\.25"/);
    assert.match(html, /<th scope="col" class="col-amount">Sales Tax<\/th>/);
    assert.match(html, /class="line-taxable"[^>]* checked/);
    assert.match(html, /<td class="col-amount line-tax">\$0\.00 at 8\.25%<\/td>/);

    state.rate.value = '8.25';
    state.customer.value = '1';
    App._qeCustomers = [taxable];
    state.rows = [row(4, 25, true), row(1, 10, false)];
    const t = App.qeRecalc();
    assert.deepEqual([t.subtotal, t.tax, t.total], [110, 8.25, 118.25]);
    assert.equal(state.rows[0].els['.line-tax'].textContent, '$8.25 at 8.25%');
    assert.equal(state.rows[1].els['.line-tax'].textContent, 'None');
    assert.equal(state.cells['#qe-subtotal'].textContent, '$110.00');
    assert.equal(state.cells['#qe-tax'].textContent, '$8.25');
    assert.equal(state.cells['#qe-total'].textContent, 'Total: $118.25');
});

test('a company with no rate starts at 0 percent and charges nothing', async () => {
    const { App, state } = page({ settings: {} });
    state.drawn = false;
    const html = await App.renderQuickEntry();
    state.drawn = true;
    assert.match(html, /id="qe-tax-rate"[^>]* value="0"/);
    state.rate.value = '0';
    state.rows = [row(4, 25, true)];
    assert.equal(App.qeRecalc().tax, 0);
    assert.equal(state.rows[0].els['.line-tax'].textContent, '$0.00 at 0.00%');
});

test('Save & Next sends the rate as a fraction and each line its Tax box', () => {
    const { App, state, form } = page({ rows: [row(4, 25, true), row(1, 10, false)] });
    state.rate.value = '8.25';
    state.customer.value = '1';
    const data = App.qeInvoiceData(form);
    assert.equal(data.tax_rate, 0.0825);
    assert.deepEqual(Array.from(data.lines, (l) => l.is_taxable), [true, false]);
    assert.equal(data.customer_id, 1);
});

test('a non-taxable customer has every line held off and shown as no tax', () => {
    const { App, state, form } = page({ rows: [row(4, 25, true)] });
    App._qeCustomers = [taxable, reseller];
    state.rate.value = '8.25';
    state.customer.value = '2';
    const t = App.qeRecalc();
    assert.equal(t.tax, 0);
    const box = state.rows[0].els['.line-taxable'];
    assert.equal(box.checked, false);
    assert.equal(box.disabled, true);
    assert.equal(state.rows[0].els['.line-tax'].textContent, 'None');
    assert.equal(App.qeInvoiceData(form).lines[0].is_taxable, false);
    // back to a taxable customer: the line is taxed again
    state.customer.value = '1';
    assert.equal(App.qeRecalc().tax, 8.25);
    assert.equal(box.checked, true);
});

test('picking an item takes its price and tax flag, as on the invoice form', () => {
    const { App, state } = page({ rows: [row(1, 0, true, '')] });
    App._qeItems = [
        { id: 9, name: 'Greeting cards', rate: 12, is_taxable: false },
        { id: 10, name: 'Sheet cake', rate: 40, is_taxable: true },
    ];
    state.rate.value = '8.25';
    const line = state.rows[0];
    line.els['.line-item'].value = '9';
    App.qeItemSelected(0);
    assert.equal(line.els['.line-rate'].value, 12);
    assert.equal(line.els['.line-taxable'].checked, false);
    assert.equal(line.els['.line-tax'].textContent, 'None');
    line.els['.line-item'].value = '10';
    App.qeItemSelected(0);
    assert.equal(line.els['.line-taxable'].checked, true);
    assert.equal(line.els['.line-tax'].textContent, '$3.30 at 8.25%');
});
