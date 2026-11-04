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
                    <td>${voided ? '' : `<button data-destructive class="btn btn-sm btn-secondary" onclick="CCChargesPage.voidCharge(${c.id})">Void</button>`}</td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    async showForm() {
        const [allAccounts, cards] = await Promise.all([
            API.get('/accounts'),
            API.get('/accounts?bank=1&active_only=true'),
        ]);
        // Expense and cost-of-goods accounts (W-L18): a card buys materials too.
        const accounts = PurchaseAccounts.filter(allAccounts);
        const cardOpts = cards.filter(a => a.bank_kind === 'credit_card').map(a =>
            `<option value="${a.id}" ${a.account_number === '2100' ? 'selected' : ''}>${accountLabel(a)}</option>`
        ).join('');
        const classGroup = await classFormGroupHtml();
        const jobGroup = await jobFormGroupHtml(null);
        const acctOpts = PurchaseAccounts.options(accounts);

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
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save Charge</button>
                </div>
            </form>`);
    },

    // Edit from a report line (#689): the form filled in with the charge
    // (read off its journal entry); Save voids it and posts the new one.
    async replace(id) {
        const entry = await API.get(`/journal/${id}`);
        if (entry.voided) { toast('This charge is void; there is nothing to change.', 'error'); return; }
        await CCChargesPage.showForm();
        const form = document.querySelector('#modal-body form');
        if (!form) return;
        const spent = (entry.lines || []).find(l => l.debit > 0) || {};
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
            });
            if ((typeof Replace !== 'undefined' && Replace.replacing(form))) toastPosted(`Charge voided and replaced by charge #${r.transaction_id}`);
            else toastPosted('Credit card charge recorded');
            closeModal();
            App.navigate('#/cc-charges');
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
