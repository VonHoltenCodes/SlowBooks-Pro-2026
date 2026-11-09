// The Collections page's skipped-reason summary counts in plain words
// (the 2.31.10 gate's Mac read "1 reminders are turned off"). collections.js
// is loaded alone, as the browser's other probes load the pages.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function page() {
    const ctx = {
        console, window: {}, location: { hash: '#/' },
        document: { addEventListener() {}, getElementById: () => null, querySelector: () => null },
        T: s => s, escapeHtml: s => String(s ?? ''), openModal() {}, closeModal() {}, toast() {},
        formatDate: s => s || '', formatCurrency: n => String(n), $: () => null, $$: () => [], App: {}, API: {},
    };
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/collections.js'), 'utf8'), ctx);
    return ctx.window.CollectionsPage;
}
const off = { reason: 'reminders are turned off for this customer' };
const mail = { reason: 'no email address on file' };
const gone = { reason: 'customer is inactive' };

test('one of each reads as a sentence', () => {
    const P = page();
    assert.equal(P._reasonSummary([off]), '1 customer has reminders off');
    assert.equal(P._reasonSummary([mail]), '1 with no email address');
    assert.equal(P._reasonSummary([gone]), '1 inactive customer');
});

test('several count, in the order first met', () => {
    const P = page();
    assert.equal(P._reasonSummary([mail, off, mail, mail, off, gone, gone]),
        '3 with no email address, 2 customers have reminders off, 2 inactive customers');
});

test('a reason the page does not know is counted as it is', () => {
    assert.equal(page()._reasonSummary([{ reason: 'held by the owner' }]), '1 held by the owner');
});
