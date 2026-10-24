// The chart import's dry-run table (#461): each row shows the detail type it
// will be written with and the parent it will sit under, each in its own
// column with a header a screen reader announces ("Detail type", "Parent"),
// and the change notes sit under "Notes", not under a "Detail" heading.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const plan = {
    format: 'csv', dry_run: true, replace: false,
    created: 3, updated: 0, skipped: 0, deactivated: 0, kept: 0, row_errors: 0, errors: [],
    rows: [
        { row: 2, action: 'create', number: '4960', name: 'Rebates Earned', type: 'income',
          detail_type: 'other_income', detail_type_label: 'Other Income',
          parent_number: null, parent_name: null, note: '' },
        { row: 3, action: 'create', number: '7110', name: 'Fuel', type: 'expense',
          detail_type: 'other_expense', detail_type_label: 'Other Expense',
          parent_number: '7100', parent_name: 'Vehicle Costs', note: '' },
        { row: 4, action: 'create', number: '2650', name: 'Truck <Loan>', type: 'liability',
          detail_type: 'long_term_liability', detail_type_label: 'Long Term Liability',
          parent_number: null, parent_name: null, note: 'number 2600 is taken; assigned 2650' },
    ],
};

function page() {
    const els = {};
    const el = () => ({ innerHTML: '', hidden: true, textContent: '' });
    const context = {
        console, Promise, setTimeout, clearTimeout,
        FormData: class { append() {} },
        location: { hash: '#/accounts' },
        history: { pushState() {}, replaceState() {} },
        document: { addEventListener() {}, getElementById: () => null, querySelectorAll: () => [] },
        $: sel => (els[sel] = els[sel] || el()),
        $$: () => [],
        fetch: async () => ({ ok: true, json: async () => plan }),
        toast: () => {}, openModal: () => {}, closeModal: () => {},
        T: s => s,
        Terms: { init() {}, text: s => s, isNonprofit: () => false },
        formatDate: d => d,
        API: { responseError: async () => 'failed' },
    };
    context.window = {};
    vm.createContext(context);
    // the page's own escapeHtml, so the test escapes as the app does
    const utils = fs.readFileSync(path.join(__dirname, '../../app/static/js/utils.js'), 'utf8');
    const escapeHtml = utils.match(/^function escapeHtml\(str\) \{[\s\S]*?^\}/m);
    assert.ok(escapeHtml, 'escapeHtml found in utils.js');
    vm.runInContext(escapeHtml[0], context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/app.js'), 'utf8') + '\nthis.App = App;', context);
    return { App: context.App, els };
}

// The text of a cell: everything outside the tags. A character walk, not a
// regex sanitizer (escaped text has no raw angle brackets to lose).
const text = html => {
    let out = '', depth = 0;
    for (const ch of html) {
        if (ch === '<') depth++;
        else if (ch === '>') { if (depth) depth--; }
        else if (!depth) out += ch;
    }
    return out.replace(/\s+/g, ' ').trim();
};

async function render() {
    const f = page();
    const form = { file: { files: [{ name: 'chart.csv' }] }, replace: { checked: false } };
    await f.App.previewChartImport({ preventDefault() {}, target: form });
    const html = f.els['#chart-import-preview'].innerHTML;
    const headers = [...html.matchAll(/<th(\s[^>]*)?>([\s\S]*?)<\/th>/g)].map(m => ({ attrs: m[1], label: text(m[2]) }));
    const rows = [...html.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].slice(1)
        .map(m => [...m[1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map(c => text(c[1])));
    return { html, headers, rows };
}

test('the preview has Detail type, Parent and Notes columns, each a column header', async () => {
    const { headers } = await render();
    assert.deepEqual(headers.map(h => h.label), ['Action', 'Number', 'Name', 'Type', 'Detail type', 'Parent', 'Notes']);
    for (const h of headers) assert.match(h.attrs, /scope="col"/, h.label);
    assert.ok(!headers.some(h => h.label === 'Detail'), 'the notes column is no longer titled Detail');
});

test('each row shows the detail type it gets and its parent by number and name', async () => {
    const { rows } = await render();
    assert.equal(rows.length, 3);
    for (const cells of rows) assert.equal(cells.length, 7);
    assert.deepEqual(rows[0].slice(3, 7), ['income', 'Other Income', '', '']);
    assert.deepEqual(rows[1].slice(3, 7), ['expense', 'Other Expense', '7100 Vehicle Costs', '']);
    assert.deepEqual(rows[2].slice(4, 7), ['Long Term Liability', '', 'number 2600 is taken; assigned 2650']);
});

test('names in the preview are escaped', async () => {
    const { html } = await render();
    assert.match(html, /Truck &lt;Loan&gt;/);
    assert.doesNotMatch(html, /<Loan>/);
});
