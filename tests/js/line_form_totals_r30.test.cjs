// Gate round 30, three line-form lows:
//  - a line field's name is its heading's words without the required-field
//    mark ("Account *" names "Account, line 2"), and the field says it is
//    required instead (headingName in utils.js);
//  - a field ticket's $0.00 question follows the totals its footer shows
//    (FieldTickets.totals): a blank rate nobody fills is 0, a blank rate the
//    server fills on save is "Set on save", never $0.00;
//  - the in-kind gift's running total, its question and the gift it posts
//    read a line the same way (InKindPage.lineValues): a blank quantity is 1.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');
const read = (f) => fs.readFileSync(path.join(JS, f), 'utf8');
const money = (v) => `$${(Number(v) || 0).toFixed(2)}`;

function utils() {
    const ctx = {
        window: {}, App: { settings: {} }, document: { addEventListener() {} },
        Event: class { constructor(type) { this.type = type; } },
        confirm: (q) => ctx.confirmWith(q),
    };
    vm.createContext(ctx);
    vm.runInContext(read('utils.js') + '\nthis.headingName = headingName; this.ZeroTotal = ZeroTotal;', ctx);
    return ctx;
}

test('a heading names its fields without the required-field mark', () => {
    const { headingName } = utils();
    const table = [
        ['Account *', 'Account', true],
        ['Account*', 'Account', true],
        ['  Fair value\n (each)  * ', 'Fair value (each)', true],
        ['Account', 'Account', false],
        ['Qty * rate', 'Qty * rate', false],  // a star inside the words is words
        ['*', '', true],
        ['', '', false],
    ];
    for (const [text, name, required] of table) {
        assert.deepEqual({ ...headingName(text) }, { name, required }, JSON.stringify(text));
    }
});

function fieldTickets() {
    const u = utils();
    const ctx = {
        console, ZeroTotal: u.ZeroTotal, formatCurrency: money,
        escapeHtml: (s) => String(s ?? ''), $: () => null, $$: () => [],
    };
    vm.createContext(ctx);
    vm.runInContext(read('field_tickets.js') + '\nthis.FieldTickets = FieldTickets;', ctx);
    const F = ctx.FieldTickets;
    F._employees = [{ id: 1, first_name: 'Ada', last_name: 'Mason', pay_rate: 0 }];
    F._equipment = [{ id: 2, name: 'Skid steer', hourly_rate: '85.00' }, { id: 3, name: 'Hand cart', hourly_rate: '0.00' }];
    F._items = [
        { id: 4, name: 'Lumber', cost: '6.50', avg_cost: '0' },
        { id: 5, name: 'Free sample', cost: '0.00', avg_cost: '0.00' },
        { id: 6, name: 'Averaged', cost: '0.00', avg_cost: '2.25' },
    ];
    return F;
}

const line = (qty, cost, bill, type = 'labor', who = '') => ({ qty, cost, bill, type, who });

test('a field ticket line: what is typed, 0 where nothing fills a blank rate, null where the save does', () => {
    const F = fieldTickets();
    const fig = (kind, l) => ({ ...F.lineFigures(kind, l) });
    // the reported case: hours typed, both rates blank, nobody picked
    assert.deepEqual(fig('tm', line('8', '', '')), { cost: 0, bill: 0 });
    // rates typed
    assert.deepEqual(fig('tm', line('8', '40', '65')), { cost: 320, bill: 520 });
    assert.deepEqual(fig('tm', line('8', '0', '0')), { cost: 0, bill: 0 });
    // the bill rate left for the save: the cost rate with the job's markup
    assert.deepEqual(fig('tm', line('8', '40', '')), { cost: 320, bill: null });
    assert.deepEqual(fig('tm', line('8', '0', '')), { cost: 0, bill: 0 });
    // the cost rate left for the save: the employee's, the equipment's, the item's
    assert.deepEqual(fig('tm', line('8', '', '', 'labor', 'emp:1')), { cost: null, bill: null });
    assert.deepEqual(fig('tm', line('8', '', '65', 'labor', 'emp:1')), { cost: null, bill: 520 });
    assert.deepEqual(fig('tm', line('2', '', '', 'equipment', 'eq:2')), { cost: null, bill: null });
    assert.deepEqual(fig('tm', line('2', '', '', 'material', 'item:4')), { cost: null, bill: null });
    assert.deepEqual(fig('tm', line('2', '', '', 'material', 'item:6')), { cost: null, bill: null });
    assert.deepEqual(fig('material', line('2', '', undefined, 'material', 'item:4')), { cost: null, bill: null });
    // a default the list shows as 0, or a pick the line's type takes no rate from
    assert.deepEqual(fig('tm', line('2', '', '', 'equipment', 'eq:3')), { cost: 0, bill: 0 });
    assert.deepEqual(fig('tm', line('2', '', '', 'material', 'item:5')), { cost: 0, bill: 0 });
    assert.deepEqual(fig('tm', line('2', '', '', 'labor', 'eq:2')), { cost: 0, bill: 0 });
    assert.deepEqual(fig('tm', line('2', '', '', 'equipment', 'emp:1')), { cost: 0, bill: 0 });
    // an item fills any line's rate, as the server reads it
    assert.deepEqual(fig('tm', line('2', '', '', 'other', 'item:4')), { cost: null, bill: null });
    // no hours: nothing to multiply, whoever is picked
    assert.deepEqual(fig('tm', line('', '', '', 'labor', 'emp:1')), { cost: 0, bill: 0 });
    assert.deepEqual(fig('tm', line('0', '40', '', 'labor', 'emp:1')), { cost: 0, bill: 0 });
});

test('the field ticket question follows the totals the footer shows', () => {
    const F = fieldTickets();
    const t = (kind, lines) => F.totals(kind, lines);
    assert.equal(t('tm', []).zero, true);
    assert.equal(t('tm', [line('8', '', '')]).zero, true);  // Total $0.00 $0.00: asked
    assert.equal(t('tm', [line('8', '0', '0'), line('', '40', '65')]).zero, true);
    assert.equal(t('tm', [line('5', '10', '10'), line('-5', '10', '10')]).zero, true);
    assert.equal(t('tm', [line('8', '40', '65')]).zero, false);
    assert.equal(t('tm', [line('8', '0', '65')]).zero, false);  // a bill total shows
    assert.equal(t('tm', [line('8', '40', '0')]).zero, false);  // a cost total shows
    assert.equal(t('tm', [line('8', '', '', 'labor', 'emp:1')]).zero, false);  // "Set on save"
    assert.equal(t('material', [line('2', '', undefined, 'material', '')]).zero, true);
    assert.equal(t('material', [line('2', '6.5', undefined, 'material', '')]).zero, false);
    const mixed = t('tm', [line('8', '40', ''), line('2', '10', '15')]);
    assert.deepEqual(
        [mixed.cost, mixed.bill, mixed.costPending, mixed.billPending],
        [340, 30, false, true],
    );
    assert.equal(F._totalText(mixed.cost, mixed.costPending), '$340.00');
    assert.equal(F._totalText(mixed.bill, mixed.billPending), '$30.00 + set on save');
    assert.equal(F._totalText(0, true), 'Set on save');
    assert.equal(F._totalText(0, false), '$0.00');
});

function inKind(rows, answer = true) {
    const asked = [], posts = [];
    const total = { textContent: '' };
    const ctx = {
        console, window: {}, formatCurrency: money,
        $: (sel) => (sel === '#ik-total' ? total : null),
        $$: (sel) => (sel === '#ik-lines tr' ? rows : []),
        TxnWarn: { ok: async () => true }, ClassWarn: { ok: async () => true },
        classIdFromForm: () => null, jobIdFromForm: () => null,
        API: { post: async (url, body) => { posts.push(body); return { number: 'IK-0001' }; } },
        toastPosted() {}, toast() {}, closeModal() {}, App: { navigate() {} },
    };
    const u = utils();
    u.confirmWith = (q) => { asked.push(q); return answer; };
    ctx.ZeroTotal = u.ZeroTotal;
    vm.createContext(ctx);
    vm.runInContext(read('in_kind.js'), ctx);
    return { P: ctx.window.InKindPage, asked, posts, total };
}

function giftRow(qty, fv) {
    const els = {
        '.ik-desc': { value: 'Upright piano' }, '.ik-qty': { value: qty }, '.ik-fv': { value: fv },
        '.ik-debit': { value: '7' }, '.ik-amount': { textContent: '' },
    };
    return { querySelector: (sel) => els[sel] || null, els };
}

const giftForm = { customer_id: { value: '3' }, date: { value: '2026-11-20' }, memo: { value: '' } };

test('the in-kind total on the screen is the gift that posts, for every reading of the boxes', async () => {
    const table = [
        // quantity box, fair value box, the total shown, asked?
        ['', '500', 500, false],     // the reported case: the box cleared
        ['abc', '500', 500, false],  // not a number: 1, as it always posted
        ['1', '500', 500, false],
        ['3', '12.50', 37.5, false],
        ['2', '', 0, true],
        ['', '', 0, true],
        ['', '0', 0, true],
    ];
    for (const [qty, fv, shown, ask] of table) {
        const row = giftRow(qty, fv);
        const { P, asked, posts, total } = inKind([row]);
        assert.equal(P.recalc(), shown, `${qty}|${fv}`);
        assert.equal(total.textContent, `Total: ${money(shown)}`);
        assert.equal(row.els['.ik-amount'].textContent, money(shown));
        await P.save({ preventDefault() {}, target: giftForm });
        assert.equal(asked.length, ask ? 1 : 0, `${qty}|${fv}`);
        if (ask) assert.equal(asked[0], 'This in-kind gift adds up to $0.00. Save it anyway?');
        assert.equal(posts.length, 1);
        const sent = posts[0].lines.reduce((sum, l) => sum + l.quantity * l.fair_value, 0);
        assert.equal(sent, shown, `${qty}|${fv}`);
        assert.equal(total.textContent, `Total: ${money(sent)}`);
    }
});

test('a priced in-kind gift posts the body it always posted', async () => {
    const { P, asked, posts } = inKind([giftRow('2', '250'), giftRow('', '40')]);
    await P.save({ preventDefault() {}, target: giftForm });
    assert.deepEqual(asked, []);
    assert.equal(JSON.stringify(posts[0]), JSON.stringify({
        customer_id: 3, date: '2026-11-20', memo: null, class_id: null, job_id: null,
        lines: [
            { description: 'Upright piano', quantity: 2, fair_value: 250, debit_account_id: 7 },
            { description: 'Upright piano', quantity: 1, fair_value: 40, debit_account_id: 7 },
        ],
    }));
});

test('No to the in-kind question sends nothing', async () => {
    const { P, asked, posts } = inKind([giftRow('1', '0')], false);
    await P.save({ preventDefault() {}, target: giftForm });
    assert.equal(asked.length, 1);
    assert.deepEqual(posts, []);
});
