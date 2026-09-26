/**
 * Banking — the register is the ledger account (issue #114).
 *
 * A bank or credit-card account is a chart account flagged as such; its
 * register is the ledger's lines with a running balance; a register entry
 * posts; statement lines from a feed or a file wait in "To review" until
 * they are matched to a posting or added as one; reconciliation ticks
 * ledger lines; a card is paid with a transfer.
 *
 * Sign rule, once: an amount > 0 goes INTO the account (a deposit, a card
 * payment), < 0 comes OUT (a payment, a card charge).
 */
const BankingPage = {
    _kindLabel(kind) { return kind === 'credit_card' ? 'Credit card' : 'Bank'; },
    _cols(kind) { return kind === 'credit_card' ? ['Charge', 'Payment'] : ['Payment', 'Deposit']; },

    // ------------------------------------------------------------------
    // Overview
    // ------------------------------------------------------------------
    async render() {
        const [rows, feed, feeds] = await Promise.all([
            API.get('/banking/overview'),
            API.get('/simplefin/status'),
            API.get('/banking/accounts'),
        ]);
        let html = `
            <div class="page-header">
                <h2>Banking</h2>
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="BankingPage.showTransfers()">Transfers…</button>
                    <button class="btn btn-secondary" onclick="BankingPage.showTransferForm()">Transfer</button>
                    <button class="btn btn-primary" onclick="BankingPage.showAccountForm()">+ New Bank Account</button>
                </div>
            </div>`;
        html += BankingPage._legacyBanners(rows);
        if (rows.length === 0) {
            html += `<div class="empty-state"><p>No bank or credit-card accounts yet. Add one, or flag an existing chart account as a bank account.</p></div>`;
        } else {
            html += `<div class="card-grid">`;
            for (const a of rows) {
                const owed = a.bank_kind === 'credit_card';
                html += `<div class="card" style="cursor:pointer" onclick="App.navigate('#/banking/${a.account_id}')">
                    <div class="card-header">${escapeHtml(a.name)} <span style="font-size:10px; color:var(--gray-400);">${escapeHtml(a.account_number || '')} · ${BankingPage._kindLabel(a.bank_kind)}</span></div>
                    <div class="card-value">${formatCurrency(a.balance)}${owed ? ' <span style="font-size:11px; color:var(--gray-500);">owed</span>' : ''}</div>
                    <div style="font-size:12px; color:var(--gray-400); margin-top:4px;">
                        ${a.feed ? `${escapeHtml(a.feed.bank_name || a.feed.name)} ${a.feed.last_four ? '****' + a.feed.last_four : ''}` : 'No bank feed'}
                        ${a.to_review ? `<span style="color:var(--qb-gold); font-weight:700;"> · ${a.to_review} to review</span>` : ''}
                        ${a.last_reconciled ? ` · reconciled ${formatDate(a.last_reconciled)}` : ''}
                    </div>
                </div>`;
            }
            html += `</div>`;
        }
        html += BankingPage._renderFeedSection(feed, feeds);
        return html;
    },

    _legacyBanners(rows) {
        return rows.filter(a => a.feed && a.feed.legacy_balance !== null && a.feed.legacy_balance !== undefined).map(a => `
            <div class="card" style="margin-bottom:12px; border-left:4px solid var(--qb-gold);">
                <div style="font-size:12px;">
                    <strong>${escapeHtml(a.name)}</strong>: the register balance before 2.10, ${formatCurrency(a.feed.legacy_balance)},
                    is not in the ledger. Post it as the account's opening balance, or dismiss it if the ledger already carries it.
                </div>
                <div class="form-actions" style="margin-top:8px;">
                    <button class="btn btn-primary btn-sm" onclick="BankingPage.postLegacy(${a.feed.bank_account_id})">Post as opening balance</button>
                    <button class="btn btn-secondary btn-sm" onclick="BankingPage.dismissLegacy(${a.feed.bank_account_id})">Dismiss</button>
                </div>
            </div>`).join('');
    },

    async postLegacy(feedId) {
        const d = prompt('Post the opening balance as of which date? (YYYY-MM-DD)', todayISO());
        if (!d) return;
        try {
            await API.post(`/banking/accounts/${feedId}/post-legacy-balance`, { date: d });
            toast('Opening balance posted');
            App.navigate('#/banking');
        } catch (err) { toast(err.message, 'error'); }
    },

    async dismissLegacy(feedId) {
        if (!confirm('Dismiss the pre-2.10 balance? It will not be posted.')) return;
        try {
            await API.put(`/banking/accounts/${feedId}`, { legacy_balance: null });
            App.navigate('#/banking');
        } catch (err) { toast(err.message, 'error'); }
    },

    // ------------------------------------------------------------------
    // SimpleFIN bank feeds — the user brings their own credential from any
    // SimpleFIN server (bridge.simplefin.org or another provider); we claim
    // the token once, then sync on click.
    // ------------------------------------------------------------------
    _renderFeedSection(feed, feeds) {
        let body;
        if (!feed.connected) {
            body = `
                <p style="font-size:12px; margin-bottom:8px;">
                    Pull transactions straight from your bank — no file exports.
                    Connect your bank at any SimpleFIN provider — the reference one is
                    <a href="https://bridge.simplefin.org" target="_blank" rel="noopener">bridge.simplefin.org</a> —
                    then paste the <strong>setup token</strong> it gives you below.
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
                    .concat(feeds.map(ba =>
                        `<option value="${ba.id}" ${ba.id === mapped ? 'selected' : ''}>${escapeHtml(ba.name)}${ba.account_name ? ' → ' + escapeHtml(ba.account_name) : ''}</option>`))
                    .join('');
            };
            const rows = feed.accounts.map(a => `<tr>
                    <td>${escapeHtml(a.name)}<div style="font-size:10px; color:var(--gray-400);">${escapeHtml(a.org || '')}</div></td>
                    <td class="amount">${escapeHtml(a.balance)} ${escapeHtml(a.currency)}</td>
                    <td><select data-sfid="${escapeHtml(a.id)}" class="simplefin-map">${options(a.id)}</select></td>
                </tr>`).join('');
            body = `
                <p style="font-size:12px; margin-bottom:8px;">
                    Connected. Choose which SlowBooks bank account each feed lands in, then sync —
                    duplicates are skipped, bank rules suggest categories, and matches to postings you
                    already made are found. Everything else waits in the account's <em>To review</em> list.
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
            if (r.warnings && r.warnings.length) toast(r.warnings[0], 'error');
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

    // ------------------------------------------------------------------
    // New bank account: an existing chart account flagged bank/card, or a
    // new one; plus its feed identity and an opening balance.
    // ------------------------------------------------------------------
    async showAccountForm() {
        const [bankAccts, feeds] = await Promise.all([
            API.get('/accounts?bank=1&active_only=true'),
            API.get('/banking/accounts'),
        ]);
        const taken = new Set(feeds.map(f => f.account_id));
        const free = bankAccts.filter(a => !taken.has(a.id));
        const opts = free.map(a => `<option value="${a.id}">${escapeHtml(a.account_number || '')} - ${escapeHtml(a.name)} (${BankingPage._kindLabel(a.bank_kind)})</option>`).join('');
        openModal('New Bank Account', `
            <form onsubmit="BankingPage.saveAccount(event)">
                <div class="form-grid">
                    <div class="form-group full-width"><label>Ledger account *</label>
                        <select name="account_id" onchange="BankingPage._toggleNewAcct(this); BankingPage._showLedgerBalance(this.form)">
                            ${opts}<option value="__new__">+ Create a new chart account…</option>
                        </select></div>
                    <div id="new-acct-fields" class="form-group full-width" style="display:${free.length ? 'none' : 'block'};">
                        <div class="form-grid">
                            <div class="form-group"><label>Account name</label><input name="new_name"></div>
                            <div class="form-group"><label>Account number</label><input name="new_number" placeholder="e.g. 1020"></div>
                            <div class="form-group"><label>Kind</label>
                                <select name="new_kind"><option value="bank">Bank (asset)</option><option value="credit_card">Credit card (liability)</option></select></div>
                        </div>
                    </div>
                    <div class="form-group"><label>Feed / statement name *</label>
                        <input name="name" required placeholder="e.g. Numerica Operating"></div>
                    <div class="form-group"><label>Bank name</label>
                        <input name="bank_name"></div>
                    <div class="form-group"><label>Last 4 digits</label>
                        <input name="last_four" maxlength="4"></div>
                    <div class="form-group"><label>Statement balance</label>
                        <input name="opening_balance" type="number" step="0.01" value="0" oninput="this.dataset.touched = '1'">
                        <div style="font-size:10px; color:var(--gray-500);">What the statement says on the As-of date: cash in the bank, or the amount owed on a card. Posted against the opening-balance offset account (3900) — unless the books already carry this account, when only a difference you confirm is posted.</div>
                        <div id="acct-ledger-note" style="font-size:11px; margin-top:4px;" role="status"></div></div>
                    <div class="form-group"><label>As of</label>
                        <input name="opening_date" type="date" value="${todayISO()}" onchange="BankingPage._showLedgerBalance(this.form)"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create</button>
                </div>
            </form>`);
        const sel = document.querySelector('select[name=account_id]');
        if (!free.length) { if (sel) sel.value = '__new__'; }
        if (sel) BankingPage._showLedgerBalance(sel.form);
    },

    _toggleNewAcct(sel) {
        const box = $('#new-acct-fields');
        if (box) box.style.display = sel.value === '__new__' ? 'block' : 'none';
    },

    // What the books already say about the chosen account on the As-of
    // date, shown beside the statement balance. Savings that already held
    // $300 from a transfer took a second $300 when the statement balance
    // was posted blind (exploratory 2.17.3, W-H6): the ledger's figure is
    // the starting value now, so a matching statement posts nothing.
    async _showLedgerBalance(form) {
        const note = $('#acct-ledger-note');
        if (!form || !note) return;
        const accountId = form.account_id.value;
        note.textContent = '';
        if (!accountId || accountId === '__new__') return;
        const asOf = form.opening_date.value || todayISO();
        try {
            const led = await API.get(`/banking/ledger-balance?account_id=${accountId}&as_of=${asOf}`);
            if (form.account_id.value !== accountId) return;  // changed meanwhile
            if (!led.has_postings) return;
            note.textContent = `The books already show ${formatCurrency(led.balance)} in ${led.account_name} on ${formatDate(led.as_of)}. Enter the statement's figure: if it differs, you'll be asked before the difference is posted.`;
            if (!form.opening_balance.dataset.touched) form.opening_balance.value = Number(led.balance).toFixed(2);
        } catch (err) { /* the note is a courtesy; the server still guards the save */ }
    },

    async saveAccount(e) {
        e.preventDefault();
        const form = e.target;
        try {
            let accountId = form.account_id.value;
            if (accountId === '__new__') {
                const kind = form.new_kind.value;
                const created = await API.post('/accounts', {
                    name: form.new_name.value,
                    account_number: form.new_number.value || null,
                    account_type: kind === 'credit_card' ? 'liability' : 'asset',
                    bank_kind: kind,
                });
                accountId = created.id;
            }
            const body = {
                name: form.name.value,
                account_id: parseInt(accountId, 10),
                bank_name: form.bank_name.value || null,
                last_four: form.last_four.value || null,
                opening_balance: form.opening_balance.value || '0',
                opening_date: form.opening_date.value || null,
            };
            try {
                await API.post('/banking/accounts', body);
            } catch (err) {
                // The ledger already carries this account and the statement
                // differs: say both figures, and post only the difference
                // when the user says so.
                if (!(err.status === 409 && err.detail && err.detail.code === 'ledger_has_balance')) throw err;
                if (!confirm(`${err.detail.message}\n\nPost the ${formatCurrency(Number(err.detail.difference))} difference now?`)) {
                    toast('Nothing was created. Enter the balance the books already show to post nothing.', 'error');
                    return;
                }
                await API.post('/banking/accounts', { ...body, post_difference: true });
            }
            toast('Bank account created');
            closeModal();
            App.navigate('#/banking');
        } catch (err) { toast(err.message, 'error'); }
    },

    // ------------------------------------------------------------------
    // The register — route #/banking/:accountId
    // ------------------------------------------------------------------
    async renderRegister(accountId) {
        const id = parseInt(accountId, 10);
        const [reg, overview] = await Promise.all([
            API.get(`/banking/check-register?account_id=${id}`),
            API.get('/banking/overview'),
        ]);
        const info = overview.find(a => a.account_id === id) || {};
        const feed = info.feed || null;
        const kind = reg.bank_kind;
        const [outLabel, inLabel] = BankingPage._cols(kind);
        let review = [];
        if (feed) review = await API.get(`/banking/transactions?bank_account_id=${feed.bank_account_id}&status=unmatched`);
        BankingPage._ctx = { accountId: id, feedId: feed ? feed.bank_account_id : null, kind };

        let html = `
            <div class="page-header">
                <h2>${escapeHtml(reg.account_name)} <span style="font-size:11px; color:var(--gray-400);">${escapeHtml(reg.account_number || '')} · ${BankingPage._kindLabel(kind)}</span></h2>
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="App.navigate('#/banking')">Back</button>
                    <button class="btn btn-primary" onclick="BankingPage.showEntryForm(${id})">+ Entry</button>
                    <button class="btn btn-secondary" onclick="BankingPage.showTransferForm(${id})">Transfer</button>
                    <button class="btn btn-secondary" onclick="BankingPage.showOFXImport(${id})">Import file</button>
                    ${feed ? `<button class="btn btn-secondary" onclick="BankingPage.findMatches(${feed.bank_account_id}, ${id})">Find matches</button>` : ''}
                    <button class="btn btn-secondary" onclick="BankingPage.startReconcile(${id})">Reconcile</button>
                </div>
            </div>
            <div class="card-grid" style="margin-bottom:16px;">
                <div class="card"><div class="card-header">${kind === 'credit_card' ? 'Amount owed' : 'Balance'}</div>
                    <div class="card-value">${formatCurrency(reg.balance)}</div></div>
                <div class="card"><div class="card-header">To review</div>
                    <div class="card-value">${review.length}</div>
                    <div style="font-size:11px; color:var(--gray-500);">${feed ? 'statement lines not yet in the books' : 'no bank feed on this account'}</div></div>
                ${info.last_reconciled ? `<div class="card"><div class="card-header">Last reconciled</div><div class="card-value" style="font-size:16px;">${formatDate(info.last_reconciled)}</div></div>` : ''}
            </div>`;

        if (review.length) html += BankingPage._reviewPanel(review, feed.bank_account_id, id, kind);

        if (!reg.entries.length) {
            html += `<div class="empty-state"><p>Nothing posted to this account yet</p></div>`;
        } else {
            const rows = reg.entries.slice().reverse().map(e => `
                <tr style="${e.voided ? 'color:var(--gray-400); text-decoration:line-through;' : ''}">
                    <td>${formatDate(e.date)}</td>
                    <td>${escapeHtml(e.payee || '')}</td>
                    <td>${e.source_link ? `<a href="${escapeHtml(e.source_link)}">${escapeHtml(e.description)}</a>` : escapeHtml(e.description)}</td>
                    <td>${escapeHtml(e.reference || '')}</td>
                    <td style="font-size:10px; color:var(--gray-500);">${escapeHtml((e.source_type || '').replace(/_/g, ' '))}</td>
                    <td class="amount">${e.payment > 0 ? formatCurrency(e.payment) : ''}</td>
                    <td class="amount">${e.deposit > 0 ? formatCurrency(e.deposit) : ''}</td>
                    <td class="amount" style="font-weight:700;">${formatCurrency(e.balance)}</td>
                    <td style="text-align:center;">${e.reconciliation_id ? 'R' : (e.cleared ? '✓' : '')}</td>
                    <td>${e.voidable ? `<button class="btn btn-sm btn-secondary" onclick="BankingPage.voidEntry('${e.source_type}', ${e.transaction_id}, ${id})">Void</button>` : ''}</td>
                </tr>`).join('');
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th scope="col">Date</th><th scope="col">Payee</th><th scope="col">Description</th><th scope="col">Ref #</th><th scope="col">Type</th>
                    <th scope="col" class="amount">${outLabel}</th><th scope="col" class="amount">${inLabel}</th><th scope="col" class="amount">Balance</th>
                    <th scope="col" title="✓ cleared · R reconciled">✓</th><th scope="col"></th>
                </tr></thead><tbody>${rows}</tbody></table></div>`;
        }
        return html;
    },

    _reviewPanel(review, feedId, accountId, kind) {
        const rows = review.map(t => `
            <tr>
                <td>${formatDate(t.date)}</td>
                <td>${escapeHtml(t.payee || '')}<div style="font-size:10px; color:var(--gray-400);">${escapeHtml(t.description || '')}</div></td>
                <td class="amount" style="${t.amount >= 0 ? 'color:var(--success)' : 'color:var(--danger)'}">${formatCurrency(t.amount)}</td>
                <td><select id="cat-${t.id}" class="review-cat" data-current="${t.category_account_id || ''}" aria-label="Category" onchange="BankingPage.setCategory(${t.id}, this)"><option value="">${t.category_name ? escapeHtml(t.category_name) : 'Pick a category…'}</option></select></td>
                <td style="white-space:nowrap;">
                    <button class="btn btn-sm btn-primary" onclick="BankingPage.addLine(${t.id}, ${accountId})">Add</button>
                    <button class="btn btn-sm btn-secondary" onclick="BankingPage.showMatch(${t.id}, ${accountId})">Match</button>
                    <button class="btn btn-sm btn-secondary" onclick="BankingPage.excludeLine(${t.id}, ${accountId})">Exclude</button>
                </td>
            </tr>`).join('');
        setTimeout(() => BankingPage._fillCategorySelects(kind), 0);
        return `<div class="card" style="margin-bottom:16px; border-left:4px solid var(--qb-gold);">
            <div class="card-header">To review — ${review.length} statement line${review.length === 1 ? '' : 's'} not in the books
                <span style="float:right;">
                    <button class="btn btn-sm btn-secondary" onclick="BankingPage.addAll(${feedId}, ${accountId})">Add all categorised</button>
                </span>
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Date</th><th scope="col">Bank says</th><th scope="col" class="amount">Amount</th><th scope="col">Category</th><th scope="col"></th></tr></thead>
                <tbody>${rows}</tbody></table></div>
            <div style="font-size:10px; color:var(--gray-500); margin-top:6px;">
                A category you pick is kept on the line. <strong>Add</strong> posts the line with its category; <strong>Add all categorised</strong> posts every line that has one.
                <strong>Match</strong> links it to something you already entered.
                A ${kind === 'credit_card' ? 'card payment' : 'transfer'} is a line whose category is another bank or card account.
            </div>
        </div>`;
    },

    async _fillCategorySelects(kind) {
        const accounts = await API.get('/accounts?active_only=true');
        const usable = accounts.filter(a => ['expense', 'income', 'cogs', 'asset', 'liability', 'equity'].includes(a.account_type));
        document.querySelectorAll('select.review-cat').forEach(sel => {
            const current = sel.dataset.current;
            sel.innerHTML = '<option value="">Pick a category…</option>' + usable.map(a =>
                `<option value="${a.id}" ${String(a.id) === current ? 'selected' : ''}>${escapeHtml(a.account_number || '')} - ${escapeHtml(a.name)}${a.bank_kind ? ' (' + BankingPage._kindLabel(a.bank_kind).toLowerCase() + ')' : ''}</option>`).join('');
        });
    },

    async addLine(lineId, accountId) {
        const sel = $(`#cat-${lineId}`);
        const body = {};
        if (sel && sel.value) body.category_account_id = parseInt(sel.value, 10);
        try {
            await API.post(`/banking/transactions/${lineId}/add`, body);
            toast('Added to the books');
            App.navigate(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async excludeLine(lineId, accountId) {
        try {
            await API.post(`/banking/transactions/${lineId}/exclude`);
            App.navigate(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async restoreLine(lineId, accountId) {
        try {
            await API.post(`/banking/transactions/${lineId}/restore`);
            App.navigate(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async unmatchLine(lineId, accountId) {
        try {
            await API.post(`/banking/transactions/${lineId}/unmatch`);
            App.navigate(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    // A category picked in a line's dropdown is saved as it is picked, so
    // "Add all categorised" posts it and a reload still shows it. Before,
    // a pick lived only in the dropdown until that line's own Add: Add all
    // posted just the rule-categorised lines, and reloading lost the rest
    // (exploratory 2.17.3, W-M12).
    _pendingCategories: new Set(),

    setCategory(lineId, sel) {
        const value = sel.value ? parseInt(sel.value, 10) : null;
        const saving = API.request('PATCH', `/banking/transactions/${lineId}`, { category_account_id: value })
            .then(() => { sel.dataset.current = value ? String(value) : ''; })
            .catch(err => { sel.value = sel.dataset.current || ''; toast(err.message, 'error'); });
        BankingPage._pendingCategories.add(saving);
        saving.finally(() => BankingPage._pendingCategories.delete(saving));
        return saving;
    },

    async addAll(feedId, accountId) {
        try {
            // A pick made a moment ago may still be on its way.
            await Promise.allSettled([...BankingPage._pendingCategories]);
            const r = await API.post(`/banking/accounts/${feedId}/feed/add-all`);
            toast(`Added ${r.added}${r.skipped.length ? `, skipped ${r.skipped.length}: ${r.skipped[0].reason}` : ''}`);
            App.navigate(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async findMatches(feedId, accountId) {
        try {
            const r = await API.post(`/banking/accounts/${feedId}/feed/auto-match`);
            toast(`Matched ${r.matched} statement line${r.matched === 1 ? '' : 's'}`);
            App.navigate(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async showMatch(lineId, accountId) {
        try {
            const cands = await API.get(`/banking/transactions/${lineId}/candidates`);
            const rows = cands.map(c => `<tr>
                <td>${formatDate(c.date)} <span style="font-size:10px; color:var(--gray-400);">${c.days_off ? c.days_off + 'd off' : 'same day'}</span></td>
                <td>${escapeHtml(c.payee || c.description || '')}</td>
                <td>${escapeHtml(c.reference || '')}</td>
                <td style="font-size:10px;">${escapeHtml((c.source_type || '').replace(/_/g, ' '))}</td>
                <td><button class="btn btn-sm btn-primary" onclick="BankingPage.matchLine(${lineId}, ${c.line_id}, ${accountId})">Match</button></td>
            </tr>`).join('');
            openModal('Match to a posting', rows
                ? `<div class="table-container"><table><thead><tr><th scope="col">Date</th><th scope="col">Payee / description</th><th scope="col">Ref #</th><th scope="col">Type</th><th scope="col"></th></tr></thead><tbody>${rows}</tbody></table></div>`
                : `<p>Nothing in the books has this amount within 30 days. Use <strong>Add</strong> to post it.</p>`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async matchLine(lineId, ledgerLineId, accountId) {
        try {
            await API.post(`/banking/transactions/${lineId}/match`, { line_id: ledgerLineId });
            toast('Matched');
            closeModal();
            App.navigate(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async voidEntry(sourceType, txnId, accountId) {
        if (!confirm('Void this entry? A reversing entry is posted; the original stays in the ledger.')) return;
        const path = sourceType === 'transfer' ? `/transfers/${txnId}/void`
            : sourceType === 'cc_charge' ? `/cc-charges/${txnId}/void`
            : `/banking/entries/${txnId}/void`;
        try {
            await API.post(path);
            toast('Voided');
            App.navigate(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    // ------------------------------------------------------------------
    // + Entry (posts) and Transfer
    // ------------------------------------------------------------------
    async showEntryForm(accountId) {
        const accounts = await API.get('/accounts?active_only=true');
        const me = accounts.find(a => a.id === accountId) || {};
        const isCard = me.bank_kind === 'credit_card';
        const catOpts = accounts
            .filter(a => a.id !== accountId && ['expense', 'income', 'cogs', 'asset', 'liability', 'equity'].includes(a.account_type))
            .map(a => `<option value="${a.id}">${escapeHtml(a.account_number || '')} - ${escapeHtml(a.name)}${a.bank_kind ? ' (' + BankingPage._kindLabel(a.bank_kind).toLowerCase() + ')' : ''}</option>`).join('');
        const classGroup = await classFormGroupHtml();
        openModal('New Register Entry', `
            <form onsubmit="BankingPage.saveEntry(event, ${accountId})">
                <div class="form-grid">
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Amount *</label>
                        <input name="amount" type="number" step="0.01" required>
                        <div style="font-size:10px; color:var(--gray-500);">${isCard ? 'Negative = a charge (owe more). Positive = a payment to the card.' : 'Negative = money out (a payment). Positive = money in (a deposit).'}</div></div>
                    <div class="form-group"><label>Payee</label>
                        <input name="payee"></div>
                    <div class="form-group"><label>Check / ref #</label>
                        <input name="check_number"></div>
                    <div class="form-group full-width"><label>Category * <span style="font-weight:normal; color:var(--gray-500);">(the other side of the entry; a bank or card account makes it a transfer)</span></label>
                        <select name="category_account_id" required><option value="">Select…</option>${catOpts}</select></div>
                    <div class="form-group full-width"><label>Memo</label>
                        <input name="description"></div>
                    ${classGroup}
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Post Entry</button>
                </div>
            </form>`);
    },

    async saveEntry(e, accountId) {
        e.preventDefault();
        const form = e.target;
        const data = {
            account_id: accountId,
            date: form.date.value,
            amount: form.amount.value,
            category_account_id: parseInt(form.category_account_id.value, 10),
            payee: form.payee.value || null,
            description: form.description.value || null,
            check_number: form.check_number.value || null,
            class_id: classIdFromForm(form),
        };
        try {
            await API.post('/banking/transactions', data);
            toast('Entry posted');
            closeModal();
            App.navigate(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async showTransferForm(fromId = null) {
        const accounts = await API.get('/accounts?bank=1&active_only=true');
        const opt = (a, sel) => `<option value="${a.id}" ${sel ? 'selected' : ''}>${escapeHtml(a.account_number || '')} - ${escapeHtml(a.name)} (${BankingPage._kindLabel(a.bank_kind).toLowerCase()})</option>`;
        const fromOpts = accounts.map(a => opt(a, a.id === fromId)).join('');
        const firstCard = accounts.find(a => a.bank_kind === 'credit_card' && a.id !== fromId);
        const toOpts = accounts.map(a => opt(a, firstCard ? a.id === firstCard.id : a.id !== fromId)).join('');
        openModal('Transfer', `
            <form onsubmit="BankingPage.saveTransfer(event)">
                <p style="font-size:11px; color:var(--gray-500); margin-bottom:8px;">Move money between bank and card accounts. Paying a card is a transfer from the bank to the card.</p>
                <div class="form-grid">
                    <div class="form-group"><label>Date *</label><input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Amount *</label><input name="amount" type="number" step="0.01" min="0.01" required></div>
                    <div class="form-group"><label>From *</label><select name="from_account_id" required>${fromOpts}</select></div>
                    <div class="form-group"><label>To *</label><select name="to_account_id" required>${toOpts}</select></div>
                    <div class="form-group"><label>Reference</label><input name="reference"></div>
                    <div class="form-group"><label>Memo</label><input name="memo"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Post Transfer</button>
                </div>
            </form>`);
    },

    async saveTransfer(e) {
        e.preventDefault();
        const form = e.target;
        try {
            const t = await API.post('/transfers', {
                date: form.date.value,
                from_account_id: parseInt(form.from_account_id.value, 10),
                to_account_id: parseInt(form.to_account_id.value, 10),
                amount: form.amount.value,
                memo: form.memo.value || null,
                reference: form.reference.value || null,
            });
            toast('Transfer posted');
            closeModal();
            App.navigate(`#/banking/${t.from_account_id}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async showTransfers() {
        const rows = await API.get('/transfers');
        const body = rows.length ? `<div class="table-container"><table>
            <thead><tr><th scope="col">Date</th><th scope="col">From</th><th scope="col">To</th><th scope="col" class="amount">Amount</th><th scope="col">Memo</th><th scope="col"></th></tr></thead>
            <tbody>${rows.map(t => `<tr style="${t.status === 'void' ? 'color:var(--gray-400); text-decoration:line-through;' : ''}">
                <td>${formatDate(t.date)}</td><td>${escapeHtml(t.from_account_name)}</td><td>${escapeHtml(t.to_account_name)}</td>
                <td class="amount">${formatCurrency(t.amount)}</td><td>${escapeHtml(t.memo || '')}</td>
                <td>${t.status === 'void' ? '' : `<button class="btn btn-sm btn-secondary" onclick="BankingPage.voidTransfer(${t.id})">Void</button>`}</td>
            </tr>`).join('')}</tbody></table></div>` : '<p>No transfers yet.</p>';
        openModal('Transfers', body);
    },

    async voidTransfer(id) {
        if (!confirm('Void this transfer?')) return;
        try {
            await API.post(`/transfers/${id}/void`);
            toast('Transfer voided');
            closeModal();
            App.navigate('#/banking');
        } catch (err) { toast(err.message, 'error'); }
    },

    // ------------------------------------------------------------------
    // Reconciliation — over the ledger's lines
    // ------------------------------------------------------------------
    async startReconcile(accountId) {
        openModal('Begin Reconciliation', `
            <form onsubmit="BankingPage.createReconciliation(event, ${accountId})">
                <p style="margin-bottom:12px; font-size:11px; color:var(--gray-500);">
                    Enter the ending date and balance from your statement. For a card, the balance is the amount owed.
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

    async createReconciliation(e, accountId) {
        e.preventDefault();
        const form = e.target;
        try {
            const recon = await API.post('/banking/reconciliations', {
                account_id: accountId,
                statement_date: form.statement_date.value,
                statement_balance: form.statement_balance.value,
            });
            closeModal();
            BankingPage.showReconcileView(recon.id);
        } catch (err) {
            const existing = err && err.detail && err.detail.existing_id;
            if (existing) { closeModal(); BankingPage.showReconcileView(existing); return; }
            toast(err.message, 'error');
        }
    },

    async showReconcileView(reconId) {
        const data = await API.get(`/banking/reconciliations/${reconId}/transactions`);
        const rows = data.transactions.map(t => {
            const cls = t.reconciled ? 'style="background:var(--primary-light);"' : '';
            const amtCls = t.amount >= 0 ? 'color:var(--success)' : 'color:var(--danger)';
            return `<tr ${cls}>
                <td><input type="checkbox" ${t.reconciled ? 'checked' : ''}
                    onchange="BankingPage.toggleCleared(${reconId}, ${t.id}, this)"></td>
                <td>${formatDate(t.date)}</td>
                <td>${escapeHtml(t.payee || t.description || '')}${t.matched ? ' <span title="matched to a statement line" style="color:var(--success);">●</span>' : ''}</td>
                <td>${escapeHtml(t.check_number || '')}</td>
                <td class="amount" style="${amtCls}">${formatCurrency(t.amount)}</td>
            </tr>`;
        }).join('');
        const balanced = Math.abs(data.difference) < 0.01;
        const diffColor = balanced ? 'var(--success)' : 'var(--danger)';
        $('#page-content').innerHTML = `
            <div class="page-header">
                <h2>Reconcile — statement of ${formatDate(data.statement_date)}</h2>
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="App.navigate('#/banking/${data.account_id}')">Later</button>
                    <button class="btn btn-secondary" onclick="BankingPage.abandonReconcile(${reconId}, ${data.account_id})">Abandon</button>
                    <button class="btn btn-primary" id="recon-finish-btn" onclick="BankingPage.finishReconcile(${reconId}, ${data.account_id})" ${balanced ? '' : 'disabled'}>Finish Reconciliation</button>
                </div>
            </div>
            <div class="card-grid" style="margin-bottom:16px;">
                <div class="card"><div class="card-header">Beginning Balance</div>
                    <div class="card-value">${formatCurrency(data.beginning_balance)}</div></div>
                <div class="card"><div class="card-header">Cleared</div>
                    <div class="card-value" id="recon-cleared">${formatCurrency(data.cleared_total)}</div></div>
                <div class="card"><div class="card-header">Statement Balance</div>
                    <div class="card-value">${formatCurrency(data.statement_balance)}</div></div>
                <div class="card"><div class="card-header">Difference</div>
                    <div class="card-value" id="recon-diff" style="color:${diffColor}">${formatCurrency(data.difference)}</div>
                    <div style="font-size:11px; font-weight:600;" role="status">${balanced ? '✓ Balanced' : '⚠ Out of balance'}</div></div>
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col" style="width:30px;"></th><th scope="col">Date</th><th scope="col">Payee / Description</th><th scope="col">Ref #</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${rows || '<tr><td colspan="5" style="text-align:center;">No ledger lines up to this date</td></tr>'}</tbody>
            </table></div>`;
    },

    async toggleCleared(reconId, lineId, checkbox) {
        try {
            await API.post(`/banking/reconciliations/${reconId}/toggle/${lineId}`);
            BankingPage.showReconcileView(reconId);
        } catch (err) {
            checkbox.checked = !checkbox.checked;
            toast(err.message, 'error');
        }
    },

    async finishReconcile(reconId, accountId) {
        if (!confirm('Mark this reconciliation as complete? Cleared lines are locked.')) return;
        try {
            await API.post(`/banking/reconciliations/${reconId}/complete`);
            toast('Reconciliation completed');
            App.navigate(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async abandonReconcile(reconId, accountId) {
        if (!confirm('Abandon this reconciliation? Ticks are kept.')) return;
        try {
            await API.del(`/banking/reconciliations/${reconId}`);
            App.navigate(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    // ------------------------------------------------------------------
    // File import (OFX/QFX, CSV: Chase checking/credit, PayPal) — needs a
    // feed on the account; offers to create one.
    // ------------------------------------------------------------------
    async showOFXImport(accountId) {
        const feeds = await API.get('/banking/accounts');
        const feed = feeds.find(f => f.account_id === accountId);
        if (!feed) {
            if (confirm('Importing needs a bank feed (statement identity) on this account. Create one now?')) BankingPage.showAccountForm();
            return;
        }
        const feedId = feed.id;
        openModal('Import Bank File', `
            <form onsubmit="BankingPage.previewOFX(event, ${feedId}, ${accountId})">
                <div class="form-group">
                    <label>Select an OFX/QFX file, or a CSV export (Bank of America, Chase checking, Chase credit, PayPal)</label>
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

    async previewOFX(e, feedId, accountId) {
        e.preventDefault();
        const file = $('#ofx-file').files[0];
        if (!file) return;
        const isCsv = BankingPage._isCsvFile(file);
        const formData = new FormData();
        formData.append('file', file);
        // The pane stayed empty for the whole round trip and the button
        // stayed live, so a second click put a second parse in flight
        // (2.13.0 gate, owner + skytech). Say something first, and take
        // the button away until the answer is back.
        const submit = e.target.querySelector('button[type="submit"]');
        if (submit) submit.disabled = true;
        $('#ofx-preview').innerHTML = '<p class="form-hint">Reading the file — this can take a moment for a year of statement lines.</p>';
        try {
            const endpoint = isCsv ? '/api/bank-import/preview-csv' : '/api/bank-import/preview';
            const resp = await fetch(endpoint, { method: 'POST', body: formData });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Parse failed');
            if (isCsv && data.error) throw new Error(data.error);
            const rows = data.transactions.map(t => `<tr>
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
                    <button class="btn btn-primary" onclick="BankingPage.confirmOFXImport(${feedId}, ${accountId}, this)">Import ${data.transactions.length} Transactions</button>
                </div>`;
        } catch (err) {
            $('#ofx-preview').innerHTML = `<div style="color:var(--danger); font-size:11px;">${escapeHtml(err.message)}</div>`;
        } finally {
            if (submit) submit.disabled = false;
        }
    },

    async confirmOFXImport(feedId, accountId, importBtn) {
        // Same guard on the import itself: one click, one import. The
        // button comes in from its own onclick (`this`) — the first cut
        // found it through document.activeElement, and WebKit does not
        // focus a button on click, so on macOS the guard never engaged
        // (@macbase1, 2.13.0 gate, with a real click in a real WKWebView).
        const label = importBtn ? importBtn.textContent : '';
        if (importBtn) { importBtn.disabled = true; importBtn.textContent = 'Importing…'; }
        try {
            const file = $('#ofx-file').files[0];
            const isCsv = BankingPage._isCsvFile(file);
            const formData = new FormData();
            formData.append('file', file);
            const endpoint = isCsv
                ? `/api/bank-import/import-csv/${feedId}`
                : `/api/bank-import/import/${feedId}`;
            const resp = await fetch(endpoint, { method: 'POST', body: formData });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Import failed');
            toast(`Imported ${data.imported} (${data.skipped} duplicates skipped, ${data.matched || 0} matched to the books)`);
            closeModal();
            App.navigate(`#/banking/${accountId}`);
        } catch (err) {
            toast(err.message, 'error');
        } finally {
            // A failed import hands the button back, on every engine.
            if (importBtn) { importBtn.disabled = false; importBtn.textContent = label; }
        }
    },
};
