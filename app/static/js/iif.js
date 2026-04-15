/**
 * IIF Import/Export — QuickBooks 2003 Pro Interoperability
 * Decompiled from QBW32.EXE!CQBIIFEngine  Offset: 0x001B8000
 *
 * Original IIF engine lived in QBIIF32.DLL (shipped separately).
 * Import: File > Utilities > Import > IIF Files
 * Export: File > Utilities > Export > Lists to IIF Files
 *
 * The original DLL was a 342KB mess of fscanf() calls with no error
 * handling. A tab character in a company name would crash it. We do better.
 */
const IIFPage = {
    _selectedFile: null,
    _validated: false,

    async render() {
        const canManageImports = App.hasPermission ? App.hasPermission('import_export.manage') : true;
        return `
            <div class="page-header">
                <h2>QuickBooks Interop</h2>
                <div style="font-size:10px; color:var(--text-muted);">
                    QB2003-compatible legacy IIF for NZ-ledger data
                </div>
            </div>

            <div class="iif-sections">
                <!-- Export Section -->
                <div class="iif-section">
                    <h3>&#9660; Export to IIF</h3>
                    <p style="font-size:11px; color:var(--text-secondary); margin-bottom:12px;">
                        Download NZ-ledger data as .iif files for import into QuickBooks 2003 Pro
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
                        </div>
                    </div>
                </div>

                ${canManageImports ? `<!-- Import Section -->
                <div class="iif-section">
                    <h3>&#9650; Import from IIF</h3>
                    <p style="font-size:11px; color:var(--text-secondary); margin-bottom:12px;">
                        Upload .iif files exported from QuickBooks 2003 Pro or compatible legacy systems
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
                </div>` : ''}
            </div>`;
    },

    // ==== Export Functions ====

    exportAll() {
        IIFPage._download('/api/iif/export/all', 'slowbooks_export.iif');
    },

    exportSection(section) {
        let url = `/api/iif/export/${section}`;
        // Add date range for invoices/payments
        if (section === 'invoices' || section === 'payments') {
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
            await API.download(url.replace('/api', ''), fallbackName);
            toast(`Exported ${fallbackName}`);
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

            const total = (result.accounts || 0) + (result.customers || 0) +
                          (result.vendors || 0) + (result.items || 0) +
                          (result.invoices || 0) + (result.payments || 0) +
                          (result.estimates || 0);
            toast(`Imported ${total} records`);
            App.setStatus('QuickBooks Interop — Import complete');
        } catch (err) {
            toast(err.message, 'error');
            App.setStatus('Import failed');
        }
    },

    _showImportResult(result) {
        const sections = [
            ['Accounts', result.accounts],
            ['Customers', result.customers],
            ['Vendors', result.vendors],
            ['Items', result.items],
            ['Invoices', result.invoices],
            ['Payments', result.payments],
            ['Estimates', result.estimates],
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
