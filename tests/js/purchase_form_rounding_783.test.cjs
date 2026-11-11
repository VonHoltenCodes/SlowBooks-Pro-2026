// #783: the bill, purchase order and vendor credit forms round a line with
// PurchaseLines.cents (vendors.js), a copy of the sales forms' rounding. It
// moved the point in a float's shortest text, so a product such as
// 0.3 x 2.15 (0.6449999999999999) showed $0.64 where the server stores
// $0.65. It now rounds as the server's _q does: half away from zero, on the
// value read to 15 significant digits. Runs the helper on its own, as
// tests/test_purchase_forms.py does.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function purchaseLines() {
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/vendors.js'), 'utf8');
    const start = src.indexOf('const PurchaseLines = {');
    const helper = src.slice(start, src.indexOf('\n};', start) + 3);
    const context = {};
    vm.createContext(context);
    vm.runInContext(helper + '\nthis.PurchaseLines = PurchaseLines;', context);
    return context.PurchaseLines;
}

test('a purchase line rounds a float-noise product to the cent the server stores', () => {
    const { cents } = purchaseLines();
    assert.equal(cents(0.3 * 2.15), 0.65);
    assert.equal(cents(-0.3 * 2.15), -0.65);
    assert.equal(cents(0.09 * 2.5), 0.23);
    assert.equal(cents(2.5 * 13.37), 33.43);
    assert.equal(cents(0.1 + 0.2), 0.3);
});

test('a half cent goes away from zero on either side of it', () => {
    const { cents } = purchaseLines();
    assert.equal(cents(1.005), 1.01);
    assert.equal(cents(-1.005), -1.01);
    assert.equal(cents(-2.005), -2.01);
    assert.equal(cents(2.675), 2.68);
    assert.equal(cents(0.015), 0.02);
    assert.equal(cents(19.994), 19.99);
});

test('a tiny amount is zero, never minus zero', () => {
    const { cents } = purchaseLines();
    assert.equal(cents(1e-9), 0);
    assert.equal(Object.is(cents(-1e-9), -0), false);
    assert.equal(Object.is(cents(-0.004), -0), false);
});

test('large amounts keep their cents, and past 1e21 the result is still a number', () => {
    const { cents } = purchaseLines();
    assert.equal(cents(10824891.745), 10824891.75);
    assert.equal(cents(-10824891.745), -10824891.75);
    assert.ok(Number.isFinite(cents(1e21)));
    assert.ok(Number.isFinite(cents(-5e22)));
});
