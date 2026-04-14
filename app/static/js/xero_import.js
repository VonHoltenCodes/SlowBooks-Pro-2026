const XeroImportPage = {
    async render() {
        return `
            <div class="page-header">
                <h2>Xero Import</h2>
            </div>
            <div class="settings-section">
                <h3>Required CSV Exports</h3>
                <div style="font-size:11px; color:var(--text-muted); margin-bottom:12px;">
                    Upload Xero-exported CSV files for Chart of Accounts, General Ledger, Trial Balance, Profit &amp; Loss, and Balance Sheet. The importer will dry-run verification before allowing import.
                </div>
                <form onsubmit="XeroImportPage.dryRun(event)">
                    <div class="form-group">
                        <label>Xero export bundle</label>
                        <input id="xero-import-files" name="files" type="file" accept=".csv" multiple required>
                    </div>
                    <div class="form-actions">
                        <button type="submit" class="btn btn-secondary">Dry Run</button>
                        <button type="button" class="btn btn-primary" onclick="XeroImportPage.importBundle()">Import</button>
                    </div>
                </form>
                <div id="xero-import-results" style="margin-top:12px;"></div>
            </div>`;
    },

    async dryRun(e) {
        e.preventDefault();
        const files = $('#xero-import-files').files;
        const formData = new FormData();
        Array.from(files).forEach(file => formData.append('files', file));
        try {
            const resp = await fetch('/api/xero-import/dry-run', {
                method: 'POST',
                body: formData,
                headers: API.authHeaders ? API.authHeaders() : {},
            });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Dry run failed');
            XeroImportPage._lastDryRun = data;
            $('#xero-import-results').innerHTML = XeroImportPage.renderSummary(data, false);
            toast(data.import_ready ? 'Xero dry run passed' : 'Xero dry run found issues');
        } catch (err) {
            $('#xero-import-results').innerHTML = `<div class="empty-state"><p>${escapeHtml(err.message)}</p></div>`;
            toast(err.message, 'error');
        }
    },

    async importBundle() {
        const files = $('#xero-import-files').files;
        const formData = new FormData();
        Array.from(files).forEach(file => formData.append('files', file));
        try {
            const resp = await fetch('/api/xero-import/import', {
                method: 'POST',
                body: formData,
                headers: API.authHeaders ? API.authHeaders() : {},
            });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Import failed');
            $('#xero-import-results').innerHTML = XeroImportPage.renderSummary(data, true);
            toast('Xero import complete');
        } catch (err) {
            toast(err.message, 'error');
        }
    },

    renderSummary(data, imported) {
        const verification = data.verification || {};
        return `
            <div class="table-container"><table>
                <tbody>
                    ${data.required_files ? `<tr><td>Required files</td><td>${escapeHtml((data.required_files || []).join(', '))}</td></tr>` : ''}
                    ${data.detected_files ? `<tr><td>Detected files</td><td>${escapeHtml(Object.keys(data.detected_files).join(', '))}</td></tr>` : ''}
                    ${data.counts ? `<tr><td>Counts</td><td>${escapeHtml(JSON.stringify(data.counts))}</td></tr>` : ''}
                    ${data.journal_groups !== undefined ? `<tr><td>Journal groups</td><td>${data.journal_groups}</td></tr>` : ''}
                    ${data.import_ready !== undefined ? `<tr><td>Import ready</td><td>${data.import_ready ? 'Yes' : 'No'}</td></tr>` : ''}
                    ${data.imported_accounts !== undefined ? `<tr><td>Imported accounts</td><td>${data.imported_accounts}</td></tr>` : ''}
                    ${data.imported_transactions !== undefined ? `<tr><td>Imported transactions</td><td>${data.imported_transactions}</td></tr>` : ''}
                    ${data.imported_transaction_lines !== undefined ? `<tr><td>Imported lines</td><td>${data.imported_transaction_lines}</td></tr>` : ''}
                    <tr><td>Trial Balance</td><td>${verification.trial_balance_ok ? 'OK' : 'Mismatch'}</td></tr>
                    <tr><td>Profit &amp; Loss</td><td>${verification.profit_loss_ok ? 'OK' : 'Mismatch'}</td></tr>
                    <tr><td>Balance Sheet</td><td>${verification.balance_sheet_ok ? 'OK' : 'Mismatch'}</td></tr>
                </tbody>
            </table></div>
            ${(data.errors || []).length ? `<div class="iif-errors">${data.errors.map(err => escapeHtml(err)).join('<br>')}</div>` : ''}
            ${(verification.trial_balance_mismatches || []).length ? `<div class="iif-errors">${verification.trial_balance_mismatches.map(err => escapeHtml(err)).join('<br>')}</div>` : ''}
            ${(verification.profit_loss_mismatches || []).length ? `<div class="iif-errors">${verification.profit_loss_mismatches.map(err => escapeHtml(err)).join('<br>')}</div>` : ''}
            ${(verification.balance_sheet_mismatches || []).length ? `<div class="iif-errors">${verification.balance_sheet_mismatches.map(err => escapeHtml(err)).join('<br>')}</div>` : ''}
            ${imported ? '<div class="iif-validation-ok">Historic ledger imported from Xero report files.</div>' : ''}`;
    },
};
