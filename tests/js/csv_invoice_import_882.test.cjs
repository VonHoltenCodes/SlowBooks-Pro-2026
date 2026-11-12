// The CSV import page's Invoices entry (#882): the column notes and the
// template link, a dry run first with its report, and a Post button that
// sends the same file again for real only when nothing was refused.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const COLUMNS = ['invoice_number', 'date', 'due_date', 'customer', 'item', 'description',
    'quantity', 'rate', 'taxable', 'memo', 'class', 'job'];

const clean = {
    dry_run: true, posted: false, invoices: 2, lines: 3, customers_matched: 2,
    customers_unknown: [], items_matched: 1, subtotal: '315.00', tax: '0.00', total: '315.00',
    documents: [
        { row: 2, invoice_number: 'R-501', customer: 'Harbor Bakery', date: '2026-10-05',
          due_date: '2026-10-20', lines: 2, subtotal: '195.00', tax: '0.00', total: '195.00' },
        { row: 4, invoice_number: 'R-502', customer: 'Pine <Street>', date: '2026-10-07',
          due_date: null, lines: 1, subtotal: '120.00', tax: '0.00', total: '120.00' },
    ],
    errors: [],
};

function page(answer) {
    const els = {};
    const calls = [];
    const el = () => ({ innerHTML: '', hidden: true, textContent: '' });
    const context = {
        console, Promise, setTimeout, clearTimeout,
        FormData: class { constructor() { this.parts = []; } append(k, v) { this.parts.push([k, v]); } },
        location: { hash: '#/csv' },
        history: { pushState() {}, replaceState() {} },
        document: { addEventListener() {}, getElementById: () => null, querySelectorAll: () => [] },
        $: sel => (els[sel] = els[sel] || el()),
        $$: () => [],
        fetch: async (url, opts) => { calls.push({ url, opts }); return { ok: true, json: async () => answer(calls.length) }; },
        toast: () => {}, openModal: () => {}, closeModal: () => {},
        T: s => s,
        Terms: { init() {}, text: s => s, isNonprofit: () => false },
        formatDate: d => d,
        formatCurrency: v => `$${Number(v || 0).toFixed(2)}`,
        API: { responseError: async () => 'failed' },
    };
    context.window = {};
    vm.createContext(context);
    const utils = fs.readFileSync(path.join(__dirname, '../../app/static/js/utils.js'), 'utf8');
    const escapeHtml = utils.match(/^function escapeHtml\(str\) \{[\s\S]*?^\}/m);
    assert.ok(escapeHtml, 'escapeHtml found in utils.js');
    vm.runInContext(escapeHtml[0], context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/app.js'), 'utf8') + '\nthis.App = App;', context);
    return { App: context.App, els, calls };
}

// The text of the HTML: everything outside the tags (a character walk).
const text = html => {
    let out = '', depth = 0;
    for (const ch of html) {
        if (ch === '<') depth++;
        else if (ch === '>') { if (depth) depth--; }
        else if (!depth) out += ch;
    }
    return out.replace(/\s+/g, ' ').trim();
};

const form = file => ({ entity_type: { value: 'invoices' }, file: { files: [file] } });

test('the Invoices help notes every column and links the template route', () => {
    const { App } = page(() => clean);
    const html = App._csvImportHelpHtml('invoices');
    const items = [...html.matchAll(/<li><code>([^<]+)<\/code>/g)].map(m => m[1]);
    assert.deepEqual(items, COLUMNS);
    assert.match(html, /href="\/api\/csv\/import\/invoices\/template" download/);
    // the other lists keep their one-line help
    assert.doesNotMatch(App._csvImportHelpHtml('customers'), /<li>/);
});

test('the page offers Invoices in the entity picker', async () => {
    const { App } = page(() => clean);
    const html = await App.renderCSV();
    assert.match(html, /<option value="invoices">Invoices<\/option>/);
});

test('an import is a dry run first, and Post sends the same file for real', async () => {
    const file = { name: 'invoices.csv' };
    const f = page(n => (n === 1 ? clean : { ...clean, dry_run: false, posted: true }));
    await f.App.importCSV({ preventDefault() {}, target: form(file) });
    assert.equal(f.calls[0].url, '/api/csv/import/invoices?dry_run=1');
    assert.equal(f.calls[0].opts.method, 'POST');
    const html = f.els['#csv-import-results'].innerHTML;
    const shown = text(html);
    assert.match(shown, /Dry run: nothing was posted/);
    assert.match(shown, /2 invoices, 3 lines; 2 customers matched; 1 item matched/);
    assert.match(shown, /total \$315\.00/);
    assert.match(html, /Pine &lt;Street&gt;/);
    assert.match(html, /onclick="App\.postInvoiceImport\(\)">Post 2 invoices</);

    await f.App.postInvoiceImport();
    assert.equal(f.calls[1].url, '/api/csv/import/invoices?dry_run=0');
    assert.equal(f.calls[1].opts.body.parts[0][1], file);
    const after = f.els['#csv-import-results'].innerHTML;
    assert.match(text(after), /Posted 2 invoices, \$315\.00/);
    assert.doesNotMatch(after, /postInvoiceImport/);
});

test('a refused row is listed with its row, and there is no Post button', async () => {
    const refused = {
        ...clean, documents: [clean.documents[0]], customers_unknown: ['Nobody <Known>'],
        errors: ['Row 4: customer "Nobody <Known>" is not on the Customers list.'],
    };
    const f = page(() => refused);
    await f.App.importCSV({ preventDefault() {}, target: form({}) });
    const html = f.els['#csv-import-results'].innerHTML;
    assert.match(text(html), /Refused \(1\):\s*Row 4: customer &quot;Nobody/);
    assert.match(html, /1 not found \(Nobody &lt;Known&gt;\)/);
    assert.doesNotMatch(html, /<Known>/);
    assert.doesNotMatch(html, /postInvoiceImport/);
});
