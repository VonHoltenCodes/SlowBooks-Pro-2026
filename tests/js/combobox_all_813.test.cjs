// The type-ahead's blank choice (#813): a report picker's first entry,
// "All" or "All classes", is the whole list and is found by typing it;
// a "Select..." blank is no choice and is never a match.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load() {
    const context = { console, escapeHtml: (s) => String(s ?? '') };
    vm.createContext(context);
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/combobox.js'), 'utf8');
    vm.runInContext(src + '\nthis.ComboBox = ComboBox;', context);
    return context.ComboBox;
}

const opt = (value, text) => ({
    value, textContent: text, disabled: false, hidden: false, style: {},
    classList: { contains: () => false }, parentElement: null, hasAttribute: () => false,
});

function box(options) {
    const ComboBox = load();
    const me = { sel: { options }, _items: null };
    me.items = () => ComboBox.prototype.items.call(me);
    return (q) => ComboBox.prototype.match.call(me, q).list.map(it => it.text);
}

test('typing "All" finds a report picker\'s All entry', () => {
    const match = box([opt('', 'All'), opt('1', 'Bakery'), opt('2', 'Catering'), opt('3', 'Wall art')]);
    assert.deepEqual([...match('All')], ['All', 'Wall art']);
    assert.deepEqual([...match('al')], ['All', 'Wall art']);
});

test('"All classes" is found by its words too', () => {
    const match = box([opt('', 'All classes'), opt('1', 'Bakery')]);
    assert.deepEqual([...match('all cl')], ['All classes']);
});

test('a "Select..." blank is still no match', () => {
    const match = box([opt('', 'Select...'), opt('1', 'Select Foods')]);
    assert.deepEqual([...match('sel')], ['Select Foods']);
});
