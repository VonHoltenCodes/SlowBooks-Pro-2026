/**
 * QuickBooks Online Integration — OAuth connect, import, and export
 *
 * Three-section page following the IIF page pattern:
 *   1. Connection Panel — Connect/disconnect, status display
 *   2. Import from QBO — Entity checkboxes, import button, results
 *   3. Export to QBO — Entity checkboxes, export button, results
 */
const QBOPage = {
    _status: null,
    _run: null,
    _after: 0,
    _generation: 0,
    _mounted: false,
    _starting: false,
    _lastContact: 0,

    async render() {
        // Fetch connection status
        let status = { connected: false, company_name: '', realm_id: '' };
        try {
            status = await API.get('/qbo/status?include_company_name=false');
        } catch (e) { /* not connected */ }
        QBOPage._status = status;

        const connectedClass = status.connected ? 'qbo-connected' : 'qbo-disconnected';
        const statusText = status.connected
            ? `Connected to <strong>${escapeHtml(status.company_name || 'QuickBooks Online')}</strong> (Realm: ${escapeHtml(status.realm_id)})`
            : 'Not connected';

        const entityTypes = [
            ['accounts', 'Accounts'], ['customers', 'Customers'], ['vendors', 'Vendors'],
            ['items', 'Items'], ['invoices', 'Invoices'], ['payments', 'Payments'],
        ];
        // Sales receipts import as paid-invoice + payment pairs; there is
        // no matching export entity, so only the import list offers them.
        const importEntityTypes = [...entityTypes, ['sales_receipts', 'Sales Receipts'], ['journal_entries', 'Journal Entries'], ['ledger', 'Posted Ledger Activity']];

        const checkboxHtml = types => types.map(([value, label]) =>
            `<label style="display:inline-flex; align-items:center; gap:4px; margin-right:12px;">
                <input type="checkbox" value="${value}" checked> ${label}
            </label>`
        ).join('');
        const importCheckboxes = checkboxHtml(importEntityTypes);
        const checkboxes = checkboxHtml(entityTypes);

        return `
            <div class="page-header">
                <h2>QuickBooks Online</h2>
                <div style="font-size:10px; color:var(--text-muted);">
                    REST API Integration &mdash; OAuth 2.0 + python-quickbooks SDK
                </div>
            </div>

            <div class="iif-sections qbo-sections">
                <!-- Connection Panel -->
                <div class="iif-section">
                    <h3>&#9889; Connection</h3>
                    <div class="${connectedClass}" style="padding:8px 12px; margin-bottom:12px; border-radius:4px; font-size:12px;">
                        ${statusText}
                    </div>
                    ${status.connected
                        ? `<button class="btn btn-secondary" onclick="QBOPage.disconnect()">Disconnect from QuickBooks</button>`
                        : `<button class="btn btn-primary" onclick="QBOPage.connect()">Start connection with Intuit</button>
                           <form id="qbo-manual-connect" autocomplete="off" onsubmit="QBOPage.connectManual(event)" style="margin-top:12px;">
                               <div class="form-grid">
                                   <div class="form-group">
                                       <label for="qbo-authorization-code">Authorization Code</label>
                                       <input id="qbo-authorization-code" type="password" autocomplete="off" required
                                           placeholder="Paste code or full callback URL" oninput="QBOPage.extractCallbackUrl()">
                                   </div>
                                   <div class="form-group">
                                       <label for="qbo-realm-id">Realm ID</label>
                                       <input id="qbo-realm-id" type="text" autocomplete="off" required
                                           placeholder="Paste realmId from redirect URL">
                                   </div>
                               </div>
                               <button class="btn btn-secondary" type="submit">Finish QBO connection</button>
                           </form>`
                    }
                </div>

                <!-- Import Section -->
                <div class="iif-section qbo-import-section">
                    <h3>&#9650; Import from QuickBooks Online</h3>
                    <p style="font-size:11px; color:var(--text-secondary); margin-bottom:12px;">
                        Pull data from your connected QuickBooks Online company into Slowbooks.
                        Existing records are detected by name/number and skipped.
                    </p>

                    <div style="margin-bottom:10px;">
                        <button class="btn btn-primary qbo-import-button" style="width:100%;" onclick="QBOPage.importAll()"
                            disabled>
                            Import All Data
                        </button>
                    </div>

                    <div style="font-size:10px; font-weight:700; color:var(--text-secondary); text-transform:uppercase; margin-bottom:6px;">
                        Import Individual Entity Types
                    </div>
                    <div id="qbo-import-checkboxes" style="margin-bottom:8px; font-size:11px;">
                        ${importCheckboxes}
                    </div>
                    <button class="btn btn-secondary qbo-import-button" onclick="QBOPage.importSelected()"
                        disabled>
                        Import Selected
                    </button>

                    <section id="qbo-import-log" class="qbo-import-log" aria-label="Latest QBO import log">
                        <div class="qbo-log-heading"><strong>Import log</strong><div class="qbo-log-controls"><button id="qbo-errors-filter" class="btn btn-secondary" aria-pressed="false" onclick="QBOPage.toggleErrors()">Errors</button><span id="qbo-run-status" class="qbo-run-status">Checking monitor</span><button id="qbo-monitor-retry" class="btn btn-secondary" onclick="QBOPage.retryMonitor()" hidden>Retry monitor</button></div></div>
                        <div id="qbo-run-detail" class="qbo-run-detail" role="status" aria-live="polite">The latest import will appear here.</div>
                        <div id="qbo-run-counts" class="qbo-run-counts"></div>
                        <div id="qbo-run-activity" class="qbo-run-activity">Loading latest import…</div>
                        <div id="qbo-log-scroll" class="qbo-log-scroll" tabindex="0" aria-label="Import events">
                            <table class="qbo-log-table">
                                <colgroup><col class="qbo-col-ts"><col class="qbo-col-code"><col class="qbo-col-item"><col class="qbo-col-action"><col></colgroup>
                                <thead><tr><th scope="col">Timestamp</th><th scope="col">CODE</th><th scope="col">ITEM</th><th scope="col">ACTION</th><th scope="col">MESSAGE</th></tr></thead>
                                <tbody id="qbo-log-rows"></tbody>
                            </table>
                            <div id="qbo-log-empty" class="qbo-log-empty">No imports recorded yet.</div>
                        </div>
                    </section>
                    <div id="qbo-import-result" style="margin-top:12px;"></div>
                </div>

                <!-- Export Section -->
                <div class="iif-section">
                    <h3>&#9660; Export to QuickBooks Online</h3>
                    <p style="font-size:11px; color:var(--text-secondary); margin-bottom:12px;">
                        Push Slowbooks data to your connected QuickBooks Online company.
                        Already-exported records are skipped.
                    </p>

                    <div style="margin-bottom:10px;">
                        <button class="btn btn-primary" style="width:100%;" onclick="QBOPage.exportAll()"
                            ${!status.connected ? 'disabled' : ''}>
                            Export All Data
                        </button>
                    </div>

                    <div style="font-size:10px; font-weight:700; color:var(--text-secondary); text-transform:uppercase; margin-bottom:6px;">
                        Export Individual Entity Types
                    </div>
                    <div id="qbo-export-checkboxes" style="margin-bottom:8px; font-size:11px;">
                        ${checkboxes}
                    </div>
                    <button class="btn btn-secondary" onclick="QBOPage.exportSelected()"
                        ${!status.connected ? 'disabled' : ''}>
                        Export Selected
                    </button>

                    <div id="qbo-export-result" style="margin-top:12px;"></div>
                </div>
            </div>`;
    },

    // ==== Connection ====

    async connect() {
        // Keep this page available for copying values from a failed localhost
        // redirect when SlowBooks is running on a different machine.
        const authTab = window.open('about:blank', '_blank');
        if (authTab) authTab.opener = null;
        try {
            App.setStatus('Connecting to QuickBooks Online...');
            const data = await API.get('/qbo/auth-url');
            if (!data.url) throw new Error('QuickBooks did not provide an authorization link');
            if (authTab) authTab.location.replace(data.url);
            else window.location.href = data.url;
        } catch (err) {
            if (authTab) authTab.close();
            toast(err.message, 'error');
            App.setStatus('Connection failed');
        }
    },

    extractCallbackUrl() {
        const codeInput = $('#qbo-authorization-code');
        const realmInput = $('#qbo-realm-id');
        if (!codeInput || !realmInput || !/^https?:\/\//i.test(codeInput.value)) return;
        try {
            const callback = new URL(codeInput.value);
            const code = callback.searchParams.get('code');
            const realm = callback.searchParams.get('realmId');
            if (code && realm) {
                codeInput.value = code;
                realmInput.value = realm;
            }
        } catch (_) { /* Wait for a complete pasted URL. */ }
    },

    async connectManual(event) {
        event.preventDefault();
        QBOPage.extractCallbackUrl();
        const codeInput = $('#qbo-authorization-code');
        const realmInput = $('#qbo-realm-id');
        const form = $('#qbo-manual-connect');
        const button = form.querySelector('button[type="submit"]');
        button.disabled = true;
        try {
            App.setStatus('Finishing QuickBooks connection...');
            await API.post('/qbo/connect-manual', {
                authorization_code: codeInput.value.trim(),
                realm_id: realmInput.value.trim(),
            });
            codeInput.value = '';
            realmInput.value = '';
            toast('Connected to QuickBooks Online');
            await App.navigate('#/qbo');
        } catch (err) {
            toast(err.message, 'error');
            App.setStatus('Connection failed');
        } finally {
            button.disabled = false;
        }
    },

    async disconnect() {
        try {
            await API.post('/qbo/disconnect');
            toast('Disconnected from QuickBooks Online');
            App.navigate('#/qbo');
        } catch (err) {
            toast(err.message, 'error');
        }
    },

    // ==== Import ====

    async importAll() {
        await QBOPage._startImport(null);
    },

    async importSelected() {
        const checked = QBOPage._getChecked('qbo-import-checkboxes');
        if (!checked.length) { toast('Select at least one entity type', 'error'); return; }
        await QBOPage._startImport(checked);
    },

    mount() {
        QBOPage.unmount();
        QBOPage._mounted = true;
        QBOPage._run = null;
        QBOPage._after = 0;
        QBOPage._lastContact = 0;
        QBOPage._monitorReady = false;
        QBOPage._monitorBlocked = false;
        QBOPage._monitorError = null;
        QBOPage._startError = null;
        QBOPage._diagnosticKey = '';
        QBOPage._errorsOnly = false;
        QBOPage._enteredAt = Date.now();
        QBOPage._setImportButtons();
        QBOPage._clockTimer = setInterval(() => QBOPage._updateActivity(), 1000);
        QBOPage._pollLog();
        return () => QBOPage.unmount();
    },

    unmount() {
        QBOPage._mounted = false;
        QBOPage._generation++;
        clearTimeout(QBOPage._pollTimer);
        clearInterval(QBOPage._clockTimer);
        QBOPage._pollController?.abort();
        QBOPage._startController?.abort();
        QBOPage._starting = false;
    },

    _active() {
        return QBOPage._run && ['queued', 'running'].includes(QBOPage._run.status);
    },

    _setImportButtons() {
        $$('.qbo-import-button').forEach(button => {
            button.disabled = QBOPage._starting || QBOPage._active() || !QBOPage._monitorReady || QBOPage._monitorBlocked || !QBOPage._status?.connected;
        });
    },

    retryMonitor() {
        if (!QBOPage._mounted) return;
        clearTimeout(QBOPage._pollTimer);
        const previous = QBOPage._pollController;
        QBOPage._pollController = null;
        previous?.abort();
        QBOPage._monitorBlocked = false;
        QBOPage._monitorReady = false;
        QBOPage._monitorError = null;
        QBOPage._diagnostic('info', 'MONITOR_RETRY', 'monitor', 'Retrying import monitoring');
        QBOPage._setImportButtons();
        QBOPage._updateActivity();
        QBOPage._pollLog();
    },

    toggleErrors() {
        QBOPage._errorsOnly = !QBOPage._errorsOnly;
        $('#qbo-import-log').classList.toggle('qbo-errors-only', QBOPage._errorsOnly);
        $('#qbo-errors-filter').setAttribute('aria-pressed', String(QBOPage._errorsOnly));
        QBOPage._updateLogEmpty();
    },

    _updateLogEmpty() {
        const rows = $('#qbo-log-rows');
        const empty = $('#qbo-log-empty');
        const hasRows = QBOPage._errorsOnly ? !!rows.querySelector('.qbo-log-error') : !!rows.innerHTML;
        empty.hidden = hasRows;
        empty.textContent = QBOPage._errorsOnly ? 'No errors recorded.' : QBOPage._run ? 'Waiting for import events…' : 'No imports recorded yet.';
    },

    async _readImportResponse(response, action) {
        QBOPage._lastContact = Date.now();
        let data;
        try { data = await response.json(); } catch (_) { /* HTTP status still identifies failures with non-JSON bodies. */ }
        if (!response.ok) {
            const detail = typeof data?.detail === 'string' ? data.detail : data?.detail?.message;
            let message = `${action === 'start' ? 'Import request' : 'Import monitor'} failed (HTTP ${response.status})${detail ? `: ${detail}` : '.'}`;
            if ([404, 405, 501].includes(response.status)) {
                message = `Import monitoring is unavailable on the running Slowbooks server (HTTP ${response.status}). Restart the server to load the updated importer, then choose Retry monitor.`;
            } else if (response.status === 401) {
                message = 'Your Slowbooks session expired (HTTP 401). Sign in again to monitor or start imports.';
            }
            const error = new Error(message);
            error.httpStatus = response.status;
            error.code = `HTTP_${response.status}`;
            error.runId = data?.detail?.run_id;
            throw error;
        }
        if (!data || typeof data !== 'object') {
            const error = new Error('The Slowbooks import endpoint returned an unexpected response. Restart the server, then choose Retry monitor.');
            error.httpStatus = response.status;
            error.code = 'IMPORT_INVALID_RESPONSE';
            throw error;
        }
        return data;
    },

    _diagnostic(level, code, action, message, request = '') {
        const key = `${code}:${action}:${request}:${message}`;
        if (QBOPage._diagnosticKey === key) return;
        QBOPage._diagnosticKey = key;
        QBOPage._insertEvents([{
            timestamp: new Date().toISOString(), level, code, action, message,
            entity: 'monitor', item_id: '', item_label: request,
        }]);
    },

    _headers() {
        const headers = { 'Content-Type': 'application/json' };
        const company = localStorage.getItem('slowbooks_company');
        if (company) headers['X-Company-Id'] = company;
        return headers;
    },

    async _startImport(entities) {
        if (QBOPage._starting || QBOPage._active()) return;
        if (!QBOPage._monitorReady || QBOPage._monitorBlocked) {
            QBOPage._diagnostic('error', QBOPage._monitorError?.code || 'MONITOR_NOT_READY', 'start',
                QBOPage._monitorError?.message || 'Import was not started because monitoring is not ready. Choose Retry monitor.', 'POST /api/qbo/import-runs');
            QBOPage._updateActivity();
            return;
        }
        QBOPage._starting = true;
        QBOPage._startError = null;
        QBOPage._requestStartedAt = Date.now();
        QBOPage._diagnostic('info', 'IMPORT_REQUEST', 'start', `Requesting import: ${entities ? entities.join(', ') : 'all entity types'}`, 'POST /api/qbo/import-runs');
        QBOPage._setImportButtons();
        QBOPage._updateActivity();
        const generation = QBOPage._generation;
        const controller = new AbortController();
        QBOPage._startController = controller;
        const deadline = setTimeout(() => controller.abort(), 15000);
        try {
            const response = await fetch('/api/qbo/import-runs', {
                method: 'POST', headers: QBOPage._headers(), credentials: 'same-origin',
                body: JSON.stringify({ entities }), signal: controller.signal,
            });
            const data = await QBOPage._readImportResponse(response, 'start');
            if (!QBOPage._mounted || generation !== QBOPage._generation) return;
            if (!data.run_id) throw Object.assign(new Error('The import request returned no run ID. Checking the monitor before another import can start.'), { code: 'IMPORT_INVALID_RESPONSE' });
            QBOPage._after = 0;
            QBOPage._run = { run_id: data.run_id, status: 'queued', started_at: new Date().toISOString(), counters: {}, current_step: 'Queued' };
            $('#qbo-log-rows').innerHTML = '';
            $('#qbo-import-result').innerHTML = '';
            QBOPage._updateLogEmpty();
            App.setStatus('QuickBooks Online — Import running');
        } catch (error) {
            if (QBOPage._mounted && generation === QBOPage._generation) {
                const code = error.code || (error.name === 'AbortError' ? 'IMPORT_REQUEST_TIMEOUT' : 'NETWORK_ERROR');
                const message = error.httpStatus ? error.message : 'The import request could not be confirmed. Checking the latest import before retrying.';
                QBOPage._startError = { code, message, httpStatus: error.httpStatus };
                QBOPage._diagnostic('error', code, 'start', message, 'POST /api/qbo/import-runs');
                if ([404, 405, 501].includes(error.httpStatus)) {
                    QBOPage._monitorBlocked = true;
                    QBOPage._monitorReady = false;
                    QBOPage._monitorError = QBOPage._startError;
                }
                if (error.httpStatus === 401) window.SlowbooksAuth?.promptAuth();
                App.setStatus('QuickBooks Online — Import request failed');
            }
        } finally {
            clearTimeout(deadline);
            if (QBOPage._mounted && generation === QBOPage._generation) {
                QBOPage._starting = false;
                QBOPage._setImportButtons();
                clearTimeout(QBOPage._pollTimer);
                const previous = QBOPage._pollController;
                QBOPage._pollController = null;
                previous?.abort();
                QBOPage._updateActivity();
                if (!QBOPage._monitorBlocked) QBOPage._pollLog();
            }
        }
    },

    async _pollLog() {
        if (!QBOPage._mounted) return;
        const generation = QBOPage._generation;
        const controller = new AbortController();
        QBOPage._pollController = controller;
        const deadline = setTimeout(() => controller.abort(), 10000);
        let again = true;
        let delay = 2000;
        try {
            const response = await fetch(`/api/qbo/import-runs/latest?after=${QBOPage._after}`, {
                signal: controller.signal, cache: 'no-store', headers: QBOPage._headers(), credentials: 'same-origin',
            });
            const data = await QBOPage._readImportResponse(response, 'monitor');
            if (!QBOPage._mounted || generation !== QBOPage._generation || QBOPage._pollController !== controller) return;
            if (!Array.isArray(data.events) || !Object.hasOwn(data, 'run')) {
                throw Object.assign(new Error('The server returned an invalid import log. Restart the server, then choose Retry monitor.'), { code: 'IMPORT_INVALID_RESPONSE', httpStatus: response.status });
            }
            QBOPage._lastContact = Date.now();
            const recovered = !!QBOPage._monitorError;
            QBOPage._monitorReady = true;
            QBOPage._monitorBlocked = false;
            QBOPage._monitorError = null;
            QBOPage._diagnosticKey = '';
            QBOPage._serverOffset = Date.parse(data.server_time) - Date.now();
            if (data.run && QBOPage._run?.run_id !== data.run.run_id) {
                const hadCursor = QBOPage._after > 0;
                QBOPage._after = 0;
                $('#qbo-log-rows').innerHTML = '';
                $('#qbo-import-result').innerHTML = '';
                QBOPage._run = data.run;
                QBOPage._startError = null;
                QBOPage._setImportButtons();
                QBOPage._updateActivity();
                if (hadCursor) { delay = 0; return; }
            }
            QBOPage._run = data.run;
            QBOPage._appendEvents(data.events);
            QBOPage._setImportButtons();
            QBOPage._updateActivity();
            QBOPage._updateLogEmpty();
            if (recovered) QBOPage._diagnostic('info', 'MONITOR_RECONNECTED', 'monitor', 'Import monitoring reconnected');
            if (data.run && !QBOPage._active() && !data.has_more) {
                QBOPage._showResult('qbo-import-result', data.run.result, 'imported');
                App.setStatus(`QuickBooks Online — ${QBOPage._statusLabel(data.run.status)}`);
            }
            again = data.has_more || QBOPage._active();
            if (data.has_more) delay = 0;
        } catch (error) {
            if (!QBOPage._mounted || generation !== QBOPage._generation || QBOPage._pollController !== controller) return;
            const status = error.httpStatus;
            const blocked = error.code === 'IMPORT_INVALID_RESPONSE' || status === 501 || (status >= 400 && status < 500 && ![408, 429].includes(status));
            const code = error.code || (error.name === 'AbortError' ? 'MONITOR_TIMEOUT' : 'NETWORK_ERROR');
            const message = error.code ? error.message : error.name === 'AbortError' ? 'The import monitor did not respond within 10 seconds. Retrying automatically.' : 'Could not reach the Slowbooks server to read import progress. Retrying automatically.';
            QBOPage._monitorError = { code, message, httpStatus: status };
            QBOPage._monitorReady = false;
            QBOPage._monitorBlocked = blocked;
            QBOPage._diagnostic('error', code, 'monitor', message, 'GET /api/qbo/import-runs/latest');
            QBOPage._setImportButtons();
            QBOPage._updateActivity();
            again = !blocked;
            if (status === 401) window.SlowbooksAuth?.promptAuth();
        } finally {
            clearTimeout(deadline);
            if (again && QBOPage._mounted && generation === QBOPage._generation && QBOPage._pollController === controller) {
                QBOPage._pollTimer = setTimeout(() => QBOPage._pollLog(), delay);
            }
        }
    },

    _appendEvents(events) {
        const fresh = events.filter(event => event.sequence > QBOPage._after);
        if (fresh.length) QBOPage._after = fresh[fresh.length - 1].sequence;
        QBOPage._insertEvents(fresh);
    },

    _insertEvents(events) {
        const scroll = $('#qbo-log-scroll');
        const follow = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 35;
        const rows = events.map(event => {
            const time = new Date(event.timestamp);
            const ts = time.toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }) + '.' + String(time.getMilliseconds()).padStart(3, '0');
            const level = ['info', 'warning', 'error'].includes(event.level) ? event.level : 'info';
            const code = /^\d+$/.test(event.code) ? `QBO ${event.code}` : event.code;
            const codeHtml = escapeHtml(code).replace(/_/g, '_<wbr>');
            const identity = event.item_id ? `#${event.item_id}` : '';
            const context = [event.entity?.replace(/_/g, ' '), event.item_label].filter(Boolean).join(' · ');
            const message = [context, event.message].filter(Boolean).join(' — ');
            return `<tr class="qbo-log-${level}" ${event.sequence ? `data-sequence="${event.sequence}"` : 'data-diagnostic="true"'}>
                <td title="${escapeHtml(event.timestamp)}">${escapeHtml(ts)}</td>
                <td title="${escapeHtml(level.toUpperCase())}">${code ? `<span class="qbo-log-code">${codeHtml}</span>` : escapeHtml(level.toUpperCase())}</td>
                <td>${escapeHtml(identity)}</td>
                <td>${escapeHtml(event.action)}</td><td>${escapeHtml(message)}</td></tr>`;
        }).join('');
        if (rows) $('#qbo-log-rows').insertAdjacentHTML('beforeend', rows);
        QBOPage._updateLogEmpty();
        if (follow) scroll.scrollTop = scroll.scrollHeight;
    },

    _statusLabel(status) {
        return ({ queued: 'Queued', running: 'Running', completed: 'Completed', completed_with_errors: 'Completed with errors', failed: 'Failed', interrupted: 'Interrupted' })[status] || 'Ready';
    },

    _duration(seconds) {
        seconds = Math.max(0, Math.floor(seconds));
        return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    },

    _updateActivity() {
        if (!QBOPage._mounted) return;
        const run = QBOPage._run;
        const now = Date.now();
        const serverNow = now + (QBOPage._serverOffset || 0);
        const stale = now - (QBOPage._lastContact || QBOPage._enteredAt) >= 15000;
        const active = QBOPage._active();
        const wait = run ? Math.max(0, (serverNow - Date.parse(run.last_progress_at || run.started_at)) / 1000) : 0;
        const waiting = active && wait >= 30;
        const badge = $('#qbo-run-status');
        const monitorError = QBOPage._monitorError;
        const startError = QBOPage._startError;
        $('#qbo-monitor-retry').hidden = !monitorError && QBOPage._monitorReady;
        if (monitorError) {
            const unsupported = [404, 405, 501].includes(monitorError.httpStatus);
            badge.textContent = unsupported ? 'Server update required' : monitorError.httpStatus ? 'Monitor unavailable' : stale ? 'Connection interrupted' : 'Reconnecting';
            badge.className = `qbo-run-status ${monitorError.httpStatus ? 'qbo-status-error' : 'qbo-status-warning'}`;
            $('#qbo-run-detail').textContent = monitorError.message;
            $('#qbo-run-activity').textContent = QBOPage._monitorBlocked
                ? unsupported ? 'Monitoring paused. Restart the Slowbooks server, then choose Retry monitor.' : 'Monitoring paused. Resolve the error, then choose Retry monitor.'
                : `${monitorError.httpStatus ? `Server responded HTTP ${monitorError.httpStatus}. ` : ''}Retrying automatically; retaining the last readable log.`;
            App.setStatus(`QuickBooks Online — ${badge.textContent}`);
        } else if (QBOPage._starting) {
            badge.textContent = 'Starting';
            badge.className = 'qbo-run-status qbo-status-running';
            $('#qbo-run-detail').textContent = `Submitting import request · Elapsed ${QBOPage._duration((now - QBOPage._requestStartedAt) / 1000)}`;
            $('#qbo-run-activity').textContent = 'Waiting for the Slowbooks server to accept the import.';
        } else if (startError && !active) {
            badge.textContent = startError.httpStatus ? 'Import request failed' : 'Import start unconfirmed';
            badge.className = 'qbo-run-status qbo-status-error';
            $('#qbo-run-detail').textContent = startError.message;
            $('#qbo-run-activity').textContent = 'The failed request is recorded below. The latest saved import log is retained.';
        } else if (!QBOPage._monitorReady) {
            badge.textContent = 'Checking monitor';
            badge.className = 'qbo-run-status';
            $('#qbo-run-detail').textContent = 'Checking that live import monitoring is available before starting an import.';
            $('#qbo-run-activity').textContent = 'Loading latest import…';
        } else {
            badge.textContent = stale && (active || !QBOPage._lastContact) ? 'Connection interrupted' : waiting ? 'Waiting' : QBOPage._statusLabel(run?.status);
            badge.className = `qbo-run-status ${stale && active || waiting || run?.status === 'completed_with_errors' ? 'qbo-status-warning' : run?.status === 'failed' || run?.status === 'interrupted' ? 'qbo-status-error' : active ? 'qbo-status-running' : run ? 'qbo-status-complete' : ''}`;
            if (!run) {
                $('#qbo-run-detail').textContent = 'The latest import will appear here.';
                $('#qbo-run-activity').textContent = stale ? 'Connection interrupted — reconnecting…' : QBOPage._lastContact ? 'Ready for an import.' : 'Loading latest import…';
                return;
            }
            const end = run.finished_at ? Date.parse(run.finished_at) : serverNow;
            const elapsed = QBOPage._duration((end - Date.parse(run.started_at)) / 1000);
            $('#qbo-run-detail').textContent = `${run.current_step} · Elapsed ${elapsed}`;
            $('#qbo-run-activity').textContent = stale && active ? 'Connection interrupted — retaining log and reconnecting…' : active ? `${waiting ? 'Waiting for progress' : 'Server responding'} · Last progress ${QBOPage._duration(wait)} ago` : `Started ${new Date(run.started_at).toLocaleString()} · ${run.started_by || 'operator'}`;
        }
        if (!run) return;
        const counts = run.counters;
        $('#qbo-run-counts').innerHTML = ['fetched', 'processed', 'imported', 'pending', 'skipped', 'errors'].map(key => `<span>${key[0].toUpperCase() + key.slice(1)} <strong>${Number(counts[key] || 0).toLocaleString()}</strong></span>`).join('');
    },

    // ==== Export ====

    async exportAll() {
        try {
            App.setStatus('Exporting to QuickBooks Online...');
            const result = await API.post('/qbo/export');
            QBOPage._showResult('qbo-export-result', result, 'exported');
            const total = (result.accounts || 0) + (result.customers || 0) +
                          (result.vendors || 0) + (result.items || 0) +
                          (result.invoices || 0) + (result.payments || 0);
            toast(`Exported ${total} records to QBO`);
            App.setStatus('QuickBooks Online — Export complete');
        } catch (err) {
            toast(err.message, 'error');
            App.setStatus('Export failed');
        }
    },

    async exportSelected() {
        const checked = QBOPage._getChecked('qbo-export-checkboxes');
        if (checked.length === 0) { toast('Select at least one entity type', 'error'); return; }

        const result = { accounts: 0, customers: 0, vendors: 0, items: 0, invoices: 0, payments: 0, errors: [] };
        App.setStatus('Exporting to QuickBooks Online...');

        for (const entity of checked) {
            try {
                const r = await fetch(`/api/qbo/export/${entity}`, { method: 'POST' });
                const data = await r.json();
                if (!r.ok) throw new Error(data.detail || `Export ${entity} failed`);
                result[entity] = data.exported || 0;
                if (data.errors) result.errors.push(...data.errors);
            } catch (err) {
                result.errors.push({ entity, message: err.message });
            }
        }

        QBOPage._showResult('qbo-export-result', result, 'exported');
        App.setStatus('QuickBooks Online — Export complete');
    },

    // ==== Helpers ====

    _getChecked(containerId) {
        const container = $(`#${containerId}`);
        if (!container) return [];
        return Array.from(container.querySelectorAll('input[type="checkbox"]:checked'))
            .map(cb => cb.value);
    },

    _showResult(targetId, result, verb) {
        const sections = [
            ['Accounts', result.accounts],
            ['Customers', result.customers],
            ['Vendors', result.vendors],
            ['Items', result.items],
            ['Invoices', result.invoices],
            ['Payments', result.payments],
            ['Sales Receipts', result.sales_receipts],
            ['Journal Entries', result.journal_entries],
            ['Posted Ledger Activity', result.ledger],
        ];

        let html = '<div class="iif-results"><h4>Results</h4>';
        for (const [name, count] of sections) {
            if (count > 0) {
                html += `<div class="result-row">
                    <span>${name}</span>
                    <span class="result-count">${count} ${verb}</span>
                </div>`;
            }
        }

        const total = sections.reduce((sum, [, c]) => sum + (c || 0), 0);
        if (total === 0 && (!result.errors || result.errors.length === 0)) {
            html += '<div class="result-row"><span>No new records to sync</span></div>';
        }
        html += '</div>';

        if (result.errors && result.errors.length > 0) {
            html += '<div class="iif-errors">';
            result.errors.forEach(e => {
                const msg = typeof e === 'string' ? e :
                    `${e.entity || ''}: ${e.message || JSON.stringify(e)}`;
                html += `${escapeHtml(msg)}<br>`;
            });
            html += '</div>';
        }

        const el = $(`#${targetId}`);
        if (el) el.innerHTML = html;
    },
};
