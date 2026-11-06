// The desktop zoom keys (#597) step the Text size setting (#717):
// Ctrl + / Ctrl - / Ctrl 0 (Cmd on a Mac) and Ctrl + wheel go through
// 100, 115, 125 and 150 percent and write the one setting Settings → My
// Preferences shows (text_size.js), kept in localStorage per computer. The
// size is applied from <head> on every page, in a browser too; the keys and
// the wheel are taken in the desktop shell only. Before 2.31.8 the keys kept
// a free zoom level (slowbooks.desktop.zoom, 0.5 to 2.0); it is carried over
// once to the nearest size.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');
const KEY = 'slowbooks.textsize';
const OLD = 'slowbooks.desktop.zoom';

function load({ desktop = true, stored = {}, userAgent, shim = true } = {}) {
    const storage = Object.assign({}, stored);
    const listeners = {};
    const listen = (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); };
    const attrs = {};
    const control = { value: '' };
    const context = {
        window: { open() {}, addEventListener: listen, pywebview: desktop ? { api: {} } : undefined },
        navigator: { userAgent: userAgent || (desktop ? 'WebView2' : 'Firefox') },
        document: {
            addEventListener: listen,
            querySelectorAll: sel => (sel === 'select[data-text-size-control]' ? [control] : []),
            documentElement: {
                style: {},
                setAttribute: (k, v) => { attrs[k] = String(v); },
                removeAttribute: k => { delete attrs[k]; },
            },
        },
        localStorage: {
            getItem: k => (k in storage ? storage[k] : null),
            setItem: (k, v) => { storage[k] = String(v); },
            removeItem: k => { delete storage[k]; },
        },
        setTimeout() {},
        URL,
    };
    context.window.localStorage = context.localStorage;
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(JS, 'text_size.js'), 'utf8'), context);
    if (shim) vm.runInContext(fs.readFileSync(path.join(JS, 'desktop_shim.js'), 'utf8'), context);
    return {
        zoom: shim ? context.window.SlowbooksDesktop.zoom : null,
        sizes: context.window.SlowbooksTextSize,
        size: () => attrs['data-text-size'],
        control,
        storage,
        window: context.window,
        fire: (name, event) => (listeners[name] || []).forEach(fn => fn(event)),
    };
}

function wheel(deltaY, mods = {}) {
    return Object.assign({ deltaY, prevented: false, preventDefault() { this.prevented = true; } }, mods);
}

function key(k, mods = {}) {
    return Object.assign({ key: k, code: '', prevented: false, preventDefault() { this.prevented = true; } }, mods);
}

test('Ctrl + and Ctrl - step through the four sizes, Ctrl 0 resets, and the size is kept', () => {
    const { zoom, size, storage, control } = load();
    const plus = key('=', { ctrlKey: true });
    assert.equal(zoom.handle(plus), true);
    assert.equal(plus.prevented, true);
    assert.equal(size(), '115');
    assert.equal(storage[KEY], '115');
    assert.equal(control.value, '115');  // the Settings control follows
    assert.equal(zoom.handle(key('+', { ctrlKey: true, shiftKey: true })), true);
    assert.equal(size(), '125');
    assert.equal(zoom.handle(key('-', { ctrlKey: true })), true);
    assert.equal(zoom.handle(key('_', { ctrlKey: true, shiftKey: true })), true);
    // back at 100 nothing is set and nothing is stored
    assert.equal(size(), undefined);
    assert.equal(KEY in storage, false);
    // 100 is the smallest
    assert.equal(zoom.handle(key('-', { ctrlKey: true })), true);
    assert.equal(size(), undefined);
    // the numpad's keys, and Cmd on a Mac
    assert.equal(zoom.handle(key('+', { ctrlKey: true, code: 'NumpadAdd' })), true);
    assert.equal(zoom.handle(key('+', { metaKey: true, code: 'NumpadAdd' })), true);
    assert.equal(zoom.handle(key('-', { metaKey: true, code: 'NumpadSubtract' })), true);
    assert.equal(size(), '115');
    const reset = key('0', { metaKey: true });
    assert.equal(zoom.handle(reset), true);
    assert.equal(reset.prevented, true);
    assert.equal(size(), undefined);
    assert.equal(KEY in storage, false);
});

test('the size stops at 150', () => {
    const { zoom, size } = load();
    for (let i = 0; i < 10; i++) zoom.handle(key('=', { ctrlKey: true }));
    assert.equal(size(), '150');
    for (let i = 0; i < 10; i++) zoom.handle(key('-', { ctrlKey: true }));
    assert.equal(size(), undefined);
});

test('a stored size is applied on load, in the desktop shell and in a browser', () => {
    const stored = { [KEY]: '125' };
    assert.equal(load({ stored }).size(), '125');
    assert.equal(load({ stored, desktop: false }).size(), '125');
    // and on Windows, where the shell cannot be told at load time (W-46)
    assert.equal(load({ stored, desktop: false, userAgent: 'Edg/156' }).size(), '125');
    // a value that is not one of the four reads as 100
    for (const bad of ['9', '130', 'big', '']) {
        assert.equal(load({ stored: { [KEY]: bad } }).size(), undefined, bad);
    }
});

test('any other key passes untouched, and so does every key in a browser', () => {
    const { zoom, size } = load();
    for (const e of [
        key('='), key('s', { ctrlKey: true }), key('k', { ctrlKey: true }),
        key('0'), key('=', { ctrlKey: true, altKey: true }), key('1', { ctrlKey: true }),
    ]) {
        assert.equal(zoom.handle(e), false);
        assert.equal(e.prevented, false);
    }
    assert.equal(size(), undefined);
    const browser = load({ desktop: false });
    const e = key('=', { ctrlKey: true });
    assert.equal(browser.zoom.handle(e), false);
    assert.equal(e.prevented, false);
    assert.equal(browser.size(), undefined);
});

test('Ctrl + wheel steps the same setting, a notch at a time, in the shell only (NEW-186)', () => {
    const { zoom, size, storage } = load();
    const up = wheel(-100, { ctrlKey: true });
    assert.equal(zoom.wheel(up), true);
    assert.equal(up.prevented, true);  // WebView2's own zoom stays out of it
    assert.equal(size(), '115');
    assert.equal(storage[KEY], '115');
    // a trackpad's run of small deltas adds up to one notch
    for (const d of [-20, -20]) assert.equal(zoom.wheel(wheel(d, { metaKey: true })), true);
    assert.equal(size(), '115');
    zoom.wheel(wheel(-20, { metaKey: true }));
    assert.equal(size(), '125');
    zoom.wheel(wheel(100, { ctrlKey: true }));
    zoom.wheel(wheel(100, { ctrlKey: true }));
    assert.equal(size(), undefined);
    // a plain scroll, and every wheel in a browser, pass untouched
    const plain = wheel(-100);
    assert.equal(zoom.wheel(plain), false);
    assert.equal(plain.prevented, false);
    const browser = load({ desktop: false });
    const e = wheel(-100, { ctrlKey: true });
    assert.equal(browser.zoom.wheel(e), false);
    assert.equal(e.prevented, false);
});

test('a zoom level from before 2.31.8 is carried over once to the nearest size', () => {
    // exactly between two sizes, the larger (W-127): 1.2 is between 115
    // and 125, and whoever chose bigger text never gets smaller text
    const cases = [
        ['1.1', '115'], ['1.2', '125'], ['1.15', '115'], ['1.25', '125'], ['1.3', '125'], ['1.4', '150'], ['1.5', '150'],
        ['2', '150'], ['1', undefined], ['0.7', undefined],
    ];
    for (const [level, size] of cases) {
        const page = load({ stored: { [OLD]: level } });
        assert.equal(page.size(), size, level);
        assert.equal(OLD in page.storage, false, level);
        assert.equal(page.storage[KEY], size, level);
    }
    // a size already set wins over the old level, which still goes
    const both = load({ stored: { [OLD]: '1.5', [KEY]: '115' } });
    assert.equal(both.size(), '115');
    assert.equal(OLD in both.storage, false);
    // a broken old level is dropped
    const bad = load({ stored: { [OLD]: '9' } });
    assert.equal(bad.size(), undefined);
    assert.equal(OLD in bad.storage, false);
});

test('ratio() is 1 where nothing can be measured', () => {
    const { sizes } = load({ shim: false });
    assert.equal(sizes.ratio(), 1);
    assert.deepEqual(Array.from(sizes.SIZES), [100, 115, 125, 150]);
    assert.equal(sizes.nearest(1.3), 125);
    // a tie goes up, wherever it falls
    assert.equal(sizes.nearest(1.075), 115);
    assert.equal(sizes.nearest(1.2), 125);
    assert.equal(sizes.nearest(1.375), 150);
    assert.equal(sizes.nearest(1.37), 125);
});
