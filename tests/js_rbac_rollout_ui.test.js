const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const appCode = fs.readFileSync('app/static/js/app.js', 'utf8');
const customersCode = fs.readFileSync('app/static/js/customers.js', 'utf8');
const bankingCode = fs.readFileSync('app/static/js/banking.js', 'utf8');
const iifCode = fs.readFileSync('app/static/js/iif.js', 'utf8');

const makeContext = (permissions) => {
    const pageContent = { innerHTML: '' };
    const context = {
        API: {
            get: async (path) => {
                if (path === '/customers') return [{ id: 1, name: 'Aroha Ltd', company: '', phone: '', email: '', balance: 0 }];
                if (path === '/banking/accounts') return [{ id: 1, name: 'Main Bank', balance: 1200, bank_name: 'Kiwi Bank', last_four: '1234' }];
                if (path === '/banking/accounts/1') return { id: 1, name: 'Main Bank', balance: 1200, bank_name: 'Kiwi Bank', last_four: '1234' };
                if (path === '/banking/transactions?bank_account_id=1') return [];
                if (path === '/auth/me') return { authenticated: true, bootstrap_required: false, user: { full_name: 'Viewer', membership: { role_key: 'staff', effective_permissions: permissions } } };
                if (path === '/settings/public') return { locale: 'en-NZ', currency: 'NZD' };
                return [];
            },
        },
        App: {
            settings: { locale: 'en-NZ', currency: 'NZD' },
            authState: { authenticated: true, bootstrap_required: false, user: { full_name: 'Viewer', membership: { role_key: 'staff', effective_permissions: permissions } } },
            hasPermission(permission) { return !permission || permissions.includes(permission); },
            navigate() {},
            setStatus() {},
        },
        document: {
            documentElement: { getAttribute: () => 'light', setAttribute() {} },
            querySelector: (selector) => selector === '#page-content' ? pageContent : null,
            querySelectorAll: () => [],
            createElement: () => ({ click() {}, remove() {}, style: {} }),
            addEventListener() {},
        },
        window: { addEventListener() {} },
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        location: { hash: '#/customers', reload() {} },
        escapeHtml: (value) => String(value || ''),
        formatCurrency: (value) => `$${value}`,
        formatDate: (value) => value || '',
        todayISO: () => '2026-04-15',
        openModal() {},
        closeModal() {},
        toast() {},
        setInterval: () => 1,
        setTimeout,
        console,
        fetch: async () => ({ ok: true, headers: { get: () => null }, blob: async () => ({}) }),
        URL: { createObjectURL: () => 'blob:test', revokeObjectURL() {} },
        $: (selector) => selector === '#page-content' ? pageContent : null,
        $$: () => [],
        Date,
        Intl,
        Number,
    };
    vm.createContext(context);
    vm.runInContext(`${appCode}\n${customersCode}\n${bankingCode}\n${iifCode}\nthis.App = App; this.CustomersPage = CustomersPage; this.BankingPage = BankingPage; this.IIFPage = IIFPage;`, context);
    context.App.settings = { locale: 'en-NZ', currency: 'NZD' };
    context.App.authState = { authenticated: true, bootstrap_required: false, user: { full_name: 'Viewer', membership: { role_key: 'staff', effective_permissions: permissions } } };
    context.__pageContent = pageContent;
    return context;
};

(async () => {
    const viewOnly = makeContext(['contacts.view', 'banking.view', 'import_export.view']);
    assert.strictEqual(viewOnly.App.routes['/customers'].permission, 'contacts.view');
    assert.strictEqual(viewOnly.App.routes['/banking'].permission, 'banking.view');
    assert.strictEqual(viewOnly.App.routes['/iif'].permission, 'import_export.view');
    assert.strictEqual(viewOnly.App.routes['/csv'].permission, 'import_export.view');

    const customersHtml = await viewOnly.CustomersPage.render();
    assert.ok(!customersHtml.includes('+ New Customer'));
    assert.ok(!customersHtml.includes('Edit'));

    const bankingHtml = await viewOnly.BankingPage.render();
    assert.ok(!bankingHtml.includes('+ New Bank Account'));
    await viewOnly.BankingPage.viewRegister(1);
    assert.ok(!viewOnly.__pageContent.innerHTML.includes('+ Transaction'));
    assert.ok(!viewOnly.__pageContent.innerHTML.includes('Import OFX/QFX'));
    assert.ok(!viewOnly.__pageContent.innerHTML.includes('Reconcile'));

    const iifHtml = await viewOnly.IIFPage.render();
    assert.ok(iifHtml.includes('Export All Data'));
    assert.ok(!iifHtml.includes('Import from IIF'));

    const manager = makeContext(['contacts.view', 'contacts.manage', 'banking.view', 'banking.manage', 'import_export.view', 'import_export.manage']);
    const managerCustomersHtml = await manager.CustomersPage.render();
    assert.ok(managerCustomersHtml.includes('+ New Customer'));
    assert.ok(managerCustomersHtml.includes('Edit'));
    const managerIifHtml = await manager.IIFPage.render();
    assert.ok(managerIifHtml.includes('Import from IIF'));
})().catch((err) => {
    console.error(err);
    process.exit(1);
});
