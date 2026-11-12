// Settings → Payroll and checks → Check Printing → Deposit slips (#798):
// a Deposit slip stock choice (plain paper or the bank's pre-printed
// slips), a left/right and up/down nudge, and a layout table with a row per
// figure (date, account, cash, the first check line's number and amount,
// the checks subtotal, the total) and columns Left, Top, Size and Print,
// plus the slip's number of check lines and their spacing. The table's
// inputs carry no name; what they hold is kept in the hidden
// deposit_slip_layout field as the JSON the server stores, only what
// differs from the defaults, which the form saves and the test slip sends.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');

function settingsPage(document = { getElementById: () => null, querySelectorAll: () => [] }, extra = {}) {
    const context = {
        API: { get: async () => ({}) }, setTimeout() {}, T: v => v,
        Terms: { text: v => v, isNonprofit: () => false, isConstruction: () => false },
        escapeHtml: v => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
        toast() {}, document, ...extra,
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'payment_terms.js'), 'utf8'), context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'settings.js'), 'utf8') + '\nthis.page = SettingsPage;', context);
    return context.page;
}

// [key, label, default x, default y]
const FIELDS = [
    ['date', 'Date', '1', '0.9'],
    ['account', 'Account', '1', '0.55'],
    ['cash', 'Cash', '7.9', '0.55'],
    ['check_number', 'Check number (first line)', '4.9', '0.85'],
    ['check_amount', 'Check amount (first line)', '7.9', '0.85'],
    ['checks_subtotal', 'Checks subtotal', '7.9', '2.9'],
    ['total', 'Total', '7.9', '3.2'],
];
const AMOUNTS = new Set(['cash', 'check_amount', 'checks_subtotal', 'total']);
const idOf = key => 'deposit-slip-' + key.replace(/_/g, '-');

function tag(html, id) {
    const m = html.match(new RegExp(`<input[^>]*id="${id}"[^>]*>`));
    assert.ok(m, `no input #${id}`);
    return m[0];
}

test('the block offers the stock, the nudges, a labelled row per figure and the test slip', () => {
    const html = settingsPage()._depositSlipHtml({});
    assert.match(html, /<h4 id="settings-deposit-slip-h"[^>]*>Deposit slips<\/h4>/);
    assert.match(html, /role="group" aria-labelledby="settings-deposit-slip-h"/);
    // plain paper is the default
    assert.match(html, /<label for="settings-deposit-slip-stock">Deposit slip stock<\/label>/);
    assert.match(html, /<select name="deposit_slip_stock" id="settings-deposit-slip-stock"/);
    assert.match(html, /<option value="plain" selected>/);
    assert.match(html, /<option value="preprinted" >/);
    assert.match(tag(html, 'settings-deposit-slip-nudge-x'), /name="deposit_slip_nudge_x_in"[^>]*min="-0\.5" max="0\.5" step="0\.01"/);
    assert.match(tag(html, 'settings-deposit-slip-nudge-y'), /name="deposit_slip_nudge_y_in"/);
    for (const head of ['Figure', 'Left (in)', 'Top (in)', 'Size (pt)', 'Print']) {
        assert.ok(html.includes(`<th scope="col">${head}</th>`), head);
    }
    for (const [key, label, x, y] of FIELDS) {
        const id = idOf(key);
        assert.ok(html.includes(`<th scope="row" id="${id}-h"`), key);
        const where = AMOUNTS.has(key) ? 'right end' : 'left edge';
        const xs = tag(html, `${id}-x`);
        assert.ok(xs.includes(`aria-label="${label}: ${where}, inches from the left"`), key);
        assert.match(xs, /min="0" max="11" step="0\.01"/);
        // the default place is shown, and kept to compare against
        assert.ok(xs.includes(`value="${x}"`) && xs.includes(`data-default="${x}"`), key);
        const ys = tag(html, `${id}-y`);
        assert.ok(ys.includes(`value="${y}"`) && ys.includes(`aria-label="${label}: inches from the top"`), key);
        assert.match(tag(html, `${id}-size`), /min="6" max="18" step="0\.1"[^>]*value="10"/);
        assert.match(tag(html, `${id}-show`), / checked/);
        // locked, value shown, for anyone but an administrator; never a form field
        for (const part of ['x', 'y', 'size', 'show']) {
            const t = tag(html, `${id}-${part}`);
            assert.match(t, /data-admin/);
            assert.doesNotMatch(t, / name="/);
        }
    }
    assert.match(tag(html, 'deposit-slip-lines-count'), /min="1" max="30" step="1"[^>]*value="8"/);
    assert.match(tag(html, 'deposit-slip-lines-spacing'), /min="0\.1" max="1" step="0\.01"[^>]*value="0.25"/);
    assert.match(html, /<input type="hidden" name="deposit_slip_layout" id="settings-deposit-slip-layout" value="">/);
    assert.match(html, /onclick="SettingsPage\.resetDepositSlipLayout\(\)">Reset deposit slip layout to defaults<\/button>/);
    assert.match(html, /onclick="SettingsPage\.testPrintDepositSlip\(\)">Print a test slip<\/button>/);
});

test('a saved stock, nudges and layout fill the block', () => {
    const saved = '{"account":{"show":false},"check_lines":{"count":6},"date":{"x":1.25},"total":{"size":12}}';
    const html = settingsPage()._depositSlipHtml({
        deposit_slip_stock: 'preprinted', deposit_slip_nudge_x_in: '0.1', deposit_slip_nudge_y_in: '-0.05',
        deposit_slip_layout: saved,
    });
    assert.match(html, /<option value="preprinted" selected>/);
    assert.match(tag(html, 'settings-deposit-slip-nudge-x'), /value="0.1"/);
    assert.match(tag(html, 'settings-deposit-slip-nudge-y'), /value="-0.05"/);
    assert.match(tag(html, 'deposit-slip-date-x'), /value="1.25"/);
    assert.match(tag(html, 'deposit-slip-date-y'), /value="0.9"/);
    assert.match(tag(html, 'deposit-slip-total-size'), /value="12"/);
    assert.doesNotMatch(tag(html, 'deposit-slip-account-show'), / checked/);
    assert.match(tag(html, 'deposit-slip-lines-count'), /value="6"/);
    assert.ok(html.includes(`value="${saved.replace(/"/g, '&quot;')}"`));
    // a damaged value draws the defaults
    assert.match(settingsPage()._depositSlipHtml({ deposit_slip_layout: 'garbage' }), /id="settings-deposit-slip-layout" value=""/);
});

// the table's inputs as the page has them, for sync and reset
function fakeTable(values) {
    const els = [];
    const lines = [];
    for (const [key, , x, y] of FIELDS) {
        const defaults = { x, y, size: '10' };
        for (const attr of ['x', 'y', 'size', 'show']) {
            const v = (values[key] || {})[attr];
            const el = {
                attrs: { 'data-slip-field': key, 'data-attr': attr },
                getAttribute(n) { return this.attrs[n]; },
            };
            if (attr === 'show') {
                el.checked = v === undefined ? true : v;
            } else {
                el.attrs['data-default'] = defaults[attr];
                el.value = v === undefined ? defaults[attr] : String(v);
            }
            els.push(el);
        }
    }
    for (const [attr, d] of [['count', '8'], ['spacing', '0.25']]) {
        const v = (values.check_lines || {})[attr];
        lines.push({
            attrs: { 'data-slip-lines': attr, 'data-default': d },
            getAttribute(n) { return this.attrs[n]; },
            value: v === undefined ? d : String(v),
        });
    }
    const hidden = { value: 'stale' };
    const document = {
        getElementById: id => (id === 'settings-deposit-slip-layout' ? hidden : null),
        querySelectorAll: sel => {
            if (sel === '#settings-deposit-slip-fields [data-slip-field]') return els;
            if (sel === '#settings-deposit-slip-fields [data-slip-lines]') return lines;
            if (sel === '#settings-deposit-slip-fields [data-slip-field], #settings-deposit-slip-fields [data-slip-lines]') return [...els, ...lines];
            return [];
        },
    };
    return { els, lines, hidden, document };
}

test('the boxes are kept in the hidden field as only what differs from the defaults', () => {
    const t = fakeTable({
        date: { x: '1.25', y: '0.90' }, total: { size: '12' }, account: { show: false },
        cash: { x: '' }, check_lines: { count: '6', spacing: '0.25' },
    });
    settingsPage(t.document).syncDepositSlipLayout();
    assert.equal(t.hidden.value, '{"account":{"show":false},"check_lines":{"count":6},"date":{"x":1.25},"total":{"size":12}}');
    // what is not a number is sent as typed, so saving says why
    const bad = fakeTable({ date: { x: 'abc' } });
    settingsPage(bad.document).syncDepositSlipLayout();
    assert.equal(bad.hidden.value, '{"date":{"x":"abc"}}');
    // every default: empty, which the server stores as the defaults
    const none = fakeTable({});
    settingsPage(none.document).syncDepositSlipLayout();
    assert.equal(none.hidden.value, '');
});

test('Reset puts every box back at its default, Print on, and empties the hidden field', () => {
    const t = fakeTable({ date: { x: '3' }, total: { show: false, size: '14' }, check_lines: { count: '2' } });
    const page = settingsPage(t.document);
    let dirty = 0;
    page._updateDirty = () => { dirty += 1; };
    page.resetDepositSlipLayout();
    assert.equal(t.hidden.value, '');
    assert.equal(dirty, 1);
    for (const el of [...t.els, ...t.lines]) {
        if (el.attrs['data-attr'] === 'show') assert.equal(el.checked, true);
        else assert.equal(el.value, el.attrs['data-default']);
    }
});

test('the test slip opens with the layout and nudges as they stand in the form', () => {
    const fields = {
        deposit_slip_layout: { value: '{"total":{"x":6}}' },
        deposit_slip_nudge_x_in: { value: '0.1' },
        deposit_slip_nudge_y_in: { value: '0' },
    };
    const form = { querySelector: sel => fields[sel.match(/name="(\w+)"/)[1]] || null };
    const opened = [];
    const document = { getElementById: id => (id === 'settings-form' ? form : null), querySelectorAll: () => [] };
    const page = settingsPage(document, { window: { open: (url, target) => opened.push([url, target]) }, URLSearchParams });
    page.testPrintDepositSlip();
    assert.equal(opened.length, 1);
    const [url, target] = opened[0];
    assert.equal(target, '_blank');
    assert.ok(url.startsWith('/api/deposits/test-slip?'), url);
    const q = new URLSearchParams(url.split('?')[1]);
    assert.equal(q.get('deposit_slip_layout'), '{"total":{"x":6}}');
    assert.equal(q.get('deposit_slip_nudge_x_in'), '0.1');
    assert.equal(q.get('deposit_slip_nudge_y_in'), '0');
});

test('the block sits in Check Printing, after the test check', () => {
    const src = fs.readFileSync(path.join(JS, 'settings.js'), 'utf8');
    const section = src.slice(src.indexOf('id="settings-h-check-printing"'), src.indexOf('id="settings-h-backup-restore"'));
    assert.ok(section.indexOf('Print a test check') < section.indexOf('${SettingsPage._depositSlipHtml(s)}'));
});
