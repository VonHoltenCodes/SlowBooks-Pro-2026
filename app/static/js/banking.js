/**
 * Bank register + reconcile wizard. The register is one of the oldest
 * ideas in QuickBooks, going back to the Quicken era — the
 * checkbook-style layout is preserved here for nostalgia.
 */
const BankingPage = {
    async render() {
        const [accounts, feed] = await Promise.all([
            API.get('/banking/accounts'),
            API.get('/simplefin/status'),
        ]);
        let html = `
            <div class="page-header">
                <h2>Bank Accounts</h2>
                <button class="btn btn-primary" onclick="BankingPage.showAccountForm()">+ New Bank Account</button>
            </div>`;

        if (accounts.length === 0) {
            html += `<div class="empty-state"><p>No bank accounts yet</p></div>`;
        } else {
            html += `<div class="card-grid">`;
            for (const ba of accounts) {
                html += `<div class="card" style="cursor:pointer" onclick="BankingPage.viewRegister(${ba.id})">
                    <div class="card-header">${escapeHtml(ba.name)}</div>
                    <div class="card-value">${formatCurrency(ba.balance)}</div>
                    <div style="font-size:12px; color:var(--gray-400); margin-top:4px;">
                        ${escapeHtml(ba.bank_name || '')} ${ba.last_four ? '****' + ba.last_four : ''}
                    </div>
                </div>`;
            }
            html += `</div>`;
        }
        html += BankingPage._renderFeedSection(feed, accounts);
        return html;
    },

    // ------------------------------------------------------------------
    // SimpleFIN bank feeds — the user brings their own bridge credential
    // (bridge.simplefin.org); we claim the token once, then sync on click.
    // ------------------------------------------------------------------
    _renderFeedSection(feed, accounts) {
        let body;
        if (!feed.connected) {
            body = `
                <p style="font-size:12px; margin-bottom:8px;">
                    Pull transactions straight from your bank — no file exports.
                    Sign up at <a href="https://bridge.simplefin.org" target="_blank" rel="noopener">bridge.simplefin.org</a>,
                    connect your bank there, then paste your <strong>setup token</strong> below.
                    Your credential stays on this machine; SlowBooks has no middleman server.
                </p>
                <form onsubmit="BankingPage.connectSimpleFIN(event)">
                    <div class="form-group">
                        <label>SimpleFIN setup token</label>
                        <input type="password" id="simplefin-token" required autocomplete="off"
                               placeholder="Paste the one-time setup token">
                    </div>
                    <div class="form-actions">
                        <button type="submit" class="btn btn-primary">Connect</button>
                    </div>
                </form>`;
        } else {
            const options = (sfId) => {
                const mapped = feed.account_map[sfId] || '';
                return ['<option value="">— not imported —</option>']
                    .concat(accounts.map(ba =>
                        `<option value="${ba.id}" ${ba.id === mapped ? 'selected' : ''}>${escapeHtml(ba.name)}</option>`))
                    .join('');
            };
            const rows = feed.accounts.map(a => `<tr>
                    <td>${escapeHtml(a.name)}<div style="font-size:10px; color:var(--gray-400);">${escapeHtml(a.org || '')}</div></td>
                    <td class="amount">${escapeHtml(a.balance)} ${escapeHtml(a.currency)}</td>
                    <td><select data-sfid="${escapeHtml(a.id)}" class="simplefin-map">${options(a.id)}</select></td>
                </tr>`).join('');
            body = `
                <p style="font-size:12px; margin-bottom:8px;">
                    Connected. Choose which SlowBooks bank account each feed lands in,
                    then sync — duplicates are skipped automatically and bank rules apply.
                    ${feed.last_sync ? `Last sync: ${escapeHtml(feed.last_sync.replace('T', ' '))}` : 'Not synced yet.'}
                </p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Bank feed</th><th scope="col" class="amount">Balance</th><th scope="col">Imports into</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>
                <div class="form-actions" style="margin-top:12px;">
                    <button class="btn btn-primary" onclick="BankingPage.syncSimpleFIN()">Sync Now</button>
                    <button class="btn btn-secondary" onclick="BankingPage.disconnectSimpleFIN()">Disconnect</button>
                </div>`;
        }
        return `<div class="card" style="margin-top:16px;">
            <div class="card-header">Bank Feeds (SimpleFIN)</div>${body}</div>`;
    },

    async connectSimpleFIN(e) {
        e.preventDefault();
        const token = $('#simplefin-token').value.trim();
        if (!token) return;
        try {
            const data = await API.post('/simplefin/claim', { setup_token: token });
            toast(`Connected — found ${data.accounts.length} account(s). Now map them below.`);
            App.navigate('#/banking');
        } catch (err) { toast(err.message, 'error'); }
    },

    async saveSimpleFINMap() {
        const mapping = {};
        document.querySelectorAll('.simplefin-map').forEach(sel => {
            mapping[sel.dataset.sfid] = sel.value ? parseInt(sel.value, 10) : 0;
        });
        await API.post('/simplefin/map', { mapping });
    },

    async syncSimpleFIN() {
        try {
            await BankingPage.saveSimpleFINMap();
            const r = await API.post('/simplefin/sync');
            toast(`Synced: ${r.imported} new, ${r.skipped} duplicates skipped`);
            if (r.warnings.length) toast(r.warnings[0], 'error');
            App.navigate('#/banking');
        } catch (err) { toast(err.message, 'error'); }
    },

    async disconnectSimpleFIN() {
        if (!confirm('Disconnect the SimpleFIN bank feed? Imported transactions are kept.')) return;
        try {
            await API.post('/simplefin/disconnect');
            toast('Bank feed disconnected');
            App.navigate('#/banking');
        } catch (err) { toast(err.message, 'error'); }
    },

    async viewRegister(bankAccountId) {
        const [ba, txns] = await Promise.all([
            API.get(`/banking/accounts/${bankAccountId}`),
            API.get(`/banking/transactions?bank_account_id=${bankAccountId}`),
        ]);

        let html = `
            <div class="page-header">
                <h2>${escapeHtml(ba.name)} Register</h2>
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="App.navigate('#/banking')">Back</button>
                    <button class="btn btn-primary" onclick="BankingPage.showTxnForm(${bankAccountId})">+ Transaction</button>
                    <button class="btn btn-secondary" onclick="BankingPage.showOFXImport(${bankAccountId})">Import OFX/QFX/CSV</button>
                    <button class="btn btn-secondary" onclick="BankingPage.startReconcile(${bankAccountId})">Reconcile</button>
                </div>
            </div>
            <div class="card" style="margin-bottom:16px;">
                <div class="card-header">Current Balance</div>
                <div class="card-value">${formatCurrency(ba.balance)}</div>
            </div>`;

        if (txns.length === 0) {
            html += `<div class="empty-state"><p>No transactions</p></div>`;
        } else {
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th scope="col">Date</th><th scope="col">Payee</th><th scope="col">Description</th><th scope="col">Check #</th>
                    <th scope="col" class="amount">Amount</th><th scope="col">Reconciled</th>
                </tr></thead><tbody>`;
            for (const t of txns) {
                const cls = t.amount >= 0 ? 'color:var(--success)' : 'color:var(--danger)';
                html += `<tr>
                    <td>${formatDate(t.date)}</td>
                    <td>${escapeHtml(t.payee || '')}</td>
                    <td>${escapeHtml(t.description || '')}</td>
                    <td>${escapeHtml(t.check_number || '')}</td>
                    <td class="amount" style="${cls}">${formatCurrency(t.amount)}</td>
                    <td>${t.reconciled ? 'R' : ''}</td>
                </tr>`;
            }
            html += `</tbody></table></div>`;
        }

        $('#page-content').innerHTML = html;
    },

    async showAccountForm() {
        const coaAccounts = await API.get('/accounts?account_type=asset');
        const opts = coaAccounts.map(a => `<option value="${a.id}">${a.account_number} - ${escapeHtml(a.name)}</option>`).join('');

        openModal('New Bank Account', `
            <form onsubmit="BankingPage.saveAccount(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Account Name *</label>
                        <input name="name" required></div>
                    <div class="form-group"><label>Linked COA Account</label>
                        <select name="account_id"><option value="">--</option>${opts}</select></div>
                    <div class="form-group"><label>Bank Name</label>
                        <input name="bank_name"></div>
                    <div class="form-group"><label>Last 4 Digits</label>
                        <input name="last_four" maxlength="4"></div>
                    <div class="form-group"><label>Opening Balance</label>
                        <input name="balance" type="number" step="0.01" value="0"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create Account</button>
                </div>
            </form>`);
    },

    async saveAccount(e) {
        e.preventDefault();
        const form = e.target;
        const data = {
            name: form.name.value,
            account_id: form.account_id.value ? parseInt(form.account_id.value) : null,
            bank_name: form.bank_name.value || null,
            last_four: form.last_four.value || null,
            balance: parseFloat(form.balance.value) || 0,
        };
        try {
            await API.post('/banking/accounts', data);
            toast('Bank account created');
            closeModal();
            App.navigate('#/banking');
        } catch (err) { toast(err.message, 'error'); }
    },

    async showTxnForm(bankAccountId) {
        const accounts = await API.get('/accounts');
        const catOpts = accounts
            .filter(a => ['expense','income','asset','liability'].includes(a.account_type))
            .map(a => `<option value="${a.id}">${a.account_number} - ${escapeHtml(a.name)}</option>`).join('');

        openModal('New Transaction', `
            <form onsubmit="BankingPage.saveTxn(event, ${bankAccountId})">
                <div class="form-grid">
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Amount * (negative=withdrawal)</label>
                        <input name="amount" type="number" step="0.01" required></div>
                    <div class="form-group"><label>Payee</label>
                        <input name="payee"></div>
                    <div class="form-group"><label>Check #</label>
                        <input name="check_number"></div>
                    <div class="form-group full-width"><label>Description</label>
                        <input name="description"></div>
                    <div class="form-group"><label>Category</label>
                        <select name="category_account_id"><option value="">--</option>${catOpts}</select></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save Transaction</button>
                </div>
            </form>`);
    },

    async saveTxn(e, bankAccountId) {
        e.preventDefault();
        const form = e.target;
        const data = {
            bank_account_id: bankAccountId,
            date: form.date.value,
            amount: parseFloat(form.amount.value),
            payee: form.payee.value || null,
            description: form.description.value || null,
            check_number: form.check_number.value || null,
            category_account_id: form.category_account_id.value ? parseInt(form.category_account_id.value) : null,
        };
        try {
            await API.post('/banking/transactions', data);
            toast('Transaction saved');
            closeModal();
            BankingPage.viewRegister(bankAccountId);
        } catch (err) { toast(err.message, 'error'); }
    },

    // Reconciliation
    async startReconcile(bankAccountId) {
        openModal('Begin Reconciliation', `
            <form onsubmit="BankingPage.createReconciliation(event, ${bankAccountId})">
                <p style="margin-bottom:12px; font-size:11px; color:var(--gray-500);">
                    Enter the ending date and balance from your bank statement.
                </p>
                <div class="form-grid">
                    <div class="form-group"><label>Statement Date *</label>
                        <input name="statement_date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Statement Ending Balance *</label>
                        <input name="statement_balance" type="number" step="0.01" required></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Begin Reconciliation</button>
                </div>
            </form>`);
    },

    async createReconciliation(e, bankAccountId) {
        e.preventDefault();
        const form = e.target;
        try {
            const recon = await API.post('/banking/reconciliations', {
                bank_account_id: bankAccountId,
                statement_date: form.statement_date.value,
                statement_balance: parseFloat(form.statement_balance.value),
            });
            closeModal();
            BankingPage.showReconcileView(recon.id);
        } catch (err) { toast(err.message, 'error'); }
    },

    async showReconcileView(reconId) {
        const data = await API.get(`/banking/reconciliations/${reconId}/transactions`);
        let rows = data.transactions.map(t => {
            const cls = t.reconciled ? 'style="background:var(--primary-light);"' : '';
            const amtCls = t.amount >= 0 ? 'color:var(--success)' : 'color:var(--danger)';
            return `<tr ${cls}>
                <td><input type="checkbox" ${t.reconciled ? 'checked' : ''}
                    onchange="BankingPage.toggleCleared(${reconId}, ${t.id}, this)"></td>
                <td>${formatDate(t.date)}</td>
                <td>${escapeHtml(t.payee || t.description || '')}</td>
                <td>${escapeHtml(t.check_number || '')}</td>
                <td class="amount" style="${amtCls}">${formatCurrency(t.amount)}</td>
            </tr>`;
        }).join('');

        const diffColor = Math.abs(data.difference) < 0.01 ? 'var(--success)' : 'var(--danger)';

        $('#page-content').innerHTML = `
            <div class="page-header">
                <h2>Reconcile Account</h2>
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="App.navigate('#/banking')">Cancel</button>
                    <button class="btn btn-primary" id="recon-finish-btn" onclick="BankingPage.finishReconcile(${reconId})"
                        ${Math.abs(data.difference) < 0.01 ? '' : 'disabled'}>Finish Reconciliation</button>
                </div>
            </div>
            <div class="card-grid" style="margin-bottom:16px;">
                <div class="card"><div class="card-header">Statement Balance</div>
                    <div class="card-value">${formatCurrency(data.statement_balance)}</div></div>
                <div class="card"><div class="card-header">Cleared Balance</div>
                    <div class="card-value" id="recon-cleared">${formatCurrency(data.cleared_total)}</div></div>
                <div class="card"><div class="card-header">Difference</div>
                    <div class="card-value" id="recon-diff" style="color:${diffColor}">${formatCurrency(data.difference)}</div>
                    <div style="font-size:11px; font-weight:600;" role="status">${Math.abs(data.difference) < 0.01 ? '\u2713 Balanced' : '\u26a0 Out of balance'}</div></div>
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col" style="width:30px;"></th><th scope="col">Date</th><th scope="col">Payee / Description</th><th scope="col">Check #</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${rows || '<tr><td colspan="5" style="text-align:center;">No transactions</td></tr>'}</tbody>
            </table></div>`;
    },

    async toggleCleared(reconId, txnId, checkbox) {
        try {
            await API.post(`/banking/reconciliations/${reconId}/toggle/${txnId}`);
            // Refresh the view to update totals
            BankingPage.showReconcileView(reconId);
        } catch (err) {
            checkbox.checked = !checkbox.checked;
            toast(err.message, 'error');
        }
    },

    async finishReconcile(reconId) {
        if (!confirm('Mark this reconciliation as complete?')) return;
        try {
            await API.post(`/banking/reconciliations/${reconId}/complete`);
            toast('Reconciliation completed');
            App.navigate('#/banking');
        } catch (err) { toast(err.message, 'error'); }
    },

    // Feature 18: OFX/QFX Import (+ CSV: Chase checking/credit, PayPal)
    async showOFXImport(bankAccountId) {
        openModal('Import Bank File', `
            <form onsubmit="BankingPage.previewOFX(event, ${bankAccountId})">
                <div class="form-group">
                    <label>Select an OFX/QFX file, or a CSV export (Chase checking, Chase credit, PayPal)</label>
                    <input type="file" name="file" accept=".ofx,.qfx,.csv" required id="ofx-file">
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Preview Import</button>
                </div>
            </form>
            <div id="ofx-preview" style="margin-top:12px;"></div>`);
    },

    _isCsvFile(file) {
        return /\.csv$/i.test(file.name || '');
    },

    async previewOFX(e, bankAccountId) {
        e.preventDefault();
        const file = $('#ofx-file').files[0];
        if (!file) return;
        const isCsv = BankingPage._isCsvFile(file);
        const formData = new FormData();
        formData.append('file', file);
        try {
            const endpoint = isCsv ? '/api/bank-import/preview-csv' : '/api/bank-import/preview';
            const resp = await fetch(endpoint, { method: 'POST', body: formData });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Parse failed');
            if (isCsv && data.error) throw new Error(data.error);
            BankingPage._ofxData = data;
            let rows = data.transactions.map((t, i) => `<tr>
                <td>${escapeHtml(t.date || '')}</td>
                <td>${escapeHtml(t.payee || '')}</td>
                <td class="amount" style="${t.amount >= 0 ? 'color:var(--success)' : 'color:var(--danger)'}">${formatCurrency(t.amount)}</td>
                <td>${escapeHtml(isCsv ? (t.description || '') : (t.fitid || ''))}</td>
            </tr>`).join('');
            $('#ofx-preview').innerHTML = `
                <div style="margin-bottom:8px; font-size:11px;">
                    <strong>${data.transactions.length}</strong> transactions found.
                    ${isCsv && data.format ? `Format: ${escapeHtml(data.format)}` : ''}
                    ${data.account_id ? `Account: ${escapeHtml(data.account_id)}` : ''}
                </div>
                <div class="table-container" style="max-height:300px; overflow-y:auto;"><table>
                    <thead><tr><th scope="col">Date</th><th scope="col">Payee</th><th scope="col" class="amount">Amount</th><th scope="col">${isCsv ? 'Description' : 'FITID'}</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>
                <div class="form-actions" style="margin-top:12px;">
                    <button class="btn btn-primary" onclick="BankingPage.confirmOFXImport(${bankAccountId})">Import ${data.transactions.length} Transactions</button>
                </div>`;
        } catch (err) {
            $('#ofx-preview').innerHTML = `<div style="color:var(--danger); font-size:11px;">${escapeHtml(err.message)}</div>`;
        }
    },

    async confirmOFXImport(bankAccountId) {
        try {
            const file = $('#ofx-file').files[0];
            const isCsv = BankingPage._isCsvFile(file);
            const formData = new FormData();
            formData.append('file', file);
            const endpoint = isCsv
                ? `/api/bank-import/import-csv/${bankAccountId}`
                : `/api/bank-import/import/${bankAccountId}`;
            const resp = await fetch(endpoint, { method: 'POST', body: formData });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Import failed');
            toast(`Imported ${data.imported} transactions (${data.skipped} duplicates skipped)`);
            closeModal();
            BankingPage.viewRegister(bankAccountId);
        } catch (err) { toast(err.message, 'error'); }
    },
};
