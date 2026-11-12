/**
 * Credit Memos — issue credits against customers, apply to invoices
 * Feature 5: Credit memo UI with apply-to-invoice workflow
 */
const CreditMemosPage = {
    showAll() { CreditMemosPage._showAll = true; App.navigate(location.hash); },

    async render() {
        const { rows: memos, note: capNote } = await listRows(CreditMemosPage, '/credit-memos', 'CreditMemosPage.showAll()', 'credit memos');
        return renderListPage({
            title: 'Credit Memos',
            headerHtml: `<button class="btn btn-primary" onclick="CreditMemosPage.showForm()">+ New Credit Memo</button>` + capNote,
            empty: '<p>No credit memos yet</p>',
            columns: ['#', T('Customer'), 'Date', 'Status',
                { label: 'Total', cls: 'amount' }, { label: 'Remaining', cls: 'amount' }, 'Actions'],
            items: memos,
            row: m => `<tr>
                    <td><strong>${escapeHtml(m.memo_number)}</strong></td>
                    <td>${escapeHtml(m.customer_name || '')}</td>
                    <td>${formatDate(m.date)}</td>
                    <td>${statusBadge(m.status)}</td>
                    <td class="amount">${formatCurrency(m.total)}</td>
                    <td class="amount">${formatCurrency(m.balance_remaining)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="CreditMemosPage.view(${m.id})">View</button>
                        ${m.status === 'issued' ? `<button class="btn btn-sm btn-primary" onclick="CreditMemosPage.showApply(${m.id})">Apply</button>` : ''}
                        ${m.status !== 'void' ? `<button data-destructive class="btn btn-sm btn-secondary" onclick="CreditMemosPage.void(${m.id})">Void</button>` : ''}
                    </td>
                </tr>`,
        });
    },

    // A credit memo could be created and applied but never looked at, saved
    // or printed, so it could not be sent to the customer (W-L19).
    async view(id) {
        const cm = await API.get(`/credit-memos/${id}`);
        let forInvoice = '';
        if (cm.original_invoice_id) {
            try {
                const inv = await API.get(`/invoices/${cm.original_invoice_id}`);
                forInvoice = `<strong>For ${T('Invoice')}:</strong> #${escapeHtml(inv.invoice_number)}<br>`;
            } catch (e) { /* shown without it */ }
        }
        const linesHtml = cm.lines.map(l =>
            `<tr><td>${escapeHtml(l.description || '')}</td><td class="amount">${l.quantity}</td>
             <td class="amount">${SalesLines.rate(l.rate)}</td><td class="amount">${formatCurrency(l.amount)}</td></tr>`
        ).join('');
        openModal(`Credit Memo ${cm.memo_number}`, `
            <div style="margin-bottom:12px;">
                <strong>${T('Customer')}:</strong> ${escapeHtml(cm.customer_name || '')}<br>
                <strong>Date:</strong> ${formatDate(cm.date)}<br>
                ${forInvoice}
                <strong>Status:</strong> ${statusBadge(cm.status)}${cm.is_write_off ? ' (write-off)' : ''}${cm.discount_payment_id ? ` (early-payment discount${cm.discount_override ? ', taken against the terms' : ''}; void its payment to undo it)` : ''}
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Description</th><th scope="col" class="amount">Qty</th><th scope="col" class="amount">Rate</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${linesHtml}</tbody>
            </table></div>
            <div class="invoice-totals">
                <div class="total-row"><span class="label">Subtotal</span><span class="value">${formatCurrency(cm.subtotal)}</span></div>
                <div class="total-row"><span class="label">Tax</span><span class="value">${formatCurrency(cm.tax_amount)}</span></div>
                <div class="total-row grand-total"><span class="label">Total Credit</span><span class="value">${formatCurrency(cm.total)}</span></div>
                <div class="total-row"><span class="label">Applied</span><span class="value">${formatCurrency(cm.amount_applied)}</span></div>
                <div class="total-row grand-total"><span class="label">Remaining</span><span class="value">${formatCurrency(cm.balance_remaining)}</span></div>
            </div>
            ${cm.notes ? `<p style="margin-top:12px;color:var(--gray-500);">${escapeHtml(cm.notes)}</p>` : ''}
            ${typeof Replace === 'undefined' ? '' : Replace.links(cm, 'credit_memo', n => `#/credit-memos/${n}`)}
            ${typeof activityPanelHtml === 'undefined' ? '' : activityPanelHtml('credit_memo', cm.id)}
            <div class="form-actions">
                <button class="btn btn-secondary" onclick="window.open('/api/credit-memos/${cm.id}/pdf','_blank')">Save PDF</button>
                <button class="btn btn-secondary" onclick="window.open('/api/credit-memos/${cm.id}/print-preview','_blank')">Print</button>
                ${cm.status === 'issued' ? `<button class="btn btn-primary" onclick="CreditMemosPage.showApply(${cm.id})">Apply</button>` : ''}
                ${cm.status !== 'void' ? `<button data-destructive class="btn btn-danger" onclick="CreditMemosPage.void(${cm.id})">Void</button>` : ''}
                <button class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
        // the document's own address, so Back returns to the list and the Open group lists it (NEW-164)
        if (App.documentAddress) App.documentAddress(`#/credit-memos/${Number(id)}`);
    },

    async void(id) {
        if (!confirm('Void this credit memo? Any applied credit goes back onto the invoice and a reversing entry is posted.')) return;
        try {
            await API.post(`/credit-memos/${id}/void`, {});
            toast('Credit memo voided');
            closeModal();
            App.navigate('#/credit-memos');
        } catch (err) { toast(err.message, 'error'); }
    },

    _items: [],
    _customers: [],
    _invoices: [],
    lineCount: 0,

    async showForm() {
        const [customers, items, settings] = await Promise.all([
            API.get('/customers?active_only=true&tree=1'),
            API.get('/items?active_only=true'),
            API.get('/settings'),
        ]);
        CreditMemosPage._items = items;
        CreditMemosPage._customers = customers;
        CreditMemosPage._invoices = [];
        CreditMemosPage.lineCount = 1;
        const classGroup = await classFormGroupHtml();
        // the line Class cell (#640), drawn as the invoice form draws it
        if (CreditMemosPage.lineClasses()) await Nonprofit.loadFunds();
        // The company's rate, as on a new invoice; picking the invoice being
        // credited switches to that invoice's rate. It defaulted to 0%, so
        // returned taxable goods were credited without their tax (W-L14, F14).
        // A rate keeps four places of a percent (8.875).
        const defaultPct = +(parseFloat(settings.default_tax_rate || '0') || 0).toFixed(4);

        const custOpts = customers.map(c => `<option value="${c.id}">${escapeHtml(customerLabel(c))}</option>`).join('');

        openModal('New Credit Memo', `
            <form id="cm-form" onsubmit="CreditMemosPage.save(event)">
                <div class="form-grid">
                    <div class="form-group"><label>${T('Customer')} *</label>
                        <select name="customer_id" id="cm-customer-select" required onchange="CreditMemosPage.customerSelected(this.value)"><option value="">Select...</option>${custOpts}</select></div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>For ${T('Invoice')}</label>
                        <select name="original_invoice_id" id="cm-invoice-select" onchange="CreditMemosPage.invoiceSelected(this.value)"><option value="">None</option></select></div>
                    <div class="form-group"><label>Tax Rate (%)</label>
                        <input name="tax_rate" type="number" step="0.0001" value="${defaultPct}" oninput="CreditMemosPage.recalc()"></div>
                    ${classGroup}
                </div>
                <h3 style="margin:12px 0 8px;font-size:14px;">Credit Lines</h3>
                <table class="line-items-table">
                    <thead><tr><th scope="col">Item</th><th scope="col">Description</th>${CreditMemosPage.lineClasses() ? Nonprofit.classHeadHtml() : ''}<th scope="col" class="col-qty">Qty</th><th scope="col" class="col-rate">Rate</th><th scope="col" title="Sales tax applies to this line">Tax</th><th scope="col" class="col-amount">Amount</th><th scope="col" class="col-actions"></th></tr></thead>
                    <tbody id="cm-lines">${CreditMemosPage.lineRowHtml(0)}</tbody>
                </table>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="CreditMemosPage.addLine()">+ Add Line</button>
                <div class="invoice-totals" id="cm-totals">
                    <div class="total-row"><span class="label">Subtotal</span><span class="value" id="cm-subtotal">$0.00</span></div>
                    <div class="total-row"><span class="label">Tax</span><span class="value" id="cm-tax">$0.00</span></div>
                    <div class="total-row grand-total"><span class="label">Total Credit</span><span class="value" id="cm-total">$0.00</span></div>
                </div>
                <div class="form-group" style="margin-top:12px;"><label>Notes</label>
                    <textarea name="notes"></textarea></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create Credit Memo</button>
                </div>
            </form>`);
        typeof LineOrder !== 'undefined' && LineOrder.attach('#cm-lines', { add: () => CreditMemosPage.addLine(), changed: () => CreditMemosPage.recalc() });
        CreditMemosPage.recalc();
    },

    // The line Class cell is the shared helper in utils.js (#243), drawn here
    // as the invoice form draws it (#640): once the company has a class of
    // its own, and always for a nonprofit's funds. The form still renders
    // where that helper is not loaded (the node probes build a bare page
    // from this file alone).
    lineClasses() { return typeof Nonprofit !== 'undefined' && !!Nonprofit.classCellHtml; },
    // A line's class for the payload: the cell's choice, or none ("Same as
    // header"), and the posting takes the memo's class, as an invoice line's
    // does. The form has no job picker, so a line's job is set over the API.
    lineClassFromRow(row) {
        return row.querySelector('.line-class-fund') ? Nonprofit.fundFromRow(row, 'line-class') : null;
    },

    // One credit line, priced from the item the way an invoice line is.
    lineRowHtml(idx) {
        return `<tr data-cmline="${idx}">
                <td><select class="line-item" onchange="CreditMemosPage.itemSelected(${idx})">${ItemQuickAdd.options(CreditMemosPage._items)}</select></td>
                <td><input class="line-desc"></td>
                ${CreditMemosPage.lineClasses() ? Nonprofit.classCellHtml('line-class', null) : ''}
                <td><input class="line-qty" type="number" step="0.0001" value="1" oninput="CreditMemosPage.recalc()"></td>
                <td><input class="line-rate money" type="number" step="0.0001" min="0" value="0" oninput="CreditMemosPage.recalc()"></td>
                <td style="text-align:center"><input type="checkbox" class="line-taxable" title="Sales tax applies to this line" ${TaxDefault.checked()} onchange="CreditMemosPage.recalc()"></td>
                <td class="col-amount line-amount">$0.00</td>
                <td><button type="button" class="btn btn-sm btn-danger" aria-label="Remove line" onclick="CreditMemosPage.removeLine(${idx})">X</button></td>
            </tr>`;
    },

    addLine() {
        const idx = CreditMemosPage.lineCount++;
        $('#cm-lines').insertAdjacentHTML('beforeend', CreditMemosPage.lineRowHtml(idx));
        CreditMemosPage.recalc();
    },

    removeLine(idx) {
        const row = $(`[data-cmline="${idx}"]`);
        if (row) row.remove();
        CreditMemosPage.recalc();
    },

    itemSelected(idx) {
        const row = $(`[data-cmline="${idx}"]`);
        if (row && ItemQuickAdd.selected(row.querySelector('.line-item'), CreditMemosPage._items, () => CreditMemosPage.itemSelected(idx))) return;
        if (row && SalesLines.fillFromItem(row, CreditMemosPage._items)) CreditMemosPage.recalc();
    },

    // The customer's invoices, for "For Invoice" (optional).
    async customerSelected(customerId) {
        CreditMemosPage._invoices = [];
        const sel = $('#cm-invoice-select');
        if (sel) sel.innerHTML = '<option value="">None</option>';
        CreditMemosPage.recalc();
        if (!customerId) return;
        try {
            // the customer's open invoices and those of sub-customers billed
            // with it (#638): a credit is routed the way a payment is
            const invoices = await fetchAllPages(`/invoices?customer_id=${encodeURIComponent(customerId)}&open_only=true&billed_to=true`);
            CreditMemosPage._invoices = invoices.filter(i => i.status !== 'void');
        } catch (e) { return; }
        if (!sel || $('#cm-customer-select')?.value !== String(customerId)) return;
        sel.innerHTML = '<option value="">None</option>' + CreditMemosPage._invoices.map(i =>
            `<option value="${i.id}">#${escapeHtml(i.invoice_number)} · ${formatDate(i.date)} · ${SalesLines.money(i.total, i.currency)}</option>`).join('');
    },

    // Crediting a particular invoice puts its tax back at the rate it was
    // charged, as the invoice shows it (8.875, not 8.88).
    invoiceSelected(invoiceId) {
        const inv = CreditMemosPage._invoices.find(i => String(i.id) === String(invoiceId));
        const rate = $('#cm-form [name="tax_rate"]');
        if (inv && rate) rate.value = +((inv.tax_rate || 0) * 100).toFixed(4);
        CreditMemosPage.recalc();
    },

    recalc() {
        TaxExempt.enforce(CreditMemosPage._customers, $('#cm-customer-select')?.value, $('#cm-lines'));
        const t = SalesLines.totals($('#cm-lines'), $('#cm-form [name="tax_rate"]')?.value);
        SalesLines.show(t, ['cm-subtotal', 'cm-tax', 'cm-total']);
        return t;
    },

    // Edit from a report line (#689): the form filled in with the memo; Save
    // voids it (its applications given back to their invoices) and posts
    // the new one in its place.
    async replace(id) {
        const cm = await API.get(`/credit-memos/${id}`);
        if (cm.status === 'void') { toast('This credit memo is void; there is nothing to change.', 'error'); return; }
        await CreditMemosPage.showForm();
        const form = $('#cm-form');
        if (!form) return;
        Replace.set(form, 'customer_id', cm.customer_id);
        await CreditMemosPage.customerSelected(cm.customer_id);
        const inv = $('#cm-invoice-select');
        if (inv && cm.original_invoice_id) {
            if (![...inv.options].some(o => o.value === String(cm.original_invoice_id))) {
                inv.insertAdjacentHTML('beforeend', `<option value="${Number(cm.original_invoice_id)}">${T('Invoice')} #${Number(cm.original_invoice_id)}</option>`);
            }
            inv.value = String(cm.original_invoice_id);
        }
        Replace.set(form, 'date', cm.date);
        Replace.set(form, 'tax_rate', +((parseFloat(cm.tax_rate) || 0) * 100).toFixed(4));
        Replace.set(form, 'class_id', cm.class_id || '');
        Replace.set(form, 'notes', cm.notes || '');
        $('#cm-lines').innerHTML = '';
        CreditMemosPage.lineCount = 0;
        const taxed = parseFloat(cm.tax_amount) > 0;
        (cm.lines || []).forEach(l => {
            const idx = CreditMemosPage.lineCount;
            CreditMemosPage.addLine();
            const row = $(`[data-cmline="${idx}"]`);
            if (!row) return;
            if (l.item_id) row.querySelector('.line-item').value = String(l.item_id);
            row.querySelector('.line-desc').value = l.description || '';
            row.querySelector('.line-qty').value = Number(l.quantity);
            row.querySelector('.line-rate').value = Number(l.rate);
            const tax = row.querySelector('.line-taxable');
            if (tax) tax.checked = taxed;
            const cls = row.querySelector('select.line-class');
            if (cls && l.class_id) cls.value = String(l.class_id);
        });
        if (!(cm.lines || []).length) CreditMemosPage.addLine();
        CreditMemosPage.recalc();
        Replace.mark(form, 'credit_memo', id);
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        const lines = [];
        $$('#cm-lines tr').forEach((row, i) => {
            lines.push({
                item_id: ItemQuickAdd.itemId(row.querySelector('.line-item')),
                description: row.querySelector('.line-desc')?.value || '',
                quantity: parseFloat(row.querySelector('.line-qty')?.value) || 1,
                rate: parseFloat(row.querySelector('.line-rate')?.value) || 0,
                is_taxable: row.querySelector('.line-taxable') ? row.querySelector('.line-taxable').checked : null,
                class_id: CreditMemosPage.lineClassFromRow(row),
                line_order: i,
            });
        });
        const data = {
            customer_id: parseInt(form.customer_id.value),
            original_invoice_id: form.original_invoice_id.value ? parseInt(form.original_invoice_id.value) : null,
            date: form.date.value,
            tax_rate: (parseFloat(form.tax_rate.value) || 0) / 100,
            notes: form.notes.value || null,
            class_id: classIdFromForm(form),
            lines,
        };
        if (!(await TxnWarn.ok(form))) return;
        if (!(await ClassWarn.ok(form, { rows: '#cm-lines tr', cls: 'line-class' }))) return;
        try {
            // $0.00 asks "Save it anyway?" first (SalesLines.sendAllowingZero)
            const saved = await SalesLines.sendAllowingZero(flags =>
                API.post('/credit-memos' + (typeof Replace === 'undefined' ? '' : Replace.suffix(form)), { ...data, ...flags }));
            if (!saved) return;
            if ((typeof Replace !== 'undefined' && Replace.replacing(form))) toastPosted(`Credit memo voided and replaced by credit memo ${saved.memo_number || `#${saved.id}`}`);
            else toastPosted('Credit memo created');
            closeModal();
            App.navigate('#/credit-memos');
        } catch (err) { toast(err.message, 'error'); }
    },

    async showApply(cmId) {
        const cm = await API.get(`/credit-memos/${cmId}`);
        const invoices = await fetchAllPages(`/invoices?customer_id=${cm.customer_id}&open_only=true&billed_to=true`);
        const openInv = invoices.filter(i => i.status !== 'void' && i.status !== 'paid' && i.balance_due > 0);

        let rows = openInv.map(inv => `
            <tr>
                <td>${escapeHtml(inv.invoice_number)}</td>
                <td class="amount">${formatCurrency(inv.balance_due)}</td>
                <td><input type="number" step="0.01" class="apply-amt money" data-inv="${inv.id}" data-label="${escapeHtml(inv.invoice_number)}" data-balance="${inv.balance_due}" value="0" style="width:80px;"></td>
            </tr>`).join('');
        if (!rows) rows = '<tr><td colspan="3">No open invoices for this customer</td></tr>';

        openModal(`Apply Credit ${cm.memo_number}`, `
            <p style="margin-bottom:8px;">Credit remaining: <strong id="cm-apply-remaining" data-remaining="${cm.balance_remaining}">${formatCurrency(cm.balance_remaining)}</strong></p>
            <div class="table-container"><table>
                <thead><tr><th scope="col">${T('Invoice')}</th><th scope="col" class="amount">Balance</th><th scope="col" class="amount">Apply</th></tr></thead>
                <tbody>${rows}</tbody>
            </table></div>
            <div class="form-actions">
                <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                <button class="btn btn-primary" onclick="CreditMemosPage.doApply(${cmId})">Apply Credit</button>
            </div>`);
    },

    // Every row is checked before anything is sent, so a bad row does not
    // leave the rows above it applied behind a dialog that still shows the
    // old remaining (2.31.11 gate, NEW-217: a second Apply then applied them
    // again). A refusal from the server mid-way says what was applied before
    // it and opens the dialog again on what remains now.
    async doApply(cmId) {
        const rows = CreditApply.rows($$('.apply-amt'), $('#cm-apply-remaining'), T('Invoice'));
        if (!rows) return;
        const applied = [];
        for (const row of rows) {
            try {
                await API.post(`/credit-memos/${cmId}/apply`, { invoice_id: row.id, amount: row.amount });
                applied.push(row);
            } catch (err) {
                toast(CreditApply.failed(row, err, applied, T('invoice')), 'error');
                await CreditMemosPage.showApply(cmId);
                return;
            }
        }
        toast('Credit applied');
        closeModal();
        App.navigate('#/credit-memos');
    },
};

// What the two Apply Credit dialogs (a credit memo's, a vendor credit's)
// share: the rows read and checked before a request goes out, and the
// sentence when the server refuses one part-way (NEW-217).
const CreditApply = {
    // The rows with an amount: [{id, amount, label}], or null after a toast
    // when a row is not in whole cents, is more than its document's balance,
    // the rows together are more than the credit remaining, or nothing was
    // entered. Nothing is sent in those cases.
    rows(inputs, remainingEl, noun) {
        const rows = [];
        for (const input of inputs) {
            const raw = String(input.value || '').trim();
            const amt = parseFloat(raw) || 0;
            if (amt <= 0) continue;
            const label = input.dataset.label || input.dataset.inv || input.dataset.bill || '';
            if (Math.abs(Math.round(amt * 100) - amt * 100) > 1e-6) {
                toast(`${noun} ${label}: an amount is in whole cents; ${raw} isn't. Nothing was applied.`, 'error');
                return null;
            }
            const balance = parseFloat(input.dataset.balance);
            if (!isNaN(balance) && amt > balance + 1e-9) {
                toast(`${noun} ${label}: ${formatCurrency(amt)} is more than its ${formatCurrency(balance)} balance. Nothing was applied.`, 'error');
                return null;
            }
            rows.push({ id: parseInt(input.dataset.inv || input.dataset.bill), amount: Math.round(amt * 100) / 100, label });
        }
        if (!rows.length) { toast('Enter an amount to apply', 'error'); return null; }
        const remaining = remainingEl && remainingEl.dataset ? parseFloat(remainingEl.dataset.remaining) : NaN;
        const total = rows.reduce((sum, r) => sum + r.amount, 0);
        if (!isNaN(remaining) && total > remaining + 1e-9) {
            toast(`${formatCurrency(total)} is more than the ${formatCurrency(remaining)} of credit remaining. Nothing was applied.`, 'error');
            return null;
        }
        return rows;
    },
    // The server refused a row: its sentence, and what was applied before it.
    failed(row, err, applied, noun) {
        const before = applied.length
            ? ` ${applied.map(r => `${formatCurrency(r.amount)} to ${noun} ${r.label}`).join(', ')} ${applied.length === 1 ? 'was' : 'were'} applied before it; the dialog shows what remains now.`
            : ' Nothing was applied.';
        const cap = noun.charAt(0).toUpperCase() + noun.slice(1);
        // The routes' sentences ("Amount exceeds credit balance") carry no
        // full stop; without one the figure that follows read as the balance
        // (2.31.11 gate, #757).
        const said = String(err && err.message || 'The server refused it').trim();
        const sentence = /[.!?]$/.test(said) ? said : `${said}.`;
        return `${cap} ${row.label}: ${sentence}${before}`;
    },
};
