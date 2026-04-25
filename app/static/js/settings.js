/**
 * Decompiled from QBW32.EXE!CPreferencesDialog  Offset: 0x0023F800
 * Original: tabbed dialog (IDD_PREFERENCES) with 12 tabs. We condensed
 * everything into a single page because nobody needs 12 tabs for
 * company name and tax rate. The registry writes at 0x00240200 are now
 * PostgreSQL INSERTs. Progress.
 */
const SettingsPage = {
    async render() {
        const s = await API.get('/settings');
        setTimeout(() => { SettingsPage.loadBackups(); SettingsPage.loadEmailTemplates(); }, 0);
        return `
            <div class="page-header">
                <h2>Company Settings</h2>
                <div style="font-size:10px; color:var(--text-muted);">
                    CPreferencesDialog — IDD_PREFERENCES @ 0x0023F800
                </div>
            </div>
            <form id="settings-form" onsubmit="SettingsPage.save(event)">
                <div class="settings-section">
                    <h3>Company Information</h3>
                    <div class="form-grid">
                        <div class="form-group full-width"><label>Company Name *</label>
                            <input name="company_name" value="${escapeHtml(s.company_name || '')}" required></div>
                        <div class="form-group"><label>Address Line 1</label>
                            <input name="company_address1" value="${escapeHtml(s.company_address1 || '')}"></div>
                        <div class="form-group"><label>Address Line 2</label>
                            <input name="company_address2" value="${escapeHtml(s.company_address2 || '')}"></div>
                        <div class="form-group"><label>City</label>
                            <input name="company_city" value="${escapeHtml(s.company_city || '')}"></div>
                        <div class="form-group"><label>State</label>
                            <input name="company_state" value="${escapeHtml(s.company_state || '')}"></div>
                        <div class="form-group"><label>ZIP</label>
                            <input name="company_zip" value="${escapeHtml(s.company_zip || '')}"></div>
                        <div class="form-group"><label>Phone</label>
                            <input name="company_phone" value="${escapeHtml(s.company_phone || '')}"></div>
                        <div class="form-group"><label>Email</label>
                            <input name="company_email" type="email" value="${escapeHtml(s.company_email || '')}"></div>
                        <div class="form-group"><label>Website</label>
                            <input name="company_website" value="${escapeHtml(s.company_website || '')}"></div>
                        <div class="form-group"><label>Tax ID / EIN</label>
                            <input name="company_tax_id" value="${escapeHtml(s.company_tax_id || '')}"></div>
                    </div>
                </div>

                <div class="settings-section">
                    <h3>Company Logo</h3>
                    <div class="form-grid">
                        <div class="form-group">
                            ${s.company_logo_path ? `<img src="${escapeHtml(s.company_logo_path)}" style="max-width:200px; max-height:80px; margin-bottom:8px; display:block;">` : ''}
                            <input type="file" id="logo-upload" accept="image/*" onchange="SettingsPage.uploadLogo(this)">
                            <div style="font-size:10px; color:var(--text-muted); margin-top:4px;">PNG, JPG, GIF, WebP, or SVG &middot; max 5 MB &middot; 200&times;80 px recommended.</div>
                        </div>
                    </div>
                </div>

                <div class="settings-section">
                    <h3>Invoice Defaults</h3>
                    <div class="form-grid">
                        <div class="form-group"><label>Default Terms</label>
                            <select name="default_terms">
                                ${['Net 15','Net 30','Net 45','Net 60','Due on Receipt'].map(t =>
                                    `<option ${s.default_terms===t?'selected':''}>${t}</option>`).join('')}
                            </select></div>
                        <div class="form-group"><label>Default Tax Rate (%)</label>
                            <input name="default_tax_rate" type="number" step="0.01" value="${s.default_tax_rate || '0.0'}"></div>
                        <div class="form-group"><label>Invoice Prefix</label>
                            <input name="invoice_prefix" value="${escapeHtml(s.invoice_prefix || '')}" placeholder="e.g. INV-"></div>
                        <div class="form-group"><label>Next Invoice #</label>
                            <input name="invoice_next_number" value="${escapeHtml(s.invoice_next_number || '1001')}"></div>
                        <div class="form-group"><label>Estimate Prefix</label>
                            <input name="estimate_prefix" value="${escapeHtml(s.estimate_prefix || '')}" placeholder="e.g. E-"></div>
                        <div class="form-group"><label>Next Estimate #</label>
                            <input name="estimate_next_number" value="${escapeHtml(s.estimate_next_number || '1001')}"></div>
                        <div class="form-group full-width"><label>Default Invoice Notes</label>
                            <textarea name="invoice_notes">${escapeHtml(s.invoice_notes || '')}</textarea></div>
                        <div class="form-group full-width"><label>Invoice Footer</label>
                            <input name="invoice_footer" value="${escapeHtml(s.invoice_footer || '')}"></div>
                    </div>
                </div>

                <div class="settings-section">
                    <h3>Closing Date</h3>
                    <div style="font-size:10px; color:var(--text-muted); margin-bottom:8px;">
                        Prevent modifications to transactions before this date.
                    </div>
                    <div class="form-grid">
                        <div class="form-group"><label>Closing Date</label>
                            <input name="closing_date" type="date" value="${escapeHtml(s.closing_date || '')}"></div>
                        <div class="form-group"><label>Password (optional)</label>
                            <input name="closing_date_password" type="password" value="${escapeHtml(s.closing_date_password || '')}"
                                placeholder="Leave blank for no password"></div>
                    </div>
                </div>

                <div class="settings-section">
                    <h3>Email (SMTP)</h3>
                    <div style="font-size:10px; color:var(--text-muted); margin-bottom:8px;">
                        Configure SMTP for sending invoices by email.
                    </div>
                    <div class="form-grid">
                        <div class="form-group"><label>SMTP Host</label>
                            <input name="smtp_host" value="${escapeHtml(s.smtp_host || '')}" placeholder="smtp.gmail.com"></div>
                        <div class="form-group"><label>SMTP Port</label>
                            <input name="smtp_port" type="number" value="${escapeHtml(s.smtp_port || '587')}"></div>
                        <div class="form-group"><label>Username</label>
                            <input name="smtp_user" value="${escapeHtml(s.smtp_user || '')}"></div>
                        <div class="form-group"><label>Password</label>
                            <input name="smtp_password" type="password" value="${escapeHtml(s.smtp_password || '')}"></div>
                        <div class="form-group"><label>From Email</label>
                            <input name="smtp_from_email" type="email" value="${escapeHtml(s.smtp_from_email || '')}"></div>
                        <div class="form-group"><label>From Name</label>
                            <input name="smtp_from_name" value="${escapeHtml(s.smtp_from_name || '')}"></div>
                        <div class="form-group"><label>Use TLS</label>
                            <select name="smtp_use_tls">
                                <option value="true" ${s.smtp_use_tls !== 'false' ? 'selected' : ''}>Yes</option>
                                <option value="false" ${s.smtp_use_tls === 'false' ? 'selected' : ''}>No</option>
                            </select></div>
                    </div>
                    <button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.testEmail()" style="margin-top:8px;">
                        Send Test Email</button>
                </div>

                <div class="settings-section">
                    <h3>Online Payments (Stripe)</h3>
                    <div style="font-size:10px; color:var(--text-muted); margin-bottom:8px;">
                        Accept online payments via Stripe Checkout. Customers can pay invoices directly from emailed links.
                    </div>
                    <div class="form-grid">
                        <div class="form-group"><label>Enable Online Payments</label>
                            <select name="stripe_enabled">
                                <option value="false" ${s.stripe_enabled !== 'true' ? 'selected' : ''}>Disabled</option>
                                <option value="true" ${s.stripe_enabled === 'true' ? 'selected' : ''}>Enabled</option>
                            </select></div>
                        <div class="form-group"><label>Publishable Key</label>
                            <input name="stripe_publishable_key" value="${escapeHtml(s.stripe_publishable_key || '')}" placeholder="pk_..."></div>
                        <div class="form-group"><label>Secret Key</label>
                            <input name="stripe_secret_key" type="password" value="${escapeHtml(s.stripe_secret_key || '')}" placeholder="sk_..."></div>
                        <div class="form-group"><label>Webhook Secret</label>
                            <input name="stripe_webhook_secret" type="password" value="${escapeHtml(s.stripe_webhook_secret || '')}" placeholder="whsec_..."></div>
                    </div>
                </div>

                <div class="settings-section">
                    <h3>QuickBooks Online</h3>
                    <div style="font-size:10px; color:var(--text-muted); margin-bottom:8px;">
                        Configure your Intuit Developer app credentials for QBO integration.
                        Get these from <a href="https://developer.intuit.com" target="_blank" style="color:var(--qb-blue);">developer.intuit.com</a>.
                    </div>
                    <div class="form-grid">
                        <div class="form-group"><label>Enable QBO Integration</label>
                            <select name="qbo_enabled">
                                <option value="false" ${s.qbo_enabled !== 'true' ? 'selected' : ''}>Disabled</option>
                                <option value="true" ${s.qbo_enabled === 'true' ? 'selected' : ''}>Enabled</option>
                            </select></div>
                        <div class="form-group"><label>Environment</label>
                            <select name="qbo_environment">
                                <option value="sandbox" ${s.qbo_environment !== 'production' ? 'selected' : ''}>Sandbox</option>
                                <option value="production" ${s.qbo_environment === 'production' ? 'selected' : ''}>Production</option>
                            </select></div>
                        <div class="form-group"><label>Client ID</label>
                            <input name="qbo_client_id" value="${escapeHtml(s.qbo_client_id || '')}" placeholder="ABo8gw..."></div>
                        <div class="form-group"><label>Client Secret</label>
                            <input name="qbo_client_secret" type="password" value="${escapeHtml(s.qbo_client_secret || '')}" placeholder="tJCdgW..."></div>
                        <div class="form-group full-width"><label>Redirect URI</label>
                            <input name="qbo_redirect_uri" value="${escapeHtml(s.qbo_redirect_uri || 'http://localhost:8000/api/qbo/callback')}"
                                placeholder="http://localhost:8000/api/qbo/callback"></div>
                    </div>
                </div>

                <div class="settings-section">
                    <h3>Late Fees</h3>
                    <div style="font-size:10px; color:var(--text-muted); margin-bottom:8px;">
                        Automatically apply late fees to overdue invoices. Use "Apply Late Fees" on the AR Aging report.
                    </div>
                    <div class="form-grid">
                        <div class="form-group"><label>Enable Late Fees</label>
                            <select name="late_fee_enabled">
                                <option value="false" ${s.late_fee_enabled !== 'true' ? 'selected' : ''}>Disabled</option>
                                <option value="true" ${s.late_fee_enabled === 'true' ? 'selected' : ''}>Enabled</option>
                            </select></div>
                        <div class="form-group"><label>Late Fee Rate (%)</label>
                            <input name="late_fee_rate" type="number" step="0.1" value="${escapeHtml(s.late_fee_rate || '1.5')}"></div>
                        <div class="form-group"><label>Grace Days</label>
                            <input name="late_fee_grace_days" type="number" value="${escapeHtml(s.late_fee_grace_days || '15')}"></div>
                    </div>
                </div>

                <div class="settings-section">
                    <h3>Email Templates</h3>
                    <div style="font-size:10px; color:var(--text-muted); margin-bottom:8px;">
                        Customize email templates for invoices, payment receipts, and collection notices.
                        Templates use Jinja2 syntax. Available variables: {{ invoice }}, {{ customer_name }}, {{ company }}, {{ pay_url }}.
                    </div>
                    <div style="display:flex; gap:8px; margin-bottom:12px;">
                        <button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.seedTemplates()">Seed Default Templates</button>
                    </div>
                    <div id="email-template-list"></div>
                </div>

                <div class="settings-section">
                    <h3>Backup / Restore</h3>
                    <div style="display:flex; gap:8px; margin-bottom:12px;">
                        <button type="button" class="btn btn-primary" onclick="SettingsPage.createBackup()">Create Backup</button>
                    </div>
                    <div id="backup-list"></div>
                </div>

                <div class="form-actions">
                    <button type="submit" class="btn btn-primary">Save Settings</button>
                </div>
            </form>`;
    },

    async save(e) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        // Remove file input from data
        delete data.file;
        try {
            await API.put('/settings', data);
            toast('Settings saved');
        } catch (err) {
            toast(err.message, 'error');
        }
    },

    async uploadLogo(input) {
        if (!input.files[0]) return;
        const formData = new FormData();
        formData.append('file', input.files[0]);
        try {
            const resp = await fetch('/api/uploads/logo', { method: 'POST', body: formData });
            // Parse JSON defensively — a reverse-proxy or framework error can
            // return a non-JSON body, and the raw SyntaxError ("Unexpected
            // token <") confuses end users worse than the actual problem.
            let data = null;
            try { data = await resp.json(); }
            catch (_) { data = null; }
            if (!resp.ok) {
                const msg = (data && data.detail) ||
                    `Upload failed (HTTP ${resp.status}). The file may be too large or the server returned an unexpected response.`;
                throw new Error(msg);
            }
            toast('Logo uploaded');
            App.navigate('#/settings');
        } catch (err) { toast(err.message, 'error'); }
    },

    async testEmail() {
        try {
            await API.post('/settings/test-email');
            toast('Test email sent');
        } catch (err) { toast(err.message, 'error'); }
    },

    async createBackup() {
        try {
            const result = await API.post('/backups');
            toast(`Backup created: ${result.filename}`);
            SettingsPage.loadBackups();
        } catch (err) { toast(err.message, 'error'); }
    },

    async loadBackups() {
        try {
            const backups = await API.get('/backups');
            const el = $('#backup-list');
            if (!el) return;
            if (backups.length === 0) {
                el.innerHTML = '<div style="font-size:11px; color:var(--text-muted);">No backups yet.</div>';
                return;
            }
            el.innerHTML = `<div class="table-container"><table>
                <thead><tr><th>Filename</th><th>Size</th><th>Created</th><th>Actions</th></tr></thead>
                <tbody>${backups.map(b => `<tr>
                    <td>${escapeHtml(b.filename)}</td>
                    <td>${(b.file_size / 1024).toFixed(1)} KB</td>
                    <td>${formatDate(b.created_at)}</td>
                    <td class="actions">
                        <a href="/api/backups/download/${encodeURIComponent(b.filename)}" class="btn btn-sm btn-secondary" download>Download</a>
                    </td>
                </tr>`).join('')}</tbody>
            </table></div>`;
        } catch (e) { /* ignore */ }
    },

    async seedTemplates() {
        try {
            const result = await API.post('/email-templates/seed-defaults');
            toast(`Created ${result.created} default templates`);
            SettingsPage.loadEmailTemplates();
        } catch (err) { toast(err.message, 'error'); }
    },

    async loadEmailTemplates() {
        try {
            const templates = await API.get('/email-templates');
            const el = $('#email-template-list');
            if (!el) return;
            if (templates.length === 0) {
                el.innerHTML = '<div style="font-size:11px; color:var(--text-muted);">No templates. Click "Seed Default Templates" to create them.</div>';
                return;
            }
            el.innerHTML = `<div class="table-container"><table>
                <thead><tr><th>Name</th><th>Type</th><th>Subject</th><th>Actions</th></tr></thead>
                <tbody>${templates.map(t => `<tr>
                    <td><strong>${escapeHtml(t.name)}</strong></td>
                    <td>${escapeHtml(t.template_type)}</td>
                    <td style="font-size:11px;">${escapeHtml(t.subject_template)}</td>
                    <td class="actions">
                        <button class="btn btn-sm btn-secondary" onclick="SettingsPage.editTemplate(${t.id})">Edit</button>
                    </td>
                </tr>`).join('')}</tbody>
            </table></div>`;
        } catch (e) { /* ignore */ }
    },

    async editTemplate(id) {
        const t = await API.get(`/email-templates/${id}`);
        openModal('Edit Email Template', `
            <form onsubmit="SettingsPage.saveTemplate(event, ${id})">
                <div class="form-grid">
                    <div class="form-group"><label>Name</label>
                        <input name="name" value="${escapeHtml(t.name)}" readonly style="background:var(--gray-100);"></div>
                    <div class="form-group"><label>Type</label>
                        <input name="template_type" value="${escapeHtml(t.template_type)}" readonly style="background:var(--gray-100);"></div>
                    <div class="form-group full-width"><label>Subject Template</label>
                        <input name="subject_template" value="${escapeHtml(t.subject_template)}"></div>
                    <div class="form-group full-width"><label>Body Template (HTML + Jinja2)</label>
                        <textarea name="body_template" rows="10" style="font-family:monospace; font-size:11px;">${escapeHtml(t.body_template)}</textarea></div>
                </div>
                <div style="font-size:10px; color:var(--text-muted); margin:8px 0;">
                    Variables: {{ invoice.invoice_number }}, {{ invoice.total }}, {{ invoice.due_date }}, {{ customer_name }},
                    {{ company.company_name }}, {{ pay_url }}, {{ amount }}. Filters: | currency, | fdate
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save Template</button>
                </div>
            </form>`);
    },

    async saveTemplate(e, id) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        try {
            await API.put(`/email-templates/${id}`, { subject_template: data.subject_template, body_template: data.body_template });
            toast('Template saved');
            closeModal();
            SettingsPage.loadEmailTemplates();
        } catch (err) { toast(err.message, 'error'); }
    },
};
