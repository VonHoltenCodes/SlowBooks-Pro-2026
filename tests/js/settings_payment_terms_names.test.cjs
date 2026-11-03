// The terms list in Settings (#664): every row's Edit and Make Inactive /
// Make Active button is named for its own term, so a screen reader's
// button list says which row each one acts on.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function fixture(terms) {
    const list = { innerHTML: '' };
    const context = {
        API: { get: async () => terms },
        $: selector => (selector === '#payment-term-list' ? list : null),
        T: value => value,
        Terms: { text: value => value, isNonprofit: () => false, isConstruction: () => false },
        escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, character => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[character]),
        document: { getElementById: () => null },
        setTimeout() {},
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/settings.js'), 'utf8') + '\nthis.page = SettingsPage;', context);
    return { page: context.page, list };
}

const labels = html => [...html.matchAll(/<button[^>]*aria-label="([^"]*)"[^>]*>([^<]*)<\/button>/g)].map(m => [m[2], m[1]]);

test('each row\'s buttons are named for its term', async () => {
    const f = fixture([
        { id: 1, name: 'Net 30', description: 'Due in 30 days', documents: 0, elsewhere: 0, is_active: true },
        { id: 2, name: '2% 10 "Net" 30', description: '', documents: 3, elsewhere: 1, is_active: false },
    ]);
    await f.page.loadPaymentTerms();
    assert.deepEqual(labels(f.list.innerHTML), [
        ['Edit', 'Edit Net 30'],
        ['Make Inactive', 'Make Net 30 inactive'],
        ['Edit', 'Edit 2% 10 &quot;Net&quot; 30'],
        ['Make Active', 'Make 2% 10 &quot;Net&quot; 30 active'],
    ]);
});
