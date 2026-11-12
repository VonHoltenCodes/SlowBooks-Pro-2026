/**
 * Credit Card Charges — Enter CC expenses
 * DR Expense Account, CR Credit Card Payable (2100)
 */
const CCChargesPage = {
    async render() {
        const charges = await API.get('/cc-charges');
        let html = `
            <div class="page-header">
                <h2>Credit Card Charges</h2>
                <button class="btn btn-primary" onclick="CCChargesPage.showForm()">+ Enter Charge</button>
            </div>`;

        if (charges.length === 0) {
            html += '<div class="empty-state"><p>No credit card charges recorded yet</p></div>';
        } else {
            html += `<div class="table-container"><table>
                <thead><tr><th scope="col">Date</th><th scope="col">Payee</th><th scope="col">Account</th><th scope="col">Card</th><th scope="col">Reference</th>
                <th scope="col" class="amount">Amount</th><th scope="col"></th></tr></thead><tbody>`;
            for (const c of charges) {
                const voided = c.status === 'void';
                html += `<tr style="${voided ? 'color:var(--gray-400); text-decoration:line-through;' : ''}">
                    <td>${formatDate(c.date)}</td>
                    <td>${escapeHtml(c.description || '')}</td>
                    <td>${escapeHtml(c.account_name || '')}</td>
                    <td>${escapeHtml(c.card_account_name || '')}</td>
                    <td>${escapeHtml(c.reference || '')}</td>
                    <td class="amount">${formatCurrency(c.amount)}</td>
                    <td style="text-decoration:none;"><button class="btn btn-sm btn-secondary" onclick="CCChargesPage.showFiles(${c.id})">Files</button>
                        ${voided ? '' : `<button data-destructive class="btn btn-sm btn-secondary" onclick="CCChargesPage.voidCharge(${c.id})">Void</button>`}</td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    // `keepIds`: the accounts a replaced charge already posts to, kept in the
    // picker whatever their type or state (#745)
    async showForm(...keepIds) {
        const [allAccounts, cards] = await Promise.all([
            API.get('/accounts'),
            API.get('/accounts?bank=1&active_only=true'),
        ]);
        // Expense and cost-of-goods accounts (W-L18): a card buys materials too.
        const accounts = PurchaseAccounts.withBalanceSheet(allAccounts, ...keepIds);  // and the balance-sheet accounts (#766)
        const cardOpts = cards.filter(a => a.bank_kind === 'credit_card').map(a =>
            `<option value="${a.id}" ${a.account_number === '2100' ? 'selected' : ''}>${accountLabel(a)}</option>`
        ).join('');
        const classGroup = await classFormGroupHtml();
        const jobGroup = await jobFormGroupHtml(null);
        const acctOpts = PurchaseAccounts.options(accounts);
        PendingFiles.reset();  // files attached before the first save (#796)

        openModal('Enter Credit Card Charge', `
            <form onsubmit="CCChargesPage.save(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Payee</label>
                        <input name="payee"></div>
                    <div class="form-group"><label>Expense Account *</label>
                        <select name="account_id" required><option value="">Select...</option>${acctOpts}</select></div>
                    <div class="form-group"><label>Card *</label>
                        <select name="card_account_id" required>${cardOpts}</select></div>
                    <div class="form-group"><label>Amount *</label>
                        <input name="amount" class="money" type="number" step="0.01" required></div>
                    <div class="form-group"><label>Reference</label>
                        <input name="reference"></div>
                    ${classGroup}${Nonprofit.functionFormGroupHtml()}${jobGroup}
                    <div class="form-group full-width"><label>Memo</label>
                        <textarea name="memo"></textarea></div>
                </div>
                ${PendingFiles.stripHtml()}
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save Charge</button>
                </div>
            </form>`, { onClose: () => PendingFiles.discard() });
    },

    // Edit from a report line (#689): the form filled in with the charge
    // (read off its journal entry); Save voids it and posts the new one.
    async replace(id) {
        const entry = await API.get(`/journal/${id}`);
        if (entry.voided) { toast('This charge is void; there is nothing to change.', 'error'); return; }
        const spent = (entry.lines || []).find(l => l.debit > 0) || {};
        await CCChargesPage.showForm(spent.account_id);
        const form = document.querySelector('#modal-body form');
        if (!form) return;
        const card = (entry.lines || []).find(l => l.credit > 0) || {};
        const payee = (entry.description || '').startsWith('CC Charge: ') ? entry.description.slice(11) : '';
        Replace.set(form, 'date', entry.date);
        Replace.set(form, 'payee', payee);
        Replace.set(form, 'account_id', spent.account_id);
        Replace.set(form, 'card_account_id', card.account_id);
        Replace.set(form, 'amount', Number(spent.debit || 0).toFixed(2));
        Replace.set(form, 'reference', entry.reference || '');
        Replace.set(form, 'memo', spent.description && spent.description !== payee ? spent.description : '');
        Replace.set(form, 'class_id', entry.class_id || '');
        Replace.set(form, 'job_id', entry.job_id || '');
        if (spent.function) Replace.set(form, 'function', spent.function);
        Replace.mark(form, 'cc_charge', id);
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        if (!(await TxnWarn.ok(form))) return;
        if (!(await ClassWarn.ok(form))) return;
        try {
            const r = await API.post('/cc-charges' + (typeof Replace === 'undefined' ? '' : Replace.suffix(form)), {
                date: form.date.value,
                payee: form.payee.value || null,
                account_id: parseInt(form.account_id.value),
                card_account_id: parseInt(form.card_account_id.value),
                amount: parseFloat(form.amount.value),
                reference: form.reference.value || null,
                memo: form.memo.value || null,
                class_id: classIdFromForm(form),
                ...Nonprofit.formPayload(form),
                job_id: jobIdFromForm(form),
                pending_attachments: PendingFiles.ids(),
            });
            PendingFiles.saved();
            if ((typeof Replace !== 'undefined' && Replace.replacing(form))) toastPosted(`Charge voided and replaced by charge #${r.transaction_id}`);
            else toastPosted('Credit card charge recorded');
            closeModal();
            App.navigate('#/cc-charges');
        } catch (err) { toast(err.message, 'error'); }
    },

    // A charge's receipts (#796): the files attached when it was entered,
    // and any added since. A void charge keeps them.
    async showFiles(id) {
        openModal('Credit Card Charge — Attachments', `
            <div id="cc-attachments-list" style="margin-bottom:8px; font-size:11px;">Loading...</div>
            <div style="display:flex; gap:6px; align-items:center;">
                <input type="file" id="cc-attach-file" aria-label="File to attach" style="font-size:11px;">
                <button class="btn btn-sm btn-secondary" onclick="CCChargesPage.uploadAttachment(${Number(id)})">Attach</button>
            </div>
            <div class="form-actions">
                <button type="button" class="btn btn-secondary" onclick="closeModal()">Close</button>
            </div>`);
        await CCChargesPage.loadAttachments(id);
    },

    async loadAttachments(id) {
        const el = $('#cc-attachments-list');
        if (!el) return;
        try {
            const attachments = await API.get(`/attachments/cc_charge/${id}`);
            if (attachments.length === 0) {
                el.innerHTML = '<span style="color:var(--text-muted);">No attachments</span>';
            } else {
                el.innerHTML = attachments.map(a =>
                    `<div style="display:flex; flex-wrap:wrap; align-items:center; gap:0 8px; padding:2px 0;">
                        ${a.missing ? `<span>${escapeHtml(a.filename)}</span>` : `<a href="/api/attachments/download/${a.id}" target="_blank">${escapeHtml(a.filename)}</a>`}
                        ${a.file_size == null ? '' : `<span style="color:var(--gray-400);">(${formatFileSize(a.file_size)})</span>`}
                        <button data-destructive aria-label="Delete attachment" class="btn btn-sm btn-danger" onclick="CCChargesPage.deleteAttachment(${a.id},${Number(id)})" style="padding:0 4px; font-size:10px;">X</button>
                        ${storedFileNote(a)}
                    </div>`
                ).join('');
            }
        } catch (e) { el.innerHTML = ''; }
    },

    async uploadAttachment(id) {
        const fileInput = $('#cc-attach-file');
        if (!fileInput?.files[0]) { toast('Select a file first', 'error'); return; }
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        try {
            const resp = await fetch(`/api/attachments/cc_charge/${id}`, { method: 'POST', body: formData });
            if (!resp.ok) throw new Error(await API.responseError(resp, 'Upload failed'));
            toast('Attachment uploaded');
            fileInput.value = '';
            CCChargesPage.loadAttachments(id);
        } catch (err) { toast(err.message, 'error'); }
    },

    async deleteAttachment(attachmentId, id) {
        if (!confirm('Delete this attachment?')) return;
        try {
            await API.del(`/attachments/${attachmentId}`);
            toast('Attachment deleted');
            CCChargesPage.loadAttachments(id);
        } catch (err) { toast(err.message, 'error'); }
    },

    async voidCharge(id) {
        if (!confirm('Void this charge? A reversing entry is posted.')) return;
        try {
            await API.post(`/cc-charges/${id}/void`);
            toast('Charge voided');
            App.navigate('#/cc-charges');
        } catch (err) { toast(err.message, 'error'); }
    },
};
