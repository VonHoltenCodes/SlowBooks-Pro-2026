// #801: one inventory adjustment of several items. The Items page's Adjust
// Inventory window takes a row per item with + Add line; its rows become
// the request's lines, and a wrong row stops the post with the server's
// own sentence, numbered as the server numbers the lines it is sent.
// Drives the real items.js.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const js = (name) => fs.readFileSync(path.join(__dirname, '../../app/static/js', name), 'utf8');

function itemsPage(extra = {}) {
    const context = {
        console,
        window: {},
        document: { addEventListener() {} },
        escapeHtml: (s) => String(s ?? ''),
        formatCurrency: (v) => '$' + v,
        T: (s) => s,
        ...extra,
    };
    vm.createContext(context);
    vm.runInContext(js('items.js') + '\nthis.ItemsPage = ItemsPage;', context);
    return context.ItemsPage;
}

const ITEMS = [
    { id: 1, name: 'Widget', quantity_on_hand: 10, track_inventory: true },
    { id: 2, name: 'Bolt', quantity_on_hand: 20, track_inventory: true },
    { id: 3, name: 'Gear', quantity_on_hand: 5, track_inventory: true, code: 'G-1' },
];
// the page's values come from another realm: compare them as plain data
const plain = (v) => JSON.parse(JSON.stringify(v));
const row = (o) => ({ item_id: '', mode: 'add', quantity: '', unit_cost: '', memo: '', ...o });

test('each row is a line: add, remove and set to count', () => {
    const page = itemsPage();
    const { lines, problems } = page.adjustLinesFrom([
        row({ item_id: '1', mode: 'add', quantity: '5', unit_cost: '6', memo: 'found' }),
        row({ item_id: '2', mode: 'remove', quantity: '4', unit_cost: '9' }),
        row({ item_id: '3', mode: 'set', quantity: '2' }),
        row({}),  // a blank row is left out
    ], ITEMS);
    assert.deepEqual(plain(problems), []);
    assert.deepEqual(plain(lines), [
        { item_id: 1, quantity_delta: 5, unit_cost: 6, memo: 'found' },
        // a removal goes out at the average: its cost box is not sent
        { item_id: 2, quantity_delta: -4 },
        // a count is sent as the count, so the server takes the change from
        // the count as it stands when it posts
        { item_id: 3, new_quantity: 2 },
    ]);
});

test('the server\'s sentences, numbered as it numbers the lines', () => {
    const page = itemsPage();
    const { lines, problems } = page.adjustLinesFrom([
        row({ item_id: '1', quantity: '5' }),
        row({}),
        row({ item_id: '', quantity: '3' }),
        row({ item_id: '1', quantity: '1' }),
        row({ item_id: '2', quantity: '0' }),
        row({ item_id: '3', mode: 'set', quantity: '5' }),
        row({ item_id: '2', mode: 'set', quantity: '-1' }),
    ], ITEMS);
    assert.equal(lines.length, 1);
    assert.deepEqual(plain(problems), [
        'Line 2: choose an item.',
        'Line 3 (Widget): Widget is on line 1 already; put its whole change on one line.',
        'Line 4 (Bolt): the change is zero; take the line out or enter the change.',
        'Line 5 (Gear): the count is 5 already; take the line out or enter the new count.',
        'Line 6 (Bolt): Bolt is on line 4 already; put its whole change on one line.',
    ]);
});

test('a quantity left out, below zero, or a cost below zero', () => {
    const page = itemsPage();
    const check = (r) => plain(page.adjustLinesFrom([row(r)], ITEMS).problems);
    assert.deepEqual(check({ item_id: '1', quantity: '' }),
        ['Line 1 (Widget): give the change or the new quantity, one of the two.']);
    assert.deepEqual(check({ item_id: '1', mode: 'set', quantity: '-2' }),
        ['Line 1 (Widget): a new quantity cannot be below zero.']);
    assert.deepEqual(check({ item_id: '1', mode: 'remove', quantity: '-2' }),
        ['Line 1 (Widget): a quantity to remove cannot be below zero.']);
    assert.deepEqual(check({ item_id: '1', quantity: '2', unit_cost: '-1' }),
        ['Line 1 (Widget): a unit cost cannot be below zero.']);
    assert.deepEqual(plain(page.adjustLinesFrom([row({}), row({})], ITEMS).problems),
        ['An adjustment needs at least one line: an item and its change or new quantity.']);
});

test('a row offers the tracked items with their counts, and Add line adds one', () => {
    const inserted = [];
    let focused = false;
    const body = {
        insertAdjacentHTML: (where, html) => inserted.push([where, html]),
        querySelectorAll: () => [{ querySelector: () => ({ focus: () => { focused = true; } }) }],
    };
    const page = itemsPage({ document: { addEventListener() {}, querySelector: (s) => (s === '#adjust-many-lines tbody' ? body : null) } });
    page._adjustItems = ITEMS;
    const html = page.adjustRowHtml();
    assert.match(html, /<option value="1">Widget \(10 on hand\)<\/option>/);
    assert.match(html, /<option value="3">G-1 — Gear \(5 on hand\)<\/option>/);
    for (const cls of ['adj-item', 'adj-mode', 'adj-qty', 'adj-cost', 'adj-memo']) assert.match(html, new RegExp(`class="${cls}"`));
    assert.match(html, /<option value="set">Set to count<\/option>/);
    page.addAdjustLine();
    assert.equal(inserted.length, 1);
    assert.equal(inserted[0][0], 'beforeend');
    assert.match(inserted[0][1], /class="adjust-line"/);
    assert.ok(focused, 'the new row\'s item picker takes the focus');
});

test('a row reads back as the plain values the lines are made from', () => {
    const page = itemsPage();
    const cells = { '.adj-item': { value: '2' }, '.adj-mode': { value: 'remove' }, '.adj-qty': { value: ' 3 ' }, '.adj-cost': { value: '' }, '.adj-memo': { value: 'spoiled' } };
    const read = page.readAdjustRow({ querySelector: (s) => cells[s] || null });
    assert.deepEqual(plain(read), { item_id: '2', mode: 'remove', quantity: '3', unit_cost: '', memo: 'spoiled' });
});

test('the window is a write: hidden from a read-only sign-in', () => {
    const app = js('app.js');
    const block = app.slice(app.indexOf('WRITE_ACTIONS:'), app.indexOf('hideWriteControls(root)'));
    assert.match(block, /'ItemsPage\.showAdjustMany'/);
    assert.match(js('items.js'), /onclick="ItemsPage\.showAdjustMany\(\)"/);
});

test('an item\'s history names the adjustment a line was part of', () => {
    assert.match(js('items.js'), /m\.source_type === 'inventory_adjustment' \? 'Inventory adjustment'/);
});

test('an item\'s history opens the adjustment a line was part of, whole', async () => {
    const opened = [];
    const answers = {
        '/items/1': { id: 1, name: 'Widget', quantity_on_hand: 8, avg_cost: 2 },
        '/items/1/movements': [
            { date: '2026-11-12', movement_type: 'adjustment', quantity: -2, unit_cost: 2, balance_qty: 8, balance_avg_cost: 2, source_type: 'inventory_adjustment', source_id: 12, memo: 'count' },
            { date: '2026-11-11', movement_type: 'purchase', quantity: 10, unit_cost: 2, balance_qty: 10, balance_avg_cost: 2, source_type: 'bill', source_id: 4 },
        ],
        '/inventory/adjustments/12': {
            id: 12, date: '2026-11-12', memo: 'Year-end count', line_count: 2, net_amount: -9,
            lines: [
                { line: 1, item_id: 1, item_name: 'Widget', quantity_delta: -2, unit_cost: 2, amount: -4, balance_qty: 8, balance_avg_cost: 2, memo: 'count' },
                { line: 2, item_id: 3, item_name: 'Gear', quantity_delta: -1, unit_cost: 5, amount: -5, balance_qty: 4, balance_avg_cost: 5 },
            ],
        },
    };
    const page = itemsPage({
        API: { get: async (p) => answers[p] },
        openModal: (title, html) => opened.push({ title, html }),
        formatDate: (d) => d,
    });
    await page.showMovements(1);
    const history = opened.pop();
    // the adjustment's name is the way in; another source stays words
    assert.match(history.html, /<button type="button" class="btn btn-sm btn-secondary" onclick="ItemsPage\.showAdjustment\(12, 1\)"[^>]*>Inventory adjustment #12<\/button>/);
    assert.match(history.html, /bill #4/);
    await page.showAdjustment(12, 1);
    const adj = opened.pop();
    assert.equal(adj.title, 'Inventory Adjustment #12');
    assert.match(adj.html, /Year-end count/);
    assert.match(adj.html, /2 items/);
    assert.match(adj.html, /<td>Widget<\/td>/);
    assert.match(adj.html, /<td>Gear<\/td>/);
    assert.match(adj.html, /onclick="ItemsPage\.showMovements\(1\)">Back to History</);
});
