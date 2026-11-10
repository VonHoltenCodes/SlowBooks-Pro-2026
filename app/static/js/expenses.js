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
    // the vendor-list choice standing for a payee with no vendor (W-101)
    PAYEE: '__payee__',
    _accounts: [],

    async render() {
        const expenses = await API.get('/expenses');
        let html = `
            <div class="page-header">
                <h2>Expenses</h2>
                <button class="btn btn-primary" ${App.shortcutAttrs('E', 'Enter Expense')} onclick="ExpensesPage.showForm()">+ Enter Expense</button>
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
                html += `<tr data-status="${x.status}"${isVoid ? ' class="row--dim" style="text-decoration:line-through;"' : ''}>
                    <td>${formatDate(x.date)}</td>
                    <td>${escapeHtml(x.payee || '')}</td>
                    <td>${ExpensesPage.accountsText(x)}</td>
                    <td>${escapeHtml(x.paid_from_account_name || '')}</td>
                    <td>${escapeHtml(x.reference || '')}</td>
                    <td style="text-decoration:none;">${statusBadge(x.status)}</td>
                    <td class="amount">${formatCurrency(x.amount)}</td>
                    <td class="actions" style="text-decoration:none;">
                        <button class="btn btn-sm btn-secondary" onclick="ExpensesPage.showDetail(${x.id})">View</button>
                        ${isVoid ? '' : `<button class="btn btn-sm btn-secondary" data-write title="Edit this expense: saving voids it and posts the new one in its place" onclick="ExpensesPage.replace(${x.id})">Edit</button>`}
                        ${isVoid ? '' : `<button data-destructive class="btn btn-sm btn-danger" onclick="ExpensesPage.void(${x.id})">Void</button>`}
                    </td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    // The account column: the one account, or how many a split spreads over.
    accountsText(x) {
        const n = (x.lines || []).length;
        return n > 1 ? `Split (${n} accounts)` : escapeHtml(x.expense_account_name || '');
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
        try { ExpensesPage._jobs = await API.get('/jobs'); } catch (e) { ExpensesPage._jobs = []; }
        await CostCodes.load();
        await CostTypes.load();
        await Nonprofit.loadFunds();
        // Expense and cost-of-goods accounts: materials are bought against
        // 5100 Materials Cost as often as against an expense (W-L18).
        const expenseAccts = PurchaseAccounts.filter(accounts);
        ExpensesPage._expenseAccounts = expenseAccts;
        const paidFrom = ExpensesPage.paidFromAccounts(accounts);
        const acctOpt = a => `<option value="${a.id}">${accountLabel(a)}</option>`;
        const checking = paidFrom.find(a => /checking/i.test(a.name || '')) || paidFrom[0];

        openModal('Enter Expense', `
            <form id="expense-form" data-skip-to="#expense-lines" data-skip-text="Skip to the lines" onsubmit="ExpensesPage.save(event)">
                ${ScanHelper.scanRowHtml()}
                <div class="form-grid">
                    <div class="form-group"><label>Vendor *</label>
                        ${VendorQuickAdd.html(vendors, { id: 'expense-vendor', onchange: 'ExpensesPage.vendorSelected(this.value)' })}</div>
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Paid From *</label>
                        <select name="paid_from_account_id" required>${paidFrom.map(acctOpt).join('')}</select></div>
                    <div class="form-group"><label title="What left the account; the lines below add up to it">Amount Paid *</label>
                        <input name="amount" class="money" type="number" step="0.01" min="0.01" required oninput="ExpensesPage.amountChanged()"></div>
                    <div class="form-group"><label>Reference</label>
                        <input name="reference" placeholder="Receipt / check #"></div>
                    ${classGroup}${jobGroup}
                    <div class="form-group full-width"><label>Memo</label>
                        <textarea name="memo"></textarea></div>
                </div>
                <h3 style="margin:12px 0 8px;font-size:14px;">Lines</h3>
                <div class="table-container table-container--scroll"><table class="line-items-table expense-lines-table">
                    <thead><tr><th scope="col" title="Where the line is recorded">Account *</th><th scope="col">Memo</th>${ExpensesPage.jobHeadHtml()}${CostCodes.headHtml()}${CostTypes.headHtml()}${Nonprofit.headHtml()}<th scope="col" title="Billable to the job's customer">Bill?</th><th scope="col" class="col-amount">Amount</th><th scope="col"></th></tr></thead>
                    <tbody id="expense-lines">${ExpensesPage.lineHtml()}</tbody>
                </table></div>
                <button type="button" class="btn btn-sm btn-secondary" style="margin-top:8px;" onclick="ExpensesPage.addLine()">+ Add Line</button>
                <div class="invoice-totals">
                    <div class="total-row"><span class="label">Lines</span><span class="value" id="expense-lines-total">$0.00</span></div>
                    <div class="total-row grand-total"><span class="label">Amount Paid</span><span class="value" id="expense-paid-total">$0.00</span></div>
                    <div class="total-row"><span class="label">Left to assign</span><span class="value" id="expense-left">$0.00</span></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="ScanHelper.discard(); closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save Expense</button>
                </div>
            </form>`, { wide: true });
        const pf = document.querySelector('#expense-form [name="paid_from_account_id"]');
        if (pf && checking) pf.value = checking.id;
        ScanHelper.wire(ExpensesPage._applyScan, ExpensesPage._applyScanField, ExpensesPage._scanFieldTarget);
    },

    // Edit from a report line (#689): the form filled in with the expense;
    // Save voids it and posts the new one in its place.
    async replace(id) {
        const x = await API.get(`/expenses/${id}`);
        if (x.status === 'void') { toast('This expense is void; there is nothing to change.', 'error'); return; }
        await ExpensesPage.showForm();
        const form = $('#expense-form');
        if (!form) return;
        Replace.set(form, 'date', x.date);
        Replace.set(form, 'paid_from_account_id', x.paid_from_account_id);
        Replace.set(form, 'amount', Number(x.amount).toFixed(2));
        Replace.set(form, 'reference', x.reference || '');
        Replace.set(form, 'memo', x.memo || '');
        Replace.set(form, 'class_id', x.class_id || '');
        Replace.set(form, 'job_id', x.job_id || '');
        // the payee carries over (W-101): an expense with a payee and no
        // vendor (one that came in over the API) replaces as one, its
        // payee standing in the vendor list as its own choice
        form.dataset.replacePayee = x.payee || '';
        form.dataset.replaceVendor = x.vendor_id ? String(x.vendor_id) : '';
        const vendor = document.getElementById('expense-vendor');
        if (vendor && x.vendor_id) vendor.value = String(x.vendor_id);
        else if (vendor && x.payee) {
            const after = vendor.querySelector(`option[value="${VendorQuickAdd.NEW}"]`) || vendor.options[0];
            after.insertAdjacentHTML('afterend', `<option value="${ExpensesPage.PAYEE}">${escapeHtml(x.payee)} (payee, no vendor)</option>`);
            vendor.value = ExpensesPage.PAYEE;
        }
        $('#expense-lines').innerHTML = '';
        // Keep every saved line's account in the picker, even one outside the
        // active Expense/COGS list (a bank, liability or equity split such as
        // mortgage principal or escrow, or an account since made inactive),
        // so Void and Replace preselects it (#745).
        ExpensesPage._expenseAccounts = PurchaseAccounts.filter(
            ExpensesPage._accounts, ...(x.lines || []).map(l => l.account_id));
        (x.lines || []).forEach(l => ExpensesPage.addLine(l));
        if (!(x.lines || []).length) ExpensesPage.addLine();
        ExpensesPage.recalc();
        Replace.mark(form, 'expense', id);
    },

    vendorSelected(vendorId) {
        if (!vendorId || vendorId === VendorQuickAdd.NEW) return;
        const vendor = ExpensesPage._vendors.find(v => v.id == vendorId);
        // the vendor's usual account goes on a line still waiting for one
        const sel = Array.from(document.querySelectorAll('#expense-lines .line-account')).find(a => !a.value);
        if (vendor && vendor.default_expense_account_id && sel) {
            sel.value = vendor.default_expense_account_id;
        }
    },

    jobHeadHtml() { return (ExpensesPage._jobs || []).length ? `<th scope="col">${T('Job')}</th>` : ''; },

    // One line of the expense: where it is recorded, its words, its job,
    // cost code, cost type, function and class, and its share.
    lineHtml(line = null) {
        const l = line || {};
        const acctOpts = PurchaseAccounts.options(ExpensesPage._expenseAccounts, l.account_id);
        const jobs = ExpensesPage._jobs || [];
        const jobCell = jobs.length
            ? `<td><select class="line-job"><option value="">Same as header</option>${jobs.map(j => `<option value="${j.id}"${l.job_id === j.id ? ' selected' : ''}>${escapeHtml(j.full_name || j.name)}</option>`).join('')}</select></td>`
            : '';
        return `<tr>
                <td>${AccountQuickAdd.picker(`<select class="line-account" required><option value="">Choose...</option>${acctOpts}</select>`, 'expense')}</td>
                <td><input class="line-desc" value="${escapeHtml(l.description || '')}"></td>
                ${jobCell}${CostCodes.cellHtml('line-cost-code', l.cost_code_id)}${CostTypes.cellHtml('line-cost-type', l.cost_type)}${ExpensesPage.fundCellsHtml(l)}
                <td style="text-align:center;"><input type="checkbox" class="line-billable" title="Billable"${l.is_billable ? ' checked' : ''}></td>
                <td class="col-amount"><input class="line-amt money" type="number" step="0.01" min="0.01" value="${l.amount ? Number(l.amount).toFixed(2) : ''}" oninput="ExpensesPage.recalc()"></td>
                <td><button type="button" class="btn btn-sm btn-secondary line-remove" aria-label="Remove line" title="Remove this line" onclick="ExpensesPage.removeLine(this)">&times;</button></td>
            </tr>`;
    },

    // The class and function cells of a line (cellHtml's, without its
    // allocation-rule Split button: an expense line has no split-by-rule).
    fundCellsHtml(l) {
        return Nonprofit.classCellHtml('line-function', l.class_id) + (Nonprofit.enabled()
            ? `<td><select class="line-function">${Nonprofit.optionsHtml(l.function, '—')}</select></td>` : '');
    },

    rows() { return Array.from(document.querySelectorAll('#expense-lines tr')); },

    addLine(line = null) {
        $('#expense-lines').insertAdjacentHTML('beforeend', ExpensesPage.lineHtml(line));
        ExpensesPage.recalc();
    },

    removeLine(btn) {
        if (ExpensesPage.rows().length > 1) btn.closest('tr').remove();
        ExpensesPage.recalc();
    },

    // With one line the form is today's: the line takes the amount paid.
    // From the second line on, the shares are typed and must add up to it.
    amountChanged() {
        const rows = ExpensesPage.rows();
        const form = $('#expense-form');
        if (rows.length === 1 && form) {
            rows[0].querySelector('.line-amt').value = form.amount.value;
        }
        ExpensesPage.recalc();
    },

    // Running total of the lines against the amount paid; returns the cents
    // still to assign.
    recalc() {
        const form = $('#expense-form');
        const rows = ExpensesPage.rows();
        const paid = PurchaseLines.cents(parseFloat(form && form.amount.value) || 0);
        const lines = PurchaseLines.cents(rows.reduce((sum, r) => sum + (parseFloat(r.querySelector('.line-amt').value) || 0), 0));
        const left = PurchaseLines.cents(paid - lines);
        $('#expense-lines-total').textContent = formatCurrency(lines);
        $('#expense-paid-total').textContent = formatCurrency(paid);
        const leftEl = $('#expense-left');
        leftEl.textContent = formatCurrency(left);
        leftEl.style.color = left === 0 ? '' : 'var(--text-danger)';
        document.querySelectorAll('#expense-lines .line-remove').forEach(b => { b.disabled = rows.length < 2; });
        return left;
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
            ExpensesPage.amountChanged();
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
        if (total > 0) {
            form.querySelector('[name="amount"]').value = total.toFixed(2);
            ExpensesPage.amountChanged();  // the scan fills the first line
        }
        if (result.tax_detected && result.tax) ExpensesPage._noteLine(form, 'Tax', result.tax);
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        const paidFrom = parseInt(form.paid_from_account_id.value);
        // a lone line takes the amount paid, as an amount typed a moment ago
        // has had no change event yet
        if (ExpensesPage.rows().length === 1) ExpensesPage.amountChanged();
        const lines = [];
        let missing = null;
        ExpensesPage.rows().forEach((row, i) => {
            const acct = row.querySelector('.line-account');
            if (!missing && !acct.value) missing = { n: i + 1, sel: acct };
            const job = row.querySelector('.line-job');
            lines.push({
                account_id: acct.value ? parseInt(acct.value) : null,
                amount: parseFloat(row.querySelector('.line-amt').value) || 0,
                description: row.querySelector('.line-desc').value || null,
                job_id: job && job.value ? parseInt(job.value) : null,
                cost_code_id: CostCodes.fromRow(row, 'line-cost-code'),
                cost_type: CostTypes.fromRow(row, 'line-cost-type'),
                class_id: Nonprofit.fundFromRow(row, 'line-function'),
                ...Nonprofit.linePayload(row, 'line-function'),
                is_billable: !!row.querySelector('.line-billable')?.checked,
            });
        });
        if (missing) {
            toast(`Choose an account for line ${missing.n}: where should it be recorded?`, 'error');
            missing.sel.focus();
            return;
        }
        if (lines.some(l => !(l.amount > 0))) {
            toast('Every line needs an amount above 0.00; remove a line that is not used.', 'error');
            return;
        }
        const left = ExpensesPage.recalc();
        if (left !== 0) {
            toast(`The lines must add up to the amount paid: ${formatCurrency(Math.abs(left))} ${left > 0 ? 'left to assign' : 'over'}.`, 'error');
            return;
        }
        if (!(await Overdraft.confirm(paidFrom, parseFloat(form.amount.value) || 0))) return;
        if (!(await TxnWarn.ok(form))) return;
        if (!(await ClassWarn.ok(form, { rows: '#expense-lines tr', cls: 'line-function' }))) return;
        try {
            const vendorSel = document.getElementById('expense-vendor');
            const payeeOnly = !!vendorSel && vendorSel.value === ExpensesPage.PAYEE;
            const vendorId = payeeOnly ? null : await VendorQuickAdd.ensure('expense-vendor');
            // a replacement keeps the payee while its vendor is the one it had
            const payee = form.dataset.replacePayee
                && (payeeOnly || String(vendorId) === form.dataset.replaceVendor)
                ? form.dataset.replacePayee : null;
            const body = {
                date: form.date.value,
                vendor_id: vendorId,
                payee,
                paid_from_account_id: paidFrom,
                amount: parseFloat(form.amount.value),
                lines,
                reference: form.reference.value || null,
                memo: form.memo.value || null,
                class_id: classIdFromForm(form),
                job_id: jobIdFromForm(form),
            };
            // a check number the bank account already has (#481)
            const result = await CheckNumberWarn.send(
                q => API.post('/expenses' + (typeof Replace === 'undefined' ? '' : Replace.suffix(form)), body, q ? { query: q } : undefined), form.reference);
            if (!result) return;
            await ScanHelper.attachAfterSave('expense', result.id);
            if ((typeof Replace !== 'undefined' && Replace.replacing(form))) toastPosted(`Expense voided and replaced by expense #${result.id}`);
            else toastPosted('Expense recorded');
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
                ${(x.lines || []).length > 1
                    ? `<table class="line-items-table" style="margin:4px 0;"><thead><tr><th scope="col">Account</th><th scope="col">Memo</th><th scope="col" class="amount">Amount</th></tr></thead><tbody>${x.lines.map(l => `<tr><td>${escapeHtml(l.account_name || '')}</td><td>${escapeHtml(l.description || '')}</td><td class="amount">${formatCurrency(l.amount)}</td></tr>`).join('')}</tbody></table>`
                    : `<strong>Expense Account:</strong> ${escapeHtml(x.expense_account_name || '')}<br>`}
                <strong>Paid From:</strong> ${escapeHtml(x.paid_from_account_name || '')}<br>
                <strong>Amount:</strong> ${formatCurrency(x.amount)}<br>
                ${x.reference ? `<strong>Reference:</strong> ${escapeHtml(x.reference)}<br>` : ''}
                ${x.memo ? `<strong>Memo:</strong> ${escapeHtml(x.memo).replace(/\n/g, '<br>')}<br>` : ''}
                ${typeof Replace === 'undefined' ? '' : Replace.links(x, 'expense', n => `#/expenses/${n}`)}
            </div>
            <div style="margin-top:12px; border-top:1px solid var(--gray-300); padding-top:8px;">
                <div style="font-weight:700; font-size:12px; margin-bottom:4px;">Attachments</div>
                <div id="expense-attachments-list" style="margin-bottom:8px; font-size:11px;">Loading...</div>
                <div style="display:flex; gap:6px; align-items:center;">
                    <input type="file" id="expense-attach-file" aria-label="File to attach" style="font-size:11px;">
                    <button class="btn btn-sm btn-secondary" onclick="ExpensesPage.uploadAttachment(${x.id})">Attach</button>
                </div>
            </div>
            <div class="form-actions">
                ${x.printable ? `<button type="button" class="btn btn-secondary" onclick="window.open('/api/checks/print?transaction_id=${x.id}','_blank')">Print Check</button>` : ''}
                ${isVoid ? '' : `<button data-write type="button" class="btn btn-secondary" title="Edit this expense: saving voids it and posts the new one in its place" onclick="ExpensesPage.replace(${x.id})">Edit</button>`}
                ${isVoid ? '' : `<button data-destructive type="button" class="btn btn-danger" onclick="ExpensesPage.void(${x.id})">Void</button>`}
                <button type="button" class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
        if (App.documentAddress) App.documentAddress(`#/expenses/${Number(id)}`);  // the document's own address (NEW-164)
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
                    `<div style="display:flex; flex-wrap:wrap; align-items:center; gap:0 8px; padding:2px 0;">
                        ${a.missing ? `<span>${escapeHtml(a.filename)}</span>` : `<a href="/api/attachments/download/${a.id}" target="_blank">${escapeHtml(a.filename)}</a>`}
                        ${a.file_size == null ? '' : `<span style="color:var(--gray-400);">(${formatFileSize(a.file_size)})</span>`}
                        <button data-destructive aria-label="Delete attachment" class="btn btn-sm btn-danger" onclick="ExpensesPage.deleteAttachment(${a.id},${id})" style="padding:0 4px; font-size:10px;">X</button>
                        ${storedFileNote(a)}
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
    async confirm(accountId, amount, defaultNumber, action = 'Save') {
        if (!(amount > 0)) return true;
        let accounts;
        try { accounts = await API.get('/banking/overview'); } catch (e) { return true; }
        const acct = accounts.find(a => accountId ? a.account_id === accountId : a.account_number === defaultNumber);
        if (!acct || acct.bank_kind !== 'bank') return true;
        const after = PurchaseLines.cents(Number(acct.balance) - amount);
        if (after >= 0) return true;
        return confirm(`${acct.name} will be overdrawn by ${formatCurrency(-after)}. ${action} anyway?`);
    },
};

// The expense's account adds an expense or cost of goods account (#485),
// picked when it is saved; the panel opens under the picker.
if (typeof AccountQuickAdd !== 'undefined') {
    AccountQuickAdd.register('expense', {
        types: AccountQuickAdd.PURCHASE_TYPES,
        compact: true,
        chart: () => (ExpensesPage._expenseAccounts = ExpensesPage._expenseAccounts || []),
        added: () => AccountQuickAdd.refillPurchase($$('#expense-lines select.line-account'), ExpensesPage._expenseAccounts),
    });
}
