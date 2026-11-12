// Settings → Check Printing → Check memo (#800): Print a test check sends the
// form's Check memo choice, saved or not, so the sample memo follows it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');

function settingsPage(fields) {
    const opened = [];
    const form = {
        querySelector(sel) {
            const m = sel.match(/\[name="([^"]+)"\]/);
            return m && fields[m[1]] !== undefined ? { type: 'select-one', value: fields[m[1]] } : null;
        },
    };
    const context = {
        API: { get: async () => ({}) }, setTimeout() {}, T: v => v,
        Terms: { text: v => v, isNonprofit: () => false, isConstruction: () => false },
        escapeHtml: v => String(v ?? ''),
        toast() {},
        URLSearchParams,
        document: { getElementById: id => (id === 'settings-form' ? form : null), querySelectorAll: () => [] },
        window: { open: (url) => opened.push(url) },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'payment_terms.js'), 'utf8'), context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'settings.js'), 'utf8') + '\nthis.page = SettingsPage;', context);
    return { page: context.page, opened };
}

test('the test check sends the Check memo choice in the form', () => {
    const { page, opened } = settingsPage({ check_stock: 'plain', check_memo: 'none' });
    page.testPrintCheck();
    assert.equal(opened.length, 1);
    const url = new URL(opened[0], 'http://x');
    assert.equal(url.pathname, '/api/checks/test-print');
    assert.equal(url.searchParams.get('check_memo'), 'none');
    assert.equal(url.searchParams.get('check_stock'), 'plain');
});

test('a form without the choice sends no check_memo, so the saved one is used', () => {
    const { page, opened } = settingsPage({ check_stock: 'plain' });
    page.testPrintCheck();
    assert.equal(new URL(opened[0], 'http://x').searchParams.has('check_memo'), false);
});

test('the select offers the three choices with the reference as the default', () => {
    const src = fs.readFileSync(path.join(JS, 'settings.js'), 'utf8');
    const block = src.slice(src.indexOf('id="settings-check-memo"'), src.indexOf('id="settings-check-memo-help"'));
    for (const v of ['reference', 'memo', 'none']) assert.ok(block.includes(`value="${v}"`), v);
    assert.ok(block.includes("!['memo', 'none'].includes(s.check_memo)"));
});
