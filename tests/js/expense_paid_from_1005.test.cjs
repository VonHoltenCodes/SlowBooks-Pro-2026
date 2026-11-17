// Enter Expense's Paid From rules (#1005), run against the page's own functions.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const src = fs.readFileSync(path.join(__dirname, '..', '..', 'app', 'static', 'js', 'expenses.js'), 'utf8');
const pick = (name) => {
    const start = src.indexOf(name + '(');
    const open = src.indexOf('{', start);
    let depth = 0, i = open;
    for (; i < src.length; i++) { if (src[i] === '{') depth++; else if (src[i] === '}') { depth--; if (!depth) break; } }
    return 'function ' + src.slice(start, i + 1);
};
const ctx = { DepositAccounts: { startId: (banks, s) => { const d = String((s || {}).default_deposit_account_id || ''); return banks.some(a => String(a.id) === d && a.is_active !== false) ? d : ''; } } };
vm.createContext(ctx);
vm.runInContext(pick('paidFromAccounts') + ';' + pick('startPaidFrom') + '; this.paidFromAccounts = paidFromAccounts; this.startPaidFrom = startPaidFrom;', ctx);

const accts = [
    { id: 1, name: 'Old Checking', bank_kind: 'bank', is_active: false },
    { id: 2, name: 'Savings', bank_kind: 'bank', is_active: true },
    { id: 3, name: 'Main Checking', bank_kind: 'bank', is_active: true },
    { id: 4, name: 'Visa', bank_kind: 'credit_card', is_active: true },
    { id: 5, name: 'Supplies Expense', is_active: true },
];

test('inactive accounts stay off the list unless they are the edited expense\'s own', () => {
    assert.deepStrictEqual(ctx.paidFromAccounts(accts).map(a => a.id), [2, 3, 4]);
    assert.deepStrictEqual(ctx.paidFromAccounts(accts, 1).map(a => a.id), [1, 2, 3, 4]);
});

test('a new expense opens on the default deposit account, else the first active checking', () => {
    const list = ctx.paidFromAccounts(accts);
    assert.strictEqual(ctx.startPaidFrom(list, { default_deposit_account_id: 2 }).id, 2);
    assert.strictEqual(ctx.startPaidFrom(list, {}).id, 3);
    assert.strictEqual(ctx.startPaidFrom(list, { default_deposit_account_id: 1 }).id, 3);  // the default is inactive
    assert.strictEqual(ctx.startPaidFrom([accts[3]], {}).id, 4);  // nothing checking: the first on the list
});
