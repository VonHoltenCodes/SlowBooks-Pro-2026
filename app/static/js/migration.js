/**
 * Migrate Data — one page for every supported source system.
 * Pick a source, upload its export bundle, dry-run, then import; the
 * Import button only unlocks after a passing dry-run.
 */
const MigrationPage = {
    _dryRunOk: false,
    _preselect: null,

    async render(preselect) {
        MigrationPage._dryRunOk = false;
        if (preselect) MigrationPage._preselect = preselect;
        let sources = [];
        try { sources = await API.get('/migration/sources'); } catch (e) { /* renders empty */ }
        const selected = MigrationPage._preselect || (sources[0] && sources[0].key);
        const opts = sources.map(s =>
            `<option value="${s.key}" ${s.key === selected ? 'selected' : ''}>${escapeHtml(s.label)}</option>`
        ).join('');
        return `
            <div class="page-header">
                <h2>Migrate Data</h2>
                <div style="font-size:10px; color:var(--text-muted);">
                    Bring your accounting history in from another system
                </div>
            </div>
            <div class="card" style="max-width:640px;">
                <div class="form-group" style="max-width:260px;">
                    <label>Coming from</label>
                    <select id="migration-source" data-write onchange="MigrationPage.reset()">${opts}</select>
                </div>
                <p style="font-size:12px; margin:8px 0;">
                    Export and upload together: the <strong>chart of accounts</strong>
                    and <strong>general ledger / journals / transactions</strong>
                    (required), plus a <strong>trial balance</strong> when your system
                    offers one (recommended — enables balance verification). Files are
                    recognized by name; CSV, tab-separated and Excel exports all work,
                    and a <strong>zip</strong> is read as the files inside it. From
                    QuickBooks Online, upload the Export data zip as downloaded, with
                    its <strong>A/R Aging Detail</strong> report beside it (Reports →
                    A/R Aging Detail → Export to Excel) to carry the open invoices and
                    unapplied payments over.
                </p>
                <input type="file" id="migration-files" aria-label="Files to import" multiple accept=".csv,.txt,.xlsx,.zip" onchange="MigrationPage.reset()">
                <div class="form-actions" style="margin-top:12px;" data-write>
                    <button class="btn btn-primary" onclick="MigrationPage.dryRun()">Dry Run</button>
                    <button class="btn btn-danger" id="migration-import-btn" disabled onclick="MigrationPage.doImport()">Import</button>
                </div>
                <div id="migration-result" style="margin-top:12px; font-size:12px;"></div>
            </div>`;
    },

    reset() {
        MigrationPage._dryRunOk = false;
        const btn = $('#migration-import-btn');
        if (btn) btn.disabled = true;
        const out = $('#migration-result');
        if (out) out.innerHTML = '';
    },

    _formData() {
        const files = $('#migration-files').files;
        if (!files.length) { toast('Choose the export files first', 'error'); return null; }
        const fd = new FormData();
        for (const f of files) fd.append('files', f);
        return fd;
    },

    // "13 journals", "1 journal": a count with its word.
    _n(count, word) { return `${count} ${word}${count === 1 ? '' : 's'}`; },

    // What the result counts: accounts and journals always, customers,
    // vendors and employees when the source brings lists (the QuickBooks Online export).
    // A dry run counts what an import would make: the chart's accounts not
    // in the books yet, and the journals not already imported.
    _counts(data, imported) {
        const n = MigrationPage._n;
        const accounts = imported ? data.imported_accounts
            : (data.new_accounts ?? data.accounts);
        const journals = imported ? data.imported_journals
            : data.journals - (data.duplicate_journals || 0);
        const parts = [n(accounts, 'account'), n(journals, 'journal')];
        const customers = imported ? data.imported_customers : data.customers;
        const vendors = imported ? data.imported_vendors : data.vendors;
        if (customers != null) parts.push(n(customers, 'customer'));
        if (vendors != null) parts.push(n(vendors, 'vendor'));
        const employees = imported ? data.imported_employees : data.employees;
        if (employees != null) parts.push(n(employees, 'employee'));
        // QuickBooks Online's A/R Aging Detail report, when it came with the export
        const open = imported ? data.imported_open_invoices : data.open_invoices;
        const unapplied = imported ? data.imported_unapplied_payments : data.unapplied_payments;
        if (open != null) parts.push(n(open, 'open invoice'));
        if (unapplied != null) parts.push(n(unapplied, 'unapplied payment'));
        return parts;
    },

    // "13 accounts, 13 journals, 2 customers and 4 vendors"
    _list(parts) {
        return parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}` : parts[0];
    },

    _renderResult(data, imported) {
        const errs = data.errors.map(e => `<li style="color:var(--danger);">${escapeHtml(e)}</li>`).join('');
        const warns = data.warnings.map(w => `<li style="color:var(--text-warning);">${escapeHtml(w)}</li>`).join('');
        const parts = MigrationPage._counts(data, imported);
        const head = imported
            ? (data.ok ? `<strong>Imported ${MigrationPage._list(parts)}.</strong>`
                       : '<strong style="color:var(--danger);">Import refused — fix the dry-run errors below.</strong>')
            : (data.ok ? `<strong>Dry run passed:</strong> ${parts.join(', ')} ready to import.`
                       : '<strong style="color:var(--danger);">Dry run failed:</strong>');
        $('#migration-result').innerHTML = `${head}<ul style="margin-top:6px;">${errs}${warns}</ul>`;
    },

    async dryRun() {
        const fd = MigrationPage._formData();
        if (!fd) return;
        const source = $('#migration-source').value;
        try {
            const resp = await fetch(`/api/migration/${source}/dry-run`, { method: 'POST', body: fd });
            if (!resp.ok) throw new Error(await API.responseError(resp, 'Dry run failed'));
            const data = await resp.json();
            MigrationPage._dryRunOk = data.ok;
            $('#migration-import-btn').disabled = !data.ok;
            MigrationPage._renderResult(data, false);
        } catch (err) { toast(err.message, 'error'); }
    },

    async doImport() {
        if (!MigrationPage._dryRunOk) { toast('Run a passing dry run first', 'error'); return; }
        const fd = MigrationPage._formData();
        if (!fd) return;
        const source = $('#migration-source').value;
        try {
            const resp = await fetch(`/api/migration/${source}/import`, { method: 'POST', body: fd });
            if (!resp.ok) throw new Error(await API.responseError(resp, 'Import failed'));
            const data = await resp.json();
            MigrationPage._renderResult(data, true);
            if (data.ok) toast(`Imported ${MigrationPage._list(MigrationPage._counts(data, true))}`);
        } catch (err) { toast(err.message, 'error'); }
    },
};
