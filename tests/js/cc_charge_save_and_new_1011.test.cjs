// Quick Enter CC charges (#1011): the card is picked once for a statement's
// batch and kept for the session; Save & New posts the charge and opens a
// blank form on the same card with the date kept; Ctrl+Enter presses it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');

const CARDS = [
    { id: 7, account_number: '2100', name: 'Visa', bank_kind: 'credit_card' },
    { id: 9, account_number: '2110', name: 'Amex', bank_kind: 'credit_card' },
];

function page() {
    const posts = [];
    const calls = [];
    const opened = [];
    let current = null;
    const context = {
        posts, calls, opened,
        API: {
            get: async (url) => (url.includes('bank=1') ? CARDS : []),
            post: async (url, data) => { posts.push({ url, data }); return { transaction_id: posts.length }; },
        },
        App: { navigate: async (h) => { calls.push('navigate:' + h); } },
        PurchaseAccounts: { withBalanceSheet: a => a, options: () => '' },
        classFormGroupHtml: async () => '',
        jobFormGroupHtml: async () => '',
        Nonprofit: { functionFormGroupHtml: () => '', formPayload: () => ({}) },
        PendingFiles: { reset() {}, stripHtml: () => '', ids: () => [], saved() {}, discard() {} },
        accountLabel: a => a.name,
        todayISO: () => '2026-11-17',
        escapeHtml: v => String(v ?? ''),
        openModal: (title, html) => {
            const card = /<option value="(\d+)" selected>/.exec(html);
            current = { date: '2026-11-17', card: card ? card[1] : '' };
            opened.push(current);
        },
        closeModal: () => calls.push('close'),
        toast: (m) => calls.push('toast:' + m),
        toastPosted: (m) => calls.push('posted:' + m),
        TxnWarn: { ok: async () => true },
        ClassWarn: { ok: async () => true },
        classIdFromForm: () => null,
        jobIdFromForm: () => null,
        document: { querySelector: () => ({ set value(v) { current.date = v; }, get value() { return current.date; } }) },
        window: {},
    };
    context.current = () => current;
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'cc_charges.js'), 'utf8') + '\nthis.page = CCChargesPage;', context);
    return context;
}

function form(card, date, amount = '12.50') {
    const v = value => ({ value });
    return {
        date: v(date), payee: v('Hardware'), account_id: v('40'), card_account_id: v(String(card)),
        amount: v(amount), reference: v(''), memo: v(''), dataset: {},
    };
}

function submitter(saveNew) { return { hasAttribute: a => saveNew && a === 'data-save-new' }; }

test('the picker starts on the last card, while it is still an active card', () => {
    const ctx = page();
    assert.equal(ctx.page._cardStart(CARDS, null), '7');
    assert.equal(ctx.page._cardStart(CARDS, '9'), '9');
    assert.equal(ctx.page._cardStart(CARDS, 9), '9');
    assert.equal(ctx.page._cardStart(CARDS, '55'), '7');
    assert.equal(ctx.page._cardStart([CARDS[1]], null), '9');
    assert.equal(ctx.page._cardStart([], '9'), '');
});

test('three charges in a row with Save & New post to the card picked once', async () => {
    const ctx = page();
    await ctx.page.showForm();
    assert.equal(ctx.current().card, '7');
    // picked the Amex for this statement
    let card = '9';
    for (const date of ['2026-10-02', '2026-10-05', '2026-10-09']) {
        await ctx.page.save({ preventDefault() {}, target: form(card, date), submitter: submitter(true) });
        card = ctx.current().card;
        assert.equal(card, '9');
        assert.equal(ctx.current().date, date);
    }
    assert.deepEqual(ctx.posts.map(p => p.data.card_account_id), [9, 9, 9]);
    assert.deepEqual(ctx.posts.map(p => p.data.date), ['2026-10-02', '2026-10-05', '2026-10-09']);
    assert.equal(ctx.opened.length, 4);
});

test('changing the card on one keeps the change for the next', async () => {
    const ctx = page();
    await ctx.page.save({ preventDefault() {}, target: form(9, '2026-10-02'), submitter: submitter(true) });
    assert.equal(ctx.current().card, '9');
    await ctx.page.save({ preventDefault() {}, target: form(7, '2026-10-03'), submitter: submitter(true) });
    assert.equal(ctx.current().card, '7');
});

test('Save Charge is unchanged: posts, closes and opens no new form, the card still kept', async () => {
    const ctx = page();
    await ctx.page.save({ preventDefault() {}, target: form(9, '2026-10-02'), submitter: submitter(false) });
    assert.equal(ctx.posts.length, 1);
    assert.equal(ctx.opened.length, 0);
    assert.deepEqual(ctx.calls.filter(c => c === 'close' || c.startsWith('navigate')), ['close', 'navigate:#/cc-charges']);
    await ctx.page.showForm();
    assert.equal(ctx.current().card, '9');
});

test('an edit keeps its own card: no Save & New, and the batch card is not changed', async () => {
    const ctx = page();
    ctx.page._lastCard = '7';
    const f = form(9, '2026-10-02');
    f.dataset.replaceOf = '31';
    ctx.Replace = { replacing: x => !!x.dataset.replaceOf, suffix: x => `/${x.dataset.replaceOf}/replace` };
    await ctx.page.save({ preventDefault() {}, target: f, submitter: submitter(true) });
    assert.equal(ctx.posts[0].url, '/cc-charges/31/replace');
    assert.equal(ctx.opened.length, 0);
    assert.equal(ctx.page._lastCard, '7');
});

test('a failed save opens no new form, and a second press while saving posts nothing', async () => {
    const ctx = page();
    ctx.API.post = async () => { throw new Error('closed period'); };
    await ctx.page.save({ preventDefault() {}, target: form(9, '2026-10-02'), submitter: submitter(true) });
    assert.ok(ctx.calls.includes('toast:closed period'));
    assert.equal(ctx.opened.length, 0);
    assert.equal(ctx.page._lastCard, null);

    let release;
    ctx.API.post = (url, data) => new Promise(r => { ctx.posts.push({ url, data }); release = () => r({ transaction_id: 1 }); });
    const first = ctx.page.save({ preventDefault() {}, target: form(9, '2026-10-02'), submitter: submitter(true) });
    await new Promise(r => setImmediate(r));
    await ctx.page.save({ preventDefault() {}, target: form(9, '2026-10-02'), submitter: submitter(true) });
    release();
    await first;
    assert.equal(ctx.posts.length, 1);
});

function key(over) {
    const ev = { ctrlKey: true, key: 'Enter', altKey: false, metaKey: false, shiftKey: false, prevented: false, stopped: false, ...over };
    ev.preventDefault = () => { ev.prevented = true; };
    ev.stopPropagation = () => { ev.stopped = true; };
    return ev;
}

test('Ctrl+Enter presses Save & New; a hidden one (read-only) or none (an edit) is not pressed', () => {
    const ctx = page();
    const more = { disabled: false, classList: { contains: () => false } };
    const pressed = [];
    const f = { querySelector: () => more, requestSubmit: b => pressed.push(b) };
    const ev = key({ currentTarget: f });
    ctx.page._keys(ev);
    assert.deepEqual(pressed, [more]);
    assert.ok(ev.prevented && ev.stopped);
    more.classList = { contains: c => c === 'hidden' };
    ctx.page._keys(key({ currentTarget: f }));
    ctx.page._keys(key({ currentTarget: { querySelector: () => null, requestSubmit: b => pressed.push(b) } }));
    ctx.page._keys(key({ currentTarget: f, ctrlKey: false }));
    assert.equal(pressed.length, 1);
});
