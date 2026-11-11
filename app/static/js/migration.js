/**
 * Migrate Data — one page for every supported source system.
 * Pick a source, upload its export bundle, dry-run, then import; the
 * Import button only unlocks after a passing dry-run.
 */
const MigrationPage = {
    _dryRunOk: false,
    _preselect: null,
    _dryRunData: null,   // the passing dry run's counts: what Import will write
    _importing: false,   // an import is running; a second click does nothing

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
                    unapplied payments over, and its <strong>Account List</strong> report
                    (Reports → Account List → Export to Excel) to give every account its
                    number, type and description.
                </p>
                <input type="file" id="migration-files" aria-label="Files to import" multiple accept=".csv,.txt,.xlsx,.zip" onchange="MigrationPage.reset()">
                <div class="form-actions" style="margin-top:12px;" data-write>
                    <button class="btn btn-primary" onclick="MigrationPage.dryRun()">Dry Run</button>
                    <button class="btn btn-danger" id="migration-import-btn" disabled onclick="MigrationPage.doImport()">Import</button>
                </div>
                <div id="migration-choices" style="margin-top:12px; font-size:12px;"></div>
                <div id="migration-result" style="margin-top:12px; font-size:12px;"></div>
            </div>`;
    },

    reset() {
        if (MigrationPage._importing) return;
        MigrationPage._dryRunOk = false;
        MigrationPage._dryRunData = null;
        const btn = $('#migration-import-btn');
        if (btn) btn.disabled = true;
        const out = $('#migration-result');
        if (out) out.innerHTML = '';
        const choices = $('#migration-choices');
        if (choices) choices.innerHTML = '';
    },

    _formData() {
        const files = $('#migration-files').files;
        if (!files.length) { toast('Choose the export files first', 'error'); return null; }
        const fd = new FormData();
        for (const f of files) fd.append('files', f);
        // the person's choice on the Employee Contact List (NEW-222); a
        // disabled tick sends nothing, and the server says why it is left out
        const tick = $('#migration-include-employees');
        if (tick && !tick.disabled) fd.append('include_employees', tick.checked ? 'true' : 'false');
        return fd;
    },

    // Adding employees is a person's choice, never the software's (NEW-222):
    // when the dry run found the Employee Contact List, a tick says whether
    // it comes in, ticked for a caller who may add employees and disabled,
    // with the reason beside it, for one who may not. Changing it runs the
    // dry run again, so its counts are what Import will write.
    _employeeChoice(list) {
        const box = $('#migration-choices');
        if (!box) return;
        if (!list) { box.innerHTML = ''; return; }
        const why = list.may_add ? '' : `<div id="migration-include-employees-why" style="color:var(--text-muted); margin-left:22px;">Adding employees needs an administrator, or a token that may also run payroll, so the list is left out.</div>`;
        box.innerHTML = `<label style="display:flex; align-items:center; gap:6px;">
                <input type="checkbox" id="migration-include-employees" data-write
                    ${list.included ? 'checked' : ''} ${list.may_add ? '' : 'disabled aria-describedby="migration-include-employees-why"'}
                    onchange="MigrationPage.dryRun()">
                Bring in the people on the Employee Contact List (${MigrationPage._n(list.employees, 'employee')})
            </label>${why}`;
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
            MigrationPage._dryRunData = data.ok ? data : null;
            $('#migration-import-btn').disabled = !data.ok;
            MigrationPage._employeeChoice(data.employee_list);
            MigrationPage._renderResult(data, false);
        } catch (err) {
            // a refusal (a file it cannot place, another source's export)
            // reads on the page where the result goes, not only as a toast
            MigrationPage._dryRunOk = false;
            MigrationPage._dryRunData = null;
            $('#migration-import-btn').disabled = true;
            const out = $('#migration-result');
            if (out) {
                out.innerHTML = `<strong style="color:var(--danger);">Dry run failed:</strong> ${escapeHtml(err.message)}`;
            }
            toast(err.message, 'error');
        }
    },

    // The line Import shows while it runs (#706): what is being written,
    // from the passing dry run's counts, and how long it has been going.
    // The import is one request and the server reports nothing until it is
    // done, so the line counts the seconds rather than the journals.
    _progressText(seconds) {
        const data = MigrationPage._dryRunData;
        const what = data ? MigrationPage._list(MigrationPage._counts(data, false)) : 'your books';
        return `Importing ${what}… ${seconds} s so far. A large file takes a minute or so; `
            + 'keep this page open until the result shows here.';
    },

    async doImport() {
        if (MigrationPage._importing) return;
        if (!MigrationPage._dryRunOk) { toast('Run a passing dry run first', 'error'); return; }
        const fd = MigrationPage._formData();
        if (!fd) return;
        const source = $('#migration-source').value;
        const btn = $('#migration-import-btn');
        const out = $('#migration-result');
        const started = Date.now();
        const status = () => {
            const line = document.getElementById('migration-progress');
            if (line) line.textContent = MigrationPage._progressText(Math.floor((Date.now() - started) / 1000));
        };
        MigrationPage._importing = true;
        if (btn) { btn.disabled = true; btn.textContent = 'Importing…'; btn.setAttribute('aria-busy', 'true'); }
        if (out) {
            out.innerHTML = `<div id="migration-progress" role="status" aria-live="polite">${escapeHtml(MigrationPage._progressText(0))}</div>`;
        }
        const timer = setInterval(status, 1000);
        try {
            const resp = await fetch(`/api/migration/${source}/import`, { method: 'POST', body: fd });
            if (!resp.ok) throw new Error(await API.responseError(resp, 'Import failed'));
            const data = await resp.json();
            // the result takes the dry run's place; no toast on top of it
            MigrationPage._renderResult(data, true);
            if (data.ok) { MigrationPage._dryRunOk = false; MigrationPage._dryRunData = null; }
        } catch (err) {
            if (out) {
                out.innerHTML = `<strong style="color:var(--danger);">Import failed:</strong> ${escapeHtml(err.message)}`;
            }
            toast(err.message, 'error');
        } finally {
            clearInterval(timer);
            MigrationPage._importing = false;
            if (btn) {
                btn.textContent = 'Import';
                btn.setAttribute('aria-busy', 'false');
                btn.disabled = !MigrationPage._dryRunOk;
            }
        }
    },
};
