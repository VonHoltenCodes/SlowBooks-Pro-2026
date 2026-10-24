// A QuickBooks Online discount comes in as a negative line on the Discount
// item (2.18.0). The invoice form drew every price box with min="0", so the
// browser refused to save an invoice with a discount ("Value must be greater
// than or equal to 0"). A line with a negative price now draws its box
// without that floor; every other line keeps it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function page() {
    const context = {
        window: {}, App: { settings: {} },
        escapeHtml: value => String(value ?? ''),
        formatCurrency: value => `$${Number(value || 0).toFixed(2)}`,
    };
    vm.createContext(context);
    // the line's item picker (#445)
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/item_quick_add.js'), 'utf8'), context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/invoices.js'), 'utf8'), context);
    return context.window.InvoicesPage;
}

const rateBox = html => html.match(/<input class="line-rate"[^>]*>/)?.[0];
const items = [{ id: 7, name: 'Discount' }, { id: 8, name: 'Catering' }];

test('a discount line takes its negative price, on its Discount item', () => {
    const html = page().lineRowHtml(1, { item_id: 7, description: 'Discount 10%', quantity: 1, rate: '-10.00', is_taxable: true }, items);
    assert.doesNotMatch(rateBox(html), /min=/);
    assert.match(rateBox(html), /value="-10"/);
    assert.match(html, /<option value="7" selected>Discount<\/option>/);
    assert.match(html, /\$-10\.00/);  // its amount, as the page's formatCurrency prints it
});

test('every other line keeps its price box at zero or more', () => {
    const f = page();
    for (const line of [{ item_id: 8, quantity: 1, rate: '100.00' }, { quantity: 1, rate: 0 }, {}]) {
        assert.match(rateBox(f.lineRowHtml(0, line, items)), /min="0"/);
    }
});
