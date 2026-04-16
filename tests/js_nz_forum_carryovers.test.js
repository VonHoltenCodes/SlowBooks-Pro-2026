const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const code = [
    `${fs.readFileSync('app/static/js/invoices.js', 'utf8')}\nthis.InvoicesPage = InvoicesPage;`,
    `${fs.readFileSync('app/static/js/estimates.js', 'utf8')}\nthis.EstimatesPage = EstimatesPage;`,
    `${fs.readFileSync('app/static/js/payments.js', 'utf8')}\nthis.PaymentsPage = PaymentsPage;`,
    `${fs.readFileSync('app/static/js/vendors.js', 'utf8')}\nthis.VendorsPage = VendorsPage;`,
    `${fs.readFileSync('app/static/js/deposits.js', 'utf8')}\nthis.DepositsPage = DepositsPage;`,
    `${fs.readFileSync('app/static/js/check_register.js', 'utf8')}\nthis.CheckRegisterPage = CheckRegisterPage;`,
    `${fs.readFileSync('app/static/js/journal.js', 'utf8')}\nthis.JournalPage = JournalPage;`,
    `${fs.readFileSync('app/static/js/cc_charges.js', 'utf8')}\nthis.CCChargesPage = CCChargesPage;`,
].join('\n');

let modalHtml = '';
const elements = {
    '#inv-subtotal': { textContent: '' },
    '#inv-tax': { textContent: '' },
    '#inv-total': { textContent: '' },
    '#est-subtotal': { textContent: '' },
    '#est-tax': { textContent: '' },
    '#est-total': { textContent: '' },
    '#journal-balance': { innerHTML: '' },
    '#check-register-body': { innerHTML: '' },
};

const context = {
    API: {
        get: async (path) => {
            if (path === '/customers?active_only=true') return [{ id: 1, name: 'Aroha Ltd', terms: 'Net 30' }];
            if (path === '/items?active_only=true') return [];
            if (path === '/settings') return { default_terms: 'Net 30', default_tax_rate: '15' };
            if (path === '/gst-codes') return [{ code: 'GST15', rate: 0.15, name: 'GST 15%' }];
            if (path === '/payments') return [{ id: 5, date: '2026-04-01', customer_name: 'Aroha Ltd', method: 'Cash', reference: '', amount: 99, is_voided: true }];
            if (path === '/payments/5') return { id: 5, date: '2026-04-01', customer_name: 'Aroha Ltd', method: 'Cash', reference: '', amount: 99, is_voided: true, allocations: [], notes: '' };
            if (path === '/accounts?active_only=true&account_type=expense') return [{ id: 11, account_number: '600', name: 'General Expenses' }];
            if (path === '/vendors/7') return { id: 7, name: 'Harbour Supplies', country: 'NZ', default_expense_account_id: 11, terms: 'Net 30' };
            if (path === '/deposits/pending') return [{ payment_id: 9, date: '2026-04-03', customer_name: 'Aroha Ltd', reference: 'RCPT-1', amount: 55 }];
            if (path === '/accounts?active_only=true&account_type=asset') return [{ id: 21, account_number: '090', name: 'Operating Account' }];
            if (path === '/banking/check-register?account_id=21') return { account_id: 21, account_number: '090', account_name: 'Operating Account', starting_balance: 0, entries: [{ date: '2026-04-01', description: 'Opening', reference: 'J-1', source_type: 'manual_journal', payment: 0, deposit: 500, balance: 500 }] };
            if (path === '/journal') return [];
            if (path === '/accounts?active_only=true') return [{ id: 21, account_number: '090', name: 'Operating Account' }, { id: 11, account_number: '600', name: 'General Expenses' }];
            if (path === '/cc-charges') return [];
            if (path === '/accounts?active_only=true&account_type=liability') return [{ id: 31, account_number: '820', name: 'Corporate Card' }];
            throw new Error(`unexpected path ${path}`);
        },
        post: async () => ({ id: 2, name: 'New Customer', terms: 'Net 30' }),
    },
    App: {
        gstCodes: [],
        hasPermission: () => true,
        navigate() {},
    },
    openModal: (_title, html) => { modalHtml = html; },
    closeModal() {},
    toast() {},
    escapeHtml: value => String(value || ''),
    formatDate: value => String(value || ''),
    formatCurrency: value => `$${Number(value || 0).toFixed(2)}`,
    statusBadge: value => String(value || ''),
    todayISO: () => '2026-04-30',
    gstOptionsHtml: () => '<option>GST 15%</option>',
    readGstLinePayload: () => ({ quantity: 1, rate: 0, gst_code: 'GST15', gst_rate: 0.15 }),
    calculateGstTotals: () => ({ subtotal: 0, tax_amount: 0, total: 0 }),
    $: (sel) => {
        if (!elements[sel]) elements[sel] = { textContent: '', innerHTML: '', style: {} };
        return elements[sel];
    },
    $$: () => [],
    confirm: () => true,
    setTimeout: fn => fn(),
    console,
};

vm.createContext(context);
vm.runInContext(code, context);

(async () => {
    await context.InvoicesPage.showForm();
    assert.ok(modalHtml.includes('+ New Customer'));

    await context.EstimatesPage.showForm();
    assert.ok(modalHtml.includes('+ New Customer'));

    const paymentsHtml = await context.PaymentsPage.render();
    assert.ok(paymentsHtml.includes('[VOID]'));
    await context.PaymentsPage.view(5);
    assert.ok(modalHtml.includes('voided'));

    await context.VendorsPage.showForm(7);
    assert.ok(modalHtml.includes('Default Expense Account'));
    assert.ok(modalHtml.includes('General Expenses'));

    const depositsHtml = await context.DepositsPage.render();
    assert.ok(depositsHtml.includes('Aroha Ltd'));
    assert.ok(depositsHtml.includes('Record Deposit'));

    const registerHtml = await context.CheckRegisterPage.render();
    assert.ok(registerHtml.includes('Check Register'));
    await context.CheckRegisterPage.load(21);
    assert.ok(elements['#check-register-body'].innerHTML.includes('Opening'));

    await context.JournalPage.showForm();
    assert.ok(modalHtml.includes('Create Journal'));

    await context.CCChargesPage.showForm();
    assert.ok(modalHtml.includes('Corporate Card'));
})();
