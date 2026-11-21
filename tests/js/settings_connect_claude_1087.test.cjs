// Connect to AI assistant in Settings, Connections (#1087): the panel's states. The
// words are for everyone; an administrator gets the buttons: Connect to AI assistant
// when not connected; the connector address with Copy, Show a sign-in code
// and Disconnect when connected here; nothing to press for a connection
// made in another company. The code shows with its ten-minute life. A
// relay that cannot be reached says so. settings.js loaded alone, with the
// helpers it calls stubbed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function fixture(answers = {}) {
    const panel = { innerHTML: '' };
    const sent = [];
    const toasts = [];
    const confirms = [];
    const context = {
        API: {
            get: async p => { sent.push(['GET', p]); return answers[p]; },
            post: async p => {
                sent.push(['POST', p]);
                const a = answers['POST ' + p];
                if (a instanceof Error) throw a;
                return a;
            },
        },
        $: selector => (selector === '#switchboard-relay' ? panel : null),
        T: value => value,
        Terms: { text: value => value, isNonprofit: () => false, isConstruction: () => false },
        escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[c]),
        copyLineHtml: (id, label, value, handler) => `<code id="${id}">${value}</code><button type="button" onclick="${handler}">Copy</button>`,
        copyToClipboard: async () => true,
        toast: (m, kind) => toasts.push([m, kind]),
        confirm: m => { confirms.push(m); return true; },
        document: { getElementById: () => null },
        setTimeout() {},
        Date,
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/settings.js'), 'utf8') + '\nthis.page = SettingsPage;', context);
    context.page._claude = { status: null, code: null, error: '', timer: null };
    return { page: context.page, panel, sent, toasts, confirms };
}

const buttons = html => [...html.matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map(m => m[1]);
const state = html => (html.match(/id="claude-relay-state"[^>]*>([^<]*)</) || [])[1];

const ADDRESS = 'https://relay.sbplive.com/mcp';
const CONNECTED = { state: 'connected', linked: true, this_company: true, company: 'Larkspur Supply', address: ADDRESS, token_label: 'AI assistant (relay)', can_manage: true };

test('not connected: the words, and Connect to AI assistant for an administrator only', async () => {
    const f = fixture({ '/switchboard/status': { state: 'not_connected', linked: false, can_manage: true } });
    await f.page.loadSwitchboard();
    assert.equal(state(f.panel.innerHTML), 'Not connected');
    assert.deepEqual(buttons(f.panel.innerHTML), ['Connect to AI assistant']);
    const g = fixture({ '/switchboard/status': { state: 'not_connected', linked: false, can_manage: false } });
    await g.page.loadSwitchboard();
    assert.equal(state(g.panel.innerHTML), 'Not connected');
    assert.deepEqual(buttons(g.panel.innerHTML), []);
});

test('connected: the address with Copy, a code and Disconnect; status only for others', async () => {
    const f = fixture({ '/switchboard/status': CONNECTED });
    await f.page.loadSwitchboard();
    const html = f.panel.innerHTML;
    assert.equal(state(html), 'Connected');
    assert.equal((html.match(/<code id="claude-relay-address">([^<]*)</) || [])[1], ADDRESS);
    assert.ok(html.includes('Larkspur Supply'));
    assert.deepEqual(buttons(html), ['Copy', 'Show a sign-in code', 'Disconnect']);
    const g = fixture({ '/switchboard/status': { ...CONNECTED, can_manage: false } });
    await g.page.loadSwitchboard();
    assert.equal(state(g.panel.innerHTML), 'Connected');
    assert.deepEqual(buttons(g.panel.innerHTML), []);
    assert.ok(!g.panel.innerHTML.includes('claude-relay-address'));
});

test('a sign-in code shows with its ten-minute life', async () => {
    const expires = new Date(Date.now() + 10 * 60 * 1000).toISOString();
    // made here, as the relay makes one: twelve of Crockford's base32
    const ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
    const pick = () => ALPHABET[require('node:crypto').randomInt(ALPHABET.length)];
    const code = [0, 1, 2].map(() => Array.from({ length: 4 }, pick).join('')).join('-');
    const f = fixture({ '/switchboard/status': CONNECTED, 'POST /switchboard/code': { code, expires } });
    await f.page.loadSwitchboard();
    await f.page.claudeCode();
    const html = f.panel.innerHTML;
    assert.ok(html.includes('id="claude-relay-code"'));
    assert.ok(html.includes(code));
    assert.match(html, /It works once, for ten minutes \(until [^)]+\)\./);
    assert.deepEqual(f.sent.at(-1), ['POST', '/switchboard/code']);
});

test('the relay cannot be reached: said in words', async () => {
    const f = fixture({ '/switchboard/status': { ...CONNECTED, state: 'unreachable' } });
    await f.page.loadSwitchboard();
    assert.equal(state(f.panel.innerHTML), 'The relay cannot be reached');
    // and a Connect that fails to reach it says the server's sentence, kept
    // through the panel's refresh, with Connect offered again (W-208): no
    // link was made, so the state is Not connected
    const sentence = 'The relay could not be reached. SlowBooks Pro is working normally; try again in a few minutes.';
    const err = new Error(sentence);
    err.status = 503;
    const g = fixture({ '/switchboard/status': { state: 'not_connected', linked: false, can_manage: true }, 'POST /switchboard/connect': err });
    await g.page.loadSwitchboard();
    await g.page.connectClaude();
    assert.equal(state(g.panel.innerHTML), 'Not connected');
    assert.ok(g.panel.innerHTML.includes(sentence));
    assert.ok(g.panel.innerHTML.includes('role="alert"'));
    assert.ok(g.panel.innerHTML.includes('id="claude-connect"'));
    await g.page.loadSwitchboard();
    assert.ok(g.panel.innerHTML.includes(sentence));
    g.page.dismissClaudeError();
    assert.ok(!g.panel.innerHTML.includes(sentence));
});

test('a connection made in another company: whose it is, nothing to press', async () => {
    const f = fixture({ '/switchboard/status': { state: 'not_connected', linked: true, this_company: false, company: 'Larkspur Supply', address: '', can_manage: true, sentence: 'The AI assistant is connected to Larkspur Supply, not to this company.' } });
    await f.page.loadSwitchboard();
    assert.equal(state(f.panel.innerHTML), 'Not connected');
    assert.ok(f.panel.innerHTML.includes('The AI assistant is connected to Larkspur Supply'));
    assert.deepEqual(buttons(f.panel.innerHTML), []);
});

test('Disconnect asks first, then draws the panel again', async () => {
    const f = fixture({ '/switchboard/status': CONNECTED, 'POST /switchboard/disconnect': { unlinked: true, revoked: true } });
    await f.page.loadSwitchboard();
    await f.page.disconnectClaude();
    assert.deepEqual(f.sent.slice(-2), [['POST', '/switchboard/disconnect'], ['GET', '/switchboard/status']]);
    assert.equal(f.toasts.at(-1)[0], 'The AI assistant is disconnected.');
});

// Disconnect deletes the relay's record; the relay name is shown while
// connected, and kept on the panel when Disconnect could not reach the relay.
test('connected: the relay name with Copy', async () => {
    const f = fixture({ '/switchboard/status': { ...CONNECTED, slug: 'ztnqj8djvg' } });
    await f.page.loadSwitchboard();
    assert.match(f.panel.innerHTML, /<code id="claude-relay-name">ztnqj8djvg<\/code>/);
    assert.match(f.panel.innerHTML, /onclick="SettingsPage\.copyClaudeRelayName\(\)"/);
});

test('disconnect: the confirm says the relay record is deleted', async () => {
    const f = fixture({
        '/switchboard/status': CONNECTED,
        'POST /switchboard/disconnect': { unlinked: true, revoked: true, deleted: true, devices_left: 0, slug: 'ztnqj8djvg', sentence: 'x' },
    });
    f.page._claude.status = CONNECTED;
    await f.page.disconnectClaude();
    assert.match(f.confirms[0], /the relay deletes its record of these books/);
    assert.ok(f.toasts.some(([m]) => /the relay deleted its record of these books/.test(m)));
    assert.equal(f.page._claude.fallback, null);
});

test('disconnect with the relay out of reach: the fallback and the name stay until dismissed', async () => {
    const sentence = 'This computer is unlinked here, but the relay could not be reached, so it still holds a record under relay name ztnqj8djvg. Send that name to support@slowbookspro.com to have it removed.';
    // the name comes from the server's status, which keeps it (NEW-269)
    const answers = {
        'POST /switchboard/disconnect': { unlinked: false, revoked: true, deleted: false, devices_left: 0, slug: 'ztnqj8djvg', sentence },
        'POST /switchboard/dismiss': { pending: [] },
    };
    const f = fixture(answers);
    Object.defineProperty(answers, '/switchboard/status', {
        get: () => ({
            state: 'not_connected', linked: false, can_manage: true,
            pending: [{
                slug: 'ztnqj8djvg', sentence,
                dismissed: f.sent.some(([m, p]) => m === 'POST' && p === '/switchboard/dismiss'),
            }],
        }),
    });
    await f.page.disconnectClaude();
    const html = f.panel.innerHTML;
    assert.match(html, /id="claude-relay-fallback" role="alert"/);
    assert.ok(html.includes('support@slowbookspro.com'));
    assert.match(html, /<code id="claude-relay-fallback-name"[^>]*>ztnqj8djvg<\/code>/);
    assert.ok(buttons(html).includes('Dismiss'));
    await f.page.loadSwitchboard();  // asked again: still there
    assert.match(f.panel.innerHTML, /claude-relay-fallback-name/);
    await f.page.dismissClaudeFallback();
    assert.deepEqual(f.sent.at(-2), ['POST', '/switchboard/dismiss']);
    assert.doesNotMatch(f.panel.innerHTML, /claude-relay-fallback/);
});

// What Disconnect says follows what the relay answered: a refusal and an
// answer that did not confirm the deletion are not "out of reach"; and a
// sentence with no notice kept for it stays on the panel.
test('disconnect: the toast says why the relay still holds its record', async () => {
    const said = async why => {
        const f = fixture({
            '/switchboard/status': { state: 'not_connected', linked: false, can_manage: true },
            'POST /switchboard/disconnect': { unlinked: false, revoked: true, deleted: false, slug: 'ztnqj8djvg', sentence: 'x', why, pending: true, say: false },
        });
        await f.page.disconnectClaude();
        return f.toasts.at(-1)[0];
    };
    assert.equal(await said('away'), 'The AI assistant is disconnected and the token revoked. The relay was out of reach: see the relay name below.');
    assert.match(await said('refused'), /^The AI assistant is disconnected and the token revoked\. The relay did not accept the request to delete its record: see the relay name below\.$/);
    assert.match(await said('unconfirmed'), /The relay did not confirm that it deleted its record: see the relay name below\.$/);
    assert.doesNotMatch(await said('refused'), /out of reach/);
});

test('disconnect: a sentence with no notice kept for it stays on the panel, escaped', async () => {
    const sentence = 'The connection kept in the data folder could not be read, so it was removed here and its token revoked. <b>x</b>';
    const f = fixture({
        '/switchboard/status': { state: 'not_connected', linked: false, can_manage: true },
        'POST /switchboard/disconnect': { unlinked: false, revoked: true, deleted: false, slug: '', sentence, why: '', pending: false, say: true },
    });
    await f.page.disconnectClaude();
    assert.match(f.panel.innerHTML, /id="claude-relay-error" role="alert"/);
    assert.ok(f.panel.innerHTML.includes('could not be read, so it was removed here'));
    assert.ok(f.panel.innerHTML.includes('&lt;b&gt;x&lt;/b&gt;'));
    assert.ok(!f.panel.innerHTML.includes('<b>x</b>'));
    assert.ok(buttons(f.panel.innerHTML).includes('Dismiss'));
});

test('a refresh that changes nothing draws nothing', async () => {
    const f = fixture({ '/switchboard/status': CONNECTED });
    await f.page.loadSwitchboard();
    let draws = 0;
    let html = f.panel.innerHTML;
    Object.defineProperty(f.panel, 'innerHTML', { get: () => html, set: v => { draws += 1; html = v; } });
    await f.page.loadSwitchboard();
    await f.page.loadSwitchboard();
    assert.equal(draws, 0);
    f.page._claude.error = 'The relay could not be reached.';
    f.page._drawSwitchboard();
    assert.equal(draws, 1);
});
