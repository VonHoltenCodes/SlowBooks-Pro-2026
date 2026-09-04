/**
 * Batch Payment Application — apply payments to multiple invoices
 * Feature 7: UI shows open invoices with editable amounts
 */
const BatchPaymentsPage = {
    async render() {
        const [invoices, accounts] = await Promise.all([
            API.get('/invoices'),
            API.get('/accounts?account_type=asset'),
        ]);
        const openInv = invoices.filter(i => i.balance_due > 0 && i.status !== 'void');

        // Group by customer
        const byCustomer = {};
        for (const inv of openInv) {
            const cname = inv.customer_name || 'Unknown';
            if (!byCustomer[cname]) byCustomer[cname] = [];
            byCustomer[cname].push(inv);
        }

        const acctOpts = accounts.map(a => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join('');

        let html = `
            <div class="page-header">
                <h2>Batch Payment Application</h2>
            </div>
            <form onsubmit="BatchPaymentsPage.save(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Payment Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Deposit To</label>
                        <select name="deposit_to_account_id"><option value="">Undeposited Funds</option>${acctOpts}</select></div>
                    <div class="form-group"><label>Method</label>
                        <select name="method">
                            <option value="check">Check</option><option value="cash">Cash</option>
                            <option value="ach">ACH</option><option value="credit_card">Credit Card</option>
                        </select></div>
                    <div class="form-group"><label>Reference</label>
                        <input name="reference"></div>
                </div>
                <div style="margin:8px 0;"><button type="button" class="btn btn-sm btn-secondary" onclick="BatchPaymentsPage.selectAll()">Select All</button></div>`;

        if (Object.keys(byCustomer).length === 0) {
            html += '<div class="empty-state"><p>No open invoices to pay</p></div>';
        } else {
            html += '<div class="table-container"><table><thead><tr><th scope="col"></th><th scope="col">Invoice</th><th scope="col">Customer</th><th scope="col">Due</th><th scope="col" class="amount">Balance</th><th scope="col" class="amount">Payment</th></tr></thead><tbody>';
            for (const [cname, invs] of Object.entries(byCustomer)) {
                html += `<tr style="background:var(--toolbar-bg);"><td colspan="6" style="font-weight:700;font-size:11px;padding:3px 10px;">${escapeHtml(cname)}</td></tr>`;
                for (const inv of invs) {
                    html += `<tr>
                        <td><input type="checkbox" class="batch-check" data-inv="${inv.id}" data-cust="${inv.customer_id}" data-bal="${inv.balance_due}"></td>
                        <td><strong>${escapeHtml(inv.invoice_number)}</strong></td>
                        <td>${escapeHtml(cname)}</td>
                        <td>${formatDate(inv.due_date)}</td>
                        <td class="amount">${formatCurrency(inv.balance_due)}</td>
                        <td><input type="number" step="0.01" class="batch-amt" data-inv="${inv.id}" data-cust="${inv.customer_id}" value="0" style="width:80px;"></td>
                    </tr>`;
                }
            }
            html += '</tbody></table></div>';
        }

        html += `<div id="batch-total" style="margin-top:12px;font-size:16px;font-weight:700;color:var(--qb-navy);">Total: $0.00</div>
            <div class="form-actions">
                <button type="submit" class="btn btn-primary">Apply Batch Payment</button>
            </div></form>`;

        return html;
    },

    selectAll() {
        $$('.batch-check').forEach(cb => {
            cb.checked = true;
            const inv = cb.dataset.inv;
            const amtInput = $(`.batch-amt[data-inv="${inv}"]`);
            if (amtInput) amtInput.value = cb.dataset.bal;
        });
        BatchPaymentsPage.recalc();
    },

    recalc() {
        let total = 0;
        $$('.batch-amt').forEach(input => { total += parseFloat(input.value) || 0; });
        const el = $('#batch-total');
        if (el) el.textContent = `Total: ${formatCurrency(total)}`;
    },

    async save(e) {
        e.preventDefault();
        const form = e.target;
        const allocations = [];
        $$('.batch-amt').forEach(input => {
            const amt = parseFloat(input.value) || 0;
            if (amt > 0) {
                allocations.push({
                    customer_id: parseInt(input.dataset.cust),
                    invoice_id: parseInt(input.dataset.inv),
                    amount: amt,
                });
            }
        });
        if (allocations.length === 0) { toast('Enter payment amounts', 'error'); return; }

        try {
            const result = await API.post('/batch-payments', {
                date: form.date.value,
                deposit_to_account_id: form.deposit_to_account_id.value ? parseInt(form.deposit_to_account_id.value) : null,
                method: form.method.value,
                reference: form.reference.value || null,
                allocations,
            });
            toast(`${result.payments_created} payment(s) created`);
            App.navigate('#/batch-payments');
        } catch (err) { toast(err.message, 'error'); }
    },
};

// Wire up recalc after page loads
document.addEventListener('click', (e) => {
    if (e.target.classList.contains('batch-check')) {
        const inv = e.target.dataset.inv;
        const amtInput = $(`.batch-amt[data-inv="${inv}"]`);
        if (amtInput) amtInput.value = e.target.checked ? e.target.dataset.bal : '0';
        BatchPaymentsPage.recalc();
    }
});
document.addEventListener('input', (e) => {
    if (e.target.classList.contains('batch-amt')) BatchPaymentsPage.recalc();
});
