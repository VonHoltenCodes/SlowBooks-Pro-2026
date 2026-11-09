// #552: in construction mode the job detail grows a Contract tab — the terms
// block, the summary tiles and the schedule-of-values grid, every write
// guarded with data-write like the budget grid. A Business or Nonprofit
// company sees today's five tabs with no Contract tab. Drives the real
// JobsPage helpers.
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
        reversedRangeRefused: () => false,
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
    context.TradePackages._list = [{ id: 7, name: 'Concrete' }];
    return context;
}

test('the Contract tab shows only in construction mode', () => {
    assert.ok('contract' in jobsContext(true).JobsPage.tabLabels());
    assert.ok(!('contract' in jobsContext(false).JobsPage.tabLabels()));
});

test('the SOV grid carries its columns and every write is guarded', () => {
    const page = jobsContext(true).JobsPage;
    const grid = page.sovGridHtml(
        [{ item_no: '01', description: 'Concrete', kind: 'work', scheduled_value: 14000, trade_package_id: 7 }],
        { contract_amount: 14000 },
        [{ id: 7, name: 'Concrete' }],
        [{ id: 1, code: '03 30 00' }],
        true,
    );
    // the schedule-of-values columns the spec names
    for (const col of ['Item #', 'Description', 'Package', 'Section', 'Revenue account', 'Tax', 'Kind', 'Scheduled value']) {
        assert.match(grid, new RegExp(col));
    }
    // the grid's inputs and buttons are write-guarded
    assert.match(grid, /class="sov-item" data-write/);
    assert.match(grid, /class="sov-value money" data-write/);
    assert.match(grid, /JobsPage\.saveSov\(\)/);
    assert.match(grid, /JobsPage\.buildSov\(\)/);
    // Build is enabled when the job has an accepted estimate
    assert.doesNotMatch(grid, /disabled[^>]*JobsPage\.buildSov/);
});

test('Build from accepted estimate is disabled without one', () => {
    const page = jobsContext(true).JobsPage;
    const grid = page.sovGridHtml([], { contract_amount: 0 }, [], [], false);
    assert.match(grid, /disabled[^>]*JobsPage\.buildSov/);
});

test('the balance line highlights until the SOV balances to the contract sum', () => {
    const page = jobsContext(true).JobsPage;
    const off = page.sovGridHtml(
        [{ description: 'A', kind: 'work', scheduled_value: 9000 }],
        { contract_amount: 10000 },
        [], [], false,
    );
    assert.match(off, /id="sov-balance" style="color:var\(--text-danger\)/);
    const even = page.sovGridHtml(
        [{ description: 'A', kind: 'work', scheduled_value: 10000 }],
        { contract_amount: 10000 },
        [], [], false,
    );
    assert.match(even, /id="sov-balance" style="color:var\(--text-success\)/);
});

test('the terms block carries the type select and the guarded form', () => {
    const page = jobsContext(true).JobsPage;
    const terms = page.contractTermsHtml({ contract_type: 'gmp', contract_amount: 500000 });
    assert.match(terms, /id="contract-terms" data-write/);
    assert.match(terms, /name="contract_type"/);
    assert.match(terms, /value="gmp" selected/);
    assert.match(terms, /name="retainage_pct"/);
    assert.match(terms, /JobsPage\.saveContract/);
});

test('saveContract and saveSov and buildSov are registered write actions', () => {
    const app = fs.readFileSync(path.join(__dirname, '../../app/static/js/app.js'), 'utf8');
    const block = app.slice(app.indexOf('WRITE_ACTIONS:'), app.indexOf('hideWriteControls(root)'));
    assert.match(block, /'JobsPage\.saveContract'/);
    assert.match(block, /'JobsPage\.saveSov'/);
    assert.match(block, /'JobsPage\.buildSov'/);
});

test('contractHtml loads the trade packages when the cache is empty', async () => {
    // start with no package list cached, and record every API path the tab hits
    const paths = [];
    const ctx = jobsContext(true);
    ctx.TradePackages._list = null;
    ctx.API.get = async (p) => {
        paths.push(p);
        if (p.startsWith('/jobs/') && p.endsWith('/contract')) return { contract: {}, sov: [] };
        return [];
    };
    ctx.JobsPage._job = { id: 1, customer_id: 2 };
    const html = await ctx.JobsPage.contractHtml();
    assert.ok(paths.includes('/trade-packages'), 'TradePackages.load() was called');
    assert.deepEqual(ctx.TradePackages._list, []);
    assert.match(html, /Schedule of values/);
});

test('each SOV row edits its revenue account and taxable flag', () => {
    const ctx = jobsContext(true);
    const page = ctx.JobsPage;
    page._sovAccounts = [{ id: 40, account_number: '4050', name: 'Contract Revenue' }, { id: 41, account_number: '4100', name: 'Product Sales' }];
    const row = page.sovRowHtml(
        { id: 3, description: 'Slab', kind: 'work', scheduled_value: 100, revenue_account_id: 41, is_taxable: false },
        0, [], [],
    );
    assert.match(row, /class="sov-acct" data-write/);
    assert.match(row, /<option value="41" selected>4100 Product Sales<\/option>/);
    assert.match(row, /<option value="">Contract Revenue \(default\)<\/option>/);
    assert.match(row, /class="sov-tax" data-write type="checkbox" aria-label="Taxable" >/);
    // a new row is taxed by default
    assert.match(page.sovRowHtml({}, 0, [], []), /class="sov-tax" data-write type="checkbox" aria-label="Taxable" checked/);
    // a change-order row shows its section, account and tax read-only
    const co = page.sovRowHtml({ kind: 'change_order', cost_code_label: '26 05 00 Electrical', is_taxable: false, scheduled_value: 5 }, 0, [], []);
    assert.match(co, /26 05 00 Electrical/);
    assert.match(co, /Contract Revenue/);
    assert.match(co, /<td>No<\/td>/);
});

test('saving the SOV sends each row\'s revenue account and taxable flag', () => {
    const ctx = jobsContext(true);
    const field = (value, extra = {}) => ({ value, ...extra });
    const tr = {
        dataset: { id: '3' },
        querySelector(sel) {
            return {
                '.sov-item': field('01'), '.sov-desc': field('Slab'), '.sov-pkg': field(''),
                '.sov-code': field('9'), '.sov-acct': field('41'), '.sov-tax': field('', { checked: false }),
                '.sov-kind': field('work'), '.sov-value': field('100'),
            }[sel];
        },
    };
    ctx.$$ = () => [tr];
    const [line] = ctx.JobsPage._readSov();
    assert.equal(line.revenue_account_id, 41);
    assert.equal(line.is_taxable, false);
    assert.equal(line.cost_code_id, 9);
});
