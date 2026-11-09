// #550: the shared CostTypes helper renders a per-line cost type cell under
// the same gate as the cost code column (CostCodes.any()), and the Trade
// Packages settings block is hidden outside construction mode through the
// real App.applyTerminology [data-construction] path.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function utilsContext() {
    const context = {
        console,
        window: {},
        document: { addEventListener() {} },
        API: { get: async () => [] },
        Terms: { mode: 'business', isConstruction() { return this.mode === 'construction'; } },
    };
    vm.createContext(context);
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../../app/static/js/utils.js'), 'utf8') +
            '\nthis.CostCodes = CostCodes; this.CostTypes = CostTypes;',
        context,
    );
    return context;
}

test('CostTypes renders nothing when the company has no cost codes', () => {
    const ctx = utilsContext();
    ctx.Terms.mode = 'construction';
    ctx.CostCodes._list = [];
    ctx.CostTypes._list = [{ code: 'labor', name: 'Labor' }];
    assert.equal(ctx.CostTypes.any(), false);
    assert.equal(ctx.CostTypes.headHtml(), '');
    assert.equal(ctx.CostTypes.cellHtml('line-cost-type'), '');
});

test('CostTypes renders nothing outside construction mode, even with cost codes', () => {
    const ctx = utilsContext();
    ctx.Terms.mode = 'business';
    ctx.CostCodes._list = [{ id: 1, code: '03', name: 'Concrete', label: '03 Concrete' }];
    ctx.CostTypes._list = [{ code: 'labor', name: 'Labor' }];
    assert.equal(ctx.CostTypes.any(), false);
    assert.equal(ctx.CostTypes.headHtml(), '');
});

test('CostTypes renders a header and a line cell for a construction company with cost codes', () => {
    const ctx = utilsContext();
    ctx.Terms.mode = 'construction';
    ctx.CostCodes._list = [{ id: 1, code: '03', name: 'Concrete', label: '03 Concrete' }];
    ctx.CostTypes._list = [
        { code: 'labor', name: 'Labor' },
        { code: 'material', name: 'Material' },
    ];
    assert.equal(ctx.CostTypes.any(), true);
    assert.match(ctx.CostTypes.headHtml(), /<th scope="col">Cost Type<\/th>/);
    const cell = ctx.CostTypes.cellHtml('line-cost-type', 'material');
    assert.match(cell, /<select class="line-cost-type">/);
    assert.match(cell, /<option value="">--<\/option>/); // blank = use the code's type
    assert.match(cell, /<option value="material" selected>Material<\/option>/);
    assert.match(cell, /<option value="labor" >Labor<\/option>/);
});

// The Trade Packages settings block renders only for a construction company
// (the house pattern: Settings renders after applyTerminology has run, so a
// data-construction gate would never reveal it). Drive the real
// SettingsPage.tradePackagesSectionHtml() in both modes.
function settingsContext(construction) {
    const context = {
        console,
        window: {},
        document: { addEventListener() {} },
        API: { get: async () => [] },
        escapeHtml: (s) => String(s ?? ''),
        T: (s) => s,
        Terms: { text: (s) => s, isNonprofit: () => false, isConstruction: () => construction },
        formatCurrency: (v) => String(v),
        formatDate: (v) => v,
        openModal() {}, closeModal() {}, toast() {}, prompt() {}, confirm: () => true,
    };
    vm.createContext(context);
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../../app/static/js/settings.js'), 'utf8') +
            '\nthis.SettingsPage = SettingsPage;',
        context,
    );
    return context.SettingsPage;
}

test('the Trade Packages block is absent from the Settings HTML for a business', () => {
    const html = settingsContext(false).tradePackagesSectionHtml();
    assert.equal(html, '');
});

test('the Trade Packages block renders in the Settings HTML for a contractor', () => {
    const html = settingsContext(true).tradePackagesSectionHtml();
    assert.match(html, /Trade Packages/);
    assert.match(html, /SettingsPage\.addTradePackage\(\)/);
    assert.match(html, /id="trade-packages-list"/);
});

test('CostTypes.accountFor resolves a construction line the way the server does', () => {
    const ctx = utilsContext();
    ctx.CostCodes._list = [
        { id: 1, code: '26 05 00', cost_type: 'subcontract', account_id: null },
        { id: 2, code: '03 30 00', cost_type: 'material', account_id: 77 },
    ];
    ctx.CostTypes._list = [
        { code: 'subcontract', default_account_id: 53 },
        { code: 'equipment', default_account_id: 54 },
    ];
    // business mode never resolves one (the line still needs its account)
    assert.equal(ctx.CostTypes.accountFor(1, null), null);
    ctx.Terms.mode = 'construction';
    assert.equal(ctx.CostTypes.accountFor(2, 'equipment'), 77); // the code's account first
    assert.equal(ctx.CostTypes.accountFor(1, null), 53); // else the code's type
    assert.equal(ctx.CostTypes.accountFor(1, 'equipment'), 54); // the line's own type wins
    assert.equal(ctx.CostTypes.accountFor(null, null), null);
});
