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

test('a failed row gives an actionable sentence with the raw error under it', () => {
    // A real Gmail rejection carries a support.google.com URL, which is how
    // the list (which has no host) knows to name the app password.
    const gmailErr = "(535, b'5.7.8 Username and Password not accepted. https://support.google.com/mail/?p=BadCredentials')";
    const gmail = SettingsPage._emailLogFailure({ error_message: gmailErr });
    assert.match(gmail.sentence.toLowerCase(), /app password/);
    assert.equal(gmail.raw, gmailErr);

    const otherAuth = SettingsPage._emailLogFailure({ error_message: "(535, b'bad creds')" });
    assert.match(otherAuth.sentence.toLowerCase(), /username or password/);

    const conn = SettingsPage._emailLogFailure({ error_message: '[Errno 61] Connection refused' });
    assert.match(conn.sentence.toLowerCase(), /host, port/);

    const unknown = SettingsPage._emailLogFailure({ error_message: 'weird thing' });
    assert.equal(unknown.sentence, 'The email could not be sent.');
    assert.equal(unknown.raw, 'weird thing');
});

test('the rows table shows the failure reason and escapes user text', () => {
    const html = SettingsPage._emailLogRowsHtml([
        { sent_at: '2026-10-01T12:00:00+00:00', recipient: 'a@x.com', subject: 'Invoice #1', entity_type: 'invoice', status: 'sent', error_message: null },
        { sent_at: '2026-10-01T12:10:00+00:00', recipient: '<b>@x.com', subject: 'Test', entity_type: 'settings_test', status: 'failed', error_message: "(535, b'nope')" },
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
