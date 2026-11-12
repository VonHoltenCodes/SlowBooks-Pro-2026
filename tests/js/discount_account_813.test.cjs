// The discount picker offers to make Discounts Given in a company that has
// no discount account for the sales side (#813): a new company is seeded
// with 4750; an existing one stopped its first discount at "Pick the
// account the discount posts to".
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load(accounts) {
    const posts = [], toasts = [];
    const select = { options: [], value: '', appendChild(o) { this.options.push(o); }, dispatchEvent() {} };
    const button = { removed: false, remove() { this.removed = true; } };
    const ctx = {
        console, window: {}, localStorage: { getItem: () => null, setItem() {} },
        escapeHtml: s => String(s ?? ''), Event: function Event(name) { this.type = name; },
        accountNumbersOn: () => true,
        toast: (m) => toasts.push(m),
        PaymentsPage: { _accounts: accounts },
        API: { post: async (url, body) => { posts.push([url, body]); return { id: 99, ...body }; } },
        document: {
            createElement: () => ({}),
            getElementById: id => (id === 'discount-account' ? select : id === 'discount-account-create' ? button : null),
        },
    };
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/payment_terms.js'), 'utf8') + '\nthis.PaymentTerms = PaymentTerms;', ctx);
    return { P: ctx.PaymentTerms, posts, select, button, toasts };
}

const INCOME = { id: 1, account_number: '4000', name: 'Service Income', account_type: 'income' };

test('with no discount account, the picker offers to make Discounts Given', async () => {
    const accounts = [INCOME];
    const { P, posts, select, button } = load(accounts);
    const html = P.createDiscountAccountHtml(accounts, 'sales', 'discount-account');
    assert.match(html, /id="discount-account-create"[\s\S]*>Create a Discounts Given account<\/button>/);
    assert.match(html, /data-write/);
    const made = await P.createDiscountAccount('discount-account');
    assert.deepEqual(JSON.parse(JSON.stringify(posts)), [['/accounts', { name: 'Discounts Given', account_type: 'income', account_number: '4750' }]]);
    assert.equal(made.id, 99);
    assert.equal(select.value, '99');
    assert.equal(button.removed, true);
    assert.equal(accounts.length, 2);
    // it is now the side's discount account: no second offer
    assert.equal(P.createDiscountAccountHtml(accounts, 'sales', 'discount-account'), '');
});

test('a chart with a sales discount account, or the purchase side, is offered nothing', () => {
    const { P } = load([]);
    assert.equal(P.createDiscountAccountHtml([INCOME, { id: 2, name: 'Sales Discounts', account_type: 'income' }], 'sales', 'x'), '');
    assert.equal(P.createDiscountAccountHtml([INCOME], 'purchases', 'x'), '');
});

test('4750 already used: Discounts Given is made without a number', async () => {
    const accounts = [INCOME, { id: 3, account_number: '4750', name: 'Grants', account_type: 'income' }];
    const { P, posts } = load(accounts);
    await P.createDiscountAccount('discount-account');
    assert.equal(posts[0][1].account_number, null);
});
