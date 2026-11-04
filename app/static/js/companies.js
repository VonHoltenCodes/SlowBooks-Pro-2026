/**
 * Multi-Company — list and create company files/databases
 * Feature 16: Company management UI
 *
 * On desktop installs each company is its own database file (like a
 * QuickBooks company file). "Switch company…" signs this company out and
 * asks the launcher for its picker (pywebview.api.show_picker); the page
 * cannot switch a browser session, so the button only appears under the
 * desktop shell. On server (PostgreSQL, Docker) installs each company is a
 * separate database, and a server serves the one its DATABASE_URL names:
 * a second company is opened by a second server pointed at its database
 * (docs/operations.md, "Several companies on a server").
 */
const CompaniesPage = {
    _isDesktop() {
        return typeof window.pywebview !== 'undefined' && window.pywebview.api
            && typeof window.pywebview.api.show_picker === 'function';
    },

    async switchCompany() {
        if (!confirm('Sign out of this company and choose another?')) return;
        try {
            await API.post('/auth/logout', {});
        } catch (e) {
            // Signed out already (a 401) is fine; anything else may have left
            // this sign-in open, so the picker waits (skytech R6-1, 2.18.0).
            if (!(e && e.status === 401)) {
                toast(`Couldn't sign out of this company (${e.message}). Try again.`, 'error');
                return;
            }
        }
        window.pywebview.api.show_picker();
    },

    async render() {
        const companies = await API.get('/companies');
        // a Server Edition host serving several company files (#358)
        let served = [];
        try { served = (await API.get('/auth/status')).companies || []; } catch (e) { /* an older server */ }
        const serves = served.length > 1;
        let ac = { is_copy: false, copy: null, sent: [], can_make: false };
        try { ac = { ...ac, ...((await API.get('/accountants-copy/status')) || {}) }; } catch (e) { /* an older server */ }
        if (!Array.isArray(ac.sent)) ac.sent = [];
        CompaniesPage._ac = ac;
        let html = `
            <div class="page-header">
                <h2>Company Files</h2>
                <div>
                    ${CompaniesPage._isDesktop() ? '<button class="btn btn-secondary" onclick="CompaniesPage.switchCompany()">Switch company…</button> ' : ''}
                    ${ac.is_copy ? '<button class="btn btn-primary" data-accountant-ok onclick="CompaniesPage.sendChanges()">Send changes back…</button> ' : ''}
                    ${!ac.is_copy && ac.can_make ? '<button class="btn btn-secondary" data-admin data-write onclick="CompaniesPage.showSendToAccountant()">Send to accountant…</button> ' : ''}
                    ${!ac.is_copy ? '<button class="btn btn-secondary" data-admin data-write onclick="CompaniesPage.showApplyChanges()">Apply accountant\'s changes…</button> ' : ''}
                    ${CompaniesPage._isDesktop() && !ac.is_copy ? '<button class="btn btn-secondary" data-admin data-write onclick="CompaniesPage.showOpenCopy()">Open accountant\'s copy…</button> ' : ''}
                    ${!ac.is_copy ? '<button class="btn btn-secondary" data-admin data-write onclick="CompaniesPage.trySample(this)">Try a sample company</button> ' : ''}
                    <button class="btn btn-primary" data-admin onclick="CompaniesPage.showCreate()">+ New Company</button>
                </div>
            </div>
            ${ac.is_copy ? `<div class="hint" style="margin-bottom:12px;">This is an accountant's copy of <strong>${escapeHtml(ac.copy.company_name)}</strong>, made ${ac.copy.made_at ? escapeHtml(new Date(ac.copy.made_at).toLocaleDateString()) : ''}. It takes journal entries dated on or before <strong>${escapeHtml(ac.copy.dividing_date)}</strong> and nothing else. When the adjustments are in, Send changes back writes the file to return.</div>` : ''}
            ${!ac.is_copy && ac.sent.length ? `<div style="font-size:11px; color:var(--text-muted); margin-bottom:12px;">Copies sent to an accountant: ${ac.sent.map(s => `${escapeHtml(s.dividing_date)} (${s.applied} entr${s.applied === 1 ? 'y' : 'ies'} applied back)`).join(' · ')}</div>` : ''}
            <p style="font-size:11px;color:var(--text-muted);margin-bottom:12px;">
                Each company is stored in its own separate database.
                ${CompaniesPage._isDesktop()
                    ? 'Switch company takes you back to the company picker; this company is signed out.'
                    : serves
                    ? 'This server serves every company in this list. Open one here or choose it on the sign-in screen: each has its own users and roles, and a company this sign-in is not a user of asks for its own password. A new company is set up by the administrator who opens it first.'
                    : 'A server serves one database, the one its DATABASE_URL names. A new company is created as its own database, ready to serve: run a second server pointed at it (on Docker, a second service in docker-compose.yml on another port), or point this one at it and restart. The sign-in page names the company a server serves; it never lists them. See docs/operations.md, "Several companies on a server".'}
            </p>
            <!-- Creating one is the administrator's (POST /api/companies) -->
            <p class="hidden" data-admin-note style="font-size:11px;color:var(--text-muted);margin-bottom:12px;">New company files are created by an administrator.</p>`;

        if (companies.length === 0) {
            html += '<div class="empty-state"><p>No additional companies created</p></div>';
        } else {
            html += '<div class="card-grid">';
            for (const c of companies) {
                const fileLabel = c.file || c.database_name || '';
                html += `<div class="card">
                    <div class="card-header">${escapeHtml(c.name)}${c.sample ? ' <span class="badge badge-draft" data-sample style="font-size:9px;">Sample</span>' : ''}${c.is_current ? ' <span style="font-size:9px;color:var(--text-success);">(currently open)</span>' : ''}</div>
                    <div style="font-size:10px;color:var(--text-muted);">${escapeHtml(fileLabel)}</div>
                    ${c.description ? `<div style="font-size:11px;margin-top:4px;">${escapeHtml(c.description)}</div>` : ''}
                    ${c.last_accessed ? `<div style="font-size:9px;color:var(--text-light);margin-top:4px;">Last accessed: ${new Date(c.last_accessed).toLocaleDateString()}</div>` : ''}
                    ${serves && c.file && !c.is_current ? `<div style="margin-top:8px;"><button class="btn btn-sm btn-secondary" data-file="${escapeHtml(c.file)}" onclick="App.switchServedCompany(this.dataset.file)">Open</button></div>` : ''}
                </div>`;
            }
            html += '</div>';
        }
        return html;
    },

    // ---- SlowBooks' own Accountant's Copy (#346) --------------------------
    showSendToAccountant() {
        openModal('Send to accountant', `
            <form onsubmit="CompaniesPage.sendToAccountant(event)">
                <p style="font-size:12px; color:var(--text-muted); margin-bottom:10px;">A copy of these books, encrypted under a passphrase shown once, for your accountant to open in their own SlowBooks Pro. They work on or before the dividing date; you keep working after it. Their changes come back as one small file you apply here.</p>
                <div class="form-grid">
                    <div class="form-group"><label>Dividing date *</label><input name="dividing_date" type="date" required value="${todayISO()}"></div>
                    <div class="form-group"><label>Type the company's name to confirm *</label><input name="company_name" required autocomplete="off"></div>
                </div>
                <div id="ac-make-status" role="status" aria-live="polite" style="font-size:11px; color:var(--text-muted); min-height:14px; margin-top:6px;"></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Make the copy</button>
                </div>
            </form>`);
    },

    async sendToAccountant(e) {
        e.preventDefault();
        const form = e.target;
        const btn = form.querySelector('button[type="submit"]');
        const status = form.querySelector('#ac-make-status');
        if (btn) { btn.disabled = true; btn.textContent = 'Making the copy…'; }
        if (status) status.textContent = 'Copying the books and encrypting the copy; this takes a few seconds.';
        try {
            const r = await API.post('/accountants-copy/make', { dividing_date: form.dividing_date.value, company_name: form.company_name.value });
            const note = `File: ${r.file_name}\nPassphrase: ${r.passphrase}\nDividing date: ${r.dividing_date}\n\nOpen it in SlowBooks Pro: Companies → Open accountant's copy…, with this passphrase. Post the adjustments dated on or before the dividing date, then Companies → Send changes back and return that file.`;
            // The file name and the passphrase each copy on their own
            // (NEW-167); the whole note sits under them, selectable like
            // any text.
            CompaniesPage._sent = { file: r.file_name, passphrase: r.passphrase, note };
            openModal('Send these two things to your accountant', `
                <p style="font-size:12px; color:var(--text-muted); margin-bottom:8px;">The passphrase is shown here once and kept nowhere. Send the file one way and the passphrase another; each line copies on its own.</p>
                <div style="display:grid; grid-template-columns:max-content minmax(0,1fr) max-content; gap:6px 10px; align-items:center; margin-bottom:10px;">
                    ${copyLineHtml('sent-file', 'File', r.file_name, "CompaniesPage.copySent('file')")}
                    ${copyLineHtml('sent-passphrase', 'Passphrase', r.passphrase, "CompaniesPage.copySent('passphrase')")}
                    <span style="font-size:12px; color:var(--text-muted);">Dividing date</span><span style="font-size:12px;">${escapeHtml(r.dividing_date)}</span><span></span>
                </div>
                <textarea id="sent-note" readonly rows="8" aria-label="The whole note" style="width:100%; font-family:var(--font-mono); font-size:12px;">${escapeHtml(note)}</textarea>
                <div class="form-actions" style="justify-content:space-between;">
                    <button type="button" class="btn btn-secondary" onclick="CompaniesPage.copySent('note')">Copy the whole note</button>
                    <div>
                        <button type="button" class="btn btn-primary" onclick="window.open('/api/accountants-copy/download/${encodeURIComponent(r.copy_id)}','_blank')">Download the copy</button>
                        <button type="button" class="btn btn-secondary" onclick="closeModal(); App.navigate('#/companies');">Done</button>
                    </div>
                </div>`);
        } catch (err) {
            toast(err.message, 'error');
            if (btn) { btn.disabled = false; btn.textContent = 'Make the copy'; }
            if (status) status.textContent = '';
        }
    },

    // One line of the note, or all of it, to the clipboard; where the page
    // may not write it the text is selected for the copy shortcut instead.
    async copySent(which) {
        const sent = CompaniesPage._sent || {};
        const labels = { file: 'File name', passphrase: 'Passphrase', note: 'Note' };
        await copyToClipboard(sent[which] || '', labels[which] || 'Text', $(`#sent-${which}`));
    },

    showOpenCopy() {
        openModal("Open accountant's copy", `
            <form onsubmit="CompaniesPage.openCopy(event)">
                <p style="font-size:12px; color:var(--text-muted); margin-bottom:10px;">The .sbacopy file your client sent, and the passphrase they sent separately. It becomes a company file of its own here, opened with a password you choose.</p>
                <div class="form-grid">
                    <div class="form-group full-width"><label>Copy file *</label><input name="file" type="file" accept=".sbacopy,.zip" required data-accountant-ok></div>
                    <div class="form-group full-width"><label>Passphrase *</label><input name="passphrase" type="password" required autocomplete="off"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Open the copy</button>
                </div>
            </form>`);
    },

    async openCopy(e) {
        e.preventDefault();
        const form = e.target;
        const fd = new FormData();
        fd.append('file', form.file.files[0]);
        fd.append('passphrase', form.passphrase.value);
        try {
            const res = await fetch('/api/accountants-copy/open', { method: 'POST', body: fd });
            if (!res.ok) throw new Error(await API.responseError(res, 'Opening the copy failed'));
            const r = await res.json();
            toast(`${r.name} is on the picker. Switch company… opens it.`);
            closeModal();
            App.navigate('#/companies');
        } catch (err) { toast(err.message, 'error'); }
    },

    sendChanges() {
        IIFPage && typeof IIFPage._download === 'function'
            ? IIFPage._download('/api/accountants-copy/changes', 'accountants-changes.sbchanges')
            : window.open('/api/accountants-copy/changes', '_blank');
    },

    showApplyChanges() {
        openModal("Apply accountant's changes", `
            <form onsubmit="CompaniesPage.previewChanges(event)">
                <p style="font-size:12px; color:var(--text-muted); margin-bottom:10px;">The .sbchanges file your accountant sent back. You see every entry before anything posts; the new ones post as adjusting entries in their name, and a file applied twice posts nothing twice.</p>
                <div class="form-group"><label>Changes file *</label><input name="file" type="file" accept=".sbchanges,.json" required></div>
                <div id="ac-preview"></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-secondary">Preview</button>
                    <button type="button" class="btn btn-primary hidden" id="ac-apply-btn" onclick="CompaniesPage.applyChanges()">Apply</button>
                </div>
            </form>`);
    },

    async _postChanges(path) {
        const form = document.querySelector('#modal-body form');
        const fd = new FormData();
        fd.append('file', form.file.files[0]);
        const res = await fetch(path, { method: 'POST', body: fd });
        if (!res.ok) throw new Error(await API.responseError(res, 'Reading the changes file failed'));
        return res.json();
    },

    _previewHtml(seen) {
        const word = { new: 'new', applied: 'already posted', skipped: 'skipped', refused: 'refused', posted: 'posted' };
        const rows = seen.entries.map(e => `<tr>
                <td>${formatDate(e.date)}</td><td>${escapeHtml(e.description)}${e.reference ? ` (${escapeHtml(e.reference)})` : ''}</td>
                <td class="amount">${formatCurrency(e.total)}</td>
                <td>${word[e.status] || e.status}${e.why ? `<div style="font-size:10px; color:var(--gray-500);">${escapeHtml(e.why)}</div>` : ''}</td>
            </tr>`).join('');
        return `<p style="font-size:11px; color:var(--text-muted); margin:8px 0;">From ${escapeHtml(seen.accountant || 'the accountant')}, copy of ${escapeHtml(seen.company_name)}, dividing date ${formatDate(seen.dividing_date)}: ${seen.counts.new} new, ${seen.counts.applied} already posted, ${seen.counts.skipped} skipped, ${seen.counts.refused} refused.</p>
            <div class="table-container"><table><thead><tr><th scope="col">Date</th><th scope="col">Entry</th><th scope="col" class="amount">Amount</th><th scope="col">Status</th></tr></thead><tbody>${rows || '<tr><td colspan="4">No entries in the file</td></tr>'}</tbody></table></div>`;
    },

    async previewChanges(e) {
        e.preventDefault();
        try {
            const seen = await CompaniesPage._postChanges('/api/accountants-copy/changes/preview');
            document.getElementById('ac-preview').innerHTML = CompaniesPage._previewHtml(seen);
            const apply = document.getElementById('ac-apply-btn');
            if (apply) apply.classList.toggle('hidden', !seen.counts.new);
        } catch (err) { toast(err.message, 'error'); }
    },

    async applyChanges() {
        try {
            const done = await CompaniesPage._postChanges('/api/accountants-copy/changes/apply');
            document.getElementById('ac-preview').innerHTML = CompaniesPage._previewHtml(done);
            const apply = document.getElementById('ac-apply-btn');
            if (apply) apply.classList.add('hidden');
            toastPosted(`${done.counts.posted} entr${done.counts.posted === 1 ? 'y' : 'ies'} posted as adjusting entries`);
        } catch (err) { toast(err.message, 'error'); }
    },

    // Try a sample company (#684): Henry Brown's Auto Body Shop, IRS
    // Publication 583, made as a company file of its own beside this one.
    async trySample(btn) {
        if (btn) { btn.disabled = true; btn.textContent = 'Making the sample…'; }
        toast("Setting up Henry Brown's Auto Body Shop — this takes a few seconds.");
        try {
            const made = await API.post('/companies/sample', {});
            let serves = false;
            if (made && made.file && !CompaniesPage._isDesktop()) {
                try { serves = ((await API.get('/auth/status')).companies || []).length > 1; } catch (err) { /* said below */ }
            }
            toast(CompaniesPage._isDesktop()
                ? `${made.name} is on the picker. Switch company… opens it.`
                : serves
                ? `${made.name} is ready. Open it here; this company is left as it was.`
                : `${made.name} was created as its own database${made.database_name ? ` (${made.database_name})` : ''}. To open it, run a second server pointed at that database, or point this one at it and restart (docs/operations.md, "Several companies on a server").`);
            App.navigate('#/companies');
        } catch (err) {
            toast(err.message, 'error');
            if (btn) { btn.disabled = false; btn.textContent = 'Try a sample company'; }
        }
    },

    showCreate() {
        openModal('New Company', `
            <form onsubmit="CompaniesPage.create(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Company Name *</label>
                        <input name="name" required></div>
                    <div class="form-group"><label>Database Name</label>
                        <input name="database_name" pattern="[a-z0-9_]+" title="Lowercase letters, numbers, underscores only"
                            placeholder="Server installs only — auto-generated on desktop"></div>
                    <div class="form-group full-width"><label>Description</label>
                        <textarea name="description"></textarea></div>
                </div>
                <div id="company-create-status" role="status" aria-live="polite"
                    style="font-size:11px; color:var(--text-muted); min-height:14px; margin-top:6px;"></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create Company</button>
                </div>
            </form>`);
    },

    async create(e) {
        e.preventDefault();
        const form = e.target;
        const data = Object.fromEntries(new FormData(form).entries());
        if (!data.database_name) delete data.database_name;
        // Building a company file (its tables and chart of accounts) takes a
        // few seconds, and the dialog used to sit unchanged until it was done
        // (explore 2.17.3, skytech M18). Say so, and take away a second click.
        const btn = form.querySelector('button[type="submit"]');
        const status = form.querySelector('#company-create-status');
        if (btn) { btn.disabled = true; btn.textContent = 'Creating…'; }
        if (status) status.textContent = `Setting up ${data.name || 'the company'} — this takes a few seconds.`;
        try {
            const made = await API.post('/companies', data);
            let serves = false;
            if (made && made.file && !CompaniesPage._isDesktop()) {
                try { serves = ((await API.get('/auth/status')).companies || []).length > 1; } catch (err) { /* said below */ }
            }
            toast(CompaniesPage._isDesktop()
                ? 'Company created. Switch company… opens it.'
                : serves
                ? 'Company created. Open it here to set it up; this server serves it with the others.'
                : `Company created as its own database${data.database_name ? ` (${data.database_name})` : ''}. To open it, run a second server pointed at that database, or point this one at it and restart (docs/operations.md, "Several companies on a server").`);
            closeModal();
            App.navigate('#/companies');
        } catch (err) {
            toast(err.message, 'error');
            if (btn) { btn.disabled = false; btn.textContent = 'Create Company'; }
            if (status) status.textContent = '';
        }
    },
};
