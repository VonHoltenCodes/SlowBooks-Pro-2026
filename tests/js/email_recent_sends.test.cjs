// Recent sends rendering (#447): the email_log list and the saved-secret
// note, exercised without a browser. settings.js declares `const SettingsPage`
// and never attaches it to window, so the script is run with a line appended
// that hands the object out through the context.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load() {
    const context = {
        escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, character => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[character]),
        window: {}, document: { getElementById: () => null },
        T: value => value,  // business-word wrapper; identity in these tests
    };
    vm.createContext(context);
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/settings.js'), 'utf8')
        + '\n;globalThis.__SettingsPage = SettingsPage;';
    vm.runInContext(src, context);
    return context.__SettingsPage;
}

const SettingsPage = load();

test('each source gets a plain-language label', () => {
    assert.equal(SettingsPage._emailSourceLabel('invoice'), 'Invoice');
    assert.equal(SettingsPage._emailSourceLabel('statement'), 'Statement');
    assert.equal(SettingsPage._emailSourceLabel('collection'), 'Collection letter');
    assert.equal(SettingsPage._emailSourceLabel('settings_test'), 'Test');
    assert.equal(SettingsPage._emailSourceLabel('portal'), 'Portal');
    assert.equal(SettingsPage._emailSourceLabel('acknowledgment_cash'), 'Acknowledgment');
    assert.equal(SettingsPage._emailSourceLabel(''), '—');
});

test('a failed row shows the sentence the API sends, with the raw error under it', () => {
    // The mapping lives once, in app/services/email_errors.py; the list reads
    // the `failure` sentence GET /api/email-log returns for a failed row.
    const gmailErr = "(535, b'5.7.8 Username and Password not accepted.')";
    const gmail = SettingsPage._emailLogFailure({
        error_message: gmailErr, failure: 'Google will not accept a normal password here.',
    });
    assert.equal(gmail.sentence, 'Google will not accept a normal password here.');
    assert.equal(gmail.raw, gmailErr);

    // no sentence from the server (an older row shape): the plain fallback
    const unknown = SettingsPage._emailLogFailure({ error_message: 'weird thing' });
    assert.equal(unknown.sentence, 'The email could not be sent.');
    assert.equal(unknown.raw, 'weird thing');
});

test('settings.js keeps no second copy of the failure mapping', () => {
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/settings.js'), 'utf8');
    assert.doesNotMatch(src, /rejected the username or password/);
    assert.doesNotMatch(src, /could not be reached/);
});

test('the rows table shows the failure reason and escapes user text', () => {
    const html = SettingsPage._emailLogRowsHtml([
        { sent_at: '2026-10-01T12:00:00+00:00', recipient: 'a@x.com', subject: 'Invoice #1', entity_type: 'invoice', status: 'sent', error_message: null },
        { sent_at: '2026-10-01T12:10:00+00:00', recipient: '<b>@x.com', subject: 'Test', entity_type: 'settings_test', status: 'failed', error_message: "(535, b'nope')", failure: 'The server rejected the username or password — check both.' },
    ]);
    assert.match(html, /Invoice #1/);
    assert.match(html, /Failed/);
    assert.match(html, /rejected the username or password/);  // actionable sentence
    assert.match(html, /\(535, b&#39;nope&#39;\)/);            // raw error under it
    assert.doesNotMatch(html, /<b>@x\.com/);                  // recipient escaped
    assert.match(html, /&lt;b&gt;@x\.com/);
});

test('an empty list says so rather than drawing an empty table', () => {
    assert.match(SettingsPage._emailLogRowsHtml([]), /No emails have been sent yet/);
});

test('a saved secret shows the placeholder note; an unset one shows nothing', () => {
    assert.match(SettingsPage._secretNote('********'), /type a new value only to change it/);
    assert.equal(SettingsPage._secretNote(''), '');
    assert.equal(SettingsPage._secretNote('a-real-value'), '');
});

test('the sentence, then the server reply once under it, as plain lines', () => {
    // the sentence never holds the server's words, so both lines show
    const said = SettingsPage._emailLogFailure({
        error_message: '554 5.7.1 Message rejected as spam',
        failure: 'The mail server refused the message.',
    });
    assert.equal(said.sentence, 'The mail server refused the message.');
    assert.equal(said.raw, '554 5.7.1 Message rejected as spam');
    // a reader who is given the sentence in both fields sees it once
    const same = SettingsPage._emailLogFailure({
        error_message: 'The mail server asked to try again later.',
        failure: 'The mail server asked to try again later.',
    });
    assert.equal(same.raw, '');
    // one refused address per line, kept as lines and escaped
    const html = SettingsPage._emailLogRowsHtml([
        { sent_at: '2026-11-20T12:00:00+00:00', recipient: 'a@x.com', subject: 'S', entity_type: 'invoice', status: 'failed',
          error_message: 'a@x.com: 550 5.1.1 User unknown\nb@x.com: 550 <no>', failure: 'The address was refused by the mail server: a@x.com, b@x.com. Check it and send again.' },
    ]);
    assert.match(html, /white-space:pre-line/);
    assert.match(html, /a@x\.com: 550 5\.1\.1 User unknown\nb@x\.com: 550 &lt;no&gt;/);
});

test('every kind of send has a name in the What column, never its code', () => {
    // each entity_type send_email() is called with (held to this list by
    // tests/test_email_failure_sentences.py)
    const named = {
        invoice: 'Invoice', estimate: 'Estimate', reminder: 'Payment reminder',
        statement: 'Statement', collection: 'Collection letter',
        giving_statement: 'Giving statement', settings_test: 'Test',
        acknowledgment_receipt: 'Acknowledgment', acknowledgment_letter: 'Acknowledgment',
    };
    for (const [code, label] of Object.entries(named)) {
        assert.equal(SettingsPage._emailSourceLabel(code), label);
    }
});

test('a sent row whose copy was refused says so under Sent', () => {
    const html = SettingsPage._emailLogRowsHtml([
        { sent_at: '2026-11-20T12:00:00+00:00', recipient: 'a@x.com', cc: 'b@x.com', subject: 'S', entity_type: 'invoice', status: 'sent',
          note: 'The mail server refused a copy to: b@x.com.', error_message: 'b@x.com: 550 5.1.1 <nobody>', failure: null },
        { sent_at: '2026-11-20T12:01:00+00:00', recipient: 'c@x.com', subject: 'T', entity_type: 'invoice', status: 'sent',
          note: 'The mail server refused a copy to: d@x.com.', error_message: null, failure: null },
        { sent_at: '2026-11-20T12:02:00+00:00', recipient: 'e@x.com', subject: 'U', entity_type: 'invoice', status: 'sent',
          note: null, error_message: null, failure: null },
    ]);
    assert.match(html, /Sent<div[^>]*>The mail server refused a copy to: b@x\.com\.<\/div>/);
    // the server's reply under it, escaped (an administrator's row has it)
    assert.match(html, /b@x\.com: 550 5\.1\.1 &lt;nobody&gt;/);
    assert.match(html, /refused a copy to: d@x\.com\./);
    assert.equal((html.match(/refused a copy/g) || []).length, 2);
    assert.doesNotMatch(html, /Failed/);
});
