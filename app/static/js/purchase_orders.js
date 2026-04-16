/**
 * Purchase Orders — vendor-facing non-posting documents
 * Feature 6: CRUD + convert to bill
 */
const PurchaseOrdersPage = {
    _items: [],
    _vendors: [],
    _settings: {},
    _detailState: null,
    lineCount: 0,

    async render() {
        const pos = await API.get('/purchase-orders');
        const canManagePurchasing = App.hasPermission ? App.hasPermission('purchasing.manage') : true;
        let html = `
            <div class="page-header">
                <h2>Purchase Orders</h2>
                ${canManagePurchasing ? `<button class="btn btn-primary" onclick="PurchaseOrdersPage.startNew()">+ New PO</button>` : ''}
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
                        ${canManagePurchasing ? `<button class="btn btn-sm btn-secondary" onclick="PurchaseOrdersPage.open(${po.id})">Edit</button>
                        <button class="btn btn-sm btn-secondary" onclick="PurchaseOrdersPage.emailPurchaseOrder(${po.id})">Email</button>
                        ${po.status !== 'closed' ? `<button class="btn btn-sm btn-primary" onclick="PurchaseOrdersPage.convertToBill(${po.id})">To Bill</button>` : ''}` : ''}
                    </td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    async startNew() {
        await PurchaseOrdersPage._loadEditorContext(null);
        App.navigate('#/purchase-orders/detail');
    },

    async open(id) {
        await PurchaseOrdersPage._loadEditorContext(id);
        App.navigate('#/purchase-orders/detail');
    },

    async _loadEditorContext(id = null) {
        const [vendors, items, settings, gstCodes] = await Promise.all([
            API.get('/vendors?active_only=true'),
            API.get('/items?active_only=true'),
            API.get('/settings'),
            API.get('/gst-codes'),
        ]);
        App.gstCodes = gstCodes;
        PurchaseOrdersPage._vendors = vendors;
        PurchaseOrdersPage._items = items;
        PurchaseOrdersPage._settings = settings;

        let po = {
            id: null,
            vendor_id: '',
            po_number: '',
            status: 'draft',
            date: todayISO(),
            expected_date: '',
            ship_to: '',
            tax_rate: (parseFloat(settings.default_tax_rate || '0') || 0) / 100,
            notes: '',
            subtotal: 0,
            tax_amount: 0,
            total: 0,
            lines: [],
        };
        if (id) po = await API.get(`/purchase-orders/${id}`);
        if (!po.lines || po.lines.length === 0) po.lines = [{ item_id: '', description: '', quantity: 1, rate: 0, gst_code: 'GST15' }];
        PurchaseOrdersPage.lineCount = po.lines.length;
        PurchaseOrdersPage._detailState = po;
    },

    _totals(lines) {
        return calculateGstTotals(lines.map(line => ({
            quantity: parseFloat(line.quantity) || 0,
            rate: parseFloat(line.rate) || 0,
            gst_code: line.gst_code || 'GST15',
            gst_rate: line.gst_rate || 0,
        })), PurchaseOrdersPage._settings);
    },

    renderDetailScreen() {
        const po = PurchaseOrdersPage._detailState;
        const canManagePurchasing = App.hasPermission ? App.hasPermission('purchasing.manage') : true;
        if (!po) {
            return `<div class="empty-state"><p>Select a purchase order or create a new one first.</p><p style="margin-top:8px;"><button class="btn btn-primary" onclick="App.navigate('#/purchase-orders')">Back to Purchase Orders</button></p></div>`;
        }
        const totals = PurchaseOrdersPage._totals(po.lines || []);
        const vendorOpts = PurchaseOrdersPage._vendors.map(v => `<option value="${v.id}" ${po.vendor_id==v.id?'selected':''}>${escapeHtml(v.name)}</option>`).join('');
        return `
            <div class="page-header">
                <div>
                    <div style="font-size:10px; color:var(--text-muted);">Purchasing</div>
                    <h2>${po.id ? `Purchase Order ${escapeHtml(po.po_number || '')}` : 'New Purchase Order'}</h2>
                </div>
                <div class="actions">
                    <button class="btn btn-secondary" onclick="App.navigate('#/purchase-orders')">Back to Purchase Orders</button>
                </div>
            </div>
            <form onsubmit="PurchaseOrdersPage.save(event, ${po.id || 'null'})">
                <div class="settings-section">
                    <div class="form-grid">
                        <div class="form-group"><label>Vendor *</label>
                            <select name="vendor_id" required ${canManagePurchasing ? '' : 'disabled'}><option value="">Select...</option>${vendorOpts}</select></div>
                        <div class="form-group"><label>Date Raised *</label>
                            <input name="date" type="date" required value="${po.date || ''}" ${canManagePurchasing ? '' : 'disabled'}></div>
                        <div class="form-group"><label>Delivery Date</label>
                            <input name="expected_date" type="date" value="${po.expected_date || ''}" ${canManagePurchasing ? '' : 'disabled'}></div>
                        <div class="form-group"><label>Order Number</label>
                            <input value="${escapeHtml(po.po_number || 'Assigned on save')}" disabled></div>
                        <div class="form-group"><label>Status</label>
                            <input value="${escapeHtml(po.status || 'draft')}" disabled></div>
                    </div>
                </div>
                <div class="settings-section">
                    <div class="form-grid">
                        <div class="form-group full-width"><label>Delivery Address</label>
                            <textarea name="ship_to" rows="3" ${canManagePurchasing ? '' : 'disabled'}>${escapeHtml(po.ship_to || '')}</textarea></div>
                    </div>
                </div>
                <div class="settings-section">
                    <h3>Line Items</h3>
                    <table class="line-items-table">
                        <thead><tr><th>Item</th><th>Description</th><th class="col-qty">Qty</th><th>GST</th><th class="col-rate">Rate</th><th class="col-amount">Amount</th><th></th></tr></thead>
                        <tbody id="po-lines">
                            ${(po.lines || []).map((l, i) => PurchaseOrdersPage.lineHtml(i, l, PurchaseOrdersPage._items, canManagePurchasing)).join('')}
                        </tbody>
                    </table>
                    ${canManagePurchasing ? '<button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="PurchaseOrdersPage.addLine()">+ Add Line</button>' : ''}
                </div>
                <div class="settings-section">
                    <div style="display:grid; grid-template-columns: 1.4fr 0.8fr; gap:16px; align-items:start;">
                        <div class="form-group"><label>Delivery Instructions / Notes</label>
                            <textarea name="notes" rows="5" ${canManagePurchasing ? '' : 'disabled'}>${escapeHtml(po.notes || '')}</textarea></div>
                        <div>
                            <div class="table-container"><table>
                                <tbody>
                                    <tr><td><strong>Subtotal</strong></td><td class="amount" id="po-subtotal">${formatCurrency(totals.subtotal)}</td></tr>
                                    <tr><td><strong>Tax</strong></td><td class="amount" id="po-tax">${formatCurrency(totals.tax_amount)}</td></tr>
                                    <tr><td><strong>Total</strong></td><td class="amount" id="po-total">${formatCurrency(totals.total)}</td></tr>
                                </tbody>
                            </table></div>
                        </div>
                    </div>
                </div>
                ${canManagePurchasing ? `<div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="App.navigate('#/purchase-orders')">Cancel</button>
                    <button type="submit" class="btn btn-primary">${po.id ? 'Update Purchase Order' : 'Create Purchase Order'}</button>
                </div>` : ''}
            </form>`;
    },

    lineHtml(idx, line, items, canManage = true) {
        const opts = items.map(i => `<option value="${i.id}" ${line.item_id==i.id?'selected':''}>${escapeHtml(i.name)}</option>`).join('');
        return `<tr data-poline="${idx}">
            <td><select class="line-item" onchange="PurchaseOrdersPage.itemSel(${idx})" ${canManage ? '' : 'disabled'}><option value="">--</option>${opts}</select></td>
            <td><input class="line-desc" value="${escapeHtml(line.description || '')}" oninput="PurchaseOrdersPage.updateTotals()" ${canManage ? '' : 'disabled'}></td>
            <td><input class="line-qty" type="number" step="0.01" value="${line.quantity || 1}" oninput="PurchaseOrdersPage.updateTotals()" ${canManage ? '' : 'disabled'}></td>
            <td><select class="line-gst" onchange="PurchaseOrdersPage.updateTotals()" ${canManage ? '' : 'disabled'}>${gstOptionsHtml(line.gst_code || 'GST15')}</select></td>
            <td><input class="line-rate" type="number" step="0.01" value="${line.rate || 0}" oninput="PurchaseOrdersPage.updateTotals()" ${canManage ? '' : 'disabled'}></td>
            <td class="col-amount line-amount">${formatCurrency((line.quantity||1)*(line.rate||0))}</td>
            <td>${canManage ? `<button type="button" class="btn btn-sm btn-secondary" onclick="PurchaseOrdersPage.removeLine(${idx})">Remove</button>` : ''}</td>
        </tr>`;
    },

    addLine() {
        const idx = PurchaseOrdersPage.lineCount++;
        $('#po-lines').insertAdjacentHTML('beforeend', PurchaseOrdersPage.lineHtml(idx, {}, PurchaseOrdersPage._items, true));
        PurchaseOrdersPage.updateTotals();
    },

    removeLine(idx) {
        const row = $(`[data-poline="${idx}"]`);
        if (row) row.remove();
        PurchaseOrdersPage.updateTotals();
    },

    itemSel(idx) {
        const row = $(`[data-poline="${idx}"]`);
        const item = PurchaseOrdersPage._items.find(i => i.id == row.querySelector('.line-item').value);
        if (item) {
            row.querySelector('.line-desc').value = item.description || item.name;
            row.querySelector('.line-rate').value = item.cost || item.rate;
        }
        PurchaseOrdersPage.updateTotals();
    },

    updateTotals() {
        const lines = [];
        $$('#po-lines tr').forEach((row) => {
            const gst = readGstLinePayload(row);
            lines.push(gst);
            const amount = (gst.quantity || 0) * (gst.rate || 0);
            const amountCell = row.querySelector('.line-amount');
            if (amountCell) amountCell.textContent = formatCurrency(amount);
        });
        const totals = calculateGstTotals(lines, PurchaseOrdersPage._settings);
        const subtotal = $('#po-subtotal');
        const tax = $('#po-tax');
        const total = $('#po-total');
        if (subtotal) subtotal.textContent = formatCurrency(totals.subtotal);
        if (tax) tax.textContent = formatCurrency(totals.tax_amount);
        if (total) total.textContent = formatCurrency(totals.total);
    },

    async emailPurchaseOrder(id) {
        const po = await API.get(`/purchase-orders/${id}`);
        const vendor = po.vendor_id ? await API.get(`/vendors/${po.vendor_id}`) : null;
        App.showDocumentEmailModal({
            title: `Email Purchase Order #${po.po_number}`,
            endpoint: `/purchase-orders/${id}/email`,
            recipient: vendor?.email || '',
            defaultSubject: `Purchase Order #${po.po_number}`,
            successMessage: 'Purchase order emailed',
        });
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
            ship_to: form.ship_to.value || null,
            tax_rate: (parseFloat(PurchaseOrdersPage._settings.default_tax_rate || '0') || 0) / 100,
            notes: form.notes.value || null,
            lines,
        };
        try {
            if (id) { await API.put(`/purchase-orders/${id}`, data); toast('PO updated'); }
            else { await API.post('/purchase-orders', data); toast('PO created'); }
            PurchaseOrdersPage._detailState = null;
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
