const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const code = `${fs.readFileSync('app/static/js/reports.js', 'utf8')}\nthis.ReportsPage = ReportsPage;`;
const calls = [];
const downloads = [];
let modalHtml = '';
const elements = {};

const context = {
    console,
    Date,
    Math,
    Promise,
    setTimeout,
    API: {
        get: async (path) => {
            calls.push(path);
            return {
                start_date: '2026-04-01',
                end_date: '2026-04-30',
                gst_basis: 'invoice',
                gst_period: 'two-monthly',
                boxes: { 5: 115, 6: 0, 7: 115, 8: 15, 9: 5, 10: 20, 11: 0, 12: 0, 13: 2, 14: 2, 15: 18 },
                net_position: 'payable',
                items: [],
            };
        },
        download: async (path, filename) => {
            downloads.push({ path, filename });
        },
    },
    openModal: (_title, html) => {
        modalHtml = html;
        elements['#report-period-select'] = { value: 'custom', addEventListener() {} };
        elements['#report-custom-start'] = { value: '2026-04-01', addEventListener() {} };
        elements['#report-custom-end'] = { value: '2026-04-30', addEventListener() {} };
        elements['#report-content'] = { innerHTML: '' };
        elements['#gst-box9-adjustments'] = { value: '5.00', addEventListener() {} };
        elements['#gst-box13-adjustments'] = { value: '2.00', addEventListener() {} };
        elements['#report-custom-range'] = { style: {} };
    },
    closeModal: () => {},
    $: (selector) => elements[selector],
    escapeHtml: (value) => String(value ?? ''),
    formatCurrency: (value) => `$${Number(value).toFixed(2)}`,
    formatDate: (value) => value,
    todayISO: () => '2026-04-30',
};

vm.createContext(context);
vm.runInContext(code, context);

(async () => {
    const html = await context.ReportsPage.render();
    assert.ok(html.includes('GST Return'));
    assert.ok(!html.includes('Sales Tax'));

    await context.ReportsPage.gstReturn();
    assert.ok(modalHtml.includes('Box 9 adjustments'));
    assert.ok(modalHtml.includes('Box 13 credit adjustments'));
    assert.ok(calls.some(path => path.includes('/reports/gst-return?')));
    assert.ok(calls.some(path => path.includes('box9_adjustments=5.00')));
    assert.ok(calls.some(path => path.includes('box13_adjustments=2.00')));

    context.ReportsPage.downloadGstReturnPdf();
    assert.deepStrictEqual(downloads, [
        {
            path: '/reports/gst-return/pdf?start_date=2026-04-01&end_date=2026-04-30&box9_adjustments=5.00&box13_adjustments=2.00',
            filename: 'GST101A_2026-04-01_2026-04-30.pdf',
        },
    ]);
})().catch((err) => {
    console.error(err);
    process.exit(1);
});
