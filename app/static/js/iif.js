/**
 * IIF Import/Export — QuickBooks Desktop Interoperability
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
                <h2>Interoperability</h2>
                <div style="font-size:10px; color:var(--text-muted);">
                    IIF Import/Export &mdash; Works with QuickBooks Desktop
                </div>
            </div>

            <div class="iif-sections">
                <!-- Export Section -->
                <div class="iif-section">
                    <h3>&#9660; Export to IIF</h3>
                    <p style="font-size:11px; color:var(--text-secondary); margin-bottom:12px;">
                        Download Slowbooks data as .iif files for import into QuickBooks Desktop
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
                <div class="iif-section" data-write>
                    <h3>&#9650; Import from IIF</h3>
                    <p style="font-size:11px; color:var(--text-secondary); margin-bottom:12px;">
                        Upload .iif files exported from QuickBooks Desktop
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
                <div class="iif-section" data-write>
                    <h3>&#9635; Import from Report CSV</h3>
                    <p style="font-size:11px; color:var(--text-secondary); margin-bottom:12px;">
                        QuickBooks Desktop can't export transactions to IIF — export a detail
                        <strong>report</strong> to CSV instead and upload it here. The report type is
                        detected automatically: <strong>Transaction Detail</strong> filtered to Sales
                        Receipt or Invoice (a receipt imports as a paid sale + payment; an invoice with
                        its lines, its Customer:Job and its number, open, partly paid or paid by the
                        report's <strong>Open Balance</strong> column — add that column, or every
                        invoice comes in open; upload again with it and the open ones here are marked
                        paid), <strong>Deposit Detail</strong>, or <strong>Check Detail</strong> (both
                        import as journal entries on your bank account), or <strong>Transaction Detail
                        by Account</strong> with the dates set to All (every transaction of every type
                        as a journal entry; import the lists IIF first); or, for timesheets, <strong>Time by Name by Job detail</strong> or <strong>Time by Job by Name detail</strong> (Reports > Jobs, Time &amp; Mileage), saved as CSV or Excel: every entry becomes an approved time entry under its employee, marked as QuickBooks' so no pay run picks it up. Keep the report's default
                        columns. Safe to re-upload — what is already here is skipped.
                    </p>
                    <input type="file" id="qbcsv-file-input" aria-label="QuickBooks report file (CSV or Excel)" accept=".csv,.xlsx" style="font-size:11px; margin-bottom:8px;">
                    <div>
                        <button class="btn btn-primary" onclick="IIFPage.importQbReportCsv()">Import Report CSV</button>
                    </div>
                    <div id="qbcsv-import-result" style="margin-top:12px;"></div>
                </div>
            </div>`;
    },

    // What an import says when it's done (#197). One that came back with
    // errors said "Imported 0 records" in green and "Import complete", and the
    // red box below was the only sign of them.
    _reportImport(total, errors, unsupported = 0, words = null) {
        const records = words || `Imported ${total} record${total === 1 ? '' : 's'}`;
        const left = unsupported > 0 ? `, ${unsupported} not imported (type not supported)` : '';
        if (errors > 0) {
            toast(`${records}${left}, ${errors} error${errors === 1 ? '' : 's'}: see the list below`, 'error');
            App.setStatus('Interoperability — Import finished with errors');
        } else {
            toast(`${records}${left}`);
            App.setStatus('Interoperability — Import complete');
        }
    },

    async importQbReportCsv() {
        const input = $('#qbcsv-file-input');
        if (!input?.files[0]) { toast('Choose a file first', 'error'); return; }
        const formData = new FormData();
        formData.append('file', input.files[0]);
        try {
            App.setStatus('Importing QuickBooks report CSV...');
            const res = await fetch('/api/csv/import/qb-report', { method: 'POST', body: formData });
            if (!res.ok) throw new Error(await API.responseError(res, 'Import failed'));
            const result = await res.json();

            const kindLabel = { sales_receipts: 'Sales Receipts', invoices: 'Invoices', sales_and_invoices: 'Sales Receipts and Invoices', deposits: 'Deposits', checks: 'Checks', ledger: 'Transaction Detail by Account', journals: 'Journal entries', time: 'Time by Name by Job detail', time_entries: 'Time entries' };
            let html = '<div class="iif-results"><h4>Results</h4>';
            if (result.detected) {
                html += `<div class="result-row"><span>Detected report</span><span class="result-count">${escapeHtml(result.report || kindLabel[result.detected] || result.detected)}</span></div>`;
            }
            for (const key of ['sales_receipts', 'invoices', 'deposits', 'checks', 'journals', 'time_entries']) {
                if (result[key] > 0) {
                    html += `<div class="result-row"><span>${kindLabel[key]}</span><span class="result-count">${result[key]} imported</span></div>`;
                }
            }
            if (result.invoices > 0) {
                html += `<div class="result-row"><span style="font-size:11px;color:var(--gray-500)">${result.invoices_open || 0} open, ${result.invoices_partly_paid || 0} partly paid, ${result.invoices_paid || 0} paid</span></div>`;
            }
            if (result.invoices_updated > 0) {
                html += `<div class="result-row"><span>Already here, marked paid or partly paid from Open Balance</span><span class="result-count">${result.invoices_updated}</span></div>`;
            }
            if (result.duplicates_skipped > 0) {
                html += `<div class="result-row"><span>Duplicates skipped</span><span class="result-count">${result.duplicates_skipped}</span></div>`;
            }
            html += '</div>';
            if (result.warnings?.length) {
                // a warning sits in the warnings box, as the IIF import's do
                // (it sat in the error box with an orange border)
                html += '<div class="iif-warnings">';
                result.warnings.forEach(w => { html += `${escapeHtml(w)}<br>`; });
                html += '</div>';
            }
            if (result.errors?.length) {
                html += '<div class="iif-errors">';
                result.errors.forEach(e => { html += `${escapeHtml(typeof e === 'string' ? e : JSON.stringify(e))}<br>`; });
                html += '</div>';
            }
            $('#qbcsv-import-result').innerHTML = html;
            const total = (result.sales_receipts || 0) + (result.invoices || 0) + (result.deposits || 0) + (result.checks || 0) + (result.journals || 0) + (result.time_entries || 0);
            IIFPage._reportImport(total, (result.errors || []).length);
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
            // Asked for inline: the desktop app's web view takes an
            // "attachment" answer to a page's own fetch() for a download and
            // never hands it back ("Failed to fetch"). This page saves the
            // file itself either way.
            const res = await fetch(url, { headers: { 'X-Slowbooks-Desktop': '1' } });
            if (!res.ok) throw new Error(await API.responseError(res, 'Export failed'));

            // Get filename from Content-Disposition header if available
            const disposition = res.headers.get('Content-Disposition');
            let filename = fallbackName;
            if (disposition) {
                const match = disposition.match(/filename="?([^"]+)"?/);
                if (match) filename = match[1];
            }

            const blob = await res.blob();
            // The desktop app saves it to Documents/SlowBooks Pro/Reports and
            // says where, as Save CSV does; a browser downloads it.
            const desktop = window.SlowbooksDesktop;
            if (!(desktop && await desktop.saveFile(blob, filename))) {
                const a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = filename;
                a.click();
                setTimeout(() => URL.revokeObjectURL(a.href), 30000);
                toast(`Exported ${filename}`);
            }
            App.setStatus('Interoperability — Ready');
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

        const size = formatFileSize(file.size);

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
            if (!res.ok) throw new Error(await API.responseError(res, 'Validation failed'));

            const report = await res.json();
            IIFPage._showValidationReport(report);

            if (report.valid) {
                IIFPage._validated = true;
                $('#iif-import-btn').disabled = false;
                toast('Validation passed');
            } else {
                toast('Validation found errors', 'error');
            }
            App.setStatus('Interoperability — Ready');
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

        // The blocks the import would not take, by type, before the person
        // decides (#288); the warning below names each block
        for (const [ttype, count] of Object.entries(report.unsupported_counts || {})) {
            html += `<div class="result-row">
                <span>${escapeHtml(ttype)} &mdash; not imported</span>
                <span class="result-count">${count} block${count === 1 ? '' : 's'}</span>
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

        // ALL-CAPS names (#195): a box, off unless ticked, and a few of this
        // file's own names as the box would import them, so the choice is
        // made on the names that will change.
        if (report.valid && report.caps_names > 0) {
            const n = report.caps_names;
            const examples = (report.caps_name_examples || []).map(x =>
                `<li>${escapeHtml(x.name)} &rarr; ${escapeHtml(x.becomes)}</li>`).join('');
            html += `<div class="iif-retitle">
                <label><input type="checkbox" id="iif-retitle-names">
                    Change ${n} ALL-CAPS name${n === 1 ? '' : 's'} to normal capitalization</label>
                <ul>${examples}</ul>
                <p>Customer, vendor and account names; item names are kept as typed, and
                    names already in your books are not changed. A name that should stay
                    in capitals may not, so look over the examples first.</p>
            </div>`;
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
        formData.append('retitle_names', $('#iif-retitle-names')?.checked ? 'true' : 'false');

        try {
            App.setStatus('Importing IIF file...');
            const res = await fetch('/api/iif/import', { method: 'POST', body: formData });
            if (!res.ok) throw new Error(await API.responseError(res, 'Import failed'));

            const result = await res.json();
            IIFPage._showImportResult(result);

            const total = (result.classes || 0) + (result.accounts || 0) + (result.customers || 0) +
                          (result.vendors || 0) + (result.items || 0) +
                          (result.invoices || 0) + (result.payments || 0) +
                          (result.sales_receipts || 0) +
                          (result.estimates || 0) + (result.bills || 0) +
                          (result.deposits || 0);
            IIFPage._reportImport(total, (result.errors || []).length, result.unsupported || 0,
                IIFPage._importedWords(result));
        } catch (err) {
            toast(err.message, 'error');
            App.setStatus('Import failed');
        }
    },

    // Transactions the import took, every type together.
    _transactionsImported(result) {
        return (result.invoices || 0) + (result.payments || 0) + (result.sales_receipts || 0)
            + (result.estimates || 0) + (result.bills || 0) + (result.deposits || 0);
    },

    // What the IIF import brought in, in the panel's own terms (2.23.1 gate,
    // NEW-106: "Imported 4 records" was a class and three transactions added
    // up, a total the panel never shows): "Imported 1 class and 3
    // transactions"; a file of records already here, "Nothing imported, 3
    // already here".
    _importedWords(result) {
        const parts = [];
        const say = (n, one, many) => { if (n > 0) parts.push(`${n} ${n === 1 ? one : many}`); };
        say(result.classes || 0, 'class', 'classes');
        say(result.accounts || 0, 'account', 'accounts');
        say(result.customers || 0, 'customer', 'customers');
        say(result.vendors || 0, 'vendor', 'vendors');
        say(result.items || 0, 'item', 'items');
        say(IIFPage._transactionsImported(result), 'transaction', 'transactions');
        if (!parts.length) {
            const here = result.duplicates_skipped || 0;
            return here > 0 ? `Nothing imported, ${here} already here` : 'Nothing imported';
        }
        const last = parts.pop();
        return `Imported ${parts.length ? `${parts.join(', ')} and ` : ''}${last}`;
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
        // Not "imported": a record already here that the file named again
        // (both QA agents, 2.18.1 gate: "Duplicates skipped: 1 imported")
        if (result.duplicates_skipped > 0) {
            html += `<div class="result-row">
                <span>Already here, skipped</span>
                <span class="result-count">${result.duplicates_skipped}</span>
            </div>`;
        }
        // Blocks of a type the importer doesn't take (#288): counted and
        // named, never dropped without a word; the warnings say which types
        if (result.unsupported > 0) {
            const named = (result.unsupported_blocks || []).slice(0, 8).map(u =>
                `block ${u.block}: ${escapeHtml(u.type)}${u.docnum ? ' ' + escapeHtml(u.docnum) : ''}${u.date ? ', ' + escapeHtml(u.date) : ''}${u.name ? ', ' + escapeHtml(u.name) : ''}`).join('; ');
            html += `<div class="result-row">
                <span>Not imported (type not supported)</span>
                <span class="result-count">${result.unsupported}</span>
            </div>
            <div class="result-row"><span style="font-size:11px;color:var(--gray-500)">${named}${(result.unsupported_blocks || []).length > 8 ? '; …' : ''}</span></div>`;
        }
        // Every transaction block in the file accounted for, in one sentence
        // on a line of its own: as a label beside a count across the row it
        // wrapped after "the", the count's "3:" reading as a superscript
        // (2.23.1 gate, NEW-106)
        if (result.transaction_blocks > 0) {
            const n = result.transaction_blocks;
            const imported = IIFPage._transactionsImported(result);
            html += `<div class="result-row result-row--line">${n} transaction block${n === 1 ? '' : 's'} in the file: ${imported} imported, ${result.duplicates_skipped || 0} already here, ${result.unsupported || 0} not imported, ${(result.errors || []).length} failed</div>`;
        }
        if (result.names_changed > 0) {
            html += `<div class="result-row">
                <span>ALL-CAPS names changed to normal capitalization</span>
                <span class="result-count">${result.names_changed}</span>
            </div>`;
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
