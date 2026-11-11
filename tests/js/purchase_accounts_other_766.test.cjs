// The purchase-line Account picker (#766): bills, expenses and card charges
// offer the cost accounts and then, as Other accounts, the balance-sheet
// accounts a purchase can post to (a loan's principal, an escrow asset,
// equity); never a bank or card account, nor Accounts Receivable or
// Payable, nor the other control assets a sub-ledger keeps, Undeposited
// Funds and Inventory (W-143); the control liabilities and equity (Sales
// Tax Payable, Retained Earnings) are offered. `filter`, which purchase
// orders keep, is unchanged.
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
    vm.runInContext(fs.readFileSync(path.join(JS, 'payment_terms.js'), 'utf8') + '\nthis.PaymentTerms = PaymentTerms;', ctx);
    vm.runInContext(fs.readFileSync(path.join(JS, 'vendors.js'), 'utf8') + '\nthis.PurchaseAccounts = PurchaseAccounts;', ctx);
    return ctx.PurchaseAccounts;
}

const acct = (id, number, name, type, extra = {}) =>
    ({ id, account_number: number, name, account_type: type, is_active: true, ...extra });
const ACCOUNTS = [
    acct(1, '1000', 'Checking', 'asset', { bank_kind: 'bank', detail_type: 'bank', is_control: true }),
    acct(2, '1100', 'Accounts Receivable', 'asset', { detail_type: 'accounts_receivable', is_control: true }),
    acct(13, '1200', 'Undeposited Funds', 'asset', { detail_type: 'other_current_asset', is_control: true }),
    acct(14, '1300', 'Inventory Asset', 'asset', { detail_type: 'other_current_asset', is_control: true }),
    acct(3, '1700', 'Escrow Deposits', 'asset', { detail_type: 'other_asset' }),
    acct(4, '2000', 'Accounts Payable', 'liability', { detail_type: 'accounts_payable', is_control: true }),
    acct(5, '2100', 'Credit Card', 'liability', { bank_kind: 'credit_card', detail_type: 'credit_card', is_control: true }),
    acct(15, '2200', 'Sales Tax Payable', 'liability', { detail_type: 'other_current_liability', is_control: true }),
    acct(6, '2400', 'Loan Payable', 'liability', { detail_type: 'long_term_liability' }),
    acct(7, '2500', 'Old Note', 'liability', { detail_type: 'other_current_liability', is_active: false }),
    acct(8, '3000', "Owner's Equity", 'equity', { detail_type: 'equity' }),
    acct(16, '3200', 'Retained Earnings', 'equity', { detail_type: 'equity', is_control: true }),
    acct(9, '4000', 'Service Income', 'income', { detail_type: 'income' }),
    acct(10, '5100', 'Materials Cost', 'cogs', { detail_type: 'cost_of_goods_sold' }),
    acct(11, '6000', 'Advertising', 'expense', { detail_type: 'expense' }),
    acct(12, '6300', 'Interest Expense', 'expense', { detail_type: 'expense' }),
];
const numbers = (list) => list.map((a) => a.account_number);

test('bills, expenses and card charges offer the cost accounts and the balance-sheet accounts', () => {
    const P = load();
    assert.deepEqual(numbers(P.withBalanceSheet(ACCOUNTS)), ['1700', '2200', '2400', '3000', '3200', '5100', '6000', '6300']);
});

test('a bank or card account, the receivable and payable controls, income and an inactive account stay out', () => {
    const P = load();
    const offered = new Set(numbers(P.withBalanceSheet(ACCOUNTS)));
    for (const number of ['1000', '1100', '2000', '2100', '4000', '2500']) assert.ok(!offered.has(number), number);
});

test('the control assets a sub-ledger keeps stay out; the control liabilities and equity stay in (W-143)', () => {
    const P = load();
    const offered = new Set(numbers(P.withBalanceSheet(ACCOUNTS)));
    for (const number of ['1200', '1300']) assert.ok(!offered.has(number), number);
    for (const number of ['2200', '3200']) assert.ok(offered.has(number), number);
    // a record already on one keeps it, as for any account
    assert.ok(numbers(P.withBalanceSheet(ACCOUNTS, 13)).includes('1200'));
    assert.ok(numbers(P.withBalanceSheet(ACCOUNTS, 14)).includes('1300'));
});

test('an account already on the record is kept, whatever it is', () => {
    const P = load();
    assert.ok(numbers(P.withBalanceSheet(ACCOUNTS, 7, 9)).includes('2500'));
    assert.ok(numbers(P.withBalanceSheet(ACCOUNTS, 7, 9)).includes('4000'));
});

test('purchase orders and the vendor default keep the cost accounts only', () => {
    const P = load();
    assert.deepEqual(numbers(P.filter(ACCOUNTS)), ['5100', '6000', '6300']);
});

test('the picker groups cost of goods, then expenses, then Other accounts, in that order', () => {
    const P = load();
    const html = P.options(P.withBalanceSheet(ACCOUNTS), 6);
    const cogs = html.indexOf('<optgroup label="Cost of Goods Sold">');
    const exp = html.indexOf('<optgroup label="Expenses">');
    const other = html.indexOf('<optgroup label="Other accounts">');
    assert.ok(cogs >= 0 && exp > cogs && other > exp, html);
    const otherGroup = html.slice(other);
    for (const number of ['1700', '2200', '2400', '3000', '3200']) assert.ok(otherGroup.includes(number), number);
    for (const number of ['1200', '1300']) assert.ok(!otherGroup.includes(number), number);
    assert.ok(/<option value="6" selected>2400 Loan Payable<\/option>/.test(html), html);
    assert.ok(!html.includes('label="Other">'), 'the old label is gone');
});
