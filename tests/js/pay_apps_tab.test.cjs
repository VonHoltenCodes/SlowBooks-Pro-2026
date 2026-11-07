// #555: in construction mode the job detail grows a Pay apps tab, after
// Change orders — the owner pay application grid (previous / this period /
// stored / completed / retainage), the totals block, the deposits block, and
// the guards. The sub pay-app and release-retainage buttons live on the
// Purchase Orders page, and the invoice view/PDF show the retainage lines.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function jobsContext(construction) {
    const context = {
        console,
        window: {},
        document: { addEventListener() {} },
        API: { get: async () => [] },
        Terms: {
            mode: construction ? 'construction' : 'business',
            isConstruction() { return this.mode === 'construction'; },
            text: (s) => s,
        },
        escapeHtml: (s) => String(s ?? ''),
        T: (s) => s,
        formatCurrency: (v) => '$' + Number(v || 0).toFixed(2),
        todayISO: () => '2026-06-01',
    };
    vm.createContext(context);
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../../app/static/js/utils.js'), 'utf8') +
            '\nthis.TradePackages = TradePackages;',
        context,
    );
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../../app/static/js/jobs.js'), 'utf8') +
            '\nthis.JobsPage = JobsPage;',
        context,
    );
    return context;
}

test('the Pay apps tab shows only in construction mode, after Change orders', () => {
    const labels = jobsContext(true).JobsPage.tabLabels();
    assert.ok('payapps' in labels);
    const keys = Object.keys(labels);
    assert.ok(keys.indexOf('payapps') > keys.indexOf('changeorders'));
    assert.ok(!('payapps' in jobsContext(false).JobsPage.tabLabels()));
});

test('the pay-app line math computes completed, retainage and balance', () => {
    const page = jobsContext(true).JobsPage;
    // scheduled 60,000; previous 30,000; this period 20,000; stored 5,000; 10%
    const r = page.paLineMath(60000, 30000, 20000, 5000, 10);
    assert.equal(r.completed, 55000);
    assert.equal(r.retainage, 5500);
    assert.equal(r.balance, 5000);
    assert.equal(r.pct_complete, 91.7);
    // no retainage when the percent is blank
    assert.equal(page.paLineMath(60000, 0, 10000, 0, 0).retainage, 0);
});

test('the grid renders each SOV line with live figures and the totals block', () => {
    const page = jobsContext(true).JobsPage;
    const grid = page.paGridHtml(
        [{ sov_line_id: 1, item_no: '01', description: 'Sitework', scheduled_value: 60000, previous: 30000, this_period: 20000, stored: 5000 }],
        { completed_to_date: 55000, retainage_to_date: 5500, current_due: 18000, net_due: 18000 },
        { pct: 10 },
    );
    assert.match(grid, /data-sov="1"/);
    // the row's completed / retainage / balance cells are computed live by the
    // grid's own paLineMath, not echoed — prove the real numbers land.
    assert.match(grid, /pa-completed">\$55,000\.00/);
    assert.match(grid, /pa-retainage">\$5,500\.00/);
    assert.match(grid, /pa-balance">\$5,000\.00/);
});

test('the SOV grid round-trips a line id so a billed line is preserved', () => {
    const page = jobsContext(true).JobsPage;
    const row = page.sovRowHtml(
        { id: 7, item_no: '01', description: 'Sitework', kind: 'work', scheduled_value: 1000 },
        0, [], [],
    );
    assert.match(row, /data-id="7"/);
    // and _readSov emits that id back to the server
    const jobs = fs.readFileSync(path.join(__dirname, '../../app/static/js/jobs.js'), 'utf8');
    assert.match(jobs, /id:\s*tr\.dataset\.id\s*\?\s*parseInt\(tr\.dataset\.id\)/);
});

test('the pay-app log carries its columns, a guarded New, approve and PDF', () => {
    const page = jobsContext(true).JobsPage;
    const log = page.paLogHtml([
        { id: 3, number: 'PA-1-01', period_to: '2026-06-30', status: 'approved', invoice_id: 9, invoice_number: '1005', totals: { this_period_total: 30000, retainage_this_period: 3000, current_due: 27000, net_due: 27000 } },
    ]);
    assert.match(log, /data-write[\s\S]*JobsPage\.paNew\(\)/);
    assert.match(log, /pay-applications\/3\/pdf/);
    assert.match(log, /#\/invoices\/9/);
});

test('the deposits block records, lists and voids', () => {
    const page = jobsContext(true).JobsPage;
    const html = page.depositsBlockHtml({
        deposits: [{ id: 2, date: '2026-05-01', amount: 10000, memo: 'Down payment', voided_at: null }],
        received: 10000, applied: 0, unapplied: 10000,
    });
    assert.match(html, /Owner deposits/);
    assert.match(html, /data-write[\s\S]*JobsPage\.paAddDeposit\(\)/);
    assert.match(html, /JobsPage\.paVoidDeposit\(2\)/);
});

test('the pay-app write actions are registered', () => {
    const app = fs.readFileSync(path.join(__dirname, '../../app/static/js/app.js'), 'utf8');
    const block = app.slice(app.indexOf('WRITE_ACTIONS:'), app.indexOf('hideWriteControls(root)'));
    for (const fn of ['paNew', 'paSave', 'paSubmit', 'paApprove', 'paVoid', 'paAddDeposit', 'paVoidDeposit']) {
        assert.match(block, new RegExp(`'JobsPage\\.${fn}'`));
    }
    for (const fn of ['subPayApp', 'releaseRetainage']) {
        assert.match(block, new RegExp(`'PurchaseOrdersPage\\.${fn}'`));
    }
});

test('the Sub pay app and Release retainage calls wire to their routes', () => {
    const po = fs.readFileSync(path.join(__dirname, '../../app/static/js/purchase_orders.js'), 'utf8');
    assert.match(po, /subPayApp\(id\)/);
    assert.match(po, /purchase-orders\/\$\{id\}\/pay-app/);
    assert.match(po, /releaseRetainage\(id\)/);
    assert.match(po, /purchase-orders\/\$\{id\}\/release-retainage/);
});

test('the invoice view shows the retainage lines', () => {
    const inv = fs.readFileSync(path.join(__dirname, '../../app/static/js/invoices.js'), 'utf8');
    assert.match(inv, /Retainage withheld this period/);
    assert.match(inv, /Retainage released/);
    assert.match(inv, /Current payment due/);
});

test('the application form carries the tax toggle and the waivers screen lists each type', () => {
    const page = jobsContext(true).JobsPage;
    const form = page.paFormHtml({
        id: 4, number: 'PA-1-02', status: 'draft', totals: {}, tax_rate: 0.08,
        lines: [{ sov_line_id: 1, description: 'Framing', scheduled_value: 1000, previous: 0, this_period: 0, stored: 0 }],
        charge_tax: true, retainage_pct: 10, unapplied_deposits: 0,
    });
    assert.match(form, /id="pa-tax-on"[^>]*checked/);
    assert.match(form, /id="pa-tax-rate"[^>]*value="8"/);
    const html = page.paWaiversHtml([
        { id: 5, date: '2026-06-15', amount: 5000, waivers: [{ id: 9, waiver_type: 'conditional_progress' }] },
    ]);
    assert.match(html, /lien-waivers\/9\/pdf/);
    assert.match(html, /paMakeWaiver\(5, 'unconditional_final'\)/);
});

test('an approved application offers its waivers', () => {
    const page = jobsContext(true).JobsPage;
    assert.match(page.paRowActions({ id: 3, status: 'approved', invoice_id: 9 }), /paWaivers\(3\)/);
    assert.doesNotMatch(page.paRowActions({ id: 3, status: 'draft' }), /paWaivers/);
});

test('an open application offers Delete, empty or not; an approved one does not (#723)', () => {
    const page = jobsContext(true).JobsPage;
    const empty = page.paRowActions({ id: 3, status: 'draft', lines: [] });
    assert.match(empty, /paDelete\(3, true\)/);
    assert.doesNotMatch(empty, /paSubmit|paApprove/);
    // a draft with lines is thrown away too, and still submitted or approved
    const full = page.paRowActions({ id: 3, status: 'draft', lines: [{ sov_line_id: 1 }] });
    assert.match(full, /paDelete\(3, false\)/);
    assert.match(full, /paSubmit\(3\)/);
    assert.match(page.paRowActions({ id: 3, status: 'submitted', lines: [{ sov_line_id: 1 }] }), /paDelete\(3, false\)/);
    assert.doesNotMatch(page.paRowActions({ id: 3, status: 'approved', lines: [] }), /paDelete/);
    assert.match(page.paFormHtml({ id: 3, number: 'PA-1-01', status: 'draft', lines: [], totals: {} }), /id="pa-empty"/);
});

test('Void shows only on the application the server says can be voided', () => {
    const page = jobsContext(true).JobsPage;
    assert.match(page.paRowActions({ id: 3, status: 'approved', can_void: true }), /paVoid\(3\)/);
    assert.doesNotMatch(page.paRowActions({ id: 2, status: 'approved', can_void: false }), /paVoid/);
    assert.doesNotMatch(page.paRowActions({ id: 2, status: 'approved' }), /paVoid/);
});

test('the pay apps list shows a billed column that foots with retainage, release, tax and current due', () => {
    const page = jobsContext(true).JobsPage;
    page.money = (v) => '$' + Number(v || 0).toFixed(2);
    const html = page.paLogHtml([{
        id: 3, number: 'PA-1-03', status: 'approved', can_void: true, lines: [{}],
        totals: { billed_this_period: 1000, retainage_this_period: 100, release_retainage: 50, tax_amount: 10, current_due: 960, net_due: 960 },
    }]);
    assert.match(html, /<th[^>]*>Billed<\/th>/);
    assert.match(html, /\$1000\.00<\/td>\s*<td class="amount">\$100\.00<\/td>\s*<td class="amount">\$50\.00<\/td>\s*<td class="amount">\$10\.00<\/td>\s*<td class="amount">\$960\.00/);
});

test('the form has an application date that follows Period to until it is set by hand', () => {
    const page = jobsContext(true).JobsPage;
    const form = page.paFormHtml({ id: 3, number: 'PA-1-01', status: 'draft', date: '2026-06-01', period_to: '2026-05-31', lines: [{ sov_line_id: 1, scheduled_value: 10 }], totals: {} });
    assert.match(form, /id="pa-date"[^>]*value="2026-06-01"/);
    const els = { '#pa-period': { value: '2026-07-31' }, '#pa-date': { value: '2026-06-01', dataset: {} } };
    const ctx = jobsContext(true);
    ctx.$ = (sel) => els[sel] || null;
    vm.runInContext('JobsPage.paPeriodChanged()', ctx);
    assert.equal(els['#pa-date'].value, '2026-07-31');
    els['#pa-date'].dataset.touched = '1';
    els['#pa-period'].value = '2026-08-31';
    vm.runInContext('JobsPage.paPeriodChanged()', ctx);
    assert.equal(els['#pa-date'].value, '2026-07-31');
});
