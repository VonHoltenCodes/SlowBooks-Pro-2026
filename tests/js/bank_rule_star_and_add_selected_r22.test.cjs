// Round 22's bank feed findings, in the page's own code:
// - NEW-259 / W-193: a rule's pattern read as the server reads it (`*` is
//   anything, `\*` a star itself), the payee of Rule… escaped, a pattern
//   of only * or spaces refused in the server's sentence, and the pattern
//   said in words on the list.
// - W-190: Add selected names each line it did not add, with its reason, as
//   a warning, and leaves those lines ticked.
// - W-191: a To review memo's count near its limit.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = (name) => fs.readFileSync(path.join(__dirname, '../../app/static/js', name), 'utf8');

function load({ addAll } = {}) {
    const calls = { toast: [], post: [], back: [], ticked: [] };
    const boxes = {};
    const context = {
        window: {},
        document: {
            getElementById: () => null,
            querySelectorAll: (sel) => (sel === 'input.review-tick:checked'
                ? Object.values(boxes).filter(b => b.checked) : Object.values(boxes)),
            querySelector: (sel) => {
                const m = sel.match(/data-review-tick="(\d+)"/);
                return m ? boxes[m[1]] || null : null;
            },
        },
        console,
        setTimeout: () => 0,
        escapeHtml: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
        formatCurrency: (n) => '$' + Number(n).toFixed(2),
        formatDate: (d) => `D(${d})`,
        T: (s) => s,
        AccountQuickAdd: { picker: (html) => html, register: () => {} },
        toast: (msg, kind) => { calls.toast.push([msg, kind]); },
        API: { post: async (url, body) => { calls.post.push([url, body]); return addAll; } },
    };
    vm.createContext(context);
    vm.runInContext(JS('hint.js'), context);
    vm.runInContext(JS('bank_rules.js') + '\nthis.BankRulesPage = BankRulesPage;', context);
    vm.runInContext(JS('banking.js') + '\nthis.BankingPage = BankingPage;', context);
    return { BR: context.BankRulesPage, BP: context.BankingPage, calls, boxes };
}

test('a pattern reads as the server reads it', () => {
    const { BR } = load();
    const parts = (p) => JSON.parse(JSON.stringify(BR.patternParts(p)));
    assert.deepEqual(parts('T-MOBILE*'), ['T-MOBILE', null]);
    assert.deepEqual(parts('SQ \\*'), ['SQ *']);
    assert.deepEqual(parts('*PCS*SVC*'), [null, 'PCS', null, 'SVC', null]);
    assert.deepEqual(parts('a\\\\\\*b'), ['a\\*b']);
    assert.deepEqual(parts('C:\\TEMP'), ['C:\\TEMP']);
    for (const text of ['SQ *JOES', 'a\\*b', 'C:\\TEMP', '*', 'plain']) {
        assert.deepEqual(parts(BR.escapePattern(text)), [text]);
    }
});

test('a pattern of only stars or spaces is refused in the server sentence', () => {
    const { BR } = load();
    for (const p of ['*', '   ', '* *', '**']) assert.equal(BR.patternProblem(p), BR.PATTERN_EMPTY);
    assert.equal(BR.patternProblem('\\*'), null);
    assert.equal(BR.patternProblem('SQ \\*'), null);
    assert.match(BR.PATTERN_EMPTY, /only \* or spaces would match nothing/);
});

test('the list says a starred pattern in words, and a plain one not at all', () => {
    const { BR } = load();
    assert.equal(BR.readingText('T-MOBILE*'), 'Reads: “T-MOBILE”, then anything');
    assert.equal(BR.readingText('SQ \\*'), 'Reads: “SQ *”');
    assert.equal(BR.readingText('AMAZON'), '');
    assert.match(BR.patternReading('SQ \\*'), /class="rule-pattern-reads"/);
    assert.equal(BR.patternReading('AMAZON'), '');
});

test('the form explains the star behind the tortoise', async () => {
    const { BR } = load();
    const src = JS('bank_rules.js');
    const form = src.slice(src.indexOf('id="bank-rule-pattern"') - 2000, src.indexOf('id="bank-rule-pattern"'));
    assert.match(form, /hint\(`/);
    assert.match(form, /A <code>\*<\/code> stands for anything/);
    assert.match(form, /typed <code>\\\\\*<\/code>/);
    assert.ok(BR.PATTERN_EMPTY);
});

test('Add selected names each line not added, as a warning, and keeps it ticked', async () => {
    const lines = {
        11: { id: 11, date: '2026-10-03', payee: 'BETA HOMES CHECK', amount: '-120.00' },
        12: { id: 12, date: '2026-10-04', payee: 'FUEL STOP 9', amount: '-40.00' },
        13: { id: 13, date: '2026-10-05', payee: 'CAFE', amount: '-9.50' },
    };
    const { BP, calls, boxes } = load({
        addAll: {
            added: 1,
            skipped: [
                { id: 11, reason: 'Pick a category (account) to add this line', date: '2026-10-03', payee: 'BETA HOMES CHECK', amount: '-120.00' },
                { id: 13, reason: 'That line is excluded; restore it to add it', date: '2026-10-05', payee: 'CAFE', amount: '-9.50' },
            ],
            without_vendor: [],
        },
    });
    for (const id of [11, 12, 13]) boxes[id] = { checked: true, dataset: { reviewTick: String(id) } };
    BP._reviewLines = lines;
    BP._tickedIds = () => [11, 12, 13];
    BP.applyToTicked = async () => 0;
    BP._reviewPicks = () => ({});
    BP._outsideFilterAll = () => '';
    BP._offerFeedVendors = () => {};
    BP.tickChanged = () => { calls.ticked.push(Object.values(boxes).filter(b => b.checked).length); };
    BP._backToRegister = async (id) => {
        calls.back.push(id);
        for (const b of Object.values(boxes)) b.checked = false;  // the list is drawn anew
    };
    await BP.addSelected(4, 9);
    const [msg, kind] = calls.toast.at(-1);
    assert.equal(kind, 'warning');
    assert.equal(msg,
        'Added 1 of 3 ticked. Not added (2): D(2026-10-03), BETA HOMES CHECK, $-120.00: Pick a category (account) to add this line; '
        + 'D(2026-10-05), CAFE, $-9.50: That line is excluded; restore it to add it.');
    assert.deepEqual(calls.back, [9]);
    assert.equal(boxes[11].checked, true);
    assert.equal(boxes[13].checked, true);
    assert.equal(boxes[12].checked, false);
    assert.deepEqual(calls.ticked, [2]);
});

test('every line added is a success; none added is an error', async () => {
    for (const [r, kind] of [
        [{ added: 2, skipped: [], without_vendor: [] }, undefined],
        [{ added: 0, skipped: [{ id: 11, reason: 'Pick a category (account) to add this line' }], without_vendor: [] }, 'error'],
    ]) {
        const { BP, calls } = load({ addAll: r });
        BP._reviewLines = { 11: { id: 11, date: '2026-10-03', payee: 'BETA', amount: '-1.00' } };
        BP._tickedIds = () => [11, 12];
        BP.applyToTicked = async () => 0;
        BP._reviewPicks = () => ({});
        BP._outsideFilterAll = () => '';
        BP._offerFeedVendors = () => {};
        BP._backToRegister = async () => {};
        await BP.addSelected(4, 9);
        assert.equal(calls.toast.at(-1)[1], kind);
        if (kind === 'error') assert.match(calls.toast.at(-1)[0], /Not added \(1\): D\(2026-10-03\), BETA, \$-1\.00: Pick a category/);
    }
});

test('a To review memo counts once it nears its limit', () => {
    const { BP } = load();
    assert.equal(BP.MEMO_MAX, 500);
    assert.equal(BP.memoCountText(10), '');
    assert.equal(BP.memoCountText(450), '450 of 500 characters');
    assert.equal(BP.memoCountText(500), '500 of 500 characters');
});
