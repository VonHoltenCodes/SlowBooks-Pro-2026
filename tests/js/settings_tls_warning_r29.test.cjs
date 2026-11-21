// Settings, Email (SMTP): the warning beside Use TLS while it is No. The
// field is drawn by SettingsPage._useTlsFieldHtml and kept by tlsChanged,
// exercised here without a browser (settings.js never attaches SettingsPage
// to window, so a line appended to the script hands it out).

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const WARNING = 'With Use TLS off, your mail password and your mail are sent without encryption.';

function load() {
    const note = { textContent: 'unset' };
    const context = {
        escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, character => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[character]),
        window: {},
        document: { getElementById: id => (id === 'smtp-tls-off-warning' ? note : null) },
        T: value => value,
    };
    vm.createContext(context);
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/settings.js'), 'utf8')
        + '\n;globalThis.__SettingsPage = SettingsPage;';
    vm.runInContext(src, context);
    return { SettingsPage: context.__SettingsPage, note };
}

const { SettingsPage, note } = load();

// what the warning's element holds, from the field's HTML
function shown(html) {
    const m = html.match(/<div id="smtp-tls-off-warning"[^>]*>([^<]*)<\/div>/);
    assert.ok(m, 'the warning element is always drawn');
    return m[1];
}

test('the page opens with the warning when No is saved', () => {
    const html = SettingsPage._useTlsFieldHtml({ smtp_use_tls: 'false' });
    assert.equal(shown(html), WARNING);
    assert.match(html, /<option value="false" selected>No<\/option>/);
});

test('Yes, and a company that never set it, show no warning', () => {
    for (const s of [{ smtp_use_tls: 'true' }, {}]) {
        const html = SettingsPage._useTlsFieldHtml(s);
        assert.equal(shown(html), '');
        assert.match(html, /<option value="true" selected>Yes<\/option>/);
    }
});

// The mailer encrypts only when the saved value is "true" in any case; the
// API stores whatever it is sent, so the page reads the value the same way.
test('any saved value the mailer reads as off shows No and the warning', () => {
    for (const saved of ['False', 'FALSE', 'no', '0', 'off', '']) {
        const html = SettingsPage._useTlsFieldHtml({ smtp_use_tls: saved });
        assert.equal(shown(html), WARNING, JSON.stringify(saved));
        assert.match(html, /<option value="false" selected>No<\/option>/, JSON.stringify(saved));
        assert.doesNotMatch(html, /<option value="true" selected>/, JSON.stringify(saved));
    }
    for (const saved of ['True', 'TRUE']) {
        const html = SettingsPage._useTlsFieldHtml({ smtp_use_tls: saved });
        assert.equal(shown(html), '', saved);
        assert.match(html, /<option value="true" selected>Yes<\/option>/, saved);
    }
});

test('the warning is tied to the select and announced politely', () => {
    const html = SettingsPage._useTlsFieldHtml({ smtp_use_tls: 'false' });
    assert.match(html, /<select name="smtp_use_tls" aria-describedby="smtp-tls-off-warning"/);
    assert.match(html, /<div id="smtp-tls-off-warning" role="status"/);
    // in the open: not hidden, and not behind the tortoise
    const el = html.match(/<div id="smtp-tls-off-warning"[^>]*>/)[0];
    assert.doesNotMatch(el, /hidden|data-hint/);
    // the theme's warning color, which both themes define above 4.5:1
    assert.match(el, /color:var\(--text-warning\)/);
});

test('choosing No shows it at once and Yes takes it away', () => {
    SettingsPage.tlsChanged({ value: 'false' });
    assert.equal(note.textContent, WARNING);
    SettingsPage.tlsChanged({ value: 'true' });
    assert.equal(note.textContent, '');
});

test('the setting is still saved under the same name with the same two values', () => {
    const html = SettingsPage._useTlsFieldHtml({ smtp_use_tls: 'false' });
    assert.deepEqual([...html.matchAll(/<option value="([^"]*)"/g)].map(m => m[1]), ['true', 'false']);
    assert.equal((html.match(/name="/g) || []).length, 1);
});
