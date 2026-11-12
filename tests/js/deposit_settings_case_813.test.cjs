// Settings, Deposits (#813): each "Deposit into these accounts" box is
// named by the account's words as typed, not upper-cased by the form's
// label style, which VoiceOver read as "1000 - CHECKING".
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

test('the deposit account boxes keep their case', () => {
    const context = {
        console, window: {}, document: { addEventListener() {} },
        escapeHtml: s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
        accountWords: a => `${a.account_number} - ${a.name}`,
        App: { settings: {} },
    };
    vm.createContext(context);
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/settings.js'), 'utf8');
    vm.runInContext(src + '\nthis.SettingsPage = SettingsPage;', context);
    const html = context.SettingsPage._depositAccountsHtml({}, [
        { id: 1, account_number: '1000', name: 'Checking', bank_kind: 'bank' },
    ]);
    const label = html.match(/<label style="([^"]*)"><input type="checkbox" name="deposit-account:1"[^>]*>([^<]*)<\/label>/);
    assert.ok(label, html);
    assert.match(label[1], /text-transform:none/);
    assert.equal(label[2], '1000 - Checking');
});
