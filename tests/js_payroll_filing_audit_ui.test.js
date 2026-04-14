const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

async function runEmployeesPage() {
    const code = `${fs.readFileSync('app/static/js/employees.js', 'utf8')}\nthis.EmployeesPage = EmployeesPage;`;
    const calls = [];
    const context = {
        API: {
            get: async (path) => {
                calls.push(path);
                if (path === '/employees') return [{ id: 1, first_name: 'Aroha', last_name: 'Ngata', tax_code: 'M', pay_frequency: 'fortnightly', pay_rate: 78000, pay_type: 'salary', is_active: true, start_date: '2026-04-01', end_date: null }];
                if (path === '/employees/1/filing/history') return [{ filing_type: 'starter', status: 'filed', changed_since_source: true }];
                throw new Error(`unexpected path ${path}`);
            },
        },
        App: { navigate() {}, hasPermission() { return true; } },
        escapeHtml: value => String(value || ''),
        formatCurrency: value => `$${Number(value || 0).toFixed(2)}`,
        toast() {},
        openModal() {},
        closeModal() {},
        todayISO: () => '2026-04-01',
        window: { open() {} },
        console,
    };
    vm.createContext(context);
    vm.runInContext(code, context);
    return { html: await context.EmployeesPage.render(), calls };
}

async function runPayrollPage() {
    const code = `${fs.readFileSync('app/static/js/payroll.js', 'utf8')}\nthis.PayrollPage = PayrollPage;`;
    const calls = [];
    const context = {
        API: {
            get: async (path) => {
                calls.push(path);
                if (path === '/payroll') return [{ id: 2, status: 'processed', tax_year: 2027, pay_date: '2026-04-15', total_gross: 4200, total_net: 3000, total_taxes: 1200 }];
                if (path === '/employees?active_only=true') return [{ id: 1, first_name: 'Aroha', last_name: 'Ngata', pay_type: 'salary', pay_frequency: 'fortnightly' }];
                if (path === '/payroll/2/filing/history') return [{ filing_type: 'employment_information', status: 'generated', changed_since_source: false }];
                throw new Error(`unexpected path ${path}`);
            },
        },
        App: { navigate() {}, showDocumentEmailModal() {}, hasPermission() { return true; } },
        formatCurrency: value => `$${Number(value || 0).toFixed(2)}`,
        formatDate: value => String(value || ''),
        escapeHtml: value => String(value || ''),
        toast() {},
        confirm: () => true,
        openModal() {},
        closeModal() {},
        todayISO: () => '2026-04-01',
        $: () => null,
        console,
        window: { open() {} },
    };
    vm.createContext(context);
    vm.runInContext(code, context);
    return { html: await context.PayrollPage.render(), calls };
}

(async () => {
    const employeePage = await runEmployeesPage();
    assert.ok(employeePage.calls.includes('/employees/1/filing/history'));
    assert.ok(employeePage.html.includes('Starter filed'));
    assert.ok(employeePage.html.includes('Changed since filing'));

    const payrollPage = await runPayrollPage();
    assert.ok(payrollPage.calls.includes('/payroll/2/filing/history'));
    assert.ok(payrollPage.html.includes('Employment Information generated'));
    assert.ok(!payrollPage.html.includes('Changed since filing'));
})().catch(err => {
    console.error(err);
    process.exit(1);
});
