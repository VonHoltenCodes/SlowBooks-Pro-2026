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

    // Every banking page is a route of its own: #/banking, and a register
    // at #/banking/<account id>. App.navigate renders without touching the
    // address bar, so the register used to keep #/banking and a refresh
    // lost it (exploratory 2.17.3, W-L6). Moving the hash renders through
    // the hashchange listener; the same hash re-renders in place.
    // Go to a page and resolve once it is drawn: App.navigate renders the
    // page and keeps the address (pushState, no hashchange), so a caller
    // that awaits this can touch the new page. Setting location.hash and
    // returning nothing let postQuickEntry focus the old row's Payee a
    // moment before the redraw replaced it (2.23.5 round 3, W-17 / NEW-146).
    go(hash) {
        return App.navigate(hash);
    },
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
            // Each account's card is a link to its register (the cards were
            // a click alone: neither the keyboard nor a screen reader could
            // open one), named by the account, the rest of the card its
            // description; Enter or Space opens it, and Back comes back to
            // it (its key, noted on its click: ReportsPage._leaveFrom).
            html += `<div class="card-grid">`;
            for (const a of rows) {
                const owed = a.bank_kind === 'credit_card';
                const id = `bank-card-${a.account_id}`;
                html += `<a class="card card-link" href="#/banking/${a.account_id}" data-row-key="bank:${a.account_id}"
                    aria-label="${escapeHtml(a.name)}" aria-describedby="${id}-kind ${id}-value ${id}-feed"
                    onclick="ReportsPage._leaveFrom(this)" onkeydown="ReportsPage._cardKey(event)">
                    <div class="card-header">${escapeHtml(a.name)} <span id="${id}-kind" style="font-size:10px; color:var(--gray-400);">${escapeHtml(a.account_number || '')} · ${BankingPage._kindLabel(a.bank_kind)}</span></div>
                    <div class="card-value" id="${id}-value">${formatCurrency(a.balance)}${owed ? ' <span style="font-size:11px; color:var(--gray-500);">owed</span>' : ''}</div>
                    <div id="${id}-feed" style="font-size:12px; color:var(--gray-400); margin-top:4px;">
                        ${a.feed ? `${escapeHtml(a.feed.bank_name || a.feed.name)} ${a.feed.last_four ? '****' + a.feed.last_four : ''}` : 'No bank feed'}
                        ${a.to_review ? `<span style="color:var(--text-warning); font-weight:700;"> · ${a.to_review} to review</span>` : ''}
                        ${a.last_reconciled ? ` · reconciled ${formatDate(a.last_reconciled)}` : ''}
                    </div>
                </a>`;
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
            BankingPage.go('#/banking');
        } catch (err) { toast(err.message, 'error'); }
    },

    async dismissLegacy(feedId) {
        if (!confirm('Dismiss the pre-2.10 balance? It will not be posted.')) return;
        try {
            await API.put(`/banking/accounts/${feedId}`, { legacy_balance: null });
            BankingPage.go('#/banking');
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
                <form onsubmit="BankingPage.connectSimpleFIN(event)" data-write>
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
            const reversed = new Set(feed.reversed || []);
            const rows = feed.accounts.map(a => `<tr>
                    <td>${escapeHtml(a.name)}<div style="font-size:10px; color:var(--gray-400);">${escapeHtml(a.org || '')}</div></td>
                    <td class="amount">${escapeHtml(a.balance)} ${escapeHtml(a.currency)}</td>
                    <td><select data-sfid="${escapeHtml(a.id)}" class="simplefin-map" data-write>${options(a.id)}</select></td>
                    <td><label style="font-size:11px; white-space:nowrap;"><input type="checkbox" data-sfid="${escapeHtml(a.id)}" class="simplefin-reverse" data-write
                        ${reversed.has(a.id) ? 'checked' : ''}> Reverse signs</label></td>
                </tr>`).join('');
            body = `
                <p style="font-size:12px; margin-bottom:8px;">
                    Connected. Choose which SlowBooks bank account each feed lands in, then sync —
                    duplicates are skipped, bank rules suggest categories, and matches to postings you
                    already made are found. Everything else waits in the account's <em>To review</em> list.
                    ${feed.last_sync ? `Last sync: ${escapeHtml(feed.last_sync.replace('T', ' '))}` : 'Not synced yet.'}
                    The first sync brings in about the last three months; <em>Fetch older history</em> reaches
                    back as far as your SimpleFIN provider keeps.
                    If a feed shows a bank account's balance as negative and its deposits as withdrawals,
                    tick <em>Reverse signs</em> for it before syncing.
                </p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Bank feed</th><th scope="col" class="amount">Balance</th><th scope="col">Imports into</th><th scope="col">Amounts</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>
                <div class="form-actions" style="margin-top:12px;">
                    <button class="btn btn-primary" onclick="BankingPage.syncSimpleFIN()">Sync Now</button>
                    <button class="btn btn-secondary" onclick="BankingPage.showSimpleFINHistory()">Fetch older history…</button>
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
            BankingPage.go('#/banking');
        } catch (err) { toast(err.message, 'error'); }
    },

    async saveSimpleFINMap() {
        const mapping = {};
        document.querySelectorAll('.simplefin-map').forEach(sel => {
            mapping[sel.dataset.sfid] = sel.value ? parseInt(sel.value, 10) : 0;
        });
        const reversed = [...document.querySelectorAll('.simplefin-reverse')]
            .filter(box => box.checked).map(box => box.dataset.sfid);
        await API.post('/simplefin/map', { mapping, reversed });
    },

    async syncSimpleFIN() {
        try {
            await BankingPage.saveSimpleFINMap();
            const r = await API.post('/simplefin/sync');
            toast(`Synced: ${r.imported} new, ${r.skipped} duplicates skipped`);
            if (r.warnings && r.warnings.length) toast(r.warnings[0], 'error');
            BankingPage.go('#/banking');
        } catch (err) { toast(err.message, 'error'); }
    },

    // Older history: how far back is the person's choice; how much there is
    // depends on the SimpleFIN provider. The server fetches it in slices the
    // Bridge accepts; duplicates are skipped as on any sync.
    showSimpleFINHistory() {
        openModal('Fetch older history', `
            <form onsubmit="BankingPage.fetchSimpleFINHistory(event)">
                <div class="form-group">
                    <label for="simplefin-history">Reach back</label>
                    <select id="simplefin-history" name="history_months">
                        <option value="3">3 months</option>
                        <option value="6">6 months</option>
                        <option value="12" selected>12 months</option>
                    </select>
                </div>
                <p style="font-size:12px; margin:8px 0;">
                    How much history there is depends on your SimpleFIN provider: the SimpleFIN Bridge keeps
                    about 90 days, and others, such as BankSync, keep up to a year. Transactions already
                    imported are skipped; the rest wait in each account's <em>To review</em> list, where
                    anything from before your books began can be excluded.
                </p>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Fetch</button>
                </div>
            </form>`);
    },

    async fetchSimpleFINHistory(e) {
        e.preventDefault();
        const months = parseInt(e.target.history_months.value, 10);
        const btn = e.target.querySelector('button[type=submit]');
        if (btn) { btn.disabled = true; btn.textContent = 'Fetching…'; }
        try {
            await BankingPage.saveSimpleFINMap();
            const r = await API.post('/simplefin/sync', { history_months: months });
            closeModal();
            toast(`Fetched back to ${formatDate(r.since)}: ${r.imported} new, ${r.skipped} duplicates skipped`);
            if (r.warnings && r.warnings.length) toast(r.warnings[0], 'error');
            BankingPage.go('#/banking');
        } catch (err) {
            if (btn) { btn.disabled = false; btn.textContent = 'Fetch'; }
            toast(err.message, 'error');
        }
    },

    async disconnectSimpleFIN() {
        if (!confirm('Disconnect the SimpleFIN bank feed? Imported transactions are kept.')) return;
        try {
            await API.post('/simplefin/disconnect');
            toast('Bank feed disconnected');
            BankingPage.go('#/banking');
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
        const opts = free.map(a => `<option value="${a.id}">${accountLabel(a)} (${BankingPage._kindLabel(a.bank_kind)})</option>`).join('');
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
        const untouched = !form.opening_balance.dataset.touched;
        note.textContent = '';
        if (!accountId || accountId === '__new__') {
            if (untouched) form.opening_balance.value = '0';
            return;
        }
        const asOf = form.opening_date.value || todayISO();
        try {
            const led = await API.get(`/banking/ledger-balance?account_id=${accountId}&as_of=${asOf}`);
            if (form.account_id.value !== accountId) return;  // changed meanwhile
            if (untouched) form.opening_balance.value = led.has_postings ? Number(led.balance).toFixed(2) : '0';
            if (!led.has_postings) return;
            note.textContent = `The books already show ${formatCurrency(led.balance)} in ${led.account_name} on ${formatDate(led.as_of)}. Enter the statement's figure: if it differs, you'll be asked before the difference is posted.`;
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
            BankingPage.go('#/banking');
        } catch (err) { toast(err.message, 'error'); }
    },

    // ------------------------------------------------------------------
    // The register — route #/banking/:accountId?start_date=…&end_date=…&class_id=…
    // ------------------------------------------------------------------
    // The register's filters (#236): a date range and a class, carried in
    // the address (replaced in place when changed, so Back never walks
    // through them) and sent to the service, which computes the balance
    // brought forward under the same class test.
    _regFilter: { start_date: '', end_date: '', class_id: '' },

    _regFilterFrom(query) {
        const q = query || {};
        // a date that is not one, or From after To, is said so and ignored
        return {
            ...datesFromQuery(q),
            class_id: /^\d+$/.test(q.class_id || '') ? q.class_id : '',
        };
    },

    _regUrl(id) {
        const f = BankingPage._regFilter;
        const q = ['start_date', 'end_date', 'class_id'].filter(k => f[k]).map(k => `${k}=${encodeURIComponent(f[k])}`).join('&');
        return `#/banking/${id}${q ? `?${q}` : ''}`;
    },

    // A filter change, typed or chosen. From after To is refused before
    // the address is touched, as the reports refuse it (W-4: written to
    // the address first, the dates came back through datesFromQuery as
    // "in the address … both ignored", and both boxes were emptied):
    // said, with the filter kept, and the boxes put back to it — the
    // register, and its address, stay on that filter.
    regFilterChanged(id, filter = null) {
        const was = BankingPage._regFilter;
        const next = filter || {
            start_date: $('#reg-start')?.value || '',
            end_date: $('#reg-end')?.value || '',
            class_id: $('#reg-class')?.value || '',
        };
        if (reversedRangeRefused(next.start_date, next.end_date, { start: was.start_date, end: was.end_date })) {
            const startEl = $('#reg-start');
            const endEl = $('#reg-end');
            if (startEl) startEl.value = was.start_date;
            if (endEl) endEl.value = was.end_date;
            return;
        }
        BankingPage._regFilter = next;
        const url = BankingPage._regUrl(id);
        if ((location.hash || '#/') !== url) history.replaceState(history.state, '', url);
        App.navigate(url);
    },

    regClear(id) {
        BankingPage.regFilterChanged(id, { start_date: '', end_date: '', class_id: '' });
    },

    async renderRegister(accountId, query) {
        const id = parseInt(accountId, 10);
        const f = BankingPage._regFilterFrom(query);
        BankingPage._regFilter = f;
        // a date the address carried that was refused (not one, From after
        // To) is not kept on the bar: the address reads the dates in use, so
        // a reload does not toast again (NEW-35 review)
        {
            const want = BankingPage._regUrl(id);
            if ((location.hash || '') !== want && (location.hash || '').startsWith(`#/banking/${id}`)) {
                history.replaceState(history.state, '', want);
                if (window.App && App.addressShown) App.addressShown();
            }
        }
        const filterQs = Object.entries(f).filter(([, v]) => v).map(([k, v]) => `&${k}=${encodeURIComponent(v)}`).join('');
        const [reg, overview, classes, chart, vendors, customers] = await Promise.all([
            API.get(`/banking/check-register?account_id=${id}${filterQs}`),
            API.get('/banking/overview'),
            API.get('/classes?include_archived=true').catch(() => []),
            API.get('/accounts?active_only=true').catch(() => []),
            API.get('/vendors?active_only=true').catch(() => null),
            API.get('/customers?active_only=true').catch(() => null),
        ]);
        BankingPage._accounts = chart;
        BankingPage._vendorNames = vendors ? new Set(vendors.map(v => v.name.toLowerCase())) : null;
        BankingPage._customerNames = customers ? new Set(customers.map(c => c.name.toLowerCase())) : null;
        const info = overview.find(a => a.account_id === id) || {};
        const feed = info.feed || null;
        const kind = reg.bank_kind;
        const [outLabel, inLabel] = BankingPage._cols(kind);
        let review = [];
        // the whole queue: a year fetched from a feed can be more than a page
        if (feed) review = await fetchAllPages(`/banking/transactions?bank_account_id=${feed.bank_account_id}&status=unmatched`);
        BankingPage._ctx = { accountId: id, feedId: feed ? feed.bank_account_id : null, kind, lastReconciled: info.last_reconciled || null };

        let html = `
            <div class="page-header">
                <h2>${escapeHtml(reg.account_name)} <span style="font-size:11px; color:var(--gray-400);">${escapeHtml(reg.account_number || '')} · ${BankingPage._kindLabel(kind)}</span></h2>
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="BankingPage.go('#/banking')">Back</button>
                    <button class="btn btn-primary" onclick="BankingPage.showEntryForm(${id})">+ Entry</button>
                    <button class="btn btn-secondary" onclick="BankingPage.showTransferForm(${id})">Transfer</button>
                    <button class="btn btn-secondary" onclick="BankingPage.showOFXImport(${id})">Import file</button>
                    ${feed ? `<button class="btn btn-secondary" onclick="BankingPage.findMatches(${feed.bank_account_id}, ${id})">Find matches</button>` : ''}
                    <button class="btn btn-secondary" onclick="BankingPage.startReconcile(${id})">Reconcile</button>
                    <button class="btn btn-secondary" onclick="BankingPage.showReconciliations(${id})">Reconciliations…</button>
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

        const classOpts = classes.map(c => `<option value="${c.id}"${String(c.id) === f.class_id ? ' selected' : ''}>${escapeHtml(c.name)}${c.is_archived ? ' (archived)' : ''}</option>`).join('');
        const filtered = !!(f.start_date || f.end_date || f.class_id);
        html += `
            <div class="toolbar" style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
                <label for="reg-start" style="font-size:11px;">From</label>
                <input type="date" id="reg-start" value="${escapeHtml(f.start_date)}" onchange="BankingPage.regFilterChanged(${id})">
                <label for="reg-end" style="font-size:11px;">To</label>
                <input type="date" id="reg-end" value="${escapeHtml(f.end_date)}" onchange="BankingPage.regFilterChanged(${id})">
                <label for="reg-class" style="font-size:11px;">${T('Class')}</label>
                <select id="reg-class" data-no-search onchange="BankingPage.regFilterChanged(${id})"><option value="">All ${T('classes').toLowerCase()}</option>${classOpts}</select>
                ${filtered ? `<button type="button" class="btn btn-sm btn-secondary" onclick="BankingPage.regClear(${id})">Clear</button>` : ''}
                ${filtered ? `<span id="reg-filter-note" style="font-size:11px; color:var(--gray-500);">Showing ${reg.class_name ? `${T('class').toLowerCase()} <strong>${escapeHtml(reg.class_name)}</strong>` : 'every ' + T('class').toLowerCase()}${f.start_date ? ` from ${formatDate(f.start_date)}` : ''}${f.end_date ? ` to ${formatDate(f.end_date)}` : ''}: the running balance counts these lines${reg.class_name ? ` only — the account's whole balance is ${formatCurrency(reg.balance)}` : ''}.</span>` : ''}
            </div>`;

        // the quick-entry row at the foot of the register (#329): date,
        // payee, memo, category, ref, payment or deposit; Enter or Post
        // posts it through the same call as + Entry, and the next row is
        // ready. The list reads oldest to newest, as a ledger does, with the
        // running balance down the right.
        const quickRow = BankingPage._quickEntryRow(id, kind, chart, outLabel, inLabel);
        if (!reg.entries.length) {
            html += `<div class="empty-state"><p>${filtered ? 'Nothing posted to this account for these filters' : 'Nothing posted to this account yet'}</p></div>`;
            if (!filtered) html += `<div class="table-container"><table><thead><tr>
                    <th scope="col">Date</th><th scope="col">Payee</th><th scope="col">Memo</th><th scope="col">Ref</th><th scope="col">Category</th>
                    <th scope="col" class="amount">${outLabel}</th><th scope="col" class="amount">${inLabel}</th><th scope="col" class="amount">Balance</th><th scope="col"></th><th scope="col"></th>
                </tr></thead><tbody></tbody>${quickRow}</table></div>`;
        } else {
            const rows = reg.entries.slice().map(e => `
                <tr style="${e.voided ? 'color:var(--gray-400); text-decoration:line-through;' : ''}">
                    <td>${formatDate(e.date)}</td>
                    <td>${escapeHtml(e.payee || '')}</td>
                    <td>${BankingPage._memoCell(e)}</td>
                    <td>${escapeHtml(e.reference || '')}</td>
                    <td style="font-size:10px; color:var(--gray-500);">${escapeHtml(sourceWord(e.source_type))}</td>
                    <td class="amount">${e.payment > 0 ? formatCurrency(e.payment) : ''}</td>
                    <td class="amount">${e.deposit > 0 ? formatCurrency(e.deposit) : ''}</td>
                    <td class="amount" style="font-weight:700;">${formatCurrency(e.balance)}</td>
                    <td style="text-align:center;">${e.reconciliation_id ? 'R' : (e.cleared ? '✓' : '')}</td>
                    <td style="white-space:nowrap;">${BankingPage._printCheckButton(e)}${BankingPage._editButton(e, id)}${e.voidable ? `<button data-destructive class="btn btn-sm btn-secondary" onclick="BankingPage.voidEntry('${e.source_type}', ${e.transaction_id}, ${id})">Void</button>` : ''}</td>
                </tr>`).join('');
            // oldest first, so the balance brought forward (a start date's
            // doing) opens the table (2.23.5 round 2, NEW-142); the entry
            // row follows the entries on a populated register as on an
            // empty one (W-14, NEW-140), unless the register is filtered
            const opening = f.start_date ? `<tr style="color:var(--gray-500);" id="reg-opening"><td>${formatDate(f.start_date)}</td><td colspan="6">Balance brought forward${reg.class_name ? ` (${escapeHtml(reg.class_name)})` : ''}</td><td class="amount">${formatCurrency(reg.opening_balance)}</td><td></td><td></td></tr>` : '';
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th scope="col">Date</th><th scope="col">Payee</th><th scope="col">Memo</th><th scope="col">Ref #</th><th scope="col">Type</th>
                    <th scope="col" class="amount">${outLabel}</th><th scope="col" class="amount">${inLabel}</th><th scope="col" class="amount">Balance</th>
                    <th scope="col" title="✓ cleared · R reconciled">✓</th><th scope="col"></th>
                </tr></thead><tbody>${opening}${rows}</tbody>${filtered ? '' : quickRow}</table></div>`;
        }
        return html;
    },

    // Where a register line opens: the document behind it, or — for a
    // posting with none of its own (a void, a sales-tax or payroll payment,
    // an import) — its journal entry. Every line opens something.
    _entryHref(e) {
        return e.source_link || `#/journal/${e.transaction_id}`;
    },

    // The Memo cell: the memo, linking to what the line opens. A bank entry
    // (posted here, or from the bank feed) is edited with its row's Edit,
    // so its memo stays words and the link beside it is named for what it
    // opens — the journal posting behind the entry. A memo that itself
    // linked to #/journal/… read as "this is a journal entry", the kind
    // that cannot be edited, and testers went looking for a way to change
    // it. A void bank entry keeps the plain link: there is nothing to edit.
    _memoCell(e) {
        const href = escapeHtml(BankingPage._entryHref(e));
        if (e.source_type !== 'bank_entry' || !e.editable) {
            return `<a href="${href}">${escapeHtml(e.description || 'View entry')}</a>`;
        }
        return `${e.description ? `${escapeHtml(e.description)} ` : ''}<a href="${href}" style="font-size:10px;" title="The journal posting behind this bank entry; use Edit to change it">Posting</a>`;
    },

    // A register entry opens for editing (#388); the register says which
    // (`editable`: a bank entry that is not void — a reconciled one too,
    // its amount and date locked).
    _editButton(e, accountId) {
        if (!e.editable) return '';
        return `<button class="btn btn-sm btn-secondary" onclick="BankingPage.editEntry(${e.transaction_id}, ${accountId})">Edit</button>`;
    },

    // A check written here, or an expense paid from this account, prints
    // its check (discussion #354); the register says which (`printable`:
    // money out of a bank account, not a card, not void).
    _printCheckButton(e) {
        if (!e.printable) return '';
        return `<button class="btn btn-sm btn-secondary" onclick="window.open('/api/checks/print?transaction_id=${e.transaction_id}','_blank')">Print Check</button> `;
    },

    _reviewPanel(review, feedId, accountId, kind) {
        // the lines by id, for "Rule…" (the payee and the category chosen)
        BankingPage._reviewLines = Object.fromEntries(review.map(t => [t.id, t]));
        const rows = review.map(t => `
            <tr>
                <td>${formatDate(t.date)}</td>
                <td>${escapeHtml(t.payee || '')}<div style="font-size:10px; color:var(--gray-400);">${escapeHtml(t.description || '')}</div></td>
                <td class="amount" style="${t.amount >= 0 ? 'color:var(--text-success)' : 'color:var(--text-danger)'}">${formatCurrency(t.amount)}</td>
                <td><select id="cat-${t.id}" class="review-cat" data-write data-current="${t.category_account_id || ''}" aria-label="Category" onchange="BankingPage.setCategory(${t.id}, this)"><option value="">${t.category_name ? escapeHtml(t.category_name) : 'Pick a category…'}</option></select></td>
                <td style="white-space:nowrap;" data-write>
                    <button class="btn btn-sm btn-primary" onclick="BankingPage.addLine(${t.id}, ${accountId})">Add</button>
                    <button class="btn btn-sm btn-secondary" onclick="BankingPage.showMatch(${t.id}, ${accountId})">Match</button>
                    <button class="btn btn-sm btn-secondary" onclick="BankingPage.excludeLine(${t.id}, ${accountId})">Exclude</button>
                    <button class="btn btn-sm btn-secondary" onclick="BankingPage.ruleFromLine(${t.id}, ${accountId})" title="A bank rule from this payee and the category chosen">Rule…</button>
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
                `<option value="${a.id}" ${String(a.id) === current ? 'selected' : ''}>${accountLabel(a)}${a.bank_kind ? ' (' + BankingPage._kindLabel(a.bank_kind).toLowerCase() + ')' : ''}</option>`).join('');
        });
    },

    async addLine(lineId, accountId) {
        const sel = $(`#cat-${lineId}`);
        const body = {};
        if (sel && sel.value) body.category_account_id = parseInt(sel.value, 10);
        try {
            await API.post(`/banking/transactions/${lineId}/add`, body);
            toast('Added to the books');
            BankingPage.go(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async excludeLine(lineId, accountId) {
        try {
            await API.post(`/banking/transactions/${lineId}/exclude`);
            BankingPage.go(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async restoreLine(lineId, accountId) {
        try {
            await API.post(`/banking/transactions/${lineId}/restore`);
            BankingPage.go(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async unmatchLine(lineId, accountId) {
        try {
            await API.post(`/banking/transactions/${lineId}/unmatch`);
            BankingPage.go(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    // A category picked in a line's dropdown is saved as it is picked, so
    // "Add all categorised" posts it and a reload still shows it. Before,
    // a pick lived only in the dropdown until that line's own Add: Add all
    // posted just the rule-categorised lines, and reloading lost the rest
    // (exploratory 2.17.3, W-M12).
    _pendingCategories: new Set(),

    // A bank rule from a statement line (#259): the rule form opens with
    // the payee as the pattern and the category chosen here filled in, so
    // the two screens that know the same payee hand it across. Saved, the
    // rules run over the queue, and the register comes back.
    async ruleFromLine(lineId, accountId) {
        const line = (BankingPage._reviewLines || {})[lineId] || {};
        const sel = document.getElementById(`cat-${lineId}`);
        await Promise.allSettled([...BankingPage._pendingCategories]);
        const accountChosen = sel && (sel.value || sel.dataset.current);
        const payee = (line.payee || line.description || '').trim();
        if (!payee) { toast('This line has no payee to make a rule from.', 'error'); return; }
        BankRulesPage.showForm(null, {
            name: payee,
            pattern: payee,
            account_id: accountChosen ? parseInt(accountChosen, 10) : '',
            from: 'a statement line',
            afterSave: async () => {
                try { await API.post('/bank-rules/apply'); } catch (e) { /* the rule is saved; the queue catches it on the next import */ }
                App.refresh();
            },
        });
    },

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
            BankingPage.go(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async findMatches(feedId, accountId) {
        try {
            const r = await API.post(`/banking/accounts/${feedId}/feed/auto-match`);
            toast(`Matched ${r.matched} statement line${r.matched === 1 ? '' : 's'}`);
            BankingPage.go(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async showMatch(lineId, accountId) {
        try {
            const cands = await API.get(`/banking/transactions/${lineId}/candidates`);
            const rows = cands.map(c => `<tr>
                <td>${formatDate(c.date)} <span style="font-size:10px; color:var(--gray-400);">${c.days_off ? c.days_off + 'd off' : 'same day'}</span></td>
                <td>${escapeHtml(c.payee || c.description || '')}</td>
                <td>${escapeHtml(c.reference || '')}</td>
                <td style="font-size:10px;">${escapeHtml(sourceWord(c.source_type))}</td>
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
            BankingPage.go(`#/banking/${accountId}`);
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
            BankingPage.go(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    _quickEntryRow(accountId, kind, chart, outLabel, inLabel) {
        const cats = (chart || [])
            .filter(a => a.id !== accountId && BankingPage.CATEGORY_TYPES.includes(a.account_type))
            .map(a => `<option value="${a.id}">${accountLabel(a, a.account_path || a.name)}${a.bank_kind ? ' (transfer)' : ''}</option>`).join('');
        const key = `onkeydown="if (event.key === 'Enter') { event.preventDefault(); BankingPage.postQuickEntry(${accountId}); }"`;
        const now = new Date();
        const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
        // the row lives in its own <tfoot>, after the entries; every field is
        // named for the new entry, apart from the page's other fields
        return `<tfoot><tr id="reg-quick-entry" data-write style="background:var(--primary-light);">
                    <td><input type="date" id="qe-date" aria-label="New entry date" value="${today}" style="width:120px; font-size:11px; padding:2px 4px;" ${key}></td>
                    <td><input type="text" id="qe-payee" aria-label="New entry payee" placeholder="Payee" style="width:100%; font-size:11px; padding:2px 4px;" onblur="BankingPage.payeeTyped(this.value, BankingPage._rowMoneyIn(), 'qe-payee')" ${key}></td>
                    <td><input type="text" id="qe-memo" aria-label="New entry memo" placeholder="Memo" style="width:100%; font-size:11px; padding:2px 4px;" ${key}></td>
                    <td><input type="text" id="qe-ref" aria-label="New entry check or reference number" placeholder="Ref" style="width:60px; font-size:11px; padding:2px 4px;" ${key}></td>
                    <td><span style="display:inline-flex; gap:2px; align-items:center;"><select id="qe-category" aria-label="New entry category" data-no-search style="font-size:11px; padding:2px 4px; max-width:150px;"><option value="">Category…</option>${cats}</select><button type="button" id="qe-category-add" class="btn btn-sm btn-secondary" aria-label="Add a new category account" title="Add a new account" style="font-size:11px; padding:2px 6px;" onclick="BankingPage.openNewAccount('qe-category', ${accountId})">Add</button></span></td>
                    <td class="amount"><input type="number" step="0.01" min="0" id="qe-out" aria-label="New entry ${escapeHtml(outLabel)}" placeholder="0.00" style="width:90px; font-size:11px; padding:2px 4px; text-align:right;" ${key}></td>
                    <td class="amount"><input type="number" step="0.01" min="0" id="qe-in" aria-label="New entry ${escapeHtml(inLabel)}" placeholder="0.00" style="width:90px; font-size:11px; padding:2px 4px; text-align:right;" ${key}></td>
                    <td></td><td></td>
                    <td><button type="button" class="btn btn-sm btn-primary" onclick="BankingPage.postQuickEntry(${accountId})">Post</button></td>
                </tr>
                <tr data-write><td colspan="10" style="padding:0; border:0;">${BankingPage._newAccountPanel('qe-category', accountId)}</td></tr></tfoot>`;
    },

    // The quick row posts through the same call as + Entry (#329): money
    // out from the left amount box, money in from the right one (a card's
    // charge and payment the same way), one of them; then the register
    // redraws with the entry in place and the row ready for the next.
    async postQuickEntry(accountId) {
        const v = (sel) => (document.getElementById(sel) || {}).value || '';
        const out = parseFloat(v('qe-out') || '0'), inn = parseFloat(v('qe-in') || '0');
        if (!v('qe-category')) { toast('Choose a category', 'error'); return; }
        if ((out > 0) === (inn > 0)) { toast('Enter an amount in one column', 'error'); return; }
        const data = {
            account_id: accountId,
            date: v('qe-date'),
            amount: out > 0 ? (-out).toFixed(2) : inn.toFixed(2),
            category_account_id: parseInt(v('qe-category'), 10),
            payee: v('qe-payee') || null,
            description: v('qe-memo') || null,
            check_number: v('qe-ref') || null,
        };
        try {
            await API.post('/banking/transactions', data);
            toast('Entry posted');
            BankingPage.payeeTyped(data.payee, inn > 0, 'qe-payee');
            await BankingPage.go(`#/banking/${accountId}`);
            BankingPage._focusPayee();
        } catch (err) { toast(err.message, 'error'); }
    },

    // The cursor in the new row's Payee once the register is drawn, and
    // kept there through any redraw that follows the post (the page can
    // draw more than once as its lists arrive): tried every 50 ms for a
    // second until the box has the focus (W-17 / NEW-146).
    _focusPayee(tries = 20) {
        // Yield to an open toast question (the Add payee / Not now prompt moves
        // focus to itself); do not yank the keyboard back out of it.
        const active = document.activeElement;
        if (active && active.closest && active.closest('#toast-container')) return;
        const next = document.getElementById('qe-payee');
        if (next && document.activeElement !== next) {
            next.focus();
            if (typeof next.scrollIntoView === 'function') next.scrollIntoView({ block: 'center' });
        }
        if (tries > 0 && (!next || document.activeElement !== next)) {
            setTimeout(() => BankingPage._focusPayee(tries - 1), 50);
        }
    },

    // ------------------------------------------------------------------
    // + Entry (posts) and Transfer
    // ------------------------------------------------------------------
    async showEntryForm(accountId, entry = null) {
        const accounts = await API.get('/accounts?active_only=true');
        const me = accounts.find(a => a.id === accountId) || {};
        const isCard = me.bank_kind === 'credit_card';
        const catOpts = accounts
            .filter(a => a.id !== accountId && BankingPage.CATEGORY_TYPES.includes(a.account_type))
            .map(a => `<option value="${a.id}">${accountLabel(a)}${a.bank_kind ? ' (' + BankingPage._kindLabel(a.bank_kind).toLowerCase() + ')' : ''}</option>`).join('');
        BankingPage._accounts = accounts;
        if (!BankingPage._vendorNames) {
            const vendors = await API.get('/vendors?active_only=true').catch(() => null);
            if (vendors) BankingPage._vendorNames = new Set(vendors.map(v => v.name.toLowerCase()));
        }
        if (!BankingPage._customerNames) {
            const customers = await API.get('/customers?active_only=true').catch(() => null);
            if (customers) BankingPage._customerNames = new Set(customers.map(c => c.name.toLowerCase()));
        }
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
                        <input name="payee" id="entry-payee" onblur="BankingPage.payeeTyped(this.value, BankingPage._formMoneyIn(), 'entry-payee')"></div>
                    <div class="form-group"><label>Check / ref #</label>
                        <input name="check_number"></div>
                    <div class="form-group full-width"><label>Category * <span style="font-weight:normal; color:var(--gray-500);">(the other side of the entry; a bank or card account makes it a transfer)</span></label>
                        <span style="display:flex; gap:6px; align-items:center;"><select name="category_account_id" id="entry-category" style="flex:1;"><option value="">Select…</option>${catOpts}</select><button type="button" id="entry-category-add" class="btn btn-sm btn-secondary" aria-label="Add a new category account" onclick="BankingPage.openNewAccount('entry-category', ${accountId})">Add a new account…</button></span>
                        ${BankingPage._newAccountPanel('entry-category', accountId)}</div>
                    <div class="form-group full-width"><label>Memo</label>
                        <input name="description"></div>
                    ${classGroup}
                    <div class="form-group full-width" id="entry-splits" style="display:none;">
                        <label>Split across accounts <span style="font-weight:normal; color:var(--gray-500);">(each line's share of the amount; a loan payment's principal and interest, a check that pays several things, a card processor's deposit as the gross with the fee taken out)</span></label>
                        <table class="data-table" style="font-size:12px;"><thead><tr><th scope="col">Account</th><th scope="col" class="amount">Amount</th><th scope="col">Taken out</th><th scope="col">Memo</th><th scope="col"></th></tr></thead>
                        <tbody id="entry-split-rows"></tbody></table>
                        <div style="display:flex; gap:8px; align-items:center; margin-top:4px;">
                            <button type="button" class="btn btn-sm btn-secondary" onclick="BankingPage.addSplitRow()">+ Line</button>
                            <span id="entry-split-note" role="status" style="font-size:11px; color:var(--gray-500);"></span>
                        </div>
                    </div>
                    <div class="form-group full-width">
                        <button type="button" class="btn btn-sm btn-secondary" id="entry-split-toggle" onclick="BankingPage.toggleSplits()">Split this entry…</button>
                    </div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Post Entry</button>
                </div>
            </form>`);
        BankingPage._splitCats = catOpts;
        if (entry) BankingPage._fillEntryForm(entry);
    },

    // Edit (#388): a register entry opens in the + Entry form, filled in;
    // Save sends it back as a PUT and the row is rewritten in place.
    async editEntry(txnId, accountId) {
        try {
            const entry = await API.get(`/banking/entries/${txnId}`);
            await BankingPage.showEntryForm(accountId, entry);
        } catch (err) { toast(err.message, 'error'); }
    },

    // The form as "Edit entry": the entry's fields in the boxes, its split
    // lines when it has them, and the entry id on the form for saveEntry.
    // What the form has no box for rides along on it and goes back with
    // the save (#388 review): the entry's job, and each split line's own
    // class and job. A class archived since keeps its place in the list.
    _fillEntryForm(entry) {
        const form = document.querySelector('#modal-body form');
        if (!form) return;
        const title = document.getElementById('modal-title');
        if (title) title.textContent = 'Edit entry';
        form.dataset.entryId = entry.id;
        form.dataset.jobId = entry.job_id || '';
        form.date.value = entry.date;
        form.amount.value = Number(entry.amount).toFixed(2);
        form.payee.value = entry.payee || '';
        form.check_number.value = entry.reference || '';
        form.description.value = entry.description || '';
        form.category_account_id.value = entry.category_account_id || '';
        if (form.class_id) {
            const sel = form.class_id;
            const want = entry.class_id ? String(entry.class_id) : '';
            if (![...sel.options].some(o => o.value === want)) {
                const opt = document.createElement('option');
                opt.value = want;
                opt.textContent = want ? `${entry.class_name || T('Class') + ' ' + want} (archived)` : '—';
                sel.appendChild(opt);
            }
            sel.value = want;
        }
        const submit = form.querySelector('button[type="submit"]');
        if (submit) submit.textContent = 'Save';
        if (entry.splits && entry.splits.length) {
            if (!BankingPage._splitsOpen()) BankingPage.toggleSplits();
            const body = document.getElementById('entry-split-rows');
            if (body) body.innerHTML = '';
            entry.splits.forEach(sp => {
                BankingPage.addSplitRow();
                const row = body ? body.lastElementChild : null;
                if (!row) return;
                row.querySelector('.split-cat').value = sp.category_account_id;
                // a line that goes the other way from the entry (a fee on a
                // deposit, a refund on a check, #387) is shown as it was
                // typed: its own number with Taken out ticked
                const part = Number(sp.amount);
                const against = (part < 0) !== (Number(entry.amount) < 0);
                row.querySelector('.split-amt').value = (against ? -part : part).toFixed(2);
                row.querySelector('.split-fee').checked = against;
                row.querySelector('.split-memo').value = sp.description || '';
                row.dataset.classId = sp.class_id || '';
                row.dataset.jobId = sp.job_id || '';
            });
            BankingPage.splitNote();
        }
        // a reconciled entry says so as it opens: its amount and date stay
        // as the statement has them (the server refuses a change)
        if (entry.reconciled_note) {
            form.date.readOnly = true;
            form.amount.readOnly = true;
            const note = document.createElement('div');
            note.id = 'entry-reconciled-note';
            note.setAttribute('role', 'note');
            note.className = 'form-group full-width';
            note.style.cssText = 'display:block; padding:8px 10px; border:1px solid var(--warning, #b7791f); border-radius:4px; font-size:12px;';
            note.textContent = entry.reconciled_note;
            const grid = form.querySelector('.form-grid');
            if (grid) grid.prepend(note); else form.prepend(note);
        }
    },

    // Splits (#330): the Category becomes the first line and more lines
    // take their share; the note says what is left to place. Posted as one
    // entry with several lines, as the API's `splits`. A line can go the
    // other way from the entry (#387): "Taken out" on a line makes it run
    // opposite the entry's direction, so a card processor's fee is typed as
    // the positive number the statement shows and the deposit's lines are
    // the gross income and the fee, adding up to the net the bank got. The
    // same box on a check is a refund netted against it. Only the magnitude
    // typed counts, so the line's own sign (620 or −620) does not matter.
    // The table is shown and hidden by style, not the hidden attribute:
    // .form-group's display:flex won over `hidden`, so the lines showed
    // before "Split this entry…" and what was typed there did not count
    // (2.23.5 round 2, NEW-141).
    _splitsOpen() {
        const box = document.getElementById('entry-splits');
        return !!box && box.style.display !== 'none';
    },
    toggleSplits() {
        const box = document.getElementById('entry-splits');
        if (!box) return;
        const open = !BankingPage._splitsOpen();
        box.style.display = open ? '' : 'none';
        const toggle = document.getElementById('entry-split-toggle');
        if (toggle) toggle.textContent = open ? 'Not a split' : 'Split this entry…';
        if (open && !document.querySelectorAll('#entry-split-rows tr').length) { BankingPage.addSplitRow(); BankingPage.addSplitRow(); }
        BankingPage.splitNote();
    },
    addSplitRow() {
        const body = document.getElementById('entry-split-rows');
        if (!body) return;
        const n = body.querySelectorAll('tr').length + 1;
        body.insertAdjacentHTML('beforeend', `<tr>
            <td><select class="split-cat" aria-label="Split line ${n} account" data-no-search><option value="">Select…</option>${BankingPage._splitCats || ''}</select></td>
            <td class="amount"><input class="split-amt" type="number" step="0.01" aria-label="Split line ${n} amount" oninput="BankingPage.splitNote()" style="width:100px; text-align:right;"></td>
            <td style="text-align:center;"><input class="split-fee" type="checkbox" aria-label="Split line ${n} taken out" title="A fee the processor kept, or a refund netted against a check: this line goes the other way from the entry" onchange="BankingPage.splitNote()"></td>
            <td><input class="split-memo" type="text" aria-label="Split line ${n} memo"></td>
            <td><button type="button" class="btn btn-sm btn-secondary" aria-label="Remove split line ${n}" onclick="this.closest('tr').remove(); BankingPage.splitNote();">×</button></td>
        </tr>`);
    },
    // The entry's direction: +1 for money in (a deposit), −1 for money out
    // (a check). Nothing typed yet counts as +1. A split line follows this
    // direction, and "Taken out" flips it (#387).
    _entrySign(form) {
        const amt = parseFloat(form.amount.value || '0') || 0;
        return amt < 0 ? -1 : 1;
    },
    // A split line's signed amount. A plain line runs the entry's direction;
    // "Taken out" makes it go the opposite way — a fee on a deposit, or a
    // refund on a check. Only the magnitude typed is used, so 620 and −620
    // both read as the entry's side. Null when nothing was typed.
    _splitAmount(row, sign) {
        const typed = row.querySelector('.split-amt').value;
        if (typed === '') return null;
        const mag = Math.abs(parseFloat(typed) || 0);
        const dir = row.querySelector('.split-fee').checked ? -sign : sign;
        return dir * mag;
    },
    splitNote() {
        const note = document.getElementById('entry-split-note');
        const form = note ? note.closest('form') : null;
        if (!note || !form) return;
        const total = parseFloat(form.amount.value || '0') || 0;
        const sign = BankingPage._entrySign(form);
        const placed = [...form.querySelectorAll('#entry-split-rows tr')].reduce((sum, r) => sum + (BankingPage._splitAmount(r, sign) || 0), 0);
        const left = Math.round((total - placed) * 100) / 100;
        note.textContent = total ? (left === 0 ? `Every cent of ${formatCurrency(total)} is placed` : `${formatCurrency(left)} of ${formatCurrency(total)} still to place (a fee or a refund goes the other way: tick Taken out)`) : 'Enter the amount first';
    },

    // The split lines to post. A line with an amount but no account, or an
    // account but no amount, is said so and nothing posts (2.23.5 round 2,
    // W-13: a −120.00 line with no account was dropped without a word and
    // the entry posted short). A line with neither is an empty row and is
    // skipped.
    _splitLines(form) {
        if (!BankingPage._splitsOpen()) return null;
        const rows = [...form.querySelectorAll('#entry-split-rows tr')];
        const sign = BankingPage._entrySign(form);
        const splits = [];
        rows.forEach((r, i) => {
            const account = r.querySelector('.split-cat').value;
            const amount = r.querySelector('.split-amt').value;
            if (!account && amount === '') return;
            if (!account) throw new Error(`Split line ${i + 1} has an amount but no account. Choose the account, or remove the line.`);
            if (amount === '') throw new Error(`Split line ${i + 1} has an account but no amount. Enter its share, or remove the line.`);
            splits.push({
                category_account_id: parseInt(account, 10),
                amount: BankingPage._splitAmount(r, sign).toFixed(2),
                description: r.querySelector('.split-memo').value || null,
                // a line's own class and job, carried from the entry (#388)
                ...(r.dataset.classId ? { class_id: parseInt(r.dataset.classId, 10) } : {}),
                ...(r.dataset.jobId ? { job_id: parseInt(r.dataset.jobId, 10) } : {}),
            });
        });
        return splits.length ? splits : null;
    },

    // The entry as the form has it now, for a post or an edit's save.
    // Throws the sentence to show when it can't be sent.
    _entryData(form, accountId) {
        const splits = BankingPage._splitLines(form);
        if (!splits && !form.category_account_id.value) throw new Error('Choose a category, or split the entry');
        const data = {
            account_id: accountId,
            date: form.date.value,
            amount: form.amount.value,
            category_account_id: splits ? splits[0].category_account_id : parseInt(form.category_account_id.value, 10),
            payee: form.payee.value || null,
            description: form.description.value || null,
            check_number: form.check_number.value || null,
            class_id: classIdFromForm(form),
            ...(splits ? { splits } : {}),
        };
        // an edit sends the entry's job back: the form has no box for it,
        // and leaving it out of the body would be read as "keep" anyway
        if (form.dataset.entryId) data.job_id = form.dataset.jobId ? parseInt(form.dataset.jobId, 10) : null;
        return data;
    },

    async saveEntry(e, accountId) {
        e.preventDefault();
        const form = e.target;
        let data;
        try { data = BankingPage._entryData(form, accountId); } catch (err) { toast(err.message, 'error'); return; }
        if (!(await ClassWarn.ok(form))) return;
        const entryId = form.dataset.entryId ? parseInt(form.dataset.entryId, 10) : null;
        if (entryId) { await BankingPage._putEntry(entryId, data, accountId); return; }
        try {
            await API.post('/banking/transactions', data);
            toast('Entry posted');
            BankingPage.payeeTyped(data.payee, parseFloat(data.amount) > 0, 'qe-payee');
            closeModal();
            BankingPage.go(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    // The edit's save (#388). A change of category on an entry a completed
    // reconciliation holds comes back 409 ("This entry was reconciled on the
    // Sep 30, 2026 statement. Changing its category moves its amount
    // between accounts in a period already reconciled."): the sentence is
    // shown in the form with "Change it anyway", which saves what the form
    // holds then, with confirm_reconciled.
    async _putEntry(entryId, data, accountId) {
        try {
            const saved = await API.put(`/banking/entries/${entryId}`, data);
            toast(saved.note ? `Entry saved. ${saved.note}` : 'Entry saved');
            closeModal();
            BankingPage.go(`#/banking/${accountId}`);
        } catch (err) {
            if (err.status === 409 && err.detail && err.detail.code === 'reconciled' && !data.confirm_reconciled) {
                BankingPage._askReconciled(entryId, accountId, err.detail.message);
                return;
            }
            toast(err.message, 'error');
        }
    },

    _askReconciled(entryId, accountId, message) {
        const form = document.querySelector('#modal-body form');
        if (!form) { toast(message, 'error'); return; }
        BankingPage._pendingEdit = { entryId, accountId };
        let box = document.getElementById('entry-reconciled-warning');
        if (!box) {
            box = document.createElement('div');
            box.id = 'entry-reconciled-warning';
            box.setAttribute('role', 'alert');
            box.className = 'form-group full-width';
            box.style.cssText = 'display:block; padding:8px 10px; border:1px solid var(--warning, #b7791f); border-radius:4px; font-size:12px;';
            const actions = form.querySelector('.form-actions');
            if (actions) actions.before(box); else form.appendChild(box);
        }
        box.innerHTML = `<p style="margin:0 0 8px;">${escapeHtml(message)}</p>
            <button type="button" class="btn btn-sm btn-primary" onclick="BankingPage.saveEntryAnyway()">Change it anyway</button>
            <button type="button" class="btn btn-sm btn-secondary" onclick="BankingPage.keepEntry()">Keep it as it is</button>`;
        const first = box.querySelector('button');
        if (first) first.focus();
    },

    // "Change it anyway": the form as it is now, not as it was at the first
    // Save — a change made after the warning is saved too (#388 review)
    async saveEntryAnyway() {
        const pending = BankingPage._pendingEdit;
        const form = document.querySelector('#modal-body form');
        if (!pending || !form) return;
        let data;
        try { data = BankingPage._entryData(form, pending.accountId); } catch (err) { toast(err.message, 'error'); return; }
        BankingPage._pendingEdit = null;
        await BankingPage._putEntry(pending.entryId, { ...data, confirm_reconciled: true }, pending.accountId);
    },

    // "Keep it as it is": the warning goes and the focus goes back to the
    // form, on the category the warning was about (not lost to the page)
    keepEntry() {
        BankingPage._pendingEdit = null;
        const box = document.getElementById('entry-reconciled-warning');
        if (box) box.remove();
        const form = document.querySelector('#modal-body form');
        if (!form) return;
        const target = BankingPage._splitsOpen()
            ? form.querySelector('#entry-split-rows .split-cat')
            : form.category_account_id;
        // a picker with type-ahead is answered in its box, not the select
        const el = target && target._cbx ? target._cbx.box : target;
        (el || form.querySelector('button[type="submit"]')).focus();
    },

    // ------------------------------------------------------------------
    // A payee or a category typed that is not on the list is added from
    // the row or the form (#386, from a bookkeeper's notes): the payee as
    // a customer or a vendor, offered once per name and session; the
    // category as a new account under a chosen parent, made from the Add
    // button beside the picker (never from arrowing the select, #386 review).
    // ------------------------------------------------------------------
    CATEGORY_TYPES: ['expense', 'income', 'cogs', 'asset', 'liability', 'equity'],
    _accounts: null,      // the chart as last drawn: the parents offered, the picker's new option
    _vendorNames: null,   // lower-cased; null until the register has loaded them
    _customerNames: null, // lower-cased; null until the register has loaded them
    _payeesAsked: new Set(),

    // Which side the money is on for the quick-entry row and the + Entry
    // form: true = money in (a deposit), false = money out, null = not
    // known yet (no amount typed), so the offer waits until the entry posts.
    _rowMoneyIn() {
        const v = (id) => parseFloat((document.getElementById(id) || {}).value || '0') || 0;
        const out = v('qe-out'), inn = v('qe-in');
        if (inn > 0 && !(out > 0)) return true;
        if (out > 0 && !(inn > 0)) return false;
        return null;
    },
    _formMoneyIn() {
        const amt = document.querySelector('#modal-body [name="amount"]');
        const n = amt ? parseFloat(amt.value) : NaN;
        return Number.isNaN(n) || n === 0 ? null : n > 0;
    },

    // The payee as typed, on blur and again when the entry posts: not a
    // vendor's or a customer's name and not asked before, a toast offers to
    // add it — a customer for money in, a vendor for money out, the
    // categorized import's rule. Until the amount tells the side (moneyIn
    // null), the offer waits for the post, which knows it. The entry goes
    // on with the text either way; Not now, or no answer, is not asked
    // again this session. The toast moves focus to itself and gives it back
    // to the row, so the keyboard can answer (returnFocusId).
    payeeTyped(payee, moneyIn, returnFocusId) {
        const name = (payee || '').trim();
        if (!name || (!BankingPage._vendorNames && !BankingPage._customerNames)) return;
        const key = name.toLowerCase();
        const onList = (BankingPage._vendorNames && BankingPage._vendorNames.has(key))
            || (BankingPage._customerNames && BankingPage._customerNames.has(key));
        if (onList || BankingPage._payeesAsked.has(key)) return;
        if (moneyIn === null || moneyIn === undefined) return; // the post will ask once the side is known
        BankingPage._payeesAsked.add(key);
        const noun = moneyIn ? T('customer') : 'vendor';
        // TEST SEAM: window.__payeeToastMs is read only so browser tests
        // (test_browser_register_quick_add.py) can shorten the 15 s toast;
        // nothing in the app sets it.
        const ms = (typeof window !== 'undefined' && window.__payeeToastMs) || 15000;
        toastAction(
            `Add "${name}" as a ${noun}?`, 'Add',
            () => BankingPage.addParty(name, moneyIn), ms, 'Not now',
            { focusOnShow: true, returnFocus: () => document.getElementById(returnFocusId) },
        );
    },

    // Money in makes the payee a customer, money out a vendor — the same
    // name the entry posted with, so the register resolves to it (there is
    // no counterparty foreign key on a register entry; the link is by name,
    // as the categorized import's is).
    async addParty(name, moneyIn) {
        const path = moneyIn ? '/customers' : '/vendors';
        const list = moneyIn ? T('Customers') : 'Vendors';
        const noun = moneyIn ? T('customer') : 'vendor';
        const names = moneyIn ? BankingPage._customerNames : BankingPage._vendorNames;
        try {
            await API.post(path, { name });
            if (names) names.add(name.toLowerCase());
            toast(`${name} added to ${list}`);
        } catch (err) {
            // a near match (409): the entry keeps its text; no second party
            const dupes = err.status === 409 && err.detail && err.detail.duplicates;
            toast(dupes ? `Not added: ${dupes.map(d => d.name).join(', ')} is already a ${noun}` : err.message, 'error');
        }
    },

    // The panel under a category picker: name, type, an optional parent of
    // that type, an optional number. Hidden until the Add button beside the
    // picker opens it; nothing in it is `required`, so the entry form still
    // submits while it is hidden.
    _newAccountPanel(selectId, accountId) {
        const id = `${selectId}-new`;
        const key = `onkeydown="if (event.key === 'Enter') { event.preventDefault(); BankingPage.saveNewAccount('${selectId}', ${accountId}); }"`;
        const types = BankingPage.CATEGORY_TYPES
            .map(t => `<option value="${t}">${t.charAt(0).toUpperCase() + t.slice(1)}</option>`).join('');
        return `<div id="${id}" style="display:none; padding:8px; border:1px solid var(--gray-300); border-radius:4px; margin-top:4px;">
            <div class="form-grid">
                <div class="form-group"><label for="${id}-name">Account name *</label>
                    <input id="${id}-name" aria-label="New account name" aria-required="true" ${key}></div>
                <div class="form-group"><label for="${id}-type">Type</label>
                    <select id="${id}-type" data-no-search onchange="BankingPage.newAccountTypeChanged('${selectId}', ${accountId})">${types}</select></div>
                <div class="form-group"><label for="${id}-parent">Sub-account of</label>
                    <select id="${id}-parent" data-no-search><option value="">None</option></select></div>
                <div class="form-group"><label for="${id}-number">Number <span style="font-weight:normal; color:var(--gray-500);">(optional)</span></label>
                    <input id="${id}-number" maxlength="20" placeholder="e.g. 6150, or leave blank" ${key}></div>
            </div>
            <div style="display:flex; gap:8px; margin-top:6px;">
                <button type="button" class="btn btn-sm btn-primary" onclick="BankingPage.saveNewAccount('${selectId}', ${accountId})">Add account</button>
                <button type="button" class="btn btn-sm btn-secondary" onclick="BankingPage.cancelNewAccount('${selectId}')">Cancel</button>
            </div>
        </div>`;
    },

    // Opened by the Add button beside a category picker, never by the
    // select itself: arrowing through the categories must not spring the
    // panel open (#386 review — a native select with "+ Add…" on it trapped
    // the keyboard one ArrowDown from the top).
    openNewAccount(selectId, accountId) {
        const panel = document.getElementById(`${selectId}-new`);
        if (!panel) return;
        panel.style.display = '';
        BankingPage.newAccountTypeChanged(selectId, accountId);
        focusFor(document.getElementById(`${selectId}-new-name`), true);
    },

    // The parents offered are the accounts of the chosen type (a
    // sub-account takes its parent's type), never the register's own
    // account, another bank or card, or a control account the software
    // posts to by number (#386 review: 1100, 1200, 2000, 2200, 3200 …).
    newAccountTypeChanged(selectId, accountId) {
        const type = document.getElementById(`${selectId}-new-type`).value;
        const parent = document.getElementById(`${selectId}-new-parent`);
        parent.innerHTML = '<option value="">None</option>' + (BankingPage._accounts || [])
            .filter(a => a.account_type === type && a.id !== accountId && !a.bank_kind && !a.is_control)
            .map(a => `<option value="${a.id}">${accountLabel(a)}</option>`).join('');
    },

    cancelNewAccount(selectId) {
        // The panel opens from the Add button now, not from a "__new__"
        // choice, so the picker keeps whatever was already chosen — cancel
        // only closes the panel and gives the keyboard back to the select.
        const sel = document.getElementById(selectId);
        const panel = document.getElementById(`${selectId}-new`);
        if (panel) panel.style.display = 'none';
        if (sel) { focusFor(sel._cbx ? sel._cbx.box : sel, true); }
    },

    // POST /accounts, then the new account goes into every category picker
    // on the page (the split lines' list too) and is the choice; the cursor
    // moves on, to the amount in the row or the memo in the form.
    async saveNewAccount(selectId, accountId) {
        const f = (part) => document.getElementById(`${selectId}-new-${part}`);
        const name = f('name').value.trim();
        if (!name) { toast('Name the account', 'error'); focusFor(f('name'), true); return; }
        let made;
        try {
            made = await API.post('/accounts', {
                name,
                account_type: f('type').value,
                parent_id: f('parent').value ? parseInt(f('parent').value, 10) : null,
                account_number: f('number').value.trim() || null,
            });
        } catch (err) { toast(err.message, 'error'); return; }
        (BankingPage._accounts = BankingPage._accounts || []).push(made);
        const opt = `<option value="${made.id}">${accountLabel(made)}</option>`;
        for (const id of ['qe-category', 'entry-category']) {
            const s = document.getElementById(id);
            if (s) s.insertAdjacentHTML('beforeend', opt);
        }
        if (BankingPage._splitCats) BankingPage._splitCats += opt;
        f('name').value = ''; f('number').value = ''; f('parent').value = '';
        document.getElementById(`${selectId}-new`).style.display = 'none';
        document.getElementById(selectId).value = String(made.id);
        toast(`${made.account_number ? made.account_number + ' - ' : ''}${made.name} added to the chart`);
        const next = selectId === 'qe-category'
            ? document.getElementById('qe-out')
            : document.querySelector('#modal-body input[name="description"]');
        focusFor(next, true);
    },

    async showTransferForm(fromId = null) {
        const accounts = await API.get('/accounts?bank=1&active_only=true');
        const opt = (a, sel) => `<option value="${a.id}" ${sel ? 'selected' : ''}>${accountLabel(a)} (${BankingPage._kindLabel(a.bank_kind).toLowerCase()})</option>`;
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
            BankingPage.go(`#/banking/${t.from_account_id}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async showTransfers() {
        const rows = await API.get('/transfers');
        const body = rows.length ? `<div class="table-container"><table>
            <thead><tr><th scope="col">Date</th><th scope="col">From</th><th scope="col">To</th><th scope="col" class="amount">Amount</th><th scope="col">Memo</th><th scope="col"></th></tr></thead>
            <tbody>${rows.map(t => `<tr style="${t.status === 'void' ? 'color:var(--gray-400); text-decoration:line-through;' : ''}">
                <td>${formatDate(t.date)}</td><td>${escapeHtml(t.from_account_name)}</td><td>${escapeHtml(t.to_account_name)}</td>
                <td class="amount">${formatCurrency(t.amount)}</td><td>${escapeHtml(t.memo || '')}</td>
                <td>${t.status === 'void' ? '' : `<button data-destructive class="btn btn-sm btn-secondary" onclick="BankingPage.voidTransfer(${t.id})">Void</button>`}</td>
            </tr>`).join('')}</tbody></table></div>` : '<p>No transfers yet.</p>';
        openModal('Transfers', body);
    },

    async voidTransfer(id) {
        if (!confirm('Void this transfer?')) return;
        try {
            await API.post(`/transfers/${id}/void`);
            toast('Transfer voided');
            closeModal();
            BankingPage.go('#/banking');
        } catch (err) { toast(err.message, 'error'); }
    },

    // ------------------------------------------------------------------
    // Reconciliation — over the ledger's lines
    // ------------------------------------------------------------------
    async startReconcile(accountId) {
        // A statement on or before the last reconciled one is refused by
        // the server; the form says where the last one ended and won't
        // offer an earlier date.
        const ctx = BankingPage._ctx || {};
        const last = ctx.accountId === accountId ? ctx.lastReconciled : null;
        let minDate = '';
        if (last) {
            const d = new Date(last + 'T00:00:00');
            d.setDate(d.getDate() + 1);
            minDate = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
        }
        const defaultDate = minDate && todayISO() < minDate ? minDate : todayISO();
        openModal('Begin Reconciliation', `
            <form onsubmit="BankingPage.createReconciliation(event, ${accountId})">
                <p style="margin-bottom:12px; font-size:11px; color:var(--gray-500);">
                    Enter the ending date and balance from your statement. For a card, the balance is the amount owed.
                    ${last ? `This account is reconciled through ${formatDate(last)}; the statement date comes after that.` : ''}
                </p>
                <div class="form-grid">
                    <div class="form-group"><label>Statement Date *</label>
                        <input name="statement_date" type="date" required value="${defaultDate}" ${minDate ? `min="${minDate}"` : ''}></div>
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

    // The view can show only the lines dated through a day of the session
    // (#331): several statements reconciled back to back are started with
    // the last statement's date, and each month is worked with the later
    // lines hidden. The session's figures are the session's; the hidden
    // lines are counted under the table and Mark through takes the same day.
    _reconThrough: {},
    _reconDay: {},
    reconShowThrough(reconId, value) {
        BankingPage._reconThrough[reconId] = value || '';
        BankingPage.showReconcileView(reconId);
    },
    // The day box: a new day re-filters the list while the box is ticked,
    // and is kept either way, so Mark through that date and the list agree
    // (2.23.5 round 2, W-15 / NEW-143: the list stayed on the old day and
    // Mark through marked the hidden lines).
    reconDayChanged(reconId, value) {
        BankingPage._reconDay[reconId] = value || '';
        const only = document.getElementById('recon-only-through');
        if (only && only.checked) BankingPage.reconShowThrough(reconId, value);
    },
    async showReconcileView(reconId) {
        const data = await API.get(`/banking/reconciliations/${reconId}/transactions`);
        const through = BankingPage._reconThrough[reconId] || '';
        const day = through || BankingPage._reconDay[reconId] || data.statement_date;
        const shown = through ? data.transactions.filter(t => t.date <= through) : data.transactions;
        const hidden = data.transactions.length - shown.length;
        const rows = shown.map(t => {
            const cls = t.reconciled ? 'style="background:var(--primary-light);"' : '';
            const amtCls = t.amount >= 0 ? 'color:var(--text-success)' : 'color:var(--text-danger)';
            const said = ['Cleared', formatDate(t.date), t.payee || t.description, formatCurrency(t.amount)].filter(Boolean).join(', ');
            return `<tr ${cls}>
                <td><input type="checkbox" ${t.reconciled ? 'checked' : ''} aria-label="${escapeHtml(said)}"
                    onchange="BankingPage.toggleCleared(${reconId}, ${t.id}, this)"></td>
                <td>${formatDate(t.date)}</td>
                <td>${escapeHtml(t.payee || t.description || '')}${t.matched ? ' <span title="matched to a statement line" style="color:var(--text-success);">●</span>' : ''}</td>
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
                    <button class="btn btn-secondary" onclick="BankingPage.go('#/banking/${data.account_id}')">Later</button>
                    <button class="btn btn-secondary" onclick="BankingPage.abandonReconcile(${reconId}, ${data.account_id})">Abandon</button>
                    <button class="btn btn-primary" id="recon-finish-btn" onclick="BankingPage.finishReconcile(${reconId}, ${data.account_id})">Finish Reconciliation</button>
                </div>
            </div>
            <div id="recon-finish-msg" role="alert" style="font-size:12px; color:var(--danger); margin-bottom:8px;"></div>
            ${data.ticked_after_date > 0 ? `<div role="status" style="font-size:12px; color:var(--gray-600); margin-bottom:8px;">${data.ticked_after_date} ticked line${data.ticked_after_date === 1 ? '' : 's'} ${data.ticked_after_date === 1 ? 'falls' : 'fall'} after the statement date and won't be stamped when you finish; ${data.ticked_after_date === 1 ? 'it carries' : 'they carry'} to the next reconciliation, still ticked.</div>` : ''}
            <div class="card-grid" style="margin-bottom:16px;">
                <div class="card"><div class="card-header">Beginning Balance</div>
                    <div class="card-value">${formatCurrency(data.beginning_balance)}</div></div>
                <div class="card"><div class="card-header">Cleared</div>
                    <div class="card-value" id="recon-cleared">${formatCurrency(data.cleared_total)}</div></div>
                <div class="card"><div class="card-header">Statement Balance</div>
                    <div class="card-value">${formatCurrency(data.statement_balance)}</div>
                    <button type="button" class="btn btn-sm btn-secondary" style="margin-top:4px;" data-write
                        aria-label="Change the statement date or ending balance"
                        onclick="BankingPage.changeStatement(${reconId}, '${data.statement_date}', ${data.statement_balance})">Change…</button></div>
                <div class="card"><div class="card-header">Difference</div>
                    <div class="card-value" id="recon-diff" style="color:${diffColor}">${formatCurrency(data.difference)}</div>
                    <div style="font-size:11px; font-weight:600;" role="status">${balanced ? '✓ Balanced — ready to finish' : '⚠ Out of balance — finish when this is $0.00'}</div></div>
            </div>
            <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:8px;" data-write>
                <button class="btn btn-sm btn-secondary" onclick="BankingPage.markAll(${reconId}, true)">Mark all</button>
                <label for="recon-through" style="font-size:11px;">Through</label>
                <input type="date" id="recon-through" value="${escapeHtml(day)}" max="${data.statement_date}" style="font-size:11px; padding:2px 6px;"
                    aria-describedby="recon-through-note" onchange="BankingPage.reconDayChanged(${reconId}, this.value)">
                <label style="font-size:11px; font-weight:normal;"><input type="checkbox" id="recon-only-through" ${through ? 'checked' : ''} style="width:auto; vertical-align:middle;"
                    onchange="BankingPage.reconShowThrough(${reconId}, this.checked ? document.getElementById('recon-through').value : '')"> Show only lines through that date</label>
                <button class="btn btn-sm btn-secondary" onclick="BankingPage.markAll(${reconId}, true, document.getElementById('recon-through').value)">Mark through that date</button>
                <button class="btn btn-sm btn-secondary" onclick="BankingPage.markAll(${reconId}, false)">Clear all</button>
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col" style="width:30px;"></th><th scope="col">Date</th><th scope="col">Payee / Description</th><th scope="col">Ref #</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${rows || '<tr><td colspan="5" style="text-align:center;">No ledger lines up to this date</td></tr>'}</tbody>
            </table></div>
            <div id="recon-through-note" role="status" style="font-size:11px; color:var(--gray-500); margin-top:6px;">${hidden ? `${hidden} later line${hidden === 1 ? '' : 's'} hidden (dated after ${formatDate(through)}); the figures above count the whole session` : ''}</div>`;
    },

    // The statement's ending balance and date can be corrected while the
    // reconciliation is in progress (#389): a transposed digit was a fresh
    // start. The ticks stay; the view re-renders with the new difference.
    changeStatement(reconId, statementDate, statementBalance) {
        openModal('Change Statement', `
            <form onsubmit="BankingPage.saveStatement(event, ${reconId})">
                <p style="margin-bottom:12px; font-size:11px; color:var(--gray-500);">
                    Correct the ending date or balance from your statement. The lines you have ticked stay ticked; the difference is refigured.
                </p>
                <div class="form-grid">
                    <div class="form-group"><label>Statement Date *</label>
                        <input name="statement_date" type="date" required value="${escapeHtml(statementDate)}"></div>
                    <div class="form-group"><label>Statement Ending Balance *</label>
                        <input name="statement_balance" type="number" step="0.01" required value="${Number(statementBalance).toFixed(2)}"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save</button>
                </div>
            </form>`);
    },

    async saveStatement(e, reconId) {
        e.preventDefault();
        const form = e.target;
        try {
            const res = await API.put(`/banking/reconciliations/${reconId}`, {
                statement_date: form.statement_date.value,
                statement_balance: form.statement_balance.value,
            });
            closeModal();
            // The Through day and filter are bounded by the statement date;
            // start them over so they cannot sit past a date moved earlier.
            BankingPage._reconThrough[reconId] = '';
            BankingPage._reconDay[reconId] = '';
            // A date moved earlier can leave lines ticked past it; they are
            // not on this session and carry to the next one (#400 review).
            const n = res.ticked_after_date;
            toast(n > 0
                ? `Statement changed. ${n} ticked line${n === 1 ? '' : 's'} ${n === 1 ? 'falls' : 'fall'} after the new date; ${n === 1 ? 'it carries' : 'they carry'} to the next reconciliation, still ticked.`
                : 'Statement changed');
            BankingPage.showReconcileView(reconId);
        } catch (err) { toast(err.message, 'error'); }
    },

    // Every line at once (#258): ticked, ticked through a date, or all
    // unticked; the view re-renders with the new difference, as one tick
    // does. Lines after the statement date are never on the session.
    async markAll(reconId, cleared, through = null) {
        try {
            const res = await API.post(`/banking/reconciliations/${reconId}/mark-all`, { cleared, ...(through ? { through } : {}) });
            toast(`${res.changed} line${res.changed === 1 ? '' : 's'} ${cleared ? 'marked' : 'cleared'}; difference ${formatCurrency(res.difference)}`);
            BankingPage.showReconcileView(reconId);
        } catch (err) { toast(err.message, 'error'); }
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

    // An out-of-balance Finish was a disabled button: it did nothing and
    // said nothing (exploratory 2.17.3, W-M20). It says why now — the
    // difference, and what to do about it — and a finished reconciliation
    // opens its report.
    async finishReconcile(reconId, accountId) {
        const msg = $('#recon-finish-msg');
        try {
            const data = await API.get(`/banking/reconciliations/${reconId}/transactions`);
            if (Math.abs(data.difference) >= 0.005) {
                const why = `Not finished: the difference is ${formatCurrency(data.difference)}, and it must be $0.00. Tick the lines that are on your statement, or check the statement's ending balance.`;
                if (msg) msg.textContent = why;
                toast(why, 'error');
                return;
            }
            if (!confirm('Mark this reconciliation as complete? Cleared lines are locked.')) return;
            await API.post(`/banking/reconciliations/${reconId}/complete`);
            toast('Reconciliation completed');
            BankingPage.go(`#/banking/${accountId}`);
            BankingPage.showReconReport(reconId);
        } catch (err) {
            if (msg) msg.textContent = err.message;
            toast(err.message, 'error');
        }
    },

    async showReconciliations(accountId) {
        try {
            const rows = await API.get(`/banking/reconciliations?account_id=${accountId}`);
            const body = rows.length ? `<div class="table-container"><table>
                <thead><tr><th scope="col">Statement date</th><th scope="col" class="amount">Ending balance</th><th scope="col">Status</th><th scope="col"></th></tr></thead>
                <tbody>${rows.map(r => `<tr>
                    <td>${formatDate(r.statement_date)}</td>
                    <td class="amount">${formatCurrency(r.statement_balance)}</td>
                    <td>${r.status === 'completed' ? `Completed ${r.completed_at ? formatDate(r.completed_at) : ''}` : 'In progress'}</td>
                    <td style="white-space:nowrap;">${r.status === 'completed'
                        ? `<button class="btn btn-sm btn-secondary" onclick="BankingPage.showReconReport(${r.id})">Report</button>
                           <button class="btn btn-sm btn-secondary" onclick="window.open('/api/banking/reconciliations/${r.id}/pdf', '_blank')">Save PDF</button>`
                        : `<button class="btn btn-sm btn-primary" onclick="closeModal(); BankingPage.showReconcileView(${r.id})">Continue</button>`}</td>
                </tr>`).join('')}</tbody></table></div>` : '<p>No reconciliations for this account yet.</p>';
            openModal('Reconciliations', body + `<div class="form-actions"><button class="btn btn-secondary" onclick="closeModal()">Close</button></div>`);
        } catch (err) { toast(err.message, 'error'); }
    },

    // `onClose` reopens whatever list View came from (the Report Center's
    // Reconciliation Reports card, #395): the report replaces that modal, so
    // without it Close would land past the list. None → a plain Close.
    async showReconReport(reconId, onClose) {
        try {
            const r = await API.get(`/banking/reconciliations/${reconId}/report`);
            const L = r.labels;
            const line = (label, amount, style = '') => `<tr style="${style}"><td>${escapeHtml(label)}</td><td class="amount">${formatCurrency(amount)}</td></tr>`;
            const bold = 'font-weight:700; background:var(--gray-50);';
            const summary = `<div class="table-container"><table><tbody>
                ${line('Beginning balance', r.beginning_balance)}
                ${line(`Cleared ${L.increase.toLowerCase()} (${r.cleared.increase.count})`, r.cleared.increase.total)}
                ${line(`Cleared ${L.decrease.toLowerCase()} (${r.cleared.decrease.count})`, r.cleared.decrease.total)}
                ${line('Cleared balance', r.cleared_balance, bold)}
                ${line('Statement ending balance', r.ending_balance)}
                ${line('Difference', r.difference, bold)}
                ${line(`Uncleared ${L.increase.toLowerCase()} (${r.uncleared.increase.count})`, r.uncleared.increase.total)}
                ${line(`Uncleared ${L.decrease.toLowerCase()} (${r.uncleared.decrease.count})`, r.uncleared.decrease.total)}
                ${line(`Register balance as of ${formatDate(r.statement_date)}`, r.register_balance, bold)}
            </tbody></table></div>`;
            const detail = [['cleared', 'Cleared'], ['uncleared', 'Uncleared']].map(([state, title]) =>
                ['increase', 'decrease'].filter(side => r[state][side].items.length).map(side => `
                    <h4 style="margin:12px 0 4px; font-size:12px;">${title} ${escapeHtml(L[side].toLowerCase())}</h4>
                    <div class="table-container"><table>
                        <thead><tr><th scope="col">Date</th><th scope="col">Ref #</th><th scope="col">Payee / description</th><th scope="col" class="amount">Amount</th></tr></thead>
                        <tbody>${r[state][side].items.map(i => `<tr>
                            <td>${formatDate(i.date)}</td><td>${escapeHtml(i.reference || '')}</td>
                            <td>${escapeHtml(i.payee || i.description || '')}</td><td class="amount">${formatCurrency(i.amount)}</td>
                        </tr>`).join('')}</tbody>
                    </table></div>`).join('')).join('');
            BankingPage._reconReportBack = typeof onClose === 'function' ? onClose : null;
            openModal(`Reconciliation — ${r.account_name}, statement of ${formatDate(r.statement_date)}`, `
                ${summary}${detail}
                <div class="form-actions">
                    <button class="btn btn-secondary" onclick="BankingPage._closeReconReport()">Close</button>
                    <button class="btn btn-primary" onclick="window.open('/api/banking/reconciliations/${reconId}/pdf', '_blank')">Save PDF</button>
                </div>`);
        } catch (err) { toast(err.message, 'error'); }
    },

    // Close the report: back to the list it was opened from (#395), or a
    // plain close when it was opened on its own.
    _closeReconReport() {
        const back = BankingPage._reconReportBack;
        BankingPage._reconReportBack = null;
        if (back) back(); else closeModal();
    },

    async abandonReconcile(reconId, accountId) {
        if (!confirm('Abandon this reconciliation? Ticks are kept.')) return;
        try {
            await API.del(`/banking/reconciliations/${reconId}`);
            BankingPage.go(`#/banking/${accountId}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    // ------------------------------------------------------------------
    // File import (OFX/QFX; CSV: Bank of America, Chase checking/credit,
    // PayPal, or any file whose header names a date, a description and an
    // amount — and, for one that doesn't, a step that asks which column is
    // which) — needs a feed on the account; offers to create one.
    // ------------------------------------------------------------------
    _csvMapping: null,   // the mapping step's answer, sent with preview and import
    _csvLayout: null,    // the columns and sample rows the step shows

    async showOFXImport(accountId) {
        const feeds = await API.get('/banking/accounts');
        const feed = feeds.find(f => f.account_id === accountId);
        if (!feed) {
            if (confirm('Importing needs a bank feed (statement identity) on this account. Create one now?')) BankingPage.showAccountForm();
            return;
        }
        const feedId = feed.id;
        BankingPage._csvMapping = null;
        BankingPage._csvLayout = null;
        openModal('Import Bank File', `
            <form id="ofx-form" onsubmit="BankingPage.previewOFX(event, ${feedId}, ${accountId})">
                <div class="form-group">
                    <label>Select an OFX/QFX file, or a CSV export (Bank of America, Chase checking, Chase credit, PayPal, or any file with date, description and amount columns)</label>
                    <input type="file" name="file" accept=".ofx,.qfx,.csv" required id="ofx-file" onchange="BankingPage._csvMapping = null">
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
        if (isCsv && BankingPage._csvMapping) formData.append('mapping', JSON.stringify(BankingPage._csvMapping));
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
            if (!resp.ok) throw new Error(await API.responseError(resp, 'Parse failed'));
            const data = await resp.json();
            if (isCsv && data.error && data.header_row) {
                // A layout detection missed ("Unknown CSV format" with no
                // way forward — exploratory 2.17.3, W-L15): ask which
                // column is which, then preview again with the answer.
                $('#ofx-preview').innerHTML = BankingPage._mappingStep(data, data.has_header !== false);
                return;
            }
            if (isCsv && data.error) throw new Error(data.error);
            const layout = data.format === 'generic'
                ? (BankingPage._csvMapping ? 'the columns you chose' : 'date, description and amount columns')
                : data.format;
            const rows = data.transactions.map(t => `<tr>
                <td>${escapeHtml(t.date || '')}</td>
                <td>${escapeHtml(t.payee || '')}</td>
                <td class="amount" style="${t.amount >= 0 ? 'color:var(--text-success)' : 'color:var(--text-danger)'}">${formatCurrency(t.amount)}</td>
                <td>${escapeHtml(isCsv ? (t.description || '') : (t.fitid || ''))}</td>
            </tr>`).join('');
            $('#ofx-preview').innerHTML = `
                <div style="margin-bottom:8px; font-size:11px;">
                    <strong>${data.transactions.length}</strong> transactions found.
                    ${isCsv && data.format ? `Format: ${escapeHtml(layout)}.` : ''}
                    ${isCsv && data.unread ? `${data.unread} row${data.unread === 1 ? '' : 's'} could not be read and will be skipped.` : ''}
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

    _mappingStep(layout, hasHeader) {
        BankingPage._csvLayout = layout;
        const head = layout.header_row || [];
        const sample = hasHeader ? (layout.sample || []) : [head].concat(layout.sample || []).slice(0, 5);
        const names = head.map((c, i) => (hasHeader && c) ? `${i + 1}: ${c}` : `Column ${i + 1}`);
        const chosen = BankingPage._csvMapping || {};
        const guess = layout.suggested || {};
        const pick = (role, required) => {
            const current = chosen[role] !== undefined && chosen[role] !== null ? chosen[role] : guess[role];
            return `<select id="csv-map-${role}">
                <option value="">${required ? 'Choose…' : '— none —'}</option>
                ${names.map((n, i) => `<option value="${i}" ${current === i ? 'selected' : ''}>${escapeHtml(n)}</option>`).join('')}
            </select>`;
        };
        const fmt = chosen.date_format || 'auto';
        const fmtOpt = (value, text) => `<option value="${value}" ${fmt === value ? 'selected' : ''}>${text}</option>`;
        return `
            <div style="font-size:11px; margin-bottom:8px;" role="status">${escapeHtml(layout.error || '')}</div>
            <div class="form-grid">
                <div class="form-group full-width"><label><input type="checkbox" id="csv-map-header" ${hasHeader ? 'checked' : ''}
                    onchange="BankingPage._redrawMapping(this.checked)"> The first row holds column names</label></div>
                <div class="form-group"><label for="csv-map-date">Date *</label>${pick('date', true)}</div>
                <div class="form-group"><label for="csv-map-date_format">Date format</label>
                    <select id="csv-map-date_format">${fmtOpt('auto', 'Work it out')}${fmtOpt('MM/DD/YYYY', 'MM/DD/YYYY')}${fmtOpt('DD/MM/YYYY', 'DD/MM/YYYY')}${fmtOpt('YYYY-MM-DD', 'YYYY-MM-DD')}</select></div>
                <div class="form-group"><label for="csv-map-description">Description *</label>${pick('description', true)}</div>
                <div class="form-group"><label for="csv-map-payee">Payee</label>${pick('payee')}</div>
                <div class="form-group"><label for="csv-map-amount">Amount (one column; money out is negative)</label>${pick('amount')}</div>
                <div class="form-group"><label for="csv-map-check_number">Check #</label>${pick('check_number')}</div>
                <div class="form-group"><label for="csv-map-debit">…or money out (debit)</label>${pick('debit')}</div>
                <div class="form-group"><label for="csv-map-credit">…and money in (credit)</label>${pick('credit')}</div>
            </div>
            <div class="table-container" style="max-height:180px; overflow:auto;"><table>
                <thead><tr>${names.map(n => `<th scope="col">${escapeHtml(n)}</th>`).join('')}</tr></thead>
                <tbody>${sample.map(r => `<tr>${names.map((_, i) => `<td>${escapeHtml(r[i] || '')}</td>`).join('')}</tr>`).join('')}</tbody>
            </table></div>
            <div class="form-actions" style="margin-top:12px;">
                <button type="button" class="btn btn-primary" onclick="BankingPage.previewMapped()">Preview with these columns</button>
            </div>`;
    },

    _redrawMapping(hasHeader) {
        if (BankingPage._csvLayout) $('#ofx-preview').innerHTML = BankingPage._mappingStep(BankingPage._csvLayout, hasHeader);
    },

    previewMapped() {
        const col = role => {
            const el = $(`#csv-map-${role}`);
            return el && el.value !== '' ? parseInt(el.value, 10) : null;
        };
        BankingPage._csvMapping = {
            date: col('date'),
            description: col('description'),
            payee: col('payee'),
            amount: col('amount'),
            debit: col('debit'),
            credit: col('credit'),
            check_number: col('check_number'),
            date_format: $('#csv-map-date_format').value,
            has_header: $('#csv-map-header').checked,
        };
        $('#ofx-form').requestSubmit();
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
            if (isCsv && BankingPage._csvMapping) formData.append('mapping', JSON.stringify(BankingPage._csvMapping));
            const endpoint = isCsv
                ? `/api/bank-import/import-csv/${feedId}`
                : `/api/bank-import/import/${feedId}`;
            const resp = await fetch(endpoint, { method: 'POST', body: formData });
            if (!resp.ok) throw new Error(await API.responseError(resp, 'Import failed'));
            const data = await resp.json();
            toast(`Imported ${data.imported} (${data.skipped} duplicates skipped, ${data.matched || 0} matched to the books)`);
            closeModal();
            BankingPage.go(`#/banking/${accountId}`);
        } catch (err) {
            toast(err.message, 'error');
        } finally {
            // A failed import hands the button back, on every engine.
            if (importBtn) { importBtn.disabled = false; importBtn.textContent = label; }
        }
    },
};
