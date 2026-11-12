// A register line opens its posting over the register (App.openOver), not at
// the posting's own address: going there drew the document's whole list
// first (Journal Entries loads every entry in the company, minutes on years
// of imported history) and Close left that list on screen instead of the
// register the line was clicked in.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function app(startHash) {
    const calls = [];
    const pageContent = { innerHTML: '' };
    const stub = name => new Proxy({}, {
        get: (_t, prop) => (prop === 'then' ? undefined : (...args) => {
            calls.push([`${name}.${String(prop)}`, ...args.map(String)]);
            return prop === 'render' ? `<${name}>` : '';
        }),
    });
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/app.js'), 'utf8');
    const ctx = {
        console, setTimeout, clearTimeout, Promise, URLSearchParams,
        window: {},
        document: { addEventListener() {}, querySelectorAll: () => [] },
        location: { hash: startHash },
        history: {
            state: null,
            pushState: (_s, _t, url) => { calls.push(['pushState', url]); ctx.location.hash = url; },
            replaceState: (_s, _t, url) => { calls.push(['replaceState', url]); ctx.location.hash = url; },
        },
        $: sel => (sel === '#page-content' ? pageContent : null), $$: () => [],
        escapeHtml: s => String(s ?? ''),
        toast: msg => calls.push(['toast', String(msg)]),
        T: s => s,
        Terms: { text: s => s, isNonprofit: () => false, isConstruction: () => false },
    };
    for (const name of new Set(src.match(/\b[A-Z][A-Za-z]*Page\b/g) || [])) ctx[name] = stub(name);
    vm.createContext(ctx);
    vm.runInContext(src + '\nthis.App = App;', ctx);
    return { App: ctx.App, calls, ctx };
}

// The app's work after a navigation is a chain of awaits and timers; on a
// loaded machine (the CI runner, a box running several suites) five
// milliseconds was not always enough for it to reach the page's view
// call, and the test read its calls too early. settle() waits a little
// longer for the negative checks ("no list drawn"), and until() waits for
// the call a positive check expects, up to two seconds, so a slow machine
// passes and a wrong app still fails.
const settle = () => new Promise(r => setTimeout(r, 25));
const until = async pred => {
    for (let i = 0; i < 200 && !pred(); i++) await new Promise(r => setTimeout(r, 10));
};

test('a journal posting opens over the register without drawing Journal Entries', async () => {
    const a = app('#/banking/3');
    const followed = a.App.openOver({ button: 0 }, '/#/journal/42');
    await settle();
    await until(() => a.calls.some(c => c[0] === 'JournalPage.view' && c[1] === '42'));
    assert.equal(followed, false, 'the link is cancelled');
    assert.deepEqual(a.calls.filter(c => c[0].endsWith('.render')), [], 'no list drawn');
    assert.ok(a.calls.some(c => c[0] === 'JournalPage.view' && c[1] === '42'));
    assert.equal(a.ctx.location.hash, '#/journal/42', 'the posting has its address');
    // Close puts the register's address back; the register was never replaced
    a.App.dialogClosed();
    assert.equal(a.ctx.location.hash, '#/banking/3');
});

test('a document with its own page (an invoice) opens over the register too', async () => {
    const a = app('#/banking/3');
    assert.equal(a.App.openOver({ button: 0 }, '/#/invoices/9'), false);
    await settle();
    await until(() => a.calls.some(c => c[0] === 'InvoicesPage.view' && c[1] === '9'));
    assert.deepEqual(a.calls.filter(c => c[0] === 'InvoicesPage.render'), []);
    assert.ok(a.calls.some(c => c[0] === 'InvoicesPage.view' && c[1] === '9'));
    a.App.dialogClosed();
    assert.equal(a.ctx.location.hash, '#/banking/3');
});

test('a modified click or a page address follows the link as before', () => {
    const a = app('#/banking/3');
    assert.equal(a.App.openOver({ button: 0, ctrlKey: true }, '/#/journal/42'), true);
    assert.equal(a.App.openOver({ button: 1 }, '/#/journal/42'), true);
    assert.equal(a.App.openOver({ button: 0 }, '/#/banking/5'), true, 'a register is a page, not a document');
    assert.equal(a.App.openOver({ button: 0 }, '/#/reports/profit-loss'), true, 'a report view is not a document');
    assert.deepEqual(a.calls, []);
});

test('opening a document by its address still draws its list behind it', async () => {
    const a = app('#/journal/42');
    await a.App.navigate('#/journal/42');
    await settle();
    await until(() => a.calls.some(c => c[0] === 'JournalPage.view' && c[1] === '42'));
    assert.ok(a.calls.some(c => c[0] === 'JournalPage.render'));
    assert.ok(a.calls.some(c => c[0] === 'JournalPage.view' && c[1] === '42'));
});

test('a save or void inside it redraws the register, with the document back over it', async () => {
    const a = app('#/banking/3');
    a.ctx.closeModal = () => a.App.dialogClosed();
    a.App.openOver({ button: 0 }, '/#/payments/9');
    await settle();
    a.calls.length = 0;
    a.App.refresh();
    await settle();
    await until(() => a.calls.some(c => c[0] === 'PaymentsPage.view' && c[1] === '9'));
    const names = a.calls.map(c => c[0]);
    assert.ok(names.includes('BankingPage.renderRegister'), names.join(','));
    assert.ok(!names.includes('PaymentsPage.render'), 'the payments list is not drawn');
    assert.ok(a.calls.some(c => c[0] === 'PaymentsPage.view' && c[1] === '9'));
    assert.equal(a.ctx.location.hash, '#/payments/9');
});

test('a save on a document opened at its own address still redraws its list', async () => {
    const a = app('#/payments/9');
    a.ctx.closeModal = opts => { if (!(opts && opts.keepAddress)) a.App.dialogClosed(); };
    await a.App.navigate('#/payments/9');
    await settle();
    a.calls.length = 0;
    a.App.refresh();
    await settle();
    await until(() => a.calls.some(c => c[0] === 'PaymentsPage.render'));
    assert.ok(a.calls.some(c => c[0] === 'PaymentsPage.render'));
    assert.equal(a.ctx.location.hash, '#/payments/9');
});
