/**
 * IIF Import/Export — QuickBooks 2003 Pro Interoperability
 *
 * In QB2003: Import: File > Utilities > Import > IIF Files
 * Export: File > Utilities > Export > Lists to IIF Files
 *
 * The original DLL was a 342KB mess of fscanf() calls with no error
 * handling. A tab character in a company name would crash it. We do better.
 */
const IIFPage = {
    _selectedFile: null,
    _validated: false,

    async render() {
        return `
            <div class="page-header">
                <h2>QuickBooks Interop</h2>
                <div style="font-size:10px; color:var(--text-muted);">
                    IIF Import/Export &mdash; Compatible with QuickBooks 2003 Pro
                </div>
            </div>

            <div class="iif-sections">
                <!-- Export Section -->
                <div class="iif-section">
                    <h3>&#9660; Export to IIF</h3>
                    <p style="font-size:11px; color:var(--text-secondary); margin-bottom:12px;">
                        Download Slowbooks data as .iif files for import into QuickBooks 2003 Pro
                        via File &gt; Utilities &gt; Import &gt; IIF Files.
                    </p>

                    <div style="margin-bottom:10px;">
                        <button class="btn btn-primary" style="width:100%;" onclick="IIFPage.exportAll()">
                            Export All Data
                        </button>
                    </div>

                    <div style="font-size:10px; font-weight:700; color:var(--text-secondary); text-transform:uppercase; margin-bottom:6px;">
                        Export Individual Sections
                    </div>
                    <div class="iif-export-grid">
                        <button class="btn btn-secondary" onclick="IIFPage.exportSection('accounts')">Accounts</button>
                        <button class="btn btn-secondary" onclick="IIFPage.exportSection('customers')">Customers</button>
                        <button class="btn btn-secondary" onclick="IIFPage.exportSection('vendors')">Vendors</button>
                        <button class="btn btn-secondary" onclick="IIFPage.exportSection('items')">Items</button>
                        <button class="btn btn-secondary" onclick="IIFPage.exportSection('estimates')">Estimates</button>
                        <button class="btn btn-secondary" onclick="IIFPage.exportSection('classes')">Classes</button>
                    </div>

                    <div style="margin-top:12px; padding-top:10px; border-top:1px solid var(--panel-border);">
                        <div style="font-size:10px; font-weight:700; color:var(--text-secondary); text-transform:uppercase; margin-bottom:6px;">
                            Invoices &amp; Payments (with date range)
                        </div>
                        <div class="iif-date-range">
                            <label>From</label>
                            <input type="date" id="iif-date-from">
                            <label>To</label>
                            <input type="date" id="iif-date-to">
                        </div>
                        <div style="display:flex; gap:6px;">
                            <button class="btn btn-secondary" style="flex:1;" onclick="IIFPage.exportSection('invoices')">Invoices</button>
                            <button class="btn btn-secondary" style="flex:1;" onclick="IIFPage.exportSection('payments')">Payments</button>
                            <button class="btn btn-secondary" style="flex:1;" onclick="IIFPage.exportSection('sales-receipts')">Sales Receipts</button>
                            <button class="btn btn-secondary" style="flex:1;" onclick="IIFPage.exportSection('bills')">Bills</button>
                            <button class="btn btn-secondary" style="flex:1;" onclick="IIFPage.exportSection('deposits')">Deposits</button>
                        </div>
                    </div>
                </div>

                <!-- Import Section -->
                <div class="iif-section">
                    <h3>&#9650; Import from IIF</h3>
                    <p style="font-size:11px; color:var(--text-secondary); margin-bottom:12px;">
                        Upload .iif files exported from QuickBooks 2003 Pro
                        via File &gt; Utilities &gt; Export &gt; Lists to IIF Files.
                    </p>

                    <div id="iif-dropzone" class="iif-dropzone"
                         onclick="document.getElementById('iif-file-input').click()"
                         ondragover="IIFPage.handleDragOver(event)"
                         ondragleave="IIFPage.handleDragLeave(event)"
                         ondrop="IIFPage.handleDrop(event)">
                        <div class="iif-dropzone-icon">&#9783;</div>
                        <div class="iif-dropzone-text">
                            <strong>Click to browse</strong> or drag &amp; drop an .iif file here
                        </div>
                    </div>
                    <input type="file" id="iif-file-input" accept=".iif" style="display:none;"
                           onchange="IIFPage.handleFileSelect(event)">

                    <div id="iif-file-info" style="display:none;"></div>

                    <div id="iif-import-actions" class="iif-import-actions" style="display:none;">
                        <button class="btn btn-secondary" onclick="IIFPage.validateFile()">Validate</button>
                        <button class="btn btn-primary" id="iif-import-btn" onclick="IIFPage.importFile()" disabled>Import</button>
                        <button class="btn" onclick="IIFPage.clearFile()" style="margin-left:auto;">Clear</button>
                    </div>

                    <div id="iif-validation-result"></div>
                    <div id="iif-import-result"></div>
                </div>

                <!-- QuickBooks Report CSV import -->
                <div class="iif-section">
                    <h3>&#9635; Import from Report CSV</h3>
                    <p style="font-size:11px; color:var(--text-secondary); margin-bottom:12px;">
                        QuickBooks Desktop can't export transactions to IIF — export a detail
                        <strong>report</strong> to CSV instead and upload it here. The report type is
                        detected automatically: <strong>Transaction Detail</strong> filtered to Sales
                        Receipt (each imports as a paid sale + payment), <strong>Deposit
                        Detail</strong>, or <strong>Check Detail</strong> (both import as journal
                        entries on your bank account). Keep the report's default columns.
                        Safe to re-upload — duplicates are skipped.
                    </p>
                    <input type="file" id="qbcsv-file-input" accept=".csv" style="font-size:11px; margin-bottom:8px;">
                    <div>
                        <button class="btn btn-primary" onclick="IIFPage.importQbReportCsv()">Import Report CSV</button>
                    </div>
                    <div id="qbcsv-import-result" style="margin-top:12px;"></div>
                </div>
            </div>`;
    },

    async importQbReportCsv() {
        const input = $('#qbcsv-file-input');
        if (!input?.files[0]) { toast('Choose a CSV file first', 'error'); return; }
        const formData = new FormData();
        formData.append('file', input.files[0]);
        try {
            App.setStatus('Importing QuickBooks report CSV...');
            const res = await fetch('/api/csv/import/qb-report', { method: 'POST', body: formData });
            const result = await res.json();
            if (!res.ok) throw new Error(result.detail || 'Import failed');

            const kindLabel = { sales_receipts: 'Sales Receipts', deposits: 'Deposits', checks: 'Checks' };
            let html = '<div class="iif-results"><h4>Results</h4>';
            if (result.detected) {
                html += `<div class="result-row"><span>Detected report</span><span class="result-count">${kindLabel[result.detected] || result.detected}</span></div>`;
            }
            for (const key of ['sales_receipts', 'deposits', 'checks']) {
                if (result[key] > 0) {
                    html += `<div class="result-row"><span>${kindLabel[key]}</span><span class="result-count">${result[key]} imported</span></div>`;
                }
            }
            if (result.duplicates_skipped > 0) {
                html += `<div class="result-row"><span>Duplicates skipped</span><span class="result-count">${result.duplicates_skipped}</span></div>`;
            }
            html += '</div>';
            if (result.warnings?.length) {
                html += '<div class="iif-errors" style="border-color:var(--warning,#cc9933);">';
                result.warnings.forEach(w => { html += `${escapeHtml(w)}<br>`; });
                html += '</div>';
            }
            if (result.errors?.length) {
                html += '<div class="iif-errors">';
                result.errors.forEach(e => { html += `${escapeHtml(typeof e === 'string' ? e : JSON.stringify(e))}<br>`; });
                html += '</div>';
            }
            $('#qbcsv-import-result').innerHTML = html;
            const total = (result.sales_receipts || 0) + (result.deposits || 0) + (result.checks || 0);
            toast(`Imported ${total} record${total === 1 ? '' : 's'}`);
            App.setStatus('QuickBooks Interop — Import complete');
        } catch (err) {
            toast(err.message, 'error');
            App.setStatus('Import failed');
        }
    },

    // ==== Export Functions ====

    exportAll() {
        IIFPage._download('/api/iif/export/all', 'slowbooks_export.iif');
    },

    exportSection(section) {
        let url = `/api/iif/export/${section}`;
        // Add date range for invoices/payments
        if (['invoices', 'payments', 'sales-receipts', 'bills', 'deposits'].includes(section)) {
            const from = $('#iif-date-from')?.value;
            const to = $('#iif-date-to')?.value;
            const params = [];
            if (from) params.push(`date_from=${from}`);
            if (to) params.push(`date_to=${to}`);
            if (params.length) url += '?' + params.join('&');
        }
        IIFPage._download(url, `${section}.iif`);
    },

    async _download(url, fallbackName) {
        try {
            App.setStatus('Exporting IIF...');
            const res = await fetch(url);
            if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: res.statusText }));
                throw new Error(err.detail || 'Export failed');
            }

            // Get filename from Content-Disposition header if available
            const disposition = res.headers.get('Content-Disposition');
            let filename = fallbackName;
            if (disposition) {
                const match = disposition.match(/filename="?([^"]+)"?/);
                if (match) filename = match[1];
            }

            const blob = await res.blob();
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = filename;
            a.click();
            URL.revokeObjectURL(a.href);

            toast(`Exported ${filename}`);
            App.setStatus('QuickBooks Interop — Ready');
        } catch (err) {
            toast(err.message, 'error');
            App.setStatus('Export failed');
        }
    },

    // ==== Import Functions ====

    handleDragOver(e) {
        e.preventDefault();
        e.stopPropagation();
        $('#iif-dropzone').classList.add('dragover');
    },

    handleDragLeave(e) {
        e.preventDefault();
        e.stopPropagation();
        $('#iif-dropzone').classList.remove('dragover');
    },

    handleDrop(e) {
        e.preventDefault();
        e.stopPropagation();
        $('#iif-dropzone').classList.remove('dragover');

        const files = e.dataTransfer.files;
        if (files.length > 0) {
            const file = files[0];
            if (file.name.toLowerCase().endsWith('.iif')) {
                IIFPage._setFile(file);
            } else {
                toast('Please select an .iif file', 'error');
            }
        }
    },

    handleFileSelect(e) {
        const file = e.target.files[0];
        if (file) IIFPage._setFile(file);
    },

    _setFile(file) {
        IIFPage._selectedFile = file;
        IIFPage._validated = false;

        const size = file.size < 1024 ? `${file.size} B` :
                     file.size < 1048576 ? `${(file.size / 1024).toFixed(1)} KB` :
                     `${(file.size / 1048576).toFixed(1)} MB`;

        const info = $('#iif-file-info');
        info.style.display = '';
        info.innerHTML = `<div class="iif-file-info">
            <span class="filename">&#9783; ${escapeHtml(file.name)}</span>
            <span class="filesize">${size}</span>
        </div>`;

        $('#iif-import-actions').style.display = '';
        $('#iif-import-btn').disabled = true;
        $('#iif-validation-result').innerHTML = '';
        $('#iif-import-result').innerHTML = '';
    },

    clearFile() {
        IIFPage._selectedFile = null;
        IIFPage._validated = false;
        $('#iif-file-input').value = '';
        $('#iif-file-info').style.display = 'none';
        $('#iif-import-actions').style.display = 'none';
        $('#iif-validation-result').innerHTML = '';
        $('#iif-import-result').innerHTML = '';
    },

    async validateFile() {
        if (!IIFPage._selectedFile) return;

        const formData = new FormData();
        formData.append('file', IIFPage._selectedFile);

        try {
            App.setStatus('Validating IIF file...');
            const res = await fetch('/api/iif/validate', { method: 'POST', body: formData });
            if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: res.statusText }));
                throw new Error(err.detail || 'Validation failed');
            }

            const report = await res.json();
            IIFPage._showValidationReport(report);

            if (report.valid) {
                IIFPage._validated = true;
                $('#iif-import-btn').disabled = false;
                toast('Validation passed');
            } else {
                toast('Validation found errors', 'error');
            }
            App.setStatus('QuickBooks Interop — Ready');
        } catch (err) {
            toast(err.message, 'error');
            App.setStatus('Validation failed');
        }
    },

    _showValidationReport(report) {
        let html = '<div class="iif-results"><h4>Validation Report</h4>';

        html += `<div class="result-row">
            <span>Status</span>
            <span class="${report.valid ? 'iif-validation-ok' : 'iif-validation-err'}">
                ${report.valid ? 'PASS' : 'FAIL'}
            </span>
        </div>`;

        if (report.sections_found.length) {
            html += `<div class="result-row">
                <span>Sections Found</span>
                <span>${report.sections_found.join(', ')}</span>
            </div>`;
        }

        for (const [section, count] of Object.entries(report.record_counts || {})) {
            html += `<div class="result-row">
                <span>${section}</span>
                <span class="result-count">${count} records</span>
            </div>`;
        }

        html += '</div>';

        if (report.errors && report.errors.length) {
            html += '<div class="iif-errors">';
            report.errors.forEach(e => { html += `${escapeHtml(e)}<br>`; });
            html += '</div>';
        }

        if (report.warnings && report.warnings.length) {
            html += '<div class="iif-warnings">';
            report.warnings.forEach(w => { html += `${escapeHtml(w)}<br>`; });
            html += '</div>';
        }

        $('#iif-validation-result').innerHTML = html;
    },

    async importFile() {
        if (!IIFPage._selectedFile) return;

        if (!IIFPage._validated) {
            toast('Please validate the file first', 'error');
            return;
        }

        const formData = new FormData();
        formData.append('file', IIFPage._selectedFile);

        try {
            App.setStatus('Importing IIF file...');
            const res = await fetch('/api/iif/import', { method: 'POST', body: formData });
            if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: res.statusText }));
                throw new Error(err.detail || 'Import failed');
            }

            const result = await res.json();
            IIFPage._showImportResult(result);

            const total = (result.classes || 0) + (result.accounts || 0) + (result.customers || 0) +
                          (result.vendors || 0) + (result.items || 0) +
                          (result.invoices || 0) + (result.payments || 0) +
                          (result.sales_receipts || 0) +
                          (result.estimates || 0) + (result.bills || 0) +
                          (result.deposits || 0);
            toast(`Imported ${total} records`);
            App.setStatus('QuickBooks Interop — Import complete');
        } catch (err) {
            toast(err.message, 'error');
            App.setStatus('Import failed');
        }
    },

    _showImportResult(result) {
        const sections = [
            ['Classes', result.classes],
            ['Accounts', result.accounts],
            ['Customers', result.customers],
            ['Vendors', result.vendors],
            ['Items', result.items],
            ['Invoices', result.invoices],
            ['Payments', result.payments],
            ['Sales Receipts', result.sales_receipts],
            ['Estimates', result.estimates],
            ['Bills', result.bills],
            ['Deposits', result.deposits],
            ['Duplicates skipped', result.duplicates_skipped],
        ];

        let html = '<div class="iif-results"><h4>Import Results</h4>';
        for (const [name, count] of sections) {
            if (count > 0) {
                html += `<div class="result-row">
                    <span>${name}</span>
                    <span class="result-count">${count} imported</span>
                </div>`;
            }
        }
        html += '</div>';

        if (result.warnings && result.warnings.length) {
            html += '<div class="iif-warnings">';
            html += `<strong>Warnings (${result.warnings.length}):</strong><br>`;
            result.warnings.forEach(w => {
                html += `${escapeHtml(w)}<br>`;
            });
            html += '</div>';
        }

        if (result.errors && result.errors.length) {
            html += '<div class="iif-errors">';
            result.errors.forEach(e => {
                const msg = typeof e === 'string' ? e : `Row ${e.row}: ${e.message}`;
                html += `${escapeHtml(msg)}<br>`;
            });
            html += '</div>';
        }

        $('#iif-import-result').innerHTML = html;
    },
};
