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
                <thead><tr><th>Date</th><th>Payee</th><th>Account</th><th>Reference</th>
                <th class="amount">Amount</th></tr></thead><tbody>`;
            for (const c of charges) {
                html += `<tr>
                    <td>${formatDate(c.date)}</td>
                    <td>${escapeHtml(c.description || '')}</td>
                    <td>${escapeHtml(c.account_name || '')}</td>
                    <td>${escapeHtml(c.reference || '')}</td>
                    <td class="amount">${formatCurrency(c.amount)}</td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    async showForm() {
        const accounts = await API.get('/accounts?account_type=expense');
        const acctOpts = accounts.map(a =>
            `<option value="${a.id}">${escapeHtml(a.account_number)} - ${escapeHtml(a.name)}</option>`
        ).join('');

        openModal('Enter Credit Card Charge', `
            <form onsubmit="CCChargesPage.save(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Payee</label>
                        <input name="payee"></div>
                    <div class="form-group"><label>Expense Account *</label>
                        <select name="account_id" required><option value="">Select...</option>${acctOpts}</select></div>
                    <div class="form-group"><label>Amount *</label>
                        <input name="amount" type="number" step="0.01" required></div>
                    <div class="form-group"><label>Reference</label>
                        <input name="reference"></div>
                    <div class="form-group full-width"><label>Memo</label>
                        <textarea name="memo"></textarea></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save Charge</button>
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
