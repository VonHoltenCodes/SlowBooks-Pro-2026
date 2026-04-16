const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

function makeContext(apiGet) {
    let modalHtml = '';
    return {
        API: { get: apiGet },
        App: { settings: { locale: 'en-NZ', currency: 'NZD' }, navigate() {} },
        FormData,
        closeModal() {},
        document: {
            querySelector: () => null,
            querySelectorAll: () => [],
            createElement: () => ({ className: '', textContent: '', remove() {} }),
        },
        escapeHtml: value => String(value || '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;'),
        formatCurrency: value => `$${Number(value || 0).toFixed(2)}`,
        location: { hash: '#/customers' },
        openModal: (_title, html) => { modalHtml = html; },
        setTimeout,
        toast() {},
        get modalHtml() { return modalHtml; },
    };
}

async function runPage(file, expression, apiGet) {
    const context = makeContext(apiGet);
    vm.createContext(context);
    vm.runInContext(`${fs.readFileSync(file, 'utf8')}\n${expression}`, context);
    return context;
}

(async () => {
    const settingsContext = await runPage(
        'app/static/js/settings.js',
        'this.SettingsPage = SettingsPage;',
        async path => {
            assert.strictEqual(path, '/settings');
            return { company_name: 'SlowBooks NZ', country: 'NZ', tax_regime: 'NZ', currency: 'NZD' };
        },
    );
    const settingsHtml = await settingsContext.SettingsPage.render();
    assert.ok(settingsHtml.includes('<label>Region</label>'));
    assert.ok(settingsHtml.includes('<label>Postcode</label>'));
    assert.ok(!settingsHtml.includes('<label>State</label>'));
    assert.ok(!settingsHtml.includes('<label>ZIP</label>'));

    const customersContext = await runPage(
        'app/static/js/customers.js',
        'this.CustomersPage = CustomersPage;',
        async () => { throw new Error('new customer form should not load a customer'); },
    );
    await customersContext.CustomersPage.showForm();
    assert.ok(customersContext.modalHtml.includes('<label>Region</label>'));
    assert.ok(customersContext.modalHtml.includes('<label>Postcode</label>'));
    assert.ok(customersContext.modalHtml.includes('name="bill_country" type="hidden" value="NZ"'));
    assert.ok(customersContext.modalHtml.includes('name="ship_country" type="hidden" value="NZ"'));
    assert.ok(!customersContext.modalHtml.includes('<label>State</label>'));
    assert.ok(!customersContext.modalHtml.includes('<label>ZIP</label>'));

    const vendorsContext = await runPage(
        'app/static/js/vendors.js',
        'this.VendorsPage = VendorsPage;',
        async path => {
            if (path === '/accounts?active_only=true&account_type=expense') return [];
            throw new Error('new vendor form should not load a vendor');
        },
    );
    await vendorsContext.VendorsPage.showForm();
    assert.ok(vendorsContext.modalHtml.includes('<label>Region</label>'));
    assert.ok(vendorsContext.modalHtml.includes('<label>Postcode</label>'));
    assert.ok(vendorsContext.modalHtml.includes('name="country" type="hidden" value="NZ"'));
    assert.ok(!vendorsContext.modalHtml.includes('<label>State</label>'));
    assert.ok(!vendorsContext.modalHtml.includes('<label>ZIP</label>'));
})().catch(err => {
    console.error(err);
    process.exit(1);
});
