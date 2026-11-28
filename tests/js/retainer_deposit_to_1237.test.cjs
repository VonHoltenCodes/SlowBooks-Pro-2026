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
const { execFileSync } = require('node:child_process');

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

function loadPayments({ accounts = ALL, settings = {}, source = null } = {}) {
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
    vm.runInContext((source || fs.readFileSync(path.join(JS, 'payments.js'), 'utf8')) + '\nthis.PaymentsPage = PaymentsPage;', context);
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

// --- Customer deposits and retainers (Settings → Deposits) ----------------

const LISTED = { customer_deposit_account_ids: '[7]' };

test('DepositAccounts.customerDeposits: none listed is none', () => {
    const DA = depositAccounts({ settings: {} });
    for (const s of [{}, { customer_deposit_account_ids: '' }, { customer_deposit_account_ids: '[]' }, { customer_deposit_account_ids: 'nonsense' }]) {
        assert.equal(DA.customerDeposits(ALL, s).length, 0);
    }
});

test('DepositAccounts.customerDeposits: only listed, active liabilities that are no bank or card', () => {
    const DA = depositAccounts({ settings: {} });
    const s = { customer_deposit_account_ids: '[7, 8, 1, 5, 4]' };
    assert.deepEqual(DA.customerDeposits(ALL, s).map(a => a.id), [7, 8]);
    const closed = { ...DEPOSITS, is_active: false };
    assert.deepEqual(DA.customerDeposits([RETAINER, closed], s).map(a => a.id), [7]);
    // an edit keeps its own account, even deactivated since
    assert.deepEqual(DA.customerDeposits([RETAINER, closed], s, 8).map(a => a.id), [7, 8]);
    // read from App.settings when none is passed
    const fromApp = depositAccounts({ settings: LISTED });
    assert.deepEqual(fromApp.customerDeposits(ALL).map(a => a.id), [7]);
});

test('DepositAccounts.customerDeposits: never a control or payroll account, even listed', () => {
    const DA = depositAccounts({ settings: {} });
    const ap = { id: 9, account_number: '2000', name: 'Accounts Payable', account_type: 'liability', is_control: true, bank_kind: null, is_active: true };
    const payroll = { id: 12, account_number: '2300', name: 'Payroll Liabilities', account_type: 'liability', is_control: false, bank_kind: null, is_active: true };
    const held = { id: 13, account_number: '2296', name: 'Withholding Held', account_type: 'liability', is_control: false, bank_kind: null, is_active: true };
    const accounts = [...ALL, ap, payroll, held];
    const s = { customer_deposit_account_ids: '[7, 9, 12, 13]' };
    assert.deepEqual(DA.customerDeposits(accounts, s).map(a => a.id), [7, 13]);
    // an account a payroll setting names is a payroll account too
    const named = { ...s, payroll_state_withholding_account_id: '13' };
    assert.deepEqual(DA.customerDeposits(accounts, named).map(a => a.id), [7]);
    assert.deepEqual([...DA.payrollIds(accounts, named)].sort(), ['12', '13']);
});

// payments.js as it was before #1237: the merge base with origin/main.
// Null when git or the base is not there (a shallow CI checkout), and once
// this is merged the base is this code itself; it pins the change in review.
function basePayments() {
    const root = path.join(__dirname, '../..');
    try {
        const base = execFileSync('git', ['merge-base', 'HEAD', 'origin/main'], { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
        return execFileSync('git', ['show', `${base}:app/static/js/payments.js`], { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], maxBuffer: 1 << 26 });
    } catch (err) {
        return null;
    }
}
const squeeze = html => html.replace(/>\s+</g, '><').replace(/\s+/g, ' ').trim();

test('an empty list leaves Record Payment exactly as it is today', async () => {
    const html = async (settings, keep = null, source = null) => {
        const p = loadPayments({ settings, source });
        await p.page.showForm(null, null, keep);
        return p.modal();
    };
    const today = await html({});
    assert.equal(await html({ customer_deposit_account_ids: '[]' }), today);
    assert.ok(!today.includes('optgroup'));
});

test('an empty list renders the form the code before #1237 rendered', async (t) => {
    const before = basePayments();
    if (!before) { t.skip('no git merge base with origin/main here'); return; }
    const html = async (keep, source) => {
        const p = loadPayments({ settings: {}, source });
        await p.page.showForm(null, null, keep);
        return squeeze(p.modal());
    };
    // a new payment, and Edit of a bank, Undeposited Funds or blank payment
    for (const keep of [null, SAVINGS.id, UF.id]) {
        assert.equal(await html(keep, null), await html(keep, before), String(keep));
    }
});

test('a listed account is in its own labelled group, worded as no bank', async () => {
    const p = loadPayments({ settings: LISTED });
    await p.page.showForm();
    const html = p.modal();
    assert.deepEqual(depositTo(html).map(o => o[0]), ['', '1', '2', '7']);
    const at = html.indexOf('<optgroup');
    assert.ok(at > html.indexOf('>Savings<'), 'after the bank accounts');
    assert.match(html, /<optgroup label="Apply a customer deposit or retainer \(no money moves\)"><option value="7" data-retainer>Retainer Payable \(apply a deposit or retainer\)<\/option><\/optgroup>/);
    // Edit of a payment on it: once, in the group
    const q = loadPayments({ settings: LISTED });
    await q.page.showForm(null, null, 7, 'Retainer');
    assert.deepEqual(depositTo(q.modal()).map(o => o[0]), ['', '1', '2', '7']);
});

test('only Record Payment offers them: Sales Receipts and Make Deposits do not', async () => {
    for (const [file, name, open, marker] of [
        ['sales_receipts.js', 'SalesReceiptsPage', async page => { page.lineRowHtml = () => ''; page.recalc = () => {}; await page.showForm(); }, 'name="deposit_to_account_id"'],
        ['deposits.js', 'DepositsPage', async page => page.render(), 'id="deposit-bank-acct"'],
    ]) {
        const html = async settings => {
            const App = { settings, navigate() {} };
            let modal = '';
            const ctx = {
                console, window: {}, location: { hash: '#/' },
                document: { addEventListener() {}, getElementById: () => null, querySelector: () => null },
                escapeHtml: s => String(s ?? ''), customerLabel: c => (c && c.name) || '',
                formatCurrency: n => String(n), formatDate: s => s || '', todayISO: () => '2026-11-27',
                T: s => s, Terms: { text: s => s, isNonprofit: () => false, isConstruction: () => false }, App,
                ClassWarn: { enabled: () => false, okValue: () => true }, toast() {}, closeModal() {}, confirm: () => true,
                openModal: (_t, h) => { modal = h; }, classFormGroupHtml: async () => '', jobFormGroupHtml: async () => '',
                currencyFormGroupsHtml: () => '', TaxExempt: { enforce() {}, switchHtml: () => '', watchJob() {}, value: () => undefined },
                PaymentClass: { load: async () => {}, groupHtml: () => '' }, ScanHelper: { scanRowHtml: () => '', wire() {} },
                DepositAccounts: depositAccounts(App), $: () => null, $$: () => [],
                PendingFiles: { reset() {}, stripHtml: () => '', ids: () => [], saved() {}, render() {}, discard() {} },
                API: { get: async url => (url.startsWith('/accounts') ? JSON.parse(JSON.stringify(ALL)) : (url === '/settings' ? settings : [])) },
            };
            ctx.fetchAllPages = url => ctx.API.get(url);
            vm.createContext(ctx);
            vm.runInContext(fs.readFileSync(path.join(JS, 'hint.js'), 'utf8'), ctx);
            vm.runInContext(fs.readFileSync(path.join(JS, file), 'utf8') + `\nthis.page = ${name};`, ctx);
            const out = await open(ctx.page);
            const all = (typeof out === 'string' ? out : '') + modal;
            const sel = all.slice(all.indexOf(marker), all.indexOf('</select>', all.indexOf(marker)));
            assert.ok(all.indexOf(marker) >= 0, `${file}: no ${marker}`);
            return sel;
        };
        const listed = await html(LISTED);
        assert.ok(!listed.includes('Retainer Payable'), file);
        assert.equal(listed, await html({}), file);
    }
});

// Save, with the form's pieces faked: the payment's numbers, the account
// chosen, and what is sent
function saving({ settings = LISTED, depositTo, remaining = 0, amount = '300.00', answer = true, giveBack = null } = {}) {
    const p = loadPayments({ settings });
    const sent = [];
    const toasts = [];
    const asked = [];
    Object.assign(p.context, {
        toast: (m, kind) => toasts.push([m, kind]),
        confirm: m => { asked.push(m); return answer; },
        TxnWarn: { ok: async () => true }, ClassWarn: { ok: async () => true },
        PaymentTerms: { asksOverride: () => false, rememberDiscountAccount() {} },
        Replace: { suffix: () => '', replacing: () => false },
        toastPosted() {}, classIdFromForm: () => null,
    });
    p.context.App.refresh = () => {};
    p.context.API.post = async (url, data) => { sent.push([url, data]); return { id: 1 }; };
    const form = {
        customer_id: { value: '3' }, date: { value: '2026-10-20' }, amount: { value: amount },
        method: { value: 'Retainer' }, check_number: { value: '' }, reference: { value: '' },
        deposit_to_account_id: { value: depositTo }, notes: { value: '' },
    };
    return {
        async run() {
            await p.page.showForm();
            p.page._remainingCents = () => ({ total: 0, allocated: 0, remaining });
            p.page._giveBackDeposit = giveBack;
            await p.page.save({ preventDefault() {}, target: form });
            return { sent, toasts, asked };
        },
    };
}

test('Save: a retainer payment with money left over is refused, nothing sent', async () => {
    const { sent, toasts } = await saving({ depositTo: '7', remaining: 5000 }).run();
    assert.equal(sent.length, 0);
    assert.match(toasts[0][0], /Retainer Payable, a customer deposit or retainer account, so the whole payment must be applied/);
    assert.equal(toasts[0][1], 'error');
});

test('Save: within what the account holds is sent as today, no override', async () => {
    const { sent, asked } = await saving({ depositTo: '7', amount: '300.00' }).run();
    assert.equal(asked.length, 0);
    assert.equal(sent.length, 1);
    assert.equal(sent[0][1].deposit_to_account_id, 7);
    assert.ok(!('allow_overdraw' in sent[0][1]));
});

test('Save: more than the account holds asks first; No sends nothing, Yes sends the override', async () => {
    const no = await saving({ depositTo: '7', amount: '600.00', answer: false }).run();
    assert.equal(no.sent.length, 0);
    assert.match(no.asked[0], /Retainer Payable holds \$500\.00, less than the \$600\.00 applied from it/);
    const yes = await saving({ depositTo: '7', amount: '600.00', answer: true }).run();
    assert.equal(yes.sent.length, 1);
    assert.equal(yes.sent[0][1].allow_overdraw, true);
});

test('Save: an edit keeping its own retainer account saves as it was', async () => {
    // saved before the account was ticked: money left over, more than it holds
    // (money left over then takes the keep-as-credit box, as any payment's)
    const left = await saving({ depositTo: '7', remaining: 10000, giveBack: { id: '7' } }).run();
    assert.ok(!left.toasts.some(x => /customer deposit or retainer/.test(x[0])), JSON.stringify(left.toasts));
    assert.match(left.toasts[0][0], /tick the box to keep it as a credit/);
    const r = await saving({ depositTo: '7', amount: '600.00', giveBack: { id: '7' } }).run();
    assert.equal(r.asked.length, 0);
    assert.equal(r.sent.length, 1);
    assert.ok(!('allow_overdraw' in r.sent[0][1]));
});

test('Save: an edit moving a payment onto a retainer account is checked as new', async () => {
    const left = await saving({ depositTo: '7', remaining: 5000, giveBack: { id: '1' } }).run();
    assert.equal(left.sent.length, 0);
    assert.match(left.toasts[0][0], /must be applied to invoices/);
    const over = await saving({ depositTo: '7', amount: '600.00', answer: false, giveBack: { id: '1' } }).run();
    assert.equal(over.sent.length, 0);
    assert.equal(over.asked.length, 1);
});

test('Save: a bank account is never asked about, leftover or not', async () => {
    const { sent, asked } = await saving({ depositTo: '1', amount: '9000.00' }).run();
    assert.equal(asked.length, 0);
    assert.equal(sent.length, 1);
    assert.ok(!('allow_overdraw' in sent[0][1]));
});

// Settings → Connections → Deposits
function settingsPage() {
    const context = {
        API: { get: async () => ({}) }, setTimeout() {}, T: v => v,
        Terms: { text: v => v, isNonprofit: () => false, isConstruction: () => false },
        escapeHtml: v => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
        accountWords: a => `${a.account_number} - ${a.name}`,
        toast() {}, document: { getElementById: () => null },
        PendingFiles: { reset() {}, stripHtml: () => '', ids: () => [], saved() {}, render() {}, discard() {} },
        DepositAccounts: depositAccounts({ settings: {} }),
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'payment_terms.js'), 'utf8'), context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'settings.js'), 'utf8') + '\nthis.page = SettingsPage;', context);
    return context.page;
}
const AP = { id: 9, account_number: '2000', name: 'Accounts Payable', account_type: 'liability', is_control: true, bank_kind: null, is_active: true };
const INCOME = { id: 10, account_number: '4000', name: 'Sales', account_type: 'income', is_control: true, bank_kind: null, is_active: true };
const OLD = { id: 11, account_number: '2295', name: 'Old Retainers', account_type: 'liability', is_control: false, bank_kind: null, is_active: false };

test('Settings: a box per active plain liability, none ticked by default', () => {
    const html = settingsPage()._customerDepositAccountsHtml({}, [...ALL, AP, INCOME, OLD]);
    assert.match(html, /<legend[^>]*>Customer deposits and retainers<\/legend>/);
    assert.match(html, /aria-describedby="settings-customer-deposit-accounts-help"/);
    const boxes = [...html.matchAll(/data-customer-deposit-account="(\d+)"( checked)?/g)].map(m => [m[1], !!m[2]]);
    assert.deepEqual(boxes, [['7', false], ['8', false]]);
});

test('Settings: a saved account that no longer qualifies stays ticked and named', () => {
    const html = settingsPage()._customerDepositAccountsHtml({ customer_deposit_account_ids: '[7,11]' }, [...ALL, OLD]);
    const boxes = [...html.matchAll(/data-customer-deposit-account="(\d+)"( checked)?/g)].map(m => [m[1], !!m[2]]);
    assert.deepEqual(boxes, [['7', true], ['8', false], ['11', true]]);
    assert.ok(html.includes('(unavailable) 2295 - Old Retainers'));
    const page = settingsPage();
    const box = (id, checked) => ({ checked, getAttribute: () => id });
    assert.deepEqual([...page._customerDepositAccountsToSave([box('7', true), box('8', false), box('11', true)])], [7, 11]);
});

test('Settings: no box for a payroll account, seeded or named by a payroll setting', () => {
    const payroll = { id: 12, account_number: '2300', name: 'Payroll Liabilities', account_type: 'liability', is_control: false, bank_kind: null, is_active: true };
    const ss = { id: 14, account_number: '2330', name: 'Social Security Payable', account_type: 'liability', is_control: false, bank_kind: null, is_active: true };
    const held = { id: 13, account_number: '2296', name: 'Withholding Held', account_type: 'liability', is_control: false, bank_kind: null, is_active: true };
    const accounts = [...ALL, payroll, ss, held];
    const boxes = s => [...settingsPage()._customerDepositAccountsHtml(s, accounts).matchAll(/data-customer-deposit-account="(\d+)"/g)].map(m => m[1]);
    assert.deepEqual(boxes({}), ['7', '8', '13']);
    assert.deepEqual(boxes({ payroll_futa_account_id: '13' }), ['7', '8']);
});
