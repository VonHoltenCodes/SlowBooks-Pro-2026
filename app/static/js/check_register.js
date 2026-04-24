/**
 * Check Register — Filtered view of bank transactions with running balance
 */
const CheckRegisterPage = {
    async render() {
        const accounts = await API.get('/accounts');
        const bankAccts = accounts.filter(a => a.account_type === 'asset');
        const acctOpts = bankAccts.map(a => `<option value="${a.id}">${escapeHtml(a.account_number)} - ${escapeHtml(a.name)}</option>`).join('');

        let html = `
            <div class="page-header">
                <h2>Check Register</h2>
            </div>
            <div class="toolbar">
                <label style="font-size:10px;font-weight:700;">Account:</label>
                <select id="cr-account" onchange="CheckRegisterPage.load()">
                    ${acctOpts || '<option>No bank accounts</option>'}
                </select>
            </div>
            <div id="cr-results"></div>`;

        // Auto-load first account
        setTimeout(() => CheckRegisterPage.load(), 100);
        return html;
    },

    async load() {
        const accountId = $('#cr-account')?.value;
        if (!accountId) return;

        const data = await API.get(`/banking/check-register?account_id=${accountId}`);
        const container = $('#cr-results');

        if (!data.entries || data.entries.length === 0) {
            container.innerHTML = '<div class="empty-state"><p>No transactions for this account</p></div>';
            return;
        }

        let html = `
            <div style="margin-bottom:8px; font-weight:700;">${escapeHtml(data.account_name)} (${escapeHtml(data.account_number)})</div>
            <div class="table-container"><table>
                <thead><tr>
                    <th>Date</th><th>Description</th><th>Reference</th><th>Type</th>
                    <th class="amount">Payment</th><th class="amount">Deposit</th><th class="amount">Balance</th>
                </tr></thead><tbody>`;

        for (const e of data.entries) {
            html += `<tr>
                <td>${formatDate(e.date)}</td>
                <td>${escapeHtml(e.description)}</td>
                <td>${escapeHtml(e.reference || '')}</td>
                <td>${escapeHtml(e.source_type || '')}</td>
                <td class="amount">${e.payment > 0 ? formatCurrency(e.payment) : ''}</td>
                <td class="amount">${e.deposit > 0 ? formatCurrency(e.deposit) : ''}</td>
                <td class="amount" style="font-weight:700;">${formatCurrency(e.balance)}</td>
            </tr>`;
        }

        html += '</tbody></table></div>';
        container.innerHTML = html;
    },
};
