const CCChargesPage = {
    async render() {
        const charges = await API.get('/cc-charges');
        const canManage = App.hasPermission ? App.hasPermission('banking.manage') : true;
        let html = `
            <div class="page-header">
                <h2>Credit Card Charges</h2>
                ${canManage ? `<button class="btn btn-primary" onclick="CCChargesPage.showForm()">+ New Charge</button>` : ''}
            </div>`;
        if (!charges.length) {
            html += `<div class="empty-state"><p>No credit-card charges recorded yet</p></div>`;
            return html;
        }
        html += `<div class="table-container"><table>
            <thead><tr><th>Date</th><th>Payee</th><th>Expense Account</th><th>Card Account</th><th class="amount">Amount</th><th>Reference</th></tr></thead>
            <tbody>
                ${charges.map(charge => `<tr>
                    <td>${formatDate(charge.date)}</td>
                    <td>${escapeHtml(charge.payee || '')}</td>
                    <td>${escapeHtml(charge.account_name || '')}</td>
                    <td>${escapeHtml(charge.credit_card_account_name || '')}</td>
                    <td class="amount">${formatCurrency(charge.amount)}</td>
                    <td>${escapeHtml(charge.reference || '')}</td>
                </tr>`).join('')}
            </tbody>
        </table></div>`;
        return html;
    },

    async showForm() {
        const [expenseAccounts, liabilityAccounts] = await Promise.all([
            API.get('/accounts?active_only=true&account_type=expense'),
            API.get('/accounts?active_only=true&account_type=liability'),
        ]);
        openModal('New Credit Card Charge', `
            <form onsubmit="CCChargesPage.save(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Date *</label><input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Payee</label><input name="payee"></div>
                    <div class="form-group"><label>Expense Account *</label><select name="account_id" required><option value="">Select...</option>${expenseAccounts.map(account => `<option value="${account.id}">${escapeHtml(account.account_number || '')} - ${escapeHtml(account.name)}</option>`).join('')}</select></div>
                    <div class="form-group"><label>Card Liability Account *</label><select name="credit_card_account_id" required><option value="">Select...</option>${liabilityAccounts.map(account => `<option value="${account.id}">${escapeHtml(account.account_number || '')} - ${escapeHtml(account.name)}</option>`).join('')}</select></div>
                    <div class="form-group"><label>Amount *</label><input name="amount" type="number" step="0.01" required></div>
                    <div class="form-group"><label>Reference</label><input name="reference"></div>
                    <div class="form-group full-width"><label>Memo</label><input name="memo"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Record Charge</button>
                </div>
            </form>`);
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        try {
            await API.post('/cc-charges', {
                date: form.date.value,
                payee: form.payee.value || null,
                account_id: parseInt(form.account_id.value),
                credit_card_account_id: parseInt(form.credit_card_account_id.value),
                amount: parseFloat(form.amount.value),
                reference: form.reference.value || null,
                memo: form.memo.value || null,
            });
            toast('Credit card charge recorded');
            closeModal();
            App.navigate('#/cc-charges');
        } catch (err) { toast(err.message, 'error'); }
    },
};
