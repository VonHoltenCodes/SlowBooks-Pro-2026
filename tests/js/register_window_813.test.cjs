// A register's Import file on an account with no bank feed asks to make one
// (NEW-189); after Create it went to Banking, so the import asked for was
// lost. It goes back to the register and opens the import (#813). A New
// Bank Account opened from Banking itself still lands on Banking. And a
// voided entry's kept files are reached from its row.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load() {
    const seen = { navigated: [], modals: [], posts: [] };
    let feeds = [];
    const ctx = {
        console, setTimeout, clearTimeout, Promise, URLSearchParams,
        window: {}, location: { hash: '#/banking/7' },
        document: { addEventListener() {}, querySelector: () => null, querySelectorAll: () => [], getElementById: () => null },
        $: () => null, $$: () => [],
        escapeHtml: s => String(s ?? ''), accountLabel: a => a.name, todayISO: () => '2026-11-11',
        formatCurrency: n => String(n), formatDate: s => s,
        T: s => s, Terms: { text: s => s, isNonprofit: () => false, isConstruction: () => false },
        toast() {}, closeModal() {}, confirm: () => true,
        openModal: (title) => seen.modals.push(title),
        App: { navigate: async (h) => { seen.navigated.push(h); }, settings: {} },
        API: {
            get: async (url) => (url.startsWith('/banking/accounts') ? feeds
                : url.startsWith('/accounts') ? [{ id: 7, name: 'Savings', bank_kind: 'bank' }] : {}),
            post: async (url, body) => { seen.posts.push(url); if (url === '/banking/accounts') feeds = [{ id: 3, account_id: body.account_id }]; return { id: 3 }; },
        },
    };
    vm.createContext(ctx);
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/banking.js'), 'utf8');
    vm.runInContext(src + '\nthis.BankingPage = BankingPage;', ctx);
    return { B: ctx.BankingPage, seen };
}

const form = (accountId) => ({
    account_id: { value: String(accountId) }, name: { value: 'Savings feed' }, bank_name: { value: '' },
    last_four: { value: '' }, opening_balance: { value: '0', dataset: {} }, opening_date: { value: '2026-11-11' },
});

test('Create after Import file goes back to the register and its import', async () => {
    const { B, seen } = load();
    await B.showOFXImport(7);
    await new Promise(r => setTimeout(r, 5));  // the dialog opens on the feeds' answer
    assert.deepEqual(seen.modals, ['New Bank Account']);
    await B.saveAccount({ preventDefault() {}, target: form(7) });
    assert.deepEqual(seen.navigated, ['#/banking/7']);
    assert.deepEqual(seen.modals, ['New Bank Account', 'Import Bank File']);
});

test('a New Bank Account from Banking still lands on Banking', async () => {
    const { B, seen } = load();
    await B.showAccountForm();
    await B.saveAccount({ preventDefault() {}, target: form(7) });
    assert.deepEqual(seen.navigated, ['#/banking']);
    assert.deepEqual(seen.modals, ['New Bank Account']);
});

// A voided bank entry has no Edit, and Edit was the way to its attachments
// (#600): its row has Files, which lists them to open, without Delete
// (2.31.0 gate, #813).
test('a voided bank entry has Files, which lists its kept attachments', async () => {
    const { B, seen } = load();
    const live = { voided: false, source_type: 'bank_entry', transaction_id: 9 };
    const voided = { ...live, voided: true };
    const reversal = { voided: true, source_type: 'bank_entry_void', transaction_id: 10 };
    assert.equal(B._voidedFilesButton(live), '');
    assert.equal(B._voidedFilesButton(reversal), '');
    assert.match(B._voidedFilesButton(voided), /onclick="BankingPage\.showVoidedFiles\(9\)"[^>]*>Files<\/button>/);
    assert.match(B._voidedFilesButton(voided), /data-readonly-ok/);
});
