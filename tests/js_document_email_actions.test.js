const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const code = [
    `${fs.readFileSync('app/static/js/estimates.js', 'utf8')}\nthis.EstimatesPage = EstimatesPage;`,
    `${fs.readFileSync('app/static/js/purchase_orders.js', 'utf8')}\nthis.PurchaseOrdersPage = PurchaseOrdersPage;`,
    `${fs.readFileSync('app/static/js/credit_memos.js', 'utf8')}\nthis.CreditMemosPage = CreditMemosPage;`,
    `${fs.readFileSync('app/static/js/payroll.js', 'utf8')}\nthis.PayrollPage = PayrollPage;`,
    `${fs.readFileSync('app/static/js/reports.js', 'utf8')}\nthis.ReportsPage = ReportsPage;`,
].join('\n');

const calls = [];

const context = {
    API: {
        get: async (path) => {
            if (path === '/estimates/1') return { id: 1, estimate_number: 'E-1001', customer_id: 5 };
            if (path === '/customers/5') return { id: 5, name: 'Aroha Ltd', email: 'customer@example.com' };
            if (path === '/purchase-orders/2') return { id: 2, po_number: 'PO-0001', vendor_id: 6 };
            if (path === '/vendors/6') return { id: 6, name: 'Harbour Supplies', email: 'vendor@example.com' };
            if (path === '/credit-memos/3') return { id: 3, memo_number: 'CM-0001', customer_id: 5 };
            throw new Error(`unexpected path ${path}`);
        },
    },
    App: {
        showDocumentEmailModal: (payload) => calls.push(payload),
    },
    openModal() {},
    closeModal() {},
    toast() {},
    escapeHtml: value => String(value || ''),
    formatDate: value => String(value || ''),
    formatCurrency: value => `$${Number(value || 0).toFixed(2)}`,
    todayISO: () => '2026-04-30',
    statusBadge: value => value,
    gstOptionsHtml: () => '',
    readGstLinePayload: () => ({}),
    calculateDocumentPreview: () => ({}),
    renderGstSummaryRows: () => '',
    console,
    window: { open() {} },
    $: () => null,
    $$: () => [],
    confirm: () => true,
};

vm.createContext(context);
vm.runInContext(code, context);

(async () => {
    await context.EstimatesPage.emailEstimate(1);
    await context.PurchaseOrdersPage.emailPurchaseOrder(2);
    await context.CreditMemosPage.emailCreditMemo(3);
    context.PayrollPage.emailPayslip(7, 8);
    context.ReportsPage._statementCustomers = [{ id: 5, name: 'Aroha Ltd', email: 'customer@example.com' }];
    context.ReportsPage.emailStatementFromForm({
        customer_id: { value: '5' },
        as_of_date: { value: '2026-04-30' },
    });

    assert.deepStrictEqual(
        calls.map(call => [call.endpoint, call.recipient]),
        [
            ['/estimates/1/email', 'customer@example.com'],
            ['/purchase-orders/2/email', 'vendor@example.com'],
            ['/credit-memos/3/email', 'customer@example.com'],
            ['/payroll/7/payslips/8/email', ''],
            ['/reports/customer-statement/5/email', 'customer@example.com'],
        ],
    );
})().catch(err => {
    console.error(err);
    process.exit(1);
});
