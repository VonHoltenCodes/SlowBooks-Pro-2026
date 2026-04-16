const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const code = `${fs.readFileSync('app/static/js/purchase_orders.js', 'utf8')}
this.PurchaseOrdersPage = PurchaseOrdersPage;`;
const navigations = [];

const context = {
    console,
    Promise,
    setTimeout,
    API: {
        get: async (path) => {
            if (path === '/purchase-orders') return [{ id: 7, po_number: 'PO-0001', vendor_name: 'Harbour Supplies', date: '2026-04-16', status: 'draft', total: 115 }];
            if (path === '/vendors?active_only=true') return [{ id: 1, name: 'Harbour Supplies' }];
            if (path === '/items?active_only=true') return [{ id: 2, name: 'Stationery', description: 'Pens', cost: 15 }];
            if (path === '/settings') return { default_tax_rate: '15', prices_include_gst: 'false' };
            if (path === '/gst-codes') return [{ code: 'GST15', rate: 0.15, name: 'GST 15%' }];
            if (path === '/purchase-orders/7') return { id: 7, vendor_id: 1, po_number: 'PO-0001', status: 'draft', date: '2026-04-16', expected_date: '2026-04-20', ship_to: '211 Lyndhurst Road', notes: 'Leave at front desk', lines: [{ item_id: 2, description: 'Pens', quantity: 2, rate: 15, gst_code: 'GST15', gst_rate: 0.15 }] };
            throw new Error(`unexpected path ${path}`);
        },
        post: async () => ({}),
        put: async () => ({}),
    },
    App: {
        navigate: (hash) => navigations.push(hash),
        hasPermission: () => true,
        gstCodes: [],
        settings: { prices_include_gst: 'false' },
    },
    escapeHtml: value => String(value || ''),
    formatDate: value => String(value || ''),
    formatCurrency: value => `$${Number(value || 0).toFixed(2)}`,
    statusBadge: value => String(value || ''),
    todayISO: () => '2026-04-16',
    gstOptionsHtml: () => '<option value="GST15">GST 15%</option>',
    readGstLinePayload: (row) => ({
        quantity: parseFloat(row.querySelector('.line-qty')?.value) || 0,
        rate: parseFloat(row.querySelector('.line-rate')?.value) || 0,
        gst_code: row.querySelector('.line-gst')?.value || 'GST15',
        gst_rate: 0.15,
    }),
    calculateGstTotals: (lines) => {
        const subtotal = lines.reduce((sum, line) => sum + ((line.quantity || 0) * (line.rate || 0)), 0);
        const tax_amount = subtotal * 0.15;
        return { subtotal, tax_amount, total: subtotal + tax_amount };
    },
    $: () => null,
    $$: () => [],
    confirm: () => true,
    toast() {},
};

vm.createContext(context);
vm.runInContext(code, context);

(async () => {
    const listHtml = await context.PurchaseOrdersPage.render();
    assert.ok(listHtml.includes('PurchaseOrdersPage.startNew()'));
    assert.ok(listHtml.includes('PurchaseOrdersPage.open(7)'));

    await context.PurchaseOrdersPage.startNew();
    assert.deepStrictEqual(navigations, ['#/purchase-orders/detail']);
    let detailHtml = context.PurchaseOrdersPage.renderDetailScreen();
    assert.ok(detailHtml.includes('Date Raised'));
    assert.ok(detailHtml.includes('Delivery Date'));
    assert.ok(detailHtml.includes('Delivery Address'));
    assert.ok(detailHtml.includes('Delivery Instructions / Notes'));
    assert.ok(detailHtml.includes('Assigned on save'));

    navigations.length = 0;
    await context.PurchaseOrdersPage.open(7);
    assert.deepStrictEqual(navigations, ['#/purchase-orders/detail']);
    detailHtml = context.PurchaseOrdersPage.renderDetailScreen();
    assert.ok(detailHtml.includes('PO-0001'));
    assert.ok(detailHtml.includes('211 Lyndhurst Road'));
    assert.ok(detailHtml.includes('Leave at front desk'));
    assert.ok(detailHtml.includes('Subtotal'));
    assert.ok(detailHtml.includes('Total'));
})();
