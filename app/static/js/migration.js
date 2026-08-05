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
                    <select id="migration-source" onchange="MigrationPage.reset()">${opts}</select>
                </div>
                <p style="font-size:12px; margin:8px 0;">
                    Export and upload together: the <strong>chart of accounts</strong>
                    and <strong>general ledger / journals / transactions</strong>
                    (required), plus a <strong>trial balance</strong> when your system
                    offers one (recommended — enables balance verification). Files are
                    recognized by name; CSV and tab-separated exports both work.
                </p>
                <input type="file" id="migration-files" multiple accept=".csv,.txt" onchange="MigrationPage.reset()">
                <div class="form-actions" style="margin-top:12px;">
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

    _renderResult(data, imported) {
        const errs = data.errors.map(e => `<li style="color:var(--danger);">${escapeHtml(e)}</li>`).join('');
        const warns = data.warnings.map(w => `<li style="color:var(--warning, #a8761f);">${escapeHtml(w)}</li>`).join('');
        const head = imported
            ? (data.ok ? `<strong>Imported ${data.imported_accounts} accounts and ${data.imported_journals} journals.</strong>`
                       : '<strong style="color:var(--danger);">Import refused — fix the dry-run errors below.</strong>')
            : (data.ok ? `<strong>Dry run passed:</strong> ${data.accounts} accounts, ${data.journals} journals ready to import.`
                       : '<strong style="color:var(--danger);">Dry run failed:</strong>');
        $('#migration-result').innerHTML = `${head}<ul style="margin-top:6px;">${errs}${warns}</ul>`;
    },

    async dryRun() {
        const fd = MigrationPage._formData();
        if (!fd) return;
        const source = $('#migration-source').value;
        try {
            const resp = await fetch(`/api/migration/${source}/dry-run`, { method: 'POST', body: fd });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Dry run failed');
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
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Import failed');
            MigrationPage._renderResult(data, true);
            if (data.ok) toast(`Imported ${data.imported_accounts} accounts, ${data.imported_journals} journals`);
        } catch (err) { toast(err.message, 'error'); }
    },
};
