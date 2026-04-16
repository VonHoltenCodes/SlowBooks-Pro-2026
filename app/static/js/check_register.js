const CheckRegisterPage = {
    async render() {
        const accounts = await API.get('/accounts?active_only=true&account_type=asset');
        const selectedId = accounts[0]?.id;
        let html = `
            <div class="page-header">
                <h2>Check Register</h2>
            </div>
            <div class="toolbar">
                <select id="check-register-account" onchange="CheckRegisterPage.load(this.value)">
                    ${accounts.map(account => `<option value="${account.id}">${escapeHtml(account.account_number || '')} - ${escapeHtml(account.name)}</option>`).join('')}
                </select>
            </div>
            <div id="check-register-body"></div>`;
        setTimeout(() => { if (selectedId) CheckRegisterPage.load(selectedId); }, 0);
        return html;
    },

    async load(accountId) {
        const data = await API.get(`/banking/check-register?account_id=${accountId}`);
        const body = $('#check-register-body');
        if (!body) return;
        if (!data.entries.length) {
            body.innerHTML = `<div class="empty-state"><p>No posted transactions for this account yet.</p></div>`;
            return;
        }
        body.innerHTML = `
            <div class="card" style="margin-bottom:16px;">
                <div class="card-header">${escapeHtml(data.account_number || '')} - ${escapeHtml(data.account_name || '')}</div>
                <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">Opening balance ${formatCurrency(data.starting_balance || 0)}</div>
            </div>
            <div class="table-container"><table>
                <thead><tr><th>Date</th><th>Description</th><th>Reference</th><th>Source</th><th class="amount">Payment</th><th class="amount">Deposit</th><th class="amount">Balance</th></tr></thead>
                <tbody>
                    ${(data.entries || []).map(entry => `<tr>
                        <td>${formatDate(entry.date)}</td>
                        <td>${escapeHtml(entry.description || '')}</td>
                        <td>${escapeHtml(entry.reference || '')}</td>
                        <td>${escapeHtml(entry.source_type || '')}</td>
                        <td class="amount">${entry.payment ? formatCurrency(entry.payment) : ''}</td>
                        <td class="amount">${entry.deposit ? formatCurrency(entry.deposit) : ''}</td>
                        <td class="amount">${formatCurrency(entry.balance)}</td>
                    </tr>`).join('')}
                </tbody>
            </table></div>`;
    },
};
