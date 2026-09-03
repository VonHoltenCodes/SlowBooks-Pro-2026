/**
 * Enter Sales Receipts — invoice + payment in one screen, for
 * point-of-sale style transactions where payment happens at the sale.
 * The list shows only receipts (invoices flagged is_sales_receipt);
 * regular invoices keep their own page.
 */
const SalesReceiptsPage = {
    async render() {
        const receipts = await API.get('/invoices?is_sales_receipt=true');
        return renderListPage({
            title: 'Sales Receipts',
            headerHtml: `<button class="btn btn-primary" onclick="SalesReceiptsPage.showForm()">+ New Sales Receipt</button>`,
            filter: {
                id: 'sr-status-filter',
                rowSelector: '.sr-row',
                options: [['paid', 'Paid'], ['void', 'Void']],
            },
            empty: `<p>No sales receipts yet. Use them for point-of-sale style sales where the customer pays on the spot.</p>
                <button class="btn btn-primary" onclick="SalesReceiptsPage.showForm()" style="margin-top:10px;">+ Enter your first sales receipt</button>`,
            columns: ['Sale #', 'Customer', 'Date', 'Status',
                { label: 'Total', cls: 'amount' }, 'Actions'],
            items: receipts,
            row: sr => `<tr class="sr-row" data-status="${sr.status}">
                    <td><strong>${escapeHtml(sr.invoice_number)}</strong></td>
                    <td>${escapeHtml(sr.customer_name || '')}</td>
                    <td>${formatDate(sr.date)}</td>
                    <td>${statusBadge(sr.status)}</td>
                    <td class="amount">${formatCurrency(sr.total)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="SalesReceiptsPage.view(${sr.id})">View</button>
                    </td>
                </tr>`,
        });
    },

    async view(id) {
        const sr = await API.get(`/invoices/${id}`);
        const linesHtml = sr.lines.map(l =>
            `<tr><td>${escapeHtml(l.description || '')}</td><td class="amount">${l.quantity}</td>
             <td class="amount">${formatCurrency(l.rate)}</td><td class="amount">${formatCurrency(l.amount)}</td></tr>`
        ).join('');

        const payment = await SalesReceiptsPage._findPayment(sr);

        openModal(`Sales Receipt #${sr.invoice_number}`, `
            <div style="margin-bottom:12px;">
                <strong>Customer:</strong> ${escapeHtml(sr.customer_name || '')}<br>
                <strong>Date:</strong> ${formatDate(sr.date)}<br>
                <strong>Status:</strong> ${statusBadge(sr.status)}<br>
                ${payment && payment.method ? `<strong>Payment Method:</strong> ${escapeHtml(payment.method)}<br>` : ''}
                ${payment && payment.check_number ? `<strong>Check #:</strong> ${escapeHtml(payment.check_number)}<br>` : ''}
                ${payment && payment.reference ? `<strong>Reference:</strong> ${escapeHtml(payment.reference)}<br>` : ''}
            </div>
            <div class="table-container"><table>
                <thead><tr><th>Description</th><th class="amount">Qty</th><th class="amount">Rate</th><th class="amount">Amount</th></tr></thead>
                <tbody>${linesHtml}</tbody>
            </table></div>
            <div class="invoice-totals">
                <div class="total-row"><span class="label">Subtotal</span><span class="value">${formatCurrency(sr.subtotal)}</span></div>
                <div class="total-row"><span class="label">Tax</span><span class="value">${formatCurrency(sr.tax_amount)}</span></div>
                <div class="total-row grand-total"><span class="label">Total</span><span class="value">${formatCurrency(sr.total)}</span></div>
            </div>
            ${sr.notes ? `<p style="margin-top:12px;color:var(--gray-500);">${escapeHtml(sr.notes)}</p>` : ''}
            <div class="form-actions">
                <button class="btn btn-secondary" onclick="window.open('/api/invoices/${sr.id}/pdf','_blank')">Save PDF</button>
                <button class="btn btn-secondary" onclick="window.open('/api/invoices/${sr.id}/print-preview','_blank')">Print</button>
                ${sr.status !== 'void' ? `<button class="btn btn-danger" onclick="SalesReceiptsPage.void(${sr.id})">Void Receipt</button>` : ''}
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
    },

    // A sales receipt is an invoice + its payment; the payment is found by
    // its allocation to this invoice.
    async _findPayment(sr) {
        try {
            const payments = await API.get(`/payments?customer_id=${sr.customer_id}`);
            return payments.find(p =>
                !p.is_voided && p.allocations.some(a => a.invoice_id === sr.id)
            ) || null;
        } catch (e) { return null; }
    },

    // Void = void the payment first (restores the invoice balance), then
    // void the invoice — same order the API requires for the two documents.
    async void(id) {
        if (!confirm('Void this sales receipt? Its payment and invoice will both be voided. This cannot be undone.')) return;
        try {
            const sr = await API.get(`/invoices/${id}`);
            const payment = await SalesReceiptsPage._findPayment(sr);
            if (payment) await API.post(`/payments/${payment.id}/void`);
            await API.post(`/invoices/${id}/void`);
            toast('Sales receipt voided');
            closeModal();
            App.navigate(location.hash);
        } catch (err) { toast(err.message, 'error'); }
    },

    lineCount: 0,
    _customers: [],
    _items: [],

    async showForm() {
        const [customers, items, accounts, settings] = await Promise.all([
            API.get('/customers?active_only=true'),
            API.get('/items?active_only=true'),
            API.get('/accounts'),
            API.get('/settings'),
        ]);
        const bankAccts = accounts.filter(a => a.account_type === 'asset');

        const sr = {
            date: todayISO(),
            tax_rate: (parseFloat(settings.default_tax_rate || '0') || 0) / 100,
            lines: [{ item_id: '', description: '', quantity: 1, rate: 0 }],
        };
        const classGroup = await classFormGroupHtml(null);
        const jobGroup = await jobFormGroupHtml(null);

        SalesReceiptsPage.lineCount = sr.lines.length;
        SalesReceiptsPage._items = items;
        SalesReceiptsPage._customers = customers;

        const custOpts = customers.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
        const bankOpts = bankAccts.map(a => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join('');

        openModal('Enter Sales Receipt', `
            <form id="sales-receipt-form" onsubmit="SalesReceiptsPage.save(event)">
                ${ScanHelper.scanRowHtml()}
                <div class="form-grid">
                    <div class="form-group"><label>Customer *</label>
                        <select name="customer_id" id="sr-customer-select" required onchange="SalesReceiptsPage.customerSelected(this.value)"><option value="">Select...</option><option value="__new__">+ New Customer</option>${custOpts}</select>
                        <div id="sr-new-customer-form" style="display:none; margin-top:8px; padding:8px; border:1px solid var(--gray-300); border-radius:4px; background:var(--primary-light);">
                            <div style="font-weight:700; font-size:11px; margin-bottom:6px;">Quick Add Customer</div>
                            <input id="sr-new-cust-name" placeholder="Name *" style="width:100%; margin-bottom:4px; padding:4px 8px; border:1px solid var(--gray-300); border-radius:4px;">
                            <input id="sr-new-cust-email" placeholder="Email" style="width:100%; margin-bottom:4px; padding:4px 8px; border:1px solid var(--gray-300); border-radius:4px;">
                            <input id="sr-new-cust-phone" placeholder="Phone" style="width:100%; margin-bottom:4px; padding:4px 8px; border:1px solid var(--gray-300); border-radius:4px;">
                            <div style="display:flex; gap:6px;">
                                <button type="button" class="btn btn-sm btn-primary" onclick="SalesReceiptsPage.saveNewCustomer()">Save</button>
                                <button type="button" class="btn btn-sm btn-secondary" onclick="SalesReceiptsPage.cancelNewCustomer()">Cancel</button>
                            </div>
                        </div></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${sr.date}"></div>
                    <div class="form-group"><label>Payment Method</label>
                        <select name="method">
                            <option value="">--</option>
                            <option>Cash</option><option>Check</option>
                            <option>Credit Card</option><option>ACH/EFT</option><option>Other</option>
                        </select></div>
                    <div class="form-group"><label>Check #</label>
                        <input name="check_number"></div>
                    <div class="form-group"><label>Reference</label>
                        <input name="reference"></div>
                    <div class="form-group"><label>Deposit To</label>
                        <select name="deposit_to_account_id">
                            <option value="">Undeposited Funds (default)</option>${bankOpts}</select></div>
                    ${classGroup}${jobGroup}
                    ${currencyFormGroupsHtml(null, null)}
                    <div class="form-group"><label>Tax Rate (%)</label>
                        <input name="tax_rate" type="number" step="0.01" value="${(sr.tax_rate * 100) || 0}"
                            oninput="SalesReceiptsPage.recalc()"></div>
                </div>
                <h3 style="margin:16px 0 8px; font-size:14px; color:var(--gray-600);">Line Items</h3>
                <table class="line-items-table">
                    <thead><tr>
                        <th>Item</th><th>Description</th><th class="col-qty">Qty</th>
                        <th class="col-rate">Rate</th><th title="Sales tax applies to this line">Tax</th><th class="col-amount">Amount</th><th class="col-actions"></th>
                    </tr></thead>
                    <tbody id="sr-lines">
                        ${sr.lines.map((l, i) => SalesReceiptsPage.lineRowHtml(i, l, items)).join('')}
                    </tbody>
                </table>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="SalesReceiptsPage.addLine()">+ Add Line</button>
                <div class="invoice-totals" id="sr-totals">
                    <div class="total-row"><span class="label">Subtotal</span><span class="value" id="sr-subtotal">$0.00</span></div>
                    <div class="total-row"><span class="label">Tax</span><span class="value" id="sr-tax">$0.00</span></div>
                    <div class="total-row grand-total"><span class="label">Total Received</span><span class="value" id="sr-total">$0.00</span></div>
                </div>
                <div class="form-group" style="margin-top:12px;"><label>Notes</label>
                    <textarea name="notes"></textarea></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="ScanHelper.discard(); closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Record Sales Receipt</button>
                </div>
            </form>`);
        SalesReceiptsPage.recalc();
        ScanHelper.wire(SalesReceiptsPage._applyScan, SalesReceiptsPage._applyScanField,
            SalesReceiptsPage._scanFieldTarget);
    },

    // Where each canvas field lands — the canvas outlines these inputs in
    // the field's color so the form doubles as the legend. Mirrors
    // _applyScanField below.
    _scanFieldTarget(fieldKey) {
        const form = $('#sales-receipt-form');
        if (!form) return null;
        const row = document.querySelector('#sr-lines tr');
        if (fieldKey === 'date') return form.querySelector('[name="date"]');
        if (fieldKey === 'merchant') {
            const nameInput = $('#sr-new-cust-name');
            if (nameInput && nameInput.offsetParent) return nameInput;  // new-customer form open
            return row && row.querySelector('.line-desc');
        }
        if (fieldKey === 'total' || fieldKey === 'subtotal') return row && row.querySelector('.line-rate');
        if (fieldKey === 'tax') return form.querySelector('[name="tax_rate"]');
        return null;
    },

    // Box-to-fix canvas: apply one re-read field into the form.
    // Returns nothing when the value landed, {error} when it was refused, or
    // {note} when it landed with a remark (the canvas shows either).
    _applyScanField(fieldKey, value, meta) {
        const form = $('#sales-receipt-form');
        if (!form) return;
        const row = document.querySelector('#sr-lines tr');
        if (fieldKey === 'date') {
            form.querySelector('[name="date"]').value = value;
        } else if (fieldKey === 'merchant') {
            const nameInput = $('#sr-new-cust-name');
            if (nameInput) nameInput.value = value;
            const desc = row && row.querySelector('.line-desc');
            if (desc) desc.value = value;
        } else if (fieldKey === 'total' || fieldKey === 'subtotal') {
            if (row) {
                const rate = row.querySelector('.line-rate');
                if (rate) rate.value = parseFloat(value).toFixed(2);
            }
        } else if (fieldKey === 'tax') {
            const rate = row && row.querySelector('.line-rate');
            const sub = rate ? parseFloat(rate.value) : 0;
            const taxInput = form.querySelector('[name="tax_rate"]');
            const r = ScanHelper.taxPercent(value, sub, meta && meta.text);
            if (r.error) { SalesReceiptsPage.recalc(); return { error: r.error }; }
            if (taxInput) taxInput.value = r.pct.toFixed(2);
            SalesReceiptsPage.recalc();
            return { note: `— tax rate set to ${r.pct.toFixed(2)}%.` };
        }
        SalesReceiptsPage.recalc();
    },

    _applyScan(result) {
        const form = $('#sales-receipt-form');
        if (!form) return;
        if (result.date) form.querySelector('[name="date"]').value = result.date;

        const merchant = result.merchant && result.merchant.value;
        const sel = $('#sr-customer-select');
        if (merchant && sel) {
            const match = SalesReceiptsPage._customers.find(c =>
                c.name && c.name.toLowerCase() === merchant.toLowerCase());
            if (match) {
                sel.value = match.id;
            } else {
                const nameInput = $('#sr-new-cust-name');
                if (nameInput) nameInput.value = merchant;
                const statusEl = $('#scan-status');
                if (statusEl) {
                    statusEl.textContent = `Detected: ${merchant} — select from the list or add new.`;
                }
            }
        }

        const total = parseFloat(result.total || '0');
        if (total > 0) {
            const row = document.querySelector('#sr-lines tr');
            if (row) {
                const rateInput = row.querySelector('.line-rate');
                const descInput = row.querySelector('.line-desc');
                if (rateInput) {
                    // Tax-split rule (spec §6.4): line = subtotal, tax rate
                    // populated, so the saved total matches the receipt.
                    if (result.tax_detected && result.subtotal && parseFloat(result.subtotal) > 0) {
                        rateInput.value = parseFloat(result.subtotal).toFixed(2);
                        const taxRate = (parseFloat(result.tax) / parseFloat(result.subtotal)) * 100;
                        const taxInput = form.querySelector('[name="tax_rate"]');
                        if (taxInput) taxInput.value = taxRate.toFixed(2);
                    } else {
                        rateInput.value = total.toFixed(2);
                    }
                }
                if (descInput && merchant) descInput.value = merchant;
                SalesReceiptsPage.recalc();
            }
        }
    },

    customerSelected(customerId) {
        const form = $('#sr-new-customer-form');
        if (!form) return;
        form.style.display = customerId === '__new__' ? 'block' : 'none';
    },

    async saveNewCustomer() {
        const name = $('#sr-new-cust-name').value.trim();
        if (!name) { toast('Customer name is required', 'error'); return; }
        try {
            const cust = await API.post('/customers', {
                name, email: $('#sr-new-cust-email').value.trim() || null,
                phone: $('#sr-new-cust-phone').value.trim() || null,
            });
            SalesReceiptsPage._customers.push(cust);
            const sel = $('#sr-customer-select');
            const opt = document.createElement('option');
            opt.value = cust.id; opt.textContent = cust.name; opt.selected = true;
            sel.appendChild(opt);
            $('#sr-new-customer-form').style.display = 'none';
            toast(`Customer "${cust.name}" created`);
        } catch (err) { toast(err.message, 'error'); }
    },

    cancelNewCustomer() {
        $('#sr-new-customer-form').style.display = 'none';
        $('#sr-customer-select').value = '';
    },

    lineRowHtml(idx, line, items) {
        const itemOpts = items.map(i => `<option value="${i.id}" ${line.item_id==i.id?'selected':''}>${escapeHtml(i.name)}</option>`).join('');
        return `<tr data-line="${idx}">
            <td><select class="line-item" onchange="SalesReceiptsPage.itemSelected(${idx})">
                <option value="">--</option>${itemOpts}</select></td>
            <td><input class="line-desc" value="${escapeHtml(line.description || '')}"></td>
            <td><input class="line-qty" type="number" step="0.01" value="${line.quantity || 1}" oninput="SalesReceiptsPage.recalc()"></td>
            <td><input class="line-rate" type="number" step="0.01" value="${line.rate || 0}" oninput="SalesReceiptsPage.recalc()"></td>
            <td style="text-align:center"><input type="checkbox" class="line-taxable" title="Sales tax applies to this line" ${line.is_taxable === false ? '' : 'checked'} onchange="SalesReceiptsPage.recalc()"></td>
            <td class="col-amount line-amount">${formatCurrency((line.quantity||1) * (line.rate||0))}</td>
            <td><button type="button" class="btn btn-sm btn-danger" onclick="SalesReceiptsPage.removeLine(${idx})">X</button></td>
        </tr>`;
    },

    addLine() {
        const tbody = $('#sr-lines');
        const idx = SalesReceiptsPage.lineCount++;
        tbody.insertAdjacentHTML('beforeend', SalesReceiptsPage.lineRowHtml(idx, {}, SalesReceiptsPage._items));
    },

    removeLine(idx) {
        const row = $(`[data-line="${idx}"]`);
        if (row) row.remove();
        SalesReceiptsPage.recalc();
    },

    itemSelected(idx) {
        const row = $(`[data-line="${idx}"]`);
        const itemId = row.querySelector('.line-item').value;
        const item = SalesReceiptsPage._items.find(i => i.id == itemId);
        if (item) {
            row.querySelector('.line-desc').value = item.description || item.name;
            row.querySelector('.line-rate').value = item.rate;
            const tax = row.querySelector('.line-taxable');
            if (tax) tax.checked = item.is_taxable !== false;
            SalesReceiptsPage.recalc();
        }
    },

    recalc() {
        let subtotal = 0, taxable = 0;
        $$('#sr-lines tr').forEach(row => {
            const qty = parseFloat(row.querySelector('.line-qty')?.value) || 0;
            const rate = parseFloat(row.querySelector('.line-rate')?.value) || 0;
            const amount = qty * rate;
            subtotal += amount;
            if (row.querySelector('.line-taxable')?.checked !== false) taxable += amount;
            const amountCell = row.querySelector('.line-amount');
            if (amountCell) amountCell.textContent = formatCurrency(amount);
        });
        const taxPct = parseFloat($('#sales-receipt-form [name="tax_rate"]')?.value) || 0;
        const tax = taxable * (taxPct / 100);
        $('#sr-subtotal').textContent = formatCurrency(subtotal);
        $('#sr-tax').textContent = formatCurrency(tax);
        $('#sr-total').textContent = formatCurrency(subtotal + tax);
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        $$('#sr-lines tr').forEach((row, i) => {
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
            method: form.method.value || null,
            check_number: form.check_number.value || null,
            reference: form.reference.value || null,
            deposit_to_account_id: form.deposit_to_account_id.value ? parseInt(form.deposit_to_account_id.value) : null,
            class_id: classIdFromForm(form),
            job_id: jobIdFromForm(form),
            ...currencyPayloadFromForm(form),
            tax_rate: (parseFloat(form.tax_rate.value) || 0) / 100,
            notes: form.notes.value || null,
            lines,
        };

        try {
            const result = await API.post('/sales-receipts', data);
            await ScanHelper.attachAfterSave('invoice', result.invoice.id);
            toast(`Sales Receipt #${result.invoice.invoice_number} recorded`);
            closeModal();
            App.navigate(location.hash);
        } catch (err) { toast(err.message, 'error'); }
    },
};

// Top-level const creates no window property — the topbar's
// data-action dispatch (bootstrap.js callByPath) needs this export.
window.SalesReceiptsPage = SalesReceiptsPage;
