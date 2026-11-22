// Receive Payments' Save & New (#1006): the payment is posted as Record
// Payment posts it, then a blank Record Payment opens with the date kept;
// Ctrl+Enter presses Save & New, and does nothing where it is hidden.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');

function field(value) { return { value }; }

function page() {
    const posts = [];
    const calls = [];
    const newDate = { value: '2026-11-17' };
    const context = {
        posts, calls, newDate,
        API: { post: async (url, data) => { posts.push({ url, data }); return { id: posts.length }; } },
        App: {
            requestKey: () => 'k',
            refresh: () => calls.push('refresh'),
            matchRoute: () => ({ route: { page: 'payments' }, param: null }),
            parseHash: h => ({ path: String(h).replace(/^#/, '') }),
            navigate: async () => { calls.push('navigate'); },
            _dialogUnder: null,
        },
        location: { hash: '#/payments' },
        closeModal: () => calls.push('close'),
        toast: (m) => calls.push('toast:' + m),
        toastPosted: (m) => calls.push('posted:' + m),
        TxnWarn: { ok: async () => true },
        ClassWarn: { ok: async () => true },
        PaymentTerms: { rememberDiscountAccount() {}, asksOverride: () => false },
        Terms: { text: v => v },
        T: v => v,
        classIdFromForm: () => null,
        $: sel => (sel === '#payment-form [name="date"]' ? newDate : null),
        $$: () => [],
        window: {},
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'payments.js'), 'utf8') + '\nthis.page = PaymentsPage;', context);
    context.page.showForm = async () => { calls.push('showForm'); newDate.value = '2026-11-17'; };
    return context;
}

function form(date) {
    const buttons = [{ disabled: false }, { disabled: false }];
    return {
        customer_id: field('3'), date: field(date), amount: field('50.00'), method: field(''),
        check_number: field(''), reference: field(''), deposit_to_account_id: field(''), notes: field(''),
        dataset: {},
        querySelectorAll: () => buttons,
        buttons,
    };
}

function submitter(saveNew) { return { hasAttribute: a => saveNew && a === 'data-save-new' }; }

test('Save & New twice posts both payments and leaves a blank form with the date kept', async () => {
    const ctx = page();
    ctx.page._remainingCents = () => ({ remaining: 0 });
    for (const date of ['2026-11-03', '2026-11-04']) {
        const f = form(date);
        await ctx.page.save({ preventDefault() {}, target: f, submitter: submitter(true) });
        assert.equal(ctx.newDate.value, date);
        assert.ok(f.buttons.every(b => !b.disabled));
    }
    assert.equal(ctx.posts.length, 2);
    assert.deepEqual(ctx.posts.map(p => p.url), ['/payments', '/payments']);
    assert.deepEqual(ctx.posts.map(p => p.data.date), ['2026-11-03', '2026-11-04']);
    assert.ok(!ctx.calls.includes('refresh'));
    assert.equal(ctx.calls.filter(c => c === 'showForm').length, 2);
    assert.equal(ctx.calls.filter(c => c === 'posted:Payment recorded').length, 2);
});

test('Record Payment is unchanged: one post, then the page refresh and no new form', async () => {
    const ctx = page();
    ctx.page._remainingCents = () => ({ remaining: 0 });
    await ctx.page.save({ preventDefault() {}, target: form('2026-11-05'), submitter: submitter(false) });
    assert.equal(ctx.posts.length, 1);
    assert.deepEqual([...ctx.calls].filter(c => c === 'refresh' || c === 'showForm'), ['refresh']);
});

test('a document under the form is left alone: no redraw, the blank form still opens', async () => {
    const ctx = page();
    ctx.page._remainingCents = () => ({ remaining: 0 });
    ctx.App.matchRoute = () => ({ route: { page: 'invoices', document: true }, param: '12' });
    await ctx.page.save({ preventDefault() {}, target: form('2026-11-06'), submitter: submitter(true) });
    assert.ok(!ctx.calls.includes('navigate'));
    assert.ok(ctx.calls.includes('showForm'));
});

test('a failed save keeps the form: no new form opens', async () => {
    const ctx = page();
    ctx.page._remainingCents = () => ({ remaining: 0 });
    ctx.API.post = async () => { throw new Error('closed period'); };
    await ctx.page.save({ preventDefault() {}, target: form('2026-11-06'), submitter: submitter(true) });
    assert.ok(ctx.calls.includes('toast:closed period'));
    assert.ok(!ctx.calls.includes('showForm'));
});

function key(over) {
    const ev = { ctrlKey: true, key: 'Enter', altKey: false, metaKey: false, shiftKey: false, prevented: false, stopped: false, ...over };
    ev.preventDefault = () => { ev.prevented = true; };
    ev.stopPropagation = () => { ev.stopped = true; };
    return ev;
}

test('Ctrl+Enter presses Save & New; a hidden one (read-only) is not pressed', () => {
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
    assert.equal(pressed.length, 1);

    ctx.page._keys(key({ currentTarget: f, ctrlKey: false }));
    ctx.page._keys(key({ currentTarget: { querySelector: () => null, requestSubmit: b => pressed.push(b) } }));
    assert.equal(pressed.length, 1);
});
