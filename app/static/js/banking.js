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
    go(hash) {
        if (location.hash === hash) App.navigate(hash);
        else location.hash = hash;
    },
    _cols(kind) { return kind === 'credit_card' ? ['Charge', 'Payment'] : ['Payment', 'Deposit']; },

    // ------------------------------------------------------------------
    // Overview
    // ------------------------------------------------------------------
    async render() {
        const [rows, feed, feeds, mercury] = await Promise.all([
            API.get('/banking/overview'),
            API.get('/simplefin/status'),
            API.get('/banking/accounts'),
            API.get('/mercury/status'),
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
        html += BankingPage._renderMercurySection(mercury, feeds);
        return html;
    },

    _renderMercurySection(feed, feeds) {
        let body;
        if (!feed.connected) {
            body = `<p>See payments arriving in your Mercury account in SlowBooks.
                Create a <strong>Read Only</strong> token in
                <a href="https://app.mercury.com/settings/tokens" target="_blank" rel="noopener">Mercury’s API token settings</a>,
                then connect here. Your token is stored encrypted on this server.</p>
                <form onsubmit="BankingPage.connectMercury(event)" data-admin>
                    <div class="form-group">
                        <label for="mercury-token">Mercury read-only API token</label>
                        <input id="mercury-token" type="password" required autocomplete="off" maxlength="4096">
                    </div>
                    <button class="btn btn-primary" type="submit">Connect Mercury</button>
                </form>`;
        } else {
            const choices = feeds.filter(a => a.is_active && a.bank_kind === 'bank');
            const rows = feed.accounts.map(a => `<tr data-mercury-account="${escapeHtml(a.id)}">
                <td>${escapeHtml(a.name)} ${a.last_four ? '••••' + escapeHtml(a.last_four) : ''}</td>
                <td class="amount mercury-current">${escapeHtml(a.balance)} USD</td>
                <td class="amount mercury-available">${escapeHtml(a.available_balance)} USD</td>
                <td class="amount mercury-inflight">${a.in_flight_balance === undefined ? '—' : escapeHtml(a.in_flight_balance) + ' USD'}</td>
                <td><select class="mercury-map" data-mercury-id="${escapeHtml(a.id)}" data-write
                    aria-label="Import ${escapeHtml(a.name)} into">
                    <option value="">— not imported —</option>
                    ${choices.map(b => `<option value="${b.id}" ${feed.account_map[a.id] === b.id ? 'selected' : ''}>${escapeHtml(b.name)}</option>`).join('')}
                </select></td></tr>`).join('');
            body = `<p>Sync posted deposits and withdrawals into <em>To review</em>.
                Match them to payments already recorded, or review and add them to your books.
                Syncing does not mark invoices paid.</p>
                ${!choices.length ? '<p>Add a bank account using <strong>+ New Bank Account</strong> above, then map it here.</p>' : ''}
                <p>${feed.last_sync ? 'Last successful sync: ' + escapeHtml(feed.last_sync.replace('T', ' ')) : 'Not synced yet.'}</p>
                <div class="table-container"><table id="mercury-balances">
                    <thead><tr><th scope="col">Mercury account</th><th scope="col" class="amount">Current balance</th><th scope="col" class="amount">Available</th><th scope="col" class="amount">Incoming transfers</th><th scope="col">Imports into</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>
                <p id="mercury-balance-status" aria-live="polite">${escapeHtml(BankingPage._mercuryBalanceStatus(feed))}</p>
                <form onsubmit="BankingPage.syncMercury(event)" data-write>
                    <div class="form-group" style="margin-top:12px;">
                        <label for="mercury-since">Import transactions posted since</label>
                        <input type="date" id="mercury-since" value="${escapeHtml(feed.since)}" max="${todayISO()}" required>
                    </div>
                    <p>Each sync checks this date range again and skips transactions already imported.
                        Choose an earlier date to bring in more history. Use one feed source per bank account to avoid duplicates from other imports.</p>
                    <div class="form-actions">
                        <button type="submit" class="btn btn-primary">Sync Mercury</button>
                        <button type="button" class="btn btn-secondary" onclick="BankingPage.refreshMercury(this)">Refresh accounts</button>
                        <button type="button" class="btn btn-secondary" data-admin onclick="BankingPage.disconnectMercury(this)">Disconnect</button>
                    </div>
                </form>
                ${BankingPage._renderMercuryWebhook(feed.webhook)}`;
        }
        clearTimeout(BankingPage._mercuryBalanceTimer);
        if (feed.connected) BankingPage._mercuryBalanceTimer = setTimeout(() => BankingPage.pollMercuryBalances(), 15000);
        return `<div class="card" style="margin-top:16px;"><div class="card-header">Bank Feed (Mercury)</div>${body}</div>`;
    },

    _mercuryBalanceStatus(feed) {
        const updated = feed.accounts.map(a => a.balance_updated_at || '').sort().pop();
        return (updated ? `Balances updated: ${updated.replace('T', ' ')}. ` : '') +
            (feed.webhook.last_received ? `Last webhook received: ${feed.webhook.last_received.replace('T', ' ')}.` :
                feed.webhook.configured ? 'Waiting for the first webhook.' : 'Balance webhooks are not configured.');
    },

    async pollMercuryBalances() {
        clearTimeout(BankingPage._mercuryBalanceTimer);
        const table = $('#mercury-balances');
        if (location.hash !== '#/banking' || !table) return;
        try {
            if (!document.hidden) {
                const feed = await API.get('/mercury/status');
                if ($('#mercury-balances') !== table) return;
                if (!feed.connected) { BankingPage.go('#/banking'); return; }
                for (const row of table.querySelectorAll('[data-mercury-account]')) {
                    const account = feed.accounts.find(a => a.id === row.dataset.mercuryAccount);
                    if (!account) continue;
                    for (const [className, field] of [['mercury-current', 'balance'], ['mercury-available', 'available_balance'], ['mercury-inflight', 'in_flight_balance']]) {
                        row.querySelector('.' + className).textContent = account[field] === undefined ? '—' : account[field] + ' USD';
                    }
                }
                $('#mercury-balance-status').textContent = BankingPage._mercuryBalanceStatus(feed);
            }
        } catch (err) {
            if ($('#mercury-balances') === table) $('#mercury-balance-status').textContent = 'Could not refresh balance updates. Retrying shortly.';
        } finally {
            if ($('#mercury-balances') === table && location.hash === '#/banking')
                BankingPage._mercuryBalanceTimer = setTimeout(() => BankingPage.pollMercuryBalances(), 15000);
        }
    },

    _renderMercuryWebhook(webhook) {
        const url = location.origin + webhook.path;
        const registration = JSON.stringify({ url, eventTypes: webhook.event_types }, null, 2);
        return `<details style="margin-top:16px;"><summary>Balance webhooks</summary>
            <p>Receive current, available, and incoming-transfer balance updates automatically.
                The balances above refresh every 15 seconds while Banking is open. These updates do not post ledger entries.</p>
            <p>Register the HTTPS URL below with Mercury for checking and savings balance events.
                <a href="https://docs.mercury.com/reference/createwebhook" target="_blank" rel="noopener">Mercury webhook registration</a>
                returns a signing secret; save it here. Registering requires Mercury permission to manage webhooks.</p>
            ${location.protocol !== 'https:' ? '<p>Open SlowBooks at your public HTTPS address before copying this registration. Mercury cannot deliver to a LAN address.</p>' : ''}
            <div class="form-group"><label for="mercury-webhook-url">Webhook URL</label>
                <input id="mercury-webhook-url" readonly value="${escapeHtml(url)}"></div>
            <div class="form-group"><label for="mercury-webhook-registration">Mercury registration JSON</label>
                <textarea id="mercury-webhook-registration" rows="7" readonly>${escapeHtml(registration)}</textarea></div>
            <form onsubmit="BankingPage.saveMercuryWebhook(event)" data-admin>
                <div class="form-group"><label for="mercury-webhook-secret">Webhook signing secret</label>
                    <input type="password" id="mercury-webhook-secret" required autocomplete="off" maxlength="4096"
                        placeholder="${webhook.configured ? 'Secret saved — enter a new one to replace it' : 'Paste the signing secret returned by Mercury'}"></div>
                <div class="form-actions"><button type="submit" class="btn btn-primary">Save webhook secret</button>
                    ${webhook.configured ? '<button type="button" class="btn btn-secondary" onclick="BankingPage.disableMercuryWebhook(this)">Disable webhooks</button>' : ''}</div>
            </form></details>`;
    },

    async saveMercuryWebhook(e) {
        e.preventDefault();
        const button = e.target.querySelector('button[type=submit]');
        button.disabled = true;
        try {
            await API.post('/mercury/webhook-settings', { signing_secret: $('#mercury-webhook-secret').value.trim() });
            $('#mercury-webhook-secret').value = '';
            toast('Webhook secret saved. Use Mercury’s Verify endpoint to send a test event.');
            BankingPage.go('#/banking');
        } catch (err) { toast(err.message, 'error'); }
        finally { button.disabled = false; }
    },

    async disableMercuryWebhook(button) {
        if (!confirm('Stop accepting Mercury webhooks? Also pause or delete the endpoint in Mercury.')) return;
        button.disabled = true;
        try {
            await API.del('/mercury/webhook-settings');
            BankingPage.go('#/banking');
        } catch (err) { toast(err.message, 'error'); }
        finally { button.disabled = false; }
    },

    async connectMercury(e) {
        e.preventDefault();
        const input = $('#mercury-token');
        const button = e.target.querySelector('button[type=submit]');
        button.disabled = true;
        button.textContent = 'Connecting…';
        try {
            await API.post('/mercury/connect', { api_token: input.value.trim() });
            input.value = '';
            toast('Mercury connected. Choose which bank account to import into.');
            BankingPage.go('#/banking');
        } catch (err) { toast(err.message, 'error'); }
        finally { button.disabled = false; button.textContent = 'Connect Mercury'; }
    },

    async syncMercury(e) {
        e.preventDefault();
        const button = e.target.querySelector('button[type=submit]');
        const since = $('#mercury-since').value;
        const mapping = {};
        document.querySelectorAll('.mercury-map').forEach(sel => {
            if (sel.value) mapping[sel.dataset.mercuryId] = Number(sel.value);
        });
        button.disabled = true;
        button.textContent = 'Syncing…';
        try {
            await API.post('/mercury/map', { mapping });
            const r = await API.post('/mercury/sync', { since });
            toast(`Mercury: ${r.imported} new, ${r.skipped} already imported, ${r.matched} matched`);
            BankingPage.go('#/banking');
            if (r.warnings.length) openModal('Mercury sync — review needed',
                r.warnings.map(w => `<p>${escapeHtml(w)}</p>`).join(''));
        } catch (err) { toast(err.message, 'error'); }
        finally { button.disabled = false; button.textContent = 'Sync Mercury'; }
    },

    async refreshMercury(button) {
        button.disabled = true;
        try {
            await API.post('/mercury/refresh');
            BankingPage.go('#/banking');
        } catch (err) { toast(err.message, 'error'); }
        finally { button.disabled = false; }
    },

    async disconnectMercury(button) {
        if (!confirm('Disconnect Mercury? Imported transactions stay in your books.')) return;
        button.disabled = true;
        try {
            await API.post('/mercury/disconnect');
            toast('Mercury disconnected');
            BankingPage.go('#/banking');
        } catch (err) { toast(err.message, 'error'); }
        finally { button.disabled = false; }
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
            const rows = feed.accounts.map(a => `<tr>
                    <td>${escapeHtml(a.name)}<div style="font-size:10px; color:var(--gray-400);">${escapeHtml(a.org || '')}</div></td>
                    <td class="amount">${escapeHtml(a.balance)} ${escapeHtml(a.currency)}</td>
                    <td><select data-sfid="${escapeHtml(a.id)}" class="simplefin-map" data-write>${options(a.id)}</select></td>
                </tr>`).join('');
            body = `
                <p style="font-size:12px; margin-bottom:8px;">
                    Connected. Choose which SlowBooks bank account each feed lands in, then sync —
                    duplicates are skipped, bank rules suggest categories, and matches to postings you
                    already made are found. Everything else waits in the account's <em>To review</em> list.
                    ${feed.last_sync ? `Last sync: ${escapeHtml(feed.last_sync.replace('T', ' '))}` : 'Not synced yet.'}
                    The first sync brings in about the last three months; <em>Fetch older history</em> reaches
                    back as far as your SimpleFIN provider keeps.
                </p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Bank feed</th><th scope="col" class="amount">Balance</th><th scope="col">Imports into</th></tr></thead>
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
        await API.post('/simplefin/map', { mapping });
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
        const [reg, overview, classes] = await Promise.all([
            API.get(`/banking/check-register?account_id=${id}${filterQs}`),
            API.get('/banking/overview'),
            API.get('/classes?include_archived=true').catch(() => []),
        ]);
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

        if (!reg.entries.length) {
            html += `<div class="empty-state"><p>${filtered ? 'Nothing posted to this account for these filters' : 'Nothing posted to this account yet'}</p></div>`;
        } else {
            const rows = reg.entries.slice().reverse().map(e => `
                <tr style="${e.voided ? 'color:var(--gray-400); text-decoration:line-through;' : ''}">
                    <td>${formatDate(e.date)}</td>
                    <td>${escapeHtml(e.payee || '')}</td>
                    <td><a href="${escapeHtml(BankingPage._entryHref(e))}">${escapeHtml(e.description || 'View entry')}</a></td>
                    <td>${escapeHtml(e.reference || '')}</td>
                    <td style="font-size:10px; color:var(--gray-500);">${escapeHtml(sourceWord(e.source_type))}</td>
                    <td class="amount">${e.payment > 0 ? formatCurrency(e.payment) : ''}</td>
                    <td class="amount">${e.deposit > 0 ? formatCurrency(e.deposit) : ''}</td>
                    <td class="amount" style="font-weight:700;">${formatCurrency(e.balance)}</td>
                    <td style="text-align:center;">${e.reconciliation_id ? 'R' : (e.cleared ? '✓' : '')}</td>
                    <td>${e.voidable ? `<button data-destructive class="btn btn-sm btn-secondary" onclick="BankingPage.voidEntry('${e.source_type}', ${e.transaction_id}, ${id})">Void</button>` : ''}</td>
                </tr>`).join('');
            // newest first, so the balance brought forward (a start date's
            // doing) closes the table
            const opening = f.start_date ? `<tr style="color:var(--gray-500);" id="reg-opening"><td>${formatDate(f.start_date)}</td><td colspan="6">Balance brought forward${reg.class_name ? ` (${escapeHtml(reg.class_name)})` : ''}</td><td class="amount">${formatCurrency(reg.opening_balance)}</td><td></td><td></td></tr>` : '';
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th scope="col">Date</th><th scope="col">Payee</th><th scope="col">Description</th><th scope="col">Ref #</th><th scope="col">Type</th>
                    <th scope="col" class="amount">${outLabel}</th><th scope="col" class="amount">${inLabel}</th><th scope="col" class="amount">Balance</th>
                    <th scope="col" title="✓ cleared · R reconciled">✓</th><th scope="col"></th>
                </tr></thead><tbody>${rows}${opening}</tbody></table></div>`;
        }
        return html;
    },

    // Where a register line opens: the document behind it, or — for a
    // posting with none of its own (a void, a sales-tax or payroll payment,
    // an import) — its journal entry. Every line opens something.
    _entryHref(e) {
        return e.source_link || `#/journal/${e.transaction_id}`;
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
        if (!(await ClassWarn.ok(form))) return;
        try {
            await API.post('/banking/transactions', data);
            toast('Entry posted');
            closeModal();
            BankingPage.go(`#/banking/${accountId}`);
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

    async showReconcileView(reconId) {
        const data = await API.get(`/banking/reconciliations/${reconId}/transactions`);
        const rows = data.transactions.map(t => {
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
            <div class="card-grid" style="margin-bottom:16px;">
                <div class="card"><div class="card-header">Beginning Balance</div>
                    <div class="card-value">${formatCurrency(data.beginning_balance)}</div></div>
                <div class="card"><div class="card-header">Cleared</div>
                    <div class="card-value" id="recon-cleared">${formatCurrency(data.cleared_total)}</div></div>
                <div class="card"><div class="card-header">Statement Balance</div>
                    <div class="card-value">${formatCurrency(data.statement_balance)}</div></div>
                <div class="card"><div class="card-header">Difference</div>
                    <div class="card-value" id="recon-diff" style="color:${diffColor}">${formatCurrency(data.difference)}</div>
                    <div style="font-size:11px; font-weight:600;" role="status">${balanced ? '✓ Balanced — ready to finish' : '⚠ Out of balance — finish when this is $0.00'}</div></div>
            </div>
            <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:8px;" data-write>
                <button class="btn btn-sm btn-secondary" onclick="BankingPage.markAll(${reconId}, true)">Mark all</button>
                <label for="recon-through" style="font-size:11px;">Mark through</label>
                <input type="date" id="recon-through" value="${data.statement_date}" max="${data.statement_date}" style="font-size:11px; padding:2px 6px;">
                <button class="btn btn-sm btn-secondary" onclick="BankingPage.markAll(${reconId}, true, document.getElementById('recon-through').value)">Mark through that date</button>
                <button class="btn btn-sm btn-secondary" onclick="BankingPage.markAll(${reconId}, false)">Clear all</button>
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col" style="width:30px;"></th><th scope="col">Date</th><th scope="col">Payee / Description</th><th scope="col">Ref #</th><th scope="col" class="amount">Amount</th></tr></thead>
                <tbody>${rows || '<tr><td colspan="5" style="text-align:center;">No ledger lines up to this date</td></tr>'}</tbody>
            </table></div>`;
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

    async showReconReport(reconId) {
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
            openModal(`Reconciliation — ${r.account_name}, statement of ${formatDate(r.statement_date)}`, `
                ${summary}${detail}
                <div class="form-actions">
                    <button class="btn btn-secondary" onclick="closeModal()">Close</button>
                    <button class="btn btn-primary" onclick="window.open('/api/banking/reconciliations/${reconId}/pdf', '_blank')">Save PDF</button>
                </div>`);
        } catch (err) { toast(err.message, 'error'); }
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
