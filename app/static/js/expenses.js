/**
 * Expenses — record a receipt that's already been paid.
 * DR Expense Account, CR the bank / credit-card account it was paid from.
 *
 * This is where most scanned receipts belong: a bill is money you still
 * owe, a sales receipt is money you took in — a paid receipt is neither.
 * One form, one save, scan attached as evidence.
 */
const ExpensesPage = {
    _vendors: [],
    _accounts: [],

    async render() {
        const expenses = await API.get('/expenses');
        let html = `
            <div class="page-header">
                <h2>Expenses</h2>
                <button class="btn btn-primary" onclick="ExpensesPage.showForm()">+ Enter Expense</button>
            </div>`;

        if (expenses.length === 0) {
            html += `<div class="empty-state"><p>No expenses recorded yet</p>
                <p style="font-size:12px; color:var(--gray-600);">Enter a receipt you've already paid — by card, cash, or check. Scan it to fill the form.</p></div>`;
        } else {
            html += `<div class="table-container"><table>
                <thead><tr><th scope="col">Date</th><th scope="col">Payee</th><th scope="col">Expense Account</th><th scope="col">Paid From</th><th scope="col">Reference</th>
                <th scope="col">Status</th><th scope="col" class="amount">Amount</th><th scope="col"></th></tr></thead><tbody>`;
            for (const x of expenses) {
                const isVoid = x.status === 'void';
                html += `<tr data-status="${x.status}"${isVoid ? ' style="opacity:0.6; text-decoration:line-through;"' : ''}>
                    <td>${formatDate(x.date)}</td>
                    <td>${escapeHtml(x.payee || '')}</td>
                    <td>${escapeHtml(x.expense_account_name || '')}</td>
                    <td>${escapeHtml(x.paid_from_account_name || '')}</td>
                    <td>${escapeHtml(x.reference || '')}</td>
                    <td style="text-decoration:none;">${statusBadge(x.status)}</td>
                    <td class="amount">${formatCurrency(x.amount)}</td>
                    <td class="actions" style="text-decoration:none;">
                        <button class="btn btn-sm btn-secondary" onclick="ExpensesPage.showDetail(${x.id})">View</button>
                        ${isVoid ? '' : `<button class="btn btn-sm btn-danger" onclick="ExpensesPage.void(${x.id})">Void</button>`}
                    </td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    // Accounts money can leave from: the chart's bank and credit-card
    // accounts (bank_kind), nothing else.
    paidFromAccounts(accounts) {
        return accounts.filter(a => a.bank_kind);
    },

    async showForm() {
        const [vendors, accounts] = await Promise.all([
            API.get('/vendors?active_only=true'),
            API.get('/accounts'),
        ]);
        ExpensesPage._vendors = vendors;
        ExpensesPage._accounts = accounts;
        const classGroup = await classFormGroupHtml();
        const jobGroup = await jobFormGroupHtml(null);
        await CostCodes.load();
        const costCodeGroup = CostCodes.any() ? `<div class="form-group"><label>Cost Code</label><select name="cost_code_id">${CostCodes.optionsHtml(null)}</select></div>` : '';
        const billableGroup = `<div class="form-group"><label>Billable</label><label style="font-weight:normal;"><input type="checkbox" name="is_billable"> Bill this cost to the job's customer</label></div>`;
        // Expense and cost-of-goods accounts: materials are bought against
        // 5100 Materials Cost as often as against an expense (W-L18).
        const expenseAccts = PurchaseAccounts.filter(accounts);
        const paidFrom = ExpensesPage.paidFromAccounts(accounts);
        const acctOpt = a => `<option value="${a.id}">${escapeHtml(a.account_number || '')} - ${escapeHtml(a.name)}</option>`;
        const checking = paidFrom.find(a => /checking/i.test(a.name || '')) || paidFrom[0];

        openModal('Enter Expense', `
            <form id="expense-form" onsubmit="ExpensesPage.save(event)">
                ${ScanHelper.scanRowHtml()}
                <div class="form-grid">
                    <div class="form-group"><label>Vendor *</label>
                        ${VendorQuickAdd.html(vendors, { id: 'expense-vendor', onchange: 'ExpensesPage.vendorSelected(this.value)' })}</div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Expense Account *</label>
                        <select name="expense_account_id" required><option value="">Select...</option>${expenseAccts.map(acctOpt).join('')}</select></div>
                    <div class="form-group"><label>Paid From *</label>
                        <select name="paid_from_account_id" required>${paidFrom.map(acctOpt).join('')}</select></div>
                    <div class="form-group"><label>Amount *</label>
                        <input name="amount" type="number" step="0.01" min="0.01" required></div>
                    <div class="form-group"><label>Reference</label>
                        <input name="reference" placeholder="Receipt / check #"></div>
                    ${classGroup}${Nonprofit.functionFormGroupHtml()}${jobGroup}${costCodeGroup}${billableGroup}
                    <div class="form-group full-width"><label>Memo</label>
                        <textarea name="memo"></textarea></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="ScanHelper.discard(); closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save Expense</button>
                </div>
            </form>`);
        const pf = document.querySelector('#expense-form [name="paid_from_account_id"]');
        if (pf && checking) pf.value = checking.id;
        ScanHelper.wire(ExpensesPage._applyScan, ExpensesPage._applyScanField, ExpensesPage._scanFieldTarget);
    },

    vendorSelected(vendorId) {
        if (!vendorId || vendorId === VendorQuickAdd.NEW) return;
        const vendor = ExpensesPage._vendors.find(v => v.id == vendorId);
        const sel = document.querySelector('#expense-form [name="expense_account_id"]');
        if (vendor && vendor.default_expense_account_id && sel && !sel.value) {
            sel.value = vendor.default_expense_account_id;
        }
    },

    // Where each canvas field lands — the canvas outlines these inputs in
    // the field's color so the form doubles as the legend.
    _scanFieldTarget(fieldKey) {
        const form = $('#expense-form');
        if (!form) return null;
        if (fieldKey === 'date') return form.querySelector('[name="date"]');
        if (fieldKey === 'merchant') return VendorQuickAdd.nameInput('expense-vendor') || form.querySelector('#expense-vendor');
        if (fieldKey === 'total') return form.querySelector('[name="amount"]');
        if (fieldKey === 'subtotal' || fieldKey === 'tax') return form.querySelector('[name="memo"]');
        if (fieldKey === 'reference') return form.querySelector('[name="reference"]');
        return null;
    },

    _noteLine(form, label, value) {
        const memo = form.querySelector('[name="memo"]');
        if (!memo) return;
        const re = new RegExp(`^${label}:`);
        const kept = memo.value.split('\n').filter(l => !re.test(l)).join('\n').replace(/\s*$/, '');
        memo.value = (kept ? kept + '\n' : '') + `${label}: $${value}`;
    },

    // Box-to-fix canvas: apply one re-read field. Returns {error} when the
    // value can't land, otherwise nothing.
    _applyScanField(fieldKey, value) {
        const form = $('#expense-form');
        if (!form) return;
        if (fieldKey === 'date') {
            form.querySelector('[name="date"]').value = value;
        } else if (fieldKey === 'merchant') {
            VendorQuickAdd.prefill('expense-vendor', value, ExpensesPage._vendors);
        } else if (fieldKey === 'total') {
            // The receipt total is what left the account — tax included.
            form.querySelector('[name="amount"]').value = parseFloat(value).toFixed(2);
        } else if (fieldKey === 'reference') {
            const ref = form.querySelector('[name="reference"]');
            if (ref) ref.value = value;
        } else if (fieldKey === 'subtotal') {
            ExpensesPage._noteLine(form, 'Subtotal', value);
        } else if (fieldKey === 'tax') {
            ExpensesPage._noteLine(form, 'Tax', value);
        }
    },

    _applyScan(result) {
        const form = $('#expense-form');
        if (!form) return;
        if (result.date) form.querySelector('[name="date"]').value = result.date;
        const ref = form.querySelector('[name="reference"]');
        if (result.reference && ref && !ref.value) ref.value = result.reference;

        const merchant = result.merchant && result.merchant.value;
        if (merchant) {
            const match = VendorQuickAdd.prefill('expense-vendor', merchant, ExpensesPage._vendors);
            if (match) {
                ExpensesPage.vendorSelected(match.id);
            } else {
                const statusEl = $('#scan-status');
                if (statusEl) {
                    statusEl.textContent = `Detected: ${merchant} — new vendor; it's added when you save (or pick one from the list).`;
                }
            }
        }

        const total = parseFloat(result.total || '0');
        if (total > 0) form.querySelector('[name="amount"]').value = total.toFixed(2);
        if (result.tax_detected && result.tax) ExpensesPage._noteLine(form, 'Tax', result.tax);
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        const paidFrom = parseInt(form.paid_from_account_id.value);
        if (!(await Overdraft.confirm(paidFrom, parseFloat(form.amount.value) || 0))) return;
        try {
            const vendorId = await VendorQuickAdd.ensure('expense-vendor');
            const result = await API.post('/expenses', {
                date: form.date.value,
                vendor_id: vendorId,
                expense_account_id: parseInt(form.expense_account_id.value),
                paid_from_account_id: paidFrom,
                amount: parseFloat(form.amount.value),
                reference: form.reference.value || null,
                memo: form.memo.value || null,
                class_id: classIdFromForm(form),
                ...Nonprofit.formPayload(form),
                job_id: jobIdFromForm(form),
                cost_code_id: form.cost_code_id ? (form.cost_code_id.value ? parseInt(form.cost_code_id.value) : null) : null,
                is_billable: !!(form.is_billable && form.is_billable.checked),
            });
            await ScanHelper.attachAfterSave('expense', result.id);
            toast('Expense recorded');
            closeModal();
            App.navigate('#/expenses');
        } catch (err) { toast(err.message, 'error'); }
    },

    /** Wrong account, wrong amount? Void posts the mirror-image entry
     *  (the original stays in the ledger) — then enter it again. */
    async void(id) {
        if (!confirm('Void this expense? A reversing entry will be posted; enter it again to correct it.')) return;
        try {
            await API.post(`/expenses/${id}/void`);
            toast('Expense voided');
            closeModal();
            App.navigate('#/expenses');
        } catch (err) { toast(err.message, 'error'); }
    },

    async showDetail(id) {
        const x = await API.get(`/expenses/${id}`);
        const isVoid = x.status === 'void';
        openModal(`Expense — ${escapeHtml(x.payee || '')}${isVoid ? ' (VOID)' : ''}`, `
            <div style="font-size:13px; line-height:1.7;">
                <strong>Date:</strong> ${formatDate(x.date)}<br>
                <strong>Payee:</strong> ${escapeHtml(x.payee || '')}<br>
                <strong>Expense Account:</strong> ${escapeHtml(x.expense_account_name || '')}<br>
                <strong>Paid From:</strong> ${escapeHtml(x.paid_from_account_name || '')}<br>
                <strong>Amount:</strong> ${formatCurrency(x.amount)}<br>
                ${x.reference ? `<strong>Reference:</strong> ${escapeHtml(x.reference)}<br>` : ''}
                ${x.memo ? `<strong>Memo:</strong> ${escapeHtml(x.memo).replace(/\n/g, '<br>')}<br>` : ''}
            </div>
            <div style="margin-top:12px; border-top:1px solid var(--gray-300); padding-top:8px;">
                <div style="font-weight:700; font-size:12px; margin-bottom:4px;">Attachments</div>
                <div id="expense-attachments-list" style="margin-bottom:8px; font-size:11px;">Loading...</div>
                <div style="display:flex; gap:6px; align-items:center;">
                    <input type="file" id="expense-attach-file" style="font-size:11px;">
                    <button class="btn btn-sm btn-secondary" onclick="ExpensesPage.uploadAttachment(${x.id})">Attach</button>
                </div>
            </div>
            <div class="form-actions">
                ${isVoid ? '' : `<button type="button" class="btn btn-danger" onclick="ExpensesPage.void(${x.id})">Void</button>`}
                <button type="button" class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
        ExpensesPage.loadAttachments(x.id);
    },

    async loadAttachments(id) {
        const el = $('#expense-attachments-list');
        if (!el) return;
        try {
            const attachments = await API.get(`/attachments/expense/${id}`);
            if (attachments.length === 0) {
                el.innerHTML = '<span style="color:var(--text-muted);">No attachments</span>';
            } else {
                el.innerHTML = attachments.map(a =>
                    `<div style="display:flex; align-items:center; gap:8px; padding:2px 0;">
                        <a href="/api/attachments/download/${a.id}" target="_blank">${escapeHtml(a.filename)}</a>
                        <span style="color:var(--gray-400);">(${formatFileSize(a.file_size)})</span>
                        <button aria-label="Delete attachment" class="btn btn-sm btn-danger" onclick="ExpensesPage.deleteAttachment(${a.id},${id})" style="padding:0 4px; font-size:10px;">X</button>
                    </div>`
                ).join('');
            }
        } catch (e) { el.innerHTML = ''; }
    },

    async uploadAttachment(id) {
        const fileInput = $('#expense-attach-file');
        if (!fileInput?.files[0]) { toast('Select a file first', 'error'); return; }
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        try {
            const resp = await fetch(`/api/attachments/expense/${id}`, { method: 'POST', body: formData });
            if (!resp.ok) throw new Error(await API.responseError(resp, 'Upload failed'));
            toast('Attachment uploaded');
            fileInput.value = '';
            ExpensesPage.loadAttachments(id);
        } catch (err) { toast(err.message, 'error'); }
    },

    async deleteAttachment(attachmentId, id) {
        if (!confirm('Delete this attachment?')) return;
        try {
            await API.del(`/attachments/${attachmentId}`);
            toast('Attachment deleted');
            ExpensesPage.loadAttachments(id);
        } catch (err) { toast(err.message, 'error'); }
    },
};

/**
 * Money leaving a bank account: before an expense or a bill payment is
 * saved, say so when it takes the account below zero, and let the user go
 * ahead or stop (macbase1 S-a: an expense and a pay run overdrew Checking
 * to -$2,986.90 without a word). The balance is the ledger balance the
 * register shows. A card is left alone — its balance is money owed, not
 * money in the bank. A failed lookup never blocks the save.
 */
const Overdraft = {
    // accountId, or null for "the server's default" named by defaultNumber.
    async confirm(accountId, amount, defaultNumber) {
        if (!(amount > 0)) return true;
        let accounts;
        try { accounts = await API.get('/banking/overview'); } catch (e) { return true; }
        const acct = accounts.find(a => accountId ? a.account_id === accountId : a.account_number === defaultNumber);
        if (!acct || acct.bank_kind !== 'bank') return true;
        const after = PurchaseLines.cents(Number(acct.balance) - amount);
        if (after >= 0) return true;
        return confirm(`${acct.name} will be overdrawn by ${formatCurrency(-after)}. Save anyway?`);
    },
};
