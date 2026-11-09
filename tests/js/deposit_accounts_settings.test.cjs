// Make Deposits' destination settings (#734, #735). "Default deposit
// account" (default_deposit_account_id) is the bank account Make Deposits
// opens on and the first bank in a payment's or a sales receipt's Deposit
// To, below Undeposited Funds, which stays the opening choice there (#709).
// "Deposit into these accounts" (deposit_account_ids) narrows those lists
// to the accounts chosen, and an edit keeps its own account. Blank is
// today's order. Each page is loaded alone, as the other probes
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

test('a default deactivated after it was chosen never opens Make Deposits (N2, the 2.31.10 gate)', async () => {
    const inactiveSavings = Object.assign({}, SAVINGS, { is_active: false });
    const opts = picker(await makeDeposits({ default_deposit_account_id: '2' }, [CHECKING, UF, inactiveSavings, PETTY, CARD]));
    assert.ok(!opts.some(o => o[2]), 'nothing is marked');
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

// --- #735: Deposit into these accounts ------------------------------------

test('Make Deposits lists only the accounts chosen, the default chosen among them', async () => {
    const opts = picker(await makeDeposits({ deposit_account_ids: '[1,3]', default_deposit_account_id: '3' }));
    assert.deepEqual(opts.map(o => o[0]), ['1', '3']);
    assert.deepEqual(opts.filter(o => o[2]).map(o => o[0]), ['3']);
});

test('an empty list, or one naming no bank account here, lists every bank account', async () => {
    for (const ids of ['[]', '', '[77]', 'garbage']) {
        const opts = picker(await makeDeposits({ deposit_account_ids: ids }));
        assert.deepEqual(opts.map(o => o[0]), ['1', '2', '3'], ids);
    }
});

test('a recent deposit into an account off the list is still listed', async () => {
    const recent = [{ id: 31, date: '2026-11-03', account_name: 'Savings', reference: 'SLIP-1', items: 1, amount: 125, voided: false, reconciled: false }];
    const html = await makeDeposits({ deposit_account_ids: '[1]' }, undefined, recent);
    assert.deepEqual(picker(html).map(o => o[0]), ['1']);
    assert.match(html, /<td>Savings<\/td>/);
    assert.match(html, /DepositsPage\.view\(31\)/);
});

test('editing a deposit offers no account to change: it keeps its own', async () => {
    const p = load('deposits.js', 'DepositsPage', {
        accounts: [CHECKING, SAVINGS], settings: { deposit_account_ids: '[1]', default_deposit_account_id: '1' },
        routes: { '/deposits/pending': [], '/deposits/31': { id: 31, date: '2026-11-03', account_name: 'Savings', reference: 'SLIP-1', amount: 125, payments: [] } },
    });
    await p.page.editDeposit(31);
    assert.ok(p.modal().includes('DepositsPage.saveEdit(31)'));
    assert.ok(!/deposit_to_account_id|deposit-bank-acct|<select/.test(p.modal()));
});

for (const [file, name, open] of forms) {
    test(`${file}: Deposit To offers Undeposited Funds and only the chosen bank accounts`, async () => {
        const html = await open(load(file, name, { accounts: [CHECKING, UF, SAVINGS, PETTY], settings: { deposit_account_ids: '[1,3]', default_deposit_account_id: '3' } }));
        assert.deepEqual(depositTo(html).map(o => o[0]), ['', '3', '1']);
    });
}

test('payments.js: editing a payment into an account off the list keeps that account', async () => {
    const p = load('payments.js', 'PaymentsPage', { accounts: [CHECKING, UF, SAVINGS, PETTY], settings: { deposit_account_ids: '[1]' } });
    await p.page.showForm(null, null, 2);
    const opts = depositTo(p.modal());
    assert.deepEqual(opts.map(o => o[0]), ['', '1', '2']);
    // a new payment does not offer it
    await p.page.showForm();
    assert.deepEqual(depositTo(p.modal()).map(o => o[0]), ['', '1']);
});

test('Settings: Deposit into these accounts is a labelled box per active bank account', () => {
    const html = settingsPage()._depositAccountsHtml({ deposit_account_ids: '[1,3]' }, ALL);
    assert.match(html, /<fieldset[^>]*>\s*<legend[^>]*>Deposit into these accounts<\/legend>/);
    const boxes = [...html.matchAll(/<label[^>]*><input type="checkbox" name="deposit-account:(\d+)" data-deposit-account="\d+"( checked)?[^>]*>([^<]*)<\/label>/g)]
        .map(m => [m[1], !!m[2], m[3]]);
    assert.deepEqual(boxes, [
        ['1', true, '1000 - Checking'],
        ['2', false, '1010 - Savings'],
        ['3', true, '1020 - Petty Cash'],
    ]);
});

test('Settings: a chosen account no longer an active bank stays ticked and says so', () => {
    const html = settingsPage()._depositAccountsHtml({ deposit_account_ids: '[6]' }, ALL);
    assert.match(html, /data-deposit-account="6" checked[^>]*>\(unavailable\) 1030 - Old Reserve<\/label>/);
});

test('Settings: Save sends the ticked boxes as the list, none ticked as []', async () => {
    const page = settingsPage();
    const box = (id, checked) => ({ checked, getAttribute: () => String(id) });
    assert.deepEqual(page._depositAccountsToSave([box(1, true), box(2, false), box(3, true)]), [1, 3]);
    assert.deepEqual(page._depositAccountsToSave([box(1, false)]), []);
    const src = fs.readFileSync(path.join(JS, 'settings.js'), 'utf8');
    const save = src.slice(src.indexOf('    async save(e) {'), src.indexOf("await API.put('/settings', data);"));
    assert.match(save, /data\.deposit_account_ids = JSON\.stringify\(SettingsPage\._depositAccountsToSave\(depositBoxes\)\)/);
    assert.match(save, /key\.startsWith\('deposit-account:'\)\) delete data\[key\]/);
});

// --- an account deactivated in the chart (#741) ----------------------------

const RETIRED = { ...SAVINGS, is_active: false };

test('Make Deposits leaves out a bank account deactivated in the chart', async () => {
    const opts = picker(await makeDeposits({}, [CHECKING, UF, RETIRED, PETTY, CARD]));
    assert.deepEqual(opts.map(o => o[0]), ['1', '3']);
});

test('Make Deposits leaves out a chosen account deactivated since it was chosen', async () => {
    const accounts = [CHECKING, UF, RETIRED, PETTY, CARD];
    assert.deepEqual(picker(await makeDeposits({ deposit_account_ids: '[1,2]' }, accounts)).map(o => o[0]), ['1']);
    // every chosen one deactivated: every active bank account, as with none chosen
    assert.deepEqual(picker(await makeDeposits({ deposit_account_ids: '[2]' }, accounts)).map(o => o[0]), ['1', '3']);
});

for (const [file, name, open] of forms) {
    test(`${file}: Deposit To leaves out a bank account deactivated in the chart`, async () => {
        const html = await open(load(file, name, { accounts: [CHECKING, UF, RETIRED, PETTY], settings: {} }));
        assert.deepEqual(depositTo(html).map(o => o[0]), ['', '1', '3']);
    });
}

test('payments.js: editing a payment into an account deactivated since keeps that account', async () => {
    const p = load('payments.js', 'PaymentsPage', { accounts: [CHECKING, UF, RETIRED, PETTY], settings: {} });
    await p.page.showForm(null, null, 2);
    assert.deepEqual(depositTo(p.modal()).map(o => o[0]), ['', '1', '2', '3']);
    // left off the list as well (#735), and still kept
    const p2 = load('payments.js', 'PaymentsPage', { accounts: [CHECKING, UF, RETIRED, PETTY], settings: { deposit_account_ids: '[1]' } });
    await p2.page.showForm(null, null, 2);
    assert.deepEqual(depositTo(p2.modal()).map(o => o[0]), ['', '1', '2']);
    // a new payment does not offer it
    await p.page.showForm();
    assert.deepEqual(depositTo(p.modal()).map(o => o[0]), ['', '1', '3']);
});

test('DepositAccounts.active keeps active accounts and the record\'s own, in order', () => {
    const DA = depositAccounts({ settings: {} });
    const ids = list => list.map(a => a.id);
    assert.deepEqual(ids(DA.active([CHECKING, RETIRED, PETTY])), [1, 3]);
    assert.deepEqual(ids(DA.active([CHECKING, RETIRED, PETTY], 2)), [1, 2, 3]);
    assert.deepEqual(ids(DA.active([CHECKING, RETIRED, PETTY], '2')), [1, 2, 3]);
    // an account with no is_active (an older answer) counts as active
    assert.deepEqual(ids(DA.active([{ id: 9 }])), [9]);
});

test('Batch payments: Deposit To leaves out an account deactivated in the chart', async () => {
    const p = load('batch_payments.js', 'BatchPaymentsPage', { accounts: [CHECKING, UF, RETIRED, PETTY], settings: {} });
    const html = await p.page.render();
    assert.deepEqual(depositTo(html).map(o => o[0]), ['', '1', '3']);
});
