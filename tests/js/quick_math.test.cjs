// QuickMath (#696): + - * / in an amount box opens a calculator tape, and
// = or Enter puts the worked-out sum in the box. QuickMath.evaluate is the
// arithmetic (exact fractions, no eval), QuickMath.format the box's
// rounding; QuickMath.handle is the one delegated keydown handler, tried
// here against stand-in boxes. utils.js loads alone, as the probes load it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load() {
    const context = {
        window: {}, App: { settings: {} }, document: { addEventListener() {} },
        Event: class { constructor(type) { this.type = type; } },
    };
    vm.createContext(context);
    const js = fs.readFileSync(path.join(__dirname, '../../app/static/js/utils.js'), 'utf8');
    vm.runInContext(js + '\nthis.QuickMath = QuickMath;', context);
    return context.QuickMath;
}

const Q = load();
const calc = (expr, places = 2) => {
    const r = Q.result(expr, places);
    return r.error ? `!${r.error}` : r.text;
};

test('the table of sums', () => {
    const table = [
        ['100+25*2', '150.00'],         // * before +
        ['(100+25)*2', '250.00'],       // parentheses first
        ['100 - 25 - 5', '70.00'],      // left to right
        ['100/4/5', '5.00'],
        ['2*3+4*5', '26.00'],
        ['2*(3+4)*5', '70.00'],
        ['((1+2))*3', '9.00'],
        ['12.50 + 7.25', '19.75'],
        ['0.1+0.2', '0.30'],            // exact decimals, not floating point
        ['0.1*3', '0.30'],
        ['1.005*1', '1.01'],            // half away from zero, not 1.00
        ['10/3', '3.33'],
        ['20/3', '6.67'],
        ['-5+2', '-3.00'],
        ['5*-2', '-10.00'],
        ['-(2+3)', '-5.00'],
        ['-0.001', '0.00'],             // no "-0.00"
        ['.5+5.', '5.50'],
        ['1,234.50 + 1', '1235.50'],
        ['  42  ', '42.00'],
        ['3 × 4 − 2 ÷ 2', '11.00'],
    ];
    for (const [expr, want] of table) assert.equal(calc(expr), want, expr);
});

test('a four-place box (a line rate) keeps its places, two at least', () => {
    assert.equal(calc('10/3', 4), '3.3333');
    assert.equal(calc('100+25*2', 4), '150.00');
    assert.equal(calc('1.5', 4), '1.50');
    assert.equal(calc('0.125*1', 4), '0.125');
});

test('division by zero and an unfinished sum say so', () => {
    assert.equal(calc('5/0'), '!divide');
    assert.equal(calc('5/(2-2)'), '!divide');
    assert.equal(calc('100+'), '!unfinished');
    assert.equal(calc('100 * '), '!unfinished');
    assert.equal(calc('(1+2'), '!unfinished');
    assert.equal(calc(''), '!unfinished');
    assert.equal(calc('1+2)'), '!invalid');
    assert.equal(calc('2(3)'), '!invalid');
    assert.equal(calc('1+a'), '!invalid');
    assert.equal(calc('1..2'), '!invalid');
    assert.match(Q.result('5/0').message, /divides by zero/);
    assert.match(Q.result('5+').message, /not finished/);
    // nothing here reaches for eval or Function
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/utils.js'), 'utf8');
    const body = src.slice(src.indexOf('const QuickMath = {'), src.indexOf("document.addEventListener('keydown', QuickMath.handle"));
    assert.doesNotMatch(body, /\beval\s*\(|new Function|\bFunction\s*\(/);
});

function box(value, extra = {}) {
    return Object.assign({
        tagName: 'INPUT', type: 'number', value, readOnly: false, disabled: false,
        attrs: { step: '0.01' },
        getAttribute(k) { return this.attrs[k] == null ? null : this.attrs[k]; },
        classList: { contains: () => false },
        dataset: {},
    }, extra);
}
function key(target, k, mods = {}) {
    return Object.assign({
        target, key: k, prevented: false, stopped: false,
        preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; },
    }, mods);
}

test('only an amount box with a figure in it opens the tape', () => {
    const shown = [];
    const real = Q.show;
    Q.show = (el, op) => shown.push([el.value, op]);
    try {
        // a leading minus in an empty box is a sign
        const empty = key(box(''), '-');
        assert.equal(Q.handle(empty), false);
        assert.equal(empty.prevented, false);
        assert.equal(Q.handle(key(box(''), '+')), false);
        // a figure, and an operator: taken
        for (const op of ['+', '-', '*', '/']) {
            const e = key(box('12.50'), op);
            assert.equal(Q.handle(e), true, op);
            assert.equal(e.prevented, true);
        }
        assert.deepEqual(shown.map(s => s[1]), ['+', '-', '*', '/']);
        // read-only, disabled, a quantity box, a text box, a chord, a digit
        assert.equal(Q.handle(key(box('5', { readOnly: true }), '+')), false);
        assert.equal(Q.handle(key(box('5', { disabled: true }), '+')), false);
        assert.equal(Q.handle(key(box('5', { attrs: { step: '1' } }), '+')), false);
        assert.equal(Q.handle(key(box('5', { type: 'text' }), '+')), false);
        assert.equal(Q.handle(key(box('5'), '+', { ctrlKey: true })), false);
        assert.equal(Q.handle(key(box('5'), '7')), false);
        // a money field with a finer step (a line's rate) counts
        const rate = box('5', { attrs: { step: '0.0001' }, classList: { contains: c => c === 'money' } });
        assert.equal(Q.handle(key(rate, '*')), true);
        assert.equal(Q.places(rate), 4);
        assert.equal(Q.places(box('5')), 2);
    } finally {
        Q.show = real;
    }
});
