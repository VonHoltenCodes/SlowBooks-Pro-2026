// The item form's Income and Expense pickers (#1038) offer their own
// accounts and then, as Other accounts, the balance-sheet accounts a bill
// line offers (#766): a security deposit invoiced through an item credits
// a liability. Never a bank or card account, nor Accounts Receivable or
// Payable, Undeposited Funds or Inventory.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '..', '..', 'app', 'static', 'js');

function load() {
    const ctx = {
        console,
        window: {},
        escapeHtml: (s) => String(s ?? ''),
        accountLabel: (a) => `${a.account_number} ${a.name}`,
        API: { get: async () => [] },
        T: (s) => s,
        Terms: { isConstruction: () => false, isNonprofit: () => false },
    };
    vm.createContext(ctx);
    // utils.js wants a document; its pickerAccounts stands alone.
    const utils = fs.readFileSync(path.join(JS, 'utils.js'), 'utf8');
    const picker = utils.match(/function pickerAccounts\([\s\S]*?\n}\n/)[0];
    vm.runInContext(picker + '\nthis.pickerAccounts = pickerAccounts;', ctx);
    vm.runInContext(fs.readFileSync(path.join(JS, 'payment_terms.js'), 'utf8') + '\nthis.PaymentTerms = PaymentTerms;', ctx);
    vm.runInContext(fs.readFileSync(path.join(JS, 'vendors.js'), 'utf8') + '\nthis.PurchaseAccounts = PurchaseAccounts;', ctx);
    vm.runInContext(fs.readFileSync(path.join(JS, 'items.js'), 'utf8') + '\nthis.ItemsPage = ItemsPage;', ctx);
    return ctx.ItemsPage;
}

const acct = (id, number, name, type, extra = {}) =>
    ({ id, account_number: number, name, account_type: type, is_active: true, ...extra });
const ACCOUNTS = [
    acct(1, '1000', 'Checking', 'asset', { bank_kind: 'bank', detail_type: 'bank', is_control: true }),
    acct(2, '1100', 'Accounts Receivable', 'asset', { detail_type: 'accounts_receivable', is_control: true }),
    acct(3, '1200', 'Undeposited Funds', 'asset', { detail_type: 'other_current_asset', is_control: true }),
    acct(4, '1300', 'Inventory Asset', 'asset', { detail_type: 'other_current_asset', is_control: true }),
    acct(5, '1700', 'Escrow Deposits', 'asset', { detail_type: 'other_asset' }),
    acct(6, '2000', 'Accounts Payable', 'liability', { detail_type: 'accounts_payable', is_control: true }),
    acct(7, '2100', 'Credit Card', 'liability', { bank_kind: 'credit_card', detail_type: 'credit_card', is_control: true }),
    acct(8, '2450', 'Security Deposits Held', 'liability', { detail_type: 'other_current_liability' }),
    acct(9, '2500', 'Old Note', 'liability', { detail_type: 'other_current_liability', is_active: false }),
    acct(10, '3000', "Owner's Equity", 'equity', { detail_type: 'equity' }),
    acct(11, '4000', 'Service Income', 'income', { detail_type: 'income' }),
    acct(12, '5100', 'Materials Cost', 'cogs', { detail_type: 'cost_of_goods_sold' }),
    acct(13, '6000', 'Advertising', 'expense', { detail_type: 'expense' }),
];
const numbers = (list) => list.map((a) => a.account_number);

test('the income picker offers income, then the balance-sheet accounts a bill line offers', () => {
    const P = load();
    assert.deepEqual(numbers(P.withOtherAccounts(ACCOUNTS, ['income'])), ['4000', '1700', '2450', '3000']);
});

test('the expense picker offers expense and cost of goods, then the same Other accounts', () => {
    const P = load();
    assert.deepEqual(numbers(P.withOtherAccounts(ACCOUNTS, ['expense', 'cogs'])),
        ['5100', '6000', '1700', '2450', '3000']);
});

test('the account an item already uses stays listed, once', () => {
    const P = load();
    const kept = numbers(P.withOtherAccounts(ACCOUNTS, ['income'], 2));
    assert.ok(kept.includes('1100'));
    assert.equal(kept.filter((n) => n === '1100').length, 1);
    assert.ok(numbers(P.withOtherAccounts(ACCOUNTS, ['income'], 9)).includes('2500'));
});

test('an account Books Check ties is not offered, unless the item already uses it (W-196)', () => {
    const P = load();
    const refused = new Set([8, 10]);
    assert.deepEqual(numbers(P.withOtherAccounts(ACCOUNTS, ['income'], null, refused)), ['4000', '1700']);
    assert.deepEqual(numbers(P.withOtherAccounts(ACCOUNTS, ['income'], 8, refused)), ['2450', '4000', '1700']);
});

test('the income options group income first, then Other accounts', () => {
    const P = load();
    const html = P.incomeOptions(P.withOtherAccounts(ACCOUNTS, ['income']), 8);
    assert.ok(html.indexOf('<optgroup label="Income">') < html.indexOf('<optgroup label="Other accounts">'));
    assert.match(html, /<option value="8" selected>2450 Security Deposits Held<\/option>/);
    assert.ok(html.indexOf('4000 Service Income') < html.indexOf('<optgroup label="Other accounts">'));
});

test('the item form uses them for both pickers', () => {
    const src = fs.readFileSync(path.join(JS, 'items.js'), 'utf8');
    assert.ok(src.includes("ItemsPage.withOtherAccounts(accounts, ['income'], item.income_account_id, refused)"));
    assert.ok(src.includes("ItemsPage.withOtherAccounts(accounts, ['expense','cogs'], item.expense_account_id, refused)"));
    // the server's list of the accounts Books Check ties (W-196)
    assert.ok(src.includes("API.get('/items/refused-accounts')"));
    assert.ok(src.includes('ItemsPage.incomeOptions(incomeAccts, item.income_account_id)'));
    assert.ok(src.includes('PurchaseAccounts.options(expenseAccts, item.expense_account_id)'));
});
