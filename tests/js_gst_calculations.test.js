const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const utilsCode = fs.readFileSync('app/static/js/utils.js', 'utf8');

const context = {
    document: {
        querySelector: () => null,
        querySelectorAll: () => [],
        createElement: () => ({ className: '', textContent: '', remove() {} }),
    },
    setTimeout,
    Intl,
    Date,
    Number,
    App: { settings: { prices_include_gst: 'false' } },
};
vm.createContext(context);
vm.runInContext(utilsCode, context);

let result = context.calculateGstTotals([
    { quantity: 1, rate: 100, gst_code: 'GST15', gst_rate: 0.15 },
    { quantity: 1, rate: 50, gst_code: 'ZERO', gst_rate: 0 },
]);

assert.strictEqual(result.subtotal, 150);
assert.strictEqual(result.tax_amount, 15);
assert.strictEqual(result.total, 165);

context.App.settings.prices_include_gst = 'true';
result = context.calculateGstTotals([
    { quantity: 1, rate: 115, gst_code: 'GST15', gst_rate: 0.15 },
]);

assert.strictEqual(result.subtotal, 100);
assert.strictEqual(result.tax_amount, 15);
assert.strictEqual(result.total, 115);

const row = {
    querySelector(selector) {
        const values = {
            '.line-qty': { value: '2' },
            '.line-rate': { value: '25' },
            '.line-gst': { value: 'EXEMPT' },
        };
        return values[selector] || null;
    },
};

assert.deepStrictEqual(JSON.parse(JSON.stringify(context.readGstLinePayload(row))), {
    quantity: 2,
    rate: 25,
    gst_code: 'EXEMPT',
    gst_rate: 0,
});
