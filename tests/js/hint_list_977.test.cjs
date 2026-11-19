// #977, Settings: one tortoise per section. hintList() draws one button
// beside a section heading whose note lists the section's fields, each name
// in bold over its description; the same Hints open it as a field's hint.
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
    vm.runInContext(js + '\nthis.hint = hint; this.hintList = hintList; this.Hints = Hints;', context);
    return { context, listeners, nodes };
}

const ENTRIES = [
    { id: 'closing-date-help', text: 'Prevent modifications to transactions before this date.' },
    { name: 'Password (optional)', id: 'closing-date-password-help', text: 'With a password set, a change <em>asks</em>.' },
    { name: 'Second', text: 'No id of its own.' },
];

test('one button names the section and opens a note listing its fields', () => {
    const { context } = load();
    const html = context.hintList('Closing Date', ENTRIES);
    const id = /aria-controls="(hint-list-\d+)"/.exec(html)[1];
    assert.equal((html.match(/<button /g) || []).length, 1, 'one tortoise for the whole section');
    assert.match(html, /^<div class="hint-wrap hint-wrap--list"><button type="button" class="hint-icon" data-hint aria-label="About Closing Date" aria-expanded="false" aria-controls="hint-list-\d+">/);
    // the button is named, not described by the whole list: each field is
    assert.ok(!html.includes('aria-describedby'));
    assert.ok(html.includes(`<div class="hint-pop hint-pop--list" id="${id}" role="note" aria-label="About Closing Date" tabindex="0" hidden>`));
    assert.ok(html.includes('<strong class="hint-pop-title">Closing Date</strong>'));
    assert.ok(html.includes('src="/static/img/tortoise-hint.png"') && html.includes('src="/static/img/tortoise-info.png"'));
});

test("the section's lead comes first, then each field's name in bold over its words, in order", () => {
    const { context } = load();
    const html = context.hintList('Closing Date', ENTRIES);
    const lead = html.indexOf('<p class="hint-list-lead" id="closing-date-help">Prevent modifications to transactions before this date.</p>');
    const dl = html.indexOf('<dl class="hint-list">');
    const pw = html.indexOf('<div class="hint-list-item"><dt>Password (optional)</dt><dd id="closing-date-password-help">With a password set, a change <em>asks</em>.</dd></div>');
    const second = html.indexOf('<dt>Second</dt>');
    assert.ok(lead > 0 && dl > lead && pw > dl && second > pw, html);
    // an entry with no id of its own gets one, from the note's
    const id = /aria-controls="(hint-list-\d+)"/.exec(html)[1];
    assert.ok(html.includes(`<dd id="${id}-3">No id of its own.</dd>`));
});

test('each note and each made-up entry id is its own', () => {
    const { context } = load();
    const a = context.hintList('One', [{ name: 'x', text: 'y' }]);
    const b = context.hintList('Two', [{ name: 'x', text: 'y' }]);
    const ida = /id="(hint-list-\d+)"/.exec(a)[1];
    const idb = /id="(hint-list-\d+)"/.exec(b)[1];
    assert.notEqual(ida, idb);
    assert.notEqual(/<dd id="([^"]+)"/.exec(a)[1], /<dd id="([^"]+)"/.exec(b)[1]);
});

test('names are escaped; hidden and administrator-only entries are marked', () => {
    const { context } = load();
    const html = context.hintList('A "quoted" <section>', [
        { name: 'Field <b>', id: 'f', text: 't' },
        { name: 'Later', id: 'later', text: 'shown on a choice', hidden: true },
        { name: 'Admin', id: 'adm', text: 'theirs', admin: true },
    ]);
    assert.ok(html.includes('aria-label="About A &quot;quoted&quot; &lt;section>"'));
    assert.ok(html.includes('<dt>Field &lt;b></dt>'));
    assert.ok(html.includes('<div class="hint-list-item" hidden><dt>Later</dt><dd id="later">'));
    assert.ok(html.includes('<div class="hint-list-item" data-admin><dt>Admin</dt>'));
});

test('a section with nothing to say gets no tortoise', () => {
    const { context } = load();
    assert.equal(context.hintList('Empty', []), '');
    assert.equal(context.hintList('Empty', [null, undefined]), '');
    // an entry kept up by the page (filled in later) still has its place
    assert.ok(context.hintList('Later', [{ name: 'Provider', id: 'p', text: '', hidden: true }]).includes('<dd id="p"></dd>'));
});

// a list note's button, its note, and an element inside the note (a link,
// or the note itself, which takes focus to scroll)
function listPair(env, id) {
    const attrs = { 'aria-controls': id, 'aria-expanded': 'false' };
    const classes = new Set();
    const inside = {};
    const note = {
        hidden: true,
        classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), has: (c) => classes.has(c) },
        getBoundingClientRect: () => ({ right: 700 }),
    };
    const wrap = { contains: (el) => el === button || el === note || el === inside };
    const button = {
        getAttribute: (k) => attrs[k],
        setAttribute: (k, v) => { attrs[k] = v; },
        closest: (sel) => (sel === '[data-hint]' ? button : sel === '.hint-wrap' ? wrap : null),
        focus() { button.focused = true; },
    };
    env.nodes[id] = note;
    return { button, note, attrs, inside };
}

test('the same Hints open it: click, one at a time with field hints, Escape keeps focus', () => {
    const env = load();
    const list = listPair(env, 'hint-list-1');
    const field = listPair(env, 'hint-9');
    const click = env.listeners.click[0];
    click({ target: list.button, preventDefault() {} });
    assert.equal(list.note.hidden, false);
    assert.equal(list.attrs['aria-expanded'], 'true');
    click({ target: field.button, preventDefault() {} });
    assert.equal(list.note.hidden, true, 'one note open at a time');
    click({ target: list.button, preventDefault() {} });
    assert.equal(field.note.hidden, true);
    let stopped = 0;
    env.listeners.keydown[0]({ key: 'Escape', stopPropagation() { stopped += 1; } });
    assert.equal(list.note.hidden, true);
    assert.equal(list.button.focused, true);
    assert.equal(stopped, 1);
});

test('Tab into the note (to scroll or follow a link) keeps it; Tab out of the section closes it', () => {
    const env = load();
    const list = listPair(env, 'hint-list-2');
    env.listeners.click[0]({ target: list.button, preventDefault() {} });
    env.listeners.focusout[0]({ relatedTarget: list.note });
    env.listeners.focusout[0]({ relatedTarget: list.inside });
    assert.equal(list.note.hidden, false);
    // a click in the note (selecting its words, scrolling it) keeps it
    env.listeners.click[0]({ target: { closest: (sel) => (sel === '.hint-pop' ? list.note : null) } });
    assert.equal(list.note.hidden, false);
    env.listeners.focusout[0]({ relatedTarget: {} });
    assert.equal(list.note.hidden, true);
});

test('showEntry shows or leaves out one entry, its name with it', () => {
    const env = load();
    const item = { hidden: true };
    const dd = { closest: (sel) => (sel === '.hint-list-item' ? item : null) };
    const lead = { hidden: true, closest: () => null };
    env.nodes['llc-tax-election-help'] = dd;
    env.nodes['ocr-status-help'] = lead;
    env.context.Hints.showEntry('llc-tax-election-help', true);
    assert.equal(item.hidden, false);
    env.context.Hints.showEntry('llc-tax-election-help', false);
    assert.equal(item.hidden, true);
    env.context.Hints.showEntry('ocr-status-help', true);
    assert.equal(lead.hidden, false);
    env.context.Hints.showEntry('no-such-entry', true); // nothing to do
});

test('the list note scrolls inside a fixed height, in rem, wider than a field note', () => {
    const css = fs.readFileSync(path.join(__dirname, '../../app/static/css/style.css'), 'utf8');
    const at = css.indexOf('.hint-pop--list {');
    const rule = css.slice(at, css.indexOf('}', at));
    assert.ok(at > css.indexOf('.hint-pop-title {'), 'after the field hint rules');
    assert.match(rule, /max-height: 24rem;/);
    assert.match(rule, /overflow-y: auto;/);
    // never wider than the window less 8px a side
    assert.match(rule, /width: 30rem; max-width: calc\(100% - 16px\);/);
    assert.ok(!/\d(vh|vw|vmin|vmax)\b/.test(rule));
    assert.ok(css.includes('.hint-pop--list:focus-visible { outline: 2px solid var(--focus-ring);'));
});

// a list note's button and note with their places in a window 800 tall:
// the note is placed against the window (position: fixed) from its button
function placed(env, id, buttonTop, noteHeight) {
    const p = listPair(env, id);
    p.note.style = {};
    p.note.setAttribute = (k, v) => { p.note[k] = v; };
    p.note.getBoundingClientRect = () => ({ left: 0, top: 0, width: 480, height: noteHeight, right: 480, bottom: noteHeight });
    p.button.getBoundingClientRect = () => ({ left: 500, right: 524, top: buttonTop, bottom: buttonTop + 24 });
    return p;
}

test('a section note from a heading low in the window opens upward, and one with no room either side scrolls in what it has', () => {
    const env = load();
    env.context.window.innerHeight = 800;
    env.context.window.addEventListener = () => {};
    env.context.window.removeEventListener = () => {};
    const low = placed(env, 'hint-list-7', 700, 384);
    env.listeners.click[0]({ target: low.button, preventDefault() {} });
    assert.equal(low.note['data-side'], 'above');
    assert.equal(low.note.style.top, `${700 - 4 - 384}px`);
    assert.equal(low.note.style.maxHeight, '');
    env.listeners.click[0]({ target: low.button, preventDefault() {} });
    const fits = placed(env, 'hint-list-8', 100, 384);
    env.listeners.click[0]({ target: fits.button, preventDefault() {} });
    assert.equal(fits.note['data-side'], 'below');
    assert.equal(fits.note.style.top, `${124 + 4}px`);
    env.listeners.click[0]({ target: fits.button, preventDefault() {} });
    // 384 tall, 300 above and 464 below in a window 500 tall: neither fits
    env.context.window.innerHeight = 500;
    const cramped = placed(env, 'hint-list-9', 200, 384);
    env.listeners.click[0]({ target: cramped.button, preventDefault() {} });
    assert.equal(cramped.note['data-side'], 'below');
    assert.equal(cramped.note.style.top, '228px');
    assert.equal(cramped.note.style.maxHeight, `${500 - 8 - 228}px`);
    assert.equal(cramped.note.style.overflowY, 'auto');
});
