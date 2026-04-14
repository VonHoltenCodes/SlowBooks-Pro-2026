const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const code = `${fs.readFileSync('app/static/js/xero_import.js', 'utf8')}\nthis.XeroImportPage = XeroImportPage;`;
let lastHtml = '';
const context = {
    App: { hasPermission() { return true; } },
    API: { authHeaders() { return { Authorization: 'Bearer token' }; } },
    escapeHtml: value => String(value || ''),
    toast() {},
    FormData,
    $: (selector) => {
        if (selector === '#xero-import-results') return { innerHTML: '' };
        if (selector === '#xero-import-files') return { files: [] };
        return null;
    },
    fetch: async () => ({ ok: true, json: async () => ({ import_ready: true, required_files: ['chart_of_accounts'], detected_files: { chart_of_accounts: 'xero_chart_of_accounts.csv' }, verification: { trial_balance_ok: true, profit_loss_ok: true, balance_sheet_ok: true }, counts: { accounts: 1 }, journal_groups: 1, errors: [] }) }),
    console,
};
vm.createContext(context);
vm.runInContext(code, context);

(async () => {
    const html = await context.XeroImportPage.render();
    assert.ok(html.includes('Xero Import'));
    assert.ok(html.includes('Required CSV Exports'));
    assert.ok(html.includes('General Ledger'));
    const summary = context.XeroImportPage.renderSummary({
        required_files: ['chart_of_accounts', 'general_ledger'],
        detected_files: { chart_of_accounts: 'coa.csv' },
        counts: { accounts: 2 },
        journal_groups: 1,
        import_ready: true,
        errors: [],
        verification: { trial_balance_ok: true, profit_loss_ok: false, profit_loss_mismatches: ['Net income mismatch'], balance_sheet_ok: true },
    }, false);
    assert.ok(summary.includes('Import ready'));
    assert.ok(summary.includes('Profit &amp; Loss'));
    assert.ok(summary.includes('Net income mismatch'));
})();
