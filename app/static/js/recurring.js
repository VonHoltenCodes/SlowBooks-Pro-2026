/**
 * Recurring Invoices — schedule automatic invoice generation
 * Feature 2: Weekly/monthly/quarterly/yearly templates
 */
const RecurringPage = {
    async render() {
        const recs = await API.get('/recurring');
        let html = `
            <div class="page-header">
                <h2>Recurring Invoices</h2>
                <div class="btn-group">
                    <button class="btn btn-primary" onclick="RecurringPage.showForm()">+ New Recurring</button>
                    <button class="btn btn-secondary" onclick="RecurringPage.generateNow()">Generate Due Now</button>
                </div>
            </div>`;

        if (recs.length === 0) {
            html += '<div class="empty-state"><p>No recurring invoices set up</p></div>';
        } else {
            html += `<div class="table-container"><table>
                <thead><tr><th>Customer</th><th>Frequency</th><th>Next Due</th><th>Active</th><th>Created</th><th>Actions</th></tr></thead><tbody>`;
            for (const r of recs) {
                html += `<tr>
                    <td><strong>${escapeHtml(r.customer_name || '')}</strong></td>
                    <td>${r.frequency}</td>
                    <td>${formatDate(r.next_due)}</td>
                    <td>${r.is_active ? '<span class="badge badge-paid">Active</span>' : '<span class="badge badge-draft">Inactive</span>'}</td>
                    <td style="font-family:var(--font-mono);">${r.invoices_created}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="RecurringPage.showForm(${r.id})">Edit</button>
                        <button class="btn btn-sm btn-danger" onclick="RecurringPage.del(${r.id})">Delete</button>
                    </td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    _items: [],
    _customers: [],
    lineCount: 0,

    async showForm(id = null) {
        const [customers, items, settings] = await Promise.all([
            API.get('/customers?active_only=true'),
            API.get('/items?active_only=true'),
            API.get('/settings'),
        ]);
        RecurringPage._items = items;
        RecurringPage._customers = customers;

        let rec = {
            customer_id: '',
            frequency: 'monthly',
            start_date: todayISO(),
            end_date: '',
            terms: settings.default_terms || 'Net 30',
            tax_rate: (parseFloat(settings.default_tax_rate || '0') || 0) / 100,
            notes: '',
            lines: [],
        };
        if (id) rec = await API.get(`/recurring/${id}`);
        if (rec.lines.length === 0) rec.lines = [{ item_id: '', description: '', quantity: 1, rate: 0 }];
        RecurringPage.lineCount = rec.lines.length;

        const custOpts = customers.map(c => `<option value="${c.id}" ${rec.customer_id==c.id?'selected':''}>${escapeHtml(c.name)}</option>`).join('');
        const itemOpts = items.map(i => `<option value="${i.id}">${escapeHtml(i.name)}</option>`).join('');

        openModal(id ? 'Edit Recurring Invoice' : 'New Recurring Invoice', `
            <form onsubmit="RecurringPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Customer *</label>
                        <select name="customer_id" required onchange="RecurringPage.customerSelected(this.value)"><option value="">Select...</option>${custOpts}</select></div>
                    <div class="form-group"><label>Frequency *</label>
                        <select name="frequency">
                            ${['weekly','monthly','quarterly','yearly'].map(f =>
                                `<option ${rec.frequency===f?'selected':''}>${f}</option>`).join('')}
                        </select></div>
                    <div class="form-group"><label>Start Date *</label>
                        <input name="start_date" type="date" required value="${rec.start_date}"></div>
                    <div class="form-group"><label>End Date</label>
                        <input name="end_date" type="date" value="${rec.end_date || ''}"></div>
                    <div class="form-group"><label>Terms</label>
                        <select name="terms" id="recurring-terms">
                            ${['Net 15','Net 30','Net 45','Net 60','Due on Receipt'].map(t =>
                                `<option value="${t}" ${rec.terms===t?'selected':''}>${t}</option>`).join('')}
                        </select></div>
                    <div class="form-group"><label>Tax Rate (%)</label>
                        <input name="tax_rate" type="number" step="0.01" value="${(rec.tax_rate * 100) || 0}"></div>
                </div>
                <h3 style="margin:12px 0 8px;font-size:14px;">Line Items</h3>
                <table class="line-items-table">
                    <thead><tr><th>Item</th><th>Description</th><th class="col-qty">Qty</th><th class="col-rate">Rate</th></tr></thead>
                    <tbody id="rec-lines">
                        ${rec.lines.map((l, i) => {
                            const opts = items.map(it => `<option value="${it.id}" ${l.item_id==it.id?'selected':''}>${escapeHtml(it.name)}</option>`).join('');
                            return `<tr data-recline="${i}">
                                <td><select class="line-item"><option value="">--</option>${opts}</select></td>
                                <td><input class="line-desc" value="${escapeHtml(l.description || '')}"></td>
                                <td><input class="line-qty" type="number" step="0.01" value="${l.quantity || 1}"></td>
                                <td><input class="line-rate" type="number" step="0.01" value="${l.rate || 0}"></td>
                            </tr>`;
                        }).join('')}
                    </tbody>
                </table>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="RecurringPage.addLine()">+ Add Line</button>
                <div class="form-group" style="margin-top:12px;"><label>Notes</label>
                    <textarea name="notes">${escapeHtml(rec.notes || '')}</textarea></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'}</button>
                </div>
            </form>`);
        if (!id && rec.customer_id) RecurringPage.customerSelected(rec.customer_id);
    },

    customerSelected(customerId) {
        const customer = RecurringPage._customers.find(c => c.id == customerId);
        const termsField = $('#recurring-terms');
        if (customer && termsField && customer.terms) {
            termsField.value = customer.terms;
        }
    },

    addLine() {
        const idx = RecurringPage.lineCount++;
        const itemOpts = RecurringPage._items.map(i => `<option value="${i.id}">${escapeHtml(i.name)}</option>`).join('');
        $('#rec-lines').insertAdjacentHTML('beforeend', `
            <tr data-recline="${idx}">
                <td><select class="line-item"><option value="">--</option>${itemOpts}</select></td>
                <td><input class="line-desc"></td>
                <td><input class="line-qty" type="number" step="0.01" value="1"></td>
                <td><input class="line-rate" type="number" step="0.01" value="0"></td>
            </tr>`);
    },

    async save(e, id) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        $$('#rec-lines tr').forEach((row, i) => {
            lines.push({
                item_id: row.querySelector('.line-item')?.value ? parseInt(row.querySelector('.line-item').value) : null,
                description: row.querySelector('.line-desc')?.value || '',
                quantity: parseFloat(row.querySelector('.line-qty')?.value) || 1,
                rate: parseFloat(row.querySelector('.line-rate')?.value) || 0,
                line_order: i,
            });
        });
        const data = {
            customer_id: parseInt(form.customer_id.value),
            frequency: form.frequency.value,
            start_date: form.start_date.value,
            end_date: form.end_date.value || null,
            terms: form.terms.value,
            tax_rate: (parseFloat(form.tax_rate.value) || 0) / 100,
            notes: form.notes.value || null,
            lines,
        };
        try {
            if (id) { await API.put(`/recurring/${id}`, data); toast('Recurring updated'); }
            else { await API.post('/recurring', data); toast('Recurring created'); }
            closeModal();
            App.navigate('#/recurring');
        } catch (err) { toast(err.message, 'error'); }
    },

    async del(id) {
        if (!confirm('Delete this recurring invoice?')) return;
        try {
            await API.del(`/recurring/${id}`);
            toast('Deleted');
            App.navigate('#/recurring');
        } catch (err) { toast(err.message, 'error'); }
    },

    async generateNow() {
        try {
            const result = await API.post('/recurring/generate');
            toast(`Generated ${result.invoices_created} invoice(s)`);
            App.navigate('#/recurring');
        } catch (err) { toast(err.message, 'error'); }
    },
};
