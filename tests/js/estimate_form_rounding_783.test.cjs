// #783: the construction estimate form's bid panel rounded in floating point
// (Math.round(x + EPSILON) at two places), so a deduct alternate of -2.005
// showed -$2.00 under a -$2.01 line and a total a cent off the saved estimate
// and its PDF. The panel now rounds every figure as the server's _q does,
// half away from zero, on the rounded lines, through the sales forms'
// shared SalesLines.cents. Drives the real invoices.js and estimates.js.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const js = (name) => fs.readFileSync(path.join(__dirname, '../../app/static/js', name), 'utf8');

function salesLines() {
    const c = { console, window: {}, App: { settings: {} }, document: { addEventListener() {} } };
    vm.createContext(c);
    vm.runInContext(js('invoices.js') + '\nthis.SalesLines = SalesLines;', c);
    return c.SalesLines;
}

function estimatesPage(rows, pct = {}) {
    const context = {
        console,
        window: {},
        document: { addEventListener() {} },
        API: { get: async () => [] },
        Terms: { isConstruction: () => true },
        escapeHtml: (s) => String(s ?? ''),
        T: (s) => s,
        formatCurrency: (v) => '$' + v,
        ItemQuickAdd: { options: () => '' },
        TaxDefault: { checked: () => '' },
        SalesLines: salesLines(),
    };
    vm.createContext(context);
    vm.runInContext(js('utils.js'), context);
    vm.runInContext(js('estimates.js') + '\nthis.EstimatesPage = EstimatesPage;', context);
    const v = (n) => ({ value: String(pct[n] ?? 0) });
    const form = { gc_pct: v('gc_pct'), insurance_pct: v('insurance_pct'), fee_pct: v('fee_pct'), bond_pct: v('bond_pct'), tax_rate: v('tax_rate') };
    context.$ = (sel) => (sel === '#est-form' ? form : null);
    context.$$ = (sel) => (sel === '#est-lines tr' ? rows : []);
    return context.EstimatesPage;
}

// a construction line row; a line with a unit cost is priced at cost (the
// rate mirrors it, as syncRatesToCost keeps it)
function row(eline, { kind = 'base', qty = 1, uc = null, rate, accepted = false, taxable = false }) {
    const cells = {
        '.line-kind': { value: kind },
        '.line-qty': { value: String(qty) },
        '.line-unit-cost': { value: uc == null ? '' : String(uc) },
        '.line-rate': { value: String(rate ?? uc ?? 0) },
        '.line-alt-accepted': { checked: accepted },
        '.line-taxable': { checked: taxable },
        '.line-desc': { value: '' },
        '.line-unit': { value: '' },
    };
    return { dataset: { eline: String(eline) }, querySelector: (s) => cells[s] || null };
}

test('a taken deduct alternate of -2.005 makes the form total 97.99, as the API and the PDF do', () => {
    const page = estimatesPage([
        row(0, { uc: 100 }),
        row(1, { kind: 'alternate', uc: -2.005, accepted: true }),
    ]);
    const t = page.bidTotals();
    assert.equal(page._r(-2.005), -2.01);
    assert.equal(t.alternates[0].amount, -2.01);
    assert.equal(t.markupBase, 97.99);
    assert.equal(t.subtotal, 97.99);
    assert.equal(t.bid, 97.99);
    assert.equal(t.total, 97.99);
});

test('a half cent goes away from zero on either side of it', () => {
    const { cents } = salesLines();
    assert.equal(cents(1.005), 1.01);
    assert.equal(cents(-1.005), -1.01);
    assert.equal(cents(2.675), 2.68);
    assert.equal(cents(-2.675), -2.68);
    assert.equal(cents(0.015), 0.02);
    assert.equal(cents(-0.015), -0.02);
    assert.equal(cents(-2.005), -2.01);
    assert.equal(cents(1.004), 1);
    assert.equal(cents(-1.004), -1);
});

test('floating-point noise in a product does not move the cent', () => {
    const { cents } = salesLines();
    // 0.3 x 2.15 is 0.6449999999999999 and 0.09 x 2.5 is 0.22499999999999998
    // in floating point; the server multiplies decimals and gets the half
    assert.equal(cents(0.3 * 2.15), 0.65);
    assert.equal(cents(0.09 * 2.5), 0.23);
    assert.equal(cents(-0.3 * 2.15), -0.65);
    assert.equal(cents(2.5 * 13.37), 33.43);
    assert.equal(cents(0.1 + 0.2), 0.3);
    assert.equal(cents(97.99000000000001), 97.99);
    assert.equal(cents(1e-9), 0);
    assert.equal(cents(-1e-9), 0);
    assert.equal(Object.is(cents(-1e-9), -0), false);
});

test('large amounts keep their cents, and past 1e21 the result is still a number', () => {
    const { cents } = salesLines();
    assert.equal(cents(10824891.745), 10824891.75);
    assert.equal(cents(-10824891.745), -10824891.75);
    assert.equal(cents(123456789012.345), 123456789012.35);
    assert.equal(cents(0), 0);
    assert.ok(Number.isFinite(cents(1e21)));
    assert.ok(Number.isFinite(cents(-5e22)));
});

test('the markups, tax and total are built from the rounded figures, as the server builds them', () => {
    // cost 0.3 x 2.15 = 0.645 → 0.65; gc 10% of 0.65 = 0.065 → 0.07;
    // bid 0.72; tax 10% of 0.72 = 0.072 → 0.07; total 0.79
    const page = estimatesPage([row(0, { qty: 0.3, uc: 2.15, taxable: true })], { gc_pct: 10, tax_rate: 10 });
    const t = page.bidTotals();
    assert.equal(t.markupBase, 0.65);
    assert.equal(t.gc, 0.07);
    assert.equal(t.bid, 0.72);
    assert.equal(t.tax, 0.07);
    assert.equal(t.total, 0.79);
});
