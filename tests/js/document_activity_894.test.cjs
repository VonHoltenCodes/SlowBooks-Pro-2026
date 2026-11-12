// Activity on every document (#894): the panel under the Attachments panel.
// Drawn closed as "Activity", it asks for the count at once (count_only) and
// shows "Activity (N)"; opened, it loads the rows once and lists each
// sentence with its time and its details, escaped. The real ActivityPanel
// from utils.js, in a context with a stub page and API.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');
const UTILS = fs.readFileSync(path.join(JS, 'utils.js'), 'utf8');

function slice(start, end) {
    const i = UTILS.indexOf(start);
    assert.ok(i >= 0, `${start} is in utils.js`);
    return UTILS.slice(i, UTILS.indexOf(end, i) + end.length);
}

// a tiny element: what the panel reads and writes
function element(attrs = {}) {
    const children = {};
    return {
        open: false,
        dataset: { ...attrs },
        innerHTML: '',
        textContent: '',
        querySelector(sel) {
            if (!children[sel]) children[sel] = element();
            return children[sel];
        },
    };
}

function panel(answer) {
    const calls = [];
    const timers = [];
    const els = {};
    const context = {
        console,
        Date,
        setTimeout: (fn) => timers.push(fn),
        escapeHtml: (v) => String(v ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`),
        API: { get: async (url) => { calls.push(url); return answer(url); } },
        document: { getElementById: (id) => els[id] || null },
        window: {},
    };
    vm.createContext(context);
    vm.runInContext(
        `${slice('const ActivityPanel = {', '\n};\n')}\n`
        + `${slice('function activityPanelHtml(', '}\n')}\n`
        + 'this.ActivityPanel = ActivityPanel; this.activityPanelHtml = activityPanelHtml;',
        context,
    );
    return { context, calls, timers, els };
}

const EVENTS = [
    { when: '2026-11-12T15:00:00+00:00', when_local: '2026-11-12 09:00', who: 'Pat', kind: 'voided', text: 'Voided by Pat.', details: [] },
    { when: '2026-11-12T14:00:00+00:00', when_local: '2026-11-12 08:00', who: 'Pat', kind: 'edited', text: 'Edited by Pat.', details: ['Due date 2026-11-30 → 2026-12-15', '<b>x</b>'] },
];

test('the panel is drawn closed as "Activity" and asks for its count', async () => {
    const { context, calls, timers, els } = panel(() => ({ count: 2, events: [] }));
    const html = context.activityPanelHtml('invoice', 7);
    assert.match(html, /<details id="activity-panel-1"/);
    assert.doesNotMatch(html, /<details[^>]* open/);
    assert.match(html, /data-activity-type="invoice" data-activity-id="7"/);
    assert.match(html, />Activity<span class="activity-count">/);
    // the count is asked for once the panel is on the page
    const el = element({ activityType: 'invoice', activityId: '7' });
    els['activity-panel-1'] = el;
    assert.equal(timers.length, 1);
    await timers[0]();
    assert.equal(calls.length, 1);
    assert.match(calls[0], /^\/activity\?type=invoice&id=7&tz_offset=-?\d+&count_only=true$/);
    assert.equal(el.querySelector('.activity-count').textContent, ' (2)');
});

test('opening it loads the rows once, newest first, escaped', async () => {
    const { context, calls } = panel(() => ({ count: 2, events: EVENTS }));
    const el = element({ activityType: 'bill', activityId: '3' });
    el.open = true;
    context.ActivityPanel.toggled(el);
    await new Promise((r) => setImmediate(r));
    assert.equal(calls.length, 1);
    assert.doesNotMatch(calls[0], /count_only/);
    const list = el.querySelector('.activity-list').innerHTML;
    assert.ok(list.indexOf('Voided by Pat.') < list.indexOf('Edited by Pat.'));
    assert.match(list, /2026-11-12 09:00/);
    assert.match(list, /Due date 2026-11-30 → 2026-12-15/);
    assert.match(list, /&#60;b&#62;x&#60;\/b&#62;/);
    assert.equal(el.querySelector('.activity-count').textContent, ' (2)');
    // closed and opened again: no second read
    context.ActivityPanel.toggled(el);
    await new Promise((r) => setImmediate(r));
    assert.equal(calls.length, 1);
});

test('nothing recorded, and a refused read, each say so', async () => {
    const { context } = panel(() => ({ count: 0, events: [] }));
    const el = element({ activityType: 'job', activityId: '1' });
    el.open = true;
    await context.ActivityPanel.load(el);
    assert.match(el.querySelector('.activity-list').innerHTML, /Nothing recorded yet\./);

    const failing = panel(() => { throw new Error('There is no invoice 9.'); });
    const el2 = element({ activityType: 'invoice', activityId: '9' });
    await failing.context.ActivityPanel.load(el2);
    assert.match(el2.querySelector('.activity-list').innerHTML, /There is no invoice 9\./);
});

test('a record not saved yet gets no panel', () => {
    const { context } = panel(() => ({}));
    assert.equal(context.activityPanelHtml('invoice', null), '');
    assert.equal(context.activityPanelHtml('', 4), '');
});

test('every document view listed draws the panel under its attachments', () => {
    const views = {
        'invoices.js': "'invoice'",
        'sales_receipts.js': "'sales_receipt'",
        'estimates.js': "'estimate'",
        'credit_memos.js': "'credit_memo'",
        'bills.js': "'bill'",
        'vendor_credits.js': "'vendor_credit'",
        'expenses.js': "'expense'",
        'cc_charges.js': "'cc_charge'",
        'banking.js': "'bank_entry'",
        'deposits.js': "'deposit'",
        'journal.js': "'journal_entry'",
        'payments.js': "'payment'",
        'purchase_orders.js': "'purchase_order'",
        'jobs.js': "'pay_application'",
        'customers.js': "'customer'",
        'vendors.js': "'vendor'",
    };
    for (const [file, type] of Object.entries(views)) {
        const src = fs.readFileSync(path.join(JS, file), 'utf8');
        assert.ok(src.includes(`activityPanelHtml(${type}`), `${file} draws ${type}`);
    }
    const jobs = fs.readFileSync(path.join(JS, 'jobs.js'), 'utf8');
    for (const type of ["'change_order'", "'job'"]) assert.ok(jobs.includes(`activityPanelHtml(${type}`), type);
    const bills = fs.readFileSync(path.join(JS, 'bills.js'), 'utf8');
    assert.ok(bills.includes("activityPanelHtml('bill_payment'"));
});
