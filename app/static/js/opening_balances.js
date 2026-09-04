/**
 * Opening Balances — guided setup without manual journal knowledge.
 * Positive amounts land on each account's normal side; the auto-balance
 * helper posts any difference to a chosen equity account.
 */
const OpeningBalancesPage = {
    _accounts: [],

    async render() {
        const status = await API.get('/opening-balances/status');
        if (!status.ready) {
            return `
                <div class="page-header"><h2>Opening Balances</h2></div>
                <div class="empty-state">
                    <p>Your chart of accounts isn't set up yet.</p>
                    <p style="font-size:12px; margin-top:8px;">
                        Load a chart in <a href="#/settings">Settings</a> or migrate one
                        via <a href="#/migrate">Migrate Data</a>, then come back here.
                    </p>
                </div>`;
        }
        OpeningBalancesPage._accounts = status.accounts;
        const groups = { asset: 'Assets', liability: 'Liabilities', equity: 'Equity' };
        let rows = '';
        for (const [type, label] of Object.entries(groups)) {
            const accts = status.accounts.filter(a => a.account_type === type);
            if (!accts.length) continue;
            rows += `<tr><td colspan="2" style="font-weight:700; background:var(--gray-50);">${label}</td></tr>`;
            rows += accts.map(a => `<tr>
                <td>${escapeHtml(a.account_number || '')} ${escapeHtml(a.name)}</td>
                <td class="amount"><input type="number" step="0.01" class="ob-amount" data-account="${a.id}"
                    style="width:120px; text-align:right;" oninput="OpeningBalancesPage.recalc()"></td>
            </tr>`).join('');
        }
        const equityOpts = status.accounts.filter(a => a.account_type === 'equity')
            .map(a => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join('');
        return `
            <div class="page-header"><h2>Opening Balances</h2></div>
            <div class="card" style="max-width:720px;">
                <div class="form-grid">
                    <div class="form-group"><label>As of Date *</label>
                        <input type="date" id="ob-date" value="${todayISO()}"></div>
                    <div class="form-group"><label>Reference</label>
                        <input type="text" id="ob-reference" placeholder="e.g. OB-2026"></div>
                </div>
                <div class="table-container" style="margin-top:8px;"><table>
                    <thead><tr><th scope="col">Account</th><th scope="col" class="amount">Opening Balance</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>
                <div style="margin-top:12px; display:flex; gap:12px; align-items:center; flex-wrap:wrap;">
                    <div id="ob-difference" style="font-weight:700;">Difference: $0.00</div>
                    <label style="font-size:11px;">
                        <input type="checkbox" id="ob-auto-balance"> Auto-balance to
                    </label>
                    <select id="ob-equity-account">${equityOpts}</select>
                </div>
                <div class="form-actions" style="margin-top:12px;">
                    <button class="btn btn-primary" onclick="OpeningBalancesPage.save()">Post Opening Balances</button>
                </div>
            </div>`;
    },

    recalc() {
        let debit = 0, credit = 0;
        $$('.ob-amount').forEach(input => {
            const amount = parseFloat(input.value) || 0;
            if (!amount) return;
            const acct = OpeningBalancesPage._accounts.find(a => a.id == input.dataset.account);
            const debitNormal = acct.account_type === 'asset';
            const debitSide = amount > 0 ? debitNormal : !debitNormal;
            if (debitSide) debit += Math.abs(amount); else credit += Math.abs(amount);
        });
        const el = $('#ob-difference');
        if (el) el.textContent = `Difference: ${formatCurrency(debit - credit)}`;
    },

    async save() {
        const lines = [];
        $$('.ob-amount').forEach(input => {
            const amount = parseFloat(input.value) || 0;
            if (amount) lines.push({ account_id: parseInt(input.dataset.account), amount });
        });
        const autoBalance = $('#ob-auto-balance').checked;
        try {
            const result = await API.post('/opening-balances', {
                date: $('#ob-date').value,
                reference: $('#ob-reference').value || null,
                lines,
                auto_balance_account_id: autoBalance ? parseInt($('#ob-equity-account').value) : null,
            });
            toast(result.auto_balanced
                ? 'Opening balances posted (equity auto-balanced)'
                : 'Opening balances posted');
            App.navigate('#/accounts');
        } catch (err) { toast(err.message, 'error'); }
    },
};
