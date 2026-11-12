// #709: Deposit To names Undeposited Funds. A blank Deposit To posts the
// money to Undeposited Funds (1200), so on Receive Payment, Enter Sales
// Receipt and Batch Payments the first option, the default, says so when
// the company has that account; the bank accounts follow it. Each page is
// loaded alone, as the browser's other probes load them.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const UF = { id: 4, account_number: '1200', name: 'Undeposited Funds', account_type: 'asset', is_control: true, bank_kind: null };
const CHECKING = { id: 1, account_number: '1000', name: 'Checking', account_type: 'asset', is_control: true, bank_kind: 'bank' };
const SAVINGS = { id: 2, account_number: '1010', name: 'Savings', account_type: 'asset', is_control: false, bank_kind: 'bank' };

function load(file, name, accounts) {
    let modal = '';
    const context = {
        console, window: {}, location: { hash: '#/' },
        document: { addEventListener() {}, getElementById: () => null, querySelector: () => null },
        escapeHtml: s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
        customerLabel: c => (c && c.name) || '',
        formatCurrency: n => '$' + (Number(n) || 0).toFixed(2),
        formatDate: s => s || '',
        todayISO: () => '2026-11-04',
        T: s => s,
        Terms: { text: s => s, isNonprofit: () => false, isConstruction: () => false },
        App: { settings: { home_currency: 'USD' }, navigate() {} },
        toast() {}, closeModal() {}, confirm: () => true,
        openModal: (_title, html) => { modal = html; },
        classFormGroupHtml: async () => '',
        jobFormGroupHtml: async () => '',
        currencyFormGroupsHtml: () => '',
        PaymentClass: { load: async () => {}, groupHtml: () => '' },
        ScanHelper: { scanRowHtml: () => '', wire() {} },
        $: () => null,
        $$: () => [],
        API: {
            get: async url => {
                if (url.startsWith('/accounts')) return JSON.parse(JSON.stringify(accounts));
                if (url === '/settings') return {};
                return [];
            },
        },
    };
    context.fetchAllPages = url => context.API.get(url);
    vm.createContext(context);
    // the files a new document holds before its first save (#881): not this test's
    context.PendingFiles = { reset() {}, stripHtml: () => '', ids: () => [], saved() {}, render() {}, discard() {} };
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js', file), 'utf8') + `\nthis.${name} = ${name};`, context);
    return { page: context[name], modal: () => modal };
}

// The Deposit To select's markup, and its options in order: [value, text].
function depositSelect(html) {
    const at = html.indexOf('name="deposit_to_account_id"');
    assert.ok(at >= 0, 'no Deposit To select');
    return html.slice(at, html.indexOf('</select>', at));
}
function depositOptions(html) {
    const select = depositSelect(html);
    return [...select.matchAll(/<option value="([^"]*)"[^>]*>([^<]*)<\/option>/g)].map(m => [m[1], m[2]]);
}

const forms = [
    ['payments.js', 'PaymentsPage', async p => { await p.page.showForm(); return p.modal(); }],
    ['sales_receipts.js', 'SalesReceiptsPage', async p => {
        p.page.lineRowHtml = () => '';
        p.page.recalc = () => {};
        await p.page.showForm();
        return p.modal();
    }],
    ['batch_payments.js', 'BatchPaymentsPage', async p => p.page.render()],
];

for (const [file, name, open] of forms) {
    test(`${file}: the blank Deposit To is Undeposited Funds, first and the default`, async () => {
        const html = await open(load(file, name, [CHECKING, UF, SAVINGS]));
        const options = depositOptions(html);
        assert.deepEqual(options[0], ['', 'Undeposited Funds (deposit later)']);
        // nothing is marked selected, so the browser opens on the first option
        assert.ok(!/selected/.test(depositSelect(html)));
        const rest = options.slice(1);
        assert.ok(rest.some(([v, t]) => v === '1' && t === 'Checking'));
        assert.ok(rest.some(([v, t]) => v === '2' && t === 'Savings'));
        // the account itself is not listed a second time
        assert.ok(!rest.some(([v]) => v === '4'));
    });

    test(`${file}: without the Undeposited Funds account the blank option is not named for it`, async () => {
        const html = await open(load(file, name, [CHECKING, SAVINGS]));
        const options = depositOptions(html);
        assert.deepEqual(options[0], ['', '--']);
    });
}
