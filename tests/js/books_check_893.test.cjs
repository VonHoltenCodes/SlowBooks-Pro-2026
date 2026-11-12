// Books Check (#893): the report page draws a row per check, PASS in green
// and a difference in the theme's warning colour with its sentence, and each
// document behind a difference as a link to its own page.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load(mode = 'business') {
    const navigated = [];
    const context = {
        console, JSON, URLSearchParams, Date,
        window: {},
        document: { querySelector: () => null, querySelectorAll: () => [] },
        escapeHtml: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
        formatCurrency: (v) => `$${Number(v).toFixed(2)}`,
        formatDate: (s) => s,
        T: (s) => (mode === 'nonprofit' && s === 'Invoice' ? 'Pledge' : s),
        Terms: { text: (s) => s, isNonprofit: () => mode === 'nonprofit', isConstruction: () => false },
        App: { navigate: (h) => navigated.push(h) },
    };
    vm.createContext(context);
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/reports.js'), 'utf8');
    vm.runInContext(src + '\nthis.ReportsPage = ReportsPage;', context);
    context.ReportsPage._leaveFrom = () => {};
    return { R: context.ReportsPage, navigated };
}

const DATA = {
    as_of: '2026-11-12',
    basis: 'accrual',
    differences: 1,
    checked: 2,
    summary: '1 difference — open Books Check.',
    rows: [
        {
            key: 'ap_aging', name: 'A/P Aging = Accounts Payable', status: 'pass', ok: true,
            subledger_label: 'A/P Aging total', control_label: 'Accounts Payable',
            subledger: 423.5, control: 423.5, difference: 0,
            sentence: 'The A/P Aging total equals 2000 Accounts Payable.', documents: [], more_documents: 0,
        },
        {
            key: 'ar_aging', name: 'A/R Aging = Accounts Receivable', status: 'difference', ok: false,
            subledger_label: 'A/R Aging total', control_label: 'Accounts Receivable',
            subledger: 845, control: 945, difference: -100,
            sentence: 'The A/R Aging total is $845.00 and 1100 Accounts Receivable is $945.00. Look at the documents below.',
            documents: [
                { type: 'journal', id: 41, number: 'entry 41', amount: 100, why: 'Posted straight to 1100.' },
                { type: 'invoice', id: 7, number: '1007', amount: 50, why: 'Its balance due drifted.' },
                { type: 'item', id: 3, number: 'Sourdough Loaf', amount: 5, why: 'Count drifted.' },
                { type: 'job', id: null, number: 'No job', amount: 9, why: 'No job.' },
            ],
            more_documents: 2,
        },
    ],
};

test('PASS is green, a difference is in the warning colour with its sentence', () => {
    const { R } = load();
    const html = R.booksCheckBody(DATA);
    assert.match(html, /books-check-pass[\s\S]*color:var\(--text-success\);">PASS/);
    assert.match(html, /books-check-difference[\s\S]*color:var\(--text-warning\);">Difference \$-100\.00/);
    assert.match(html, /Look at the documents below\./);
    assert.match(html, /1 difference — open Books Check\./);
    assert.match(html, /… and 2 more/);
    // Print, Save PDF and Save CSV for the same date
    assert.match(html, /\/api\/reports\/books-check\/pdf\?as_of=2026-11-12/);
    assert.match(html, /\/api\/reports\/books-check\/csv\?as_of=2026-11-12/);
});

test('each named document links to its page, and the link opens it', () => {
    const { R, navigated } = load();
    const html = R.booksCheckBody(DATA);
    assert.match(html, /data-row-key="books:ar_aging:journal:41:0"[^>]*>Journal entry entry 41</);
    assert.match(html, /data-row-key="books:ar_aging:invoice:7:1"[^>]*>Invoice 1007</);
    assert.equal(R.booksCheckHref({ type: 'invoice', id: 7 }), '#/invoices/7');
    assert.equal(R.booksCheckHref({ type: 'pay_run', id: 2 }), '#/payroll/2');
    assert.equal(R.booksCheckHref({ type: 'journal', id: 41 }), '#/journal/41');
    assert.equal(R.booksCheckHref({ type: 'item', id: 3 }), '#/items');
    assert.equal(R.booksCheckHref({ type: 'job', id: null }), null);
    // a job with no id is named, not linked
    assert.doesNotMatch(html, /books:ar_aging:job:/);
    R.openBooksCheckDocument('#/invoices/7', null);
    assert.deepEqual(navigated, ['#/invoices/7']);
});

test('document kinds are named in the company words', () => {
    const { R } = load('nonprofit');
    assert.match(R.booksCheckBody(DATA), />Pledge 1007</);
});

test('the view is registered as-of with its Back label and a card', () => {
    const { R } = load();
    const view = R._view('books-check');
    assert.ok(view && view.asOf);
    assert.equal(R._backText('books-check', ''), 'Back to Books Check');
    R._pins = [];
    const views = R._cards().map((c) => c.view);
    assert.equal(views[views.indexOf('trial-balance') + 1], 'books-check');
});
