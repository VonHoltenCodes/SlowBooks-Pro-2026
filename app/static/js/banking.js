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
                    <div class="card-header">${escapeHtml(a.name)} <span id="${id}-kind" style="font-size:10px; color:var(--gray-400);">${accountLabel(a, '', '') ? accountLabel(a, '', '') + ' · ' : ''}${BankingPage._kindLabel(a.bank_kind)}</span></div>
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
        html += BankingPage._renderFeedSection(feed, feeds, rows);
        html += BankingPage._renderMercurySection(mercury, feeds, rows);
        return html;
    },

    _renderMercurySection(feed, feeds, rows = []) {
        let body;
        if (!feed.connected) {
            body = `<p>See payments arriving in your Mercury account in SlowBooks.
                Create a <strong>Read Only</strong> token in
                <a href="https://app.mercury.com/settings/tokens" target="_blank" rel="noopener">Mercury’s API token settings</a>,
                then connect here. Your token is stored encrypted on this server.</p>
                <p class="form-hint">SlowBooks Pro is not affiliated with, endorsed by, or sponsored by Mercury; Mercury is a trademark of Mercury Technologies, Inc.</p>
                <p class="form-hint hidden" data-admin-note>An administrator connects the bank feed.</p>
                <form onsubmit="BankingPage.connectMercury(event)" data-admin>
                    <div class="form-group">
                        <label for="mercury-token">Mercury read-only API token</label>
                        <input id="mercury-token" type="password" required autocomplete="off" maxlength="4096">
                    </div>
                    <button class="btn btn-primary" type="submit">Connect Mercury</button>
                </form>`;
        } else {
            const banks = rows.filter(a => a.bank_kind === 'bank');
            const tableRows = feed.accounts.map(a => `<tr data-mercury-account="${escapeHtml(a.id)}">
                <td>${escapeHtml(a.name)} ${a.last_four ? '••••' + escapeHtml(a.last_four) : ''}</td>
                <td class="amount mercury-current">${escapeHtml(a.balance)} USD</td>
                <td class="amount mercury-available">${escapeHtml(a.available_balance)} USD</td>
                <td class="amount mercury-inflight">${a.in_flight_balance === undefined ? '—' : escapeHtml(a.in_flight_balance) + ' USD'}</td>
                <td><select class="mercury-map" data-mercury-id="${escapeHtml(a.id)}" data-admin
                    aria-label="Import ${escapeHtml(a.name)} into">
                    ${BankingPage._importOptions(rows, feeds, feed.account_map[a.id], ['bank'])}
                </select></td></tr>`).join('');
            body = `<p>Sync posted deposits and withdrawals into <em>To review</em>.
                Match them to payments already recorded, or review and add them to your books.
                Syncing does not mark invoices paid.</p>
                ${!banks.length ? BankingPage._noBankAccountHint('bank account') : ''}
                <p class="form-hint hidden" data-admin-note>An administrator chooses which bank account each Mercury account imports into${Object.keys(feed.account_map || {}).length ? '.' : '; until one does, Sync has nothing to import.'}</p>
                <p>${feed.last_sync ? 'Last successful sync: ' + escapeHtml(feed.last_sync.replace('T', ' ')) : 'Not synced yet.'}</p>
                <div class="table-container"><table id="mercury-balances">
                    <thead><tr><th scope="col">Mercury account</th><th scope="col" class="amount">Current balance</th><th scope="col" class="amount">Available</th><th scope="col" class="amount">Incoming transfers</th><th scope="col">Imports into</th></tr></thead>
                    <tbody>${tableRows}</tbody>
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
        const mapping = {}, ledger_mapping = {};
        document.querySelectorAll('.mercury-map').forEach(sel => {
            const [kind, id] = BankingPage._importChoice(sel.value);
            if (kind === 'acct') ledger_mapping[sel.dataset.mercuryId] = id;
            else if (kind === 'feed') mapping[sel.dataset.mercuryId] = id;
        });
        button.disabled = true;
        button.textContent = 'Syncing…';
        try {
            // the mapping is an administrator's to change (#384's rule); a
            // bookkeeper syncs with the one saved
            if (App.isAdmin()) await API.post('/mercury/map', { mapping, ledger_mapping });
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
            toastPosted('Opening balance posted');
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
    // The "Imports into" choices (a support email, 2.31.1): every bank (and,
    // where the feed takes them, credit-card) account in the chart, whether
    // or not it has a bank feed record yet. Choosing one that has none makes
    // it when the mapping is saved (ledger_mapping on the map routes), so no
    // trip to + New Bank Account comes first. A feed record whose account is
    // no longer listed stays offered, so a saved mapping is never dropped.
    _importOptions(rows, feeds, mapped, kinds) {
        const listed = rows.filter(a => kinds.includes(a.bank_kind));
        const opts = ['<option value="">— not imported —</option>'];
        const shown = new Set();
        for (const a of listed) {
            const feedId = a.feed ? a.feed.bank_account_id : null;
            if (feedId) shown.add(feedId);
            const sel = feedId && feedId === mapped ? ' selected' : '';
            const named = a.feed && a.feed.name && a.feed.name !== a.name ? ` (${escapeHtml(a.feed.name)})` : '';
            opts.push(`<option value="acct:${a.account_id}"${sel}>${accountLabel(a)}${named}</option>`);
        }
        for (const ba of feeds) {
            if (shown.has(ba.id) || !ba.is_active) continue;
            if (ba.bank_kind && !kinds.includes(ba.bank_kind)) continue;
            opts.push(`<option value="feed:${ba.id}"${ba.id === mapped ? ' selected' : ''}>${escapeHtml(ba.name)}${ba.account_name ? ' → ' + escapeHtml(ba.account_name) : ''}</option>`);
        }
        return opts.join('');
    },

    // A choice's value: "acct:<chart account id>", "feed:<bank feed id>" or "".
    _importChoice(value) {
        const m = /^(acct|feed):(\d+)$/.exec(value || '');
        return m ? [m[1], parseInt(m[2], 10)] : ['none', 0];
    },

    _noBankAccountHint(what) {
        return `<p>The chart has no ${what} yet. Add one with <strong>+ New Bank Account</strong> above, or mark an account in the Chart of Accounts as a bank account, then choose it here.</p>`;
    },

    _renderFeedSection(feed, feeds, rows = []) {
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
                <p class="form-hint hidden" data-admin-note>An administrator connects the bank feed.</p>
                <form onsubmit="BankingPage.connectSimpleFIN(event)" data-admin>
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
            const options = (sfId) => BankingPage._importOptions(
                rows, feeds, feed.account_map[sfId], ['bank', 'credit_card']);
            const reversed = new Set(feed.reversed || []);
            const tableRows = feed.accounts.map(a => `<tr>
                    <td>${escapeHtml(a.name)}<div style="font-size:10px; color:var(--gray-400);">${escapeHtml(a.org || '')}</div></td>
                    <td class="amount">${escapeHtml(a.balance)} ${escapeHtml(a.currency)}</td>
                    <td><select data-sfid="${escapeHtml(a.id)}" class="simplefin-map" data-admin
                        aria-label="Import ${escapeHtml(a.name)} into">${options(a.id)}</select></td>
                    <td><label style="font-size:11px; white-space:nowrap;"><input type="checkbox" data-sfid="${escapeHtml(a.id)}" class="simplefin-reverse" data-admin
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
                ${!rows.some(a => a.bank_kind === 'bank' || a.bank_kind === 'credit_card') ? BankingPage._noBankAccountHint('bank or credit-card account') : ''}
                <p class="form-hint hidden" data-admin-note>An administrator chooses which bank account each feed imports into and whether its signs are reversed${Object.keys(feed.account_map || {}).length ? '.' : '; until one does, Sync has nothing to import.'}</p>
                <div class="table-container"><table>
                    <thead><tr><th scope="col">Bank feed</th><th scope="col" class="amount">Balance</th><th scope="col">Imports into</th><th scope="col">Amounts</th></tr></thead>
                    <tbody>${tableRows}</tbody>
                </table></div>
                <div class="form-actions" style="margin-top:12px;">
                    <button class="btn btn-primary" onclick="BankingPage.syncSimpleFIN()">Sync Now</button>
                    <button class="btn btn-secondary" onclick="BankingPage.showSimpleFINHistory()">Fetch older history…</button>
                    <button class="btn btn-secondary" data-admin onclick="BankingPage.disconnectSimpleFIN()">Disconnect</button>
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
        const mapping = {}, ledger_mapping = {};
        document.querySelectorAll('.simplefin-map').forEach(sel => {
            const [kind, id] = BankingPage._importChoice(sel.value);
            if (kind === 'acct') ledger_mapping[sel.dataset.sfid] = id;
            else mapping[sel.dataset.sfid] = kind === 'feed' ? id : 0;
        });
        const reversed = [...document.querySelectorAll('.simplefin-reverse')]
            .filter(box => box.checked).map(box => box.dataset.sfid);
        await API.post('/simplefin/map', { mapping, ledger_mapping, reversed });
    },

    async syncSimpleFIN() {
        try {
            // the mapping is an administrator's to change (#384's rule); a
            // bookkeeper syncs with the one saved
            if (App.isAdmin()) await BankingPage.saveSimpleFINMap();
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
            if (App.isAdmin()) await BankingPage.saveSimpleFINMap();
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
    // `accountId`: the account a register's Import file asked for, chosen in
    // the list when it has no feed yet (2.31.2 gate, NEW-189: it opened on
    // 1000 Checking, the list's first, for every imported account).
    async showAccountForm(accountId = null) {
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
                        <input name="opening_balance" class="money" type="number" step="0.01" value="0" oninput="this.dataset.touched = '1'">
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
        else if (sel && accountId != null && free.some(a => a.id === accountId)) sel.value = String(accountId);
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
    // the class column's rule and the classes, read through a guard: the
    // register is also drawn in a test probe that loads this file alone
    _lineClassShown() { return typeof Nonprofit !== 'undefined' && Nonprofit.lineClassShown(); },
    _classes_() { return (typeof Nonprofit !== 'undefined' && Nonprofit._funds) || BankingPage._classes || []; },
    _regFilterId: null,  // the register it is for

    // The From date each register was last read from (#663), so one worked
    // from the start of the month opens there, the date in its From box.
    // Kept in this browser, as a list keeps its Show choice, by sign-in,
    // company and account; Clear, or a From emptied, forgets it. The
    // company is the file (or database) the server says is open
    // (/api/companies, asked once a page load): account ids start again in
    // every company file, a rename leaves the file as it was, and a company
    // with no name yet, or still called My Company, has one too, where
    // /api/auth/status names neither. Failing a file, the company's name
    // (whose it is, as the Recent list keys its own); a company with
    // neither keeps no From, so two of them never share one.
    _companyFile: null,
    _companyAsked: null,
    _openCompany() {
        if (!BankingPage._companyAsked) {
            BankingPage._companyAsked = Promise.resolve()
                .then(() => API.get('/companies'))
                .then(list => {
                    const open = (Array.isArray(list) ? list : []).find(c => c && c.is_current);
                    return open ? String(open.file || open.database_name || '') : '';
                })
                .catch(() => '')
                .then(file => { BankingPage._companyFile = file; return file; });
        }
        return BankingPage._companyAsked;
    },
    _fromKey(id) {
        const owner = (typeof App !== 'undefined' && App._tabsOwner) || '';
        const bar = owner.indexOf('|');  // "user|company name"; a username has no |
        const user = bar < 0 ? '' : owner.slice(0, bar);
        const name = bar < 0 ? owner : owner.slice(bar + 1);
        const file = BankingPage._companyFile;
        const company = file ? `file:${file}` : name ? `name:${name}` : '';
        return company ? `slowbooks-register-from:${user}|${company}:${id}` : null;
    },
    // A From kept for a day after today is not used: it would open the
    // register on nothing yet, wherever the link came from.
    _lastFrom(id) {
        try {
            const key = BankingPage._fromKey(id);
            const v = (key && localStorage.getItem(key)) || '';
            return BankingPage._likelyDate(v) && v <= todayISO() ? v : '';
        } catch (e) { return ''; }
    },
    // A From with a year no books hold (a year typed a digit at a time), or
    // after today (a look at checks dated ahead), is not kept; the one kept
    // before it stays.
    _keepFrom(id, date) {
        try {
            const key = BankingPage._fromKey(id);
            if (!key) return;
            if (!date) localStorage.removeItem(key);
            else if (BankingPage._likelyDate(date) && date <= todayISO()) localStorage.setItem(key, date);
        } catch (e) { /* not kept: the next visit opens on every date */ }
    },

    // A date the register takes from its boxes, its address or the From it
    // keeps (#663): a real date in a year the books can hold, 1900 to 2199
    // (the years a request may carry, app/schemas/common.py). A year typed
    // a digit at a time reads 0002, 0020 and 0202 before it is 2026.
    _likelyDate(v) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(v || ''));
        if (!m || +m[1] < 1900 || +m[1] > 2199) return false;
        const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
        return d.getUTCMonth() === +m[2] - 1 && d.getUTCDate() === +m[3];
    },
    _yearsSaid: 'the year must be between 1900 and 2199',

    // The address of a register opened to show an entry dated `date`
    // (#663): on the From it keeps, as any link to it opens, unless that is
    // after the entry's date and would leave the entry out; then from the
    // entry's own date. With no From kept it opens on every date.
    async hrefShowing(accountId, date) {
        await BankingPage._openCompany();
        const id = Number(accountId);
        const from = BankingPage._lastFrom(id);
        const start = from && date && from > date ? String(date) : from;
        return `#/banking/${id}${start ? `?start_date=${encodeURIComponent(start)}` : ''}`;
    },

    _regFilterFrom(query) {
        const q = query || {};
        // a date that is not one, or From after To, is said so and ignored
        const dates = datesFromQuery(q);
        // and one in a year no books hold (#663)
        for (const key of ['start_date', 'end_date']) {
            if (dates[key] && !BankingPage._likelyDate(dates[key])) {
                toast(`${key} in the address is out of range (${dates[key]}): ${BankingPage._yearsSaid} — ignored`, 'error');
                dates[key] = '';
            }
        }
        return {
            ...dates,
            class_id: /^\d+$/.test(q.class_id || '') ? q.class_id : '',
        };
    },

    _regUrl(id) {
        // one register's filter is not carried to another
        const f = String(BankingPage._regFilterId) === String(id) ? BankingPage._regFilter : {};
        const q = ['start_date', 'end_date', 'class_id'].filter(k => f[k]).map(k => `${k}=${encodeURIComponent(f[k])}`).join('&');
        return `#/banking/${id}${q ? `?${q}` : ''}`;
    },

    // A filter change, typed or chosen. From after To is refused before
    // the address is touched, as the reports refuse it (W-4: written to
    // the address first, the dates came back through datesFromQuery as
    // "in the address … both ignored", and both boxes were emptied):
    // said, with the filter kept, and the boxes put back to it — the
    // register, and its address, stay on that filter. A date half typed,
    // or in a year no books hold, is refused the same way, box by box
    // (#663).
    regFilterChanged(id, filter = null) {
        const was = BankingPage._regFilter;
        let refused = false;
        const typed = (sel, word, kept) => {
            const box = $(sel);
            const why = BankingPage._dateRefused(box, word);
            if (!why) return (box && box.value) || '';
            refused = true;
            toast(`${why} — kept ${BankingPage._keptWords(was)}`, 'error');
            BankingPage._putBack(box, kept);
            return kept || '';
        };
        const next = filter || {
            start_date: typed('#reg-start', 'From', was.start_date),
            end_date: typed('#reg-end', 'To', was.end_date),
            class_id: $('#reg-class')?.value || '',
        };
        if (reversedRangeRefused(next.start_date, next.end_date, { start: was.start_date, end: was.end_date })) {
            const startEl = $('#reg-start');
            const endEl = $('#reg-end');
            if (startEl) startEl.value = was.start_date;
            if (endEl) endEl.value = was.end_date;
            return;
        }
        // a date refused and nothing else changed: the register stays as it is
        if (refused && ['start_date', 'end_date', 'class_id'].every(k => (next[k] || '') === (was[k] || ''))) return;
        BankingPage._regFilter = next;
        BankingPage._regFilterId = id;
        BankingPage._keepFrom(id, next.start_date);
        const url = BankingPage._regUrl(id);
        if ((location.hash || '#/') !== url) history.replaceState(history.state, '', url);
        return App.navigate(url);
    },

    regClear(id) {
        BankingPage.regFilterChanged(id, { start_date: '', end_date: '', class_id: '' });
    },

    // Why a date box's date is not one to apply (#663), or '': a date half
    // typed (a part left empty), or one in a year no books hold.
    _dateRefused(box, word) {
        if (!box) return '';
        if (!box.value) return box.validity && box.validity.badInput ? `The ${word} date is not complete` : '';
        return BankingPage._likelyDate(box.value) ? '' : `The ${word} date's year, ${box.value.slice(0, 4)}, is out of range: ${BankingPage._yearsSaid}`;
    },

    // The filter in use, as a refusal names what it kept (reversedRangeRefused).
    _keptWords(f) {
        return f.start_date && f.end_date ? `${formatDate(f.start_date)} to ${formatDate(f.end_date)}`
            : f.start_date ? `from ${formatDate(f.start_date)}`
                : f.end_date ? `to ${formatDate(f.end_date)}` : 'every date';
    },

    // A date box back on a date. Emptied, its type goes to text and back:
    // WebKit left the parts of a half typed date in a box only emptied
    // (settings.js clearClosingDate).
    _putBack(box, date) {
        if (!box) return;
        if (date) { box.value = date; return; }
        box.type = 'text';
        box.value = '';
        box.type = 'date';
    },

    // From and To apply as a text box's change does (#663): on Enter, or
    // when the filter bar is left, not at each whole date a box makes while
    // one is typed. Chromium and WebView2 fire change on the first
    // keystroke that forms a date, and the register drawn again on it cut
    // the typing short (11 typed into the month gave January, a day from 10
    // up became 1 to 3, a year 0002) and kept that From for every later
    // visit. A date picked from the calendar, or stepped with the date keys
    // (+ - T M H Y R), applies at once, as the other date filters apply
    // their change: nothing is being typed. Moving between From, To, the
    // class and Clear is staying in the bar, so From and To typed one after
    // the other apply together.
    regFiltersKey(e, id) {
        if (e.key === 'Tab') { BankingPage._tabbed = e.shiftKey ? 'back' : 'on'; return; }
        BankingPage._tabbed = null;
        const box = e.target;
        if ((box.id !== 'reg-start' && box.id !== 'reg-end') || e.defaultPrevented) return;
        if (e.key === 'Enter') {
            e.preventDefault();
            BankingPage._applyDates(id, { focus: box.id });
        } else if (BankingPage._editsDate(e)) {
            box.dataset.typed = '1';
        }
    },

    // A key that changes a date box's month, day or year as typed: a digit,
    // Backspace or Delete, an arrow up or down; not Tab, Enter, the arrows
    // across, nor a key held with Ctrl, Alt or Cmd.
    _editsDate(e) {
        if (e.ctrlKey || e.altKey || e.metaKey) return false;
        return /^[0-9]$/.test(e.key) || ['Backspace', 'Delete', 'ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End'].includes(e.key);
    },

    // A press on a box: its calendar opening, or a click into a part of it.
    // What is picked from the calendar applies at once.
    regDatePointer(box) { delete box.dataset.typed; },

    regDateChanged(box, id) {
        if (box.dataset.typed) return;  // typed: on Enter, or when the bar is left
        BankingPage._applyDates(id, { focus: document.activeElement === box ? box.id : null });
    },

    // The bar left. Not when the focus only moved within it, or the window
    // lost it, or the page is going. Left by the keyboard, the register is
    // drawn again and the keyboard goes on past the bar. Left by a click on
    // a link or a button, the dates apply once that click is done, and not
    // if it took the page elsewhere (Back, the sidebar): drawn then, the
    // register could land over the page the click opened.
    regFiltersLeft(e, id) {
        const bar = e.currentTarget;
        const to = e.relatedTarget;
        const past = BankingPage._tabbed;
        BankingPage._tabbed = null;
        if ((to && bar.contains(to)) || bar.contains(document.activeElement)) return;
        if (!document.contains(bar) || !BankingPage._onRegister(id) || !BankingPage._datesTyped()) return;
        const keyboard = typeof App !== 'undefined' && typeof App.keyboardFocus === 'function' && App.keyboardFocus();
        if (!keyboard && to && to.closest && to.closest('a[href], button')) BankingPage._applyAfterClick(id);
        else BankingPage._applyDates(id, { focus: to && to.id ? to.id : null, past: keyboard ? past : null });
    },

    // Whether a date box holds what the register is not on: a date typed
    // and not applied yet, or one half typed. Never on a bar a newer drawing
    // of the register is replacing: that drawing has the filter already
    // (Clear, a save), and Chromium's focusout as the old bar is taken away
    // read its old dates as typed.
    _datesTyped() {
        const bar = document.getElementById('reg-filters');
        if (!bar || bar.dataset.drawn !== String(BankingPage._drawing)) return false;
        const f = BankingPage._regFilter;
        return [['reg-start', 'start_date'], ['reg-end', 'end_date']].some(([el, k]) => {
            const box = document.getElementById(el);
            return !!box && (!!(box.validity && box.validity.badInput) || (box.value || '') !== (f[k] || ''));
        });
    },

    _applyDates(id, { focus = null, past = null } = {}) {
        ['reg-start', 'reg-end'].forEach(k => { const box = document.getElementById(k); if (box) delete box.dataset.typed; });
        if (!BankingPage._datesTyped()) return;
        const drawn = BankingPage.regFilterChanged(id);
        if (drawn && (focus || past)) drawn.then(() => BankingPage._focusAfterDates(focus, past));
    },

    _applyAfterClick(id) {
        if (BankingPage._afterClick) document.removeEventListener('click', BankingPage._afterClick, true);
        const after = () => {
            document.removeEventListener('click', after, true);
            BankingPage._afterClick = null;
            setTimeout(() => { if (BankingPage._onRegister(id)) BankingPage._applyDates(id); }, 0);
        };
        BankingPage._afterClick = after;
        document.addEventListener('click', after, true);
    },

    // The keyboard where it was once the register is drawn again: in the
    // box Enter was pressed in, on the control it moved to, or past the bar
    // (the stop after it, or before it for Shift+Tab). Focus the redraw did
    // not take (a box outside the page) is left where it is.
    _focusAfterDates(focus, past) {
        const active = document.activeElement;
        if (active && active !== document.body && document.contains(active)) return;
        let el = focus ? document.getElementById(focus) : null;
        const page = document.getElementById('page-content');
        const bar = document.getElementById('reg-filters');
        if (!el && past && page && bar) {
            const after = past === 'on';
            const side = [...page.querySelectorAll(_TAB_SEL)]
                .filter(s => !bar.contains(s) && !!(bar.compareDocumentPosition(s) & Node.DOCUMENT_POSITION_FOLLOWING) === after);
            if (!after) side.reverse();
            el = side.find(s => s.tabIndex >= 0 && !s.matches(':disabled') && _drawn(s)) || null;
        }
        if (el) focusFor(el, true);
    },

    // Back to the register after a change made on it (#663): on its own
    // dates and class, which the bare address dropped, with the page where
    // it was and, given an entry, that entry's row in view: scrolled to
    // only when it is not, so a row on screen stays where it was.
    async _backToRegister(accountId, { txnId = null, top = null } = {}) {
        const box = document.getElementById('content');
        const at = top === null ? (box ? box.scrollTop : 0) : top;
        await BankingPage.go(BankingPage._regUrl(accountId));
        const row = txnId ? document.querySelector(`#page-content tr[data-txn="${Number(txnId)}"]`) : null;
        if (box) box.scrollTop = at;
        if (row && typeof row.scrollIntoView === 'function') row.scrollIntoView({ block: 'nearest' });
        return row;
    },

    // After + Entry, Edit or Transfer saves on the register: the dialog
    // closes and the entry comes into view. One the dates leave out was
    // said with the save's toast; one the class leaves out is said here.
    async _showSaved(accountId, txnId, outside) {
        const box = document.getElementById('content');
        const top = box ? box.scrollTop : 0;
        closeModal();
        const row = await BankingPage._backToRegister(accountId, { txnId, top });
        const cls = (BankingPage._ctx || {}).className;
        if (!row && !outside && cls && BankingPage._regFilter.class_id && document.getElementById('reg-class')) {
            toast(`This entry is not in ${T('class').toLowerCase()} ${cls}, so the register does not show it.`);
        }
    },

    // Whether the page under any dialog open is this account's register.
    _onRegister(id) {
        return App.parseHash(location.hash).path === `/banking/${id}`;
    },

    _drawing: 0,  // each drawing of a register, its filter bar marked with it

    async renderRegister(accountId, query) {
        const id = parseInt(accountId, 10);
        const drawing = ++BankingPage._drawing;
        const q = query || {};
        await BankingPage._openCompany();  // whose From is kept
        const f = BankingPage._regFilterFrom(q);
        // no dates in the address: the From date this register was last
        // read from (#663); a From in the address is the one kept
        if (!q.start_date && !q.end_date) f.start_date = BankingPage._lastFrom(id);
        else if (f.start_date) BankingPage._keepFrom(id, f.start_date);
        BankingPage._regFilter = f;
        BankingPage._regFilterId = id;
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
        const [reg, overview, classes, chart, vendors, customers, jobs] = await Promise.all([
            API.get(`/banking/check-register?account_id=${id}${filterQs}`),
            API.get('/banking/overview'),
            API.get('/classes?include_archived=true').catch(() => []),
            API.get('/accounts?active_only=true').catch(() => []),
            API.get('/vendors?active_only=true').catch(() => null),
            API.get('/customers?active_only=true&tree=1').catch(() => null),
            API.get('/jobs').catch(() => []),
        ]);
        BankingPage._accounts = chart;
        BankingPage._setVendors(vendors);
        BankingPage._customerNames = customers ? new Set(customers.map(c => c.name.toLowerCase())) : null;
        // the class, job and name boxes of the review rows and the entry row
        // (#671) draw from these: the classes as the forms read them
        // (Nonprofit.lineClassShown), the jobs, the active customers
        if (typeof Nonprofit !== 'undefined') Nonprofit._funds = Array.isArray(classes) ? classes : [];
        BankingPage._classes = Array.isArray(classes) ? classes : [];
        BankingPage._jobs = Array.isArray(jobs) ? jobs : [];
        if (customers) BankingPage._customers = customers;
        const info = overview.find(a => a.account_id === id) || {};
        const feed = info.feed || null;
        const kind = reg.bank_kind;
        const [outLabel, inLabel] = BankingPage._cols(kind);
        let review = [];
        // the whole queue: a year fetched from a feed can be more than a page
        if (feed) review = await fetchAllPages(`/banking/transactions?bank_account_id=${feed.bank_account_id}&status=unmatched`);
        // the class chosen, for + Entry to take (#663): one archived is not
        // in the form's list, so a new entry is not given it
        const chosen = f.class_id ? classes.find(c => String(c.id) === f.class_id) || null : null;
        BankingPage._ctx = { accountId: id, accountName: reg.account_name, feedId: feed ? feed.bank_account_id : null, kind, lastReconciled: info.last_reconciled || null, className: reg.class_name || '', classId: chosen && !chosen.is_archived ? chosen.id : null };

        let html = `
            <div class="page-header">
                <h2>${escapeHtml(reg.account_name)} <span style="font-size:11px; color:var(--gray-400);">${accountLabel(reg, '', '') ? accountLabel(reg, '', '') + ' · ' : ''}${BankingPage._kindLabel(kind)}</span></h2>
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
        // From and To apply on Enter or once the bar is left, a pick from the
        // calendar at once (#663: regFiltersKey)
        const dateBox = `onpointerdown="BankingPage.regDatePointer(this)" onchange="BankingPage.regDateChanged(this, ${id})"`;
        html += `
            <div class="toolbar" id="reg-filters" data-drawn="${drawing}" style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;"
                onkeydown="BankingPage.regFiltersKey(event, ${id})" onfocusout="BankingPage.regFiltersLeft(event, ${id})">
                <label for="reg-start" style="font-size:11px;">From</label>
                <input type="date" id="reg-start" value="${escapeHtml(f.start_date)}" ${dateBox}>
                <label for="reg-end" style="font-size:11px;">To</label>
                <input type="date" id="reg-end" value="${escapeHtml(f.end_date)}" ${dateBox}>
                <label for="reg-class" style="font-size:11px;">${T('Class')}</label>
                <select id="reg-class" data-no-search onchange="BankingPage.regFilterChanged(${id})"><option value="">All ${T('classes').toLowerCase()}</option>${classOpts}</select>
                ${filtered ? `<button type="button" class="btn btn-sm btn-secondary" onclick="BankingPage.regClear(${id})">Clear</button>` : ''}
                ${filtered ? `<span id="reg-filter-note" style="font-size:11px; color:var(--gray-500);">Showing ${reg.class_name ? `${T('class').toLowerCase()} <strong>${escapeHtml(reg.class_name)}</strong>` : 'every ' + T('class').toLowerCase()}${f.start_date ? ` from ${formatDate(f.start_date)}` : ''}${f.end_date ? ` to ${formatDate(f.end_date)}` : ''}: the running balance counts these lines${reg.class_name ? ` only — the account's whole balance is ${formatCurrency(reg.balance)}` : ''}.</span>` : ''}
            </div>`;

        // the quick-entry row at the foot of the register (#329): date,
        // payee, memo, category, ref, payment or deposit; Enter or Post
        // posts it through the same call as + Entry, and the next row is
        // ready. The list reads oldest to newest, as a ledger does, with the
        // running balance down the right. A date filter keeps the row (#663:
        // a register worked from the start of the month never showed it). A
        // class filter puts a line in its place: the running balance above it
        // counts that class's lines only (and where the company has no class
        // of its own the row has no class box, #671, so its entry would post
        // with none and leave the list).
        const quickRow = f.class_id ? BankingPage._entryRowOff(!!(chosen && chosen.is_system_default))
            : BankingPage._quickEntryRow(id, kind, chart, outLabel, inLabel);
        if (!reg.entries.length) {
            html += `<div class="empty-state"><p>${filtered ? 'Nothing posted to this account for these filters' : 'Nothing posted to this account yet'}</p></div>`;
            html += `<div class="table-container"><table><thead><tr>
                    <th scope="col">Date</th><th scope="col">Payee</th><th scope="col">Memo</th><th scope="col">Ref</th><th scope="col">Category</th>
                    <th scope="col" class="amount">${outLabel}</th><th scope="col" class="amount">${inLabel}</th><th scope="col" class="amount">Balance</th><th scope="col"></th><th scope="col"></th>
                </tr></thead><tbody></tbody>${quickRow}</table></div>`;
        } else {
            const rows = reg.entries.slice().map(e => `
                <tr data-txn="${e.transaction_id}" style="${e.voided ? 'color:var(--gray-400); text-decoration:line-through;' : ''}">
                    <td>${formatDate(e.date)}</td>
                    <td>${escapeHtml(e.payee || '')}</td>
                    <td>${BankingPage._memoCell(e)}</td>
                    <td>${escapeHtml(e.reference || '')}</td>
                    <td style="font-size:10px; color:var(--gray-500);">${escapeHtml(sourceWord(e.source_type, e.source_kind))}</td>
                    <td class="amount">${e.payment > 0 ? formatCurrency(e.payment) : ''}</td>
                    <td class="amount">${e.deposit > 0 ? formatCurrency(e.deposit) : ''}</td>
                    <td class="amount" style="font-weight:700;">${formatCurrency(e.balance)}</td>
                    <td style="text-align:center;">${e.reconciliation_id ? 'R' : (e.cleared ? '✓' : '')}</td>
                    <td style="white-space:nowrap;">${BankingPage._printCheckButton(e)}${BankingPage._editButton(e, id)}${e.voidable ? `<button data-destructive class="btn btn-sm btn-secondary" onclick="BankingPage.voidEntry('${e.source_type}', ${e.transaction_id}, ${id})">Void</button>` : ''}</td>
                </tr>`).join('');
            // oldest first, so the balance brought forward (a start date's
            // doing) opens the table (2.23.5 round 2, NEW-142); the entry
            // row follows the entries on a populated register as on an
            // empty one (W-14, NEW-140)
            const opening = f.start_date ? `<tr style="color:var(--gray-500);" id="reg-opening"><td>${formatDate(f.start_date)}</td><td colspan="6">Balance brought forward${reg.class_name ? ` (${escapeHtml(reg.class_name)})` : ''}</td><td class="amount">${formatCurrency(reg.opening_balance)}</td><td></td><td></td></tr>` : '';
            html += `<div class="table-container"><table>
                <thead><tr>
                    <th scope="col">Date</th><th scope="col">Payee</th><th scope="col">Memo</th><th scope="col">Ref #</th><th scope="col">Type</th>
                    <th scope="col" class="amount">${outLabel}</th><th scope="col" class="amount">${inLabel}</th><th scope="col" class="amount">Balance</th>
                    <th scope="col" title="✓ cleared · R reconciled">✓</th><th scope="col"></th>
                </tr></thead><tbody>${opening}${rows}</tbody>${quickRow}</table></div>`;
        }
        return html;
    },

    // Where a register line opens: the document behind it, or — for a
    // posting with none of its own (a void, a sales-tax or payroll payment,
    // an import) — its journal entry. Every line opens something.
    _entryHref(e) {
        return e.source_link || `#/journal/${e.transaction_id}`;
    },

    // The Memo cell: the memo, linking to what the line opens, over the
    // register (App.openOver), so Close comes back to it. A bank entry
    // (posted here, or from the bank feed) is edited with its row's Edit,
    // so its memo stays words and the link beside it is named for what it
    // opens — the journal posting behind the entry. A memo that itself
    // linked to #/journal/… read as "this is a journal entry", the kind
    // that cannot be edited, and testers went looking for a way to change
    // it. A void bank entry keeps the plain link: there is nothing to edit.
    _memoCell(e) {
        const href = escapeHtml(BankingPage._entryHref(e));
        if (e.source_type !== 'bank_entry' || !e.editable) {
            return `<a href="${href}" onclick="return App.openOver(event, this.getAttribute('href'))">${escapeHtml(e.description || 'View entry')}</a>`;
        }
        return `${e.description ? `${escapeHtml(e.description)} ` : ''}<a href="${href}" onclick="return App.openOver(event, this.getAttribute('href'))" style="font-size:10px;" title="The journal posting behind this bank entry; use Edit to change it">Posting</a>`;
    },

    // A register entry opens for editing (#388); the register says which
    // (`editable`: a bank entry, a hand-entered journal entry or a deposit
    // that is not void — a reconciled one too, its amount and date locked).
    // Each opens its own editor: the + Entry form, the journal form, the
    // deposit form.
    _editButton(e, accountId) {
        if (!e.editable) return '';
        let onclick;
        if (e.source_type === 'manual') onclick = `JournalPage.editEntry(${e.transaction_id})`;
        else if (e.source_type === 'deposit') onclick = `DepositsPage.editDeposit(${e.transaction_id})`;
        else onclick = `BankingPage.editEntry(${e.transaction_id}, ${accountId})`;
        return `<button class="btn btn-sm btn-secondary" onclick="${onclick}">Edit</button>`;
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
                <td>${AccountQuickAdd.picker(`<select id="cat-${t.id}" class="review-cat" data-write data-current="${t.category_account_id || ''}" aria-label="Category" onchange="BankingPage.setCategory(${t.id}, this)"><option value="">${t.category_name ? escapeHtml(t.category_name) : 'Pick a category…'}</option></select>`, 'review', { ctx: accountId })}</td>
                ${BankingPage._reviewDimCells(t)}
                <td style="white-space:nowrap;" data-write>
                    <button class="btn btn-sm btn-primary" onclick="BankingPage.addLine(${t.id}, ${accountId})">Add</button>
                    ${t.suggested_payout ? `<button class="btn btn-sm btn-secondary" onclick="BankingPage.showPayout(${t.id}, ${accountId})" title="Record this deposit as a ${escapeHtml(t.suggested_payout.display_name)} payout, clearing the clearing account">Payout…</button>` : ''}
                    <button class="btn btn-sm btn-secondary" onclick="BankingPage.showMatch(${t.id}, ${accountId})">Match</button>
                    <button class="btn btn-sm btn-secondary" onclick="BankingPage.excludeLine(${t.id}, ${accountId})">Exclude</button>
                    <button class="btn btn-sm btn-secondary" onclick="BankingPage.ruleFromLine(${t.id}, ${accountId})" title="A bank rule from this payee and the category chosen">Rule…</button>
                </td>
            </tr>`).join('');
        setTimeout(() => BankingPage._fillCategorySelects(kind), 0);
        // one column for the row's picks (NEW-197: three columns pushed
        // Match / Exclude / Rule… past the right edge at 1280)
        const dimHeads = `<th scope="col">${[BankingPage._lineClassShown() ? T('Class') : '', (BankingPage._jobs || []).length ? T('Job') : '', 'Name'].filter(Boolean).join(' / ')}</th>`;
        return `<div class="card" style="margin-bottom:16px; border-left:4px solid var(--qb-gold);">
            <div class="card-header">To review — ${review.length} statement line${review.length === 1 ? '' : 's'} not in the books
                <span style="float:right;">
                    <button class="btn btn-sm btn-secondary" onclick="BankingPage.addAll(${feedId}, ${accountId})">Add all categorised</button>
                </span>
            </div>
            <div class="table-container"><table>
                <thead><tr><th scope="col">Date</th><th scope="col">Bank says</th><th scope="col" class="amount">Amount</th><th scope="col">Category</th>${dimHeads}<th scope="col"></th></tr></thead>
                <tbody>${rows}</tbody></table></div>${BankingPage._vendorDatalist('review-vendors')}
            <div style="font-size:10px; color:var(--gray-500); margin-top:6px;">
                A category you pick is kept on the line. <strong>Add</strong> posts the line with its category and the ${BankingPage._lineClassShown() ? `${T('class').toLowerCase()}, ` : ''}${(BankingPage._jobs || []).length ? `${T('job').toLowerCase()}, ` : ''}vendor or ${T('customer').toLowerCase()} chosen beside it; <strong>Add all categorised</strong> posts every line that has one, each with its own.
                <strong>Match</strong> links it to something you already entered.
                A ${kind === 'credit_card' ? 'card payment' : 'transfer'} is a line whose category is another bank or card account.
            </div>
        </div>`;
    },

    async _fillCategorySelects(kind) {
        const accounts = await API.get('/accounts?active_only=true');
        BankingPage._reviewAccounts = accounts.filter(a => BankingPage.CATEGORY_TYPES.includes(a.account_type));
        BankingPage._fillReviewCats();
    },

    // Every review line's category picker drawn from the chart as it is
    // now; a line keeps its own choice (one picked a moment ago, not yet
    // reloaded, included).
    _reviewAccounts: null,
    _fillReviewCats() {
        const usable = BankingPage._reviewAccounts || [];
        document.querySelectorAll('select.review-cat').forEach(sel => {
            const current = sel.value || sel.dataset.current;
            sel.innerHTML = '<option value="">Pick a category…</option>' + usable.map(a =>
                `<option value="${a.id}" ${String(a.id) === current ? 'selected' : ''}>${accountLabel(a)}${a.bank_kind ? ' (' + BankingPage._kindLabel(a.bank_kind).toLowerCase() + ')' : ''}</option>`).join('');
        });
    },

    async addLine(lineId, accountId) {
        const sel = $(`#cat-${lineId}`);
        const picks = BankingPage._reviewPicks(lineId);
        const body = { ...picks };
        if (sel && sel.value) body.category_account_id = parseInt(sel.value, 10);
        if (document.getElementById(`rv-class-${lineId}`) && !ClassWarn.okValue(picks.class_id)) return;
        try {
            const added = await API.post(`/banking/transactions/${lineId}/add`, body);
            const said = added.vendor_name ? `Added to the books for ${added.vendor_name}` : 'Added to the books';
            const outside = BankingPage._outsideFilter(BankingPage._lineDate(added, lineId));
            toast(outside ? `${said}. ${outside}` : said);
            BankingPage._backToRegister(accountId);
            // a name typed that is no vendor's is the payee the offer names
            BankingPage._offerFeedVendors([picks.payee ? { ...added, payee: picks.payee } : added]);
        } catch (err) { toast(err.message, 'error'); }
    },

    // The review row's Class, Job and Name (#671), shown under the rules
    // every form follows: a class once the company has one of its own
    // (Uncategorized alone has nothing to choose; a nonprofit's funds always),
    // a job once there are jobs. The name is the payee picker the register
    // uses: money out, a vendor (the vendor list suggested; a name that is
    // no vendor's posts as the payee's text); money in, a customer, whose
    // jobs the Job box then narrows to. Left blank, the line posts as it
    // always did: no class, no job, the vendor its payee or rule names.
    _reviewDimCells(t) {
        const small = 'font-size:11px; padding:2px 4px; width:150px; display:block;';
        let html = '';
        if (BankingPage._lineClassShown()) {
            const opts = BankingPage._classes_().filter(c => !c.is_archived)
                .map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
            html += `<select id="rv-class-${t.id}" class="review-class" data-write data-no-search aria-label="${T('Class')}, line ${t.id}" style="${small}"><option value="">—</option>${opts}</select>`;
        }
        const moneyIn = parseFloat(t.amount) > 0;
        const jobs = BankingPage._jobs || [];
        if (jobs.length) {
            const opts = jobs.map(j => `<option value="${j.id}" data-customer="${j.customer_id}">${escapeHtml(j.full_name || j.name)}</option>`).join('');
            const bind = moneyIn ? ` data-customer-select="rv-customer-${t.id}"` : '';
            html += `<select id="rv-job-${t.id}" class="review-job" data-write data-no-search${bind} aria-label="${T('Job')}, line ${t.id}" style="${small}"><option value="">— No job —</option>${opts}</select>`;
        }
        if (moneyIn) {
            const opts = (BankingPage._customers || [])
                .map(c => `<option value="${c.id}">${escapeHtml(customerLabel(c))}</option>`).join('');
            html += `<select id="rv-customer-${t.id}" class="review-customer" data-write aria-label="${T('Customer')}, line ${t.id}" style="${small}"><option value="">—</option>${opts}</select>`;
        } else {
            html += `<input type="text" id="rv-vendor-${t.id}" class="review-vendor" data-write list="review-vendors" autocomplete="off" aria-label="Vendor, line ${t.id}" placeholder="${escapeHtml(t.payee || 'Vendor')}" style="${small}">`;
        }
        return html ? `<td><div style="display:grid; gap:2px;">${html}</div></td>` : '';
    },

    // What a review row chose besides its category, as the add call takes it.
    _reviewPicks(lineId) {
        const val = (id) => { const el = document.getElementById(id); return el && el.value ? el.value : ''; };
        const picks = {};
        for (const [box, key] of [['rv-class', 'class_id'], ['rv-job', 'job_id'], ['rv-customer', 'customer_id']]) {
            const v = val(`${box}-${lineId}`);
            if (v) picks[key] = parseInt(v, 10);
        }
        const name = val(`rv-vendor-${lineId}`).trim();
        if (name) {
            const vendorId = BankingPage._payeeVendorId(name);
            if (vendorId) picks.vendor_id = vendorId; else picks.payee = name;
        }
        return picks;
    },

    // Record a provider payout from the review queue (#584): the deposit
    // net clears the clearing account for the gross and books any fee, the
    // same split the register uses. Net is the statement amount (read-only);
    // gross prefills to the net (fee 0). The clearing balance is shown as
    // information only: it already holds the net of every capture not yet
    // paid out, so prefilling it would book the rest as fees. Raise the gross
    // only for a fee the capture did not book; the server refuses a gross over
    // the balance. Posts through the normal Add path so the line links and
    // voids/reconciliation behave as usual.
    showPayout(lineId, accountId) {
        const t = (BankingPage._reviewLines || {})[lineId];
        if (!t || !t.suggested_payout) return;
        const net = Math.abs(parseFloat(t.amount) || 0);
        const bal = parseFloat(t.suggested_payout.clearing_balance) || 0;
        const name = t.suggested_payout.display_name;
        const gross = net;
        openModal(`Record ${escapeHtml(name)} payout`, `
            <form onsubmit="BankingPage.submitPayout(event, ${lineId}, ${accountId})">
                <p style="font-size:12px; margin:0 0 8px;">
                    This ${escapeHtml(formatCurrency(net))} deposit clears the ${escapeHtml(name)} clearing
                    account, which holds ${escapeHtml(formatCurrency(bal))} awaiting payout. Leave the gross
                    at the net unless the processor took a fee that was not booked when the payment came in:
                    the gross less the net is booked as fee.
                </p>
                <div class="form-grid">
                    <div class="form-group"><label>Gross cleared</label>
                        <input id="payout-gross" type="number" step="0.01" min="${net.toFixed(2)}" value="${gross.toFixed(2)}"
                            oninput="BankingPage._payoutFee()"></div>
                    <div class="form-group"><label>Fee</label>
                        <input id="payout-fee" type="number" step="0.01" min="0" value="${Math.max(0, gross - net).toFixed(2)}" readonly></div>
                    <div class="form-group"><label>Net to bank</label>
                        <input id="payout-net" type="number" value="${net.toFixed(2)}" readonly></div>
                    <div class="form-group"><label>Clearing balance</label>
                        <input type="text" value="${escapeHtml(formatCurrency(bal))}" readonly aria-readonly="true"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Record payout</button>
                </div>
            </form>`);
    },

    // Fee = gross − net, recomputed as the gross changes; never negative.
    _payoutFee() {
        const gross = parseFloat($('#payout-gross').value) || 0;
        const net = parseFloat($('#payout-net').value) || 0;
        $('#payout-fee').value = Math.max(0, gross - net).toFixed(2);
    },

    async submitPayout(e, lineId, accountId) {
        e.preventDefault();
        const t = (BankingPage._reviewLines || {})[lineId];
        const sp = t && t.suggested_payout;
        if (!sp) return;
        const gross = parseFloat($('#payout-gross').value) || 0;
        const net = parseFloat($('#payout-net').value) || 0;
        if (gross < net) { toast('The gross cannot be less than the net', 'error'); return; }
        try {
            // The server builds the split from the gross, so the clearing and
            // fee account ids never touch the client.
            const paid = await API.post(`/banking/transactions/${lineId}/payout`, {
                provider: sp.provider, gross: gross.toFixed(2),
            });
            closeModal();
            const outside = BankingPage._outsideFilter(BankingPage._lineDate(paid, lineId));
            toast(outside ? `Payout recorded. ${outside}` : 'Payout recorded');
            BankingPage._backToRegister(accountId);
        } catch (err) { toast(err.message, 'error'); }
    },

    // A feed line added with a payee no vendor has (#491): the entry
    // posted with the payee as text, and a toast offers to make it a
    // vendor and name it on the entry, as the register's quick row does
    // (payeeTyped). Money out only; a name is asked once a session. A
    // transfer (a line categorised to a bank or card account) names nobody
    // and the server makes no vendor of it, so it is not offered; nor is a
    // line with no payee.
    _offerFeedVendors(lines) {
        const transfer = (t) => (BankingPage._reviewAccounts || [])
            .some(a => a.bank_kind && String(a.id) === String(t.category_account_id));
        const want = lines.filter(t => t && !t.vendor_id && parseFloat(t.amount) < 0 && (t.payee || '').trim()
            && !transfer(t)
            && !BankingPage._payeesAsked.has(t.payee.trim().toLowerCase()));
        if (!want.length) return;
        want.forEach(t => BankingPage._payeesAsked.add(t.payee.trim().toLowerCase()));
        const names = [...new Set(want.map(t => t.payee.trim()))];
        const ms = (typeof window !== 'undefined' && window.__payeeToastMs) || 15000;
        const text = names.length === 1 ? `Add "${names[0]}" as a vendor?`
            : `${names.length} payees are not on the vendor list. Add them as vendors?`;
        toastAction(text, 'Add', () => BankingPage.feedVendors(want.map(t => t.id)), ms, 'Not now', { focusOnShow: true });
    },

    async feedVendors(lineIds) {
        try {
            const r = await API.post('/banking/transactions/vendors', { line_ids: lineIds });
            const made = r.created.map(v => v.name);
            if (BankingPage._vendorNames) made.forEach(n => BankingPage._vendorNames.add(n.toLowerCase()));
            if (BankingPage._vendorsByName) r.created.forEach(v => BankingPage._vendorsByName.set(v.name.trim().toLowerCase(), { id: v.id, name: v.name }));
            toast(made.length === 1 ? `${made[0]} added to Vendors`
                : made.length ? `${made.length} vendors added` : `Linked to ${r.linked} vendor${r.linked === 1 ? '' : 's'}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async excludeLine(lineId, accountId) {
        try {
            await API.post(`/banking/transactions/${lineId}/exclude`);
            BankingPage._backToRegister(accountId);
        } catch (err) { toast(err.message, 'error'); }
    },

    async restoreLine(lineId, accountId) {
        try {
            await API.post(`/banking/transactions/${lineId}/restore`);
            BankingPage._backToRegister(accountId);
        } catch (err) { toast(err.message, 'error'); }
    },

    async unmatchLine(lineId, accountId) {
        try {
            await API.post(`/banking/transactions/${lineId}/unmatch`);
            BankingPage._backToRegister(accountId);
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
            const lines = BankingPage._reviewLines || {};
            // the lines it posts: each with a category, as the server takes them
            const categorised = Object.values(lines).filter(t => {
                const sel = document.getElementById(`cat-${t.id}`);
                return sel ? !!sel.dataset.current : !!t.category_account_id;
            });
            // each row's own class, job and name go with it (#671)
            const picked = Object.values(lines)
                .map(t => ({ id: t.id, ...BankingPage._reviewPicks(t.id) }))
                .filter(p => Object.keys(p).length > 1);
            const r = await API.post(`/banking/accounts/${feedId}/feed/add-all`, picked.length ? { lines: picked } : undefined);
            const skipped = new Set(r.skipped.map(x => x.id));
            const outside = BankingPage._outsideFilterAll(categorised.filter(t => !skipped.has(t.id)).map(t => String(t.date || '')));
            const said = `Added ${r.added}${r.skipped.length ? `, skipped ${r.skipped.length}: ${r.skipped[0].reason}` : ''}`;
            toast(outside ? `${said}. ${outside}` : said);
            BankingPage._backToRegister(accountId);
            const typed = Object.fromEntries(picked.filter(p => p.payee).map(p => [p.id, p.payee]));
            BankingPage._offerFeedVendors((r.without_vendor || []).map(id => lines[id])
                .filter(Boolean).map(t => (typed[t.id] ? { ...t, payee: typed[t.id] } : t)));
        } catch (err) { toast(err.message, 'error'); }
    },

    async findMatches(feedId, accountId) {
        try {
            const r = await API.post(`/banking/accounts/${feedId}/feed/auto-match`);
            toast(`Matched ${r.matched} statement line${r.matched === 1 ? '' : 's'}`);
            BankingPage._backToRegister(accountId);
        } catch (err) { toast(err.message, 'error'); }
    },

    async showMatch(lineId, accountId) {
        try {
            const cands = await API.get(`/banking/transactions/${lineId}/candidates`);
            // each posting's date, for Matched to say one the dates leave out
            BankingPage._matchDates = Object.fromEntries(cands.map(c => [c.line_id, c.date]));
            const rows = cands.map(c => `<tr>
                <td>${formatDate(c.date)} <span style="font-size:10px; color:var(--gray-400);">${c.days_off ? c.days_off + 'd off' : 'same day'}</span></td>
                <td>${escapeHtml(c.payee || c.description || '')}</td>
                <td>${escapeHtml(c.reference || '')}</td>
                <td style="font-size:10px;">${escapeHtml(sourceWord(c.source_type, c.source_kind))}</td>
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
            const outside = BankingPage._outsideFilter(String((BankingPage._matchDates || {})[ledgerLineId] || ''));
            toast(outside ? `Matched. ${outside}` : 'Matched');
            closeModal();
            BankingPage._backToRegister(accountId);
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
            BankingPage._backToRegister(accountId);
        } catch (err) { toast(err.message, 'error'); }
    },

    _quickEntryRow(accountId, kind, chart, outLabel, inLabel) {
        const cats = (chart || [])
            .filter(a => a.id !== accountId && BankingPage.CATEGORY_TYPES.includes(a.account_type))
            .map(a => `<option value="${a.id}">${accountLabel(a, a.account_path || a.name)}${a.bank_kind ? ' (transfer)' : ''}</option>`).join('');
        const key = `onkeydown="BankingPage._quickKey(event, ${accountId})"`;
        const now = new Date();
        const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
        // the date of this register's last post, while the page lives (#566)
        const carried = BankingPage._lastDate[accountId] || '';
        // the row lives in its own <tfoot>, after the entries; every field is
        // named for the new entry, apart from the page's other fields
        return `<tfoot><tr id="reg-quick-entry" data-write style="background:var(--primary-light);">
                    <td><input type="date" id="qe-date" aria-label="New entry date" value="${carried || today}" data-carried="${carried}" style="width:120px; font-size:11px; padding:2px 4px;" ${key}></td>
                    <td><input type="text" id="qe-payee" list="qe-payee-vendors" autocomplete="off" aria-label="New entry payee" placeholder="Payee" style="width:100%; font-size:11px; padding:2px 4px;" onblur="BankingPage.payeeTyped(this.value, BankingPage._rowMoneyIn(), 'qe-payee')" ${key}>${BankingPage._vendorDatalist('qe-payee-vendors')}</td>
                    <td><input type="text" id="qe-memo" aria-label="New entry memo" placeholder="Memo" style="width:100%; font-size:11px; padding:2px 4px;" ${key}></td>
                    <td><input type="text" id="qe-ref" aria-label="New entry check or reference number" placeholder="Ref" style="width:60px; font-size:11px; padding:2px 4px;" ${key}></td>
                    <td>${AccountQuickAdd.picker('<select id="qe-category" aria-label="New entry category" data-no-search ' + key + ' style="font-size:11px; padding:2px 4px; max-width:150px;"><option value="">Category…</option>' + cats + '</select>', 'register', { ctx: accountId, id: 'qe-category-add', style: 'font-size:11px; padding:2px 6px;' })}${BankingPage._quickClassHtml(key)}</td>
                    <td class="amount"><input type="number" step="0.01" min="0" id="qe-out" class="money" aria-label="New entry ${escapeHtml(outLabel)}" placeholder="0.00" style="width:90px; font-size:11px; padding:2px 4px; text-align:right;" ${key}></td>
                    <td class="amount"><input type="number" step="0.01" min="0" id="qe-in" class="money" aria-label="New entry ${escapeHtml(inLabel)}" placeholder="0.00" style="width:90px; font-size:11px; padding:2px 4px; text-align:right;" ${key}></td>
                    <td></td><td></td>
                    <td><button type="button" class="btn btn-sm btn-primary" onclick="BankingPage.postQuickEntry(${accountId})">Post</button></td>
                </tr>
                <tr data-write><td colspan="10" id="qe-category-host" style="padding:0; border:0;"></td></tr></tfoot>`;
    },

    // The entry row's Class (#671), as + Entry has it: shown once the
    // company has a class of its own, the default class chosen unless
    // Settings asks for a class to be picked every time.
    _quickClassHtml(key) {
        if (!BankingPage._lineClassShown()) return '';
        const classes = BankingPage._classes_().filter(c => !c.is_archived);
        const blank = ClassWarn.enabled();
        const opts = (blank ? `<option value="" selected>— choose a ${T('class').toLowerCase()} —</option>` : '<option value="">—</option>')
            + classes.map(c => `<option value="${c.id}"${!blank && c.is_system_default ? ' selected' : ''}>${escapeHtml(c.name)}</option>`).join('');
        return `<select id="qe-class" aria-label="New entry ${T('class').toLowerCase()}" data-no-search ${key} style="display:block; margin-top:2px; font-size:11px; padding:2px 4px; max-width:150px;">${opts}</select>`;
    },

    // In the entry row's place while a class is chosen (#663). The
    // company's default class (Uncategorized) counts every line with no
    // class, the row's entry among them, so there the balance is the
    // reason alone.
    _entryRowOff(isDefault = false) {
        const cls = T('class').toLowerCase();
        // a company with a class of its own has a class box on the row
        // (#671); the balance is the reason then too
        const why = isDefault || BankingPage._lineClassShown() ? `the running balance counts this ${cls} only`
            : `it has no ${cls} box, and the running balance counts this ${cls} only`;
        return `<tfoot><tr id="reg-quick-entry-off" data-write><td colspan="10" style="padding:6px 10px; font-size:11px; color:var(--gray-500);">The entry row is hidden while a ${cls} is chosen: ${why}. Choose All ${T('classes').toLowerCase()} to use it, or post with + Entry.</td></tr></tfoot>`;
    },

    // Enter in the quick row. As before, it posts the entry, unless the
    // preference "Enter moves between fields" is on (#480): then it moves
    // to the row's next field, and only Enter in the last amount (or Post)
    // posts, the rule every form follows. Enter on a button is its own.
    _quickKey(event, accountId) {
        if (event.key !== 'Enter' || event.defaultPrevented) return;
        // the category list never posted on Enter; it only moves through
        if (!DataEntryPrefs.enter_moves && event.target.tagName === 'SELECT') return;
        const row = document.getElementById('reg-quick-entry');
        if (row && enterMovesOn(event, row)) return;
        event.preventDefault();
        BankingPage.postQuickEntry(accountId);
    },

    // The date of the last entry posted in each register, by account id,
    // for the quick row and + Entry to open with (#566, QuickBooks keeps
    // it). Held in memory only: it lasts the page's life, and sign-in
    // reloads the page. One register's date is not another's.
    _lastDate: {},

    // An entry dated outside the register's From and To would leave the
    // list without a word when it is drawn again: the toast says so (#663).
    _outsideFilter(date) {
        const f = BankingPage._regFilter;
        if (date && f.start_date && date < f.start_date) return `Its date, ${formatDate(date)}, is before the From date, so the register does not show it.`;
        if (date && f.end_date && date > f.end_date) return `Its date, ${formatDate(date)}, is after the To date, so the register does not show it.`;
        return '';
    },

    // Several entries posted at once (Add all categorised): those the
    // register's dates leave out, said as _outsideFilter says one.
    _outsideFilterAll(dates) {
        if (dates.length === 1) return BankingPage._outsideFilter(dates[0]);
        const f = BankingPage._regFilter;
        const before = (d) => !!(d && f.start_date && d < f.start_date);
        const after = (d) => !!(d && f.end_date && d > f.end_date);
        const out = dates.filter(d => before(d) || after(d));
        if (!out.length) return '';
        const side = out.some(before) && out.some(after) ? 'before the From date or after the To date'
            : out.some(before) ? 'before the From date' : 'after the To date';
        return out.length === 1
            ? `One of them, dated ${formatDate(out[0])}, is ${side}, so the register does not show it.`
            : `${out.length} of them are dated ${side}, so the register does not show them.`;
    },

    // The date a statement line posted on: the answer's, else the queue's.
    _lineDate(answer, lineId) {
        const line = (BankingPage._reviewLines || {})[lineId] || {};
        return String((answer && answer.date) || line.date || '');
    },

    // The quick row posts through the same call as + Entry (#329): money
    // out from the left amount box, money in from the right one (a card's
    // charge and payment the same way), one of them; then the register
    // redraws on its filter (#663) with the entry in place and the row
    // ready for the next.
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
            vendor_id: BankingPage._payeeVendorId(v('qe-payee')),
            description: v('qe-memo') || null,
            check_number: v('qe-ref') || null,
        };
        // the row's class (#671), when it has the box; a transfer keeps it too
        const classBox = document.getElementById('qe-class');
        if (classBox) {
            data.class_id = classBox.value ? parseInt(classBox.value, 10) : null;
            if (!ClassWarn.okValue(data.class_id)) return;
        }
        // a date far from today asks first, as every form does (#481),
        // unless it is the last entry's date carried over (#566)
        const dateBox = document.getElementById('qe-date');
        const carried = dateBox && dateBox.dataset ? dateBox.dataset.carried : undefined;
        if (!(await TxnWarn.ok(null, { date: data.date, carried }))) return;
        try {
            // a check number the account already has: keep it or edit it
            const posted = await CheckNumberWarn.send(
                q => API.post('/banking/transactions', data, q ? { query: q } : undefined),
                document.getElementById('qe-ref'));
            if (!posted) return;
            BankingPage._lastDate[accountId] = data.date;
            const outside = BankingPage._outsideFilter(data.date);
            toastPosted(outside ? `Entry posted. ${outside}` : 'Entry posted');
            BankingPage.payeeTyped(data.payee, inn > 0, 'qe-payee');
            await BankingPage.go(BankingPage._regUrl(accountId));
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
            if (vendors) BankingPage._setVendors(vendors);
        }
        // the names a deposit line can carry (#606): every open of the form
        // reads the list afresh, so a donor added elsewhere is there
        const customers = await API.get('/customers?active_only=true&tree=1').catch(() => null);
        if (customers) {
            BankingPage._customers = customers;
            BankingPage._customerNames = new Set(customers.map(c => c.name.toLowerCase()));
        }
        await Nonprofit.loadFunds();
        // a new entry on a register shown for one class takes that class
        // (#663), so one posted as the class line suggests stays in the
        // list; an edit keeps its own
        const ctx = BankingPage._ctx || {};
        const shownFor = !entry && ctx.accountId === accountId && String(BankingPage._regFilterId) === String(accountId)
            && BankingPage._regFilter.class_id ? ctx.classId : null;
        const classGroup = await classFormGroupHtml(shownFor || undefined);
        // the entry's job beside its class (#671 put one on the review row)
        const jobGroup = await jobFormGroupHtml(entry ? entry.job_id : null);
        // a new entry opens on the register's last posted date (#566); an
        // edit fills in its own
        const carried = (!entry && BankingPage._lastDate[accountId]) || '';
        openModal('New Register Entry', `
            <form onsubmit="BankingPage.saveEntry(event, ${accountId})">
                <div class="form-grid">
                    <div class="form-group"><label>Date *</label>
                        <input name="date" type="date" required value="${carried || todayISO()}" data-carried="${carried}"></div>
                    <div class="form-group"><label>Amount *</label>
                        <input name="amount" class="money" type="number" step="0.01" required>
                        <div style="font-size:10px; color:var(--gray-500);">${isCard ? 'Negative = a charge (owe more). Positive = a payment to the card.' : 'Negative = money out (a payment). Positive = money in (a deposit).'}</div></div>
                    <div class="form-group"><label>Payee</label>
                        <input name="payee" id="entry-payee" list="entry-payee-vendors" autocomplete="off" onblur="BankingPage.payeeTyped(this.value, BankingPage._formMoneyIn(), 'entry-payee')">
                        ${BankingPage._vendorDatalist('entry-payee-vendors')}</div>
                    <div class="form-group"><label>Check / ref #</label>
                        <input name="check_number"></div>
                    <div class="form-group full-width"><label>Category * <span style="font-weight:normal; color:var(--gray-500);">(the other side of the entry; a bank or card account makes it a transfer)</span></label>
                        ${AccountQuickAdd.picker(`<select name="category_account_id" id="entry-category"><option value="">Select…</option>${catOpts}</select>`, 'register', { ctx: accountId, id: 'entry-category-add', label: 'Add a new account…' })}</div>
                    <div class="form-group full-width"><label>Memo</label>
                        <input name="description"></div>
                    ${classGroup}
                    ${jobGroup}
                    <div class="form-group" id="entry-name-group"><label>${T('Customer')} <span style="font-weight:normal; color:var(--gray-500);">(who the money came from)</span></label>
                        ${BankingPage.nameSelectHtml('entry-name', 'customer_id', null, `${T('Customer')}`)}</div>
                    <div class="form-group full-width" id="entry-splits" style="display:none;">
                        <label>Split across accounts <span style="font-weight:normal; color:var(--gray-500);">(each line's share of the amount; a loan payment's principal and interest, a check that pays several things, a card processor's deposit as the gross with the fee taken out)</span></label>
                        <table class="data-table" style="font-size:12px;"><thead><tr><th scope="col">Account</th><th scope="col" class="amount">Amount</th><th scope="col">Taken out</th><th scope="col">${T('Customer')}</th>${Nonprofit.classHeadHtml()}<th scope="col">Memo</th><th scope="col"></th></tr></thead>
                        <tbody id="entry-split-rows"></tbody></table>
                        ${BankingPage.newNameBoxHtml()}
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
            </form>
            ${entry ? BankingPage._attachmentsHtml(entry.id) : ''}`);
        BankingPage._splitCats = catOpts;
        if (entry) BankingPage._fillEntryForm(entry);
        if (entry) BankingPage.loadAttachments(entry.id);
    },

    // The receipt behind a register entry (#600): the same panel, files and
    // routes as a journal entry's. Only an entry already posted has one.
    _attachmentsHtml(id) {
        return `<div style="margin-top:12px; border-top:1px solid var(--gray-300); padding-top:8px;">
            <div style="font-weight:700; font-size:12px; margin-bottom:4px;">Attachments</div>
            <div id="entry-attachments-list" style="margin-bottom:8px; font-size:11px;">Loading...</div>
            <div style="display:flex; gap:6px; align-items:center;">
                <input type="file" id="entry-attach-file" aria-label="File to attach" style="font-size:11px;">
                <button type="button" class="btn btn-sm btn-secondary" onclick="BankingPage.uploadAttachment(${id})">Attach</button>
            </div>
        </div>`;
    },

    async loadAttachments(id) {
        const el = $('#entry-attachments-list');
        if (!el) return;
        try {
            const attachments = await API.get(`/attachments/bank_entry/${id}`);
            if (attachments.length === 0) {
                el.innerHTML = '<span style="color:var(--text-muted);">No attachments</span>';
            } else {
                el.innerHTML = attachments.map(a =>
                    `<div style="display:flex; flex-wrap:wrap; align-items:center; gap:0 8px; padding:2px 0;">
                        ${a.missing ? `<span>${escapeHtml(a.filename)}</span>` : `<a href="/api/attachments/download/${a.id}" target="_blank">${escapeHtml(a.filename)}</a>`}
                        ${a.file_size == null ? '' : `<span style="color:var(--gray-400);">(${formatFileSize(a.file_size)})</span>`}
                        <button type="button" data-destructive aria-label="Delete attachment" class="btn btn-sm btn-danger" onclick="BankingPage.deleteAttachment(${a.id},${id})" style="padding:0 4px; font-size:10px;">X</button>
                        ${storedFileNote(a)}
                    </div>`
                ).join('');
            }
        } catch (e) { el.innerHTML = ''; }
    },

    async uploadAttachment(id) {
        const fileInput = $('#entry-attach-file');
        if (!fileInput?.files[0]) { toast('Select a file first', 'error'); return; }
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        try {
            const resp = await fetch(`/api/attachments/bank_entry/${id}`, { method: 'POST', body: formData });
            if (!resp.ok) throw new Error(await API.responseError(resp, 'Upload failed'));
            toast('Attachment uploaded');
            fileInput.value = '';
            BankingPage.loadAttachments(id);
        } catch (err) { toast(err.message, 'error'); }
    },

    async deleteAttachment(attachmentId, id) {
        if (!confirm('Delete this attachment?')) return;
        try {
            await API.del(`/attachments/${attachmentId}`);
            toast('Attachment deleted');
            BankingPage.loadAttachments(id);
        } catch (err) { toast(err.message, 'error'); }
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
        form.dataset.payee = (entry.payee || '').trim();
        form.dataset.vendorId = entry.vendor_id || '';
        form.check_number.value = entry.reference || '';
        form.description.value = entry.description || '';
        BankingPage._setName(form.customer_id, entry.customer_id, entry.customer_name);
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
        // a gift on a named donor's line gets the thank-you letter (#606)
        if (Terms.isNonprofit() && entry.gift_letters && entry.gift_letters.length) {
            const box = document.createElement('div');
            box.className = 'form-group full-width';
            box.id = 'entry-gift-letters';
            box.innerHTML = entry.gift_letters.map(g =>
                `<button type="button" class="btn btn-sm btn-secondary" onclick="window.open('/api/donors/gifts/bank-entry/${g.line_id}/acknowledgment/pdf','_blank')">Acknowledgment (PDF): ${escapeHtml(g.customer_name || '')} ${formatCurrency(g.amount)}</button> `
                + `<button type="button" class="btn btn-sm btn-secondary" onclick="Donors.emailAcknowledgment('bank-entry', ${g.line_id})">Email</button>`).join('<br>');
            const grid = form.querySelector('.form-grid');
            if (grid) grid.appendChild(box);
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
                BankingPage._setName(row.querySelector('.split-name'), sp.customer_id, null);
                BankingPage._setSplitClass(row, sp.class_id);
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
        // each line names its own; the entry's single name is for a plain entry
        const nameGroup = document.getElementById('entry-name-group');
        if (nameGroup) nameGroup.style.display = open ? 'none' : '';
        if (open && !document.querySelectorAll('#entry-split-rows tr').length) { BankingPage.addSplitRow(); BankingPage.addSplitRow(); }
        BankingPage.splitNote();
    },
    // The Name (customer, a donor in a nonprofit) on a deposit line (#606):
    // a select of active customers with "+ New Customer" at the top, as the
    // document forms have it. Small cell helpers in the journal form's mould.
    _nameOptions(selected) {
        const list = BankingPage._customers || [];
        return `<option value="">—</option>`
            + (App.isLocked() ? '' : `<option value="__new__">+ ${T('New Customer')}</option>`)
            + list.map(c => `<option value="${c.id}"${c.id === selected ? ' selected' : ''}>${escapeHtml(customerLabel(c))}</option>`).join('');
    },
    nameSelectHtml(id, name, selected, label) {
        return `<select${id ? ` id="${id}"` : ''}${name ? ` name="${name}"` : ''} aria-label="${escapeHtml(label)}" onchange="BankingPage.nameChosen(this)">${BankingPage._nameOptions(selected)}</select>`;
    },
    nameCellHtml(cls, selected, label) {
        return `<td><select class="${cls}" aria-label="${escapeHtml(label)}" onchange="BankingPage.nameChosen(this)">${BankingPage._nameOptions(selected)}</select></td>`;
    },
    nameFromRow(row) {
        const v = row.querySelector('.split-name')?.value;
        return v && v !== '__new__' ? parseInt(v, 10) : null;
    },
    // A name an edit opens on stays a choice though the customer has since
    // gone inactive (the list holds active ones only).
    _setName(sel, id, name) {
        if (!sel) return;
        const want = id ? String(id) : '';
        if (want && ![...sel.options].some(o => o.value === want)) {
            const opt = document.createElement('option');
            opt.value = want;
            opt.textContent = name || `${T('Customer')} ${want}`;
            sel.appendChild(opt);
        }
        sel.value = want;
    },
    // a class archived since stays a choice on its line, as in the journal form
    _setSplitClass(row, id) {
        const sel = row.querySelector('.split-fund');
        if (!sel) return;
        const want = id ? String(id) : '';
        if (want && ![...sel.options].some(o => o.value === want)) {
            const opt = document.createElement('option');
            opt.value = want;
            opt.textContent = `${T('Class')} ${want} (archived)`;
            sel.appendChild(opt);
        }
        sel.value = want;
    },
    // "+ New Customer" opens one small box under the form; Save adds the
    // customer, offers it in every name picker and picks it where it was asked.
    newNameBoxHtml() {
        if (App.isLocked()) return '';
        return `<div id="entry-new-name" style="display:none; margin-top:6px; padding:8px; border:1px solid var(--gray-300); border-radius:4px;" onkeydown="BankingPage.newNameKey(event)">
            <div style="font-weight:700; font-size:11px; margin-bottom:4px;">Quick Add ${T('Customer')}</div>
            <input id="entry-new-name-input" placeholder="Name *" aria-label="${T('Customer')} name" aria-required="true" maxlength="200" style="width:60%;">
            <button type="button" class="btn btn-sm btn-primary" onclick="BankingPage.saveNewName()">Save</button>
            <button type="button" class="btn btn-sm btn-secondary" onclick="BankingPage.cancelNewName()">Cancel</button>
        </div>`;
    },
    _newNameFor: null,
    nameChosen(sel) {
        const box = document.getElementById('entry-new-name');
        if (!box || sel.value !== '__new__') { if (box && sel.value !== '__new__') box.style.display = 'none'; return; }
        BankingPage._newNameFor = sel;
        box.style.display = 'block';
        const input = document.getElementById('entry-new-name-input');
        if (input) input.focus();
    },
    newNameKey(e) {
        if (e.key === 'Enter' && e.target.tagName !== 'BUTTON') { e.preventDefault(); e.stopPropagation(); BankingPage.saveNewName(); }
        else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); BankingPage.cancelNewName(); }
    },
    cancelNewName() {
        const box = document.getElementById('entry-new-name');
        if (box) box.style.display = 'none';
        if (BankingPage._newNameFor) BankingPage._newNameFor.value = '';
        BankingPage._newNameFor = null;
    },
    async saveNewName() {
        const input = document.getElementById('entry-new-name-input');
        const name = input ? input.value.trim() : '';
        if (!name) { toast(`${T('Customer')} name is required`, 'error'); if (input) input.focus(); return; }
        try {
            const made = await API.post('/customers', { name });
            (BankingPage._customers = BankingPage._customers || []).push(made);
            if (BankingPage._customerNames) BankingPage._customerNames.add(name.toLowerCase());
            const opt = `<option value="${made.id}">${escapeHtml(made.name)}</option>`;
            document.querySelectorAll('#modal-body select[name="customer_id"], #modal-body .split-name')
                .forEach(sel => sel.insertAdjacentHTML('beforeend', opt));
            if (BankingPage._newNameFor) BankingPage._newNameFor.value = String(made.id);
            input.value = '';
            document.getElementById('entry-new-name').style.display = 'none';
            BankingPage._newNameFor = null;
        } catch (err) {
            const dupes = err.status === 409 && err.detail && err.detail.duplicates;
            toast(dupes ? `Not added: ${dupes.map(d => d.name).join(', ')} already exists` : err.message, 'error');
        }
    },

    addSplitRow() {
        const body = document.getElementById('entry-split-rows');
        if (!body) return;
        const n = body.querySelectorAll('tr').length + 1;
        body.insertAdjacentHTML('beforeend', `<tr>
            <td><select class="split-cat" aria-label="Split line ${n} account" data-no-search><option value="">Select…</option>${BankingPage._splitCats || ''}</select></td>
            <td class="amount"><input class="split-amt money" type="number" step="0.01" aria-label="Split line ${n} amount" oninput="BankingPage.splitNote()" style="width:100px; text-align:right;"></td>
            <td style="text-align:center;"><input class="split-fee" type="checkbox" aria-label="Split line ${n} taken out" title="A fee the processor kept, or a refund netted against a check: this line goes the other way from the entry" onchange="BankingPage.splitNote()"></td>
            ${BankingPage.nameCellHtml('split-name', null, `${T('Customer')}, line ${n}`)}${Nonprofit.classCellHtml('split', null)}
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
                // the name on the line (#606); null clears it
                customer_id: BankingPage.nameFromRow(r),
                // a line's own class: the picker's when the form shows one,
                // else the one carried from the entry (#388)
                ...(r.querySelector('.split-fund')
                    ? (Nonprofit.fundFromRow(r, 'split') ? { class_id: Nonprofit.fundFromRow(r, 'split') } : {})
                    : (r.dataset.classId ? { class_id: parseInt(r.dataset.classId, 10) } : {})),
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
            // a vendor picked or exactly typed; an edit keeps its own while the payee is as it was (#590)
            vendor_id: BankingPage._payeeVendorId(form.payee.value, form.dataset.entryId
                ? { payee: form.dataset.payee || '', vendorId: form.dataset.vendorId ? parseInt(form.dataset.vendorId, 10) : null } : null),
            description: form.description.value || null,
            check_number: form.check_number.value || null,
            class_id: classIdFromForm(form),
            job_id: jobIdFromForm(form),
            ...(splits ? { splits } : {}),
        };
        // the plain entry's name (#606): on money in only, null clears it
        if (!splits && parseFloat(form.amount.value || '0') > 0) data.customer_id = form.customer_id.value ? parseInt(form.customer_id.value, 10) : null;
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
        const entryId = form.dataset.entryId ? parseInt(form.dataset.entryId, 10) : null;
        if (!(await TxnWarn.ok(form, { editing: !!entryId }))) return;
        if (!(await ClassWarn.ok(form))) return;
        if (entryId) { await BankingPage._putEntry(entryId, data, accountId); return; }
        try {
            const posted = await CheckNumberWarn.send(
                q => API.post('/banking/transactions', data, q ? { query: q } : undefined),
                form.check_number || null);
            if (!posted) return;
            BankingPage._lastDate[accountId] = data.date;
            const outside = BankingPage._outsideFilter(data.date);
            toastPosted(outside ? `Entry posted. ${outside}` : 'Entry posted');
            BankingPage.payeeTyped(data.payee, parseFloat(data.amount) > 0, 'qe-payee');
            await BankingPage._showSaved(accountId, posted.id, outside);
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
            const form = document.querySelector('#modal-body form');
            const saved = await CheckNumberWarn.send(
                q => API.put(`/banking/entries/${entryId}`, data, q ? { query: q } : undefined),
                (form && form.check_number) || null);
            if (!saved) return;
            const outside = BankingPage._outsideFilter(data.date);
            const said = [saved.note, outside].filter(Boolean).join(' ');
            toastPosted(said ? `Entry saved. ${said}` : 'Entry saved');
            await BankingPage._showSaved(accountId, entryId, outside);
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
    _vendorsByName: null, // lower-cased name -> {id, name}, for the payee box (#590)
    _customerNames: null, // lower-cased; null until the register has loaded them
    _payeesAsked: new Set(),

    // The vendor list as loaded: the names the payee box suggests and the
    // ids a picked or exactly typed name links the entry to (#590).
    _setVendors(vendors) {
        BankingPage._vendorsByName = vendors
            ? new Map(vendors.map(v => [v.name.trim().toLowerCase(), { id: v.id, name: v.name }])) : null;
        BankingPage._vendorNames = vendors ? new Set(BankingPage._vendorsByName.keys()) : null;
    },

    // The payee box's suggestions: a native datalist of the vendors, so a
    // pick fills the box with the vendor's name exactly. The names in it
    // are the ones the entry links to when it posts.
    _vendorDatalist(id) {
        const opts = [...(BankingPage._vendorsByName || new Map()).values()]
            .map(v => `<option value="${escapeHtml(v.name)}"></option>`).join('');
        return `<datalist id="${id}">${opts}</datalist>`;
    },

    // The vendor an entry is paid to: the one whose name is the payee, as
    // typed or picked (capitals and edge spaces aside); else, on an edit,
    // the entry's own while its payee text is untouched; else none (null),
    // and the payee stays text (#590).
    _payeeVendorId(payee, keep = null) {
        const name = (payee || '').trim();
        const hit = BankingPage._vendorsByName && BankingPage._vendorsByName.get(name.toLowerCase());
        if (hit) return hit.id;
        return keep && name === keep.payee ? keep.vendorId : null;
    },

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
            const made = await API.post(path, { name });
            if (names) names.add(name.toLowerCase());
            if (!moneyIn && made && made.id && BankingPage._vendorsByName) {
                BankingPage._vendorsByName.set(name.trim().toLowerCase(), { id: made.id, name });
            }
            toast(`${name} added to ${list}`);
        } catch (err) {
            // a near match (409): the entry keeps its text; no second party
            const dupes = err.status === 409 && err.detail && err.detail.duplicates;
            toast(dupes ? `Not added: ${dupes.map(d => d.name).join(', ')} is already a ${noun}` : err.message, 'error');
        }
    },

    // The category's new account is the shared quick add (account_quick_add.js,
    // #485): any of the six types, never the register's own account as a
    // parent; the new account goes into both category pickers and the split
    // lines' list, and the cursor moves on to the amount in the row or the
    // memo in the form.
    _registerAccountAdd() {
        AccountQuickAdd.register('register', {
            types: BankingPage.CATEGORY_TYPES,
            chart: () => (BankingPage._accounts = BankingPage._accounts || []),
            excludeId: (accountId) => accountId,
            host: (select) => (select.id === 'qe-category' ? document.getElementById('qe-category-host') : null),
            added: (made) => {
                const opt = `<option value="${made.id}">${accountLabel(made)}</option>`;
                for (const id of ['qe-category', 'entry-category']) {
                    const s = document.getElementById(id);
                    if (s && !s.querySelector(`option[value="${made.id}"]`)) s.insertAdjacentHTML('beforeend', opt);
                }
                if (BankingPage._splitCats) BankingPage._splitCats += opt;
            },
            next: (select) => (select.id === 'qe-category'
                ? document.getElementById('qe-out')
                : document.querySelector('#modal-body input[name="description"]')),
        });
        // A review line's category (#492): the new account is picked on that
        // line and saved there as a pick is (its change event), and every
        // other line can choose it. The keyboard goes back to the picker.
        AccountQuickAdd.register('review', {
            types: BankingPage.CATEGORY_TYPES,
            compact: true,
            change: true,
            chart: () => (BankingPage._reviewAccounts = BankingPage._reviewAccounts || []),
            excludeId: (accountId) => accountId,
            added: () => BankingPage._fillReviewCats(),
        });
    },

    async showTransferForm(fromId = null) {
        const accounts = await API.get('/accounts?bank=1&active_only=true');
        // the class both lines carry (#676), as on + Entry
        const classGroup = await classFormGroupHtml();
        const opt = (a, sel) => `<option value="${a.id}" ${sel ? 'selected' : ''}>${accountLabel(a)} (${BankingPage._kindLabel(a.bank_kind).toLowerCase()})</option>`;
        const fromOpts = accounts.map(a => opt(a, a.id === fromId)).join('');
        const firstCard = accounts.find(a => a.bank_kind === 'credit_card' && a.id !== fromId);
        const toOpts = accounts.map(a => opt(a, firstCard ? a.id === firstCard.id : a.id !== fromId)).join('');
        openModal('Transfer', `
            <form onsubmit="BankingPage.saveTransfer(event)">
                <p style="font-size:11px; color:var(--gray-500); margin-bottom:8px;">Move money between bank and card accounts. Paying a card is a transfer from the bank to the card.</p>
                <div class="form-grid">
                    <div class="form-group"><label>Date *</label><input name="date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Amount *</label><input name="amount" class="money" type="number" step="0.01" min="0.01" required></div>
                    <div class="form-group"><label>From *</label><select name="from_account_id" required>${fromOpts}</select></div>
                    <div class="form-group"><label>To *</label><select name="to_account_id" required>${toOpts}</select></div>
                    <div class="form-group"><label>Reference</label><input name="reference"></div>
                    <div class="form-group"><label>Memo</label><input name="memo"></div>
                    ${classGroup}
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
        if (!(await TxnWarn.ok(form))) return;
        if (!(await ClassWarn.ok(form))) return;
        try {
            const t = await API.post('/transfers', {
                date: form.date.value,
                from_account_id: parseInt(form.from_account_id.value, 10),
                to_account_id: parseInt(form.to_account_id.value, 10),
                amount: form.amount.value,
                memo: form.memo.value || null,
                reference: form.reference.value || null,
                class_id: classIdFromForm(form),
            });
            const here = BankingPage._onRegister(t.from_account_id);
            const outside = here ? BankingPage._outsideFilter(form.date.value) : '';
            toastPosted(outside ? `Transfer posted. ${outside}` : 'Transfer posted');
            if (here) { await BankingPage._showSaved(t.from_account_id, t.id, outside); return; }
            closeModal();
            BankingPage.go(`#/banking/${t.from_account_id}`);
        } catch (err) { toast(err.message, 'error'); }
    },

    async showTransfers() {
        const rows = await API.get('/transfers');
        const body = rows.length ? `<div class="table-container"><table>
            <thead><tr><th scope="col">Date</th><th scope="col">From</th><th scope="col">To</th><th scope="col" class="amount">Amount</th><th scope="col">Memo</th>${rows.some(t => t.class_name) ? `<th scope="col">${T('Class')}</th>` : ''}<th scope="col"></th></tr></thead>
            <tbody>${rows.map(t => `<tr style="${t.status === 'void' ? 'color:var(--gray-400); text-decoration:line-through;' : ''}">
                <td>${formatDate(t.date)}</td><td>${escapeHtml(t.from_account_name)}</td><td>${escapeHtml(t.to_account_name)}</td>
                <td class="amount">${formatCurrency(t.amount)}</td><td>${escapeHtml(t.memo || '')}</td>${rows.some(x => x.class_name) ? `<td>${escapeHtml(t.class_name || '')}</td>` : ''}
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
                        <input name="statement_balance" class="money" type="number" step="0.01" required></div>
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
        const typedDate = form.statement_date.value;
        const typedBalance = form.statement_balance.value;
        try {
            const recon = await API.post('/banking/reconciliations', {
                account_id: accountId,
                statement_date: typedDate,
                statement_balance: typedBalance,
            });
            closeModal();
            BankingPage.showReconcileView(recon.id);
        } catch (err) {
            const existing = err && err.detail && err.detail.existing_id;
            if (!existing) { toast(err.message, 'error'); return; }
            closeModal();
            // One session per account (409). Rather than drop the date and
            // balance just typed and reopen the old figures (#497), offer to
            // move the open session onto them (PUT, #389/#400).
            try {
                const open = await API.get(`/banking/reconciliations/${existing}/transactions`);
                const ctx = BankingPage._ctx || {};
                const acct = ctx.accountId === accountId && ctx.accountName ? ctx.accountName : 'this account';
                const msg = `A reconciliation of ${acct} through ${formatDate(open.statement_date)} is already open `
                    + `with an ending balance of ${formatCurrency(open.statement_balance)}.\n\n`
                    + `Use the date and balance you just typed (${formatCurrency(Number(typedBalance))} through ${formatDate(typedDate)})? `
                    + `Cancel keeps the open one as it is.`;
                if (confirm(msg)) {
                    await API.put(`/banking/reconciliations/${existing}`, {
                        statement_date: typedDate,
                        statement_balance: typedBalance,
                    });
                }
            } catch (putErr) {
                toast(putErr.message, 'error');
            }
            BankingPage.showReconcileView(existing);
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
                    <button class="btn btn-secondary" onclick="BankingPage.go(BankingPage._regUrl(${data.account_id}))">Later</button>
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
                        <input name="statement_balance" class="money" type="number" step="0.01" required value="${Number(statementBalance).toFixed(2)}"></div>
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
            BankingPage.go(BankingPage._regUrl(accountId));
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
            BankingPage.go(BankingPage._regUrl(accountId));
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
            if (confirm('Importing needs a bank feed (statement identity) on this account. Create one now?')) BankingPage.showAccountForm(accountId);
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
            BankingPage._backToRegister(accountId);
        } catch (err) {
            toast(err.message, 'error');
        } finally {
            // A failed import hands the button back, on every engine.
            if (importBtn) { importBtn.disabled = false; importBtn.textContent = label; }
        }
    },
};

// the register's category quick add (#386), on the shared helper (#485)
if (typeof AccountQuickAdd !== 'undefined') BankingPage._registerAccountAdd();
