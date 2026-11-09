// Splitting a stored bill line by an allocation rule (nonprofit) makes one
// row per share. Each share was a copy of the row, data-line-id and all, so
// saving the edit sent the line's id once per share and the server refused
// it ("Line id N is sent twice."). The first share keeps the stored line's
// id; the others are new lines and carry none. Drives the real splitApply.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function loadBillsPage() {
    const context = {
        console,
        window: {},
        document: { addEventListener() {} },
        Nonprofit: { label: (fn) => fn },
        escapeHtml: (s) => String(s ?? ''),
    };
    vm.createContext(context);
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../../app/static/js/bills.js'), 'utf8') +
            '\nthis.BillsPage = BillsPage;',
        context,
    );
    return context.BillsPage;
}

// A grid row, just enough of one for splitApply: its inputs, its dataset,
// cloneNode copying both, and the rows inserted after it.
function fakeRow(dataset, inserted) {
    const inputs = {
        '.line-desc': { value: 'Electric' },
        '.line-qty': { value: '' },
        '.line-rate': { value: '' },
    };
    return {
        dataset: { ...dataset },
        inputs,
        querySelector(sel) { return inputs[sel] || null; },
        querySelectorAll() { return []; },
        cloneNode() {
            const copy = fakeRow(this.dataset, inserted);
            for (const k of Object.keys(inputs)) copy.inputs[k].value = inputs[k].value;
            return copy;
        },
        insertAdjacentElement(_where, el) { inserted.push(el); },
        remove() { this.removed = true; },
    };
}

function split(lineId) {
    const BP = loadBillsPage();
    BP.recalc = () => 0;
    BP.lineCount = 1;
    const inserted = [];
    const row = fakeRow({ billline: '0', lineId, poLine: '' }, inserted);
    BP.splitApply(row, {
        rule_name: 'Utilities',
        lines: [
            { function: 'program', amount: 60 },
            { function: 'management', amount: 30 },
            { function: 'fundraising', amount: 10 },
        ],
    });
    assert.equal(row.removed, true);
    return inserted;
}

test('a split stored line keeps its id on the first share only', () => {
    const shares = split('42');
    assert.deepEqual(shares.map((r) => r.dataset.lineId), ['42', '', '']);
    // what collectLines would send for each: one id, never the same one twice
    const ids = shares.filter((r) => r.dataset.lineId).map((r) => parseInt(r.dataset.lineId));
    assert.deepEqual(ids, [42]);
    assert.deepEqual(shares.map((r) => r.inputs['.line-rate'].value), ['60.00', '30.00', '10.00']);
});

test('a split new line sends no id on any share', () => {
    const shares = split('');
    assert.deepEqual(shares.map((r) => r.dataset.lineId), ['', '', '']);
});
