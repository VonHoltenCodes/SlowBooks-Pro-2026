/**
 * Recurring Invoices — schedule automatic invoice generation
 * Feature 2: Weekly/monthly/quarterly/yearly templates
 */
const RecurringPage = {
    async render() {
        const recs = await API.get('/recurring');
        let html = `
            <div class="page-header">
                <h2>${T('Recurring Invoices')}</h2>
                <div class="btn-group">
                    <button class="btn btn-primary" onclick="RecurringPage.showForm()">+ New Recurring</button>
                    <button class="btn btn-secondary" onclick="RecurringPage.generateNow()">Generate Due Now</button>
                </div>
            </div>`;

        if (recs.length === 0) {
            html += '<div class="empty-state"><p>No recurring invoices set up</p></div>';
        } else {
            html += `<div class="table-container"><table>
                <thead><tr><th scope="col">${T('Customer')}</th><th scope="col">Frequency</th><th scope="col">Next Due</th><th scope="col">Active</th><th scope="col">Created</th><th scope="col">Actions</th></tr></thead><tbody>`;
            for (const r of recs) {
                html += `<tr>
                    <td><strong>${escapeHtml(r.customer_name || '')}</strong></td>
                    <td>${r.frequency}</td>
                    <td>${formatDate(r.next_due)}</td>
                    <td>${r.is_active ? '<span class="badge badge-paid">Active</span>' : '<span class="badge badge-draft">Inactive</span>'}</td>
                    <td style="font-family:var(--font-mono);">${r.invoices_created}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="RecurringPage.showForm(${r.id})">Edit</button>
                        <button data-destructive class="btn btn-sm btn-danger" onclick="RecurringPage.del(${r.id})">Delete</button>
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
            API.get('/customers?active_only=true&tree=1'),
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
        // the terms list (#664); each invoice's due date follows its rule
        await PaymentTerms.load();
        const classGroup = await classFormGroupHtml(rec.class_id);
        if (rec.lines.length === 0) rec.lines = [{ item_id: '', description: '', quantity: 1, rate: 0 }];
        RecurringPage.lineCount = rec.lines.length;

        const custOpts = customers.map(c => `<option value="${c.id}" ${rec.customer_id==c.id?'selected':''}>${escapeHtml(customerLabel(c))}</option>`).join('');
        // A saved schedule keeps its customer and start date (the API edits
        // neither); sending them made every Update a 422.
        const locked = id ? 'disabled title="A saved schedule keeps its customer and start date"' : '';

        openModal(id ? `Edit ${T('Recurring Invoices').replace(/s$/, '')}` : `New ${T('Recurring Invoices').replace(/s$/, '')}`, `
            <form id="rec-form" onsubmit="RecurringPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>${T('Customer')} *</label>
                        <select name="customer_id" id="rec-customer-select" required ${locked} onchange="RecurringPage.customerSelected(this.value)"><option value="">Select...</option>${custOpts}</select></div>
                    <div class="form-group"><label>Frequency *</label>
                        <select name="frequency">
                            ${['weekly','monthly','quarterly','yearly'].map(f =>
                                `<option ${rec.frequency===f?'selected':''}>${f}</option>`).join('')}
                        </select></div>
                    <div class="form-group"><label>Start Date *</label>
                        <input name="start_date" type="date" required ${locked} value="${rec.start_date}"></div>
                    <div class="form-group"><label>End Date</label>
                        <input name="end_date" type="date" value="${rec.end_date || ''}"></div>
                    <div class="form-group"><label>Terms</label>
                        <select name="terms" id="recurring-terms">
                            ${PaymentTerms.optionsHtml(rec.terms)}
                        </select></div>
                    <div class="form-group"><label>Tax Rate (%)</label>
                        <input name="tax_rate" type="number" step="0.0001" value="${+((rec.tax_rate || 0) * 100).toFixed(4)}" oninput="RecurringPage.recalc()"></div>
                    ${classGroup}
                    ${id ? `<div class="form-group"><label style="font-weight:normal"><input type="checkbox" name="is_active" ${rec.is_active !== false ? 'checked' : ''}> Active</label></div>` : ''}
                </div>
                <h3 style="margin:12px 0 8px;font-size:14px;">Line Items</h3>
                <table class="line-items-table">
                    <thead><tr><th scope="col">Item</th><th scope="col">Description</th><th scope="col" class="col-qty">Qty</th><th scope="col" class="col-rate">Rate</th><th scope="col" title="Sales tax applies to this line">Tax</th><th scope="col" class="col-amount">Amount</th><th scope="col" class="col-actions"></th></tr></thead>
                    <tbody id="rec-lines">
                        ${rec.lines.map((l, i) => RecurringPage.lineRowHtml(i, l)).join('')}
                    </tbody>
                </table>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="RecurringPage.addLine()">+ Add Line</button>
                <div class="invoice-totals" id="rec-totals">
                    <div class="total-row"><span class="label">Subtotal</span><span class="value" id="rec-subtotal">$0.00</span></div>
                    <div class="total-row"><span class="label">Tax</span><span class="value" id="rec-tax">$0.00</span></div>
                    <div class="total-row grand-total"><span class="label">Each ${T('Invoice')}</span><span class="value" id="rec-total">$0.00</span></div>
                </div>
                <div class="form-group" style="margin-top:12px;"><label>Notes</label>
                    <textarea name="notes">${escapeHtml(rec.notes || '')}</textarea></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'}</button>
                </div>
            </form>`);
        typeof LineOrder !== 'undefined' && LineOrder.attach('#rec-lines', { add: () => RecurringPage.addLine(), changed: () => RecurringPage.recalc() });
        if (!id && rec.customer_id) RecurringPage.customerSelected(rec.customer_id);
        RecurringPage.recalc();
    },

    // One schedule line, priced from the item the way an invoice line is.
    lineRowHtml(idx, line) {
        return `<tr data-recline="${idx}">
                <td><select class="line-item" onchange="RecurringPage.itemSelected(${idx})">${ItemQuickAdd.options(RecurringPage._items, line.item_id)}</select></td>
                <td><input class="line-desc" value="${escapeHtml(line.description || '')}"></td>
                <td><input class="line-qty" type="number" step="0.0001" value="${line.quantity || 1}" oninput="RecurringPage.recalc()"></td>
                <td><input class="line-rate money" type="number" step="0.0001" min="0" value="${Number(line.rate) || 0}" oninput="RecurringPage.recalc()"></td>
                <td style="text-align:center"><input type="checkbox" class="line-taxable" title="Sales tax applies to this line" ${TaxDefault.checked(line)} onchange="RecurringPage.recalc()"></td>
                <td class="col-amount line-amount">$0.00</td>
                <td><button type="button" class="btn btn-sm btn-danger" aria-label="Remove line" onclick="RecurringPage.removeLine(${idx})">X</button></td>
            </tr>`;
    },

    customerSelected(customerId) {
        const customer = RecurringPage._customers.find(c => c.id == customerId);
        const termsField = $('#recurring-terms');
        if (customer && termsField && customer.terms) {
            PaymentTerms.choose(termsField, customer.terms);
        }
        RecurringPage.recalc();
    },

    addLine() {
        const idx = RecurringPage.lineCount++;
        $('#rec-lines').insertAdjacentHTML('beforeend', RecurringPage.lineRowHtml(idx, {}));
        RecurringPage.recalc();
    },

    removeLine(idx) {
        const row = $(`[data-recline="${idx}"]`);
        if (row) row.remove();
        RecurringPage.recalc();
    },

    itemSelected(idx) {
        const row = $(`[data-recline="${idx}"]`);
        if (row && ItemQuickAdd.selected(row.querySelector('.line-item'), RecurringPage._items, () => RecurringPage.itemSelected(idx))) return;
        if (row && SalesLines.fillFromItem(row, RecurringPage._items)) RecurringPage.recalc();
    },

    recalc() {
        TaxExempt.enforce(RecurringPage._customers, $('#rec-customer-select')?.value, $('#rec-lines'));
        const t = SalesLines.totals($('#rec-lines'), $('#rec-form [name="tax_rate"]')?.value);
        SalesLines.show(t, ['rec-subtotal', 'rec-tax', 'rec-total']);
        return t;
    },

    async save(e, id) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        $$('#rec-lines tr').forEach((row, i) => {
            lines.push({
                item_id: ItemQuickAdd.itemId(row.querySelector('.line-item')),
                description: row.querySelector('.line-desc')?.value || '',
                quantity: parseFloat(row.querySelector('.line-qty')?.value) || 1,
                is_taxable: row.querySelector('.line-taxable') ? row.querySelector('.line-taxable').checked : null,
                rate: parseFloat(row.querySelector('.line-rate')?.value) || 0,
                line_order: i,
            });
        });
        const data = {
            frequency: form.frequency.value,
            end_date: form.end_date.value || null,
            terms: form.terms.value,
            tax_rate: (parseFloat(form.tax_rate.value) || 0) / 100,
            notes: form.notes.value || null,
            class_id: classIdFromForm(form),
            lines,
        };
        // Active pauses a schedule that has made invoices (it cannot be deleted);
        // an End Date stops it for good. Re-ticking Active skips the missed
        // periods rather than back-dating them.
        if (id) data.is_active = form.is_active.checked;
        if (!id) {
            data.customer_id = parseInt(form.customer_id.value);
            data.start_date = form.start_date.value;
        }
        if (!(await ClassWarn.ok(form))) return;
        try {
            if (id) { await API.put(`/recurring/${id}`, data); toast('Recurring updated'); }
            else { await API.post('/recurring', data); toast('Recurring created'); }
            closeModal();
            App.navigate(RecurringPage.backTo());
        } catch (err) { toast(err.message, 'error'); }
    },

    // The list to return to: the Memorized transactions page when the form
    // was opened from there (#605), else this page.
    backTo() {
        return location.hash.startsWith('#/memorized') ? '#/memorized' : '#/recurring';
    },

    async del(id) {
        if (!confirm('Delete this recurring invoice?')) return;
        try {
            await API.del(`/recurring/${id}`);
            toast('Deleted');
            App.navigate(RecurringPage.backTo());
        } catch (err) { toast(err.message, 'error'); }
    },

    async generateNow() {
        try {
            const result = await API.post('/recurring/generate');
            toast(`Generated ${result.invoices_created} ${T('invoice')}(s)`);
            // A schedule that adds up to $0.00 creates nothing; say which.
            (result.skipped || []).forEach(s => toast(s.message, 'error'));
            App.navigate('#/recurring');
        } catch (err) { toast(err.message, 'error'); }
    },
};
