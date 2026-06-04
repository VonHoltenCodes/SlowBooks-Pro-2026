/**
 * Employees — CRUD for employee records
 * Feature 17: Payroll basics
 */
const EmployeesPage = {
    async render() {
        const emps = await API.get('/employees');
        let html = `
            <div class="page-header">
                <h2>Employees</h2>
                <button class="btn btn-primary" onclick="EmployeesPage.showForm()">+ Add Employee</button>
            </div>`;

        if (emps.length === 0) {
            html += '<div class="empty-state"><p>No employees added yet</p></div>';
        } else {
            html += `<div class="table-container"><table>
                <thead><tr><th>Name</th><th>Pay Type</th><th class="amount">Rate</th><th>Status</th><th>Filing</th><th>Actions</th></tr></thead><tbody>`;
            for (const e of emps) {
                html += `<tr>
                    <td><strong>${escapeHtml(e.first_name)} ${escapeHtml(e.last_name)}</strong></td>
                    <td>${e.pay_type}</td>
                    <td class="amount">${formatCurrency(e.pay_rate)}${e.pay_type==='hourly'?'/hr':'/yr'}</td>
                    <td>${e.is_active ? '<span class="badge badge-paid">Active</span>' : '<span class="badge badge-draft">Inactive</span>'}</td>
                    <td>${e.filing_status}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="EmployeesPage.showForm(${e.id})">Edit</button>
                        <button class="btn btn-sm btn-secondary" onclick="EmployeesPage.viewDetails(${e.id})">Details</button>
                    </td>
                </tr>`;
            }
            html += '</tbody></table></div>';
        }
        return html;
    },

    async showForm(id = null) {
        let emp = {
            first_name: '', last_name: '', pay_type: 'hourly', pay_rate: 0,
            filing_status: 'single', allowances: 0, hire_date: todayISO(),
            email: '', pay_frequency: 'biweekly', work_state: '', residence_state: '',
            role: 'employee', manager_id: '',
            multiple_jobs: false, dependents_amount: 0, other_income_annual: 0,
            deductions_annual: 0, extra_withholding: 0,
            address1: '', address2: '', city: '', state: '', zip: '',
            wc_class_code: '', notes: ''
        };
        if (id) emp = await API.get(`/employees/${id}`);

        openModal(id ? 'Edit Employee' : 'Add Employee', `
            <form onsubmit="EmployeesPage.save(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>First Name *</label>
                        <input name="first_name" required value="${escapeHtml(emp.first_name)}"></div>
                    <div class="form-group"><label>Last Name *</label>
                        <input name="last_name" required value="${escapeHtml(emp.last_name)}"></div>
                    <div class="form-group"><label>SSN Last 4</label>
                        <input name="ssn_last_four" maxlength="4" value="${escapeHtml(emp.ssn_last_four || '')}"></div>
                    <div class="form-group"><label>Email</label>
                        <input name="email" type="email" value="${escapeHtml(emp.email || '')}"></div>
                    <div class="form-group"><label>Pay Type</label>
                        <select name="pay_type">
                            <option value="hourly" ${emp.pay_type==='hourly'?'selected':''}>Hourly</option>
                            <option value="salary" ${emp.pay_type==='salary'?'selected':''}>Salary</option>
                        </select></div>
                    <div class="form-group"><label>Pay Rate</label>
                        <input name="pay_rate" type="number" step="0.01" value="${emp.pay_rate}"></div>
                    <div class="form-group"><label>Pay Frequency</label>
                        <select name="pay_frequency">
                            <option value="biweekly" ${(emp.pay_frequency||'biweekly')==='biweekly'?'selected':''}>Bi-Weekly</option>
                            <option value="weekly" ${emp.pay_frequency==='weekly'?'selected':''}>Weekly</option>
                            <option value="semimonthly" ${emp.pay_frequency==='semimonthly'?'selected':''}>Semi-Monthly</option>
                            <option value="monthly" ${emp.pay_frequency==='monthly'?'selected':''}>Monthly</option>
                        </select></div>
                    <div class="form-group"><label>Filing Status</label>
                        <select name="filing_status">
                            <option value="single" ${emp.filing_status==='single'?'selected':''}>Single</option>
                            <option value="married" ${emp.filing_status==='married'?'selected':''}>Married</option>
                            <option value="head_of_household" ${emp.filing_status==='head_of_household'?'selected':''}>Head of Household</option>
                        </select></div>
                    <div class="form-group"><label>Allowances</label>
                        <input name="allowances" type="number" value="${emp.allowances || 0}"></div>
                    <div class="form-group"><label>Hire Date</label>
                        <input name="hire_date" type="date" value="${emp.hire_date || ''}"></div>
                    <div class="form-group"><label>Work State</label>
                        <input name="work_state" maxlength="2" placeholder="e.g. WA" value="${escapeHtml(emp.work_state || '')}"></div>
                    <div class="form-group"><label>Residence State</label>
                        <input name="residence_state" maxlength="2" placeholder="e.g. WA" value="${escapeHtml(emp.residence_state || '')}"></div>
                    <div class="form-group"><label>Role</label>
                        <select name="role">
                            <option value="employee" ${(emp.role||'employee')==='employee'?'selected':''}>Employee</option>
                            <option value="manager" ${emp.role==='manager'?'selected':''}>Manager</option>
                            <option value="admin" ${emp.role==='admin'?'selected':''}>Admin</option>
                        </select></div>
                    <div class="form-group"><label>Manager ID (optional)</label>
                        <input name="manager_id" type="number" value="${emp.manager_id || ''}"></div>
                    <div class="form-group"><label>WC Class Code</label>
                        <input name="wc_class_code" value="${escapeHtml(emp.wc_class_code || '')}"></div>
                </div>

                <h4>Form W-4 (2020+)</h4>
                <div class="form-grid">
                    <div class="form-group" style="grid-column:1/-1">
                        <label><input name="multiple_jobs" type="checkbox" ${emp.multiple_jobs ? 'checked' : ''}> Multiple Jobs / Spouse Works</label>
                    </div>
                    <div class="form-group"><label>Dependents Amount ($)</label>
                        <input name="dependents_amount" type="number" step="0.01" value="${emp.dependents_amount || 0}"></div>
                    <div class="form-group"><label>Other Income Annual ($)</label>
                        <input name="other_income_annual" type="number" step="0.01" value="${emp.other_income_annual || 0}"></div>
                    <div class="form-group"><label>Deductions Annual ($)</label>
                        <input name="deductions_annual" type="number" step="0.01" value="${emp.deductions_annual || 0}"></div>
                    <div class="form-group"><label>Extra Withholding ($)</label>
                        <input name="extra_withholding" type="number" step="0.01" value="${emp.extra_withholding || 0}"></div>
                </div>

                <h4>Address</h4>
                <div class="form-grid">
                    <div class="form-group" style="grid-column:1/-1"><label>Address Line 1</label>
                        <input name="address1" value="${escapeHtml(emp.address1 || '')}"></div>
                    <div class="form-group" style="grid-column:1/-1"><label>Address Line 2</label>
                        <input name="address2" value="${escapeHtml(emp.address2 || '')}"></div>
                    <div class="form-group"><label>City</label>
                        <input name="city" value="${escapeHtml(emp.city || '')}"></div>
                    <div class="form-group"><label>State</label>
                        <input name="state" maxlength="2" value="${escapeHtml(emp.state || '')}"></div>
                    <div class="form-group"><label>ZIP</label>
                        <input name="zip" value="${escapeHtml(emp.zip || '')}"></div>
                </div>

                <div class="form-group">
                    <label>Notes</label>
                    <textarea name="notes" rows="3">${escapeHtml(emp.notes || '')}</textarea>
                </div>

                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">${id ? 'Update' : 'Add'} Employee</button>
                </div>
            </form>`);
    },

    async save(e, id) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        data.pay_rate = parseFloat(data.pay_rate) || 0;
        data.allowances = parseInt(data.allowances) || 0;
        data.dependents_amount = parseFloat(data.dependents_amount) || 0;
        data.other_income_annual = parseFloat(data.other_income_annual) || 0;
        data.deductions_annual = parseFloat(data.deductions_annual) || 0;
        data.extra_withholding = parseFloat(data.extra_withholding) || 0;
        data.multiple_jobs = data.multiple_jobs === 'on';
        if (data.manager_id) { data.manager_id = parseInt(data.manager_id) || null; }
        else { delete data.manager_id; }
        if (!data.hire_date) delete data.hire_date;
        try {
            if (id) { await API.put(`/employees/${id}`, data); toast('Employee updated'); }
            else { await API.post('/employees', data); toast('Employee added'); }
            closeModal();
            App.navigate('#/employees');
        } catch (err) { toast(err.message, 'error'); }
    },

    async viewDetails(id) {
        const emp = await API.get(`/employees/${id}`);
        const fullName = `${escapeHtml(emp.first_name)} ${escapeHtml(emp.last_name)}`;

        const html = `
            <div style="max-width:700px;margin:0 auto">
                <h4>Overview</h4>
                <dl class="detail-list" style="display:grid;grid-template-columns:1fr 1fr;gap:4px 16px">
                    <dt>Name</dt><dd>${fullName}</dd>
                    <dt>Email</dt><dd>${escapeHtml(emp.email || '—')}</dd>
                    <dt>SSN Last 4</dt><dd>${escapeHtml(emp.ssn_last_four || '—')}</dd>
                    <dt>Pay Type</dt><dd>${emp.pay_type}</dd>
                    <dt>Pay Rate</dt><dd>${formatCurrency(emp.pay_rate)}${emp.pay_type==='hourly'?'/hr':'/yr'}</dd>
                    <dt>Pay Frequency</dt><dd>${emp.pay_frequency || '—'}</dd>
                    <dt>Filing Status</dt><dd>${emp.filing_status}</dd>
                    <dt>Allowances</dt><dd>${emp.allowances ?? '—'}</dd>
                    <dt>Hire Date</dt><dd>${formatDate(emp.hire_date)}</dd>
                    <dt>Work State</dt><dd>${escapeHtml(emp.work_state || '—')}</dd>
                    <dt>Residence State</dt><dd>${escapeHtml(emp.residence_state || '—')}</dd>
                    <dt>Role</dt><dd>${emp.role || '—'}</dd>
                    <dt>Manager ID</dt><dd>${emp.manager_id ?? '—'}</dd>
                    <dt>WC Class Code</dt><dd>${escapeHtml(emp.wc_class_code || '—')}</dd>
                    <dt>Status</dt><dd>${emp.is_active ? 'Active' : 'Inactive'}</dd>
                    <dt style="grid-column:1/-1">Address</dt>
                    <dd style="grid-column:1/-1">${[emp.address1, emp.address2, emp.city, emp.state, emp.zip].filter(Boolean).map(escapeHtml).join(', ') || '—'}</dd>
                    <dt>Multiple Jobs (W-4)</dt><dd>${emp.multiple_jobs ? 'Yes' : 'No'}</dd>
                    <dt>Dependents Amount</dt><dd>${formatCurrency(emp.dependents_amount)}</dd>
                    <dt>Other Income Annual</dt><dd>${formatCurrency(emp.other_income_annual)}</dd>
                    <dt>Deductions Annual</dt><dd>${formatCurrency(emp.deductions_annual)}</dd>
                    <dt>Extra Withholding</dt><dd>${formatCurrency(emp.extra_withholding)}</dd>
                    <dt style="grid-column:1/-1">Notes</dt>
                    <dd style="grid-column:1/-1">${escapeHtml(emp.notes || '—')}</dd>
                </dl>

                <hr>
                <h4>Portal Access</h4>
                <div id="emp-portal-section-${id}">
                    <p>Loading portal token…</p>
                </div>

                <hr>
                <h4>E-Verify</h4>
                <div id="emp-everify-section-${id}">
                    <p>Loading E-Verify…</p>
                </div>

                <hr>
                <h4>YTD Totals</h4>
                <div id="emp-ytd-section-${id}">
                    <p>Loading YTD totals…</p>
                </div>

                <hr>
                <h4>Bank Accounts</h4>
                <div id="emp-bank-section-${id}">
                    <p>Loading bank accounts…</p>
                </div>

                <hr>
                <h4>Documents</h4>
                <div id="emp-docs-section-${id}">
                    <p>Loading documents…</p>
                </div>
            </div>`;

        openModal(`Employee: ${fullName}`, html);

        // Load all sections asynchronously after modal opens
        EmployeesPage._loadPortal(id);
        EmployeesPage._loadEverify(id);
        EmployeesPage._loadYTD(id);
        EmployeesPage._loadBankAccounts(id);
        EmployeesPage._loadDocuments(id);
    },

    async _loadEverify(id) {
        const el = document.getElementById(`emp-everify-section-${id}`);
        if (!el) return;
        try {
            const data = await API.get(`/employees/${id}/everify`);
            el.innerHTML = EmployeesPage._renderEverify(id, data);
        } catch (err) {
            el.innerHTML = `<p class="text-muted">E-Verify unavailable: ${escapeHtml(err.message)}</p>`;
        }
    },

    _renderEverify(id, data) {
        const STATUS_LABELS = {
            not_submitted: 'Not submitted',
            pending: 'Pending',
            photo_match_required: 'Photo match required',
            tnc: 'Tentative non-confirmation (TNC)',
            employment_authorized: 'Employment authorized',
            final_non_confirmation: 'Final non-confirmation',
            case_closed: 'Case closed',
        };
        const STATUS_COLORS = {
            employment_authorized: '#1f7a36',
            final_non_confirmation: '#a4242b',
            tnc: '#a4242b',
            photo_match_required: '#a8761f',
            pending: '#336699',
        };
        const status = data.status || 'not_submitted';
        const color = STATUS_COLORS[status] || '#666';
        return `
            <p style="margin:0 0 6px 0">
                <strong>Status:</strong> <span style="color:${color};font-weight:600">${escapeHtml(STATUS_LABELS[status] || status)}</span>
            </p>
            <p style="font-size:12px;color:#666;margin:0 0 4px 0">
                Case #: ${escapeHtml(data.case_number || '—')}
                ${data.submitted_at ? `&nbsp;·&nbsp;Submitted ${escapeHtml(data.submitted_at.slice(0, 10))}` : ''}
                ${data.closed_at ? `&nbsp;·&nbsp;Closed ${escapeHtml(data.closed_at.slice(0, 10))}` : ''}
            </p>
            ${data.notes ? `<p style="font-size:12px;color:#666;margin:0 0 6px 0;white-space:pre-wrap">${escapeHtml(data.notes)}</p>` : ''}
            <button class="btn btn-sm btn-secondary" onclick="EmployeesPage._showEverifyForm(${id})">Update E-Verify case</button>
            <p style="font-size:11px;color:#999;margin-top:6px">
                Record-keeping only — submit cases via the federal E-Verify portal.
            </p>`;
    },

    async _showEverifyForm(id) {
        let data = {};
        try { data = await API.get(`/employees/${id}/everify`); }
        catch (_) { /* fall through with empty defaults */ }
        const status = data.status || 'not_submitted';
        const opts = [
            ['not_submitted', 'Not submitted'],
            ['pending', 'Pending'],
            ['photo_match_required', 'Photo match required'],
            ['tnc', 'Tentative non-confirmation (TNC)'],
            ['employment_authorized', 'Employment authorized'],
            ['final_non_confirmation', 'Final non-confirmation'],
            ['case_closed', 'Case closed'],
        ].map(([v, l]) => `<option value="${v}"${status === v ? ' selected' : ''}>${escapeHtml(l)}</option>`).join('');
        openModal('Update E-Verify Case', `
            <form onsubmit="EmployeesPage._saveEverify(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Case number</label>
                        <input name="case_number" value="${escapeHtml(data.case_number || '')}" placeholder="e.g. 2026123456789"></div>
                    <div class="form-group"><label>Status</label>
                        <select name="status">${opts}</select></div>
                </div>
                <div class="form-group"><label>Notes</label>
                    <textarea name="notes" rows="3" placeholder="Internal note (e.g. photo match received, TNC contested)">${escapeHtml(data.notes || '')}</textarea></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save</button>
                </div>
            </form>`);
    },

    async _saveEverify(e, id) {
        e.preventDefault();
        const f = e.target;
        const body = {
            case_number: f.case_number.value,
            status: f.status.value,
            notes: f.notes.value,
        };
        try {
            await API.put(`/employees/${id}/everify`, body);
            toast('E-Verify case updated');
            closeModal();
            EmployeesPage._loadEverify(id);
        } catch (err) { toast(err.message, 'error'); }
    },

    async _loadPortal(id) {
        const el = document.getElementById(`emp-portal-section-${id}`);
        if (!el) return;
        try {
            const [token, access] = await Promise.all([
                API.get(`/employees/${id}/portal-token`),
                API.get(`/employees/${id}/portal-access?limit=10`).catch(() => []),
            ]);
            el.innerHTML = EmployeesPage._renderPortalSection(id, token, access);
        } catch (err) {
            el.innerHTML = `<p class="text-muted">Portal token unavailable: ${escapeHtml(err.message)}</p>
                <button class="btn btn-sm btn-secondary" onclick="EmployeesPage._regeneratePortalToken(${id})">Generate Token</button>`;
        }
    },

    _renderPortalSection(id, token, access) {
        const url = token.portal_url || '';
        const expires = token.expires_at ? new Date(token.expires_at) : null;
        const lastUsed = token.last_used_at ? new Date(token.last_used_at) : null;
        const now = new Date();
        const daysUntilExpiry = expires ? Math.round((expires - now) / 86400000) : null;
        const daysSinceUsed = lastUsed ? Math.round((now - lastUsed) / 86400000) : null;
        const expiryClass = daysUntilExpiry !== null && daysUntilExpiry < 30
            ? 'style="color:#c0392b;font-weight:600"' : '';

        let html = `
            <p style="margin:0 0 4px 0">Portal URL: <a href="${escapeHtml(url)}" target="_blank">${escapeHtml(url)}</a></p>
            <p style="font-size:12px;color:#666;margin:0 0 4px 0">
                <span ${expiryClass}>Expires ${expires ? expires.toISOString().slice(0,10) : 'never'}${daysUntilExpiry !== null ? ` (${daysUntilExpiry} days)` : ''}</span>
                &nbsp;·&nbsp;
                Last used ${lastUsed ? `${daysSinceUsed} day${daysSinceUsed === 1 ? '' : 's'} ago (${lastUsed.toISOString().slice(0,10)})` : '<em>never</em>'}
            </p>
            <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;">
                <button class="btn btn-sm btn-primary" data-portal-url="${escapeHtml(url)}" onclick="EmployeesPage._copyPortalLink(this.dataset.portalUrl)">Copy Link</button>
                <button class="btn btn-sm btn-secondary" data-portal-url="${escapeHtml(url)}" onclick="EmployeesPage._emailPortalLink(this.dataset.portalUrl)">Email to Employee…</button>
                <button class="btn btn-sm btn-secondary" onclick="EmployeesPage._regeneratePortalToken(${id})">Rotate Token</button>
            </div>`;

        if (Array.isArray(access) && access.length > 0) {
            html += `
                <details style="margin-top:10px">
                    <summary style="cursor:pointer;font-size:12px;color:#336699">Recent access (${access.length} most recent)</summary>
                    <table class="data-table" style="margin-top:6px;font-size:11px">
                        <thead><tr><th>When</th><th>IP</th><th>Path</th><th>OK?</th></tr></thead>
                        <tbody>
                        ${access.map(a => `
                            <tr style="${a.success ? '' : 'color:#c0392b'}">
                                <td>${escapeHtml((a.created_at || '').replace('T',' ').slice(0,19))}</td>
                                <td>${escapeHtml(a.ip || '')}</td>
                                <td>${escapeHtml(a.path || '')}</td>
                                <td>${a.success ? '✓' : '✗'}</td>
                            </tr>`).join('')}
                        </tbody>
                    </table>
                </details>`;
        }
        return html;
    },

    _copyPortalLink(url) {
        if (!url) { toast('No portal URL yet — generate a token first.', 'error'); return; }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(url).then(
                () => toast('Portal link copied to clipboard'),
                () => toast('Couldn\'t copy — select the link and copy manually.', 'error'),
            );
        } else {
            toast('Clipboard API unavailable — select the URL above and copy manually.', 'error');
        }
    },

    _emailPortalLink(url) {
        if (!url) { toast('No portal URL yet — generate a token first.', 'error'); return; }
        // Single-token URL — leaks via Referer if the employee clicks it on
        // a page with outbound links. The actual portal claim flow swaps the
        // token for an HttpOnly cookie on first hit, so the exposure window
        // is one request. Still worth the heads-up in the mailto body.
        const subject = encodeURIComponent('Your Slowbooks employee portal link');
        const body = encodeURIComponent(
            'Hi,\n\nUse the link below to access your pay stubs, W-4, and ' +
            'time-off requests:\n\n' + url + '\n\nThe link is personal — please ' +
            'don\'t forward it. After your first visit it becomes cookieless ' +
            'and can\'t be reused from another device.\n\nThanks.'
        );
        window.location.href = `mailto:?subject=${subject}&body=${body}`;
    },

    async _regeneratePortalToken(id) {
        try {
            await API.post(`/employees/${id}/portal-token`, {});
            toast('Portal token rotated');
            // Re-fetch + re-render to pick up the new expires_at and reset last_used
            EmployeesPage._loadPortal(id);
        } catch (err) {
            toast(err.message, 'error');
            const el = document.getElementById(`emp-portal-section-${id}`);
            if (el) {
                el.innerHTML = `
                    <p class="text-muted">Rotate failed: ${escapeHtml(err.message)}</p>
                    <button class="btn btn-sm btn-secondary" onclick="EmployeesPage._regeneratePortalToken(${id})">Try again</button>`;
            }
        }
    },

    async _loadYTD(id) {
        const el = document.getElementById(`emp-ytd-section-${id}`);
        if (!el) return;
        try {
            const ytd = await API.get(`/employees/${id}/ytd`);
            el.innerHTML = `
                <table>
                    <thead><tr><th>Gross</th><th class="amount">Federal</th><th class="amount">State</th><th class="amount">SS</th><th class="amount">Medicare</th><th class="amount">Net</th></tr></thead>
                    <tbody><tr>
                        <td class="amount">${formatCurrency(ytd.gross)}</td>
                        <td class="amount">${formatCurrency(ytd.federal)}</td>
                        <td class="amount">${formatCurrency(ytd.state)}</td>
                        <td class="amount">${formatCurrency(ytd.ss)}</td>
                        <td class="amount">${formatCurrency(ytd.medicare)}</td>
                        <td class="amount">${formatCurrency(ytd.net)}</td>
                    </tr></tbody>
                </table>`;
        } catch (err) {
            el.innerHTML = `<p class="text-muted">YTD data unavailable: ${escapeHtml(err.message)}</p>`;
        }
    },

    async _loadBankAccounts(id) {
        const el = document.getElementById(`emp-bank-section-${id}`);
        if (!el) return;
        try {
            const accounts = await API.get(`/employees/${id}/bank-accounts`);
            let html = '';
            if (accounts.length === 0) {
                html = '<p class="text-muted">No bank accounts on file.</p>';
            } else {
                html = `<table>
                    <thead><tr><th>Nickname</th><th>Kind</th><th>Account</th><th>Deposit Type</th><th>Actions</th></tr></thead>
                    <tbody>`;
                for (const acct of accounts) {
                    html += `<tr>
                        <td>${escapeHtml(acct.nickname || '')}</td>
                        <td>${escapeHtml(acct.kind || acct.account_kind || '')}</td>
                        <td>••••${escapeHtml(acct.last_four || '')}</td>
                        <td>${escapeHtml(acct.deposit_type || '')}</td>
                        <td class="actions">
                            <button class="btn btn-sm btn-danger" onclick="EmployeesPage._deleteBankAccount(${id}, ${acct.id})">Delete</button>
                        </td>
                    </tr>`;
                }
                html += '</tbody></table>';
            }
            html += `
                <button class="btn btn-sm btn-secondary" style="margin-top:8px" onclick="EmployeesPage._showAddBankForm(${id})">+ Add Bank Account</button>
                <div id="emp-bank-form-${id}" style="display:none;margin-top:12px">
                    <div class="form-grid">
                        <div class="form-group"><label>Nickname</label>
                            <input id="bank-nickname-${id}" placeholder="e.g. Main Checking"></div>
                        <div class="form-group"><label>Account Kind</label>
                            <select id="bank-kind-${id}">
                                <option value="checking">Checking</option>
                                <option value="savings">Savings</option>
                            </select></div>
                        <div class="form-group"><label>Routing Number (9 digits)</label>
                            <input id="bank-routing-${id}" maxlength="9" pattern="[0-9]{9}"></div>
                        <div class="form-group"><label>Account Number</label>
                            <input id="bank-account-${id}"></div>
                        <div class="form-group"><label>Deposit Type</label>
                            <select id="bank-deposit-${id}">
                                <option value="full">Full</option>
                                <option value="fixed_amount">Fixed Amount</option>
                                <option value="percentage">Percentage</option>
                            </select></div>
                    </div>
                    <div class="form-actions">
                        <button class="btn btn-secondary btn-sm" onclick="document.getElementById('emp-bank-form-${id}').style.display='none'">Cancel</button>
                        <button class="btn btn-primary btn-sm" onclick="EmployeesPage._saveBankAccount(${id})">Save Account</button>
                    </div>
                </div>`;
            el.innerHTML = html;
        } catch (err) {
            el.innerHTML = `<p class="text-muted">Bank account data unavailable: ${escapeHtml(err.message)}</p>`;
        }
    },

    _showAddBankForm(id) {
        const formEl = document.getElementById(`emp-bank-form-${id}`);
        if (formEl) formEl.style.display = 'block';
    },

    async _saveBankAccount(id) {
        const nickname = document.getElementById(`bank-nickname-${id}`).value;
        const account_kind = document.getElementById(`bank-kind-${id}`).value;
        const routing_number = document.getElementById(`bank-routing-${id}`).value;
        const account_number = document.getElementById(`bank-account-${id}`).value;
        const deposit_type = document.getElementById(`bank-deposit-${id}`).value;
        if (!routing_number || routing_number.length !== 9) { toast('Routing number must be 9 digits', 'error'); return; }
        if (!account_number) { toast('Account number is required', 'error'); return; }
        try {
            await API.post(`/employees/${id}/bank-accounts`, { nickname, account_kind, routing_number, account_number, deposit_type });
            toast('Bank account added');
            EmployeesPage._loadBankAccounts(id);
        } catch (err) {
            toast(err.message, 'error');
        }
    },

    async _deleteBankAccount(empId, acctId) {
        if (!confirm('Delete this bank account?')) return;
        try {
            await API.del(`/employees/${empId}/bank-accounts/${acctId}`);
            toast('Bank account deleted');
            EmployeesPage._loadBankAccounts(empId);
        } catch (err) {
            toast(err.message, 'error');
        }
    },

    async _loadDocuments(id) {
        const el = document.getElementById(`emp-docs-section-${id}`);
        if (!el) return;
        try {
            const docs = await API.get(`/employees/${id}/documents`);
            let html = '';
            if (docs.length === 0) {
                html = '<p class="text-muted">No documents on file.</p>';
            } else {
                html = `<table>
                    <thead><tr><th>Filename</th><th>Category</th><th>Size</th><th>Uploaded</th><th>Actions</th></tr></thead>
                    <tbody>`;
                for (const doc of docs) {
                    html += `<tr>
                        <td>${escapeHtml(doc.filename || doc.file_name || '')}</td>
                        <td>${escapeHtml(doc.category || doc.doc_category || '')}</td>
                        <td>${doc.size ? (doc.size / 1024).toFixed(1) + ' KB' : '—'}</td>
                        <td>${formatDate(doc.uploaded_at || doc.uploaded || doc.created_at)}</td>
                        <td class="actions">
                            <a class="btn btn-sm btn-secondary" href="/api/employees/${id}/documents/${doc.id}" target="_blank">Download</a>
                            <button class="btn btn-sm btn-danger" onclick="EmployeesPage._deleteDocument(${id}, ${doc.id})">Delete</button>
                        </td>
                    </tr>`;
                }
                html += '</tbody></table>';
            }
            html += `
                <div id="emp-doc-dropzone-${id}"
                     style="margin-top:12px;border:2px dashed #c0c8d0;border-radius:6px;padding:18px;text-align:center;color:#666;font-size:13px;background:#fafbfc;cursor:pointer"
                     onclick="document.getElementById('doc-file-${id}').click()">
                    Drag a file here, or click to browse
                </div>
                <button class="btn btn-sm btn-secondary" style="margin-top:8px" onclick="EmployeesPage._showUploadForm(${id})">+ Upload Document (with category)</button>
                <div id="emp-doc-form-${id}" style="display:none;margin-top:12px">
                    <div class="form-grid">
                        <div class="form-group"><label>Category</label>
                            <input id="doc-category-${id}" placeholder="e.g. I-9, W-4, Offer Letter"></div>
                        <div class="form-group"><label>File</label>
                            <input id="doc-file-${id}" type="file"></div>
                    </div>
                    <div class="form-actions">
                        <button class="btn btn-secondary btn-sm" onclick="document.getElementById('emp-doc-form-${id}').style.display='none'">Cancel</button>
                        <button class="btn btn-primary btn-sm" onclick="EmployeesPage._uploadDocument(${id})">Upload</button>
                    </div>
                </div>`;
            el.innerHTML = html;
            EmployeesPage._wireDropzone(id);
        } catch (err) {
            el.innerHTML = `<p class="text-muted">Documents unavailable: ${escapeHtml(err.message)}</p>`;
        }
    },

    _showUploadForm(id) {
        const formEl = document.getElementById(`emp-doc-form-${id}`);
        if (formEl) formEl.style.display = 'block';
    },

    _wireDropzone(id) {
        const dz = document.getElementById(`emp-doc-dropzone-${id}`);
        const hidden = document.getElementById(`doc-file-${id}`);
        if (!dz || !hidden) return;

        const highlight = (on) => {
            dz.style.background = on ? '#eef4fb' : '#fafbfc';
            dz.style.borderColor = on ? '#336699' : '#c0c8d0';
        };

        // The hidden file input still drives the manual upload path, so a
        // click-to-browse falls back to the same uploader as the form.
        hidden.addEventListener('change', () => {
            if (hidden.files && hidden.files.length) {
                EmployeesPage._uploadDroppedFile(id, hidden.files[0]);
                hidden.value = '';
            }
        });

        ['dragenter', 'dragover'].forEach(evt =>
            dz.addEventListener(evt, e => { e.preventDefault(); highlight(true); })
        );
        ['dragleave', 'drop'].forEach(evt =>
            dz.addEventListener(evt, e => { e.preventDefault(); highlight(false); })
        );
        dz.addEventListener('drop', e => {
            const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
            if (file) EmployeesPage._uploadDroppedFile(id, file);
        });
    },

    async _uploadDroppedFile(empId, file) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('doc_category', 'general');
        try {
            const res = await fetch(`/api/employees/${empId}/documents`, {
                method: 'POST',
                body: formData,
                credentials: 'same-origin',
            });
            if (!res.ok) throw new Error((await res.json()).detail || 'Upload failed');
            toast(`Uploaded ${file.name}`);
            EmployeesPage._loadDocuments(empId);
        } catch (err) {
            toast(err.message || 'Upload failed', 'error');
        }
    },

    async _uploadDocument(id) {
        const fileInput = document.getElementById(`doc-file-${id}`);
        const categoryInput = document.getElementById(`doc-category-${id}`);
        if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
            toast('Please select a file', 'error');
            return;
        }
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        formData.append('doc_category', categoryInput ? categoryInput.value : '');
        try {
            const res = await fetch(`/api/employees/${id}/documents`, {
                method: 'POST',
                credentials: 'same-origin',
                body: formData
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({ message: 'Upload failed' }));
                throw new Error(err.message || 'Upload failed');
            }
            toast('Document uploaded');
            EmployeesPage._loadDocuments(id);
        } catch (err) {
            toast(err.message, 'error');
        }
    },

    async _deleteDocument(empId, docId) {
        if (!confirm('Delete this document?')) return;
        try {
            await API.del(`/employees/${empId}/documents/${docId}`);
            toast('Document deleted');
            EmployeesPage._loadDocuments(empId);
        } catch (err) {
            toast(err.message, 'error');
        }
    },
};
