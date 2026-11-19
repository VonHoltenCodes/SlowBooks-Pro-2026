// #977 review: an open note was cut off by a dialog's edge or a table's
// scroll box, since it sat inside its button's box and only the window's
// right edge was checked. Hints.position now places it against the window
// (position: fixed), wholly inside it, 8px clear of each edge; under the
// field row it describes when there is room, else above its button.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load(win = { innerWidth: 1280, innerHeight: 900 }) {
    const listeners = {};
    const winListeners = {};
    const nodes = {};
    const context = {
        window: Object.assign(win, {
            addEventListener: (name, fn) => { (winListeners[name] = winListeners[name] || []).push(fn); },
            removeEventListener: (name, fn) => { winListeners[name] = (winListeners[name] || []).filter((f) => f !== fn); },
        }),
        document: {
            body: { tag: 'body' },
            addEventListener: (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); },
            removeEventListener: (name, fn) => { listeners[name] = (listeners[name] || []).filter((f) => f !== fn); },
            getElementById: (id) => nodes[id] || null,
        },
        escapeHtml: (s) => String(s),
    };
    vm.createContext(context);
    const js = fs.readFileSync(path.join(__dirname, '../../app/static/js/hint.js'), 'utf8');
    vm.runInContext(js + '\nthis.Hints = Hints;', context);
    return { context, Hints: context.Hints, listeners, winListeners, nodes };
}

const rect = (left, top, width, height) => ({ left, top, width, height, right: left + width, bottom: top + height });
const WIN = { width: 1280, height: 900 };

test('a note opens under its button, lined up with it, when it fits', () => {
    const { Hints } = load();
    const p = Hints.place(rect(400, 100, 24, 24), { width: 340, height: 80 }, WIN, null);
    assert.deepEqual(JSON.parse(JSON.stringify(p)), { left: 398, top: 128, side: 'below', maxHeight: null });
});

test('near the right edge (a dialog at the window edge) it moves in, 8px clear', () => {
    const { Hints } = load();
    const p = Hints.place(rect(1200, 100, 24, 24), { width: 340, height: 80 }, WIN, null);
    assert.equal(p.left, 1280 - 8 - 340);
    // and never past the left edge, however wide
    const wide = Hints.place(rect(4, 100, 24, 24), { width: 2000, height: 80 }, WIN, null);
    assert.equal(wide.left, 8);
});

test('near the bottom it opens above its button; with no room either side it takes the roomier and scrolls', () => {
    const { Hints } = load();
    const up = Hints.place(rect(400, 820, 24, 24), { width: 340, height: 120 }, WIN, null);
    assert.equal(up.side, 'above');
    assert.equal(up.top, 820 - 4 - 120);
    assert.ok(up.top + 120 <= 900 - 8);
    const tall = Hints.place(rect(400, 300, 24, 24), { width: 480, height: 1000 }, WIN, null);
    assert.equal(tall.side, 'below');
    assert.equal(tall.top, 328);
    assert.equal(tall.maxHeight, 900 - 8 - 328);
    const tallLow = Hints.place(rect(400, 700, 24, 24), { width: 480, height: 1000 }, WIN, null);
    assert.equal(tallLow.side, 'above');
    assert.equal(tallLow.top, 8);
    assert.equal(tallLow.maxHeight, 700 - 4 - 8);
});

test('a field note leaves its field in view: under the field row, else above the button', () => {
    const { Hints } = load();
    const button = rect(345, 258, 24, 24);
    const row = rect(308, 258, 326, 52); // the label and the select under it
    const p = Hints.place(button, { width: 340, height: 90 }, WIN, row);
    assert.equal(p.top, 310 + 4);
    // no room under the row: above the button, the field still uncovered
    const low = Hints.place(rect(345, 800, 24, 24), { width: 340, height: 90 }, WIN, rect(308, 800, 326, 60));
    assert.equal(low.side, 'above');
    assert.equal(low.top + 90, 800 - 4);
    // room under the button only: there, as before
    const cramped = Hints.place(rect(345, 60, 24, 24), { width: 340, height: 700 }, WIN, rect(308, 60, 326, 160));
    assert.equal(cramped.top, 88);
    assert.equal(cramped.maxHeight, null);
});

// a button and its note on a fake page; `offset` is where the note's 0,0
// actually lands (a transform on a box around it moves it)
function onPage(env, id, buttonRect, { size = { width: 340, height: 80 }, offset = { x: 0, y: 0 }, group = null, overflowBox = null } = {}) {
    const attrs = { 'aria-controls': id, 'aria-expanded': 'false' };
    const note = {
        hidden: true,
        style: {},
        setAttribute(k, v) { note[k] = v; },
        contains: (el) => el === note || el === note.inner,
        inner: { tag: 'inside' },
        getBoundingClientRect: () => {
            const x = parseFloat(note.style.left || '0') + offset.x;
            const y = parseFloat(note.style.top || '0') + offset.y;
            return rect(x, y, size.width, size.height);
        },
    };
    const scroller = overflowBox && { style: 'scroll', getBoundingClientRect: () => overflowBox, parentElement: env.context.document.body };
    const button = {
        getAttribute: (k) => attrs[k],
        setAttribute: (k, v) => { attrs[k] = v; },
        closest: (sel) => (sel === '[data-hint]' ? button : sel === '.form-group' ? group : null),
        getBoundingClientRect: () => buttonRect(),
        parentElement: scroller || null,
    };
    env.nodes[id] = note;
    return { button, note, attrs, scroller };
}

test('the note is placed with fixed coordinates, and an offset 0,0 (a transformed box) is taken off', () => {
    const env = load();
    const a = onPage(env, 'h1', () => rect(1200, 100, 24, 24));
    env.Hints.open(a.button);
    assert.equal(a.note.style.left, `${1280 - 8 - 340}px`);
    assert.equal(a.note.style.top, '128px');
    const box = a.note.getBoundingClientRect();
    assert.ok(box.left >= 8 && box.right <= 1272 && box.top >= 8 && box.bottom <= 892);
    env.Hints.close();
    const b = onPage(env, 'h2', () => rect(1200, 100, 24, 24), { offset: { x: 290, y: 40 } });
    env.Hints.open(b.button);
    const moved = b.note.getBoundingClientRect();
    assert.equal(moved.left, 1280 - 8 - 340);
    assert.equal(moved.top, 128);
});

test('the field row comes from the button\'s form group', () => {
    const env = load();
    const group = { getBoundingClientRect: () => rect(308, 258, 326, 52) };
    const a = onPage(env, 'h3', () => rect(345, 258, 24, 24), { group });
    env.Hints.open(a.button);
    assert.equal(a.note.style.top, '314px');
    // a tall group (a whole fieldset) is not a row: under the button
    env.Hints.close();
    const big = { getBoundingClientRect: () => rect(308, 258, 326, 400) };
    const b = onPage(env, 'h4', () => rect(345, 258, 24, 24), { group: big });
    env.Hints.open(b.button);
    assert.equal(b.note.style.top, '286px');
});

test('a scroll places it again; its own scroll does not; out of sight it closes; closed, nothing is listened for', () => {
    const env = load();
    let top = 100;
    const a = onPage(env, 'h5', () => rect(400, top, 24, 24));
    env.Hints.open(a.button);
    assert.equal(env.listeners.scroll.length, 1);
    assert.equal(env.winListeners.resize.length, 1);
    top = 300;
    env.listeners.scroll[0]({ type: 'scroll', target: {} });
    assert.equal(a.note.style.top, '328px');
    // the note's own list scrolling: left where it is
    top = 350;
    env.listeners.scroll[0]({ type: 'scroll', target: a.note.inner });
    assert.equal(a.note.style.top, '328px');
    // a resize places it again too
    env.winListeners.resize[0]({ type: 'resize' });
    assert.equal(a.note.style.top, '378px');
    // scrolled out of the window: closed, and the listeners go with it
    top = -100;
    env.listeners.scroll[0]({ type: 'scroll', target: {} });
    assert.equal(a.note.hidden, true);
    assert.equal(a.attrs['aria-expanded'], 'false');
    assert.equal(env.listeners.scroll.length, 0);
    assert.equal(env.winListeners.resize.length, 0);
});

test('scrolled out of a box around it (a dialog\'s body, a table) it closes', () => {
    const env = load();
    env.context.getComputedStyle = (el) => (el.style === 'scroll' ? { overflowX: 'auto', overflowY: 'auto' } : { overflowX: 'visible', overflowY: 'visible' });
    let top = 200;
    const a = onPage(env, 'h6', () => rect(400, top, 24, 24), { overflowBox: rect(300, 150, 600, 300) });
    env.Hints.open(a.button);
    env.listeners.scroll[0]({ type: 'scroll', target: a.scroller });
    assert.equal(a.note.hidden, false);
    top = 100; // above the box's top edge, still inside the window
    env.listeners.scroll[0]({ type: 'scroll', target: a.scroller });
    assert.equal(a.note.hidden, true);
});

test('the note is fixed in the CSS, never wider than the window, and resets the text styles it would inherit', () => {
    const css = fs.readFileSync(path.join(__dirname, '../../app/static/css/style.css'), 'utf8');
    const at = css.indexOf('.hint-pop {');
    const rule = css.slice(at, css.indexOf('}', at));
    assert.match(rule, /position: fixed;/);
    assert.match(rule, /max-width: min\(340px, calc\(100% - 16px\)\);/);
    for (const decl of ['font-family: var(--font);', 'font-size: 12px;', 'font-weight: 400;', 'letter-spacing: normal;',
        'text-transform: none;', 'white-space: normal;', 'text-align: left;', 'color: var(--text-primary);']) {
        assert.ok(rule.includes(decl), decl);
    }
    assert.ok(!/\d(vh|vw|vmin|vmax)\b/.test(rule));
    assert.ok(!css.includes('hint-pop--right') && !css.includes('hint-pop--up'), 'the old flip classes are gone');
});
