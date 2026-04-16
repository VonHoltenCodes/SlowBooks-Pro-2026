const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const code = `${fs.readFileSync('app/static/js/reports.js', 'utf8')}\nthis.ReportsPage = ReportsPage;`;
const context = {
    formatCurrency: value => `$${Number(value || 0).toFixed(2)}`,
    formatDate: value => String(value || ''),
    escapeHtml: value => String(value || ''),
    openModal() {},
    closeModal() {},
    todayISO: () => '2026-04-30',
    API: { get: async () => ({}) },
    $: () => null,
    console,
    window: { open() {} },
};
vm.createContext(context);
vm.runInContext(code, context);

const html = context.ReportsPage.renderGstReturnSummary({
    start_date: '2026-04-01',
    end_date: '2026-04-30',
    gst_basis: 'invoice',
    gst_period: 'two-monthly',
    output_gst: 15,
    input_gst: 0,
    net_gst: 15,
    net_position: 'payable',
    boxes: { '5': 115, '6': 0, '7': 115, '8': 15, '9': 0, '10': 15, '11': 0, '12': 0, '13': 0, '14': 0, '15': 15 },
    items: [],
    settlement: {
        status: 'unsettled',
        direction: 'payment',
        expected_bank_amount: -15,
        candidates: [{ id: 4, date: '2026-05-07', payee: 'Inland Revenue', description: 'GST payment', amount: -15 }],
    },
});

assert.ok(html.includes('Settlement Status'));
assert.ok(html.includes('Unsettled'));
assert.ok(html.includes('Inland Revenue'));
assert.ok(html.includes('Confirm Settlement'));
