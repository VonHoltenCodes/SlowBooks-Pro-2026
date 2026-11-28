// #1233 follow-up: Release retainage asks for its amount and date in the
// app's own dialog, with a date input, not two free-text prompts. A typed
// "11/24/2026" never reaches the server (whose answer would be a raw
// validation message); a good date posts as YYYY-MM-DD. Drives the real
// PurchaseOrdersPage.releaseRetainage / doReleaseRetainage.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');
const SRC = fs.readFileSync(path.join(JS, 'purchase_orders.js'), 'utf8');

function page({ amount = '1000.00', date = '2026-11-24', refuse = null } = {}) {
    const seen = { modal: null, posts: [], toasts: [], closed: 0, prompts: 0 };
    const fields = { '#po-ret-amount': { value: amount, focus() {} }, '#po-ret-date': { value: date, focus() {} } };
    const context = {
        console, window: {}, document: { addEventListener() {} },
        location: { hash: '#/purchase-orders' },
        App: { navigate(h) { seen.navigated = h; } },
        API: {
            get: async () => ({ po_number: 'PO-7', retainage_outstanding: 1000 }),
            post: async (url, body) => { seen.posts.push({ url, body }); if (refuse) throw new Error(refuse); return { message: 'Released' }; },
        },
        formatCurrency: (v) => '$' + Number(v).toFixed(2),
        escapeHtml: (s) => String(s ?? ''),
        todayISO: () => '2026-11-27',
        toast: (m, kind) => seen.toasts.push({ m, kind }),
        prompt: () => { seen.prompts += 1; return null; },
        openModal: (title, html) => { seen.modal = { title, html }; },
        closeModal: () => { seen.closed += 1; },
        $: (sel) => fields[sel] || null,
        $$: () => [],
    };
    vm.createContext(context);
    vm.runInContext(SRC + '\nthis.PurchaseOrdersPage = PurchaseOrdersPage;', context);
    return { P: context.PurchaseOrdersPage, seen };
}

test('the release opens a dialog with an amount and a date input, defaulting to today', async () => {
    const { P, seen } = page();
    await P.releaseRetainage(7);
    assert.equal(seen.prompts, 0);
    assert.equal(seen.modal.title, 'Release retainage on PO-7');
    assert.match(seen.modal.html, /Retainage outstanding is \$1000\.00\./);
    assert.match(seen.modal.html, /<label for="po-ret-amount">Amount to release<\/label>\s*<input type="number"[^>]*id="po-ret-amount" value="1000\.00">/);
    assert.match(seen.modal.html, /<label for="po-ret-date">Release date<\/label>\s*<input type="date" id="po-ret-date" value="2026-11-27">/);
    assert.match(seen.modal.html, /PurchaseOrdersPage\.doReleaseRetainage\(7\)/);
});

test('a good date posts as YYYY-MM-DD and the dialog closes', async () => {
    const { P, seen } = page({ amount: '400', date: '2026-11-24' });
    await P.doReleaseRetainage(7);
    assert.deepEqual(seen.posts.map(p => [p.url, p.body.amount, p.body.date]),
        [['/purchase-orders/7/release-retainage', 400, '2026-11-24']]);
    assert.equal(seen.closed, 1);
});

test('a typed 11/24/2026 or an empty date gets a plain sentence and posts nothing', async () => {
    for (const bad of ['11/24/2026', '', '2026-11-2']) {
        const { P, seen } = page({ date: bad });
        await P.doReleaseRetainage(7);
        assert.equal(seen.posts.length, 0, bad);
        assert.deepEqual(seen.toasts, [{ m: 'Enter the release date.', kind: 'error' }], bad);
        assert.equal(seen.closed, 0);
    }
});

test('no amount is refused before anything posts', async () => {
    const { P, seen } = page({ amount: '' });
    await P.doReleaseRetainage(7);
    assert.equal(seen.posts.length, 0);
    assert.deepEqual(seen.toasts, [{ m: 'Enter an amount to release.', kind: 'error' }]);
});

test('the source no longer asks with prompt()', () => {
    const body = SRC.slice(SRC.indexOf('async releaseRetainage('), SRC.indexOf('// To Bill asks'));
    assert.doesNotMatch(body, /\bprompt\(/);
});

test('opened from the order view, Cancel opens the order again; from the list it just closes', async () => {
    const fromView = page();
    await fromView.P.releaseRetainage(7, true);
    assert.match(fromView.seen.modal.html, /onclick="PurchaseOrdersPage\.view\(7\)">Cancel<\/button>/);
    const fromList = page();
    await fromList.P.releaseRetainage(7);
    assert.match(fromList.seen.modal.html, /onclick="closeModal\(\)">Cancel<\/button>/);
});

test("the order view's Release retainage button says it came from the view", () => {
    const body = SRC.slice(SRC.indexOf('async view('), SRC.indexOf('fillsInvoiceHtml(po) {'));
    assert.match(body, /PurchaseOrdersPage\.releaseRetainage\(\$\{po\.id\}, true\)/);
    // the list row's button stays a plain open
    const list = SRC.slice(0, SRC.indexOf('async view('));
    assert.match(list, /PurchaseOrdersPage\.releaseRetainage\(\$\{po\.id\}\)"/);
});

test('a refused release keeps the dialog open with the reason; a release closes it', async () => {
    const no = page({ refuse: 'The release date is in a closed period.' });
    await no.P.doReleaseRetainage(7);
    assert.equal(no.seen.posts.length, 1);
    assert.equal(no.seen.closed, 0);
    assert.deepEqual(no.seen.toasts, [{ m: 'The release date is in a closed period.', kind: 'error' }]);

    const yes = page();
    await yes.P.doReleaseRetainage(7);
    assert.equal(yes.seen.closed, 1);
});
