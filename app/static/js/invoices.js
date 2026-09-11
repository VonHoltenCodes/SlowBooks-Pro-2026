/**
 * The "Create Invoices" form — QB2003's crown jewel, yellow paper
 * texture and all. We use an HTML table instead of a custom grid;
 * auto-fill on item selection lives in itemSelected() below.
 */
const InvoicesPage = {
    // The document's literal face, as the PDF prints it: a flagged pledge is a
    // PLEDGE, everything else an INVOICE regardless of company vocabulary.

    async render() {
        // Sales receipts are invoices under the hood; they get their own
        // page, so keep them out of this list.
        const invoices = await API.get('/invoices?is_sales_receipt=false');
        return renderListPage({
            title: T('Invoices'),
            headerHtml: `<button class="btn btn-primary" onclick="InvoicesPage.showForm()">+ ${T('New Invoice')}</button>`,
            filter: {
                id: 'inv-status-filter',
                rowSelector: '.inv-row',
                options: [['draft', 'Draft'], ['sent', 'Sent'], ['partial', 'Partial'], ['paid', 'Paid'], ['void', 'Void']],
            },
            empty: Terms.text(`<p>No invoices yet.</p>
                <button class="btn btn-primary" onclick="InvoicesPage.showForm()" style="margin-top:10px;">+ Create your first invoice</button>`),
            columns: ['#', T('Customer'), 'Date', 'Due Date', 'Status',
                { label: 'Total', cls: 'amount' }, { label: 'Balance', cls: 'amount' }, 'Actions'],
            items: invoices,
            row: inv => `<tr class="inv-row" data-status="${inv.status}">
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
                        ${inv.status === 'draft' ? `<button class="btn btn-sm btn-primary" onclick="InvoicesPage.markSent(${inv.id})">Mark Sent</button>` : ''}
                        ${Terms.isNonprofit() && inv.status !== 'void' && parseFloat(inv.balance_due) > 0 ? `<button class="btn btn-sm btn-secondary" onclick="InvoicesPage.showWriteOff(${inv.id}, ${parseFloat(inv.balance_due)})">Write Off</button>` : ''}
                    </td>
                </tr>`,
        });
    },

    // Nonprofit: forgive an open balance (a pledge that will never be paid)
    // — a write-off credit memo to Bad Debt Expense, applied at once.
    showWriteOff(id, balance) {
        openModal('Write Off Balance', `
            <form onsubmit="InvoicesPage.saveWriteOff(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Date *</label><input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Amount *</label><input name="amount" type="number" step="0.01" min="0.01" max="${balance}" required value="${balance.toFixed(2)}"></div>
                    <div class="form-group full-width"><label>Memo</label><input name="memo" placeholder="e.g. pledge withdrawn"></div>
                </div>
                <div style="font-size:11px;color:var(--gray-500);margin-top:6px">Posts a credit memo to Bad Debt Expense and applies it to this ${T('invoice')}. Void the credit memo to undo.</div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Write Off</button>
                </div>
            </form>`);
    },

    async saveWriteOff(e, id) {
        e.preventDefault();
        const form = e.target;
        try {
            const cm = await API.post(`/invoices/${id}/write-off`, {
                date: form.date.value, amount: parseFloat(form.amount.value), memo: form.memo.value || null,
            });
            toast(`Written off as credit memo ${cm.memo_number}`);
            closeModal();
            App.navigate('#/invoices');
        } catch (err) { toast(err.message, 'error'); }
    },

    async view(id) {
        const inv = await API.get(`/invoices/${id}`);
        let linesHtml = inv.lines.map(l =>
            `<tr><td>${escapeHtml(l.description || '')}</td><td class="amount">${l.quantity}</td>
             <td class="amount">${formatCurrency(l.rate)}</td><td class="amount">${formatCurrency(l.amount)}</td></tr>`
        ).join('');

        openModal(`${T('Invoice')} #${inv.invoice_number}`, `
            <div style="margin-bottom:12px;">
                <strong>${T('Customer')}:</strong> ${escapeHtml(inv.customer_name || '')}<br>
                <strong>Date:</strong> ${formatDate(inv.date)}<br>
                <strong>Due:</strong> ${formatDate(inv.due_date)}<br>
                <strong>Status:</strong> ${statusBadge(inv.status)}<br>
                ${inv.po_number ? `<strong>PO#:</strong> ${escapeHtml(inv.po_number)}<br>` : ''}
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Description</th><th scope="col" class="amount">Qty</th><th scope="col" class="amount">Rate</th><th scope="col" class="amount">Amount</th></tr></thead>
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
            <div style="margin-top:16px; border-top:1px solid var(--gray-200); padding-top:12px;">
                <h3 style="font-size:13px; margin-bottom:8px;">Attachments</h3>
                <div id="inv-attachments-list" style="margin-bottom:8px; font-size:11px;">Loading...</div>
                <input type="file" id="inv-attach-file" style="font-size:11px;">
                <button class="btn btn-sm btn-secondary" onclick="InvoicesPage.uploadAttachment(${inv.id})" style="margin-left:4px;">Upload</button>
            </div>
            <div class="form-actions">
                <button class="btn btn-secondary" onclick="window.open('/api/invoices/${inv.id}/pdf','_blank')">Save PDF</button>
                <button class="btn btn-secondary" onclick="window.open('/api/invoices/${inv.id}/print-preview','_blank')">Print</button>
                <button class="btn btn-secondary" onclick="InvoicesPage.duplicate(${inv.id})">Duplicate</button>
                <button class="btn btn-secondary" onclick="InvoicesPage.emailInvoice(${inv.id})">Email ${T('Invoice')}</button>
                <button class="btn btn-secondary" onclick="InvoicesPage.copyPaymentLink(${inv.id})">Copy Payment Link</button>
                ${inv.checkout_provider && inv.status !== 'paid' && inv.status !== 'void' ? `<button class="btn btn-secondary" onclick="InvoicesPage.checkPaymentStatus(${inv.id}, '${inv.checkout_provider}')">Check Payment Status</button>` : ''}
                ${inv.status === 'draft' ? `<button class="btn btn-primary" onclick="InvoicesPage.markSent(${inv.id})">Mark Sent</button>` : ''}
                ${inv.status !== 'void' ? `<button class="btn btn-danger" onclick="InvoicesPage.void(${inv.id})">Void ${T('Invoice')}</button>` : ''}
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
        InvoicesPage.loadAttachments('invoice', inv.id);
    },

    async void(id) {
        if (!confirm('Void this invoice? This cannot be undone.')) return;
        try {
            await API.post(`/invoices/${id}/void`);
            toast(`${T('Invoice')} voided`);
            closeModal();
            App.navigate(location.hash);
        } catch (err) { toast(err.message, 'error'); }
    },

    async markSent(id) {
        try {
            await API.post(`/invoices/${id}/send`);
            toast(`${T('Invoice')} marked as sent`);
            closeModal();
            App.navigate(location.hash);
        } catch (err) { toast(err.message, 'error'); }
    },

    async duplicate(id) {
        try {
            const inv = await API.post(`/invoices/${id}/duplicate`);
            toast(`Duplicated as ${T('Invoice')} #${inv.invoice_number}`);
            closeModal();
            App.navigate('#/invoices');
        } catch (err) { toast(err.message, 'error'); }
    },

    async copyPaymentLink(id) {
        try {
            const data = await API.get(`/payments/payment-link/${id}`);
            await navigator.clipboard.writeText(data.url);
            toast('Payment link copied to clipboard');
        } catch (err) { toast(err.message, 'error'); }
    },

    // Desktop-mode fallback: webhooks can't reach 127.0.0.1, so poll the
    // provider for the invoice's last checkout and record it if captured.
    async checkPaymentStatus(id, provider) {
        try {
            const data = await API.post(`/payments/${provider || 'stripe'}/check-status/${id}`);
            if (data.status === 'payment_recorded') {
                toast('Payment received and recorded');
                closeModal();
                App.navigate('#/invoices');
            } else if (data.status === 'already_processed') {
                toast('Payment was already recorded');
            } else if (data.status === 'no_checkout') {
                toast('No checkout has been started for this invoice');
            } else {
                toast(`Not paid yet (provider status: ${data.provider_status || 'unknown'})`);
            }
        } catch (err) { toast(err.message, 'error'); }
    },

    async emailInvoice(id) {
        try {
            const preview = await API.post(`/invoices/${id}/email-preview`, {});
            openModal(Terms.text('Email Invoice'), `
                <form onsubmit="InvoicesPage.sendEmail(event, ${id})">
                    <div class="form-grid">
                        <div class="form-group full-width"><label>Recipient Email *</label>
                            <input name="recipient" type="email" required value="${escapeHtml(preview.recipient || '')}"></div>
                        <div class="form-group full-width"><label>Subject</label>
                            <input name="subject" value="${escapeHtml(preview.subject)}"></div>
                    </div>
                    <div class="form-group full-width"><label>${Terms.text('Message (optional)')}</label>
                        <textarea name="message" rows="3" placeholder="${Terms.text('Added as the first paragraph of the email.')}"></textarea></div>
                    <p>${Terms.text('Below is your saved invoice email template, which follows the message above.')}</p>
                    <iframe id="invoice-email-preview" sandbox="" title="${T('Invoice')} email preview" style="width:100%;height:330px;border:1px solid #ddd;background:white;"></iframe>
                    <p><a href="${escapeHtml(preview.pdf_url)}" target="_blank" rel="noopener">${Terms.text('Preview attached invoice PDF')}</a></p>
                    <div class="form-actions">
                        <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                        <button type="submit" class="btn btn-primary">Send Email</button>
                    </div>
                </form>`);
            const frame = $('#invoice-email-preview');
            if (frame) frame.srcdoc = preview.html_body;
        } catch (err) { toast(`Could not build the email preview: ${err.message}`, 'error'); }
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
            toast(`${T('Invoice')} emailed`);
            closeModal();
        } catch (err) { toast(err.message, 'error'); }
    },

    lineCount: 0,
    _customers: [],

    async showForm(id = null, prefillCustomerId = null) {
        const [customers, items, settings] = await Promise.all([
            API.get('/customers?active_only=true'),
            API.get('/items?active_only=true'),
            API.get('/settings'),
        ]);


        let inv = {
            customer_id: prefillCustomerId || '',
            date: todayISO(),
            terms: settings.default_terms || 'Net 30',
            po_number: '',
            tax_rate: (parseFloat(settings.default_tax_rate || '0') || 0) / 100,
            notes: settings.invoice_notes || '',
            lines: [],
        };
        if (id) inv = await API.get(`/invoices/${id}`);
        const classGroup = await classFormGroupHtml(inv.class_id);
        const jobGroup = await jobFormGroupHtml(inv.job_id, 'inv-customer-select');
        // Nonprofit: a pledge prints as one; program fees and rentals stay invoices
        const pledgeGroup = Terms.isNonprofit() ? `<div class="form-group"><label>Document</label><label style="font-weight:normal;"><input type="checkbox" name="is_pledge" ${(id ? inv.is_pledge : true) ? 'checked' : ''}> This is a pledge (prints as PLEDGE)</label></div>` : '';
        if (inv.lines.length === 0) inv.lines = [{ item_id: '', description: '', quantity: 1, rate: 0 }];

        InvoicesPage.lineCount = inv.lines.length;
        InvoicesPage._items = items;
        InvoicesPage._customers = customers;

        const custOpts = customers.map(c => `<option value="${c.id}" ${inv.customer_id==c.id?'selected':''}>${escapeHtml(c.name)}</option>`).join('');

        openModal(Terms.text(id ? 'Edit Invoice' : 'New Invoice'), `
            <form id="invoice-form" onsubmit="InvoicesPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>${T('Customer')} *</label>
                        <select name="customer_id" id="inv-customer-select" required onchange="InvoicesPage.customerSelected(this.value)"><option value="">Select...</option><option value="__new__">+ ${T('New Customer')}</option>${custOpts}</select>
                        <div id="inv-new-customer-form" style="display:none; margin-top:8px; padding:8px; border:1px solid var(--gray-300); border-radius:4px; background:var(--primary-light);">
                            <div style="font-weight:700; font-size:11px; margin-bottom:6px;">Quick Add ${T('Customer')}</div>
                            <input id="inv-new-cust-name" placeholder="Name *" style="width:100%; margin-bottom:4px; padding:4px 8px; border:1px solid var(--gray-300); border-radius:4px;">
                            <input id="inv-new-cust-email" placeholder="Email" style="width:100%; margin-bottom:4px; padding:4px 8px; border:1px solid var(--gray-300); border-radius:4px;">
                            <input id="inv-new-cust-phone" placeholder="Phone" style="width:100%; margin-bottom:4px; padding:4px 8px; border:1px solid var(--gray-300); border-radius:4px;">
                            <div style="display:flex; gap:6px;">
                                <button type="button" class="btn btn-sm btn-primary" onclick="InvoicesPage.saveNewCustomer()">Save</button>
                                <button type="button" class="btn btn-sm btn-secondary" onclick="InvoicesPage.cancelNewCustomer()">Cancel</button>
                            </div>
                        </div></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${inv.date}"
                            onchange="InvoicesPage._recomputeDueDate()"></div>
                    <div class="form-group"><label>Terms</label>
                        <select name="terms" id="invoice-terms"
                            onchange="InvoicesPage._recomputeDueDate()">
                            ${['Net 15','Net 30','Net 45','Net 60','Due on Receipt'].map(t =>
                                `<option value="${t}" ${inv.terms===t?'selected':''}>${t}</option>`).join('')}
                        </select></div>
                    <div class="form-group"><label>Due Date</label>
                        <input name="due_date" type="date" value="${inv.due_date || ''}"
                            title="Auto-calculated from Date + Terms. Edit to override."></div>
                    <div class="form-group"><label>PO #</label>
                        <input name="po_number" value="${escapeHtml(inv.po_number || '')}"></div>
                    ${classGroup}${jobGroup}${pledgeGroup}
                    ${currencyFormGroupsHtml(inv.currency, inv.exchange_rate)}
                    <div class="form-group"><label>Tax Rate (%)</label>
                        <input name="tax_rate" type="number" step="0.01" value="${(inv.tax_rate * 100) || 0}"
                            oninput="InvoicesPage.recalc()"></div>
                </div>
                <h3 style="margin:16px 0 8px; font-size:14px; color:var(--gray-600);">Line Items</h3>
                <table class="line-items-table">
                    <thead><tr>
                        <th scope="col">Item</th><th scope="col">Description</th><th scope="col" class="col-qty">Qty</th>
                        <th scope="col" class="col-rate">Rate</th><th scope="col" title="Sales tax applies to this line">Tax</th><th scope="col" class="col-amount">Amount</th><th scope="col" class="col-actions"></th>
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
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Create'} ${T('Invoice')}</button>
                </div>
            </form>`);
        if (!id && inv.customer_id) InvoicesPage.customerSelected(inv.customer_id);
        InvoicesPage.recalc();
        // Populate due_date for fresh invoices that don't already have one.
        if (!inv.due_date) InvoicesPage._recomputeDueDate();
    },

    customerSelected(customerId) {
        if (customerId === '__new__') {
            const form = $('#inv-new-customer-form');
            if (form) form.style.display = 'block';
            return;
        }
        const ncf = $('#inv-new-customer-form');
        if (ncf) ncf.style.display = 'none';
        const customer = InvoicesPage._customers.find(c => c.id == customerId);
        const termsField = $('#invoice-terms');
        if (customer && termsField && customer.terms) {
            termsField.value = customer.terms;
            // Setting .value programmatically does NOT fire 'change', so the
            // due-date wouldn't recompute on its own — leaving the form with
            // (e.g.) Net 60 terms but a Net 30 due date. Recompute explicitly.
            InvoicesPage._recomputeDueDate();
        }
    },

    async saveNewCustomer() {
        const name = $('#inv-new-cust-name').value.trim();
        if (!name) { toast(`${T('Customer')} name is required`, 'error'); return; }
        try {
            const cust = await API.post('/customers', {
                name, email: $('#inv-new-cust-email').value.trim() || null,
                phone: $('#inv-new-cust-phone').value.trim() || null,
            });
            InvoicesPage._customers.push(cust);
            const sel = $('#inv-customer-select');
            const opt = document.createElement('option');
            opt.value = cust.id; opt.textContent = cust.name; opt.selected = true;
            sel.appendChild(opt);
            $('#inv-new-customer-form').style.display = 'none';
            toast(`${T('Customer')} "${cust.name}" created`);
        } catch (err) { toast(err.message, 'error'); }
    },

    cancelNewCustomer() {
        $('#inv-new-customer-form').style.display = 'none';
        $('#inv-customer-select').value = '';
    },

    lineRowHtml(idx, line, items) {
        const itemOpts = items.map(i => `<option value="${i.id}" ${line.item_id==i.id?'selected':''}>${escapeHtml(i.name)}</option>`).join('');
        return `<tr data-line="${idx}">
            <td><select class="line-item" onchange="InvoicesPage.itemSelected(${idx})">
                <option value="">--</option>${itemOpts}</select></td>
            <td><input class="line-desc" value="${escapeHtml(line.description || '')}"></td>
            <td><input class="line-qty" type="number" step="0.01" value="${line.quantity || 1}" oninput="InvoicesPage.recalc()"></td>
            <td><input class="line-rate" type="number" step="0.01" value="${line.rate || 0}" oninput="InvoicesPage.recalc()"></td>
            <td style="text-align:center"><input type="checkbox" class="line-taxable" title="Sales tax applies to this line" ${line.is_taxable === false ? '' : 'checked'} onchange="InvoicesPage.recalc()"></td>
            <td class="col-amount line-amount">${formatCurrency((line.quantity||1) * (line.rate||0))}</td>
            <td><button type="button" class="btn btn-sm btn-danger" aria-label="Remove line" onclick="InvoicesPage.removeLine(${idx})">X</button></td>
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
            const tax = row.querySelector('.line-taxable');
            if (tax) tax.checked = item.is_taxable !== false;
            InvoicesPage.recalc();
        }
    },

    recalc() {
        let subtotal = 0, taxable = 0;
        $$('#inv-lines tr').forEach(row => {
            const qty = parseFloat(row.querySelector('.line-qty')?.value) || 0;
            const rate = parseFloat(row.querySelector('.line-rate')?.value) || 0;
            const amount = qty * rate;
            subtotal += amount;
            if (row.querySelector('.line-taxable')?.checked !== false) taxable += amount;
            const amountCell = row.querySelector('.line-amount');
            if (amountCell) amountCell.textContent = formatCurrency(amount);
        });
        const taxPct = parseFloat($('[name="tax_rate"]')?.value) || 0;
        const tax = taxable * (taxPct / 100);
        $('#inv-subtotal').textContent = formatCurrency(subtotal);
        $('#inv-tax').textContent = formatCurrency(tax);
        $('#inv-total').textContent = formatCurrency(subtotal + tax);
    },

    // Auto-fill due_date from date + terms when either changes. Backend
    // does the same calc server-side if due_date arrives null, but
    // showing it inline tells the user "yes this is what we mean by
    // Net 30" before they hit Save.
    _recomputeDueDate() {
        // Scope to the invoice form — a backced report page or another modal
        // could also have a [name="date"] input, and a bare document-level
        // query would grab whichever appears first in the DOM.
        const form = $('#invoice-form');
        if (!form) return;
        const dateEl = form.querySelector('[name="date"]');
        const termsEl = form.querySelector('[name="terms"]');
        const dueDateEl = form.querySelector('[name="due_date"]');
        if (!dateEl || !termsEl || !dueDateEl || !dateEl.value) return;
        const daysMap = {
            'Net 15': 15, 'Net 30': 30, 'Net 45': 45, 'Net 60': 60,
            'Due on Receipt': 0,
        };
        const days = daysMap[termsEl.value];
        if (days === undefined) return;
        // Parse YYYY-MM-DD as local date (not UTC) to avoid DST shifts.
        const [y, m, d] = dateEl.value.split('-').map(Number);
        const dt = new Date(y, m - 1, d);
        dt.setDate(dt.getDate() + days);
        const pad = n => String(n).padStart(2, '0');
        dueDateEl.value = `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}`;
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
                is_taxable: row.querySelector('.line-taxable') ? row.querySelector('.line-taxable').checked : null,
                rate: parseFloat(row.querySelector('.line-rate')?.value) || 0,
                line_order: i,
            });
        });

        const data = {
            customer_id: parseInt(form.customer_id.value),
            date: form.date.value,
            due_date: form.due_date.value || null,
            terms: form.terms.value,
            po_number: form.po_number.value || null,
            is_pledge: form.is_pledge ? form.is_pledge.checked : false,
            class_id: classIdFromForm(form),
            job_id: jobIdFromForm(form),
            ...currencyPayloadFromForm(form),
            tax_rate: (parseFloat(form.tax_rate.value) || 0) / 100,
            notes: form.notes.value || null,
            lines,
        };

        try {
            if (id) { await API.put(`/invoices/${id}`, data); toast(Terms.text('Invoice updated')); }
            else { await API.post('/invoices', data); toast(Terms.text('Invoice created')); }
            closeModal();
            App.navigate(location.hash);
        } catch (err) { toast(err.message, 'error'); }
    },

    async loadAttachments(entityType, entityId) {
        const el = $('#inv-attachments-list');
        if (!el) return;
        try {
            const attachments = await API.get(`/attachments/${entityType}/${entityId}`);
            if (attachments.length === 0) {
                el.innerHTML = '<span style="color:var(--text-muted);">No attachments</span>';
            } else {
                el.innerHTML = attachments.map(a =>
                    `<div style="display:flex; align-items:center; gap:8px; padding:2px 0;">
                        <a href="/api/attachments/download/${a.id}" target="_blank">${escapeHtml(a.filename)}</a>
                        <span style="color:var(--gray-400);">(${(a.file_size/1024).toFixed(1)} KB)</span>
                        <button aria-label="Delete attachment" class="btn btn-sm btn-danger" onclick="InvoicesPage.deleteAttachment(${a.id},'${entityType}',${entityId})" style="padding:0 4px; font-size:10px;">X</button>
                    </div>`
                ).join('');
            }
        } catch (e) { el.innerHTML = ''; }
    },

    async uploadAttachment(entityId) {
        const fileInput = $('#inv-attach-file');
        if (!fileInput?.files[0]) { toast('Select a file first', 'error'); return; }
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        try {
            const resp = await fetch(`/api/attachments/invoice/${entityId}`, { method: 'POST', body: formData });
            if (!resp.ok) { const d = await resp.json(); throw new Error(d.detail || 'Upload failed'); }
            toast('Attachment uploaded');
            fileInput.value = '';
            InvoicesPage.loadAttachments('invoice', entityId);
        } catch (err) { toast(err.message, 'error'); }
    },

    async deleteAttachment(attachId, entityType, entityId) {
        if (!confirm('Delete this attachment?')) return;
        try {
            await API.del(`/attachments/${attachId}`);
            toast('Attachment deleted');
            InvoicesPage.loadAttachments(entityType, entityId);
        } catch (err) { toast(err.message, 'error'); }
    },
};

// Top-level const creates no window property — the topbar's
// data-action dispatch (bootstrap.js callByPath) needs this export.
window.InvoicesPage = InvoicesPage;
