// Connect to AI assistant in Settings, Connections (#1087): the panel's states. The
// words are for everyone; an administrator gets the buttons: Connect to AI assistant
// when not connected; the connector address with Copy, Show a sign-in code
// and Disconnect when connected here; Add for a company not on the
// connection, with no other company's name. The code shows with its ten-minute life. A
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
// connected here, with this company alone on the connection, as its
// administrator sees it
const CONNECTED = {
    state: 'connected', linked: true, on_link: true, this_company: true, company: 'Larkspur Supply',
    here_file: 'larkspur.db', address: ADDRESS, token_label: 'AI assistant (relay)', can_manage: true,
    companies: [{ file: 'larkspur.db', name: 'Larkspur Supply', books: 'larkspur~k3f9q2', state: 'ready', here: true }],
    count_others: 0, can_code: true, can_take_off: true, can_disconnect: true, can_add: false,
};

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
    assert.deepEqual(buttons(html), ['Copy', 'Show a sign-in code', 'Disconnect the AI assistant']);
    // someone who is not an administrator: the state, and no other company
    const g = fixture({ '/switchboard/status': {
        ...CONNECTED, can_manage: false, companies: undefined, count_others: undefined,
        can_code: false, can_take_off: false, can_disconnect: false,
    } });
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

test('another company is on the connection: this one is offered Add, with no other name', async () => {
    // an administrator of this company who may add it: the server gives no
    // other company's name or count
    const f = fixture({ '/switchboard/status': {
        state: 'not_connected', linked: true, on_link: false, this_company: false, company: '',
        here_name: 'Smith Plumbing', address: '', can_manage: true, can_add: true,
        can_code: false, can_take_off: false, can_disconnect: false,
    } });
    await f.page.loadSwitchboard();
    assert.equal(state(f.panel.innerHTML), 'Not connected');
    assert.ok(f.panel.innerHTML.includes("Add Smith Plumbing to the AI assistant connection, and the assistant can work Smith Plumbing&#39;s books as a bookkeeper. Every change it makes is recorded in Smith Plumbing&#39;s audit log as token:AI assistant (relay)."));
    assert.deepEqual(buttons(f.panel.innerHTML), ['Add Smith Plumbing to the AI connection']);
    assert.ok(!f.panel.innerHTML.includes('claude-relay-companies'));
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

// One press at a time, through every refresh: the panel's rule, point 6
// (app/services/switchboard_link.py).
function later() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
}
const tick = () => new Promise(r => setImmediate(r));
const NOT_LINKED = { state: 'not_connected', linked: false, can_manage: true, pending: [{ slug: 'older-name', sentence: 'A record is still held.', dismissed: false }] };
const connectButton = html => (html.match(/<button[^>]*id="claude-connect"[^>]*>[^<]*<\/button>/) || [''])[0];

test('a refresh while Connect is being worked on does not give the button back', async () => {
    const answer = later();
    const f = fixture({ '/switchboard/status': NOT_LINKED, 'POST /switchboard/connect': answer.promise });
    await f.page.loadSwitchboard();
    assert.ok(!connectButton(f.panel.innerHTML).includes(' disabled'));
    const press = f.page.connectClaude();
    await tick();
    assert.match(connectButton(f.panel.innerHTML), / disabled[^>]*>Connecting…</);
    // what the timer does, while the press is still in flight
    await f.page.loadSwitchboard();
    await f.page.loadSwitchboard();
    assert.match(connectButton(f.panel.innerHTML), / disabled[^>]*>Connecting…</);
    // a second press meanwhile asks the server for nothing
    await f.page.connectClaude();
    assert.equal(f.sent.filter(s => s[0] === 'POST').length, 1);
    answer.resolve({ ...CONNECTED, pending: NOT_LINKED.pending });
    await press;
    assert.equal(state(f.panel.innerHTML), 'Connected');
    assert.ok(!f.panel.innerHTML.includes('claude-relay-error'));
    assert.ok(!f.panel.innerHTML.includes(' disabled'));
});

test('while a sign-in code or Disconnect is being worked on, the panel takes no other press', async () => {
    for (const [press, path] of [['claudeCode', '/switchboard/code'], ['disconnectClaude', '/switchboard/disconnect']]) {
        const answer = later();
        const f = fixture({ '/switchboard/status': CONNECTED, ['POST ' + path]: answer.promise });
        await f.page.loadSwitchboard();
        const going = f.page[press]();
        await tick();
        const off = [...f.panel.innerHTML.matchAll(/<button[^>]*id="claude-(?:code|disconnect)"[^>]*>/g)].map(m => m[0]);
        assert.equal(off.length, 2);
        assert.ok(off.every(b => b.includes(' disabled')), press);
        await f.page.loadSwitchboard();
        await f.page.claudeCode();
        await f.page.disconnectClaude();
        assert.equal(f.sent.filter(s => s[0] === 'POST').length, 1, press);
        answer.resolve(press === 'claudeCode' ? { code: 'shown', expires: null } : { unlinked: true, revoked: true, deleted: true });
        await going;
        assert.ok(!f.panel.innerHTML.includes(' disabled'), press);
    }
});

test('"another press is working on it" goes when the panel shows the connection', async () => {
    const busy = Object.assign(new Error('The AI assistant connection is still being worked on from another press. Try again in a moment.'), { status: 409 });
    const answers = { '/switchboard/status': NOT_LINKED, 'POST /switchboard/connect': busy };
    const f = fixture(answers);
    await f.page.loadSwitchboard();
    await f.page.connectClaude();
    assert.ok(f.panel.innerHTML.includes('still being worked on'));
    // still not connected: the sentence stays
    await f.page.loadSwitchboard();
    assert.ok(f.panel.innerHTML.includes('still being worked on'));
    // the other press connected: a connected panel does not say a press is being worked on
    answers['/switchboard/status'] = CONNECTED;
    await f.page.loadSwitchboard();
    assert.equal(state(f.panel.innerHTML), 'Connected');
    assert.ok(!f.panel.innerHTML.includes('still being worked on'));
    assert.ok(!f.panel.innerHTML.includes('claude-relay-error'));
});

test('a failure that is not about another press stays until dismissed', async () => {
    const away = Object.assign(new Error('The relay could not be reached.'), { status: 503 });
    const answers = { '/switchboard/status': NOT_LINKED, 'POST /switchboard/connect': away };
    const f = fixture(answers);
    await f.page.loadSwitchboard();
    await f.page.connectClaude();
    answers['/switchboard/status'] = CONNECTED;
    await f.page.loadSwitchboard();
    assert.ok(f.panel.innerHTML.includes('The relay could not be reached.'));
});

test('a read asked before a press answered does not draw over what the press made', async () => {
    const stale = later();
    const answers = { '/switchboard/status': NOT_LINKED, 'POST /switchboard/connect': CONNECTED };
    const f = fixture(answers);
    await f.page.loadSwitchboard();
    answers['/switchboard/status'] = stale.promise;
    const read = f.page.loadSwitchboard();   // the timer's read, slow to come back
    await f.page.connectClaude();
    assert.equal(state(f.panel.innerHTML), 'Connected');
    stale.resolve(NOT_LINKED);               // it says what was true before the press
    await read;
    assert.equal(state(f.panel.innerHTML), 'Connected');
    assert.deepEqual(buttons(f.panel.innerHTML).slice(-2), ['Show a sign-in code', 'Disconnect the AI assistant']);
});

// One connection, several companies (2.32.0-beta.2): what the panel says to
// each person, by what the server gives them.
const THREE = [
    { file: 'harbor.db', name: 'Harbor Books', books: 'harbor~q7m2xa', state: 'ready', here: true },
    { file: 'smith.db', name: 'Smith Plumbing', books: 'smith~k3f9q2', state: 'not_open_here', here: false },
    { file: 'lake.db', name: 'Lake Dental', books: 'lake~p4ww7n', state: 'refused', here: false },
];
const MANAGER = {
    ...CONNECTED, company: 'Harbor Books', here_file: 'harbor.db', companies: THREE, count_others: 2,
};
// The words of a piece of markup: tags are taken out until none is left, so
// one that only appears once another is removed goes too.
const text = html => {
    let out = String(html);
    let before;
    do {
        before = out;
        out = out.replace(/<[^>]*>/g, '');
    } while (out !== before);
    return out.replace(/&#39;/g, "'").replace(/&amp;/g, '&');
};

test('a manager sees every company, each with its note, and buttons named for their company', async () => {
    const f = fixture({ '/switchboard/status': MANAGER });
    await f.page.loadSwitchboard();
    const html = f.panel.innerHTML;
    assert.ok(text(html).includes('Connected. The AI assistant can work the books of: Harbor Books (this company), Smith Plumbing (not open on this computer), Lake Dental (turned off in that company).'));
    assert.ok(text(html).includes('On this computer only the open company answers. When the assistant asks for another, open that company here.'));
    assert.deepEqual(buttons(html), [
        'Copy', 'Show a sign-in code', 'Take Harbor Books off the AI connection',
        'Take Lake Dental off the AI connection', 'Disconnect the AI assistant',
    ]);
    // each name is a block keyed by its file, so a refresh keeps a selection in it
    for (const co of THREE) assert.ok(html.includes(`id="claude-relay-co-${f.page.claudeKey(co.file)}"`), co.file);
    assert.equal(new Set(buttons(html)).size, buttons(html).length);
    // on a server, the note says so and the desktop line is not there
    const g = fixture({ '/switchboard/status': { ...MANAGER, server_edition: true, companies: THREE.map(c => (c.state === 'not_open_here' ? { ...c, state: 'not_served' } : c)) } });
    await g.page.loadSwitchboard();
    assert.ok(text(g.panel.innerHTML).includes('Smith Plumbing (not served by this server now)'));
    assert.ok(!g.panel.innerHTML.includes('claude-relay-desktop'));
});

test('each state of a company has its note', () => {
    const f = fixture();
    const said = {
        not_open_here: 'not open on this computer', other_window: 'open in another window',
        not_served: 'not served by this server now', locked: 'locked', refused: 'turned off in that company',
        maker_gone: 'the person who added it is no longer an administrator there', ready: '',
    };
    for (const [st, words] of Object.entries(said)) assert.equal(f.page.claudeNote(st), words, st);
});

test('the sign-in code says it reaches every company listed', async () => {
    const f = fixture({ '/switchboard/status': MANAGER, 'POST /switchboard/code': { code: 'shown', expires: null } });
    await f.page.loadSwitchboard();
    await f.page.claudeCode();
    assert.ok(text(f.panel.innerHTML).includes('Anyone who signs an assistant in with this code can work every company listed above.'));
    // with one company there is no list to point at
    const g = fixture({ '/switchboard/status': CONNECTED, 'POST /switchboard/code': { code: 'shown', expires: null } });
    await g.page.loadSwitchboard();
    await g.page.claudeCode();
    assert.ok(!g.panel.innerHTML.includes('claude-relay-code-reach'));
});

test('on the connection without power over it: this company alone, and Take off for its administrator', async () => {
    const mine = {
        state: 'connected', linked: true, on_link: true, this_company: true, company: 'Smith Plumbing',
        here_file: 'smith.db', token_label: 'AI assistant (relay)', address: ADDRESS, can_manage: true,
        can_take_off: true, can_code: false, can_disconnect: false, can_add: false,
    };
    const f = fixture({ '/switchboard/status': mine });
    await f.page.loadSwitchboard();
    const html = f.panel.innerHTML;
    assert.ok(text(html).includes("Smith Plumbing is on this computer's AI assistant connection. The assistant works these books as a bookkeeper; every change is recorded here as token:AI assistant (relay)."));
    assert.deepEqual(buttons(html), ['Take Smith Plumbing off the AI connection']);
    assert.ok(!html.includes('Harbor') && !html.includes('Lake'));
    assert.ok(!html.includes('claude-relay-address'));
    // a bookkeeper: the same words, nothing to press
    const g = fixture({ '/switchboard/status': { ...mine, can_manage: false, can_take_off: false } });
    await g.page.loadSwitchboard();
    assert.ok(g.panel.innerHTML.includes('claude-relay-on-link'));
    assert.deepEqual(buttons(g.panel.innerHTML), []);
});

test('the server says why there is nothing to press: its sentence, no buttons', async () => {
    for (const sentence of [
        'An AI assistant connection already exists on this server. Only someone who is an administrator of every company on it can add Lake Dental.',
        'On PostgreSQL this server serves one company, so the connection reaches only this one.',
    ]) {
        const f = fixture({ '/switchboard/status': { state: 'not_connected', linked: true, on_link: false, this_company: false, can_manage: true, sentence } });
        await f.page.loadSwitchboard();
        assert.ok(text(f.panel.innerHTML).includes(sentence));
        assert.deepEqual(buttons(f.panel.innerHTML), []);
    }
    const g = fixture({ '/switchboard/status': { state: 'not_connected', linked: true, on_link: false, can_manage: false, sentence: 'Only an administrator can connect an AI assistant.' } });
    await g.page.loadSwitchboard();
    assert.ok(g.panel.innerHTML.includes('Only an administrator can connect an AI assistant.'));
    assert.deepEqual(buttons(g.panel.innerHTML), []);
});

test('Add asks first in the exact words, then shows the company on the connection', async () => {
    const offered = {
        state: 'connected', linked: true, on_link: false, this_company: false, here_name: 'Smith Plumbing',
        can_manage: true, can_add: true,
    };
    const added = { ...MANAGER, company: 'Smith Plumbing', here_file: 'smith.db', results: [{ file: 'smith.db', name: 'Smith Plumbing', result: 'added' }] };
    const f = fixture({ '/switchboard/status': offered, 'POST /switchboard/add': added });
    await f.page.loadSwitchboard();
    await f.page.addClaudeCompany();
    assert.equal(f.confirms[0], "Add Smith Plumbing? Every assistant already signed in to this connection, and any signed in later, will be able to read and change Smith Plumbing's books as a bookkeeper. You can take it off again here at any time.");
    assert.deepEqual(f.sent.at(-1), ['POST', '/switchboard/add']);
    assert.ok(f.panel.innerHTML.includes('claude-relay-companies'));
    assert.equal(f.toasts.at(-1)[0], 'Smith Plumbing is on the AI assistant connection.');
});

test('Take off asks first in the exact words and names the file', async () => {
    const answers = { '/switchboard/status': MANAGER, 'POST /switchboard/take-off': { ...MANAGER, companies: THREE.slice(1), results: [{ file: 'harbor.db', name: 'Harbor Books', result: 'ended' }] } };
    const f = fixture(answers);
    await f.page.loadSwitchboard();
    await f.page.takeClaudeOff('');
    assert.equal(f.confirms[0], 'Take Harbor Books off? Its token, AI assistant (relay), is turned off at once and the assistant can no longer reach these books. The other companies stay connected.');
    assert.deepEqual(f.sent.at(-1), ['POST', '/switchboard/take-off']);
    assert.equal(f.toasts.at(-1)[0], 'Harbor Books is off the AI assistant connection, and its token is turned off.');
    // a dead entry of another company, by its own button
    const g = fixture({ '/switchboard/status': MANAGER, 'POST /switchboard/take-off': { ...MANAGER, results: [{ file: 'lake.db', name: 'Lake Dental', result: 'later' }] } });
    await g.page.loadSwitchboard();
    await g.page.takeClaudeOff('lake.db');
    assert.match(g.confirms[0], /^Take Lake Dental off the AI connection\? /);
    assert.equal(g.toasts.at(-1)[0], 'Lake Dental is off the AI assistant connection. It is not open now; its token will be turned off the next time it is opened.');
});

test('Take off asked of a client administrator says nothing of other companies', async () => {
    const mine = {
        state: 'connected', linked: true, on_link: true, this_company: true, company: 'Smith Plumbing',
        here_file: 'smith.db', token_label: 'AI assistant (relay) 2', address: ADDRESS, can_manage: true,
        can_take_off: true, can_code: false, can_disconnect: false, can_add: false,
    };
    const f = fixture({ '/switchboard/status': mine, 'POST /switchboard/take-off': { ...mine, on_link: false, this_company: false, results: [{ file: 'smith.db', name: 'Smith Plumbing', result: 'ended' }] } });
    await f.page.loadSwitchboard();
    await f.page.takeClaudeOff('');
    assert.equal(f.confirms[0], 'Take Smith Plumbing off? Its token, AI assistant (relay) 2, is turned off at once and the assistant can no longer reach these books.');
    // a manager dropping a company whose token is already off there
    const g = fixture({ '/switchboard/status': MANAGER, 'POST /switchboard/take-off': { ...MANAGER, results: [{ file: 'lake.db', name: 'Lake Dental', result: 'ended' }] } });
    await g.page.loadSwitchboard();
    await g.page.takeClaudeOff('lake.db');
    assert.equal(g.confirms[0], 'Take Lake Dental off the AI connection? Its token is already turned off in that company; this removes it from the connection. The other companies stay connected.');
});

test('a company whose token\'s maker is no longer an administrator is said to be that, and offered Take off', async () => {
    const gone = THREE.map(co => (co.file === 'lake.db' ? { ...co, state: 'maker_gone' } : co));
    const status = { ...MANAGER, companies: gone };
    const f = fixture({ '/switchboard/status': status, 'POST /switchboard/take-off': { ...status, results: [{ file: 'lake.db', name: 'Lake Dental', result: 'ended' }] } });
    await f.page.loadSwitchboard();
    const html = f.panel.innerHTML;
    assert.ok(text(html).includes('Lake Dental (the person who added it is no longer an administrator there)'));
    assert.ok(!text(html).includes('turned off in that company'));
    assert.ok(buttons(html).includes('Take Lake Dental off the AI connection'));
    await f.page.takeClaudeOff('lake.db');
    assert.equal(f.confirms[0], 'Take Lake Dental off the AI connection? The person who added it is no longer an administrator there, so that company refuses its token; this removes it from the connection and turns the token off. The other companies stay connected.');
});

test('Disconnect from several companies asks in the exact words, then says which are not open', async () => {
    const f = fixture({
        '/switchboard/status': MANAGER,
        'POST /switchboard/disconnect': {
            unlinked: true, revoked: true, deleted: true, slug: 'ztnqj8djvg', sentence: 'x', say: true,
            results: [
                { file: 'harbor.db', name: 'Harbor Books', result: 'ended' },
                { file: 'smith.db', name: 'Smith Plumbing', result: 'ended' },
                { file: 'lake.db', name: 'Lake Dental', result: 'later' },
            ],
        },
    });
    await f.page.loadSwitchboard();
    await f.page.disconnectClaude();
    assert.equal(f.confirms[0], "Disconnect the AI assistant from all 3 companies? The relay forgets this connection and each company's AI assistant token is turned off: Harbor Books, Smith Plumbing, Lake Dental. A company that is not open now has its token turned off the next time it opens; until then it cannot be used, because its secret is deleted from this computer.");
    assert.equal(f.page._claude.error, 'Disconnected. Lake Dental is not open now; its token will be turned off the next time it is opened.');
    const two = f.page.claudeDisconnectQuestion(MANAGER, THREE.slice(0, 2));
    assert.match(two, /^Disconnect the AI assistant from both companies\? /);
});

test('a busy connection says so, and the sentence stays', async () => {
    const busy = Object.assign(new Error('Another change to the AI assistant connection is under way. Try again in a moment.'), { status: 409 });
    const offered = { state: 'connected', linked: true, on_link: false, here_name: 'Smith Plumbing', can_manage: true, can_add: true };
    const f = fixture({ '/switchboard/status': offered, 'POST /switchboard/add': busy });
    await f.page.loadSwitchboard();
    await f.page.addClaudeCompany();
    await f.page.loadSwitchboard();
    assert.ok(f.panel.innerHTML.includes('Another change to the AI assistant connection is under way. Try again in a moment.'));
});

test('Connect pressed while a connection exists: "Use Add instead" stays while Add is offered', async () => {
    const already = Object.assign(new Error('An AI assistant connection already exists on this computer. Use Add instead.'), { status: 409 });
    const answers = { '/switchboard/status': { state: 'not_connected', linked: false, can_manage: true }, 'POST /switchboard/connect': already };
    const f = fixture(answers);
    await f.page.loadSwitchboard();
    answers['/switchboard/status'] = { state: 'connected', linked: true, on_link: false, here_name: 'Smith Plumbing', can_manage: true, can_add: true };
    await f.page.connectClaude();
    assert.ok(f.panel.innerHTML.includes('Use Add instead.'));
    assert.ok(buttons(f.panel.innerHTML).includes('Add Smith Plumbing to the AI connection'));
});

test('a company name is shown as text, never as markup', async () => {
    const odd = [{ file: 'x.db', name: '<img src=x onerror=1>', books: 'x~aaaaaa', state: 'refused', here: false }, THREE[0]];
    const f = fixture({ '/switchboard/status': { ...MANAGER, companies: odd } });
    await f.page.loadSwitchboard();
    assert.ok(!f.panel.innerHTML.includes('<img'));
    assert.ok(f.panel.innerHTML.includes('&lt;img src=x onerror=1&gt;'));
});

test('Add names the token it will make, " 2" and all', async () => {
    const offered = {
        state: 'connected', linked: true, on_link: false, this_company: false, here_name: 'Smith Plumbing',
        can_manage: true, can_add: true, next_label: 'AI assistant (relay) 2',
    };
    const f = fixture({ '/switchboard/status': offered });
    await f.page.loadSwitchboard();
    assert.ok(text(f.panel.innerHTML).includes("Every change it makes is recorded in Smith Plumbing's audit log as token:AI assistant (relay) 2."));
});

test('a manager offered no code is told which companies it waits for', async () => {
    const f = fixture({ '/switchboard/status': { ...MANAGER, can_code: false, code_needs: ['Lake Dental'] } });
    await f.page.loadSwitchboard();
    assert.ok(text(f.panel.innerHTML).includes('A sign-in code reaches every company on the connection, so it is offered only while each of them can answer now and you are an administrator of each. Not now: Lake Dental.'));
    assert.ok(!buttons(f.panel.innerHTML).includes('Show a sign-in code'));
    const g = fixture({ '/switchboard/status': MANAGER });
    await g.page.loadSwitchboard();
    assert.ok(!g.panel.innerHTML.includes('claude-relay-code-needs'));
});

test('this company, turned off, is "turned off in this company"', async () => {
    const mine = THREE.map(c => (c.here ? { ...c, state: 'refused' } : c));
    const f = fixture({ '/switchboard/status': { ...MANAGER, companies: mine } });
    await f.page.loadSwitchboard();
    assert.ok(text(f.panel.innerHTML).includes('Harbor Books (this company; turned off in this company)'));
    assert.ok(text(f.panel.innerHTML).includes('Lake Dental (turned off in that company)'));
});

test('a long company name wraps in the list and in its button', async () => {
    const long = 'L'.repeat(85);
    const list = THREE.map(c => (c.file === 'lake.db' ? { ...c, name: long } : c));
    const f = fixture({ '/switchboard/status': { ...MANAGER, companies: list } });
    await f.page.loadSwitchboard();
    const html = f.panel.innerHTML;
    assert.match(html, /id="claude-relay-companies" style="[^"]*overflow-wrap:anywhere/);
    assert.match(html, /id="claude-take-off-lake_2e_db"[^>]*overflow-wrap:anywhere/);
});

test('a company turned off here is offered Add again', async () => {
    const dead = {
        ...CONNECTED, can_add: true, here_name: 'Larkspur Supply', next_label: 'AI assistant (relay)',
        companies: [{ ...CONNECTED.companies[0], state: 'refused' }],
    };
    const f = fixture({ '/switchboard/status': dead });
    await f.page.loadSwitchboard();
    assert.ok(buttons(f.panel.innerHTML).includes('Add Larkspur Supply to the AI connection'));
});
