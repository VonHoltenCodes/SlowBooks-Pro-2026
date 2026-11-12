// #824: an estimate's cost side is the contractor's own. A read-only or
// accountant sign-in reads the estimate without it (the server leaves it
// out), so the form shows no Cost column and no Cost of work row, and its
// bid panel shows the server's figures. A bookkeeper and an administrator
// see the form as before, on every company type. Drives the real
// EstimatesPage helpers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function estimatesContext(construction, role) {
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
        SalesLines: { rate: (v) => '$' + v, cents: (v) => Math.round(v * 100) / 100 },
    };
    if (role !== undefined) context.App = { role };
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
    return context;
}

const LINE = { id: 11, description: 'Slab', quantity: 1, rate: 40000, unit_cost: 40000 };

for (const construction of [false, true]) {
    const kind = construction ? 'construction' : 'business';

    for (const role of ['admin', 'bookkeeper', undefined]) {
        test(`${role || 'a single-user'} sign-in sees the Cost column (${kind})`, () => {
            const ctx = estimatesContext(construction, role);
            assert.equal(ctx.EstimatesPage.showsCost(), true);
            assert.match(ctx.EstimatesPage.costHead(), />Cost<\/th>/);
            const row = ctx.EstimatesPage.lineRowHtml(0, LINE, []);
            assert.match(row, /class="line-unit-cost money"[^>]*value="40000"/);
            if (construction) assert.match(ctx.EstimatesPage.bidTotalsPanelHtml(), /Cost of work/);
        });
    }

    for (const role of ['readonly', 'accountant']) {
        test(`a ${role} sign-in's form has no Cost column or Cost of work (${kind})`, () => {
            const ctx = estimatesContext(construction, role);
            assert.equal(ctx.EstimatesPage.showsCost(), false);
            assert.equal(ctx.EstimatesPage.costHead(), '');
            assert.equal(ctx.EstimatesPage.costCell(LINE), '');
            const row = ctx.EstimatesPage.lineRowHtml(0, LINE, []);
            assert.doesNotMatch(row, /line-unit-cost/);
            // the rate is the line's price, still shown
            assert.match(row, /class="line-rate money"/);
            const panel = ctx.EstimatesPage.bidTotalsPanelHtml();
            assert.doesNotMatch(panel, /Cost of work|est-bid-cost/);
            if (construction) assert.match(panel, /Bid total/);
        });
    }
}

test("a reader's bid panel shows the server's figures, not a recompute without costs", () => {
    const ctx = estimatesContext(true, 'readonly');
    const lines = [{ id: 11 }, { id: 12 }, { id: 13 }];
    const t = ctx.EstimatesPage.readerBidTotals({
        markup_allowances: false,
        gc: '8000.00', insurance: '1320.00', fee: '5466.00', bond: '1147.86',
        markup_total: '15933.86', priced_direct: '0', allowance_total: '5000.00',
        subtotal: '105000.00', bid_total: '120933.86', bid_tax: '0.00',
        bid_total_with_tax: '120933.86',
        alternates: [{ id: 13, description: 'Upgrade', amount: '1180.00', accepted: false }],
        unit_prices: [{ id: 12, description: 'Rock', unit: 'CY', rate: '95.00', quantity: '0' }],
    }, lines);
    assert.equal(t.gc, 8000);
    assert.equal(t.subtotal, 105000);
    assert.equal(t.bid, 120933.86);
    assert.equal(t.total, 120933.86);
    assert.equal(t.allowanceTotal, 5000);
    assert.equal(t.markupBase, undefined);
    assert.deepEqual(JSON.parse(JSON.stringify(t.alternates)), [{ idx: 2, desc: 'Upgrade', amount: 1180, accepted: false }]);
    assert.equal(t.unitPrices[0].idx, 1);
    assert.equal(t.unitPrices[0].unit, 'CY');
});
