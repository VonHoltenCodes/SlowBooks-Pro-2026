/**
 * Decompiled from QBW32.EXE!CCreateInvoicesView  Offset: 0x0015E400
 * This was the crown jewel of QB2003 — the "Create Invoices" form with
 * the yellow-tinted paper background texture (resource RT_BITMAP id=0x012C).
 * Line items were rendered in a custom owner-draw CListCtrl subclass called
 * CQBGridCtrl. We're using an HTML table instead. Less charming, more functional.
 * The original auto-fill from item selection was in CInvoiceForm::OnItemChanged()
 * at 0x0015E890 — same logic lives in itemSelected() below.
 */
const InvoicesPage = {
    async render() {
        const invoices = await API.get('/invoices');
        let html = `
            <div class="page-header">
                <h2>Invoices</h2>
                <button class="btn btn-primary" onclick="InvoicesPage.showForm()">+ New Invoice</button>
            </div>
            <div class="toolbar">
                <select id="inv-status-filter" onchange="InvoicesPage.applyFilter()">
                    <option value="">All Statuses</option>
                    <option value="draft">Draft</option>
                    <option value="sent">Sent</option>
                    <option value="partial">Partial</option>
                    <option value="paid">Paid</option>
                    <option value="void">Void</option>
                </select>
            </div>`;

        if (invoices.length === 0) {
            html += `<div class="empty-state"><p>No invoices yet</p></div>`;
        } else {
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th>#</th><th>Customer</th><th>Date</th><th>Due Date</th>
                    <th>Status</th><th class="amount">Total</th><th class="amount">Balance</th><th>Actions</th>
                </tr></thead><tbody id="inv-tbody">`;
            for (const inv of invoices) {
                html += `<tr class="inv-row" data-status="${inv.status}">
                    <td><strong>${escapeHtml(inv.invoice_number)}</strong></td>
                    <td>${escapeHtml(inv.customer_name || '')}</td>
                    <td>${formatDate(inv.date)}</td>
                    <td>${formatDate(inv.due_date)}</td>
                    <td>${statusBadge(inv.status)}</td>
                    <td class="amount">${formatCurrency(inv.total)}</td>
                    <td class="amount">${formatCurrency(inv.balance_due)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="InvoicesPage.view(${inv.id})">View</button>
                        <button class="btn btn-sm btn-secondary" onclick="InvoicesPage.showForm(${inv.id})">Edit</button>
                    </td>
                </tr>`;
            }
            html += `</tbody></table></div>`;
        }
        return html;
    },

    applyFilter() {
        const status = $('#inv-status-filter').value;
        $$('.inv-row').forEach(row => {
            row.style.display = (!status || row.dataset.status === status) ? '' : 'none';
        });
    },

    async view(id) {
        const inv = await API.get(`/invoices/${id}`);
        let linesHtml = inv.lines.map(l =>
            `<tr><td>${escapeHtml(l.description || '')}</td><td class="amount">${l.quantity}</td>
             <td class="amount">${formatCurrency(l.rate)}</td><td class="amount">${formatCurrency(l.amount)}</td></tr>`
        ).join('');

        openModal(`Invoice #${inv.invoice_number}`, `
            <div style="margin-bottom:12px;">
                <strong>Customer:</strong> ${escapeHtml(inv.customer_name || '')}<br>
                <strong>Date:</strong> ${formatDate(inv.date)}<br>
                <strong>Due:</strong> ${formatDate(inv.due_date)}<br>
                <strong>Status:</strong> ${statusBadge(inv.status)}<br>
                ${inv.po_number ? `<strong>PO#:</strong> ${escapeHtml(inv.po_number)}<br>` : ''}
            </div>
            <div class="table-container"><table>
                <thead><tr><th>Description</th><th class="amount">Qty</th><th class="amount">Rate</th><th class="amount">Amount</th></tr></thead>
                <tbody>${linesHtml}</tbody>
            </table></div>
            <div class="invoice-totals">
                <div class="total-row"><span class="label">Subtotal</span><span class="value">${formatCurrency(inv.subtotal)}</span></div>
                <div class="total-row"><span class="label">Tax</span><span class="value">${formatCurrency(inv.tax_amount)}</span></div>
                <div class="total-row grand-total"><span class="label">Total</span><span class="value">${formatCurrency(inv.total)}</span></div>
                <div class="total-row"><span class="label">Paid</span><span class="value">${formatCurrency(inv.amount_paid)}</span></div>
                <div class="total-row grand-total"><span class="label">Balance Due</span><span class="value">${formatCurrency(inv.balance_due)}</span></div>
            </div>
            ${inv.notes ? `<p style="margin-top:12px;color:var(--gray-500);">${escapeHtml(inv.notes)}</p>` : ''}
            <div class="form-actions">
                <button class="btn btn-secondary" onclick="window.open('/api/invoices/${inv.id}/pdf','_blank')">Print / PDF</button>
                <button class="btn btn-secondary" onclick="InvoicesPage.duplicate(${inv.id})">Duplicate</button>
                <button class="btn btn-secondary" onclick="InvoicesPage.emailInvoice(${inv.id})">Email Invoice</button>
                ${inv.status === 'draft' ? `<button class="btn btn-primary" onclick="InvoicesPage.markSent(${inv.id})">Mark Sent</button>` : ''}
                ${inv.status !== 'void' ? `<button class="btn btn-danger" onclick="InvoicesPage.void(${inv.id})">Void Invoice</button>` : ''}
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
    },

    async void(id) {
        if (!confirm('Void this invoice? This cannot be undone.')) return;
        try {
            await API.post(`/invoices/${id}/void`);
            toast('Invoice voided');
            closeModal();
            App.navigate(location.hash);
        } catch (err) { toast(err.message, 'error'); }
    },

    async markSent(id) {
        try {
            await API.post(`/invoices/${id}/send`);
            toast('Invoice marked as sent');
            closeModal();
            App.navigate(location.hash);
        } catch (err) { toast(err.message, 'error'); }
    },

    async duplicate(id) {
        try {
            const inv = await API.post(`/invoices/${id}/duplicate`);
            toast(`Duplicated as Invoice #${inv.invoice_number}`);
            closeModal();
            App.navigate('#/invoices');
        } catch (err) { toast(err.message, 'error'); }
    },

    async emailInvoice(id) {
        const inv = await API.get(`/invoices/${id}`);
        const email = inv.customer_email || '';
        openModal('Email Invoice', `
            <form onsubmit="InvoicesPage.sendEmail(event, ${id})">
                <div class="form-grid">
                    <div class="form-group full-width"><label>Recipient Email *</label>
                        <input name="recipient" type="email" required value="${escapeHtml(email)}"></div>
                    <div class="form-group full-width"><label>Subject</label>
                        <input name="subject" value="Invoice #${escapeHtml(inv.invoice_number)} from ${escapeHtml(inv.customer_name || 'us')}"></div>
                    <div class="form-group full-width"><label>Message</label>
                        <textarea name="message">Please find attached Invoice #${escapeHtml(inv.invoice_number)}.</textarea></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Send Email</button>
                </div>
            </form>`);
    },

    async sendEmail(e, id) {
        e.preventDefault();
        const form = e.target;
        try {
            await API.post(`/invoices/${id}/email`, {
                recipient: form.recipient.value,
                subject: form.subject.value,
                message: form.message.value,
            });
            toast('Invoice emailed');
            closeModal();
        } catch (err) { toast(err.message, 'error'); }
    },

    lineCount: 0,
    _customers: [],

    async showForm(id = null) {
        const [customers, items, settings] = await Promise.all([
            API.get('/customers?active_only=true'),
            API.get('/items?active_only=true'),
            API.get('/settings'),
        ]);

        let inv = {
            customer_id: '',
            date: todayISO(),
            terms: settings.default_terms || 'Net 30',
            po_number: '',
            tax_rate: (parseFloat(settings.default_tax_rate || '0') || 0) / 100,
            notes: settings.invoice_notes || '',
            lines: [],
        };
        if (id) inv = await API.get(`/invoices/${id}`);
        if (inv.lines.length === 0) inv.lines = [{ item_id: '', description: '', quantity: 1, rate: 0 }];

        InvoicesPage.lineCount = inv.lines.length;
        InvoicesPage._items = items;
        InvoicesPage._customers = customers;

        const custOpts = customers.map(c => `<option value="${c.id}" ${inv.customer_id==c.id?'selected':''}>${escapeHtml(c.name)}</option>`).join('');

        openModal(id ? 'Edit Invoice' : 'New Invoice', `
            <form id="invoice-form" onsubmit="InvoicesPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Customer *</label>
                        <select name="customer_id" required onchange="InvoicesPage.customerSelected(this.value)"><option value="">Select...</option>${custOpts}</select></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${inv.date}"></div>
                    <div class="form-group"><label>Terms</label>
                        <select name="terms" id="invoice-terms">
                            ${['Net 15','Net 30','Net 45','Net 60','Due on Receipt'].map(t =>
                                `<option value="${t}" ${inv.terms===t?'selected':''}>${t}</option>`).join('')}
                        </select></div>
                    <div class="form-group"><label>PO #</label>
                        <input name="po_number" value="${escapeHtml(inv.po_number || '')}"></div>
                    <div class="form-group"><label>Tax Rate (%)</label>
                        <input name="tax_rate" type="number" step="0.01" value="${(inv.tax_rate * 100) || 0}"
                            oninput="InvoicesPage.recalc()"></div>
                </div>
                <h3 style="margin:16px 0 8px; font-size:14px; color:var(--gray-600);">Line Items</h3>
                <table class="line-items-table">
                    <thead><tr>
                        <th>Item</th><th>Description</th><th class="col-qty">Qty</th>
                        <th class="col-rate">Rate</th><th class="col-amount">Amount</th><th class="col-actions"></th>
                    </tr></thead>
                    <tbody id="inv-lines">
                        ${inv.lines.map((l, i) => InvoicesPage.lineRowHtml(i, l, items)).join('')}
                    </tbody>
                </table>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="InvoicesPage.addLine()">+ Add Line</button>
                <div class="invoice-totals" id="inv-totals">
                    <div class="total-row"><span class="label">Subtotal</span><span class="value" id="inv-subtotal">$0.00</span></div>
                    <div class="total-row"><span class="label">Tax</span><span class="value" id="inv-tax">$0.00</span></div>
                    <div class="total-row grand-total"><span class="label">Total</span><span class="value" id="inv-total">$0.00</span></div>
                </div>
                <div class="form-group" style="margin-top:12px;"><label>Notes</label>
                    <textarea name="notes">${escapeHtml(inv.notes || '')}</textarea></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'} Invoice</button>
                </div>
            </form>`);
        if (!id && inv.customer_id) InvoicesPage.customerSelected(inv.customer_id);
        InvoicesPage.recalc();
    },

    customerSelected(customerId) {
        const customer = InvoicesPage._customers.find(c => c.id == customerId);
        const termsField = $('#invoice-terms');
        if (customer && termsField && customer.terms) {
            termsField.value = customer.terms;
        }
    },

    lineRowHtml(idx, line, items) {
        const itemOpts = items.map(i => `<option value="${i.id}" ${line.item_id==i.id?'selected':''}>${escapeHtml(i.name)}</option>`).join('');
        return `<tr data-line="${idx}">
            <td><select class="line-item" onchange="InvoicesPage.itemSelected(${idx})">
                <option value="">--</option>${itemOpts}</select></td>
            <td><input class="line-desc" value="${escapeHtml(line.description || '')}"></td>
            <td><input class="line-qty" type="number" step="0.01" value="${line.quantity || 1}" oninput="InvoicesPage.recalc()"></td>
            <td><input class="line-rate" type="number" step="0.01" value="${line.rate || 0}" oninput="InvoicesPage.recalc()"></td>
            <td class="col-amount line-amount">${formatCurrency((line.quantity||1) * (line.rate||0))}</td>
            <td><button type="button" class="btn btn-sm btn-danger" onclick="InvoicesPage.removeLine(${idx})">X</button></td>
        </tr>`;
    },

    addLine() {
        const tbody = $('#inv-lines');
        const idx = InvoicesPage.lineCount++;
        tbody.insertAdjacentHTML('beforeend', InvoicesPage.lineRowHtml(idx, {}, InvoicesPage._items));
    },

    removeLine(idx) {
        const row = $(`[data-line="${idx}"]`);
        if (row) row.remove();
        InvoicesPage.recalc();
    },

    itemSelected(idx) {
        const row = $(`[data-line="${idx}"]`);
        const itemId = row.querySelector('.line-item').value;
        const item = InvoicesPage._items.find(i => i.id == itemId);
        if (item) {
            row.querySelector('.line-desc').value = item.description || item.name;
            row.querySelector('.line-rate').value = item.rate;
            InvoicesPage.recalc();
        }
    },

    recalc() {
        let subtotal = 0;
        $$('#inv-lines tr').forEach(row => {
            const qty = parseFloat(row.querySelector('.line-qty')?.value) || 0;
            const rate = parseFloat(row.querySelector('.line-rate')?.value) || 0;
            const amount = qty * rate;
            subtotal += amount;
            const amountCell = row.querySelector('.line-amount');
            if (amountCell) amountCell.textContent = formatCurrency(amount);
        });
        const taxPct = parseFloat($('[name="tax_rate"]')?.value) || 0;
        const tax = subtotal * (taxPct / 100);
        $('#inv-subtotal').textContent = formatCurrency(subtotal);
        $('#inv-tax').textContent = formatCurrency(tax);
        $('#inv-total').textContent = formatCurrency(subtotal + tax);
    },

    async save(e, id) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        $$('#inv-lines tr').forEach((row, i) => {
            const item_id = row.querySelector('.line-item')?.value;
            lines.push({
                item_id: item_id ? parseInt(item_id) : null,
                description: row.querySelector('.line-desc')?.value || '',
                quantity: parseFloat(row.querySelector('.line-qty')?.value) || 1,
                rate: parseFloat(row.querySelector('.line-rate')?.value) || 0,
                line_order: i,
            });
        });

        const data = {
            customer_id: parseInt(form.customer_id.value),
            date: form.date.value,
            terms: form.terms.value,
            po_number: form.po_number.value || null,
            tax_rate: (parseFloat(form.tax_rate.value) || 0) / 100,
            notes: form.notes.value || null,
            lines,
        };

        try {
            if (id) { await API.put(`/invoices/${id}`, data); toast('Invoice updated'); }
            else { await API.post('/invoices', data); toast('Invoice created'); }
            closeModal();
            App.navigate(location.hash);
        } catch (err) { toast(err.message, 'error'); }
    },
};
