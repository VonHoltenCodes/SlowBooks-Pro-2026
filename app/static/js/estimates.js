/**
 * Decompiled from QBW32.EXE!CCreateEstimatesView  Offset: 0x00195200
 * Same form as invoices (see invoices.js) but with a green tint instead of
 * yellow (RT_BITMAP id=0x012D). The "Create Invoice" button called
 * CEstimate::ConvertToInvoice() at 0x001944A0 which deep-copied every field
 * and line item, then set EstimateStatus to CONVERTED. Our version does the
 * same thing but through an API call instead of a COM QueryInterface.
 */
const EstimatesPage = {
    async render() {
        const estimates = await API.get('/estimates');
        const canManageSales = App.hasPermission ? App.hasPermission('sales.manage') : true;
        let html = `
            <div class="page-header">
                <h2>Estimates</h2>
                ${canManageSales ? `<button class="btn btn-primary" onclick="EstimatesPage.showForm()">+ New Estimate</button>` : ''}
            </div>`;

        if (estimates.length === 0) {
            html += `<div class="empty-state"><p>No estimates yet</p></div>`;
        } else {
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th>#</th><th>Customer</th><th>Date</th><th>Expires</th>
                    <th>Status</th><th class="amount">Total</th><th>Actions</th>
                </tr></thead><tbody>`;
            for (const est of estimates) {
                html += `<tr>
                    <td><strong>${escapeHtml(est.estimate_number)}</strong></td>
                    <td>${escapeHtml(est.customer_name || '')}</td>
                    <td>${formatDate(est.date)}</td>
                    <td>${formatDate(est.expiration_date)}</td>
                    <td>${statusBadge(est.status)}</td>
                    <td class="amount">${formatCurrency(est.total)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="EstimatesPage.view(${est.id})">View</button>
                        ${canManageSales ? `<button class="btn btn-sm btn-secondary" onclick="EstimatesPage.showForm(${est.id})">Edit</button>
                        ${est.status !== 'converted' ? `<button class="btn btn-sm btn-primary" onclick="EstimatesPage.convert(${est.id})">Convert</button>` : ''}` : ''}
                    </td>
                </tr>`;
            }
            html += `</tbody></table></div>`;
        }
        return html;
    },

    async view(id) {
        const est = await API.get(`/estimates/${id}`);
        let linesHtml = est.lines.map(l =>
            `<tr><td>${escapeHtml(l.description || '')}</td><td class="amount">${l.quantity}</td>
             <td class="amount">${formatCurrency(l.rate)}</td><td class="amount">${formatCurrency(l.amount)}</td></tr>`
        ).join('');

        openModal(`Estimate #${est.estimate_number}`, `
            <div style="margin-bottom:12px;">
                <strong>Customer:</strong> ${escapeHtml(est.customer_name || '')}<br>
                <strong>Date:</strong> ${formatDate(est.date)}<br>
                ${est.expiration_date ? `<strong>Expires:</strong> ${formatDate(est.expiration_date)}<br>` : ''}
                <strong>Status:</strong> ${statusBadge(est.status)}
            </div>
            <div class="table-container"><table>
                <thead><tr><th>Description</th><th class="amount">Qty</th><th class="amount">Rate</th><th class="amount">Amount</th></tr></thead>
                <tbody>${linesHtml}</tbody>
            </table></div>
            <div class="invoice-totals">
                <div class="total-row"><span class="label">Subtotal</span><span class="value">${formatCurrency(est.subtotal)}</span></div>
                <div class="total-row"><span class="label">Tax</span><span class="value">${formatCurrency(est.tax_amount)}</span></div>
                <div class="total-row grand-total"><span class="label">Total</span><span class="value">${formatCurrency(est.total)}</span></div>
            </div>
            ${est.notes ? `<p style="margin-top:12px;color:var(--gray-500);">${escapeHtml(est.notes)}</p>` : ''}
            <div class="form-actions">
                <button class="btn btn-secondary" onclick="API.open('/estimates/${est.id}/pdf', 'estimate-${est.estimate_number}.pdf')">Print / PDF</button>
                ${App.hasPermission && !App.hasPermission('sales.manage') ? '' : `<button class="btn btn-secondary" onclick="EstimatesPage.emailEstimate(${est.id})">Email</button>
                ${est.status !== 'converted' ? `<button class="btn btn-primary" onclick="EstimatesPage.convert(${est.id})">Convert to Invoice</button>` : ''}` }
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
    },

    async emailEstimate(id) {
        const est = await API.get(`/estimates/${id}`);
        const customer = est.customer_id ? await API.get(`/customers/${est.customer_id}`) : null;
        App.showDocumentEmailModal({
            title: `Email Estimate #${est.estimate_number}`,
            endpoint: `/estimates/${id}/email`,
            recipient: customer?.email || '',
            defaultSubject: `Estimate #${est.estimate_number}`,
            successMessage: 'Estimate emailed',
        });
    },

    async convert(id) {
        if (!confirm('Convert this estimate to an invoice?')) return;
        try {
            const inv = await API.post(`/estimates/${id}/convert`);
            toast(`Created Invoice #${inv.invoice_number}`);
            closeModal();
            App.navigate('#/invoices');
        } catch (err) { toast(err.message, 'error'); }
    },

    lineCount: 0,
    _items: [],

    async showForm(id = null) {
        const [customers, items, settings, gstCodes] = await Promise.all([
            API.get('/customers?active_only=true'),
            API.get('/items?active_only=true'),
            API.get('/settings'),
            API.get('/gst-codes'),
        ]);
        App.gstCodes = gstCodes;

        let est = {
            customer_id: '',
            date: todayISO(),
            expiration_date: '',
            tax_rate: (parseFloat(settings.default_tax_rate || '0') || 0) / 100,
            notes: '',
            lines: [],
        };
        if (id) est = await API.get(`/estimates/${id}`);
        if (est.lines.length === 0) est.lines = [{ item_id: '', description: '', quantity: 1, rate: 0 }];

        EstimatesPage.lineCount = est.lines.length;
        EstimatesPage._items = items;

        const custOpts = customers.map(c => `<option value="${c.id}" ${est.customer_id==c.id?'selected':''}>${escapeHtml(c.name)}</option>`).join('');

        openModal(id ? 'Edit Estimate' : 'New Estimate', `
            <form id="est-form" onsubmit="EstimatesPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Customer *</label>
                        <select name="customer_id" required><option value="">Select...</option>${custOpts}</select></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${est.date}"></div>
                    <div class="form-group"><label>Expiration Date</label>
                        <input name="expiration_date" type="date" value="${est.expiration_date || ''}"></div>
                    <input name="tax_rate" type="hidden" value="${(est.tax_rate * 100) || 0}">
                </div>
                <h3 style="margin:16px 0 8px; font-size:14px; color:var(--gray-600);">Line Items</h3>
                <table class="line-items-table">
                    <thead><tr>
                        <th>Item</th><th>Description</th><th class="col-qty">Qty</th><th>GST</th>
                        <th class="col-rate">Rate</th><th class="col-amount">Amount</th><th class="col-actions"></th>
                    </tr></thead>
                    <tbody id="est-lines">
                        ${est.lines.map((l, i) => EstimatesPage.lineRowHtml(i, l, items)).join('')}
                    </tbody>
                </table>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="EstimatesPage.addLine()">+ Add Line</button>
                <div class="invoice-totals" id="est-totals">
                    <div class="total-row"><span class="label">Subtotal</span><span class="value" id="est-subtotal">$0.00</span></div>
                    <div class="total-row"><span class="label">Tax</span><span class="value" id="est-tax">$0.00</span></div>
                    <div class="total-row grand-total"><span class="label">Total</span><span class="value" id="est-total">$0.00</span></div>
                </div>
                <div class="form-group" style="margin-top:12px;"><label>Notes</label>
                    <textarea name="notes">${escapeHtml(est.notes || '')}</textarea></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'} Estimate</button>
                </div>
            </form>`);
        EstimatesPage.recalc();
    },

    lineRowHtml(idx, line, items) {
        const itemOpts = items.map(i => `<option value="${i.id}" ${line.item_id==i.id?'selected':''}>${escapeHtml(i.name)}</option>`).join('');
        return `<tr data-eline="${idx}">
            <td><select class="line-item" onchange="EstimatesPage.itemSelected(${idx})">
                <option value="">--</option>${itemOpts}</select></td>
            <td><input class="line-desc" value="${escapeHtml(line.description || '')}"></td>
            <td><input class="line-qty" type="number" step="0.01" value="${line.quantity || 1}" oninput="EstimatesPage.recalc()"></td>
            <td><select class="line-gst" onchange="EstimatesPage.recalc()">${gstOptionsHtml(line.gst_code || 'GST15')}</select></td>
            <td><input class="line-rate" type="number" step="0.01" value="${line.rate || 0}" oninput="EstimatesPage.recalc()"></td>
            <td class="col-amount line-amount">${formatCurrency((line.quantity||1) * (line.rate||0))}</td>
            <td><button type="button" class="btn btn-sm btn-danger" onclick="EstimatesPage.removeLine(${idx})">X</button></td>
        </tr>`;
    },

    addLine() {
        const tbody = $('#est-lines');
        const idx = EstimatesPage.lineCount++;
        tbody.insertAdjacentHTML('beforeend', EstimatesPage.lineRowHtml(idx, {}, EstimatesPage._items));
    },

    removeLine(idx) {
        const row = $(`[data-eline="${idx}"]`);
        if (row) row.remove();
        EstimatesPage.recalc();
    },

    itemSelected(idx) {
        const row = $(`[data-eline="${idx}"]`);
        const itemId = row.querySelector('.line-item').value;
        const item = EstimatesPage._items.find(i => i.id == itemId);
        if (item) {
            row.querySelector('.line-desc').value = item.description || item.name;
            row.querySelector('.line-rate').value = item.rate;
            EstimatesPage.recalc();
        }
    },

    recalc() {
        const lines = [];
        $$('#est-lines tr').forEach(row => {
            const payload = readGstLinePayload(row);
            const amount = payload.quantity * payload.rate;
            lines.push(payload);
            const amountCell = row.querySelector('.line-amount');
            if (amountCell) amountCell.textContent = formatCurrency(amount);
        });
        const totals = calculateGstTotals(lines);
        if ($('#est-subtotal')) $('#est-subtotal').textContent = formatCurrency(totals.subtotal);
        if ($('#est-tax')) $('#est-tax').textContent = formatCurrency(totals.tax_amount);
        if ($('#est-total')) $('#est-total').textContent = formatCurrency(totals.total);
    },

    async save(e, id) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        $$('#est-lines tr').forEach((row, i) => {
            const item_id = row.querySelector('.line-item')?.value;
            const gst = readGstLinePayload(row);
            lines.push({
                item_id: item_id ? parseInt(item_id) : null,
                description: row.querySelector('.line-desc')?.value || '',
                quantity: gst.quantity,
                rate: gst.rate,
                gst_code: gst.gst_code,
                gst_rate: gst.gst_rate,
                line_order: i,
            });
        });

        const data = {
            customer_id: parseInt(form.customer_id.value),
            date: form.date.value,
            expiration_date: form.expiration_date.value || null,
            tax_rate: (parseFloat(form.tax_rate.value) || 0) / 100,
            notes: form.notes.value || null,
            lines,
        };

        try {
            if (id) { await API.put(`/estimates/${id}`, data); toast('Estimate updated'); }
            else { await API.post('/estimates', data); toast('Estimate created'); }
            closeModal();
            App.navigate(location.hash);
        } catch (err) { toast(err.message, 'error'); }
    },
};
