// openModal's onClose (W-131, the 2.31.10 gate): a dialog that asks a
// question hears every way out. closeModal() alone, which × and Escape
// call, answers no; closeModal({ chosen: true }) answers yes; the hook
// runs once and does not outlive its dialog.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');
const JS = path.join(__dirname, '../../app/static/js');

function el() {
    const classes = new Set(['hidden']);
    return {
        classList: {
            add: c => classes.add(c), remove: c => classes.delete(c),
            toggle: (c, on) => { on ? classes.add(c) : classes.delete(c); },
            contains: c => classes.has(c),
        },
        dataset: {}, textContent: '', innerHTML: '',
        querySelectorAll: () => [], querySelector: () => null,
        insertAdjacentHTML() {}, matches: () => false, focus() {}, getAttribute: () => null,
        getClientRects: () => [], closest: () => null,
    };
}
function utils() {
    const nodes = {};
    const $ = sel => (nodes[sel] = nodes[sel] || el());
    const ctx = {
        window: {}, $, $$: () => [], console,
        // utils.js defines its own $ over document.querySelector, so the
        // dialog's nodes come from here, one per selector
        document: { activeElement: null, contains: () => false, addEventListener() {}, getElementById: id => $('#' + id), body: el(), querySelector: $, querySelectorAll: () => [] },
        setTimeout: () => 0, clearTimeout() {}, location: { hash: '#/' },
    };
    ctx.window.document = ctx.document;
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(JS, 'utils.js'), 'utf8') + '\nthis.openModal = openModal; this.closeModal = closeModal;', ctx);
    return ctx;
}

test('closeModal() alone answers no; Keep it on answers yes', () => {
    const u = utils();
    const heard = [];
    u.openModal('Ask', '<p>?</p>', { onClose: kept => heard.push(kept) });
    u.closeModal();
    assert.deepEqual(heard, [false]);
    u.openModal('Ask', '<p>?</p>', { onClose: kept => heard.push(kept) });
    u.closeModal({ chosen: true });
    assert.deepEqual(heard, [false, true]);
});

test('the hook runs once and belongs to its own dialog', () => {
    const u = utils();
    const heard = [];
    u.openModal('Ask', '<p>?</p>', { onClose: kept => heard.push(kept) });
    u.closeModal();
    u.closeModal();                 // already closed: nothing more is heard
    u.openModal('Plain', '<p>.</p>');
    u.closeModal();                 // a dialog with no question asks nothing
    assert.deepEqual(heard, [false]);
});
