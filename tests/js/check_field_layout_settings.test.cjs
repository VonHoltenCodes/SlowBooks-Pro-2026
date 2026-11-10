// Settings → Payroll and checks → Check Printing → Field layout (#749):
// a table with a row per field of the check face (payee, payee address,
// date, amount in figures, amount in words, memo) and columns Left/right,
// Up/down, Size and Bold. Its inputs carry no name; what they hold is kept
// in the hidden check_field_layout field as the JSON the server stores,
// only what differs from today's face, which the form saves and the test
// print sends. Reset empties it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');

function settingsPage(document = { getElementById: () => null, querySelectorAll: () => [] }) {
    const context = {
        API: { get: async () => ({}) }, setTimeout() {}, T: v => v,
        Terms: { text: v => v, isNonprofit: () => false, isConstruction: () => false },
        escapeHtml: v => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
        toast() {}, document,
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'payment_terms.js'), 'utf8'), context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'settings.js'), 'utf8') + '\nthis.page = SettingsPage;', context);
    return context.page;
}

const FIELDS = [
    ['payee', 'Payee'],
    ['payee_address', 'Payee address'],
    ['date', 'Date'],
    ['amount', 'Amount in figures'],
    ['amount_words', 'Amount in words'],
    ['memo', 'Memo'],
];

function tag(html, id) {
    const m = html.match(new RegExp(`<input[^>]*id="${id}"[^>]*>`));
    assert.ok(m, `no input #${id}`);
    return m[0];
}

test('the table has a labelled row per field, with real column headers', () => {
    const html = settingsPage()._checkFieldLayoutHtml('');
    assert.match(html, /<h4 id="settings-check-fields-h"[^>]*>Field layout<\/h4>/);
    assert.match(html, /role="group" aria-labelledby="settings-check-fields-h"/);
    for (const head of ['Field', 'Left/right (in)', 'Up/down (in)', 'Size (pt)', 'Bold']) {
        assert.ok(html.includes(`<th scope="col">${head}</th>`), head);
    }
    for (const [key, label] of FIELDS) {
        const id = 'check-field-' + key.replace(/_/g, '-');
        assert.ok(html.includes(`<th scope="row" id="${id}-h"`), key);
        const x = tag(html, `${id}-x`);
        assert.match(x, new RegExp(`aria-label="${label} left/right offset \\(in\\)"`));
        assert.match(x, /min="-1" max="1" step="0\.01"/);
        assert.match(tag(html, `${id}-y`), new RegExp(`aria-label="${label} up/down offset \\(in\\)"`));
        const size = tag(html, `${id}-size`);
        assert.match(size, new RegExp(`aria-label="${label} text size \\(pt\\)`));
        assert.match(size, /min="6" max="18" step="0\.1"/);
        assert.match(tag(html, `${id}-bold`), new RegExp(`aria-label="${label} bold"`));
        // locked, value shown, for anyone but an administrator; never a form field
        for (const part of ['x', 'y', 'size', 'bold']) {
            const t = tag(html, `${id}-${part}`);
            assert.match(t, /data-admin/);
            assert.doesNotMatch(t, / name="/);
        }
    }
    assert.match(html, /<input type="hidden" name="check_field_layout" id="settings-check-field-layout" value="">/);
    assert.match(html, /onclick="SettingsPage\.resetCheckFieldLayout\(\)">Reset field layout to defaults<\/button>/);
});

test("with nothing saved every box shows today's face: blank, bold where it is bold today", () => {
    const html = settingsPage()._checkFieldLayoutHtml('');
    for (const [key] of FIELDS) {
        const id = 'check-field-' + key.replace(/_/g, '-');
        assert.match(tag(html, `${id}-x`), /value=""/);
        assert.match(tag(html, `${id}-size`), /value=""/);
        const bold = tag(html, `${id}-bold`);
        const today = key === 'payee' || key === 'amount';
        assert.equal(/ checked/.test(bold), today, key);
        assert.match(bold, new RegExp(`data-default="${today}"`));
    }
    // the size it prints at today, as the hint
    assert.match(tag(html, 'check-field-amount-size'), /placeholder="14"/);
    assert.match(tag(html, 'check-field-memo-size'), /placeholder="8"/);
});

test('a saved layout fills the boxes and the hidden field', () => {
    const saved = '{"date":{"bold":true,"size":12.5},"payee":{"bold":false,"x":0.25,"y":-0.1}}';
    const html = settingsPage()._checkFieldLayoutHtml(saved);
    assert.match(tag(html, 'check-field-payee-x'), /value="0.25"/);
    assert.match(tag(html, 'check-field-payee-y'), /value="-0.1"/);
    assert.doesNotMatch(tag(html, 'check-field-payee-bold'), / checked/);
    assert.match(tag(html, 'check-field-date-size'), /value="12.5"/);
    assert.match(tag(html, 'check-field-date-bold'), / checked/);
    assert.ok(html.includes(`value="${saved.replace(/"/g, '&quot;')}"`));
    // a damaged value draws today's face
    assert.match(settingsPage()._checkFieldLayoutHtml('garbage'), /id="settings-check-field-layout" value=""/);
});

// the table's inputs as the page has them, for sync and reset
function fakeTable(values) {
    const els = [];
    for (const [key] of FIELDS) {
        for (const attr of ['x', 'y', 'size', 'bold']) {
            const v = (values[key] || {})[attr];
            const el = {
                attrs: { 'data-check-field': key, 'data-attr': attr },
                getAttribute(n) { return this.attrs[n]; },
            };
            if (attr === 'bold') {
                const today = key === 'payee' || key === 'amount';
                el.attrs['data-default'] = String(today);
                el.checked = v === undefined ? today : v;
            } else {
                el.value = v === undefined ? '' : String(v);
            }
            els.push(el);
        }
    }
    const hidden = { value: 'stale' };
    const document = {
        getElementById: id => (id === 'settings-check-field-layout' ? hidden : null),
        querySelectorAll: sel => (sel === '#settings-check-fields [data-check-field]' ? els : []),
    };
    return { els, hidden, document };
}

test('the boxes are kept in the hidden field as only what differs from today', () => {
    const t = fakeTable({ memo: { y: '-0.2', size: '10' }, payee: { bold: false, x: '0' }, amount: { bold: true } });
    const page = settingsPage(t.document);
    page.syncCheckFieldLayout();
    assert.equal(t.hidden.value, '{"memo":{"size":10,"y":-0.2},"payee":{"bold":false}}');
    // nothing set: empty, which the server stores as today's face
    const none = fakeTable({});
    settingsPage(none.document).syncCheckFieldLayout();
    assert.equal(none.hidden.value, '');
});

test('Reset empties every box, puts Bold back to today and empties the hidden field', () => {
    const t = fakeTable({ memo: { y: '-0.2', bold: true }, payee: { bold: false }, date: { size: '14' } });
    const page = settingsPage(t.document);
    let dirty = 0;
    page._updateDirty = () => { dirty += 1; };
    page.resetCheckFieldLayout();
    assert.equal(t.hidden.value, '');
    assert.equal(dirty, 1);
    for (const el of t.els) {
        if (el.attrs['data-attr'] === 'bold') assert.equal(el.checked, el.attrs['data-default'] === 'true');
        else assert.equal(el.value, '');
    }
});

test('the test print sends the layout with the other check settings', () => {
    const src = fs.readFileSync(path.join(JS, 'settings.js'), 'utf8');
    assert.ok(src.includes("'check_blank_stock', 'check_micr_amount', 'check_field_layout']"));
});
