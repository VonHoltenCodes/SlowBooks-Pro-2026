// Attachments before the first save on the rest of the new-document forms
// (#881, after #796): the strip is on a new document only, the save sends
// the held ids and clears them once the create has answered, a refused save
// keeps them for the retry, closing the dialog discards them, and the two
// Quick Entry pages discard them when the page is left. The forms drive the
// real PendingFiles from utils.js, with the page helpers stubbed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');
const read = (f) => fs.readFileSync(path.join(JS, f), 'utf8');
const UTILS = read('utils.js');

function slice(start, end) {
    const i = UTILS.indexOf(start);
    assert.ok(i >= 0, `${start} is in utils.js`);
    return UTILS.slice(i, UTILS.indexOf(end, i) + end.length);
}

// PendingFiles as utils.js has it, in the page's context, holding `held`
// (as if each file had been attached), its server calls recorded.
function pendingFiles(context, held, calls) {
    context.API.del = async (p) => { calls.del.push(p); return { status: 'deleted' }; };
    context.formatFileSize = (n) => `${n} bytes`;
    context.$ = context.$ || (() => null);
    vm.runInContext(`${slice('const PendingFiles = {', '\n};\n')}\nthis.PendingFiles = PendingFiles;`, context);
    context.PendingFiles._files = held.map((id) => ({ id, filename: `${id.slice(0, 4)}.pdf`, file_size: 5 }));
    return context.PendingFiles;
}

const A = 'a'.repeat(32);
const B = 'b'.repeat(32);

function journal({ accountant = false, post } = {}) {
    const calls = { modal: [], post: [], del: [], toast: [], closed: 0 };
    const context = {
        console,
        $: () => null,
        $$: () => [],
        API: {
            get: async (url) => (url === '/accounts' ? [{ id: 1, name: 'Cash' }] : []),
            post: post || (async (url, body) => { calls.post.push({ url, body }); return {}; }),
        },
        App: { isAccountant: () => accountant, navigate: () => {} },
        location: { hash: '#/journal' },
        T: (w) => w,
        Terms: { text: (w) => w },
        escapeHtml: (v) => String(v ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`),
        accountLabel: (a) => a.name,
        todayISO: () => '2026-11-12',
        classFormGroupHtml: async () => '',
        jobFormGroupHtml: async () => '',
        classIdFromForm: () => null,
        jobIdFromForm: () => null,
        TxnWarn: { ok: async () => true },
        ClassWarn: { ok: async () => true },
        toast: (...args) => calls.toast.push(args),
        toastPosted: () => {},
        closeModal: () => { calls.closed++; },
        CostCodes: { headHtml: () => '', cellHtml: () => '', load: async () => {}, fromRow: () => null },
        CostTypes: { headHtml: () => '', cellHtml: () => '', load: async () => {}, fromRow: () => null },
        Nonprofit: { loadFunds: async () => {}, headHtml: () => '', cellHtml: () => '' },
        openModal: (title, html, opts) => calls.modal.push({ title, html, opts }),
        document: { querySelector: () => null },
    };
    vm.createContext(context);
    vm.runInContext(read('journal.js') + '\nthis.JournalPage = JournalPage;', context);
    const JournalPage = context.JournalPage;
    JournalPage.loadJobs = async () => {};
    JournalPage.collectLines = () => [
        { account_id: 1, debit: 40, credit: 0 },
        { account_id: 2, debit: 0, credit: 40 },
    ];
    return { context, JournalPage, calls };
}

const journalForm = () => ({
    dataset: {},
    date: { value: '2026-11-12' },
    description: { value: 'Owner paid for the sign' },
    reference: { value: '' },
    basis: { value: 'both' },
});

test('a new journal entry shows the strip; an edit and an accountant do not', async () => {
    const f = journal();
    pendingFiles(f.context, [], f.calls);
    await f.JournalPage.showForm();
    assert.match(f.calls.modal[0].html, /class="pending-files" data-write/);
    assert.match(f.calls.modal[0].html, /aria-label="File to attach"/);
    await f.JournalPage.showForm({ id: 9, lines: [] });
    assert.doesNotMatch(f.calls.modal[1].html, /pending-files/);

    const acct = journal({ accountant: true });
    pendingFiles(acct.context, [], acct.calls);
    await acct.JournalPage.showForm();
    // the accountant's journal form is unlocked (data-accountant-ok), and
    // the server refuses it a held file: no strip at all
    assert.doesNotMatch(acct.calls.modal[0].html, /pending-files/);
});

test('closing the journal form discards its files; the save sends and clears them', async () => {
    const f = journal();
    const pf = pendingFiles(f.context, [A, B], f.calls);
    await f.JournalPage.showForm();
    // showForm starts the form with none; attach two
    pf._files = [{ id: A, filename: 'a.pdf' }, { id: B, filename: 'b.pdf' }];
    f.calls.modal[0].opts.onClose();
    assert.deepEqual(f.calls.del, [`/attachments/pending/${A}`, `/attachments/pending/${B}`]);
    assert.deepEqual([...pf.ids()], []);

    pf._files = [{ id: A, filename: 'a.pdf' }];
    await f.JournalPage.save({ preventDefault() {}, target: journalForm() });
    assert.deepEqual(f.calls.post.map((p) => [p.url, [...p.body.pending_attachments]]), [['/journal', [A]]]);
    assert.deepEqual([...pf.ids()], []);
    // the dialog closing after the save discards nothing: the files are the entry's
    f.calls.del.length = 0;
    f.calls.modal[0].opts.onClose();
    assert.deepEqual(f.calls.del, []);
});

test('a refused journal save keeps the files for the retry', async () => {
    const f = journal({ post: async () => { throw new Error('Debits and credits must balance'); } });
    const pf = pendingFiles(f.context, [A], f.calls);
    await f.JournalPage.save({ preventDefault() {}, target: journalForm() });
    assert.deepEqual(f.calls.toast, [['Debits and credits must balance', 'error']]);
    assert.deepEqual([...pf.ids()], [A]);
    assert.deepEqual(f.calls.del, []);
});

function vendorCredits({ post } = {}) {
    const calls = { modal: [], post: [], del: [], toast: [] };
    const context = {
        console,
        $: () => null,
        $$: () => [],
        API: {
            get: async () => [],
            post: post || (async (url, body) => { calls.post.push({ url, body }); return { id: 3, credit_number: 'VC-3' }; }),
        },
        App: { navigate: () => {} },
        escapeHtml: (v) => String(v ?? ''),
        todayISO: () => '2026-11-12',
        classFormGroupHtml: async () => '',
        classIdFromForm: () => null,
        PurchaseAccounts: { filter: (a) => a },
        PurchaseLines: { lineAmount: () => 0 },
        ItemQuickAdd: { itemId: () => null },
        TxnWarn: { ok: async () => true },
        ClassWarn: { ok: async () => true },
        toast: (...args) => calls.toast.push(args),
        toastPosted: () => {},
        closeModal: () => {},
        openModal: (title, html, opts) => calls.modal.push({ title, html, opts }),
        document: { querySelector: () => null },
    };
    vm.createContext(context);
    vm.runInContext(read('vendor_credits.js') + '\nthis.VendorCreditsPage = VendorCreditsPage;', context);
    const VendorCreditsPage = context.VendorCreditsPage;
    VendorCreditsPage.lineHtml = () => '<tr></tr>';
    VendorCreditsPage.recalc = () => 40;
    VendorCreditsPage._vendors = [{ id: 1, name: 'Ridge Supply' }];
    return { context, VendorCreditsPage, calls };
}

const vcForm = () => ({
    dataset: {},
    vendor_id: { value: '1' },
    date: { value: '2026-11-12' },
    ref_number: { value: '' },
    tax_rate: { value: '0' },
    notes: { value: '' },
});

test('New Vendor Credit holds files until the create answers', async () => {
    const f = vendorCredits();
    const pf = pendingFiles(f.context, [], f.calls);
    await f.VendorCreditsPage.showForm();
    assert.match(f.calls.modal[0].html, /class="pending-files" data-write/);
    pf._files = [{ id: A, filename: 'credit-note.pdf' }];
    await f.VendorCreditsPage.save({ preventDefault() {}, target: vcForm() });
    assert.deepEqual([...f.calls.post[0].body.pending_attachments], [A]);
    assert.deepEqual([...pf.ids()], []);

    const refused = vendorCredits({ post: async () => { throw new Error('Vendor not found'); } });
    const kept = pendingFiles(refused.context, [B], refused.calls);
    await refused.VendorCreditsPage.save({ preventDefault() {}, target: vcForm() });
    assert.deepEqual([...kept.ids()], [B]);
});

test('the Quick Entry pages discard their files when the page is left', () => {
    const app = read('app.js');
    for (const route of ["'/quick-entry':", "'/bill-entry':"]) {
        const line = app.split('\n').find((l) => l.includes(route));
        assert.match(line, /mount: \(\) => \(\) => PendingFiles\.discard\(\)/, route);
    }
    // Save & Next: the files go with this document, and the next starts with none
    for (const [file, create] of [['app.js', "API.post('/invoices', { ...data, pending_attachments: PendingFiles.ids() })"],
        ['bill_quick_entry.js', 'pending_attachments: PendingFiles.ids(),']]) {
        const js = read(file);
        const at = js.indexOf(create);
        assert.ok(at >= 0, file);
        const after = js.slice(at, at + 400);
        assert.ok(after.indexOf('PendingFiles.saved();') < after.indexOf('PendingFiles.render();'), file);
    }
});

test('each new-document form shows the strip on a new document only and discards on close', () => {
    for (const [file, guard] of [
        ['invoices.js', "${id ? '' : PendingFiles.stripHtml()}"],
        ['estimates.js', "${id ? '' : PendingFiles.stripHtml()}"],
        ['purchase_orders.js', "${id ? '' : PendingFiles.stripHtml()}"],
        ['banking.js', "${entry ? '' : PendingFiles.stripHtml()}"],
        ['sales_receipts.js', '${PendingFiles.stripHtml()}'],
        ['vendor_credits.js', '${PendingFiles.stripHtml()}'],
    ]) {
        const js = read(file);
        assert.ok(js.includes(guard), file);
        assert.ok(js.includes('PendingFiles.reset();'), file);
        assert.ok(js.includes('onClose: () => PendingFiles.discard()'), file);
        assert.ok(js.includes('pending_attachments: PendingFiles.ids()') || js.includes('pending_attachments = PendingFiles.ids()'), file);
    }
    // an invoice's edit sends none: the ids ride on the create only
    assert.ok(read('invoices.js').includes('if (!id) body.pending_attachments = PendingFiles.ids();'));
});

test("a saved document's panel uploads under its own record type", async () => {
    const calls = { fetch: [], toast: [], get: [] };
    const fileInput = { files: [{ name: 'quote.pdf' }], value: 'quote.pdf' };
    const list = { innerHTML: '' };
    const context = {
        console,
        window: {},
        document: { addEventListener() {} },
        $: (sel) => ({ '#inv-attach-file': fileInput, '#inv-attachments-list': list })[sel] || null,
        API: {
            get: async (url) => { calls.get.push(url); return []; },
            responseError: async () => 'Upload failed',
        },
        fetch: async (url, opts) => { calls.fetch.push([url, opts.method]); return { ok: true }; },
        FormData: class { append() {} },
        toast: (m) => calls.toast.push(m),
        escapeHtml: (v) => String(v ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`),
    };
    vm.createContext(context);
    vm.runInContext(read('invoices.js') + '\nthis.InvoicesPage = InvoicesPage;', context);
    const html = context.InvoicesPage.attachmentsPanelHtml('purchase_order', 7);
    assert.match(html, /onclick="InvoicesPage\.uploadAttachment\(7, 'purchase_order'\)"/);
    assert.match(html, /id="inv-attach-file" aria-label="File to attach"/);
    await context.InvoicesPage.uploadAttachment(7, 'purchase_order');
    assert.deepEqual(calls.fetch, [['/api/attachments/purchase_order/7', 'POST']]);
    assert.deepEqual(calls.get, ['/attachments/purchase_order/7']);
    // the invoice view's own call is unchanged
    await context.InvoicesPage.uploadAttachment(3);
    assert.deepEqual(calls.fetch[1], ['/api/attachments/invoice/3', 'POST']);
});
