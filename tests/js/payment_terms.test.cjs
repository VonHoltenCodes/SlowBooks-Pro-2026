// The terms list on the forms (#664): every terms select offers the list's
// active terms plus the record's own text when it is off the list, so a
// save never changes it silently; and the discount Receive Payments and Pay
// Bills suggest is the terms' percent of the balance until the discount
// date passes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function helper(list, answer = true) {
    const context = {
        confirm: () => answer,
        window: {},
        escapeHtml: value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
        API: { get: async () => list },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/payment_terms.js'), 'utf8'), context);
    return context.window.PaymentTerms;
}

const LIST = [{ name: 'Due on Receipt' }, { name: 'Net 30' }, { name: '2% 10 Net 30' }];
const options = html => [...html.matchAll(/<option value="([^"]*)"( selected)?/g)].map(m => [m[1], !!m[2]]);

test('the select offers the list and keeps the selected term', async () => {
    const pt = helper(LIST);
    await pt.load();
    assert.deepEqual(options(pt.optionsHtml('Net 30')), [
        ['Due on Receipt', false], ['Net 30', true], ['2% 10 Net 30', false],
    ]);
    // a record's own text is offered and sent back exactly as stored, not
    // swapped for the list name it resembles (#665: a memo-only save of a
    // "net30" invoice saved it as "NET30" and tied it to a later term)
    const own = options(pt.optionsHtml('net  30'));
    assert.deepEqual(own.find(o => o[1]), ['net  30', true]);
    assert.deepEqual(own.find(o => o[0] === 'Net 30'), ['Net 30', false]);
});

test('terms off the list stay offered, and a blank choice can lead', async () => {
    const pt = helper(LIST);
    await pt.load();
    const opts = options(pt.optionsHtml('15th of following month', "The vendor's terms"));
    assert.deepEqual(opts[0], ['', false]);
    assert.deepEqual(opts[opts.length - 1], ['15th of following month', true]);
});

test('a stubbed or failed list leaves the record\'s own terms', async () => {
    const pt = helper({});
    await pt.load();
    assert.deepEqual(options(pt.optionsHtml('Net 30')), [['Net 30', true]]);
});

test('the discount is the percent of the balance until its date', () => {
    const pt = helper(LIST);
    const inv = { balance_due: '312.50', discount_percent: '2.00', discount_date: '2026-09-30' };
    assert.equal(pt.suggestedCents(inv, '2026-09-30'), 625);
    assert.equal(pt.suggestedCents(inv, '2026-10-01'), 0);
    assert.equal(pt.suggestedCents({ balance_due: '100' }, '2026-09-01'), 0);
});

test('a discount posts to an income, expense or cost of goods account', () => {
    const pt = helper(LIST);
    const html = pt.discountAccountOptions([
        { id: 1, name: 'Checking', account_type: 'asset' },
        { id: 2, name: 'Sales Discounts', account_type: 'income', account_number: '4950' },
        { id: 3, name: 'Old', account_type: 'expense', is_active: false },
    ], 'sales');
    assert.deepEqual(options(html), [['', false], ['2', true]]);
});

test('blank terms stay blank, not the first term on the list', async () => {
    const pt = helper(LIST);
    await pt.load();
    const opts = options(pt.optionsHtml(''));
    assert.deepEqual(opts[0], ['', true]);
    assert.ok(pt.optionsHtml('').includes('No terms (due in 30 days)'));
});

test('only a refusal that names the override is asked about, once', () => {
    const pt = helper(LIST);
    const named = { message: 'Its discount had to be taken by Oct 15. Send allow_discount_override to take it anyway.' };
    assert.equal(pt.asksOverride(named, {}), true);
    assert.equal(pt.asksOverride(named, { allow_discount_override: true }), false);
    assert.equal(pt.asksOverride({ message: 'Allocation exceeds bill balance' }, {}), false);
    assert.equal(helper(LIST, false).asksOverride(named, {}), false);
});

test('the discount account picked is the one for the side', () => {
    const pt = helper(LIST);
    const accounts = [
        { id: 1, name: 'Purchase Discounts Earned', account_type: 'income', account_number: '4800' },
        { id: 2, name: 'Sales Discounts', account_type: 'income', account_number: '4900' },
    ];
    assert.equal(options(pt.discountAccountOptions(accounts, 'sales')).find(o => o[1])[0], '2');
    assert.equal(options(pt.discountAccountOptions(accounts, 'purchases')).find(o => o[1])[0], '1');
});
