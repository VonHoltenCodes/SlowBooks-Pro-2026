// Line grids let a line move up or down, take a drop, and take a new line
// below any line (#604). The page's rows are stood in for by a small tree.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function module_() {
    const context = { document: {}, setTimeout };
    vm.createContext(context);
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/line_order.js'), 'utf8');
    return vm.runInContext(src + '\n;LineOrder', context);
}

// a table body of rows named by a letter, with the tree calls the module uses
function body(names) {
    const tbody = {
        children: [],
        get lastElementChild() { return this.children[this.children.length - 1] || null; },
        removeChild(node) { this.children = this.children.filter(c => c !== node); },
        insertBefore(node, ref) {
            this.children = this.children.filter(c => c !== node);
            const at = ref ? this.children.indexOf(ref) : this.children.length;
            this.children.splice(at, 0, node);
        },
    };
    const make = name => {
        const row = {
            name, parentNode: tbody, tagName: 'TR',
            get previousElementSibling() { return tbody.children[tbody.children.indexOf(this) - 1] || null; },
            get nextElementSibling() { return tbody.children[tbody.children.indexOf(this) + 1] || null; },
        };
        return row;
    };
    tbody.make = make;
    tbody.children = names.map(make);
    return tbody;
}
const names = tbody => tbody.children.map(r => r.name);

test('a line moves up and down, and stops at the ends', () => {
    const LineOrder = module_();
    const t = body(['a', 'b', 'c']);
    assert.equal(LineOrder.moveRow(t.children[1], -1), true);
    assert.deepEqual(names(t), ['b', 'a', 'c']);
    assert.equal(LineOrder.moveRow(t.children[0], -1), false);
    assert.equal(LineOrder.moveRow(t.children[1], 1), true);
    assert.deepEqual(names(t), ['b', 'c', 'a']);
    assert.equal(LineOrder.moveRow(t.children[2], 1), false);
});

test('a dropped line lands before or after the line it is dropped on', () => {
    const LineOrder = module_();
    const t = body(['a', 'b', 'c', 'd']);
    LineOrder.moveNextTo(t.children[0], t.children[2], true);
    assert.deepEqual(names(t), ['b', 'c', 'a', 'd']);
    LineOrder.moveNextTo(t.children[3], t.children[0], false);
    assert.deepEqual(names(t), ['d', 'b', 'c', 'a']);
    assert.equal(LineOrder.moveNextTo(t.children[0], t.children[0], true), false);
});

test('a new line is slotted in below the line it was asked for', () => {
    const LineOrder = module_();
    const t = body(['a', 'b', 'c']);
    LineOrder._opts.set(t, { add: () => t.children.push(t.make('new')) });
    LineOrder.insertBelow(t, t.children[0]);
    assert.deepEqual(names(t), ['a', 'new', 'b', 'c']);
    LineOrder.insertBelow(t, t.children[3]);
    assert.deepEqual(names(t), ['a', 'new', 'b', 'c', 'new']);
});

test('insert: the focus lands on the new row\'s first control after the picker pass (W-53)', async () => {
    const LineOrder = module_();
    let focused = null;
    const box = { disabled: false, focus() { focused = this; } };
    const row = { querySelector: () => box };
    LineOrder.focusFirst(row);
    assert.equal(focused, null, 'not before the row has been through the observers');
    await new Promise(r => setTimeout(r, 5));
    assert.equal(focused, box);
    LineOrder.focusFirst(null);  // nothing to focus is fine
});

test('a grid with no Remove line of its own gets one, and a removed line hands the keyboard on (NEW-278)', () => {
    const LineOrder = module_();
    assert.match(LineOrder.cellHtml(true), /data-line-order="remove" aria-label="Remove line" title="Remove line"/);
    assert.doesNotMatch(LineOrder.cellHtml(), /data-line-order="remove"/);
    const t = body(['a', 'b', 'c']);
    assert.equal(LineOrder.removeRow(t.children[1]).name, 'c');
    assert.deepEqual(names(t), ['a', 'c']);
    // the last row hands it to the one above
    assert.equal(LineOrder.removeRow(t.children[1]).name, 'a');
    assert.deepEqual(names(t), ['a']);
});

// A row that belongs to the line above it (the nonprofit Split chooser,
// data-line-part) is not a line: it has no place, moves with its line, and
// goes when its line is removed.
test('a row that belongs to a line moves and goes with it, and is not counted', () => {
    const LineOrder = module_();
    const t = body(['a', 'a-part', 'b', 'c']);
    t.children[1].hasAttribute = name => name === 'data-line-part';
    const row = name => t.children.find(r => r.name === name);
    assert.deepEqual(Array.from(LineOrder.rows(t), r => r.name), ['a', 'b', 'c']);
    assert.equal(LineOrder.lineOf(row('a-part')).name, 'a');
    assert.equal(LineOrder.moveRow(row('a'), 1), true);
    assert.deepEqual(names(t), ['b', 'a', 'a-part', 'c']);
    assert.equal(LineOrder.moveRow(row('c'), -1), true);
    assert.deepEqual(names(t), ['b', 'c', 'a', 'a-part']);
    assert.equal(LineOrder.moveRow(row('a'), 1), false);
    // a new line goes under the line and what belongs to it
    LineOrder._opts.set(t, { add: () => t.children.push(t.make('new')) });
    LineOrder.insertBelow(t, row('c'));
    assert.deepEqual(names(t), ['b', 'c', 'new', 'a', 'a-part']);
    // a drop on the part lands after its line; a drop of the line takes the part along
    LineOrder.moveNextTo(row('b'), row('a-part'), false);
    assert.deepEqual(names(t), ['c', 'new', 'a', 'a-part', 'b']);
    LineOrder.moveNextTo(row('a'), row('c'), false);
    assert.deepEqual(names(t), ['a', 'a-part', 'c', 'new', 'b']);
    assert.equal(LineOrder.moveNextTo(row('a'), row('a-part'), true), false);
    // removed with its line; the line below takes the place
    assert.equal(LineOrder.removeRow(row('a')).name, 'c');
    assert.deepEqual(names(t), ['c', 'new', 'b']);
});
