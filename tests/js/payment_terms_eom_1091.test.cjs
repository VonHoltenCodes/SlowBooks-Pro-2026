// Settings > Payment Terms, the end-of-month kind (#1091): the New/Edit form
// offers it with its one field (days after the end of the month), its
// teaching text behind the tortoise, and a live example whose due date the
// server's parser gives ("Net N EOM"), so the page computes none itself. The
// save sends net_days (and discount_days) for it, and nothing of the other
// kinds.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');

function page({ answer = { due_date: '2027-02-13' } } = {}) {
    const nodes = {};
    const modals = [];
    const asked = [];
    const posted = [];
    const context = {
        T: v => v,
        Terms: { text: v => v, isNonprofit: () => false, isConstruction: () => false },
        escapeHtml: v => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
        formatDate: iso => { const [y, m, d] = iso.split('-'); return `${Number(m)}/${Number(d)}/${y}`; },
        todayISO: () => '2026-11-19',
        toast() {}, closeModal() {}, setTimeout() {},
        openModal: (title, html) => modals.push({ title, html }),
        API: {
            get: async url => { asked.push(url); return answer; },
            post: async (url, body) => { posted.push(['POST', url, body]); return {}; },
            put: async (url, body) => { posted.push(['PUT', url, body]); return {}; },
        },
        document: {
            getElementById: id => nodes[id] || null,
            querySelectorAll: () => [],
        },
    };
    vm.createContext(context);
    for (const f of ['hint.js', 'payment_terms.js', 'settings.js']) {
        vm.runInContext(fs.readFileSync(path.join(JS, f), 'utf8') + (f === 'settings.js' ? '\nthis.page = SettingsPage;' : ''), context);
    }
    return { S: context.page, nodes, modals, asked, posted, context };
}

test('the kind select offers end of month, with its own field and a hint', () => {
    const { S, modals } = page();
    S.editPaymentTerm();
    const html = modals[0].html;
    assert.match(html, /<option value="end_of_month" >End of month: due a number of days after the month ends<\/option>/);
    assert.match(html, /<div class="form-group" data-kind="end_of_month"><label for="pt-eom-days">Due \(days\) after the end of the month <span class="hint-wrap">/);
    assert.match(html, /aria-label="About Days after the end of the month"/);
    assert.ok(html.includes('75 here is "Net 75 EOM". 0 is due on the last day of the month.'));
    assert.match(html, /<input id="pt-eom-days" name="eom_days" type="number" min="0" max="3650" step="1" value="30"/);
    assert.match(html, /id="pt-eom-discount-days" name="eom_discount_days"/);
    assert.match(html, /<p class="form-group full-width" data-kind="end_of_month" id="pt-eom-example" aria-live="polite"/);
    // no raw viewport units in the form
    assert.doesNotMatch(html, /\d(vh|vw|vmin|vmax)\b/);
});

test('an end-of-month term opens on its kind with its own days', () => {
    const { S, modals } = page();
    S._paymentTerms = [{ id: 7, name: 'Net 75 EOM', kind: 'end_of_month', net_days: 75, cutoff_days: 0, discount_percent: '2.00', discount_days: 10, documents: 0 }];
    S.editPaymentTerm(7);
    const html = modals[0].html;
    assert.match(html, /<option value="end_of_month" selected>/);
    assert.match(html, /id="pt-eom-days" name="eom_days"[^>]*value="75"/);
    assert.match(html, /id="pt-eom-discount-days" name="eom_discount_days"[^>]*value="10"/);
});

test('the example is the server\'s due date for "Net N EOM" from today', async () => {
    const { S, nodes, asked } = page();
    nodes['pt-eom-days'] = { value: '75' };
    nodes['pt-eom-example'] = { textContent: '' };
    await S.paymentTermExample();
    assert.equal(asked.length, 1);
    assert.equal(asked[0], '/payment-terms/dates?date=2026-11-19&terms=Net%2075%20EOM&document=invoice');
    assert.equal(nodes['pt-eom-example'].textContent, 'For example, a document dated 11/19/2026 is due 2/13/2027.');
    // a blank or impossible number: no example and no request
    nodes['pt-eom-days'].value = '';
    await S.paymentTermExample();
    assert.equal(nodes['pt-eom-example'].textContent, '');
    nodes['pt-eom-days'].value = '4000';
    await S.paymentTermExample();
    assert.equal(nodes['pt-eom-example'].textContent, '');
    assert.equal(asked.length, 1);
});

function form(values) {
    const f = {};
    for (const [k, v] of Object.entries(values)) f[k] = { value: v };
    return f;
}

test('saving an end-of-month term sends its days and none of the others', async () => {
    const { S, posted } = page();
    S.loadPaymentTerms = () => {};
    await S.savePaymentTerm({
        preventDefault() {},
        target: form({
            name: ' Net 75 EOM ', kind: 'end_of_month', net_days: '30', eom_days: '75',
            due_day: '10', cutoff_days: '5', discount_percent: '2', discount_days: '3',
            eom_discount_days: '10', discount_day: '4',
        }),
    }, null);
    assert.deepEqual(JSON.parse(JSON.stringify(posted[0])), ['POST', '/payment-terms', {
        name: 'Net 75 EOM', kind: 'end_of_month', net_days: 75, due_day: null, cutoff_days: 0,
        discount_percent: '2', discount_days: 10, discount_day: null,
    }]);
});

test('the other kinds save as they did', async () => {
    const { S, posted } = page();
    S.loadPaymentTerms = () => {};
    const base = {
        name: 'T', net_days: '30', eom_days: '75', due_day: '10', cutoff_days: '5',
        discount_percent: '', discount_days: '3', eom_discount_days: '10', discount_day: '4',
    };
    await S.savePaymentTerm({ preventDefault() {}, target: form({ ...base, kind: 'standard' }) }, null);
    await S.savePaymentTerm({ preventDefault() {}, target: form({ ...base, kind: 'date_driven' }) }, null);
    const sent = JSON.parse(JSON.stringify(posted.map(p => p[2])));
    assert.deepEqual(sent[0], { name: 'T', kind: 'standard', net_days: 30, due_day: null, cutoff_days: 0, discount_percent: null, discount_days: null, discount_day: null });
    assert.deepEqual(sent[1], { name: 'T', kind: 'date_driven', net_days: null, due_day: 10, cutoff_days: 5, discount_percent: null, discount_days: null, discount_day: null });
});
