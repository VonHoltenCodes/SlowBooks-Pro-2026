// #1237: Record Payment and a payment deposited to an account that is not a
// bank account. Edit opens the form with the payment's own Deposit To and
// Method; both used to be dropped when the form's lists lacked them (a
// liability such as Customer Deposits or Retainer Payable, a method such as
// "Retainer" written by an import), so Save sent a blank Deposit To and
// moved the payment to Undeposited Funds. payments.js is loaded alone, as
// the other probes load it, with DepositAccounts taken from utils.js.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');

const CHECKING = { id: 1, account_number: '1000', name: 'Checking', account_type: 'asset', is_control: true, bank_kind: 'bank', balance: 0, is_active: true };
const SAVINGS = { id: 2, account_number: '1010', name: 'Savings', account_type: 'asset', is_control: false, bank_kind: 'bank', balance: 0, is_active: true };
const UF = { id: 4, account_number: '1200', name: 'Undeposited Funds', account_type: 'asset', is_control: true, bank_kind: null, balance: 0, is_active: true };
const CARD = { id: 5, account_number: '2100', name: 'Visa', account_type: 'liability', is_control: true, bank_kind: 'credit_card', balance: 0, is_active: true };
const RETAINER = { id: 7, account_number: '2290', name: 'Retainer Payable', account_type: 'liability', is_control: false, bank_kind: null, balance: '500.00', is_active: true };
const DEPOSITS = { id: 8, account_number: '2210', name: 'Customer Deposits', account_type: 'liability', is_control: false, bank_kind: null, balance: '0.00', is_active: true };
const ALL = [CHECKING, SAVINGS, UF, CARD, RETAINER, DEPOSITS];

function depositAccounts(App) {
    const ctx = { window: {}, App, document: { addEventListener() {} } };
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(JS, 'utils.js'), 'utf8') + '\nthis.DepositAccounts = DepositAccounts;', ctx);
    return ctx.DepositAccounts;
}

function loadPayments({ accounts = ALL, settings = {} } = {}) {
    let modal = '';
    const App = { settings: JSON.parse(JSON.stringify(settings)), navigate() {} };
    const context = {
        console, window: {}, location: { hash: '#/' },
        document: { addEventListener() {}, getElementById: () => null, querySelector: () => null },
        escapeHtml: s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
        customerLabel: c => (c && c.name) || '',
        formatCurrency: n => '$' + (Number(n) || 0).toFixed(2),
        todayISO: () => '2026-11-27',
        T: s => s,
        Terms: { text: s => s, isNonprofit: () => false, isConstruction: () => false },
        App,
        toast() {}, closeModal() {}, confirm: () => true,
        openModal: (_title, html) => { modal = html; },
        PaymentClass: { load: async () => {}, groupHtml: () => '' },
        DepositAccounts: depositAccounts(App),
        $: () => null,
        $$: () => [],
        API: { get: async url => (url.startsWith('/accounts') ? JSON.parse(JSON.stringify(accounts)) : []) },
    };
    context.fetchAllPages = url => context.API.get(url);
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'payments.js'), 'utf8') + '\nthis.PaymentsPage = PaymentsPage;', context);
    return { page: context.PaymentsPage, modal: () => modal, context, App };
}

// a <select>'s options, in order: [value, text]; an <option> with no value
// attribute has its text as its value, as in the browser
function options(html, name) {
    const at = html.indexOf(`name="${name}"`);
    assert.ok(at >= 0, `no ${name}`);
    const select = html.slice(at, html.indexOf('</select>', at));
    return [...select.matchAll(/<option(?: value="([^"]*)")?([^>]*)>([^<]*)<\/option>/g)]
        .map(m => [m[1] === undefined ? m[3] : m[1], m[3]]);
}
const depositTo = html => options(html, 'deposit_to_account_id');
const method = html => options(html, 'method');

test('a new payment: Deposit To is today\'s list, no liability in it', async () => {
    const p = loadPayments();
    await p.page.showForm();
    assert.deepEqual(depositTo(p.modal()).map(o => o[0]), ['', '1', '2']);
    assert.deepEqual(method(p.modal()).map(o => o[0]), ['', 'Check', 'Cash', 'Credit Card', 'ACH/EFT', 'Other']);
});

test('Edit of a payment deposited to a liability keeps that account', async () => {
    for (const acct of [RETAINER, DEPOSITS]) {
        const p = loadPayments();
        await p.page.showForm(null, null, acct.id, null);
        const opts = depositTo(p.modal());
        assert.deepEqual(opts.map(o => o[0]), ['', '1', '2', String(acct.id)]);
        assert.equal(opts[3][1], acct.name);
    }
});

test('Edit keeps a method the list lacks, once, escaped', async () => {
    const p = loadPayments();
    await p.page.showForm(null, null, RETAINER.id, 'Retainer');
    assert.deepEqual(method(p.modal()).map(o => o[0]), ['', 'Check', 'Cash', 'Credit Card', 'ACH/EFT', 'Other', 'Retainer']);
    const q = loadPayments();
    await q.page.showForm(null, null, null, 'Check');
    assert.deepEqual(method(q.modal()).map(o => o[0]), ['', 'Check', 'Cash', 'Credit Card', 'ACH/EFT', 'Other']);
    const r = loadPayments();
    await r.page.showForm(null, null, null, '<b>x"</b>');
    assert.ok(!r.modal().includes('<b>x"</b>'));
    assert.ok(r.modal().includes('&lt;b&gt;x&quot;&lt;/b&gt;'));
});

test('Edit of a bank or Undeposited Funds payment adds nothing', async () => {
    for (const keep of [SAVINGS.id, UF.id, null, 999]) {
        const p = loadPayments();
        await p.page.showForm(null, null, keep, null);
        assert.deepEqual(depositTo(p.modal()).map(o => o[0]), ['', '1', '2']);
    }
});

test('Edit sends the kept account back: replace() sets the select to it', () => {
    // the form's own source: replace() opens showForm with the payment's
    // Deposit To and Method, then sets both selects to them
    const src = fs.readFileSync(path.join(JS, 'payments.js'), 'utf8');
    const body = src.slice(src.indexOf('async replace(id)'), src.indexOf('_giveBack: null'));
    assert.match(body, /showForm\(null, null, p\.deposit_to_account_id \|\| null, p\.method \|\| null\)/);
    assert.match(body, /Replace\.set\(form, 'deposit_to_account_id', p\.deposit_to_account_id \|\| ''\)/);
    assert.match(body, /Replace\.set\(form, 'method', p\.method \|\| ''\)/);
});
