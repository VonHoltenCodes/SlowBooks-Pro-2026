// The bid an owner receives: the estimate form's "Show markup rows" checkbox
// and terms box, the view's Project line, alternate tag and Email button, and a
// taken alternate shown at cost in the form's alternates panel. A Business or
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
        Terms: { isConstruction: () => construction },
        escapeHtml: (s) => String(s ?? ''),
        T: (s) => s,
        formatCurrency: (v) => '$' + v,
        formatDate: (v) => String(v),
        statusBadge: (v) => String(v),
        ItemQuickAdd: { options: () => '' },
        TaxDefault: { checked: () => '' },
        SalesLines: { rate: (v) => '$' + v, cents: salesLinesCents() },
        // a saved estimate's Attachments panel (#881) is invoices.js's
        InvoicesPage: { attachmentsPanelHtml: () => '', loadAttachments() {} },
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
    return context;
}

const totals = (extra = {}) => ({
    markup_allowances: false, gc_pct: 8, insurance_pct: 0, fee_pct: 0, bond_pct: 0,
    gc: 8000, insurance: 0, fee: 0, bond: 0, subtotal: 100000, bid_total: 108000,
    by_package: [{ package_id: 1, package_name: 'Concrete' }],
    alternates: [], unit_prices: [], ...extra,
});

test('the markups block carries Show markup rows, on unless the estimate says off', () => {
    const page = estimatesContext(true).EstimatesPage;
    assert.match(page.markupsBlockHtml({}), /name="show_markup_rows"\s+checked/);
    assert.match(page.markupsBlockHtml({ show_markup_rows: true }), /name="show_markup_rows"\s+checked/);
    const off = page.markupsBlockHtml({ show_markup_rows: false });
    assert.match(off, /name="show_markup_rows"/);
    assert.doesNotMatch(off, /name="show_markup_rows"\s+checked/);
    assert.equal(estimatesContext(false).EstimatesPage.markupsBlockHtml({}), '');
});

test('the terms box shows in construction mode only and holds the estimate\'s terms', () => {
    const html = estimatesContext(true).EstimatesPage.termsBlockHtml({ terms: 'No rock.' });
    assert.match(html, /<textarea name="terms"[^>]*>No rock\.<\/textarea>/);
    assert.equal(estimatesContext(false).EstimatesPage.termsBlockHtml({ terms: 'x' }), '');
});

test('an accepted alternate is tagged in its package, and a hidden-rows note shows', () => {
    const page = estimatesContext(true).EstimatesPage;
    const est = {
        show_markup_rows: false, tax_amount: 0, total: 108000,
        totals: totals(),
        lines: [
            { description: 'Slab', quantity: '1', rate: 100000, amount: 100000, trade_package_id: 1, line_kind: 'base' },
            { description: 'Delete soffit', quantity: '1', rate: -6000, amount: -6000, trade_package_id: 1, line_kind: 'alternate', alternate_accepted: true },
        ],
    };
    const html = page.constructionViewHtml(est);
    assert.match(html, /Delete soffit.*\(deduct alternate, accepted\)/);
    assert.match(html, /markup rows are hidden/);
    est.show_markup_rows = true;
    assert.doesNotMatch(page.constructionViewHtml(est), /markup rows are hidden/);
});

// bidTotals reads the form through $/$$ like the form tests do.
function row(eline, { kind, qty, uc, rate, accepted }) {
    const cells = {
        '.line-kind': { value: kind }, '.line-qty': { value: String(qty) },
        '.line-unit-cost': { value: uc == null ? '' : String(uc) }, '.line-rate': { value: String(rate) },
        '.line-alt-accepted': { checked: !!accepted }, '.line-taxable': { checked: false },
        '.line-desc': { value: 'Alt' }, '.line-unit': { value: '' },
    };
    return { dataset: { eline: String(eline) }, querySelector: (s) => cells[s] || null };
}

function formWith(ctx, rows) {
    const form = { gc_pct: { value: '10' }, insurance_pct: { value: '0' }, fee_pct: { value: '0' }, bond_pct: { value: '0' }, tax_rate: { value: '0' } };
    ctx.$ = (sel) => (sel === '#est-form' ? form : null);
    ctx.$$ = (sel) => (sel === '#est-lines tr' ? rows : []);
}

test('a taken alternate shows at cost in the form; one not taken shows marked up', () => {
    const ctx = estimatesContext(true);
    // cost 1,000 at a 10% factor: marked up 1,100, at cost 1,000
    formWith(ctx, [
        row(0, { kind: 'alternate', qty: 1, uc: 1000, rate: 1000, accepted: false }),
        row(1, { kind: 'alternate', qty: 1, uc: 1000, rate: 1000, accepted: true }),
    ]);
    const t = ctx.EstimatesPage.bidTotals();
    assert.equal(t.alternates[0].amount, 1100);
    assert.equal(t.alternates[1].amount, 1000);
    assert.equal(t.alternates[1].accepted, true);
});

test('the view modal offers Email', async () => {
    const ctx = estimatesContext(true);
    let shown = '';
    ctx.openModal = (_title, html) => { shown = html; };
    ctx.API.get = async () => ({
        id: 7, estimate_number: 'E-1', customer_name: 'Owner', date: '2026-03-01', status: 'pending',
        job_id: null, job_name: 'Clinic', job_site_address: '1 Main St', tax_amount: 0, total: 0,
        totals: totals(), lines: [], notes: null, terms: 'Net 30',
    });
    await ctx.EstimatesPage.view(7);
    assert.match(shown, /EstimatesPage\.emailEstimate\(7\)/);
    assert.match(shown, /Project:<\/strong> Clinic, 1 Main St/);
    assert.match(shown, /Net 30/);
});
