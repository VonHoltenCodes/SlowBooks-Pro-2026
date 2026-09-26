const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function fixture(settings = {}) {
    const writes = [];
    const context = {
        API: { get: async () => settings, put: async (url, data) => { writes.push({ url, data }); } },
        setTimeout() {}, T: value => value, Terms: { text: value => value },
        escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, character => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[character]),
        toast() {},
        // A checkbox is absent from browser FormData when it is unchecked.
        FormData: class {
            constructor(form) { this.form = form; }
            entries() { return Object.entries(this.form.fields); }
        },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/settings.js'), 'utf8') + '\nthis.page = SettingsPage;', context);
    return { page: context.page, writes };
}

const checkboxTag = html => html.match(/<input[^>]*name="invoice_show_logo"[^>]*>/)?.[0];

test('logo option is hidden without a configured logo and appears after upload', async () => {
    const settings = { company_logo_path: '' };
    const f = fixture(settings);
    assert.equal(checkboxTag(await f.page.render()), undefined);
    settings.company_logo_path = '/static/uploads/company_logo.png';
    const html = await f.page.render();
    assert.match(checkboxTag(html), /checked/);
    assert.match(html, /Show company logo on invoices/);
});

test('logo option reflects the saved company preference', async () => {
    const settings = { company_logo_path: '/static/uploads/company_logo.png', invoice_show_logo: 'false' };
    const f = fixture(settings);
    assert.doesNotMatch(checkboxTag(await f.page.render()), /checked/);
    settings.invoice_show_logo = 'true';
    assert.match(checkboxTag(await f.page.render()), /checked/);
});

for (const checked of [false, true]) {
    test(`saving the ${checked ? 'checked' : 'unchecked'} option writes an explicit string value`, async () => {
        const f = fixture();
        const fields = { company_phone: '555-0100', ...(checked ? { invoice_show_logo: 'true' } : {}) };
        const form = { fields, querySelector: () => ({ checked }) };
        await f.page.save({ preventDefault() {}, target: form });
        assert.equal(f.writes[0].url, '/settings');
        assert.equal(f.writes[0].data.invoice_show_logo, String(checked));
        assert.equal(f.writes[0].data.company_phone, '555-0100');
    });
}

test('saving without a logo leaves the hidden preference untouched', async () => {
    const f = fixture();
    await f.page.save({ preventDefault() {}, target: { fields: { company_phone: '555-0100' }, querySelector: () => null } });
    assert.equal(Object.hasOwn(f.writes[0].data, 'invoice_show_logo'), false);
});
