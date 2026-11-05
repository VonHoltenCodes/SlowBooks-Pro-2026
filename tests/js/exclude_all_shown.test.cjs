// Exclude all shown (#705): the review list's header has a named, keyboard
// reachable button that, after a confirm saying how many, excludes every
// line the list shows in one call. A month imported twice used to be one
// Exclude per line.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load({ confirmAnswer = true, elements = {} } = {}) {
    const calls = { confirm: [], post: [], toast: [], back: [] };
    const context = {
        window: {},
        document: { getElementById: (id) => elements[id] || null, querySelectorAll: () => [] },
        console,
        setTimeout: () => 0,
        escapeHtml: (s) => String(s),
        formatCurrency: (n) => '$' + Number(n).toFixed(2),
        formatDate: (d) => String(d),
        T: (s) => s,
        AccountQuickAdd: { picker: (html) => html, register: () => {} },
        confirm: (msg) => { calls.confirm.push(msg); return confirmAnswer; },
        toast: (msg, kind) => { calls.toast.push([msg, kind]); },
        API: {
            post: async (url, body) => {
                calls.post.push([url, body]);
                return body ? { excluded: body.line_ids.length } : {};
            },
        },
    };
    vm.createContext(context);
    const js = fs.readFileSync(
        path.join(__dirname, '../../app/static/js/banking.js'),
        'utf8'
    );
    vm.runInContext(js + '\nthis.BankingPage = BankingPage;', context);
    const BP = context.BankingPage;
    BP._backToRegister = (id) => { calls.back.push(id); };
    BP._reviewDimCells = () => '';
    BP._vendorDatalist = () => '';
    BP._lineClassShown = () => false;
    return { BP, calls };
}

const LINES = [
    { id: 11, date: '2026-10-01', amount: 5070, payee: 'DEPOSIT' },
    { id: 12, date: '2026-10-01', amount: -650, payee: 'CHECK 1356' },
    { id: 13, date: '2026-10-05', amount: -689.17, payee: 'HVSB LOAN PMT' },
];

test('the review list has a named Exclude all shown button', () => {
    const { BP } = load();
    const html = BP._reviewPanel(LINES, 4, 9, 'bank');
    const m = html.match(/<button([^>]*)>Exclude all shown<\/button>/);
    assert.ok(m, 'no Exclude all shown button');
    const attrs = m[1];
    // a real <button> is in the tab order and answers Enter and Space
    assert.match(attrs, /type="button"/);
    assert.doesNotMatch(attrs, /tabindex="-1"/);
    // the accessible name starts with the visible label (WCAG 2.5.3, W-120)
    assert.match(attrs, /aria-label="Exclude all shown: 3 statement lines"/);
    // and the hint says where the lines go
    assert.match(attrs, /title="[^"]*Show excluded[^"]*Restore[^"]*"/);
    assert.match(attrs, /BankingPage\.excludeAll\(4, 9\)/);
});

test('it confirms with the count, then excludes every line shown in one call', async () => {
    const { BP, calls } = load();
    BP._reviewPanel(LINES, 4, 9, 'bank');
    await BP.excludeAll(4, 9);
    assert.equal(calls.confirm.length, 1);
    assert.match(calls.confirm[0], /Exclude all 3 statement lines shown\?/);
    assert.equal(calls.post.length, 1);
    const [url, body] = calls.post[0];
    assert.equal(url, '/banking/accounts/4/feed/exclude-all');
    assert.deepEqual([...body.line_ids].sort(), [11, 12, 13]);
    assert.equal(calls.toast[0][0], 'Excluded 3 statement lines');
    assert.deepEqual(calls.back, [9]);
});

test('cancelling the confirm excludes nothing', async () => {
    const { BP, calls } = load({ confirmAnswer: false });
    BP._reviewPanel(LINES, 4, 9, 'bank');
    await BP.excludeAll(4, 9);
    assert.equal(calls.confirm.length, 1);
    assert.equal(calls.post.length, 0);
    assert.deepEqual(calls.back, []);
});

test('one line says line, not lines', async () => {
    const { BP, calls } = load();
    const html = BP._reviewPanel(LINES.slice(0, 1), 4, 9, 'bank');
    assert.match(html, /aria-label="Exclude all shown: 1 statement line"/);
    await BP.excludeAll(4, 9);
    assert.match(calls.confirm[0], /Exclude all 1 statement line shown\?/);
});


// Show excluded (W-116): the excluded lines, each with why and Restore.
const EXCLUDED = {
    count: 2,
    lines: [
        { id: 21, date: '2026-10-02', payee: 'CHK #2201 CLEARED', description: '', amount: '-310.00', reason: 'in_books' },
        { id: 22, date: '2026-09-30', payee: 'OLD FEE', description: 'before the books', amount: '-4.00', reason: 'excluded' },
    ],
};

test('Show excluded is a real button that says how many and opens the list', () => {
    const { BP } = load();
    const html = BP._excludedPanel(EXCLUDED, 9);
    const m = html.match(/<button([^>]*)>Show excluded \(2\)<\/button>/);
    assert.ok(m, 'no Show excluded (2) button');
    assert.match(m[1], /type="button"/);
    assert.match(m[1], /aria-expanded="false"/);
    assert.match(m[1], /aria-controls="excluded-list"/);
    assert.doesNotMatch(m[1], /data-write/);
    // the list starts closed
    assert.match(html, /<div id="excluded-list"[^>]*\bhidden\b/);
});

test('each excluded line says why and has Restore', () => {
    const { BP } = load();
    const html = BP._excludedPanel(EXCLUDED, 9);
    assert.match(html, /CHK #2201 CLEARED[\s\S]*Already in the books: same date and amount as a matched line/);
    assert.match(html, /OLD FEE[\s\S]*Excluded by you/);
    const restores = [...html.matchAll(/<button([^>]*)>Restore<\/button>/g)].map(x => x[1]);
    assert.equal(restores.length, 2);
    for (const attrs of restores) {
        // hidden from a read-only or accountant's sign-in (App.hideWriteControls)
        assert.match(attrs, /data-write/);
        assert.match(attrs, /type="button"/);
    }
    assert.match(restores[0], /BankingPage\.restoreLine\(21, 9\)/);
    assert.match(restores[1], /BankingPage\.restoreLine\(22, 9\)/);
});

test('the toggle opens and closes the list, aria-expanded with it', () => {
    const list = { hidden: true };
    const { BP } = load({ elements: { 'excluded-list': list } });
    const attrs = { 'aria-expanded': 'false' };
    const btn = {
        textContent: 'Show excluded (2)',
        getAttribute: (k) => attrs[k],
        setAttribute: (k, v) => { attrs[k] = v; },
    };
    BP.toggleExcluded(btn);
    assert.equal(attrs['aria-expanded'], 'true');
    assert.equal(list.hidden, false);
    assert.equal(btn.textContent, 'Hide excluded (2)');
    BP.toggleExcluded(btn);
    assert.equal(attrs['aria-expanded'], 'false');
    assert.equal(list.hidden, true);
    assert.equal(btn.textContent, 'Show excluded (2)');
});

test('Restore posts to the restore route and comes back to the register', async () => {
    const { BP, calls } = load();
    await BP.restoreLine(21, 9);
    assert.equal(calls.post.length, 1);
    assert.equal(calls.post[0][0], '/banking/transactions/21/restore');
    assert.deepEqual(calls.back, [9]);
});
