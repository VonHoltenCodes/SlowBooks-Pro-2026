// A batch send's result lists the sends that did not go through. Some of
// those lines read "… may or may not have been sent." (the message was
// handed over and the connection lost), so the heading over them never
// says flatly that they were not sent, and reads right for one and for many.
// Every dialog that lists a batch's failures uses the one heading:
// Reports' statements and collection letters, the Collections page's
// reminders and monthly statements, and Year-End Giving Statements.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');
const HEADING = 'Not sent, or not confirmed';
const UNSURE = 'Ada: The statement may or may not have been sent. The connection to the mail server was lost.';
const REFUSED = 'Bo: The statement could not be sent. The mail server refused the message.';

function load({ admin = false, giving = null } = {}) {
    const shown = { modals: [], toasts: [], keys: [] };
    const box = { textContent: '', innerHTML: '' };
    const context = {
        console, JSON, URLSearchParams, Date, parseInt,
        window: {}, location: { hash: '#/' },
        document: { querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, getElementById: () => null },
        escapeHtml: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
        formatCurrency: (v) => String(v), formatDate: (s) => s || '',
        T: (s) => s,
        Terms: { text: (s) => { shown.keys.push(s); return s; }, isNonprofit: () => false, isConstruction: () => false },
        App: { isAdmin: () => admin, navigate() {} },
        API: { post: async () => giving },
        confirm: () => true,
        $: (sel) => (sel === '#gs-year' ? { value: '2025' } : sel === '#gs-result' ? box : null),
        $$: () => [],
        openModal: (title, html) => shown.modals.push({ title, html }),
        closeModal() {},
        toast: (text, kind) => shown.toasts.push({ text, kind }),
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'reports.js'), 'utf8') + '\nthis.ReportsPage = ReportsPage;', context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'collections.js'), 'utf8'), context);
    return { R: context.ReportsPage, C: context.window.CollectionsPage, shown, box };
}

test("the heading counts one and many in the same words, through the company's vocabulary", () => {
    const { R, shown } = load();
    assert.equal(R._notSentHeading(1), `${HEADING} (1):`);
    assert.equal(R._notSentHeading(12), `${HEADING} (12):`);
    assert.ok(shown.keys.includes(`${HEADING} (12):`));
});

test('a result with a send that may have gone out is not headed "could not be sent"', () => {
    const { R, shown } = load();
    R._sendResult('Statements', 'Sent 3 statements.', [UNSURE, REFUSED]);
    assert.equal(shown.modals.length, 1);
    const html = shown.modals[0].html;
    assert.ok(html.includes(`Sent 3 statements. ${HEADING} (2):`), html);
    assert.ok(!/\d+ could not be sent:/.test(html), html);
    assert.ok(html.includes('may or may not have been sent.'));
    assert.ok(html.includes('Bo: The statement could not be sent.'));
});

test('one line is headed the same way', () => {
    const { R, shown } = load();
    R._sendResult('Collection Letters', 'Generated 1 letter, emailed 0.', [UNSURE]);
    assert.ok(shown.modals[0].html.includes(`Generated 1 letter, emailed 0. ${HEADING} (1):`));
});

test('a result with no failures has no such heading', () => {
    const { R, shown } = load();
    R._sendResult('Statements', 'Sent 2 statements.', [], ['Cy: The mail server refused a copy to: c@example.test.']);
    assert.ok(!shown.modals[0].html.includes(HEADING));
    R._sendResult('Statements', 'Sent 2 statements.', []);
    assert.deepEqual(shown.toasts.map(t => t.text), ['Sent 2 statements.']);
});

test("the Collections page's result is headed by the same words", () => {
    const { C, shown } = load();
    C.reload = () => {};
    C._showResult('Reminders', { sent: 1, failed: 2, skipped: 0, errors: [UNSURE, REFUSED] });
    const html = shown.modals[0].html;
    assert.ok(html.includes(`Sent 1, failed 2, skipped 0. ${HEADING} (2):`), html);
    assert.ok(!/\d+ could not be sent:/.test(html));
});

test('Year-End Giving Statements heads its failed lines the same way', async () => {
    const { R, box } = load({ giving: { sent: 0, failed: 1, skipped: 0, errors: [UNSURE], notes: [] } });
    await R.emailGivingStatements();
    assert.ok(box.innerHTML.includes(`Sent 0, failed 1, skipped 0. ${HEADING} (1):<ul>`), box.innerHTML);
    const clean = load({ giving: { sent: 2, failed: 0, skipped: 0, errors: [], notes: [] } });
    await clean.R.emailGivingStatements();
    assert.equal(clean.box.innerHTML, 'Sent 2, failed 0, skipped 0.');
});
