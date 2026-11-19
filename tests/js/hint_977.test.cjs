// #977: helper text behind the tortoise. hint() draws the button and its
// note; Hints opens one at a time, by click or key, never on hover, and the
// text is in the page for a screen reader whether or not it is open.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load() {
    const listeners = {};
    const nodes = {};
    const context = {
        window: { innerWidth: 1200 },
        document: {
            addEventListener: (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); },
            getElementById: (id) => nodes[id] || null,
        },
        escapeHtml: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
    };
    vm.createContext(context);
    const js = fs.readFileSync(path.join(__dirname, '../../app/static/js/hint.js'), 'utf8');
    vm.runInContext(js + '\nthis.hint = hint; this.Hints = Hints;', context);
    return { context, listeners, nodes };
}

// a button and its note, as the page would have them after hint() is drawn
function pair(env, id, right = 300) {
    const attrs = { 'aria-controls': id, 'aria-expanded': 'false' };
    const classes = new Set();
    const note = {
        hidden: true,
        classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), has: (c) => classes.has(c) },
        getBoundingClientRect: () => ({ right }),
    };
    const wrap = { contains: (el) => el === button || el === note };
    const button = {
        getAttribute: (k) => attrs[k],
        setAttribute: (k, v) => { attrs[k] = v; },
        closest: (sel) => (sel === '[data-hint]' ? button : sel === '.hint-wrap' ? wrap : null),
        focus() { button.focused = true; },
    };
    env.nodes[id] = note;
    return { button, note, attrs, wrap };
}

test('the button names its field, and its text is in the page though hidden', () => {
    const { context } = load();
    const html = context.hint('Optional. Separate addresses with commas.', 'CC');
    assert.match(html, /<button type="button" class="hint-icon" data-hint aria-label="About CC" aria-expanded="false"/);
    const id = /aria-controls="(hint-\d+)"/.exec(html)[1];
    assert.ok(html.includes(`aria-describedby="${id}"`));
    assert.ok(html.includes(`<span class="hint-pop" id="${id}" role="note" hidden>`));
    assert.ok(html.includes('<strong class="hint-pop-title">CC</strong>Optional. Separate addresses with commas.'));
    // the whole tortoise on the button and, larger, heading the note
    assert.ok(html.includes('src="/static/img/tortoise-hint.png"') && html.includes('src="/static/img/tortoise-info.png"'));
    assert.equal((html.match(/alt="" aria-hidden="true"/g) || []).length, 2);
});

test('a hint with no field name is "About this field", and each has its own id', () => {
    const { context } = load();
    const a = context.hint('One.');
    const b = context.hint('Two.');
    assert.ok(a.includes('aria-label="About this field"') && !a.includes('hint-pop-title'));
    assert.notEqual(/id="(hint-\d+)"/.exec(a)[1], /id="(hint-\d+)"/.exec(b)[1]);
});

test('a field name typed by a user is escaped', () => {
    const { context } = load();
    assert.ok(context.hint('x', 'A "quoted" <name>').includes('aria-label="About A &quot;quoted&quot; &lt;name>"'));
});

test('a click opens the note, a second click closes it, and one is open at a time', () => {
    const env = load();
    const one = pair(env, 'hint-a');
    const two = pair(env, 'hint-b');
    const click = env.listeners.click[0];
    let prevented = 0;
    click({ target: one.button, preventDefault() { prevented += 1; } });
    assert.equal(one.note.hidden, false);
    assert.equal(one.attrs['aria-expanded'], 'true');
    assert.equal(prevented, 1, 'a hint inside a label does not click the field');
    click({ target: two.button, preventDefault() {} });
    assert.equal(one.note.hidden, true);
    assert.equal(one.attrs['aria-expanded'], 'false');
    assert.equal(two.note.hidden, false);
    click({ target: two.button, preventDefault() {} });
    assert.equal(two.note.hidden, true);
});

test('a click elsewhere closes it; a click in the note itself does not', () => {
    const env = load();
    const one = pair(env, 'hint-a');
    const click = env.listeners.click[0];
    click({ target: one.button, preventDefault() {} });
    click({ target: { closest: (sel) => (sel === '.hint-pop' ? one.note : null) } });
    assert.equal(one.note.hidden, false);
    click({ target: { closest: () => null } });
    assert.equal(one.note.hidden, true);
});

test('Escape closes the note, keeps focus on its button, and goes no further', () => {
    const env = load();
    const one = pair(env, 'hint-a');
    env.listeners.click[0]({ target: one.button, preventDefault() {} });
    let stopped = 0;
    env.listeners.keydown[0]({ key: 'Escape', stopPropagation() { stopped += 1; } });
    assert.equal(one.note.hidden, true);
    assert.equal(one.button.focused, true);
    assert.equal(stopped, 1, 'the dialog the hint sits in stays open');
    // with nothing open, Escape is the dialog's own
    env.listeners.keydown[0]({ key: 'Escape', stopPropagation() { stopped += 1; } });
    assert.equal(stopped, 1);
});

test('Tab out closes it; focus moving within it does not', () => {
    const env = load();
    const one = pair(env, 'hint-a');
    env.listeners.click[0]({ target: one.button, preventDefault() {} });
    env.listeners.focusout[0]({ relatedTarget: one.note });
    assert.equal(one.note.hidden, false);
    env.listeners.focusout[0]({ relatedTarget: {} });
    assert.equal(one.note.hidden, true);
});

test('a note that would run off the right edge opens leftward', () => {
    const env = load();
    const edge = pair(env, 'hint-a', 1300);
    env.listeners.click[0]({ target: edge.button, preventDefault() {} });
    assert.equal(edge.note.classList.has('hint-pop--right'), true);
});

test('nothing opens on hover: only click, keydown and focusout are listened for', () => {
    const env = load();
    assert.deepEqual(Object.keys(env.listeners).sort(), ['click', 'focusout', 'keydown']);
});

test('a hint can carry the id a field already names with aria-describedby', () => {
    const { context } = load();
    const html = context.hint('A line this rule matches is added for this vendor.', 'Vendor', 'bank-rule-vendor-hint');
    assert.ok(html.includes('<span class="hint-pop" id="bank-rule-vendor-hint" role="note" hidden>'));
    assert.ok(html.includes('aria-controls="bank-rule-vendor-hint" aria-describedby="bank-rule-vendor-hint"'));
    // without one the ids still count on, and never repeat a named one
    const a = context.hint('One.');
    const b = context.hint('Two.', '', '');
    assert.match(a, /id="hint-\d+"/);
    assert.notEqual(/id="(hint-\d+)"/.exec(a)[1], /id="(hint-\d+)"/.exec(b)[1]);
});

test('setText swaps a note body and keeps the field name at its head', () => {
    const env = load();
    const head = { cls: 'hint-pop-title' };
    const body = {
        children: [head],
        querySelector: (sel) => (sel === '.hint-pop-title' ? head : null),
        set textContent(t) { body.children = [t]; },
        get firstChild() { return body.children[0]; },
        insertBefore(node, ref) { body.children.splice(body.children.indexOf(ref), 0, node); },
    };
    env.nodes['vendor-bank-rule-hint'] = { querySelector: (sel) => (sel === '.hint-pop-body' ? body : null) };
    env.context.Hints.setText('vendor-bank-rule-hint', 'Choose the default account first.');
    assert.deepEqual(body.children, [head, 'Choose the default account first.']);
    // an id not on the page is no error
    env.context.Hints.setText('nowhere', 'x');
});
