const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const code = `${fs.readFileSync('app/static/js/tax.js', 'utf8')}
this.TaxPage = TaxPage;`;
const context = {
    Date,
    API: { get: async () => ({}) },
    formatCurrency: value => `$${Number(value || 0).toFixed(2)}`,
    escapeHtml: value => String(value || ''),
    $: () => ({ value: '2026-01-01' }),
    window: { open() {} },
};

vm.createContext(context);
vm.runInContext(code, context);

(async () => {
    const html = await context.TaxPage.render();
    assert.ok(html.includes('Disabled for SlowBooks NZ'));
    assert.ok(html.includes('GST Return'));
    assert.ok(!html.includes('Schedule C'));
    assert.ok(!html.includes('Profit or Loss from Business'));
})().catch(err => {
    console.error(err);
    process.exit(1);
});
