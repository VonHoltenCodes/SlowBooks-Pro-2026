/**
 * A nod to QBW32.EXE!CPreferencesDialog  imagined offset: 0x0023F800
 * Original: tabbed dialog (IDD_PREFERENCES) with 12 tabs. We condensed
 * everything into a single page because nobody needs 12 tabs for
 * company name and tax rate. The registry writes at 0x00240200 are now
 * PostgreSQL INSERTs. Progress.
 */
const SettingsPage = {
    async render() {
        const s = await API.get('/settings');
        setTimeout(() => {
            SettingsPage.loadBackups();
            SettingsPage.loadEmailTemplates();
            SettingsPage.loadAiConfig();
            SettingsPage.scrollToFocus();
        }, 0);
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

                <div class="settings-section" id="settings-ai">
                    <h3>AI Insights</h3>
                    <div style="font-size:10px; color:var(--text-muted); margin-bottom:8px;">
                        Bring-your-own-key access to xAI Grok, Groq, Cloudflare Workers AI, Anthropic Claude, OpenAI, or Google Gemini.
                        Used by the Analytics dashboard to generate observations, risks, and recommendations.
                        API keys are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256).
                    </div>
                    <div id="ai-config-container" class="ai-settings-form">
                        <div style="font-size:11px; color:var(--text-muted);">Loading…</div>
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

    // ----- AI Insights config (provider/model/key/etc.) ------------------
    // Backed by /api/analytics/ai-config (GET/PUT) and
    // /api/analytics/ai-config/test (POST). Same endpoints the Analytics
    // page's gear button used to call from a modal — now consolidated here
    // so AI config is discoverable without opening Analytics.

    aiConfigState: null,

    async loadAiConfig() {
        const host = document.getElementById('ai-config-container');
        if (!host) return;
        try {
            const cfg = await API.get('/analytics/ai-config');
            SettingsPage.aiConfigState = cfg;
            host.innerHTML = SettingsPage._renderAiConfig(cfg);
            SettingsPage._wireAiConfig(cfg);
        } catch (err) {
            host.innerHTML =
                `<div style="font-size:11px; color:var(--danger,#c00);">` +
                `Failed to load AI config: ${escapeHtml(err.message || String(err))}</div>`;
        }
    },

    _renderAiConfig(cfg) {
        const providers = cfg.providers || [];
        const currentProvider =
            cfg.provider || (providers[0] && providers[0].key) || '';
        const currentSpec =
            providers.find(p => p.key === currentProvider) || providers[0] || {};
        const needsAccount = !!currentSpec.needs_account_id;
        const needsWorker = !!currentSpec.needs_worker_url;
        const hasKey = !!cfg.has_api_key;
        const currentModel = cfg.model || '';

        const providerOptions = providers.map(p =>
            `<option value="${escapeHtml(p.key)}"${p.key === currentProvider ? ' selected' : ''}>` +
            `${escapeHtml(p.label)}</option>`
        ).join('');

        return `
            <label class="form-field">
                <span>Provider</span>
                <select id="ai-settings-provider">${providerOptions}</select>
            </label>
            <div id="ai-settings-hint" class="ai-settings-hint">
                ${escapeHtml(currentSpec.free_tier_hint || '')}
                ${currentSpec.docs_url ? ` &middot; <a href="${escapeHtml(currentSpec.docs_url)}" target="_blank" rel="noopener">Get a key</a>` : ''}
            </div>
            <label class="form-field">
                <span>Model</span>
                <select id="ai-settings-model-select">
                    ${SettingsPage._modelOptionsHtml(currentSpec, currentModel)}
                </select>
                <input type="text" id="ai-settings-model-custom"
                       value="${escapeHtml(currentModel || '')}"
                       placeholder="Type a model ID"
                       style="margin-top:6px; ${SettingsPage._isCustomModel(currentSpec, currentModel) ? '' : 'display:none;'}">
            </label>
            <label class="form-field" id="ai-settings-cf-wrap" style="${needsAccount ? '' : 'display:none'}">
                <span>Cloudflare Account ID</span>
                <input type="text" id="ai-settings-cf-account"
                       value="${escapeHtml(cfg.cloudflare_account_id || '')}"
                       placeholder="32-char hex (from dash.cloudflare.com)">
            </label>
            <fieldset id="ai-settings-worker-wrap" class="ai-worker-section"
                      style="${needsWorker ? '' : 'display:none'}">
                <legend>Cloudflare Worker Gateway</legend>
                <p class="ai-worker-help">
                    Deploy <code>cloudflare/worker.js</code> in your own
                    Cloudflare account — the real AI credentials live inside
                    Cloudflare as a Worker secret, not in Slowbooks' database.
                    Slowbooks only holds the shared Bearer token. See
                    <code>cloudflare/README.md</code> for the 5-minute setup.
                </p>
                <label class="form-field">
                    <span>Worker URL <em class="ai-worker-required">(https only)</em></span>
                    <input type="url" id="ai-settings-worker-url"
                           value="${escapeHtml(cfg.worker_url || '')}"
                           placeholder="https://slowbooks-ai.yourname.workers.dev/v1/chat/completions"
                           autocomplete="off" spellcheck="false">
                </label>
                <p class="ai-worker-security">
                    <strong>Security:</strong> only <code>https://</code> URLs
                    are accepted; private/loopback IPs, embedded credentials,
                    and non-HTTPS schemes are rejected. Redirects are disabled
                    and TLS certificates are always verified.
                </p>
            </fieldset>
            <label class="form-field">
                <span>API Key / Shared Secret ${hasKey ? '<em class="ai-key-saved">(saved &#10003;)</em>' : ''}</span>
                <input type="password" id="ai-settings-key"
                       placeholder="${hasKey ? 'Leave blank to keep existing' : 'Paste key or openssl rand -hex 32'}"
                       autocomplete="new-password">
            </label>
            <div class="ai-settings-buttons">
                <button type="button" class="btn btn-secondary btn-sm" id="ai-settings-test">Test</button>
                <span id="ai-settings-test-result" class="ai-settings-test-result"></span>
                <div class="ai-settings-spacer"></div>
                <button type="button" class="btn btn-primary btn-sm" id="ai-settings-save">Save</button>
            </div>
        `;
    },

    // True when the saved model isn't in the curated list — the dropdown
    // should show "Custom…" pre-selected and reveal the text input.
    _isCustomModel(spec, model) {
        if (!model) return false;
        const choices = (spec && spec.model_choices) || [];
        return choices.indexOf(model) === -1;
    },

    _modelOptionsHtml(spec, currentModel) {
        const choices = (spec && spec.model_choices) || [];
        const isCustom = SettingsPage._isCustomModel(spec, currentModel);
        // Default to default_model if no choice saved yet; otherwise echo
        // the saved one (or pick Custom if it's not in the list).
        const selected = currentModel || (spec && spec.default_model) || '';
        const opts = choices.map(m =>
            `<option value="${escapeHtml(m)}"${m === selected && !isCustom ? ' selected' : ''}>${escapeHtml(m)}</option>`
        ).join('');
        return opts +
            `<option value="__custom__"${isCustom ? ' selected' : ''}>Custom…</option>`;
    },

    _wireAiConfig(cfg) {
        const providers = cfg.providers || [];
        const providerSel = document.getElementById('ai-settings-provider');
        const hintEl = document.getElementById('ai-settings-hint');
        const modelSel = document.getElementById('ai-settings-model-select');
        const modelCustom = document.getElementById('ai-settings-model-custom');
        const cfWrap = document.getElementById('ai-settings-cf-wrap');
        const workerWrap = document.getElementById('ai-settings-worker-wrap');
        const saveBtn = document.getElementById('ai-settings-save');
        const testBtn = document.getElementById('ai-settings-test');
        const testRes = document.getElementById('ai-settings-test-result');

        if (!providerSel) return; // render failed; nothing to wire

        // Show the custom text input only when "Custom…" is selected.
        const syncCustomVisibility = () => {
            modelCustom.style.display =
                modelSel.value === '__custom__' ? '' : 'none';
        };
        modelSel.addEventListener('change', syncCustomVisibility);

        providerSel.addEventListener('change', () => {
            const spec = providers.find(p => p.key === providerSel.value) || {};
            hintEl.innerHTML =
                escapeHtml(spec.free_tier_hint || '') +
                (spec.docs_url
                    ? ` &middot; <a href="${escapeHtml(spec.docs_url)}" target="_blank" rel="noopener">Get a key</a>`
                    : '');
            // Repopulate model dropdown for the new provider — the old
            // provider's options aren't valid for this one. Reset custom
            // input too so we don't carry a stale model ID over.
            modelSel.innerHTML =
                SettingsPage._modelOptionsHtml(spec, spec.default_model || '');
            modelCustom.value = '';
            syncCustomVisibility();
            cfWrap.style.display = spec.needs_account_id ? '' : 'none';
            workerWrap.style.display = spec.needs_worker_url ? '' : 'none';
        });

        const resolveModel = () => {
            if (modelSel.value === '__custom__') return modelCustom.value.trim();
            return modelSel.value;
        };

        const collectPayload = () => ({
            provider: providerSel.value,
            model: resolveModel(),
            cloudflare_account_id: document.getElementById('ai-settings-cf-account').value.trim(),
            worker_url: document.getElementById('ai-settings-worker-url').value.trim(),
            api_key: document.getElementById('ai-settings-key').value,
        });

        saveBtn.addEventListener('click', async () => {
            try {
                const updated = await API.put('/analytics/ai-config', collectPayload());
                SettingsPage.aiConfigState = updated;
                toast('AI settings saved', 'success');
                // Re-render to reflect "(saved ✓)" state and clear the key input
                SettingsPage.loadAiConfig();
            } catch (err) {
                toast('Save failed: ' + (err.message || err), 'error');
            }
        });

        testBtn.addEventListener('click', async () => {
            // Save first so the test uses any just-entered key, then call /test.
            testRes.textContent = 'Saving…';
            testRes.className = 'ai-settings-test-result';
            try {
                await API.put('/analytics/ai-config', collectPayload());
            } catch (err) {
                testRes.textContent = 'Save failed: ' + (err.message || err);
                testRes.classList.add('ai-test-fail');
                return;
            }
            testRes.textContent = 'Testing…';
            try {
                const res = await API.post('/analytics/ai-config/test', {});
                testRes.textContent = `✓ ${res.provider_label} replied: "${res.reply}"`;
                testRes.classList.add('ai-test-ok');
            } catch (err) {
                testRes.textContent = '✗ ' + (err.message || err);
                testRes.classList.add('ai-test-fail');
            }
        });
    },

    // Honors a sessionStorage hint from other pages (e.g., Analytics' gear)
    // requesting that the Settings page open scrolled to a specific section.
    scrollToFocus() {
        const target = sessionStorage.getItem('settings_focus');
        if (!target) return;
        sessionStorage.removeItem('settings_focus');
        const el = document.getElementById(target);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    },
};
