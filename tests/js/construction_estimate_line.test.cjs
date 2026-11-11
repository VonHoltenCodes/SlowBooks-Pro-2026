// #551: in construction mode the estimate line row grows a Package, a Kind
// and a Unit cell, the form grows a Markups block, and the view modal grows
// an Accept button (guarded like the other write actions). A Business or
// Nonprofit company sees none of it. Drives the real EstimatesPage helpers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

// The form's money rounding is the sales forms' own (SalesLines.cents in
// invoices.js), loaded for real so the bid figures round as they do (#783).
function salesLinesCents() {
    const c = { console, window: {}, App: { settings: {} }, document: { addEventListener() {} } };
    vm.createContext(c);
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../../app/static/js/invoices.js'), 'utf8') + '\nthis.SalesLines = SalesLines;',
        c,
    );
    return c.SalesLines.cents;
}

function estimatesContext(construction) {
    const context = {
        console,
        window: {},
        document: { addEventListener() {} },
        API: { get: async () => [] },
        Terms: { mode: construction ? 'construction' : 'business', isConstruction() { return this.mode === 'construction'; } },
        escapeHtml: (s) => String(s ?? ''),
        T: (s) => s,
        formatCurrency: (v) => '$' + v,
        ItemQuickAdd: { options: () => '' },
        TaxDefault: { checked: () => '' },
        SalesLines: { rate: (v) => '$' + v, cents: salesLinesCents() },
    };
    vm.createContext(context);
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../../app/static/js/utils.js'), 'utf8') +
            '\nthis.CostCodes = CostCodes; this.CostTypes = CostTypes; this.TradePackages = TradePackages;',
        context,
    );
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../../app/static/js/estimates.js'), 'utf8') +
            '\nthis.EstimatesPage = EstimatesPage;',
        context,
    );
    // a package and a cost code so the construction cells have something to show
    context.CostCodes._list = [{ id: 1, code: '03', name: 'Concrete', label: '03 Concrete' }];
    context.CostTypes._list = [{ code: 'labor', name: 'Labor' }];
    context.TradePackages._list = [{ id: 7, name: 'Concrete' }];
    return context;
}

test('a construction line row carries the package, kind and unit cells', () => {
    const ctx = estimatesContext(true);
    const row = ctx.EstimatesPage.lineRowHtml(0, { line_kind: 'alternate', unit: 'SF' }, []);
    assert.match(row, /class="line-package"/);
    assert.match(row, /class="line-kind"/);
    assert.match(row, /class="line-unit"/);
    assert.match(row, /class="line-alt-accepted"/); // hidden accept flag rides along
    // the existing cells are still there in their pinned order
    assert.match(row, /class="line-item"/);
    assert.match(row, /class="line-unit-cost money"/);
});

test('a business line row carries none of the construction cells', () => {
    const ctx = estimatesContext(false);
    const row = ctx.EstimatesPage.lineRowHtml(0, {}, []);
    assert.doesNotMatch(row, /class="line-package"/);
    assert.doesNotMatch(row, /class="line-kind"/);
    assert.doesNotMatch(row, /class="line-unit"/);
    // today's form is intact
    assert.match(row, /class="line-item"/);
    assert.match(row, /class="line-unit-cost money"/);
});

test('a construction alternate line lifts the rate min so a deduct can be keyed', () => {
    const ctx = estimatesContext(true);
    // the markup itself keeps min=0 (the sales forms share it); the lift runs after render
    const alt = ctx.EstimatesPage.lineRowHtml(1, { line_kind: 'alternate' }, []);
    assert.match(alt, /class="line-rate money"[^>]*min="0"/);
    const calls = [];
    const row = (kindValue) => ({
        querySelector: (sel) => sel === '.line-kind' ? { value: kindValue }
            : { setAttribute: (k, v) => calls.push([kindValue, 'set', v]), removeAttribute: () => calls.push([kindValue, 'removed']) },
    });
    ctx.$$ = () => [row('base'), row('alternate')];
    ctx.EstimatesPage.liftAlternateMins();
    assert.deepEqual(calls, [['base', 'set', '0'], ['alternate', 'removed']]);
});

test('switching the kind to Alternate and back toggles the rate min', () => {
    const ctx = estimatesContext(true);
    ctx.EstimatesPage.recalc = () => {};
    let min = '0';
    const kind = { value: 'base' };
    const rate = { setAttribute: (k, v) => { min = v; }, removeAttribute: () => { min = null; } };
    const row = { querySelector: (s) => (s === '.line-rate' ? rate : s === '.line-kind' ? kind : null) };
    ctx.$$ = () => [row];
    kind.value = 'alternate';
    ctx.EstimatesPage.kindChanged(kind);
    assert.equal(min, null); // lifted — a deduct's negative amount is allowed
    kind.value = 'base';
    ctx.EstimatesPage.kindChanged(kind);
    assert.equal(min, '0'); // restored
});

test('the markups block shows only in construction mode', () => {
    assert.match(estimatesContext(true).EstimatesPage.markupsBlockHtml({}), /Markups/);
    assert.match(estimatesContext(true).EstimatesPage.markupsBlockHtml({}), /name="gc_pct"/);
    assert.equal(estimatesContext(false).EstimatesPage.markupsBlockHtml({}), '');
});

test('the Accept button shows only for a pending estimate with a job', () => {
    const page = estimatesContext(true).EstimatesPage;
    assert.match(page.acceptButtonHtml({ id: 5, job_id: 3, status: 'pending' }), /EstimatesPage\.accept\(5\)/);
    assert.equal(page.acceptButtonHtml({ id: 5, job_id: null, status: 'pending' }), '');
    assert.equal(page.acceptButtonHtml({ id: 5, job_id: 3, status: 'accepted' }), '');
});

test('Accept is a registered write action (hidden for read-only)', () => {
    const app = fs.readFileSync(path.join(__dirname, '../../app/static/js/app.js'), 'utf8');
    const block = app.slice(app.indexOf('WRITE_ACTIONS:'), app.indexOf('hideWriteControls(root)'));
    assert.match(block, /'EstimatesPage\.accept'/);
    // Price from cost is gone (#551): the lines stay at cost, no reprice action
    assert.doesNotMatch(block, /repriceFromCost/);
});

test('syncRatesToCost mirrors the unit cost into the rate box and locks it', () => {
    const ctx = estimatesContext(true);
    const makeCell = (kind, uc) => ({
        '.line-kind': { value: kind },
        '.line-unit-cost': { value: uc },
        '.line-rate': { value: '', readOnly: false, title: '' },
    });
    const atCost = makeCell('base', '250');
    const noCost = makeCell('base', '');
    const allowance = makeCell('allowance', '400');
    const rows = [atCost, noCost, allowance].map(cells => ({ querySelector: s => cells[s] || null }));
    ctx.$$ = () => rows;
    ctx.EstimatesPage.syncRatesToCost();
    // a base row with a unit cost: the rate mirrors it and is read-only
    assert.equal(atCost['.line-rate'].value, '250');
    assert.equal(atCost['.line-rate'].readOnly, true);
    assert.match(atCost['.line-rate'].title, /At cost/);
    // a base row with no cost stays editable
    assert.equal(noCost['.line-rate'].readOnly, false);
    // an allowance keeps an editable rate even with a cost typed
    assert.equal(allowance['.line-rate'].readOnly, false);
});

test('bidTotals puts the compounding markups under the subtotal (lines at cost)', () => {
    const ctx = estimatesContext(true);
    // one base line at cost 100,000, gc 10% (the withFakeDom form), lines at cost
    const rows = [makeRow(0, { kind: 'base', qty: 1, uc: 100000, rate: 100000, accepted: false })];
    withFakeDom(ctx, rows);
    const t = ctx.EstimatesPage.bidTotals();
    assert.equal(t.markupBase, 100000);
    assert.equal(t.gc, 10000);
    assert.equal(t.markupTotal, 10000);
    assert.equal(t.subtotal, 100000);
    assert.equal(t.bid, 110000); // subtotal + markups, pre-tax
    assert.equal(t.tax, 0); // no taxable line
    assert.equal(t.total, 110000);
});

// bidTotals()/toggleAlternate use the stable data-eline index, so removing a
// row never shifts which alternate an Accept checkbox toggles.
function makeRow(eline, { kind, qty, uc, rate, accepted, desc, unit, taxable }) {
    const cells = {
        '.line-kind': { value: kind },
        '.line-qty': { value: String(qty) },
        '.line-unit-cost': { value: uc == null ? '' : String(uc) },
        '.line-rate': { value: String(rate) },
        '.line-alt-accepted': { checked: !!accepted },
        '.line-taxable': { checked: !!taxable },
        '.line-desc': { value: desc || '' },
        '.line-unit': { value: unit || '' },
    };
    return { dataset: { eline: String(eline) }, querySelector: (s) => cells[s] || null };
}

function withFakeDom(ctx, rows) {
    const form = { gc_pct: { value: '10' }, insurance_pct: { value: '0' }, fee_pct: { value: '0' }, bond_pct: { value: '0' }, tax_rate: { value: '10' } };
    ctx.$ = (sel) => {
        if (sel === '#est-form') return form;
        const m = String(sel).match(/\[data-eline="(\d+)"\]/);
        if (m) return rows.find(r => r.dataset.eline === m[1]) || null;
        return null; // #est-bid-totals etc. absent — we call bidTotals directly
    };
    ctx.$$ = (sel) => (sel === '#est-lines tr' ? rows : []);
    ctx.EstimatesPage.recalc = () => {}; // toggleAlternate calls it; no DOM to touch
}

test('bidTotals and toggleAlternate ride the stable data-eline, not the row position', () => {
    const ctx = estimatesContext(true);
    // rows 0 (base), 1 (alternate) then REMOVE row 1, leaving 0 and 2 (alternate)
    const rows = [
        makeRow(0, { kind: 'base', qty: 1, uc: 100, rate: 110, accepted: false }),
        makeRow(2, { kind: 'alternate', qty: 1, uc: 50, rate: 55, accepted: false, desc: 'Skylight' }),
    ];
    withFakeDom(ctx, rows);

    const t = ctx.EstimatesPage.bidTotals();
    assert.equal(t.alternates.length, 1);
    assert.equal(t.alternates[0].idx, 2); // the data-eline, not positional 1

    ctx.EstimatesPage.toggleAlternate(t.alternates[0].idx, true);
    assert.equal(rows[1].querySelector('.line-alt-accepted').checked, true); // row with data-eline 2
    assert.equal(rows[0].querySelector('.line-alt-accepted').checked, false); // base row untouched
});

test('bidTotals taxes only the markups on taxable lines (server rule, est B3)', () => {
    const ctx = estimatesContext(true);
    // A $60,000 exempt, B $40,000 taxable, both at cost: the markups split
    // 60/40 by markup base and only B's share is taxed
    const rows = [
        makeRow(0, { kind: 'base', qty: 1, uc: 60000, rate: 60000, taxable: false }),
        makeRow(1, { kind: 'base', qty: 1, uc: 40000, rate: 40000, taxable: true }),
    ];
    withFakeDom(ctx, rows); // form tax_rate 10%, gc 10%
    const t = ctx.EstimatesPage.bidTotals();
    assert.equal(t.markupTotal, 10000);
    // tax = 10% × (40,000 + 40% of 10,000) = 4,400, not 10% × 50,000
    assert.equal(t.tax, 4400);
    // a taxable line priced direct carries no markup base, so no markup tax
    const ctx2 = estimatesContext(true);
    withFakeDom(ctx2, [
        makeRow(0, { kind: 'base', qty: 1, uc: 100000, rate: 100000, taxable: false }),
        makeRow(1, { kind: 'base', qty: 1, uc: null, rate: 0, taxable: true }),
    ]);
    assert.equal(ctx2.EstimatesPage.bidTotals().tax, 0);
});

test('the markups block carries the allowance-markup checkbox, off unless set', () => {
    const page = estimatesContext(true).EstimatesPage;
    const off = page.markupsBlockHtml({});
    assert.match(off, /name="markup_allowances"/);
    assert.doesNotMatch(off, /name="markup_allowances"\s+checked/);
    assert.match(page.markupsBlockHtml({ markup_allowances: true }), /name="markup_allowances"\s+checked/);
});

test('bidTotals leaves allowances out of the markup base unless the box is checked', () => {
    const rows = () => [
        makeRow(0, { kind: 'base', qty: 1, uc: 100000, rate: 100000, accepted: false }),
        makeRow(1, { kind: 'allowance', qty: 1, uc: '', rate: 20000, accepted: false }),
    ];
    const ctx = estimatesContext(true);
    withFakeDom(ctx, rows());
    const off = ctx.EstimatesPage.bidTotals();
    assert.equal(off.markupAllowances, false);
    assert.equal(off.markupBase, 100000);
    assert.equal(off.gc, 10000);
    const ctx2 = estimatesContext(true);
    withFakeDom(ctx2, rows());
    ctx2.$('#est-form').markup_allowances = { checked: true };
    const on = ctx2.EstimatesPage.bidTotals();
    assert.equal(on.markupAllowances, true);
    assert.equal(on.markupBase, 120000);
    assert.equal(on.gc, 12000);
});
