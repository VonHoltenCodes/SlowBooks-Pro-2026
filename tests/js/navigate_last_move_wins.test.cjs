// The last move wins: the app's first page still loading when another page
// is chosen drew over it whenever it finished last (a slow Dashboard over
// the Expenses list just opened, the address and the sidebar saying
// Expenses). A move overtaken by a later one draws nothing, and awaiting
// any of them waits until the page the address names is drawn.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');

function loadApp() {
    const pageContent = { innerHTML: '' };
    const ctx = {
        console, setTimeout, clearTimeout, Promise, JSON,
        window: { addEventListener() {}, dispatchEvent() {} },
        CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
        document: { addEventListener() {}, querySelectorAll: () => [], getElementById: () => null, title: '' },
        location: { hash: '#/' },
        history: { state: null, pushState: (_s, _t, url) => { ctx.location.hash = url; }, replaceState() {} },
        $: sel => (sel === '#page-content' ? pageContent : null),
        $$: () => [],
        escapeHtml: s => String(s ?? ''),
        toast: () => {},
        T: s => s,
        Terms: { init: () => {}, text: s => s, isNonprofit: () => false, isConstruction: () => false },
        API: { revalidate: async (_p, tag) => ({ status: 304, etag: tag, body: null }), get: async () => ({}) },
    };
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(JS, 'app.js'), 'utf8') + '\nthis.App = App;', ctx);
    return { App: ctx.App, ctx, pageContent };
}

// a route whose render waits until `release()` is called
function heldRoute(html) {
    let release;
    const held = new Promise(r => { release = r; });
    return { route: { page: 'dashboard', label: html, render: async () => { await held; return html; } }, release };
}

test('a slow first page that finishes last does not draw over the page chosen since', async () => {
    const { App, ctx, pageContent } = loadApp();
    const slow = heldRoute('<p>slow first page</p>');
    App.routes['/__slow'] = slow.route;
    App.routes['/__chosen'] = { page: 'expenses', label: 'Chosen', render: async () => '<p>chosen</p>' };
    const first = App.navigate('#/__slow');
    const chosen = App.navigate('#/__chosen');
    await chosen;
    assert.equal(pageContent.innerHTML, '<p>chosen</p>');
    slow.release();
    await first;
    assert.equal(pageContent.innerHTML, '<p>chosen</p>');
    assert.equal(ctx.location.hash, '#/__chosen');
});

test('awaiting an overtaken move waits for the page the address names', async () => {
    const { App, pageContent } = loadApp();
    const later = heldRoute('<p>later</p>');
    App.routes['/__first'] = { page: 'dashboard', label: 'First', render: async () => '<p>first</p>' };
    App.routes['/__later'] = later.route;
    let firstDone = false;
    const first = App.navigate('#/__first').then(() => { firstDone = true; });
    const second = App.navigate('#/__later');
    await new Promise(r => setTimeout(r, 20));
    // the first move's page was overtaken: nothing of it drawn, and it is
    // not done while the page asked for last is still loading
    assert.equal(pageContent.innerHTML, '');
    assert.equal(firstDone, false);
    later.release();
    await first;
    assert.equal(pageContent.innerHTML, '<p>later</p>');
    await second;
});
