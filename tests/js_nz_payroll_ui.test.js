const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

async function runEmployeesPage() {
    const code = `${fs.readFileSync('app/static/js/employees.js', 'utf8')}\nthis.EmployeesPage = EmployeesPage;`;
    let modalHtml = '';
    const context = {
        API: { get: async () => { throw new Error('new employee form should not load existing employee'); } },
        App: { navigate() {} },
        FormData,
        closeModal() {},
        escapeHtml: value => String(value || ''),
        openModal: (_title, html) => { modalHtml = html; },
        todayISO: () => '2026-04-01',
        toast() {},
        console,
    };
    vm.createContext(context);
    vm.runInContext(code, context);
    await context.EmployeesPage.showForm();
    return modalHtml;
}

async function runPayrollPage() {
    const code = `${fs.readFileSync('app/static/js/payroll.js', 'utf8')}\nthis.PayrollPage = PayrollPage;`;
    const context = {
        API: { get: async () => [] },
        formatCurrency: value => `$${Number(value || 0).toFixed(2)}`,
        formatDate: value => String(value || ''),
        statusBadge: value => value,
        toast() {},
        confirm: () => true,
        App: { navigate() {} },
        closeModal() {},
        openModal() {},
        todayISO: () => '2026-04-01',
        $: () => null,
        $$: () => [],
        escapeHtml: value => String(value || ''),
        console,
    };
    vm.createContext(context);
    vm.runInContext(code, context);
    return context.PayrollPage.render();
}

(async () => {
    const employeeHtml = await runEmployeesPage();
    assert.ok(employeeHtml.includes('IRD Number'));
    assert.ok(employeeHtml.includes('Tax Code'));
    assert.ok(employeeHtml.includes('KiwiSaver'));
    assert.ok(employeeHtml.includes('Pay Frequency'));
    assert.ok(!employeeHtml.includes('SSN Last 4'));
    assert.ok(!employeeHtml.includes('Filing Status'));
    assert.ok(!employeeHtml.includes('Allowances'));

    const payrollHtml = await runPayrollPage();
    assert.ok(payrollHtml.includes('NZ payroll setup is ready'));
    assert.ok(payrollHtml.includes('PAYE calculations'));
    assert.ok(!payrollHtml.includes('Federal'));
    assert.ok(!payrollHtml.includes('Medicare'));
    assert.ok(!payrollHtml.includes('Social Security'));
    assert.ok(!payrollHtml.includes('New Pay Run'));
})().catch(err => {
    console.error(err);
    process.exit(1);
});
