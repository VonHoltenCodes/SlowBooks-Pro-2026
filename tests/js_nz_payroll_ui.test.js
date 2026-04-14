const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

async function runEmployeesPage() {
    const code = `${fs.readFileSync('app/static/js/employees.js', 'utf8')}\nthis.EmployeesPage = EmployeesPage;`;
    let modalHtml = '';
    const context = {
        API: { get: async (path) => {
            if (path === '/employees') {
                return [{
                    id: 1,
                    first_name: 'Aroha',
                    last_name: 'Ngata',
                    tax_code: 'M',
                    pay_frequency: 'fortnightly',
                    pay_rate: 85000,
                    pay_type: 'salary',
                    is_active: true,
                    start_date: '2026-04-01',
                    end_date: '2026-04-30',
                }];
            }
            if (path === '/employees/1/filing/history') {
                return [{ filing_type: 'starter', status: 'generated', changed_since_source: false }];
            }
            throw new Error('new employee form should not load existing employee');
        } },
        App: { navigate() {} },
        FormData,
        closeModal() {},
        escapeHtml: value => String(value || ''),
        formatCurrency: value => `$${Number(value || 0).toFixed(2)}`,
        openModal: (_title, html) => { modalHtml = html; },
        todayISO: () => '2026-04-01',
        toast() {},
        console,
    };
    vm.createContext(context);
    vm.runInContext(code, context);
    const listHtml = await context.EmployeesPage.render();
    await context.EmployeesPage.showForm();
    return { listHtml, modalHtml };
}

async function runPayrollPage() {
    const code = `${fs.readFileSync('app/static/js/payroll.js', 'utf8')}\nthis.PayrollPage = PayrollPage;`;
    const calls = [];
    const context = {
        API: {
            get: async (path) => {
                calls.push(path);
                if (path === '/payroll') {
                    return [
                        {
                            id: 1,
                            status: 'draft',
                            tax_year: 2027,
                            pay_date: '2026-04-15',
                            total_gross: 4200,
                            total_net: 2922.13,
                            total_taxes: 1277.87,
                            stubs: [],
                        },
                        {
                            id: 2,
                            status: 'processed',
                            tax_year: 2027,
                            pay_date: '2026-04-29',
                            total_gross: 4200,
                            total_net: 2922.13,
                            total_taxes: 1277.87,
                            stubs: [],
                        }
                    ];
                }
                if (path === '/employees?active_only=true') {
                    return [{
                        id: 1,
                        first_name: 'Aroha',
                        last_name: 'Ngata',
                        pay_type: 'salary',
                        pay_frequency: 'fortnightly',
                    }];
                }
                if (path === '/payroll/2/filing/history') {
                    return [{ filing_type: 'employment_information', status: 'generated', changed_since_source: false }];
                }
                throw new Error(`unexpected path ${path}`);
            },
        },
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
    return { html: await context.PayrollPage.render(), calls };
}

(async () => {
    const { listHtml: employeeListHtml, modalHtml: employeeHtml } = await runEmployeesPage();
    assert.ok(employeeHtml.includes('IRD Number'));
    assert.ok(employeeHtml.includes('Tax Code'));
    assert.ok(employeeHtml.includes('KiwiSaver'));
    assert.ok(employeeHtml.includes('Child Support Amount'));
    assert.ok(employeeHtml.includes('Pay Frequency'));
    assert.ok(employeeListHtml.includes('Starter Filing'));
    assert.ok(employeeListHtml.includes('Leaver Filing'));
    assert.ok(employeeListHtml.includes('Starter generated'));
    assert.ok(!employeeHtml.includes('SSN Last 4'));
    assert.ok(!employeeHtml.includes('Filing Status'));
    assert.ok(!employeeHtml.includes('Allowances'));

    const { html: payrollHtml, calls } = await runPayrollPage();
    assert.ok(calls.includes('/payroll'));
    assert.ok(calls.includes('/employees?active_only=true'));
    assert.ok(payrollHtml.includes('New Pay Run'));
    assert.ok(payrollHtml.includes('Tax Year'));
    assert.ok(payrollHtml.includes('Process'));
    assert.ok(payrollHtml.includes('Draft'));
    assert.ok(payrollHtml.includes('Payslip'));
    assert.ok(payrollHtml.includes('Employment Information'));
    assert.ok(payrollHtml.includes('Employment Information generated'));
    assert.ok(payrollHtml.includes('NZ payroll setup is ready'));
    assert.ok(payrollHtml.includes('starter/leaver filing'));
    assert.ok(payrollHtml.includes('PAYE'));
    assert.ok(!payrollHtml.includes('payday filing'));
    assert.ok(!payrollHtml.includes('later slices'));
    assert.ok(!payrollHtml.includes('Federal'));
    assert.ok(!payrollHtml.includes('Medicare'));
    assert.ok(!payrollHtml.includes('Social Security'));
})().catch(err => {
    console.error(err);
    process.exit(1);
});
