// Starting a reconciliation while one is already open (409): the handler
// offers the date and balance just typed to the open session with a PUT,
// instead of dropping them and reopening the old figures (#497). On confirm
// it PUTs the typed values; on cancel it opens the existing session as it is.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load({ answer }) {
    const puts = [];
    let opened = null;
    const context = {
        window: {}, document: {}, console,
        confirm: () => answer,
        closeModal() {},
        toast() {},
        formatDate: d => String(d),
        formatCurrency: n => '$' + Number(n).toFixed(2),
        API: {
            post: async () => {
                const err = new Error('A reconciliation is already in progress for this account');
                err.status = 409;
                err.detail = { message: err.message, existing_id: 7 };
                throw err;
            },
            get: async url => {
                assert.equal(url, '/banking/reconciliations/7/transactions');
                return { statement_date: '2026-03-31', statement_balance: 100.0, account_id: 3 };
            },
            put: async (url, body) => { puts.push([url, body]); return {}; },
        },
    };
    vm.createContext(context);
    const js = fs.readFileSync(path.join(__dirname, '../../app/static/js/banking.js'), 'utf8');
    vm.runInContext(js + '\nthis.BankingPage = BankingPage;', context);
    const BP = context.BankingPage;
    BP._ctx = { accountId: 3, accountName: 'Checking' };
    BP.showReconcileView = id => { opened = id; };
    return { BP, puts, get opened() { return opened; } };
}

function event() {
    return {
        preventDefault() {},
        target: { statement_date: { value: '2026-04-30' }, statement_balance: { value: '250.00' } },
    };
}

test('on confirm the 409 handler PUTs the typed date and balance, then opens the session', async () => {
    const f = load({ answer: true });
    await f.BP.createReconciliation(event(), 3);
    assert.equal(f.puts.length, 1);
    assert.equal(f.puts[0][0], '/banking/reconciliations/7');
    assert.equal(f.puts[0][1].statement_date, '2026-04-30');
    assert.equal(f.puts[0][1].statement_balance, '250.00');
    assert.equal(f.opened, 7);
});

test('on cancel the open session is opened as it is, with no PUT', async () => {
    const f = load({ answer: false });
    await f.BP.createReconciliation(event(), 3);
    assert.equal(f.puts.length, 0);
    assert.equal(f.opened, 7);
});
