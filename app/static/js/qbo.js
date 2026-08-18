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

    async render() {
        // Fetch connection status
        let status = { connected: false, company_name: '', realm_id: '' };
        try {
            status = await API.get('/qbo/status');
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
        const importEntityTypes = [...entityTypes, ['sales_receipts', 'Sales Receipts']];

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

            <div class="iif-sections">
                <!-- Connection Panel -->
                <div class="iif-section">
                    <h3>&#9889; Connection</h3>
                    <div class="${connectedClass}" style="padding:8px 12px; margin-bottom:12px; border-radius:4px; font-size:12px;">
                        ${statusText}
                    </div>
                    ${status.connected
                        ? `<button class="btn btn-secondary" onclick="QBOPage.disconnect()">Disconnect from QuickBooks</button>`
                        : `<button class="btn btn-primary" onclick="QBOPage.connect()">Connect to QuickBooks</button>
                           <div style="font-size:10px; color:var(--text-muted); margin-top:8px;">
                               Configure Client ID and Secret in <a href="#/settings" style="color:var(--qb-blue);">Settings</a> first.
                           </div>`
                    }
                </div>

                <!-- Import Section -->
                <div class="iif-section">
                    <h3>&#9650; Import from QuickBooks Online</h3>
                    <p style="font-size:11px; color:var(--text-secondary); margin-bottom:12px;">
                        Pull data from your connected QuickBooks Online company into Slowbooks.
                        Existing records are detected by name/number and skipped.
                    </p>

                    <div style="margin-bottom:10px;">
                        <button class="btn btn-primary" style="width:100%;" onclick="QBOPage.importAll()"
                            ${!status.connected ? 'disabled' : ''}>
                            Import All Data
                        </button>
                    </div>

                    <div style="font-size:10px; font-weight:700; color:var(--text-secondary); text-transform:uppercase; margin-bottom:6px;">
                        Import Individual Entity Types
                    </div>
                    <div id="qbo-import-checkboxes" style="margin-bottom:8px; font-size:11px;">
                        ${importCheckboxes}
                    </div>
                    <button class="btn btn-secondary" onclick="QBOPage.importSelected()"
                        ${!status.connected ? 'disabled' : ''}>
                        Import Selected
                    </button>

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
        try {
            App.setStatus('Connecting to QuickBooks Online...');
            const data = await API.get('/qbo/auth-url');
            if (data.url) {
                window.location.href = data.url;
            }
        } catch (err) {
            toast(err.message, 'error');
            App.setStatus('Connection failed');
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
        try {
            App.setStatus('Importing from QuickBooks Online...');
            const result = await API.post('/qbo/import');
            QBOPage._showResult('qbo-import-result', result, 'imported');
            const total = (result.accounts || 0) + (result.customers || 0) +
                          (result.vendors || 0) + (result.items || 0) +
                          (result.invoices || 0) + (result.payments || 0) +
                          (result.sales_receipts || 0);
            toast(`Imported ${total} records from QBO`);
            App.setStatus('QuickBooks Online — Import complete');
        } catch (err) {
            toast(err.message, 'error');
            App.setStatus('Import failed');
        }
    },

    async importSelected() {
        const checked = QBOPage._getChecked('qbo-import-checkboxes');
        if (checked.length === 0) { toast('Select at least one entity type', 'error'); return; }

        const result = { accounts: 0, customers: 0, vendors: 0, items: 0, invoices: 0, payments: 0, sales_receipts: 0, errors: [] };
        App.setStatus('Importing from QuickBooks Online...');

        for (const entity of checked) {
            try {
                const r = await fetch(`/api/qbo/import/${entity}`, { method: 'POST' });
                const data = await r.json();
                if (!r.ok) throw new Error(data.detail || `Import ${entity} failed`);
                result[entity] = data.imported || 0;
                if (data.errors) result.errors.push(...data.errors);
            } catch (err) {
                result.errors.push({ entity, message: err.message });
            }
        }

        QBOPage._showResult('qbo-import-result', result, 'imported');
        App.setStatus('QuickBooks Online — Import complete');
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
