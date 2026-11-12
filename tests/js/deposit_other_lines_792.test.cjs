// Make Deposits' lines for money that was never invoiced (#792): the grid
// rows beside the payments, with Add line; the accounts a line may take
// (no bank or card account, Undeposited Funds, Accounts Receivable or
// Accounts Payable); who it came from (a customer, a vendor, a name typed
// in); the sentences the server says too; the totals; the deposit's view
// and its edit. deposits.js is loaded alone, as the other probes load it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');

const ACCOUNTS = [
    { id: 1, account_number: '1000', name: 'Checking', account_type: 'asset', is_control: true, is_active: true, bank_kind: 'bank', balance: 0 },
    { id: 2, account_number: '1100', name: 'Accounts Receivable', account_type: 'asset', is_control: true, is_active: true, bank_kind: null, detail_type: 'accounts_receivable', balance: 0 },
    { id: 3, account_number: '1200', name: 'Undeposited Funds', account_type: 'asset', is_control: true, is_active: true, bank_kind: null, balance: 0 },
    { id: 4, account_number: '2000', name: 'Accounts Payable', account_type: 'liability', is_control: true, is_active: true, bank_kind: null, detail_type: 'accounts_payable', balance: 0 },
    { id: 5, account_number: '2100', name: 'Visa', account_type: 'liability', is_control: false, is_active: true, bank_kind: 'credit_card', balance: 0 },
    { id: 6, account_number: '4900', name: 'Other Income', account_type: 'income', is_control: false, is_active: true, bank_kind: null, balance: 0 },
    { id: 7, account_number: '6300', name: 'Insurance', account_type: 'expense', is_control: false, is_active: true, bank_kind: null, balance: 0 },
    { id: 8, account_number: '4300', name: 'Labor Income', account_type: 'income', is_control: false, is_active: false, bank_kind: null, balance: 0 },
];
const CUSTOMERS = [{ id: 11, name: 'Acme Diner' }];
const VENDORS = [{ id: 21, name: 'Flour Mill Co' }, { id: 22, name: 'Acme Diner' }];

function load({ routes = {}, construction = false } = {}) {
    let modal = '';
    const context = {
        console, window: {}, location: { hash: '#/deposits' },
        document: { addEventListener() {}, getElementById: () => null, querySelector: () => null },
        escapeHtml: s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
        formatCurrency: n => '$' + (Number(n) || 0).toFixed(2),
        formatDate: s => s || '',
        todayISO: () => '2026-11-12',
        T: s => s,
        Terms: { isConstruction: () => construction },
        App: { settings: {}, navigate() {} },
        ClassWarn: { enabled: () => false, okValue: () => true },
        toast() {}, closeModal() {}, confirm: () => true,
        openModal: (_title, html) => { modal = html; },
        $: () => null,
        $$: () => [],
        API: {
            get: async url => {
                for (const [prefix, value] of Object.entries(routes)) {
                    if (url.startsWith(prefix)) return JSON.parse(JSON.stringify(value));
                }
                if (url.startsWith('/accounts')) return JSON.parse(JSON.stringify(ACCOUNTS));
                if (url.startsWith('/customers')) return CUSTOMERS;
                if (url.startsWith('/vendors')) return VENDORS;
                if (url.startsWith('/jobs')) return [{ id: 31, full_name: 'Acme Diner: Remodel', is_active: true }];
                if (url.startsWith('/classes')) return [{ id: 41, name: 'Bakery' }];
                return [];
            },
        },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'deposits.js'), 'utf8') + '\nthis.DepositsPage = DepositsPage;', context);
    return { page: context.DepositsPage, modal: () => modal };
}

test('a line may take any active account but a bank, card or control account', () => {
    const { page } = load();
    assert.deepEqual(page.otherAccounts(ACCOUNTS).map(a => a.name), ['Other Income', 'Insurance']);
});

test('who it came from: a customer first, then a vendor, else the name typed', () => {
    const { page } = load();
    assert.deepEqual({ ...page.resolveName('acme diner', CUSTOMERS, VENDORS) }, { customer_id: 11 });
    assert.deepEqual({ ...page.resolveName(' Flour Mill Co ', CUSTOMERS, VENDORS) }, { vendor_id: 21 });
    assert.deepEqual({ ...page.resolveName('State Energy Office', CUSTOMERS, VENDORS) }, { received_from: 'State Energy Office' });
    assert.deepEqual({ ...page.resolveName('', CUSTOMERS, VENDORS) }, {});
});

test('the rows become other_lines; a blank row is skipped; the sentences match the server', () => {
    const { page } = load();
    const ctx = page._otherContext({ accounts: ACCOUNTS, customers: CUSTOMERS, vendors: VENDORS });
    const built = page.buildOtherLines([
        {},
        { name: 'Flour Mill Co', account_id: '7', memo: ' Policy refund ', check_number: '88123', method: 'Check', class_id: '41', amount: '45.5' },
    ], ctx);
    assert.equal(built.error, undefined);
    assert.deepEqual(JSON.parse(JSON.stringify(built.lines)), [
        { vendor_id: 21, account_id: 7, amount: '45.50', memo: 'Policy refund', check_number: '88123', method: 'Check', class_id: 41 },
    ]);
    assert.equal(page.buildOtherLines([{ amount: '5' }], ctx).error,
        'Other deposits, line 1: choose the account the money goes to. Nothing was saved.');
    assert.equal(page.buildOtherLines([{ account_id: '6', amount: '5' }, { account_id: '6', amount: '0' }], ctx).error,
        'Other deposits, line 2: enter the amount that came in (more than $0.00), or remove the line. Nothing was saved.');
});

test('the total names the payments, the other lines and the whole deposit', () => {
    const { page } = load();
    assert.equal(page._totalText(40000, 2, 16550), 'Selected: $400.00 (2 items) · Other deposits: $165.50 · Deposit total: $565.50');
    assert.equal(page._totalText(0, 0, 0), 'Selected: $0.00 (0 items) · Deposit total: $0.00');
    assert.equal(page._otherCents([{ amount: '45.50' }, { amount: '' }, { amount: '-3' }]), 4550);
});

test('Make Deposits offers the other lines and Add line even with no payment waiting', async () => {
    const { page } = load();
    const html = await page.render();
    assert.match(html, /No payments waiting to be deposited/);
    assert.match(html, /<div id="dep-other" data-write>/);
    assert.match(html, />Other deposits</);
    assert.match(html, /data-write onclick="DepositsPage.addOtherLine\('dep-other-rows'\)"[^>]*>Add line</);
    assert.match(html, /DepositsPage.makeDeposit\(\)">Make Deposit</);
    // one blank row to start, its account picker without the refused ones
    const row = html.slice(html.indexOf('class="dep-other-row"'));
    const picker = row.slice(row.indexOf('dep-other-account'), row.indexOf('</select>'));
    assert.match(picker, /Other Income/);
    assert.doesNotMatch(picker, /Checking|Undeposited|Receivable|Payable|Visa|Labor Income/);
    // the names list: customers and vendors; a class column; no job column outside construction
    assert.match(html, /<datalist id="dep-other-rows-names"><option value="Acme Diner"><\/option><option value="Flour Mill Co">/);
    assert.match(html, /dep-other-class/);
    assert.doesNotMatch(html, /dep-other-job/);
});

test('a construction company gets a job on each line', async () => {
    const { page } = load({ construction: true });
    const html = await page.render();
    assert.match(html, /<select class="dep-other-job" aria-label="Line 1 job"><option value=""><\/option><option value="31">Acme Diner: Remodel</);
});

const DEPOSIT = {
    id: 9, date: '2026-09-12', reference: 'SLIP-9', account_name: 'Checking', amount: '565.50',
    voided: false, reconciled: false, payments: [], payments_total: 0, other_total: 165.5,
    other_lines: [
        { id: 1, account_id: 7, account_name: 'Insurance', received_from: 'Flour Mill Co', vendor_id: 21, memo: 'Policy refund', check_number: '88123', method: 'Check', amount: 45.5 },
        { id: 2, account_id: 8, account_name: 'Labor Income', received_from: 'State Energy Office', memo: '', check_number: '', method: '', amount: 120 },
    ],
};

test("the deposit's view lists its other lines", async () => {
    const { page, modal } = load({ routes: { '/deposits/9': DEPOSIT } });
    await page.view(9);
    const html = modal();
    assert.match(html, />Other deposits</);
    assert.match(html, /Flour Mill Co/);
    assert.match(html, /Insurance <span[^>]*>Policy refund/);
    assert.match(html, /Check 88123/);
    assert.doesNotMatch(html, /does not list its payments/);
});

test('the edit fills the lines in, keeping an account no longer offered', async () => {
    const { page, modal } = load({ routes: { '/deposits/9': DEPOSIT, '/deposits/pending': [] } });
    page._ctx = null;
    await page.editDeposit(9);
    const html = modal();
    assert.match(html, /<tbody id="dep-edit-other-rows">/);
    assert.match(html, /value="Flour Mill Co"/);
    assert.match(html, /<option value="7" selected>6300 · Insurance</);
    // the inactive account it already has stays its choice
    assert.match(html, /<option value="8" selected>Labor Income</);
    assert.match(html, /value="45.50"/);
    assert.match(html, /DepositsPage.addOtherLine\('dep-edit-other-rows'\)/);
});

test("a reconciled deposit's lines are shown locked, with no Add line", async () => {
    const { page, modal } = load({ routes: { '/deposits/9': { ...DEPOSIT, reconciled: true }, '/deposits/pending': [] } });
    await page.editDeposit(9);
    const html = modal();
    assert.match(html, /class="dep-other-amount money"[^>]*disabled/);
    assert.doesNotMatch(html, /addOtherLine/);
    assert.doesNotMatch(html, /removeOtherLine/);
});

test('Add line and Remove are writes the read-only lock hides', () => {
    const app = fs.readFileSync(path.join(JS, 'app.js'), 'utf8');
    assert.match(app, /'DepositsPage.addOtherLine', 'DepositsPage.removeOtherLine'/);
});
