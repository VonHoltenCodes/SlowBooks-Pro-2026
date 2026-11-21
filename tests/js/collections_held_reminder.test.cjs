// The Collections list never says "ready" for a reminder no run will send.
// A reminder that failed for the customer while a copy went out is held:
// the list says so and points at Remind now. collections.js is loaded
// alone, as the browser's other probes load the pages.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function page(seen) {
    const ctx = {
        console, window: {}, location: { hash: '#/' },
        document: { addEventListener() {}, getElementById: () => null, querySelector: () => null },
        T: s => { seen.push(s); return s; },
        escapeHtml: s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
        openModal() {}, closeModal() {}, toast() {},
        formatDate: s => s || '', formatCurrency: n => String(n), $: () => null, $$: () => [], App: {}, API: {},
    };
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/collections.js'), 'utf8'), ctx);
    return ctx.window.CollectionsPage;
}
const row = next => ({ reminders_enabled: true, next_reminder: { label: '15 days overdue', due_on: '2026-10-21', ...next } });

test('a held reminder is not called ready and points at Remind now', () => {
    const seen = [];
    const html = page(seen)._nextHtml(row({ ready: false, held: true }));
    assert.ok(html.includes('15 days overdue'));
    assert.ok(html.includes('held: use Remind now'));
    assert.ok(!html.includes('ready'));
    assert.ok(!html.includes('2026-10-21'));
    assert.ok(html.includes('Check the email address, then use Remind now.'));
    assert.ok(seen.includes('held: use Remind now'), 'the words go through T()');
});

test('a reminder a run will send still reads ready, one ahead reads its date', () => {
    const P = page([]);
    const ready = P._nextHtml(row({ ready: true, held: false }));
    assert.ok(ready.includes('<strong>ready</strong>') && !ready.includes('held'));
    const ahead = P._nextHtml(row({ ready: false, held: false }));
    assert.ok(ahead.includes('2026-10-21') && !ahead.includes('held') && !ahead.includes('ready'));
    // an answer from before the flag existed reads as it did
    assert.ok(P._nextHtml(row({ ready: true })).includes('<strong>ready</strong>'));
});
