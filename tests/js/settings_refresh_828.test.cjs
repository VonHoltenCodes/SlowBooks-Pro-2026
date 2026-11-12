// The open window re-reads settings changed through the API (#828): the
// shell keeps the ETag of its copy, asks again on focus and when a page
// opens, does nothing on a 304, and replaces the copy (telling the page)
// only when a value really differs. The Settings page draws again over a
// clean form and asks before it draws over unsaved edits.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');

// app.js with a fake server: `answers` is the queue of revalidate results.
function loadApp(answers) {
    const asked = [];
    const events = [];
    const listeners = {};
    const pageContent = { innerHTML: '' };
    const ctx = {
        console, setTimeout, clearTimeout, Promise, JSON,
        window: {
            addEventListener: (name, fn) => { listeners[name] = fn; },
            dispatchEvent: e => { events.push(e); },
        },
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
        API: {
            revalidate: async (p, tag) => {
                asked.push([p, tag]);
                const next = answers.shift();
                if (next instanceof Promise) return next;
                return next || { status: 304, etag: tag, body: null };
            },
            get: async p => { asked.push(['get', p]); return { company_name: 'Fallback Co' }; },
        },
    };
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(JS, 'app.js'), 'utf8') + '\nthis.App = App;', ctx);
    return { App: ctx.App, ctx, asked, events, listeners, pageContent };
}

test('the first read keeps the ETag of the answer', async () => {
    const { App, asked } = loadApp([{ status: 200, etag: '"t1"', body: { company_name: 'North Pier Test Co' } }]);
    await App.loadCompanySettings();
    assert.deepEqual(asked, [['/settings', null]]);
    assert.equal(App.settingsTag, '"t1"');
    assert.equal(App.settings.company_name, 'North Pier Test Co');
});

test('a refused first read (before sign-in) falls back to API.get as before', async () => {
    const { App, asked } = loadApp([{ status: 401, etag: null, body: null }]);
    await App.loadCompanySettings();
    assert.deepEqual(asked, [['/settings', null], ['get', '/settings']]);
    assert.equal(App.settingsTag, null);
    assert.equal(App.settings.company_name, 'Fallback Co');
});

test('a 304 sends the held tag and changes nothing', async () => {
    const { App, asked, events } = loadApp([]);
    App.settings = { company_name: 'Kept Co' };
    App.settingsTag = '"t1"';
    const held = App.settings;
    assert.equal(await App.refreshSettings('focus'), false);
    assert.deepEqual(asked, [['/settings', '"t1"']]);
    assert.equal(App.settings, held);
    assert.equal(events.length, 0);
});

test('a refusal or a lost server changes nothing and says nothing', async () => {
    const { App, events } = loadApp([{ status: 401, etag: null, body: null }, { status: 0, etag: null, body: null }]);
    App.settings = { company_name: 'Kept Co' };
    App.settingsTag = '"t1"';
    assert.equal(await App.refreshSettings('focus'), false);
    assert.equal(await App.refreshSettings('focus'), false);
    assert.equal(App.settingsTag, '"t1"');
    assert.equal(events.length, 0);
});

test('a new tag over the same values keeps the copy and tells no one', async () => {
    // after this window's own Save: the copy holds the save's answer, which
    // carries extra keys a plain read does not
    const { App, events } = loadApp([{ status: 200, etag: '"t2"', body: { company_name: 'Same Co', deposit_account_ids: '[3,1]' } }]);
    App.settings = { company_name: 'Same Co', deposit_account_ids: '[3,1]', clearing_warnings: ['x'] };
    App.settingsTag = '"t1"';
    const held = App.settings;
    assert.equal(await App.refreshSettings('focus'), false);
    assert.equal(App.settingsTag, '"t2"');
    assert.equal(App.settings, held);
    assert.equal(events.length, 0);
});

test('a changed value replaces the copy and tells the page what changed', async () => {
    const fresh = { company_name: 'Same Co', deposit_account_ids: '[1,3]' };
    const { App, events } = loadApp([{ status: 200, etag: '"t2"', body: fresh }]);
    App.settings = { company_name: 'Same Co', deposit_account_ids: '[3,1]' };
    App.settingsTag = '"t1"';
    assert.equal(await App.refreshSettings('focus'), true);
    assert.equal(App.settingsTag, '"t2"');
    assert.equal(App.settings, fresh);
    assert.equal(events.length, 1);
    assert.equal(events[0].type, 'slowbooks:settings-changed');
    assert.equal(events[0].detail.reason, 'focus');
    assert.equal(events[0].detail.previous.deposit_account_ids, '[3,1]');
    assert.equal(events[0].detail.settings.deposit_account_ids, '[1,3]');
});

test('two asks at once share one request', async () => {
    let release;
    const pending = new Promise(r => { release = r; });
    const { App, asked } = loadApp([pending]);
    App.settingsTag = '"t1"';
    const a = App.refreshSettings('focus');
    const b = App.refreshSettings('page');
    assert.equal(a, b);
    release({ status: 304, etag: '"t1"', body: null });
    await a;
    assert.equal(asked.length, 1);
    // and the next ask is a request of its own
    await App.refreshSettings('focus');
    assert.equal(asked.length, 2);
});

test('a page opening reads the settings before it draws', async () => {
    const { App, ctx, asked, pageContent } = loadApp([{ status: 200, etag: '"t2"', body: { company_name: 'Fresh Co' } }]);
    App.settings = { company_name: 'Old Co' };
    App.settingsTag = '"t1"';
    let seen = null;
    App.routes['/__probe'] = { page: 'dashboard', label: 'Probe', render: () => { seen = App.settings.company_name; return '<p>probe</p>'; } };
    ctx.location.hash = '#/__probe';
    await App.navigate('#/__probe');
    assert.deepEqual(asked, [['/settings', '"t1"']]);
    assert.equal(seen, 'Fresh Co');
    assert.equal(pageContent.innerHTML, '<p>probe</p>');
});

test('the window asks again when it regains focus, and runs no timer', () => {
    const src = fs.readFileSync(path.join(JS, 'app.js'), 'utf8');
    const init = src.slice(src.indexOf('    init() {'), src.indexOf('    checkReminders()'));
    assert.match(init, /window\.addEventListener\('focus', \(\) => \{ App\.refreshSettings\('focus'\); \}\)/);
    const refresh = src.slice(src.indexOf('    refreshSettings(reason)'), src.indexOf('    _sameSettings('));
    assert.doesNotMatch(refresh, /setInterval|setTimeout/);
});

test('API.revalidate sends If-None-Match and hands back a 304 with no body', async () => {
    const calls = [];
    const replies = [
        { status: 304, ok: false, headers: { get: () => '"t1"' } },
        { status: 200, ok: true, headers: { get: () => '"t2"' }, json: async () => ({ company_name: 'New Co' }) },
        { status: 401, ok: false, headers: { get: () => null } },
    ];
    const ctx = {
        window: { localStorage: { getItem: () => '7' } },
        fetch: async (url, opts) => { calls.push([url, opts]); return replies.shift(); },
    };
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(JS, 'api.js'), 'utf8') + '\nthis.API = API;', ctx);
    const r304 = await ctx.API.revalidate('/settings', '"t1"');
    assert.deepEqual({ ...r304 }, { status: 304, etag: '"t1"', body: null });
    assert.equal(calls[0][0], '/api/settings');
    assert.equal(calls[0][1].headers['If-None-Match'], '"t1"');
    assert.equal(calls[0][1].headers['X-Company-Id'], '7');
    assert.equal(calls[0][1].cache, 'no-store');
    const r200 = await ctx.API.revalidate('/settings', '"t1"');
    assert.equal(r200.status, 200);
    assert.equal(r200.etag, '"t2"');
    assert.equal(r200.body.company_name, 'New Co');
    const r401 = await ctx.API.revalidate('/settings', null);
    assert.deepEqual({ ...r401 }, { status: 401, etag: null, body: null });
    assert.equal(calls[2][1].headers['If-None-Match'], undefined);
});

// settings.js with a fake form, as the unsaved-changes probe has it
function loadSettings(fields, answer) {
    const navigated = [];
    const confirms = [];
    const listeners = {};
    const form = { id: 'settings-form', elements: { namedItem: k => (k in fields ? {} : null) } };
    class FakeFormData { entries() { return Object.entries(fields)[Symbol.iterator](); } }
    const ctx = {
        console, JSON,
        FormData: FakeFormData,
        document: { getElementById: id => (id === 'settings-form' ? form : null) },
        window: { addEventListener: (name, fn) => { listeners[name] = fn; } },
        location: { hash: '#/settings' },
        history: { replaceState() {} },
        confirm: msg => { confirms.push(msg); return answer; },
        App: { navigate: async h => { navigated.push(h); }, stayPut() {}, isReadOnly: () => false },
    };
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(JS, 'settings.js'), 'utf8') + '\nthis.SettingsPage = SettingsPage;', ctx);
    const S = ctx.SettingsPage;
    S._isAdmin = () => true;
    S._installLeaveGuard();
    S._markClean();
    return { S, ctx, navigated, confirms, listeners };
}

const change = (previous, settings, reason = 'focus') => ({ detail: { previous, settings, reason } });

test('Settings with no unsaved edits draws again from the saved values, unasked', () => {
    const fields = { company_name: 'Harbor Test Co', company_phone: '555-0100' };
    const { navigated, confirms, listeners } = loadSettings(fields, false);
    listeners['slowbooks:settings-changed'](change({ company_phone: '555-0100' }, { company_phone: '555-0199' }));
    assert.deepEqual(navigated, ['#/settings']);
    assert.equal(confirms.length, 0);
});

test('Settings asks before drawing over unsaved edits a change reached; Cancel keeps them', () => {
    const fields = { company_name: 'Harbor Test Co', company_phone: '555-0100' };
    const { S, ctx, navigated, confirms, listeners } = loadSettings(fields, false);
    fields.company_name = 'Harbor Test Co, edited';
    listeners['slowbooks:settings-changed'](change({ company_phone: '555-0100' }, { company_phone: '555-0199' }));
    assert.equal(confirms.length, 1);
    assert.match(confirms[0], /changed somewhere else/);
    assert.match(confirms[0], /unsaved changes/);
    assert.deepEqual(navigated, []);
    assert.equal(S.isDirty(), true);  // the edits stay
    // OK: the saved settings, and the leave guard does not ask again
    ctx.confirm = msg => { confirms.push(msg); return true; };
    listeners['slowbooks:settings-changed'](change({ company_phone: '555-0100' }, { company_phone: '555-0177' }));
    assert.equal(confirms.length, 2);
    assert.deepEqual(navigated, ['#/settings']);
    assert.equal(S.isDirty(), false);
});

test('a change that reached no field on the form leaves the edits alone', () => {
    const fields = { company_name: 'Harbor Test Co' };
    const { navigated, confirms, listeners } = loadSettings(fields, true);
    fields.company_name = 'Harbor Test Co, edited';
    listeners['slowbooks:settings-changed'](change({ hidden_menu: '[]' }, { hidden_menu: '["iif"]' }));
    assert.equal(confirms.length, 0);
    assert.deepEqual(navigated, []);
});

test('Settings ignores a page opening, another page, and a change of nothing', () => {
    const fields = { company_phone: '555-0100' };
    const { ctx, navigated, confirms, listeners } = loadSettings(fields, true);
    const fire = listeners['slowbooks:settings-changed'];
    fire(change({ company_phone: '1' }, { company_phone: '2' }, 'page'));
    fire(change({ company_phone: '1' }, { company_phone: '1' }));
    ctx.location.hash = '#/invoices';
    fire(change({ company_phone: '1' }, { company_phone: '2' }));
    assert.deepEqual(navigated, []);
    assert.equal(confirms.length, 0);
});
