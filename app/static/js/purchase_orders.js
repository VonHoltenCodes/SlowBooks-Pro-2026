/**
 * Purchase Orders — vendor-facing non-posting documents
 * Feature 6: CRUD + convert to bill
 */
const PurchaseOrdersPage = {
    async render() {
        const pos = await API.get('/purchase-orders');
        let html = `
            <div class="page-header">
                <h2>Purchase Orders</h2>
                <button class="btn btn-primary" onclick="PurchaseOrdersPage.showForm()">+ New PO</button>
            </div>`;

        if (pos.length === 0) {
            html += '<div class="empty-state"><p>No purchase orders yet</p></div>';
        } else {
            html += `<div class="table-container"><table>
                <thead><tr><th>#</th><th>Vendor</th><th>Date</th><th>Status</th><th class="amount">Total</th><th>Actions</th></tr></thead><tbody>`;
            for (const po of pos) {
                html += `<tr>
                    <td><strong>${escapeHtml(po.po_number)}</strong></td>
                    <td>${escapeHtml(po.vendor_name || '')}</td>
                    <td>${formatDate(po.date)}</td>
                    <td>${statusBadge(po.status)}</td>
                    <td class="amount">${formatCurrency(po.total)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="PurchaseOrdersPage.showForm(${po.id})">Edit</button>
                        ${po.status !== 'closed' ? `<button class="btn btn-sm btn-primary" onclick="PurchaseOrdersPage.convertToBill(${po.id})">To Bill</button>` : ''}
                    </td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    _items: [],
    lineCount: 0,

    async showForm(id = null) {
        const [vendors, items, settings, gstCodes] = await Promise.all([
            API.get('/vendors?active_only=true'),
            API.get('/items?active_only=true'),
            API.get('/settings'),
            API.get('/gst-codes'),
        ]);
        App.gstCodes = gstCodes;
        PurchaseOrdersPage._items = items;

        let po = {
            vendor_id: '',
            date: todayISO(),
            expected_date: '',
            ship_to: '',
            tax_rate: (parseFloat(settings.default_tax_rate || '0') || 0) / 100,
            notes: '',
            lines: [],
        };
        if (id) po = await API.get(`/purchase-orders/${id}`);
        if (po.lines.length === 0) po.lines = [{ item_id: '', description: '', quantity: 1, rate: 0 }];
        PurchaseOrdersPage.lineCount = po.lines.length;

        const vendorOpts = vendors.map(v => `<option value="${v.id}" ${po.vendor_id==v.id?'selected':''}>${escapeHtml(v.name)}</option>`).join('');

        openModal(id ? 'Edit Purchase Order' : 'New Purchase Order', `
            <form onsubmit="PurchaseOrdersPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Vendor *</label>
                        <select name="vendor_id" required><option value="">Select...</option>${vendorOpts}</select></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${po.date}"></div>
                    <div class="form-group"><label>Expected Date</label>
                        <input name="expected_date" type="date" value="${po.expected_date || ''}"></div>
                    <input name="tax_rate" type="hidden" value="${(po.tax_rate * 100) || 0}">
                </div>
                <h3 style="margin:12px 0 8px;font-size:14px;">Line Items</h3>
                <table class="line-items-table">
                    <thead><tr><th>Item</th><th>Description</th><th class="col-qty">Qty</th><th>GST</th><th class="col-rate">Rate</th><th class="col-amount">Amount</th></tr></thead>
                    <tbody id="po-lines">
                        ${po.lines.map((l, i) => PurchaseOrdersPage.lineHtml(i, l, items)).join('')}
                    </tbody>
                </table>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="PurchaseOrdersPage.addLine()">+ Add Line</button>
                <div class="form-group" style="margin-top:12px;"><label>Notes</label>
                    <textarea name="notes">${escapeHtml(po.notes || '')}</textarea></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'} PO</button>
                </div>
            </form>`);
    },

    lineHtml(idx, line, items) {
        const opts = items.map(i => `<option value="${i.id}" ${line.item_id==i.id?'selected':''}>${escapeHtml(i.name)}</option>`).join('');
        return `<tr data-poline="${idx}">
            <td><select class="line-item" onchange="PurchaseOrdersPage.itemSel(${idx})"><option value="">--</option>${opts}</select></td>
            <td><input class="line-desc" value="${escapeHtml(line.description || '')}"></td>
            <td><input class="line-qty" type="number" step="0.01" value="${line.quantity || 1}"></td>
            <td><select class="line-gst">${gstOptionsHtml(line.gst_code || 'GST15')}</select></td>
            <td><input class="line-rate" type="number" step="0.01" value="${line.rate || 0}"></td>
            <td class="col-amount">${formatCurrency((line.quantity||1)*(line.rate||0))}</td>
        </tr>`;
    },

    addLine() {
        const idx = PurchaseOrdersPage.lineCount++;
        $('#po-lines').insertAdjacentHTML('beforeend',
            PurchaseOrdersPage.lineHtml(idx, {}, PurchaseOrdersPage._items));
    },

    itemSel(idx) {
        const row = $(`[data-poline="${idx}"]`);
        const item = PurchaseOrdersPage._items.find(i => i.id == row.querySelector('.line-item').value);
        if (item) {
            row.querySelector('.line-desc').value = item.description || item.name;
            row.querySelector('.line-rate').value = item.cost || item.rate;
        }
    },

    async save(e, id) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        $$('#po-lines tr').forEach((row, i) => {
            const gst = readGstLinePayload(row);
            lines.push({
                item_id: row.querySelector('.line-item')?.value ? parseInt(row.querySelector('.line-item').value) : null,
                description: row.querySelector('.line-desc')?.value || '',
                quantity: gst.quantity,
                rate: gst.rate,
                gst_code: gst.gst_code,
                gst_rate: gst.gst_rate,
                line_order: i,
            });
        });
        const data = {
            vendor_id: parseInt(form.vendor_id.value),
            date: form.date.value,
            expected_date: form.expected_date.value || null,
            tax_rate: (parseFloat(form.tax_rate.value) || 0) / 100,
            notes: form.notes.value || null,
            lines,
        };
        try {
            if (id) { await API.put(`/purchase-orders/${id}`, data); toast('PO updated'); }
            else { await API.post('/purchase-orders', data); toast('PO created'); }
            closeModal();
            App.navigate('#/purchase-orders');
        } catch (err) { toast(err.message, 'error'); }
    },

    async convertToBill(id) {
        if (!confirm('Convert this PO to a bill?')) return;
        try {
            const result = await API.post(`/purchase-orders/${id}/convert-to-bill`);
            toast(result.message);
            App.navigate('#/bills');
        } catch (err) { toast(err.message, 'error'); }
    },
};
