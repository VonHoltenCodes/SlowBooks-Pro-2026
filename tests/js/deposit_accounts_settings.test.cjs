// Make Deposits' destination settings (#734, #735). "Default deposit
// account" (default_deposit_account_id) is the bank account Make Deposits
// opens on and the first bank in a payment's or a sales receipt's Deposit
// To, below Undeposited Funds, which stays the opening choice there (#709).
// Blank is today's order. Each page is loaded alone, as the other probes
// load them, with DepositAccounts taken from utils.js.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');

const UF = { id: 4, account_number: '1200', name: 'Undeposited Funds', account_type: 'asset', is_control: true, bank_kind: null, balance: 0 };
const CHECKING = { id: 1, account_number: '1000', name: 'Checking', account_type: 'asset', is_control: true, bank_kind: 'bank', balance: 0 };
const SAVINGS = { id: 2, account_number: '1010', name: 'Savings', account_type: 'asset', is_control: false, bank_kind: 'bank', balance: 0 };
const PETTY = { id: 3, account_number: '1020', name: 'Petty Cash', account_type: 'asset', is_control: false, bank_kind: 'bank', balance: 0 };
const CARD = { id: 5, account_number: '2100', name: 'Visa', account_type: 'liability', is_control: false, bank_kind: 'credit_card', balance: 0 };

// utils.js in a context of its own, sharing the page's App (one global in
// the browser)
function depositAccounts(App) {
    const ctx = { window: {}, App, document: { addEventListener() {} } };
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(JS, 'utils.js'), 'utf8') + '\nthis.DepositAccounts = DepositAccounts;', ctx);
    return ctx.DepositAccounts;
}

function load(file, name, { accounts, settings = {}, routes = {} }) {
    let modal = '';
    const fields = {};
    const App = { settings: JSON.parse(JSON.stringify(settings)), navigate() {} };
    const context = {
        console, window: {}, location: { hash: '#/' },
        document: { addEventListener() {}, getElementById: () => null, querySelector: () => null },
        escapeHtml: s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
        customerLabel: c => (c && c.name) || '',
        formatCurrency: n => '$' + (Number(n) || 0).toFixed(2),
        formatDate: s => s || '',
        todayISO: () => '2026-11-08',
        T: s => s,
        Terms: { text: s => s, isNonprofit: () => false, isConstruction: () => false },
        App,
        ClassWarn: { enabled: () => false, okValue: () => true },
        toast() {}, closeModal() {}, confirm: () => true,
        openModal: (_title, html) => { modal = html; },
        classFormGroupHtml: async () => '',
        jobFormGroupHtml: async () => '',
        currencyFormGroupsHtml: () => '',
        PaymentClass: { load: async () => {}, groupHtml: () => '' },
        ScanHelper: { scanRowHtml: () => '', wire() {} },
        DepositAccounts: depositAccounts(App),
        $: () => null,
        $$: () => [],
        API: {
            get: async url => {
                for (const [prefix, value] of Object.entries(routes)) {
                    if (url.startsWith(prefix)) return JSON.parse(JSON.stringify(value));
                }
                if (url.startsWith('/accounts')) return JSON.parse(JSON.stringify(accounts));
                if (url === '/settings') return JSON.parse(JSON.stringify(settings));
                return [];
            },
        },
    };
    context.fetchAllPages = url => context.API.get(url);
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(JS, file), 'utf8') + `\nthis.${name} = ${name};`, context);
    return { page: context[name], modal: () => modal, context, fields };
}

// a <select>'s options, in order: [value, text, selected]
function options(html, marker) {
    const at = html.indexOf(marker);
    assert.ok(at >= 0, `no ${marker}`);
    const select = html.slice(at, html.indexOf('</select>', at));
    return [...select.matchAll(/<option value="([^"]*)"([^>]*)>([^<]*)<\/option>/g)]
        .map(m => [m[1], m[3], /\bselected\b/.test(m[2])]);
}

const PENDING = [{ transaction_line_id: 70, transaction_id: 7, amount: 125, date: '2026-11-02', description: 'Payment from Acme', received_from: 'Acme' }];

async function makeDeposits(settings, accounts = [CHECKING, UF, SAVINGS, PETTY, CARD], recent = []) {
    const p = load('deposits.js', 'DepositsPage', { accounts, settings, routes: { '/deposits/pending': PENDING, '/deposits?': recent } });
    return p.page.render();
}
const picker = html => options(html, 'id="deposit-bank-acct"');

test('Make Deposits opens on the default deposit account', async () => {
    const opts = picker(await makeDeposits({ default_deposit_account_id: '2' }));
    assert.deepEqual(opts.filter(o => o[2]).map(o => o[0]), ['2']);
    // the list keeps its order; the choice is only where it starts
    assert.deepEqual(opts.map(o => o[0]), ['1', '2', '3']);
});

test('Make Deposits with no default is today: the first bank, nothing marked', async () => {
    for (const settings of [{}, { default_deposit_account_id: '' }]) {
        const opts = picker(await makeDeposits(settings));
        assert.deepEqual(opts.map(o => o[0]), ['1', '2', '3']);
        assert.ok(!opts.some(o => o[2]));
    }
});

test('a default that is no longer a bank account on the list is ignored', async () => {
    const opts = picker(await makeDeposits({ default_deposit_account_id: '99' }));
    assert.ok(!opts.some(o => o[2]));
    assert.deepEqual(opts.map(o => o[0]), ['1', '2', '3']);
});

const forms = [
    ['payments.js', 'PaymentsPage', async p => { await p.page.showForm(); return p.modal(); }],
    ['sales_receipts.js', 'SalesReceiptsPage', async p => {
        p.page.lineRowHtml = () => '';
        p.page.recalc = () => {};
        await p.page.showForm();
        return p.modal();
    }],
];
const depositTo = html => options(html, 'name="deposit_to_account_id"');

for (const [file, name, open] of forms) {
    test(`${file}: Deposit To keeps Undeposited Funds first and chosen; the default bank follows it`, async () => {
        const html = await open(load(file, name, { accounts: [CHECKING, UF, SAVINGS, PETTY], settings: { default_deposit_account_id: '3' } }));
        const opts = depositTo(html);
        assert.deepEqual(opts[0], ['', 'Undeposited Funds (deposit later)', false]);
        assert.ok(!opts.some(o => o[2]), 'the box opens on Undeposited Funds');
        assert.deepEqual(opts.slice(1).map(o => o[0]), ['3', '1', '2']);
    });

    test(`${file}: with no Undeposited Funds account Deposit To starts on the default bank`, async () => {
        const html = await open(load(file, name, { accounts: [CHECKING, SAVINGS, PETTY], settings: { default_deposit_account_id: '2' } }));
        const opts = depositTo(html);
        assert.deepEqual(opts.filter(o => o[2]).map(o => o[0]), ['2']);
    });

    test(`${file}: no default leaves Deposit To in today's order`, async () => {
        const html = await open(load(file, name, { accounts: [CHECKING, UF, SAVINGS, PETTY], settings: {} }));
        const opts = depositTo(html);
        assert.deepEqual(opts.map(o => o[0]), ['', '1', '2', '3']);
        assert.ok(!opts.some(o => o[2]));
    });
}

// Settings → Connections → Deposits
function settingsPage() {
    const context = {
        API: { get: async () => ({}) }, setTimeout() {}, T: v => v,
        Terms: { text: v => v, isNonprofit: () => false, isConstruction: () => false },
        escapeHtml: v => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
        accountWords: a => `${a.account_number} - ${a.name}`,
        toast() {}, document: { getElementById: () => null },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'payment_terms.js'), 'utf8'), context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'settings.js'), 'utf8') + '\nthis.page = SettingsPage;', context);
    return context.page;
}
const CLOSED = { ...PETTY, id: 6, account_number: '1030', name: 'Old Reserve', is_active: false };
const ALL = [CHECKING, UF, SAVINGS, PETTY, CARD, CLOSED];

test('Settings: Default deposit account is a labelled picker of the active bank accounts', () => {
    const html = settingsPage()._depositAccountsHtml({ default_deposit_account_id: '2' }, ALL);
    assert.match(html, /<label for="settings-default-deposit-account">Default deposit account<\/label>/);
    assert.match(html, /<select name="default_deposit_account_id" id="settings-default-deposit-account"/);
    const opts = options(html, 'name="default_deposit_account_id"');
    assert.deepEqual(opts.map(o => o[0]), ['', '1', '2', '3']);
    assert.deepEqual(opts.filter(o => o[2]).map(o => o[0]), ['2']);
});

test('Settings: a saved default that is no longer an active bank stays chosen and says so', () => {
    const opts = options(settingsPage()._depositAccountsHtml({ default_deposit_account_id: '6' }, ALL), 'name="default_deposit_account_id"');
    assert.deepEqual(opts.filter(o => o[2]), [['6', '(unavailable) 1030 - Old Reserve', true]]);
});

test('Settings: an account name reaches the page escaped', () => {
    const html = settingsPage()._depositAccountsHtml({}, [{ ...CHECKING, name: '<b>Chk</b>' }]);
    assert.ok(!html.includes('<b>Chk</b>'));
    assert.ok(html.includes('&lt;b&gt;Chk&lt;/b&gt;'));
});
