/**
 * Tax Forms — generate W-2, W-3, 940, 941, 1099 PDFs
 * Feature 18: Payroll tax form generation
 */

// Forms open the way invoices do: window.open on the form's GET URL. In a
// browser that is a new tab; in the desktop app the shim fetches it and
// shows the native PDF viewer. A fetched blob: window opened nothing in the
// Mac app (WKWebView), so W-2 / W-3 / 940 / 941 produced nothing there.
function _openForm(url) {
    window.open(url, '_blank');
}

const TaxFormsPage = {
    async render() {
        const currentYear = new Date().getFullYear();

        // Form history (#263): every W-2 / W-3 / 940 / 941 generated from these
        // books, newest first, with who generated it; Check re-figures one.
        let history = [];
        try { history = await API.get('/document-audits?limit=100'); } catch (_) { history = null; }

        // Fetch employee list for the W-2 dropdown
        let empOptions = '<option value="">— Select Employee —</option>';
        try {
            const emps = await API.get('/employees?active_only=false');
            for (const e of emps) {
                empOptions += `<option value="${e.id}">${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}</option>`;
            }
        } catch (_) {
            empOptions += '<option value="" disabled>Could not load employees</option>';
        }

        return `
            <div class="page-header">
                <h2>Tax Forms</h2>
            </div>

            <div class="card" style="margin-bottom:16px;padding:16px">
                <h3 id="taxforms-h-w-2-w-3">W-2 / W-3</h3>
                <div class="form-grid" role="group" aria-labelledby="taxforms-h-w-2-w-3">
                    <div class="form-group">
                        <label>Year</label>
                        <input id="w2-year" type="number" value="${currentYear}" min="2000" max="2099" style="width:100px">
                    </div>
                    <div class="form-group">
                        <label>Employee (for W-2)</label>
                        <select id="w2-employee">${empOptions}</select>
                    </div>
                </div>
                <div class="form-actions" style="margin-top:8px">
                    <button class="btn btn-primary" onclick="TaxFormsPage.generateW2()">Generate W-2</button>
                    <button class="btn btn-secondary" onclick="TaxFormsPage.generateW3()">Generate W-3 (All Employees)</button>
                </div>
            </div>

            <div class="card" style="margin-bottom:16px;padding:16px">
                <h3 id="taxforms-h-form-940-futa">Form 940 (FUTA)</h3>
                <div class="form-grid" role="group" aria-labelledby="taxforms-h-form-940-futa">
                    <div class="form-group">
                        <label>Year</label>
                        <input id="f940-year" type="number" value="${currentYear}" min="2000" max="2099" style="width:100px">
                    </div>
                </div>
                <div class="form-actions" style="margin-top:8px">
                    <button class="btn btn-primary" onclick="TaxFormsPage.generate940()">Generate 940</button>
                </div>
            </div>

            <div class="card" style="margin-bottom:16px;padding:16px">
                <h3 id="taxforms-h-form-941-payroll-tax">Form 941 (Payroll Tax)</h3>
                <div class="form-grid" role="group" aria-labelledby="taxforms-h-form-941-payroll-tax">
                    <div class="form-group">
                        <label>Year</label>
                        <input id="f941-year" type="number" value="${currentYear}" min="2000" max="2099" style="width:100px">
                    </div>
                    <div class="form-group">
                        <label>Quarter</label>
                        <select id="f941-quarter">
                            <option value="1">Q1 (Jan–Mar)</option>
                            <option value="2">Q2 (Apr–Jun)</option>
                            <option value="3">Q3 (Jul–Sep)</option>
                            <option value="4">Q4 (Oct–Dec)</option>
                        </select>
                    </div>
                </div>
                <div class="form-actions" style="margin-top:8px">
                    <button class="btn btn-primary" onclick="TaxFormsPage.generate941()">Generate 941</button>
                </div>
            </div>

            <div class="card" style="margin-bottom:16px;padding:16px">
                <h3 id="taxforms-h-1099-nec-1096-contractors">1099-NEC / 1096 (Contractors)</h3>
                <p style="font-size:12px;color:var(--gray-500);margin:0 0 8px;">Vendors marked "1099 Vendor: Yes" with type NEC on the Vendors page. A 1099-NEC is required for anyone paid $2,000 or more in the year from 2026 ($600 or more through 2025); the 1096 sends them to the IRS.</p>
                <div class="form-grid" role="group" aria-labelledby="taxforms-h-1099-nec-1096-contractors">
                    <div class="form-group">
                        <label>Year</label>
                        <input id="f1099-year" type="number" value="${currentYear}" min="2000" max="2099" style="width:100px" onchange="TaxFormsPage.load1099Vendors()">
                    </div>
                    <div class="form-group">
                        <label>Vendor (for 1099-NEC)</label>
                        <select id="f1099-vendor">${await TaxFormsPage._vendorOptions(currentYear)}</select>
                    </div>
                </div>
                <div class="form-actions" style="margin-top:8px">
                    <button class="btn btn-primary" onclick="TaxFormsPage.generate1099()">Generate 1099-NEC</button>
                    <button class="btn btn-secondary" onclick="TaxFormsPage.generate1096()">Generate 1096 (Transmittal)</button>
                </div>
            </div>

            <div class="card" style="margin-bottom:16px;padding:16px">
                <h3 id="taxforms-h-form-history">Form history</h3>
                <p style="font-size:12px;color:var(--gray-500);margin:0 0 8px;">Every W-2, W-3, 940 and 941 generated from these books, newest first. Check re-figures a form from today's books and says whether it still matches the one that was generated; the hash is the one printed in the form's footer.</p>
                <div class="form-grid" role="group" aria-labelledby="taxforms-h-form-history">
                    <div class="form-group" style="grid-column:1 / -1">
                        <label for="audit-hash">Find a form by its footer: the hash's 16 characters (or the full 64), or the audit ID</label>
                        <input id="audit-hash" placeholder="e.g. 054c0c8ea8389ab8, or #42" spellcheck="false" style="font-family:var(--font-mono);width:100%;max-width:560px">
                    </div>
                </div>
                <div class="form-actions" style="margin-top:8px">
                    <button class="btn btn-secondary" onclick="TaxFormsPage.findByHash()">Find</button>
                </div>
                <p id="audit-hash-result" role="status" style="font-size:12px;margin:8px 0 0"></p>
                <div class="table-container" style="margin-top:8px">
                    <table>
                        <thead><tr>
                            <th scope="col">Form</th><th scope="col">For</th><th scope="col">Generated</th><th scope="col">By</th><th scope="col">Hash</th><th scope="col">Status</th><th scope="col" style="position:relative"><span class="visually-hidden">Check</span></th>
                        </tr></thead>
                        <tbody id="audit-rows">${TaxFormsPage._historyRows(history)}</tbody>
                    </table>
                </div>
            </div>

            <div class="card note--caution" style="padding:16px">
                <p style="margin:0"><strong>Note:</strong> Tax forms are for reference. Verify calculations with a licensed tax professional before filing.</p>
            </div>`;
    },

    _historyRows(history) {
        if (history === null) return '<tr><td colspan="7"><em>Could not load the form history</em></td></tr>';
        if (!history.length) return '<tr><td colspan="7"><em>No forms generated yet</em></td></tr>';
        return history.map(a => `<tr data-audit="${a.id}">
            <td>${escapeHtml(a.form || a.doc_type)}</td>
            <td>${escapeHtml(a.subject || a.doc_key)}</td>
            <td>${escapeHtml(TaxFormsPage._when(a.created_at))}</td>
            <td>${escapeHtml(a.username || '—')}</td>
            <td><code title="${escapeHtml(a.content_hash)}" style="font-size:11px">${escapeHtml(String(a.content_hash || '').slice(0, 16))}…</code></td>
            <td id="audit-status-${a.id}" role="status" aria-atomic="true" style="font-size:12px;max-width:420px">Not checked</td>
            <td class="actions"><button class="btn btn-sm btn-secondary" onclick="TaxFormsPage.checkForm(${a.id})" aria-label="Check the ${escapeHtml(a.form || a.doc_type)} for ${escapeHtml(a.subject || a.doc_key)}">Check</button></td>
        </tr>`).join('');
    },

    _when(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return String(iso);
        return d.toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' });
    },

    // Re-figure one form from today's books; the answer is a sentence in
    // the row's Status cell (a live region, so a screen reader hears it).
    // Re-figure one form from today's books; the answer is a sentence in
    // the row's Status cell (a live region, so a screen reader hears it),
    // with a mark in the success or danger colour and the time it was
    // checked, so a second Check is seen to have happened (NEW-67).
    async checkForm(id) {
        const cell = document.getElementById(`audit-status-${id}`);
        if (cell) { cell.className = ''; cell.textContent = 'Checking…'; }
        // with seconds: two Checks within a minute read differently, so the
        // second is seen and announced (gate round 3, W-8 / NEW-71)
        const at = new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', second: '2-digit' });
        try {
            const r = await API.get(`/document-audits/${id}/check`);
            if (!cell) return;
            const kind = r.matches === true ? 'ok' : r.matches === false ? 'changed' : 'unknown';
            const mark = { ok: '✓', changed: '✗', unknown: '?' }[kind];
            cell.className = `audit-status audit-status--${kind}`;
            cell.innerHTML = `<span class="audit-mark" aria-hidden="true">${mark}</span> ${escapeHtml(r.message)} <span class="audit-checked-at">(checked at ${escapeHtml(at)})</span>`;
        } catch (err) {
            if (cell) { cell.className = 'audit-status audit-status--unknown'; cell.textContent = `Could not check: ${err.message}`; }
        }
    },

    // The hash printed in a form's footer, looked up: known to these books
    // (which form, when, by whom) or not.
    async findByHash() {
        const input = document.getElementById('audit-hash');
        const out = document.getElementById('audit-hash-result');
        const typed = (input ? input.value : '').trim().toLowerCase().replace(/^hash\s+/, '');
        const byId = /^#?(\d{1,9})$/.exec(typed);
        const hash = typed.replace(/^#/, '');
        // the last match's highlight goes before any answer (NEW-64)
        document.querySelectorAll('#audit-rows tr[data-found]').forEach(tr => tr.removeAttribute('data-found'));
        if (!byId && !/^[0-9a-f]{16,64}$/.test(hash)) {
            if (out) out.textContent = 'Type the 16 characters after "Hash" in the footer (or the full 64), or the audit ID after "Audit ID".';
            return;
        }
        try {
            let rows;
            if (byId) {
                try { rows = [await API.get(`/document-audits/${byId[1]}`)]; } catch (e) { rows = []; }
            } else {
                rows = await API.get(`/document-audits/verify/${hash}`);
            }
            if (!rows.length) {
                if (out) out.textContent = byId ? `Not known to these books: no generated form has audit ID ${byId[1]}.` : 'Not known to these books: no generated form carries that hash.';
                return;
            }
            const said = rows.map(a => `${a.form || a.doc_type} for ${a.subject || a.doc_key}, generated ${TaxFormsPage._when(a.created_at)}${a.username ? ` by ${a.username}` : ''} (audit #${a.id})`);
            if (out) out.textContent = `Known to these books: ${said.join('; ')}.`;
            for (const a of rows) {
                const tr = document.querySelector(`#audit-rows tr[data-audit="${a.id}"]`);
                if (tr) tr.setAttribute('data-found', '1');
            }
        } catch (err) {
            if (out) out.textContent = `Could not look it up: ${err.message}`;
        }
    },

    // The 1099-NEC vendors for a year, with what each was paid.
    async _vendorOptions(year) {
        let html = '<option value="">— Select Vendor —</option>';
        try {
            const data = await API.get(`/tax-forms/1099?year=${year}`);
            if (!data.vendors.length) {
                return '<option value="">No 1099-NEC vendors: mark vendors "1099 Vendor: Yes" on the Vendors page</option>';
            }
            for (const v of data.vendors) {
                html += `<option value="${v.vendor_id}">${escapeHtml(v.name)} — ${formatCurrency(v.total_paid)}${v.reportable ? '' : ` (under ${formatCurrency(data.threshold)})`}</option>`;
            }
        } catch (_) {
            html += '<option value="" disabled>Could not load vendors</option>';
        }
        return html;
    },

    async load1099Vendors() {
        const yearEl = document.getElementById('f1099-year');
        const sel = document.getElementById('f1099-vendor');
        if (!yearEl || !yearEl.value || !sel) return;
        sel.innerHTML = await TaxFormsPage._vendorOptions(yearEl.value);
    },

    generate1099() {
        const yearEl = document.getElementById('f1099-year');
        const vendorEl = document.getElementById('f1099-vendor');
        const year = yearEl ? yearEl.value : '';
        const vendorId = vendorEl ? vendorEl.value : '';
        if (!year) { toast('Please enter a year', 'error'); return; }
        if (!vendorId) { toast('Please select a vendor', 'error'); return; }
        _openForm(`/api/tax-forms/1099/${vendorId}/pdf?year=${year}`);
    },

    generate1096() {
        const yearEl = document.getElementById('f1099-year');
        const year = yearEl ? yearEl.value : '';
        if (!year) { toast('Please enter a year', 'error'); return; }
        _openForm(`/api/tax-forms/1096/pdf?year=${year}`);
    },

    async generateW2() {
        const yearEl = document.getElementById('w2-year');
        const empEl = document.getElementById('w2-employee');
        const year = yearEl ? yearEl.value : '';
        const empId = empEl ? empEl.value : '';
        if (!year) { toast('Please enter a year', 'error'); return; }
        if (!empId) { toast('Please select an employee', 'error'); return; }
        _openForm(`/api/payroll/forms/w2/${empId}/pdf?year=${year}`);
    },

    async generateW3() {
        const yearEl = document.getElementById('w2-year');
        const year = yearEl ? yearEl.value : '';
        if (!year) { toast('Please enter a year', 'error'); return; }
        _openForm(`/api/payroll/forms/w3/${year}/pdf`);
    },

    async generate940() {
        const yearEl = document.getElementById('f940-year');
        const year = yearEl ? yearEl.value : '';
        if (!year) { toast('Please enter a year', 'error'); return; }
        _openForm(`/api/payroll/forms/940/${year}/pdf`);
    },

    async generate941() {
        const yearEl = document.getElementById('f941-year');
        const quarterEl = document.getElementById('f941-quarter');
        const year = yearEl ? yearEl.value : '';
        const quarter = quarterEl ? quarterEl.value : '';
        if (!year) { toast('Please enter a year', 'error'); return; }
        if (!quarter) { toast('Please select a quarter', 'error'); return; }
        _openForm(`/api/payroll/forms/941/${year}/${quarter}/pdf`);
    },
};
