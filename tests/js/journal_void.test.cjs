// The Journal Entries page offers Void on a journal the QuickBooks Online
// import posted (2.18.0: it voids here like any journal), and the entry
// view offers it too, so a posting opened from the bank register (a QBO
// deposit) can be voided there. A document's own posting voids from its
// document, so its view offers no Void.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function page(entry) {
    const dialogs = [];
    const context = {
        $: () => null,  // the view's attachments block (2.22.3) looks for its list
        API: { get: async () => entry },
        escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, character => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[character]),
        formatDate: value => value, formatCurrency: value => `$${value}`,
        sourceWord: value => String(value || ''),
        CostCodes: { headHtml: () => '' },
        CostTypes: { headHtml: () => '', cellHtml: () => '', load: async () => {}, fromRow: () => null },
        openModal: (title, html) => dialogs.push({ title, html }),
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/journal.js'), 'utf8') + '\nthis.JournalPage = JournalPage;', context);
    return { page: context.JournalPage, dialogs };
}

const entry = source_type => ({
    id: 7, date: '2026-08-03', description: 'QBO Deposit', reference: '', source_type,
    lines: [], total_debit: 20, total_credit: 20,
});

for (const kind of ['manual', 'qbo_journal', 'qbo_ledger']) {
    test(`a ${kind} entry offers Void, in the list and in its view`, async () => {
        const f = page(entry(kind));
        assert.equal(f.page.canVoid(entry(kind)), true);
        await f.page.view(7);
        assert.match(f.dialogs[0].html, /onclick="JournalPage\.void\(7\)"/);
    });
}

for (const kind of ['manual_void', 'qbo_ledger_void', 'bill_payment', 'deposit']) {
    test(`a ${kind} entry does not`, async () => {
        const f = page(entry(kind));
        assert.equal(f.page.canVoid(entry(kind)), false);
        await f.page.view(7);
        assert.doesNotMatch(f.dialogs[0].html, /JournalPage\.void/);
    });
}

// 2.18.0: an entry already voided says "Voided" and offers no Void, in the
// list and in its view (a second Void used to answer "already voided").
function listPage(entries) {
    const context = {
        $: () => null,
        API: { get: async url => (url === '/journal' ? { entries, total: entries.length, skip: 0, limit: 100, default_range: true } : entries[0]) },
        escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, character => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[character]),
        formatDate: value => value, formatCurrency: value => `$${value}`,
        sourceWord: value => String(value || ''),
        CostCodes: { headHtml: () => '' },
        CostTypes: { headHtml: () => '', cellHtml: () => '', load: async () => {}, fromRow: () => null },
        openModal: (title, html) => { context.shown = html; },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/journal.js'), 'utf8') + '\nthis.JournalPage = JournalPage;', context);
    return context;
}

for (const kind of ['manual', 'qbo_journal']) {
    test(`a voided ${kind} entry shows Voided and no Void, in the list and its view`, async () => {
        const voided = { ...entry(kind), voided: true };
        const standing = { ...entry(kind), id: 8, voided: false };
        const f = listPage([voided, standing]);
        const html = await f.JournalPage.render();
        const [first, second] = html.split('<tr>').slice(2);
        assert.match(first, /Voided/);
        assert.doesNotMatch(first, /JournalPage\.void\(/);
        assert.doesNotMatch(second, /Voided/);
        assert.match(second, /JournalPage\.void\(8\)/);
        await f.JournalPage.view(7);
        assert.match(f.shown, /Voided/);
        assert.doesNotMatch(f.shown, /JournalPage\.void\(/);
    });
}
