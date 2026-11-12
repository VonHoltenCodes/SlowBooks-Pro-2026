// The Contact support form's mailto builder (support.js). The form opens the
// user's own mail client with a mailto: link — the app sends nothing — so the
// link's shape is what matters: addressed to support, an encodeURIComponent'd
// subject carrying [SBP], and a body kept under the encoded length some mail
// clients truncate. Also covers the degraded path when diagnostics can't load.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load(api) {
    const context = {
        window: {},
        navigator: { userAgent: 'TestUA/1.0' },
        document: { addEventListener() {} },
        API: api || {},
    };
    vm.createContext(context);
    const js = fs.readFileSync(path.join(__dirname, '../../app/static/js/support.js'), 'utf8');
    vm.runInContext(js, context);
    return context.window.SupportPage;  // support.js sets window.SupportPage
}

const DIAG = {
    version: '2.29.1', commit: 'abc1234def56', platform: 'macOS', deployment: 'native',
    is_native: true, reply_to: 'me@co.com',
    environment: 'Version: v2.29.1+abc1234def56\nPlatform: macOS\nPython: 3.13.0\nDeployment: native',
    log_tail: '2026-10-25 09:15:01 INFO ready', log_available: true, browser: 'TestUA/1.0',
};

const bodyOf = (href) => decodeURIComponent(href.match(/body=(.*)$/)[1]);

test('the mailto is addressed to support with an [SBP] subject', () => {
    const SupportPage = load();
    const href = SupportPage.buildMailto(DIAG, { whatHappened: 'PDF is blank', whatExpected: 'a PDF' });
    assert.ok(href.startsWith('mailto:support@slowbookspro.com?'), href);
    const subject = decodeURIComponent(href.match(/subject=([^&]*)/)[1]);
    assert.equal(subject, '[SBP] v2.29.1 · macOS native · PDF is blank');
});

test('the body carries the user text, the environment and the log', () => {
    const SupportPage = load();
    const href = SupportPage.buildMailto(DIAG, { whatHappened: 'it broke', whatExpected: 'it to work', replyTo: 'me@co.com' });
    const body = bodyOf(href);
    assert.ok(body.includes('What happened:\nit broke'));
    assert.ok(body.includes('What you expected:\nit to work'));
    assert.ok(body.includes('Reply to: me@co.com'));
    assert.ok(body.includes('--- Environment ---'));
    assert.ok(body.includes('Deployment: native'));
    assert.ok(body.includes('2026-10-25 09:15:01 INFO ready'));
});

test('the browser/web-view UA is always included, native too', () => {
    const SupportPage = load();
    const body = SupportPage.buildBody(DIAG, { whatHappened: 'x', whatExpected: 'y' });
    assert.ok(body.includes('Browser: TestUA/1.0'));
});

test('a long log is trimmed so the ENCODED body stays within budget', () => {
    const SupportPage = load();
    const diag = Object.assign({}, DIAG, { log_tail: ('a line of log\n').repeat(4000) });
    const body = SupportPage.buildBody(diag, { whatHappened: 'x', whatExpected: 'y' });
    assert.ok(encodeURIComponent(body).length <= SupportPage.MAX_ENCODED_BODY,
        `encoded ${encodeURIComponent(body).length}`);
    assert.ok(body.includes('use Copy diagnostics'));
});

test('a huge user message drops the tail rather than shipping 40k', () => {
    const SupportPage = load();
    const diag = Object.assign({}, DIAG, { log_tail: 'SECRETLOGLINE\n'.repeat(4000) });
    const body = SupportPage.buildBody(diag, { whatHappened: 'x'.repeat(1650), whatExpected: 'y' });
    assert.ok(body.includes('x'.repeat(1650)));      // user text kept whole
    assert.ok(!body.includes('SECRETLOGLINE'));      // tail dropped
    assert.ok(body.length < 3000);
});

test('a short log is kept whole and within budget', () => {
    const SupportPage = load();
    const body = SupportPage.buildBody(DIAG, { whatHappened: 'x', whatExpected: 'y' });
    assert.ok(body.endsWith('2026-10-25 09:15:01 INFO ready'));
    assert.ok(!body.includes('use Copy diagnostics'));
    assert.ok(encodeURIComponent(body).length <= SupportPage.MAX_ENCODED_BODY);
});

test('an empty tail is handled', () => {
    const SupportPage = load();
    const diag = Object.assign({}, DIAG, { log_tail: '' });
    const body = SupportPage.buildBody(diag, { whatHappened: 'x', whatExpected: 'y' });
    assert.ok(body.includes('(log is empty)'));
});

test('a missing log says so and does not pretend to a tail', () => {
    const SupportPage = load();
    const diag = Object.assign({}, DIAG, { log_available: false, log_tail: '', log_note: 'console only' });
    const body = SupportPage.buildBody(diag, { whatHappened: 'x', whatExpected: 'y' });
    assert.ok(body.trimEnd().endsWith('console only'));
});

test('an empty what-happened still yields a usable subject', () => {
    const SupportPage = load();
    const href = SupportPage.buildMailto(DIAG, { whatHappened: '' });
    const subject = decodeURIComponent(href.match(/subject=([^&]*)/)[1]);
    assert.ok(subject.endsWith('support request'), subject);
});

test('a failed diagnostics load still yields a working form', async () => {
    const SupportPage = load({ get: () => Promise.reject(new Error('offline')) });
    const diag = await SupportPage.loadDiag();
    assert.equal(diag.log_available, false);
    assert.ok(diag.environment.includes('Diagnostics unavailable'));
    const href = SupportPage.buildMailto(diag, { whatHappened: 'app is broken' });
    assert.ok(href.startsWith('mailto:support@slowbookspro.com'));
    assert.ok(bodyOf(href).includes('What happened:\napp is broken'));
    assert.ok(bodyOf(href).includes('Diagnostics unavailable: offline'));
});
