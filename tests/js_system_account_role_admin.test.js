const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const code = `${fs.readFileSync('app/static/js/app.js', 'utf8')}\nthis.App = App;`;
let modalTitle = '';
let modalHtml = '';

const context = {
    API: {
        get: async (path) => {
            if (path === '/accounts') {
                return [
                    { id: 11, name: 'Operating Account', account_type: 'asset', account_number: '090', is_active: true, is_system: true, balance: 0 },
                    { id: 12, name: 'Trade Debtors', account_type: 'asset', account_number: '610', is_active: true, is_system: false, balance: 0 },
                    { id: 13, name: 'Trade Creditors', account_type: 'liability', account_number: '810', is_active: true, is_system: false, balance: 0 },
                    { id: 14, name: 'Dormant Income', account_type: 'income', account_number: '499', is_active: false, is_system: false, balance: 0 },
                    { id: 15, name: 'Consulting Income', account_type: 'income', account_number: '410', is_active: true, is_system: false, balance: 0 },
                ];
            }
            if (path === '/accounts/system-roles') {
                return [
                    {
                        role_key: 'system_account_default_sales_income_id',
                        label: 'Default Sales Income',
                        description: 'Default income account when items or lines do not specify one.',
                        account_type: 'income',
                        status: 'fallback',
                        auto_create_on_use: false,
                        configured_account_valid: false,
                        configured_account: null,
                        resolved_account: { id: 15, name: 'Consulting Income', account_type: 'income', account_number: '410', is_active: true, is_system: false },
                        warning: 'Runtime is using fallback account resolution for this role.',
                    },
                ];
            }
            throw new Error(`unexpected path ${path}`);
        },
    },
    openModal: (title, html) => { modalTitle = title; modalHtml = html; },
    closeModal() {},
    escapeHtml: value => String(value || ''),
    formatCurrency: value => `$${Number(value || 0).toFixed(2)}`,
    formatDate: value => String(value || ''),
    console,
    window: { addEventListener() {} },
    document: {
        addEventListener() {},
        documentElement: { getAttribute() { return 'light'; }, setAttribute() {} },
        querySelector() { return null; },
    },
    localStorage: { getItem() { return null; }, setItem() {} },
    setInterval() {},
    location: { hash: '#/' },
    $: () => null,
    $$: () => [],
    CustomersPage: {},
    VendorsPage: {},
    ItemsPage: {},
    InvoicesPage: { render() {}, showForm() {} },
    EstimatesPage: {},
    PaymentsPage: { render() {}, showForm() {} },
    BankingPage: {},
    ReportsPage: {},
    SettingsPage: {},
    IIFPage: {},
    AuditPage: {},
    PurchaseOrdersPage: {},
    BillsPage: {},
    CreditMemosPage: {},
    RecurringPage: {},
    BatchPaymentsPage: {},
    CompaniesPage: {},
    EmployeesPage: {},
    PayrollPage: {},
    closeSearchDropdown() {},
    toast() {},
};

vm.createContext(context);
vm.runInContext(code, context);

(async () => {
    const html = await context.App.renderAccounts();
    assert.ok(html.includes('System Account Roles'));
    assert.ok(html.includes('Fallback'));
    assert.ok(html.includes('Consulting Income'));

    await context.App.showSystemAccountRoleForm('system_account_default_sales_income_id');
    assert.ok(modalTitle.includes('Default Sales Income'));
    assert.ok(modalHtml.includes('Consulting Income'));
    assert.ok(!modalHtml.includes('Dormant Income'));
    assert.ok(!modalHtml.includes('Trade Creditors'));
})().catch(err => {
    console.error(err);
    process.exit(1);
});
