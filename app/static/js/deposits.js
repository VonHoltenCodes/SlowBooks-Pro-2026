const DepositsPage = {
    async render() {
        const [pending, accounts] = await Promise.all([
            API.get('/deposits/pending'),
            API.get('/accounts?active_only=true&account_type=asset'),
        ]);
        const canManage = App.hasPermission ? App.hasPermission('banking.manage') : true;
        const bankOptions = accounts.map(account =>
            `<option value="${account.id}">${escapeHtml(account.account_number || '')} - ${escapeHtml(account.name)}</option>`
        ).join('');
        let html = `
            <div class="page-header">
                <h2>Make Deposits</h2>
            </div>`;

        if (!pending.length) {
            html += `<div class="empty-state"><p>No pending customer receipts in Undeposited Funds / Receipt Clearing.</p></div>`;
            return html;
        }

        html += `
            <form onsubmit="DepositsPage.save(event)">
                <div class="form-grid" style="margin-bottom:16px;">
                    <div class="form-group"><label>Date *</label><input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Deposit To *</label><select name="deposit_to_account_id" required><option value="">Select...</option>${bankOptions}</select></div>
                    <div class="form-group"><label>Reference</label><input name="reference"></div>
                    <div class="form-group full-width"><label>Memo</label><input name="memo" placeholder="Optional deposit memo"></div>
                </div>
                <div class="table-container"><table>
                    <thead><tr><th style="width:30px;"></th><th>Date</th><th>Customer</th><th>Reference</th><th class="amount">Amount</th></tr></thead>
                    <tbody>
                        ${pending.map(payment => `<tr>
                            <td><input type="checkbox" class="deposit-payment" value="${payment.payment_id}" data-amount="${payment.amount}" onchange="DepositsPage.updateTotal()"></td>
                            <td>${formatDate(payment.date)}</td>
                            <td>${escapeHtml(payment.customer_name)}</td>
                            <td>${escapeHtml(payment.reference || '')}</td>
                            <td class="amount">${formatCurrency(payment.amount)}</td>
                        </tr>`).join('')}
                    </tbody>
                </table></div>
                <div style="margin-top:10px; font-size:11px; color:var(--text-muted);">Selected total: <strong id="deposit-total">${formatCurrency(0)}</strong></div>
                ${canManage ? `<div class="form-actions"><button type="submit" class="btn btn-primary">Record Deposit</button></div>` : ''}
            </form>`;
        return html;
    },

    updateTotal() {
        let total = 0;
        $$('.deposit-payment:checked').forEach(box => { total += parseFloat(box.dataset.amount) || 0; });
        $('#deposit-total').textContent = formatCurrency(total);
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        const payment_ids = $$('.deposit-payment:checked').map(box => parseInt(box.value));
        try {
            await API.post('/deposits', {
                date: form.date.value,
                deposit_to_account_id: parseInt(form.deposit_to_account_id.value),
                payment_ids,
                reference: form.reference.value || null,
                memo: form.memo.value || null,
            });
            toast('Deposit recorded');
            App.navigate('#/deposits');
        } catch (err) { toast(err.message, 'error'); }
    },
};
