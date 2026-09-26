/**
 * Settings — QuickBooks 2003 had a 12-tab preferences dialog; we
 * condensed everything into a single page because nobody needs 12 tabs
 * for company name and tax rate.
 */
const SettingsPage = {
    async render() {
        const s = await API.get('/settings');
        setTimeout(() => {
            SettingsPage.loadBackups();
            SettingsPage.loadEmailTemplates();
            SettingsPage.loadAiConfig();
            SettingsPage.loadClasses();
            SettingsPage.loadCostCodes();
            SettingsPage.loadCostTypes();
            SettingsPage.loadEquipment();
            SettingsPage.loadUsers();
            SettingsPage.loadApiTokens();
            SettingsPage.loadSignInPref();
            SettingsPage.loadOcrStatus();
            SettingsPage.loadOcrEnginePref();
            SettingsPage.scrollToFocus();
            SettingsPage._installLeaveGuard();
            SettingsPage._markClean();
        }, 0);
        return `
            <div class="page-header">
                <h2>Company Settings</h2>
            </div>
            <form id="settings-form" onsubmit="SettingsPage.save(event)"
                oninput="SettingsPage._updateDirty()" onchange="SettingsPage._updateDirty()">
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
                        <div class="form-group full-width"><label for="company-type">Company Type</label>
                            <select id="company-type" name="company_type" onchange="SettingsPage.changeCompanyType(this)">
                                <option value="business" ${s.company_type !== 'nonprofit' ? 'selected' : ''}>Business</option>
                                <option value="nonprofit" ${s.company_type === 'nonprofit' ? 'selected' : ''}>Nonprofit</option>
                            </select>
                            <div style="font-size:10px; color:var(--text-muted); margin-top:4px;">Nonprofit shows donors, pledges, donations and funds in place of customers, invoices, sales receipts and classes, and adds the net-asset accounts and statements. Your data does not change; switch back any time.</div>
                        </div>
                    </div>
                </div>

                <div class="settings-section">
                    <h3>Company Logo</h3>
                    <div class="form-grid">
                        <div class="form-group">
                            ${s.company_logo_path ? `<img id="company-logo-preview" src="${escapeHtml(s.company_logo_path)}" style="max-width:200px; max-height:80px; margin-bottom:8px; display:block;">` : ''}
                            <input type="file" id="logo-upload" accept="image/*" onchange="SettingsPage.uploadLogo(this)">
                            <div style="font-size:10px; color:var(--text-muted); margin-top:4px;">PNG, JPG, GIF, WebP, or SVG &middot; max 5 MB &middot; 200&times;80 px recommended.</div>
                        </div>
                    </div>
                </div>

                <div class="settings-section">
                    <h3>${T('Invoice')} Defaults</h3>
                    <div class="form-grid">
                        <div class="form-group"><label>Default Terms</label>
                            <select name="default_terms">
                                ${['Net 15','Net 30','Net 45','Net 60','Due on Receipt'].map(t =>
                                    `<option ${s.default_terms===t?'selected':''}>${t}</option>`).join('')}
                            </select></div>
                        <div class="form-group"><label>Default Tax Rate (%)</label>
                            <input name="default_tax_rate" type="number" min="0" max="100" step="0.01"
                                title="A percent from 0 to 100: 8.25 means 8.25%"
                                value="${escapeHtml(s.default_tax_rate || '0.0')}"></div>
                        <div class="form-group"><label>${`${T('Invoice')} Prefix`}</label>
                            <input name="invoice_prefix" value="${escapeHtml(s.invoice_prefix || '')}" placeholder="e.g. INV-"></div>
                        <div class="form-group"><label>${`Next ${T('Invoice')} #`}</label>
                            <input name="invoice_next_number" inputmode="numeric" pattern="[0-9]*[1-9][0-9]*" required
                                title="A whole number, 1 or more"
                                value="${escapeHtml(s.invoice_next_number || '1001')}"></div>
                        <div class="form-group"><label>Estimate Prefix</label>
                            <input name="estimate_prefix" value="${escapeHtml(s.estimate_prefix || '')}" placeholder="e.g. E-"></div>
                        <div class="form-group"><label>Next Estimate #</label>
                            <input name="estimate_next_number" inputmode="numeric" pattern="[0-9]*[1-9][0-9]*" required
                                title="A whole number, 1 or more"
                                value="${escapeHtml(s.estimate_next_number || '1001')}"></div>
                        <div class="form-group full-width"><label>${`Default ${T('Invoice')} Notes`}</label>
                            <textarea name="invoice_notes">${escapeHtml(s.invoice_notes || '')}</textarea></div>
                        <div class="form-group full-width"><label>${`${T('Invoice')} Footer`}</label>
                            <input name="invoice_footer" value="${escapeHtml(s.invoice_footer || '')}"></div>
                        <div class="form-group"><label>Report PDF Paper Size</label>
                            <select name="pdf_paper_size">
                                <option value="letter" ${s.pdf_paper_size !== 'a4' ? 'selected' : ''}>US Letter</option>
                                <option value="a4" ${s.pdf_paper_size === 'a4' ? 'selected' : ''}>A4</option>
                            </select></div>
                    </div>
                </div>

                <!-- Desktop app only (shown by loadSignInPref); the session used
                     to outlive the app, so a relaunch reopened the company
                     without its password (explore 2.17.3, macbase1 S-j). -->
                <div class="settings-section" id="settings-sign-in" hidden>
                    <h3>Sign-in</h3>
                    <div class="form-grid">
                        <div class="form-group full-width">
                            <label for="ask-password-on-start">Ask for the password each time SlowBooks Pro starts</label>
                            <select id="ask-password-on-start" name="ask_password_on_start">
                                <option value="false" ${s.ask_password_on_start !== 'true' ? 'selected' : ''}>No: stay signed in until you sign out</option>
                                <option value="true" ${s.ask_password_on_start === 'true' ? 'selected' : ''}>Yes: quitting the app signs this company out</option>
                            </select>
                            <div style="font-size:10px; color:var(--text-muted); margin-top:4px;">
                                With Yes, the next start asks for the password even if you did not sign out.
                                On Server Edition, a restart of the server signs everyone out.</div>
                        </div>
                    </div>
                </div>

                <div class="settings-section" id="settings-closing-date">
                    <h3>Closing Date</h3>
                    <div style="font-size:10px; color:var(--text-muted); margin-bottom:8px;">
                        Prevent modifications to transactions before this date.
                    </div>
                    <div class="form-grid">
                        <div class="form-group"><label for="closing-date">Closing Date</label>
                            <div style="display:flex; gap:6px; align-items:center;">
                                <input id="closing-date" name="closing_date" type="date" value="${escapeHtml(s.closing_date || '')}"
                                    aria-describedby="closing-date-state"
                                    oninput="SettingsPage.showClosingState()" onchange="SettingsPage.showClosingState()">
                                <button type="button" class="btn btn-sm btn-secondary" id="closing-date-clear"
                                    onclick="SettingsPage.clearClosingDate()" ${s.closing_date ? '' : 'disabled'}>Clear</button>
                            </div>
                            <!-- An empty date field shows today's date in grey on macOS, which
                                 read as "closed through today" (explore 2.17.3, macbase1 S-i). -->
                            <div id="closing-date-state" role="status" aria-live="polite"
                                style="font-size:11px; margin-top:4px;">${escapeHtml(SettingsPage._closingStateText(s.closing_date))}</div></div>
                        <div class="form-group"><label>Password (optional)</label>
                            <input name="closing_date_password" type="password" value="${escapeHtml(s.closing_date_password || '')}"
                                placeholder="Leave blank for no password" autocomplete="new-password">
                            <div style="font-size:10px; color:var(--text-muted); margin-top:4px;">
                                With a password set, a change dated on or before the closing date asks for it
                                and goes through when it is right. Without one, such changes are refused.</div></div>
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
                            <input name="smtp_port" type="number" min="1" max="65535" step="1" value="${escapeHtml(s.smtp_port || '587')}"></div>
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
                    <h3>Online Payments</h3>
                    <div style="font-size:10px; color:var(--text-muted); margin-bottom:8px;">
                        Accept online payments on emailed invoice links. Enable any combination of
                        providers — the customer pay page shows one button per enabled provider.
                    </div>
                    <h4 style="margin:8px 0 4px; font-size:12px;">Stripe</h4>
                    <div class="form-grid">
                        <div class="form-group"><label>Stripe Payments</label>
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
                    <h4 style="margin:12px 0 4px; font-size:12px;">PayPal</h4>
                    <div class="form-grid">
                        <div class="form-group"><label>PayPal Payments</label>
                            <select name="paypal_enabled">
                                <option value="false" ${s.paypal_enabled !== 'true' ? 'selected' : ''}>Disabled</option>
                                <option value="true" ${s.paypal_enabled === 'true' ? 'selected' : ''}>Enabled</option>
                            </select></div>
                        <div class="form-group"><label>Environment</label>
                            <select name="paypal_environment">
                                <option value="sandbox" ${s.paypal_environment !== 'live' ? 'selected' : ''}>Sandbox</option>
                                <option value="live" ${s.paypal_environment === 'live' ? 'selected' : ''}>Live</option>
                            </select></div>
                        <div class="form-group"><label>Client ID</label>
                            <input name="paypal_client_id" value="${escapeHtml(s.paypal_client_id || '')}"></div>
                        <div class="form-group"><label>Client Secret</label>
                            <input name="paypal_client_secret" type="password" value="${escapeHtml(s.paypal_client_secret || '')}"></div>
                        <div class="form-group"><label>Webhook ID</label>
                            <input name="paypal_webhook_id" value="${escapeHtml(s.paypal_webhook_id || '')}"
                                placeholder="From the PayPal developer dashboard"></div>
                    </div>
                    <h4 style="margin:12px 0 4px; font-size:12px;">Square</h4>
                    <div class="form-grid">
                        <div class="form-group"><label>Square Payments</label>
                            <select name="square_enabled">
                                <option value="false" ${s.square_enabled !== 'true' ? 'selected' : ''}>Disabled</option>
                                <option value="true" ${s.square_enabled === 'true' ? 'selected' : ''}>Enabled</option>
                            </select></div>
                        <div class="form-group"><label>Environment</label>
                            <select name="square_environment">
                                <option value="sandbox" ${s.square_environment !== 'production' ? 'selected' : ''}>Sandbox</option>
                                <option value="production" ${s.square_environment === 'production' ? 'selected' : ''}>Production</option>
                            </select></div>
                        <div class="form-group"><label>Access Token</label>
                            <input name="square_access_token" type="password" value="${escapeHtml(s.square_access_token || '')}"></div>
                        <div class="form-group"><label>Location ID</label>
                            <input name="square_location_id" value="${escapeHtml(s.square_location_id || '')}"></div>
                        <div class="form-group"><label>Webhook Signature Key</label>
                            <input name="square_webhook_signature_key" type="password" value="${escapeHtml(s.square_webhook_signature_key || '')}"></div>
                        <div class="form-group"><label>Webhook Notification URL</label>
                            <input name="square_notification_url" value="${escapeHtml(s.square_notification_url || '')}"
                                placeholder="Exact URL registered in the Square dashboard"></div>
                    </div>
                </div>

                <div class="settings-section">
                    <h3>QuickBooks Online</h3>
                    <div style="font-size:10px; color:var(--text-muted); margin-bottom:8px;">
                        Configure your Intuit Developer app credentials for QBO integration.
                        Get these from <a href="https://developer.intuit.com" target="_blank" style="color:var(--text-link);">developer.intuit.com</a>.
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

                <div class="settings-section" id="settings-ocr">
                    <h3>Receipt Scanning</h3>
                    <div style="font-size:10px; color:var(--text-muted); margin-bottom:8px;">
                        ${Terms.text('Local OCR for the Scan Receipt button on the Enter Sales Receipt and Enter Bill forms.')}
                        Everything runs on this computer — no cloud, no data leaves the machine.
                        The desktop app ships with a built-in engine (Windows OCR / Apple Vision), so scanning
                        works out of the box; Tesseract is an optional extra engine you can install yourself.
                    </div>
                    <div id="ocr-status" style="font-size:12px;">Checking…</div>
                    <div style="margin-top:8px; display:flex; align-items:center; gap:8px;">
                        <label for="ocr-engine-pref" style="font-size:11px;">OCR engine:</label>
                        <select id="ocr-engine-pref" style="font-size:11px;"
                            onchange="SettingsPage.saveOcrEngine(this.value)">
                            <option value="auto">Automatic (recommended) — built-in engine first</option>
                            <option value="tesseract">Prefer Tesseract (if installed)</option>
                        </select>
                    </div>
                    <div style="font-size:10px; color:var(--text-muted); margin-top:4px;">
                        Optional: Tesseract can sharpen box re-reads on faded receipts. Install it with
                        <code>sudo apt-get install tesseract-ocr</code> (Ubuntu),
                        <code>brew install tesseract</code> (macOS), or the
                        UB&nbsp;Mannheim build from
                        <code>github.com/UB-Mannheim/tesseract</code> (Windows), then choose it here.
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
                            <input name="late_fee_rate" type="number" min="0" max="100" step="0.01" value="${escapeHtml(s.late_fee_rate || '1.5')}"></div>
                        <div class="form-group"><label>Grace Days</label>
                            <input name="late_fee_grace_days" type="number" min="0" step="1" value="${escapeHtml(s.late_fee_grace_days || '15')}"></div>
                    </div>
                </div>

                <div class="settings-section">
                    <h3>Email Templates</h3>
                    <div style="font-size:10px; color:var(--text-muted); margin-bottom:8px;">
                        Customize email templates for invoices, payment receipts, and collection notices.
                        Templates use Jinja2 syntax. Available variables: {{ invoice }}, {{ customer_name }}, {{ company }}, {{ pay_url }}. The donation acknowledgment letter (nonprofit) also gets {{ donor }}, {{ donor_name }}, {{ gift }} and {{ irs.text }}.
                    </div>
                    <div style="display:flex; gap:8px; margin-bottom:12px;">
                        <button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.seedTemplates()">Seed Default Templates</button>
                    </div>
                    <div id="email-template-list"></div>
                </div>

                <div class="settings-section">
                    <h3>${T('Classes')}</h3>
                    <div style="font-size:10px; color:var(--text-muted); margin-bottom:8px;">
                        Track income and expenses by department, location, or line of
                        business. ${T('Classes')} appear on entry forms and the ${T('P&L by Class')} report.
                    </div>
                    <div style="display:flex; gap:8px; margin-bottom:12px;">
                        <input type="text" id="new-class-name" placeholder="New ${T('class')} name" style="width:220px;">
                        <button type="button" class="btn btn-primary" onclick="SettingsPage.addClass()">Add ${T('Class')}</button>
                    </div>
                    <div id="classes-list"></div>
                </div>

                <div class="settings-section">
                    <h3>Cost Types</h3>
                    <div style="font-size:10px; color:var(--text-muted); margin-bottom:8px;">
                        How job costs roll up: labor, material, subcontract, equipment, other — add your own
                        (permits, bonding, warranty…). A labor-type carries a burden % (employer taxes, benefits,
                        insurance) posted as its own line. The cost account is where a job-cost line lands when its
                        code has none; the offset accounts are the credit side of job cost entries (payroll clearing,
                        applied equipment, applied overhead).
                    </div>
                    <div style="display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap;">
                        <input type="text" id="new-ct-code" placeholder="Code (e.g. permits)" style="width:150px;">
                        <input type="text" id="new-ct-name" placeholder="Name" style="width:200px;">
                        <label style="font-weight:normal;font-size:11px;"><input type="checkbox" id="new-ct-labor"> labor-type (burden applies)</label>
                        <button type="button" class="btn btn-primary" onclick="SettingsPage.addCostType()">Add Cost Type</button>
                        <button type="button" class="btn btn-secondary" onclick="SettingsPage.setupOffsets()" title="Creates Applied Labor Cost, Applied Labor Burden, Applied Equipment Cost and Applied Overhead if missing, points each cost type at the matching COGS account (Materials, Labor, Subcontractor) and fills in any blank accounts">Create default offset accounts</button>
                    </div>
                    <div id="cost-types-list"></div>
                </div>

                <div class="settings-section">
                    <h3>Cost Codes</h3>
                    <div style="font-size:10px; color:var(--text-muted); margin-bottom:8px;">
                        ${Terms.text('The job-costing chart: which part of a job a cost belongs to')}
                        ("03 Concrete", "26 Electrical"), independent of the account it posts
                        to. Picked per line on bills, expenses, purchase orders and journal
                        entries; ${Terms.text('the Job detail rolls costs up by code and cost type.')}
                    </div>
                    <div style="display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap;">
                        <input type="text" id="new-cc-code" placeholder="Code" style="width:90px;">
                        <input type="text" id="new-cc-name" placeholder="Name" style="width:220px;">
                        <select id="new-cc-type">
                            <option value="labor">Labor</option><option value="material">Material</option>
                            <option value="subcontract">Subcontract</option><option value="equipment">Equipment</option>
                            <option value="other" selected>Other</option>
                        </select>
                        <select id="new-cc-parent"><option value="">(top level)</option></select>
                        <button type="button" class="btn btn-primary" onclick="SettingsPage.addCostCode()">Add Cost Code</button>
                        <button type="button" class="btn btn-secondary" onclick="SettingsPage.loadStandardCostCodes()" title="CSI MasterFormat divisions + Labor + Equipment Rental">Load standard list</button>
                        <button type="button" class="btn btn-secondary" onclick="SettingsPage.showCostCodeImport()">Import CSV</button>
                    </div>
                    <div id="cost-codes-list"></div>
                </div>

                <div class="settings-section">
                    <h3>Equipment</h3>
                    <div style="font-size:10px; color:var(--text-muted); margin-bottom:8px;">
                        ${Terms.text('Owned machines charged to jobs by the hour from a Job Cost Entry.')} The recovery account is
                        the credit side (defaults to the equipment cost type's offset).
                    </div>
                    <div style="display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap;">
                        <input type="text" id="new-eq-code" placeholder="Code" style="width:90px;">
                        <input type="text" id="new-eq-name" placeholder="Name (Skid steer, F-250…)" style="width:220px;">
                        <input type="number" step="0.01" id="new-eq-rate" placeholder="$/hr" style="width:90px;">
                        <button type="button" class="btn btn-primary" onclick="SettingsPage.addEquipment()">Add Equipment</button>
                    </div>
                    <div id="equipment-list"></div>
                </div>

                <div class="settings-section" id="settings-backups">
                    <h3>Backup / Restore</h3>
                    <div style="font-size:10px; color:var(--text-muted); margin-bottom:8px;">
                        Backups of this company only, named for it. Restore replaces everything in this
                        company with the backup; a safety backup of the books as they are is taken first,
                        so a restore can be undone by restoring that one.
                    </div>
                    <div style="display:flex; gap:8px; margin-bottom:12px;">
                        <button type="button" class="btn btn-primary" onclick="SettingsPage.createBackup()">Create Backup</button>
                    </div>
                    <div id="backup-list"></div>
                </div>

                <div class="settings-section" id="settings-users" style="display:none;">
                    <h3>Users &mdash; Server Edition</h3>
                    <p style="font-size:12px; color:var(--text-muted); margin-bottom:10px;">
                        Add a second user and this deployment becomes
                        <strong>Server Edition</strong>: everyone signs in with a
                        username, every change is attributed in the audit log, and
                        roles limit what each person can do.
                    </p>
                    <div id="users-list" style="margin-bottom:12px;"></div>
                    <div class="form-grid" style="align-items:end;">
                        <div class="form-group"><label>Username</label>
                            <input id="user-new-username" autocomplete="off"></div>
                        <div class="form-group"><label>Display name</label>
                            <input id="user-new-display" autocomplete="off"></div>
                        <div class="form-group"><label>Password</label>
                            <input id="user-new-password" type="password" autocomplete="new-password"></div>
                        <div class="form-group"><label>Role</label>
                            <select id="user-new-role">
                                <option value="bookkeeper">Bookkeeper — daily books, no admin</option>
                                <option value="readonly">Read-only — reports and lookups</option>
                                <option value="admin">Admin — everything</option>
                            </select></div>
                    </div>
                    <button type="button" class="btn btn-primary" onclick="SettingsPage.createUser()">Add User</button>
                </div>

                <div class="settings-section" id="settings-api-tokens" style="display:none;">
                    <h3>API Tokens &mdash; agents &amp; integrations</h3>
                    <p style="font-size:12px; color:var(--text-muted); margin-bottom:10px;">
                        Scoped credentials for non-humans: AI agents, the receipt
                        service, scripts. A token wears a role just like a user —
                        give read-only to anything that only reports, and every
                        change it makes is attributed in the audit log as
                        <code>token:&lt;label&gt;</code>. Tokens can never manage
                        users or other tokens, whatever their role.
                    </p>
                    <div id="api-token-reveal" style="display:none; margin-bottom:12px; padding:10px; border:1px solid var(--qb-gold); border-radius:4px; background:rgba(224,158,36,0.08); font-size:12px;">
                        <strong>Copy this token now — it will never be shown again:</strong>
                        <div style="display:flex; align-items:center; gap:8px; margin-top:6px;">
                            <div style="font-family:var(--font-mono); word-break:break-all; flex:1;" id="api-token-secret"></div>
                            <button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.copyApiTokenSecret()">Copy</button>
                        </div>
                    </div>
                    <div id="api-token-list" style="margin-bottom:12px;"></div>
                    <div class="form-grid" style="align-items:end;">
                        <div class="form-group"><label>Label</label>
                            <input id="token-new-label" autocomplete="off" placeholder="e.g. claude-code, receipt-service"></div>
                        <div class="form-group"><label>Role</label>
                            <select id="token-new-role">
                                <option value="readonly">Read-only — reports and lookups</option>
                                <option value="bookkeeper">Bookkeeper — daily books, no admin</option>
                                <option value="admin">Admin — everything except identity management</option>
                            </select></div>
                    </div>
                    <button type="button" class="btn btn-primary" onclick="SettingsPage.createApiToken()">Create Token</button>
                </div>

                <!-- Always in reach: the one Save for the settings above sat at
                     the very bottom, after 25 other buttons, and the first
                     "Save" on the page was AI Insights' (explore 2.17.3, L16). -->
                <div class="form-actions" id="settings-savebar"
                    style="position:sticky; bottom:0; z-index:5; align-items:center; background:var(--content-bg);
                           padding-bottom:10px; box-shadow:0 -4px 8px -6px rgba(0,0,0,0.25);">
                    <span id="settings-dirty-note" role="status" aria-live="polite"
                        style="margin-right:auto; font-size:11px; color:var(--text-muted);"></span>
                    <button type="submit" class="btn btn-primary" id="settings-save-btn">Save Settings</button>
                </div>
            </form>`;
    },

    // ------------------------------------------------------------------
    // Unsaved changes. Leaving the page used to drop the edits without a
    // word (explore 2.17.3, skytech L16). The named fields of the form are
    // compared with what was last loaded or saved; the sections that save
    // themselves (AI, classes, users, tokens...) have no names and are not
    // counted.
    // ------------------------------------------------------------------
    _snapshot: null,
    _leaving: false,

    _formData() {
        const form = document.getElementById('settings-form');
        if (!form) return null;
        const data = {};
        for (const [k, v] of new FormData(form).entries()) {
            if (typeof v === 'string') data[k] = v;
        }
        return data;
    },

    _formState(except) {
        const data = SettingsPage._formData();
        if (!data) return null;
        if (except) delete data[except];
        return JSON.stringify(data);
    },

    _markClean() {
        SettingsPage._snapshot = SettingsPage._formData();
        SettingsPage._updateDirty();
    },

    // True when a field differs from what was last loaded or saved;
    // `except` leaves one field out of the comparison.
    isDirty(except) {
        const snap = SettingsPage._snapshot;
        const now = SettingsPage._formState(except);
        if (!snap || now === null) return false;
        const before = Object.assign({}, snap);
        if (except) delete before[except];
        return now !== JSON.stringify(before);
    },

    _updateDirty() {
        const note = document.getElementById('settings-dirty-note');
        if (note) note.textContent = SettingsPage.isDirty() ? 'Unsaved changes' : '';
    },

    // App.navigate is the one door every in-app move goes through: sidebar
    // links (via hashchange), toolbar buttons, keyboard shortcuts, search.
    _installLeaveGuard() {
        if (SettingsPage._leaveGuardInstalled || typeof App === 'undefined') return;
        SettingsPage._leaveGuardInstalled = true;
        const navigate = App.navigate;
        App.navigate = function (hash) {
            const target = String(hash || '').replace('#', '') || '/';
            if (target !== '/settings' && SettingsPage.isDirty()) {
                if (!confirm('You have unsaved changes in Settings. Leave without saving them?')) {
                    if (location.hash !== '#/settings') history.replaceState(null, '', '#/settings');
                    return Promise.resolve();
                }
                SettingsPage._snapshot = null;
            }
            return navigate.apply(this, arguments);
        };
        window.addEventListener('beforeunload', e => {
            if (SettingsPage._leaving || !SettingsPage.isDirty()) return;
            e.preventDefault();
            e.returnValue = '';
        });
    },

    // ------------------------------------------------------------------
    // Users (Server Edition) — section is visible to admins only; the
    // backend enforces the same rule, this just avoids a useless 403.
    // ------------------------------------------------------------------
    async loadUsers() {
        const section = $('#settings-users');
        if (!section) return;
        try {
            const status = await API.get('/auth/status');
            if (!status.user || status.user.role !== 'admin') return;
            const users = await API.get('/users');
            section.style.display = '';
            const rows = users.map(u => `<tr>
                <td>${escapeHtml(u.username)}</td>
                <td>${escapeHtml(u.display_name)}</td>
                <td>
                    <select onchange="SettingsPage.updateUser(${u.id}, {role: this.value})">
                        ${['admin', 'bookkeeper', 'readonly'].map(r =>
                            `<option value="${r}" ${u.role === r ? 'selected' : ''}>${r}</option>`).join('')}
                    </select>
                </td>
                <td>${u.last_login_at ? formatDate(u.last_login_at) : '—'}</td>
                <td>${u.is_active
                    ? `<button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.updateUser(${u.id}, {is_active: false})">Deactivate</button>`
                    : `<button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.updateUser(${u.id}, {is_active: true})">Reactivate</button>`}
                    <button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.resetUserPassword(${u.id}, '${escapeHtml(u.username)}')">Reset password</button>
                </td>
            </tr>`).join('');
            $('#users-list').innerHTML = `<div class="table-container"><table>
                <thead><tr><th scope="col">Username</th><th scope="col">Name</th><th scope="col">Role</th><th scope="col">Last login</th><th scope="col"></th></tr></thead>
                <tbody>${rows}</tbody></table></div>`;
        } catch (e) { /* non-admin or pre-upgrade server: section stays hidden */ }
    },

    // The start-up password choice applies to the desktop app (and its
    // Server Edition), where one server process serves the company.
    async loadSignInPref() {
        const section = $('#settings-sign-in');
        if (!section) return;
        try {
            const sys = await API.get('/system');
            if (sys && sys.desktop) section.hidden = false;
        } catch (e) { /* stays hidden */ }
    },

    // ------------------------------------------------------------------
    // API tokens — admin-only, mirrors the Users section
    // ------------------------------------------------------------------
    async loadApiTokens() {
        const section = $('#settings-api-tokens');
        if (!section) return;
        try {
            const status = await API.get('/auth/status');
            if (!status.user || status.user.role !== 'admin') return;
            const tokens = await API.get('/tokens');
            section.style.display = '';
            if (!tokens.length) {
                $('#api-token-list').innerHTML =
                    '<div style="font-size:11px; color:var(--text-muted);">No tokens yet.</div>';
                return;
            }
            const rows = tokens.map(t => `<tr>
                <td>${escapeHtml(t.label)}</td>
                <td style="font-family:var(--font-mono); font-size:10px;">${escapeHtml(t.token_hint)}&hellip;</td>
                <td>${escapeHtml(t.role)}</td>
                <td>${t.last_used_at ? formatDate(t.last_used_at) : '—'}</td>
                <td>${t.is_active
                    ? `<button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.updateApiToken(${t.id}, {is_active: false})">Revoke</button>`
                    : `<button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.updateApiToken(${t.id}, {is_active: true})">Reactivate</button>`}
                </td>
            </tr>`).join('');
            $('#api-token-list').innerHTML = `<div class="table-container"><table>
                <thead><tr><th scope="col">Label</th><th scope="col">Token</th><th scope="col">Role</th><th scope="col">Last used</th><th scope="col"></th></tr></thead>
                <tbody>${rows}</tbody></table></div>`;
        } catch (e) { /* non-admin or pre-upgrade server: section stays hidden */ }
    },

    async createApiToken() {
        try {
            const created = await API.post('/tokens', {
                label: $('#token-new-label').value.trim(),
                role: $('#token-new-role').value,
            });
            $('#api-token-secret').textContent = created.token;
            $('#api-token-reveal').style.display = '';
            $('#token-new-label').value = '';
            toast('Token created — press Copy below, it will not be shown again');
            SettingsPage.loadApiTokens();
        } catch (err) { toast(err.message, 'error'); }
    },

    copyApiTokenSecret() {
        const el = $('#api-token-secret');
        return copyToClipboard(el ? el.textContent : '', 'Token', el);
    },

    async updateApiToken(id, patch) {
        try {
            await API.put(`/tokens/${id}`, patch);
            toast('Token updated');
            SettingsPage.loadApiTokens();
        } catch (err) { toast(err.message, 'error'); }
    },

    async createUser() {
        try {
            await API.post('/users', {
                username: $('#user-new-username').value.trim(),
                display_name: $('#user-new-display').value.trim(),
                password: $('#user-new-password').value,
                role: $('#user-new-role').value,
            });
            toast('User added — this deployment is now Server Edition');
            $('#user-new-username').value = '';
            $('#user-new-display').value = '';
            $('#user-new-password').value = '';
            SettingsPage.loadUsers();
        } catch (err) { toast(err.message, 'error'); }
    },

    async updateUser(id, patch) {
        try {
            await API.put(`/users/${id}`, patch);
            toast('User updated');
            SettingsPage.loadUsers();
        } catch (err) {
            toast(err.message, 'error');
            SettingsPage.loadUsers(); // revert any optimistic select change
        }
    },

    async resetUserPassword(id, username) {
        const pw = prompt(`New password for ${username} (min 8 characters):`);
        if (!pw) return;
        try {
            await API.put(`/users/${id}`, { password: pw });
            toast('Password updated');
        } catch (err) { toast(err.message, 'error'); }
    },

    async save(e) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        // Remove file input from data
        delete data.file;
        const btn = document.getElementById('settings-save-btn');
        if (btn) btn.disabled = true;
        try {
            await API.put('/settings', data);
            SettingsPage._markClean();
            toast('Settings saved');
        } catch (err) {
            toast(err.message, 'error');
        } finally {
            if (btn) btn.disabled = false;
        }
    },

    // Closing date: say plainly whether one is set, and clear it in one
    // click — clearing used to mean emptying three date segments by hand,
    // which WebKit could leave half-empty and silently invalid.
    _closingStateText(value) {
        return value
            ? `Closed through ${formatDate(value)}: changes dated on or before it are refused.`
            : 'No closing date: every period is open.';
    },

    showClosingState() {
        const input = document.getElementById('closing-date');
        const value = input ? input.value : '';
        const state = document.getElementById('closing-date-state');
        if (state) state.textContent = SettingsPage._closingStateText(value);
        const clear = document.getElementById('closing-date-clear');
        if (clear) clear.disabled = !value;
    },

    clearClosingDate() {
        const input = document.getElementById('closing-date');
        if (!input) return;
        input.value = '';
        SettingsPage.showClosingState();
        SettingsPage._updateDirty();
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
            if (!SettingsPage.isDirty()) { App.navigate('#/settings'); return; }
            // Unsaved edits elsewhere on the page: show the new logo in place
            // rather than re-render the page over them.
            let img = document.getElementById('company-logo-preview');
            if (!img) {
                img = document.createElement('img');
                img.id = 'company-logo-preview';
                img.setAttribute('style', 'max-width:200px; max-height:80px; margin-bottom:8px; display:block;');
                input.parentElement.insertBefore(img, input);
            }
            img.src = `${(data && data.path) || ''}?t=${Date.now()}`;
        } catch (err) { toast(err.message, 'error'); }
    },

    async testEmail() {
        try {
            await API.post('/settings/test-email');
            toast('Test email sent');
        } catch (err) { toast(err.message, 'error'); }
    },

    // Company type saves on its own and reloads: the vocabulary and the
    // nonprofit nav items are applied at boot (App.applyTerminology), so
    // the whole shell has to come up again in the new words.
    async changeCompanyType(sel) {
        const value = sel.value;
        const previous = value === 'nonprofit' ? 'business' : 'nonprofit';
        // The switch reloads the page; other changes on it are saved with
        // the new type rather than dropped by the reload.
        const others = SettingsPage.isDirty('company_type');
        const msg = (value === 'nonprofit'
            ? 'Switch this company to nonprofit mode? Screens will say donor, pledge, donation and fund; the net-asset accounts are added. Nothing in your data changes.'
            : 'Switch this company back to business mode? Screens return to customer, invoice, sales receipt and class. Nothing in your data changes.')
            + (others ? '\n\nYour other unsaved changes on this page are saved with it.' : '');
        if (!confirm(msg)) { sel.value = previous; return; }
        const form = document.getElementById('settings-form');
        if (others && form && !form.reportValidity()) { sel.value = previous; return; }
        try {
            const payload = others ? SettingsPage._formData() : {};
            payload.company_type = value;
            await API.put('/settings', payload);
            if (value === 'nonprofit') {
                try { await API.post('/nonprofit/setup-accounts', {}); }
                catch (e) { /* accounts can be created later from the Nonprofit section */ }
            }
            SettingsPage._leaving = true;
            toast('Company type saved — reloading');
            setTimeout(() => location.reload(), 600);
        } catch (err) {
            sel.value = previous;
            toast(err.message, 'error');
        }
    },

    async saveOcrEngine(value) {
        try {
            await API.put('/settings', { ocr_engine: value });
            toast('OCR engine preference saved');
            this.loadOcrStatus();
        } catch (err) { toast(err.message, 'error'); }
    },

    async loadOcrEnginePref() {
        const sel = $('#ocr-engine-pref');
        if (!sel) return;
        try {
            const settings = await API.get('/settings');
            if (settings.ocr_engine) sel.value = settings.ocr_engine;
        } catch (e) { /* leave the default selected */ }
    },

    async loadOcrStatus() {
        const el = $('#ocr-status');
        if (!el) return;
        try {
            const s = await API.get('/ocr/status');
            if (s.available) {
                const langs = (s.languages || []).join(', ') || '—';
                const engineNames = { tesseract: 'Tesseract OCR', vision: 'Apple Vision (built into macOS)', winrt: 'Windows OCR (built into Windows)' };
                const engineLabel = engineNames[s.engine] || 'OCR engine';
                const pdfNames = { windows: 'built into Windows', macos: 'built into macOS', poppler: 'via poppler-utils' };
                const pdfNote = s.pdf
                    ? ` &middot; PDFs: ${escapeHtml(pdfNames[s.pdf] || s.pdf)}`
                    : '<div style="font-size:11px; color:#b45309; margin-top:4px;">PDF scanning is not available on this machine (images still scan). '
                      + 'Linux: <code>sudo apt-get install poppler-utils</code>; other platforms: <code>brew install poppler</code> / poppler for Windows on PATH.</div>';
                el.innerHTML = `<strong style="color:var(--text-success);">${escapeHtml(engineLabel)} is ready</strong>`
                    + (s.version ? ` <span style="color:var(--text-muted);">(${escapeHtml(s.version)})</span>` : '')
                    + ` &middot; languages: ${escapeHtml(langs)}`
                    + pdfNote;
            } else {
                el.innerHTML = '<strong style="color:#b45309;">No OCR engine is available — scanning is disabled.</strong>'
                    + '<div style="font-size:11px; color:var(--text-muted); margin-top:4px;">macOS and Windows normally use the engine built into the OS; installing Tesseract enables scanning anywhere.</div>'
                    + '<div style="font-size:11px; color:var(--text-muted); margin-top:6px;">'
                    + 'Ubuntu: <code>sudo apt-get install tesseract-ocr</code> &middot; '
                    + 'macOS: <code>brew install tesseract</code> &middot; '
                    + 'Windows: install the UB Mannheim Tesseract build.<br>'
                    + 'PDF receipts render without extra software on Windows and macOS; '
                    + 'on Linux install poppler-utils: <code>sudo apt-get install poppler-utils</code>.'
                    + '</div>';
            }
        } catch (e) {
            el.textContent = 'Could not check OCR status.';
        }
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
            SettingsPage._backups = backups;
            const el = $('#backup-list');
            if (!el) return;
            if (backups.length === 0) {
                el.innerHTML = '<div style="font-size:11px; color:var(--text-muted);">No backups yet.</div>';
                return;
            }
            el.innerHTML = `<div class="table-container"><table>
                <thead><tr><th scope="col">Filename</th><th scope="col">Size</th><th scope="col">Created</th><th scope="col">Actions</th></tr></thead>
                <tbody>${backups.map(b => `<tr>
                    <td>${escapeHtml(b.filename)}${b.backup_type === 'pre-restore'
                        ? ' <span style="font-size:10px; color:var(--text-muted);">(taken before a restore)</span>' : ''}</td>
                    <td>${(b.file_size / 1024).toFixed(1)} KB</td>
                    <td>${escapeHtml(SettingsPage._when(b.created_at))}</td>
                    <td class="actions">
                        <a href="/api/backups/download/${encodeURIComponent(b.filename)}" class="btn btn-sm btn-secondary" download>Download</a>
                        <button type="button" class="btn btn-sm btn-secondary" data-filename="${escapeHtml(b.filename)}"
                            onclick="SettingsPage.confirmRestore(this.dataset.filename)">Restore…</button>
                    </td>
                </tr>`).join('')}</tbody>
            </table></div>`;
        } catch (e) { /* ignore */ }
    },

    // Date and time: a company often has several backups on one day.
    _when(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString('en-US', {
            month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
        });
    },

    // Restore had an endpoint and no button (explore 2.17.3, macbase1 F25).
    // It replaces the whole company, so it asks plainly and needs a tick.
    confirmRestore(filename) {
        const b = (SettingsPage._backups || []).find(x => x.filename === filename) || { filename };
        const when = b.created_at ? SettingsPage._when(b.created_at) : 'when it was made';
        const company = (App.settings && App.settings.company_name) || 'this company';
        openModal('Restore a backup', `
            <form id="restore-form" onsubmit="SettingsPage.restoreBackup(event)">
                <input type="hidden" name="filename" value="${escapeHtml(filename)}">
                <p style="margin:0 0 8px;"><strong>This replaces everything in ${escapeHtml(company)} with the books
                    as they were in ${escapeHtml(filename)} (${escapeHtml(when)}).</strong></p>
                <p style="margin:0 0 8px;">Anything entered or changed since then (${T('invoices')}, payments, bills,
                    settings, users) is replaced. A safety backup of the books as they are now is taken first;
                    restore that one to undo this.</p>
                <label style="display:flex; gap:6px; align-items:flex-start; font-weight:normal;">
                    <input type="checkbox" name="understood" required
                        onchange="this.form.querySelector('button[type=submit]').disabled = !this.checked">
                    <span>I understand that everything since ${escapeHtml(when)} will be replaced.</span></label>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-danger" disabled>Replace my books with this backup</button>
                </div>
            </form>`);
    },

    async restoreBackup(e) {
        e.preventDefault();
        const form = e.target;
        const filename = form.filename.value;
        const btn = form.querySelector('button[type=submit]');
        const label = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Restoring…';
        const send = allowOther => API.post('/backups/restore',
            allowOther ? { filename, allow_other_company: true } : { filename });
        try {
            let result;
            try {
                result = await send(false);
            } catch (err) {
                // Another company's backup: say whose, and ask a second time.
                if (!(err.status === 409 && err.detail && err.detail.code === 'other_company')) throw err;
                if (!confirm(`${err.message}\n\nRestore it over this company anyway?`)) {
                    btn.disabled = false;
                    btn.textContent = label;
                    return;
                }
                result = await send(true);
            }
            closeModal();
            SettingsPage._leaving = true;  // the books were replaced; nothing here to keep
            toast(`Restored from ${filename}. The books as they were are kept in ${result.safety_backup}. Reloading…`);
            setTimeout(() => location.reload(), 1500);
        } catch (err) {
            toast(err.message, 'error');
            btn.disabled = false;
            btn.textContent = label;
        }
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
                <thead><tr><th scope="col">Name</th><th scope="col">Type</th><th scope="col">Subject</th><th scope="col">Actions</th></tr></thead>
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
        let t, invoices = [];
        try {
            t = await API.get(`/email-templates/${id}`);
            if (t.template_type === 'invoice') {
                // Enough to pick one to preview against; the endpoint would
                // otherwise return up to 500, each with its lines.
                invoices = await API.get('/invoices?is_sales_receipt=false&limit=50');
            }
        } catch (err) { toast(err.message, 'error'); return; }
        openModal('Edit Email Template', `
            <form id="email-template-editor" onsubmit="SettingsPage.saveTemplate(event, ${id})">
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
                    {{ company.company_name }}, {{ doc_label }}, {{ pay_url }}. Filters: | currency, | fdate
                    <br><span style="opacity:.8;">{{ doc_label }} reads &ldquo;Invoice&rdquo; or &ldquo;Sales Receipt&rdquo; to match the document.
                    {{ pay_url }} is only set when a payment provider is enabled &mdash; guard it with {% if pay_url %}.</span>
                </div>
                ${t.template_type === 'invoice' ? `<div style="margin-top:12px;">
                    <label>Preview against ${T('invoice')}
                        <select id="email-template-invoice">${invoices.map(inv => `<option value="${inv.id}">#${escapeHtml(inv.invoice_number)} — ${escapeHtml(inv.customer_name || '')}</option>`).join('')}</select></label>
                    <button type="button" class="btn btn-secondary" onclick="SettingsPage.previewTemplate()" ${invoices.length ? '' : 'disabled'}>Preview</button>
                    <p style="font-size:11px; color:var(--text-muted);">Renders what you have typed, without saving or sending.</p>
                    <div id="email-template-preview"></div>
                </div>` : ''}
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save Template</button>
                </div>
            </form>`);
    },

    async previewTemplate() {
        const form = document.getElementById('email-template-editor');
        const picker = document.getElementById('email-template-invoice');
        const out = document.getElementById('email-template-preview');
        if (!form || !picker || !out) return;
        out.innerHTML = '';
        try {
            const p = await API.post('/email-templates/preview', {
                invoice_id: Number(picker.value),
                subject_template: form.elements.subject_template.value,
                body_template: form.elements.body_template.value,
            });
            // A blank where the author expected content is the one outcome
            // that explains nothing. The server reports what resolved to
            // nothing; say so next to the preview rather than leaving them
            // looking at a hole.
            // Two kinds of blank. Telling an operator that pay_url is "not
            // available" contradicted the variable list two inches above,
            // which says it is available and conditional.
            const unavailable = p.unavailable || [];
            const conditional = p.conditional || [];
            const lines = [];
            if (unavailable.length) {
                lines.push(`<strong>Not available to an email template:</strong>
                    ${unavailable.map(escapeHtml).join(', ')} — these came out blank and
                    the email would send with the same gaps.`);
            }
            for (const c of conditional) {
                lines.push(`<strong>${escapeHtml(c.name)} is not set for this ${T('Invoice').toLowerCase()}:</strong>
                    ${escapeHtml(c.why)}.`);
            }
            const note = lines.length
                ? `<p class="form-hint" style="margin-top:8px;color:var(--text-warning,#92400e);">
                       ${lines.join('<br>')}</p>`
                : '';
            out.innerHTML = `<p style="margin-top:8px;"><strong>Subject:</strong> ${escapeHtml(p.subject)}</p>
                ${note}
                <iframe id="email-template-rendered" sandbox="" title="Template preview" style="width:100%;height:320px;border:1px solid var(--gray-300);background:white;"></iframe>`;
            const frame = document.getElementById('email-template-rendered');
            if (frame) frame.srcdoc = p.html_body;
        } catch (err) { toast(err.message, 'error'); }
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
        const needsEndpoint = !!currentSpec.needs_endpoint_url;
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
            <fieldset id="ai-settings-endpoint-wrap" class="ai-worker-section"
                      style="${needsEndpoint ? '' : 'display:none'}">
                <legend>Custom OpenAI-Compatible Endpoint</legend>
                <p class="ai-worker-help">
                    Point Slowbooks at any OpenAI-compatible chat API on the
                    public internet — another vendor's <code>/v1</code> base URL,
                    or a gateway you host. <code>/chat/completions</code> is
                    appended automatically if you don't include it. A model on
                    this machine or your LAN cannot be reached this way: the
                    address check below refuses it on purpose.
                </p>
                <label class="form-field">
                    <span>Base URL <em class="ai-worker-required">(https only)</em></span>
                    <input type="url" id="ai-settings-endpoint-url"
                           value="${escapeHtml(cfg.endpoint_url || '')}"
                           placeholder="https://api.example.com/v1"
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
                <span>API Key / Shared Secret ${hasKey ? '<em class="ai-key-saved">(saved &#10003;)</em> <button type="button" class="btn btn-sm" id="ai-settings-key-remove" title="Remove the stored key">Remove</button>' : ''}</span>
                <input type="password" id="ai-settings-key"
                       placeholder="${hasKey ? 'Leave blank to keep existing' : 'Paste key or openssl rand -hex 32'}"
                       autocomplete="new-password">
            </label>
            <div class="ai-settings-buttons">
                <button type="button" class="btn btn-secondary btn-sm" id="ai-settings-test">Test</button>
                <span id="ai-settings-test-result" class="ai-settings-test-result"></span>
                <div class="ai-settings-spacer"></div>
                <button type="button" class="btn btn-primary btn-sm" id="ai-settings-save">Save AI settings</button>
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
        const endpointWrap = document.getElementById('ai-settings-endpoint-wrap');
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
            if (endpointWrap) endpointWrap.style.display = spec.needs_endpoint_url ? '' : 'none';
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
            endpoint_url: document.getElementById('ai-settings-endpoint-url') ? document.getElementById('ai-settings-endpoint-url').value.trim() : '',
        });
        // The key field is write-only: send it only when the user typed a new
        // one. A blank field must never round-trip as "clear the key".
        const keyPayload = () => {
            const v = document.getElementById('ai-settings-key').value;
            return v.trim() ? { api_key: v } : {};
        };

        const removeBtn = document.getElementById('ai-settings-key-remove');
        if (removeBtn) {
            removeBtn.addEventListener('click', async () => {
                if (!confirm('Remove the stored API key? AI Insights will be off until a new key is saved.')) return;
                try {
                    // An explicit empty string is the API's "clear the key".
                    const updated = await API.put('/analytics/ai-config', { ...collectPayload(), api_key: '' });
                    SettingsPage.aiConfigState = updated;
                    toast('AI provider key removed', 'success');
                    SettingsPage.loadAiConfig();
                } catch (err) {
                    toast('Could not remove the key: ' + (err.message || err), 'error');
                }
            });
        }

        saveBtn.addEventListener('click', async () => {
            try {
                const updated = await API.put('/analytics/ai-config', { ...collectPayload(), ...keyPayload() });
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
                await API.put('/analytics/ai-config', { ...collectPayload(), ...keyPayload() });
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


// --- Class tracking management (Settings > Classes) ---------------------
SettingsPage.loadClasses = async function () {
    const el = document.getElementById('classes-list');
    if (!el) return;
    try {
        const classes = await API.get('/classes?include_archived=true');
        SettingsPage._classes = classes;
        const np = Terms.isNonprofit();
        const fundCols = np ? `<th scope="col">Restriction</th><th scope="col">Function</th><th scope="col">Donor / purpose</th>` : '';
        const fundCells = c => np ? `
                <td>${escapeHtml(SettingsPage.RESTRICTION_LABELS[c.restriction] || c.restriction)}</td>
                <td>${escapeHtml(SettingsPage.FUNCTION_LABELS[c.default_function] || '—')}</td>
                <td style="font-size:10px;">${escapeHtml(c.donor_name || '')}${c.donor_name && c.purpose ? ' — ' : ''}${escapeHtml(c.purpose || '')}</td>` : '';
        el.innerHTML = `<div class="table-container"><table>
            <thead><tr><th scope="col">Name</th>${fundCols}<th scope="col">Status</th><th scope="col">Actions</th></tr></thead>
            <tbody>` + classes.map(c => `<tr>
                <td>${escapeHtml(c.name)}${c.is_system_default ? ' <span style="font-size:9px;color:var(--text-muted);">(default)</span>' : ''}</td>
                ${fundCells(c)}
                <td>${c.is_archived ? 'Archived' : 'Active'}</td>
                <td class="actions">
                    ${np ? `<button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.editFund(${c.id})">Edit</button>` : ''}
                    ${c.is_system_default ? '' : `
                        <button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.renameClass(${c.id})">Rename</button>
                        <button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.toggleArchiveClass(${c.id}, ${!c.is_archived})">${c.is_archived ? 'Unarchive' : 'Archive'}</button>`}
                </td>
            </tr>`).join('') + `</tbody></table></div>`;
    } catch (err) {
        el.innerHTML = `<div style="color:var(--danger); font-size:11px;">${escapeHtml(err.message)}</div>`;
    }
};

SettingsPage.addClass = async function () {
    const input = document.getElementById('new-class-name');
    const name = (input?.value || '').trim();
    if (!name) { toast(Terms.text('Enter a class name'), 'error'); return; }
    try {
        await API.post('/classes', { name });
        input.value = '';
        toast(Terms.text('Class added'));
        SettingsPage.loadClasses();
    } catch (err) { toast(err.message, 'error'); }
};

SettingsPage.RESTRICTION_LABELS = {
    unrestricted: 'Without donor restrictions',
    temporarily_restricted: 'With donor restrictions (purpose / time)',
    permanently_restricted: 'With donor restrictions (permanent)',
};
SettingsPage.FUNCTION_LABELS = { program: 'Program services', management: 'Management & general', fundraising: 'Fundraising' };

// Nonprofit: a class is a fund. Restriction decides which net-asset line
// its activity reports on; the default function is what expenses in the
// fund count as on the Statement of Functional Expenses unless a line
// says otherwise.
SettingsPage.editFund = function (id) {
    const c = (SettingsPage._classes || []).find(x => x.id === id);
    if (!c) return;
    const opt = (map, sel) => Object.entries(map).map(([v, l]) => `<option value="${v}" ${v === sel ? 'selected' : ''}>${escapeHtml(l)}</option>`).join('');
    openModal(`Fund: ${escapeHtml(c.name)}`, `
        <form onsubmit="SettingsPage.saveFund(event, ${c.id})">
            <div class="form-grid">
                <div class="form-group full-width"><label>Restriction</label>
                    <select name="restriction" ${c.is_system_default ? 'disabled' : ''}>${opt(SettingsPage.RESTRICTION_LABELS, c.restriction)}</select>
                    ${c.is_system_default ? '<div style="font-size:10px;color:var(--text-muted);">The default bucket for untagged activity is always without restrictions.</div>' : ''}</div>
                <div class="form-group full-width"><label>Default function</label>
                    <select name="default_function"><option value="">— none —</option>${opt(SettingsPage.FUNCTION_LABELS, c.default_function)}</select></div>
                <div class="form-group full-width"><label>Donor / grantor</label>
                    <input name="donor_name" maxlength="200" value="${escapeHtml(c.donor_name || '')}"></div>
                <div class="form-group full-width"><label>Purpose</label>
                    <textarea name="purpose" rows="2">${escapeHtml(c.purpose || '')}</textarea></div>
            </div>
            <div class="form-actions">
                <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                <button type="submit" class="btn btn-primary">Save</button>
            </div>
        </form>`);
};

SettingsPage.saveFund = async function (e, id) {
    e.preventDefault();
    const f = e.target;
    const body = {
        default_function: f.default_function.value || null,
        donor_name: f.donor_name.value.trim() || null,
        purpose: f.purpose.value.trim() || null,
    };
    if (!f.restriction.disabled) body.restriction = f.restriction.value;
    try {
        await API.put(`/classes/${id}`, body);
        closeModal();
        toast(`${T('Class')} saved`);
        SettingsPage.loadClasses();
    } catch (err) { toast(err.message, 'error'); }
};

SettingsPage.renameClass = async function (id) {
    const name = prompt(Terms.text('New class name:'));
    if (!name || !name.trim()) return;
    try {
        await API.put(`/classes/${id}`, { name: name.trim() });
        toast(Terms.text('Class renamed'));
        SettingsPage.loadClasses();
    } catch (err) { toast(err.message, 'error'); }
};

SettingsPage.toggleArchiveClass = async function (id, archive) {
    try {
        await API.put(`/classes/${id}`, { is_archived: archive });
        toast(Terms.text(archive ? 'Class archived' : 'Class unarchived'));
        SettingsPage.loadClasses();
    } catch (err) { toast(err.message, 'error'); }
};

// --- Cost codes (Settings > Cost Codes) ----------------------------------
SettingsPage.loadCostCodes = async function () {
    const el = document.getElementById('cost-codes-list');
    if (!el) return;
    try {
        const codes = await API.get('/cost-codes?include_inactive=true');
        if (!codes.length) {
            el.innerHTML = '<div style="font-size:11px; color:var(--text-muted);">No cost codes yet. Add your own, or load the standard CSI list.</div>';
            return;
        }
        const parentSel = document.getElementById('new-cc-parent');
        if (parentSel) parentSel.innerHTML = '<option value="">(top level)</option>' + codes.filter(c => c.is_active).map(c => `<option value="${c.id}">${'\u00a0\u00a0'.repeat(c.depth || 0)}${escapeHtml(c.label)}</option>`).join('');
        el.innerHTML = `<div class="table-container"><table>
            <thead><tr><th scope="col">Code</th><th scope="col">Name</th><th scope="col">Type</th><th scope="col">Default account</th><th scope="col">Status</th><th scope="col">Actions</th></tr></thead>
            <tbody>` + codes.map(c => `<tr>
                <td style="padding-left:${8 + (c.depth || 0) * 16}px">${c.depth ? '<span style="color:#aaa">└ </span>' : ''}<code>${escapeHtml(c.code)}</code></td>
                <td>${escapeHtml(c.name)}</td>
                <td>${escapeHtml(c.cost_type)}</td>
                <td>${escapeHtml(c.account_name || '')}</td>
                <td>${c.is_active ? 'Active' : 'Inactive'}</td>
                <td class="actions">
                    <button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.renameCostCode(${c.id})">Rename</button>
                    <button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.toggleCostCode(${c.id}, ${!c.is_active})">${c.is_active ? 'Deactivate' : 'Activate'}</button>
                </td>
            </tr>`).join('') + `</tbody></table></div>`;
    } catch (err) {
        el.innerHTML = `<div style="color:var(--danger); font-size:11px;">${escapeHtml(err.message)}</div>`;
    }
};

SettingsPage.addCostCode = async function () {
    const code = (document.getElementById('new-cc-code')?.value || '').trim();
    const name = (document.getElementById('new-cc-name')?.value || '').trim();
    const cost_type = document.getElementById('new-cc-type')?.value || 'other';
    const parentVal = document.getElementById('new-cc-parent')?.value;
    if (!code || !name) { toast('Enter a code and a name', 'error'); return; }
    try {
        await API.post('/cost-codes', { code, name, cost_type, parent_id: parentVal ? parseInt(parentVal) : null });
        document.getElementById('new-cc-code').value = '';
        document.getElementById('new-cc-name').value = '';
        toast('Cost code added');
        SettingsPage.loadCostCodes();
    } catch (err) { toast(err.message, 'error'); }
};

SettingsPage.loadStandardCostCodes = async function () {
    try {
        const codes = await API.post('/cost-codes/standard', {});
        toast(`${codes.length} cost codes active`);
        SettingsPage.loadCostCodes();
    } catch (err) { toast(err.message, 'error'); }
};

SettingsPage.renameCostCode = async function (id) {
    const name = prompt('New cost code name:');
    if (!name || !name.trim()) return;
    try {
        await API.put(`/cost-codes/${id}`, { name: name.trim() });
        toast('Cost code renamed');
        SettingsPage.loadCostCodes();
    } catch (err) { toast(err.message, 'error'); }
};

SettingsPage.toggleCostCode = async function (id, active) {
    try {
        await API.put(`/cost-codes/${id}`, { is_active: active });
        toast(active ? 'Cost code activated' : 'Cost code deactivated');
        SettingsPage.loadCostCodes();
    } catch (err) { toast(err.message, 'error'); }
};

SettingsPage.showCostCodeImport = function () {
    openModal('Import Cost Codes', `
        <p style="font-size:12px;margin:0 0 8px 0">Paste CSV rows as <code>code,name,cost_type,parent_code</code> (header optional). Existing codes are updated, parents linked afterwards, so order doesn't matter.</p>
        <textarea id="cc-import-csv" rows="12" style="width:100%;font-family:monospace;font-size:12px" placeholder="03,Concrete,subcontract,
03-300,Cast-in-place concrete,subcontract,03
03-310,Footings,subcontract,03-300"></textarea>
        <div class="form-actions">
            <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
            <button type="button" class="btn btn-primary" onclick="SettingsPage.importCostCodes()">Import</button>
        </div>`);
};

SettingsPage.importCostCodes = async function () {
    const csv = document.getElementById('cc-import-csv')?.value || '';
    if (!csv.trim()) { toast('Paste some rows first', 'error'); return; }
    try {
        const r = await API.post('/cost-codes/import', { csv });
        toast(`${r.created} created, ${r.updated} updated${r.errors.length ? `, ${r.errors.length} skipped: ${r.errors[0]}` : ''}`, r.errors.length ? 'error' : undefined);
        closeModal();
        SettingsPage.loadCostCodes();
    } catch (err) { toast(err.message, 'error'); }
};

// --- Cost types (Settings > Cost Types) ----------------------------------
SettingsPage._accountsCache = null;
SettingsPage._accountOptions = async function (selected) {
    if (!SettingsPage._accountsCache) { try { SettingsPage._accountsCache = await API.get('/accounts'); } catch (e) { SettingsPage._accountsCache = []; } }
    return '<option value="">--</option>' + SettingsPage._accountsCache.map(a => `<option value="${a.id}" ${selected === a.id ? 'selected' : ''}>${escapeHtml((a.account_number ? a.account_number + ' ' : '') + a.name)}</option>`).join('');
};

SettingsPage.loadCostTypes = async function () {
    const el = document.getElementById('cost-types-list');
    if (!el) return;
    try {
        const types = await API.get('/cost-types?include_inactive=true');
        const rows = [];
        for (const t of types) {
            rows.push(`<tr data-ct="${t.id}">
                <td><code>${escapeHtml(t.code)}</code></td>
                <td><input class="ct-name" value="${escapeHtml(t.name)}" style="width:130px"></td>
                <td style="text-align:center"><input type="checkbox" class="ct-labor" ${t.is_labor ? 'checked' : ''}></td>
                <td><input type="number" step="0.01" class="ct-burden" value="${t.burden_pct ?? ''}" style="width:70px" placeholder="%" aria-label="Flat burden percent"></td>
                <td>${t.is_labor ? `<select class="ct-burden-method" aria-label="Burden method" title="Flat: the % above posts with each time entry. Payroll: the pay run distributes actual employer taxes + job-routed benefit codes by hours — only for stubs built from time entries (Use approved time entries on the pay run); hours typed on a stub leave that employee's burden in the pool.">
                    <option value="flat" ${t.burden_method !== 'payroll' ? 'selected' : ''}>Flat %</option>
                    <option value="payroll" ${t.burden_method === 'payroll' ? 'selected' : ''}>Actual payroll</option>
                </select>` : '—'}</td>
                <td><select class="ct-default">${await SettingsPage._accountOptions(t.default_account_id)}</select></td>
                <td><select class="ct-offset">${await SettingsPage._accountOptions(t.offset_account_id)}</select></td>
                <td><select class="ct-burden-offset">${await SettingsPage._accountOptions(t.burden_offset_account_id)}</select></td>
                <td class="actions">
                    <button type="button" class="btn btn-sm btn-primary" onclick="SettingsPage.saveCostType(${t.id})">Save</button>
                    <button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.toggleCostType(${t.id}, ${!t.is_active})">${t.is_active ? 'Deactivate' : 'Activate'}</button>
                </td>
            </tr>`);
        }
        el.innerHTML = `<div class="table-container"><table style="font-size:12px">
            <thead><tr><th scope="col">Code</th><th scope="col">Name</th><th scope="col">Labor?</th><th scope="col">Burden %</th><th scope="col">Burden method</th><th scope="col">Cost account</th><th scope="col">Offset account</th><th scope="col">Burden offset</th><th scope="col">Actions</th></tr></thead>
            <tbody>${rows.join('')}</tbody></table></div>`;
    } catch (err) {
        el.innerHTML = `<div style="color:var(--danger); font-size:11px;">${escapeHtml(err.message)}</div>`;
    }
};

SettingsPage.saveCostType = async function (id) {
    const tr = document.querySelector(`[data-ct="${id}"]`);
    if (!tr) return;
    const sel = cls => { const v = tr.querySelector(cls)?.value; return v ? parseInt(v) : null; };
    const burden = tr.querySelector('.ct-burden')?.value;
    try {
        await API.put(`/cost-types/${id}`, {
            name: tr.querySelector('.ct-name').value.trim(),
            is_labor: tr.querySelector('.ct-labor').checked,
            burden_pct: burden === '' ? null : parseFloat(burden),
            burden_method: tr.querySelector('.ct-burden-method')?.value || undefined,
            default_account_id: sel('.ct-default'),
            offset_account_id: sel('.ct-offset'),
            burden_offset_account_id: sel('.ct-burden-offset'),
        });
        toast('Cost type saved');
        SettingsPage.loadCostTypes();
    } catch (err) { toast(err.message, 'error'); }
};

SettingsPage.addCostType = async function () {
    const code = (document.getElementById('new-ct-code')?.value || '').trim();
    const name = (document.getElementById('new-ct-name')?.value || '').trim();
    const is_labor = !!document.getElementById('new-ct-labor')?.checked;
    if (!code || !name) { toast('Enter a code and a name', 'error'); return; }
    try {
        await API.post('/cost-types', { code, name, is_labor });
        document.getElementById('new-ct-code').value = '';
        document.getElementById('new-ct-name').value = '';
        toast('Cost type added');
        SettingsPage.loadCostTypes();
    } catch (err) { toast(err.message, 'error'); }
};

SettingsPage.toggleCostType = async function (id, active) {
    try {
        await API.put(`/cost-types/${id}`, { is_active: active });
        SettingsPage.loadCostTypes();
    } catch (err) { toast(err.message, 'error'); }
};

SettingsPage.setupOffsets = async function () {
    try {
        await API.post('/cost-types/setup-offsets', {});
        SettingsPage._accountsCache = null;
        toast('Offset accounts ready — job cost entries and time postings can post');
        SettingsPage.loadCostTypes();
    } catch (err) { toast(err.message, 'error'); }
};

// --- Equipment (Settings > Equipment) ------------------------------------
SettingsPage.loadEquipment = async function () {
    const el = document.getElementById('equipment-list');
    if (!el) return;
    try {
        const list = await API.get('/equipment?include_inactive=true');
        if (!list.length) { el.innerHTML = '<div style="font-size:11px; color:var(--text-muted);">No equipment yet.</div>'; return; }
        el.innerHTML = `<div class="table-container"><table>
            <thead><tr><th scope="col">Code</th><th scope="col">Name</th><th scope="col" class="amount">$/hr</th><th scope="col">Cost code</th><th scope="col">Status</th><th scope="col">Actions</th></tr></thead>
            <tbody>` + list.map(q => `<tr>
                <td><code>${escapeHtml(q.code || '')}</code></td>
                <td>${escapeHtml(q.name)}</td>
                <td class="amount">${formatCurrency(q.hourly_rate)}</td>
                <td>${escapeHtml(q.cost_code_label || '')}</td>
                <td>${q.is_active ? 'Active' : 'Inactive'}</td>
                <td class="actions">
                    <button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.rateEquipment(${q.id})">Set rate</button>
                    <button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.toggleEquipment(${q.id}, ${!q.is_active})">${q.is_active ? 'Deactivate' : 'Activate'}</button>
                </td>
            </tr>`).join('') + `</tbody></table></div>`;
    } catch (err) {
        el.innerHTML = `<div style="color:var(--danger); font-size:11px;">${escapeHtml(err.message)}</div>`;
    }
};

SettingsPage.addEquipment = async function () {
    const code = (document.getElementById('new-eq-code')?.value || '').trim();
    const name = (document.getElementById('new-eq-name')?.value || '').trim();
    const rate = parseFloat(document.getElementById('new-eq-rate')?.value) || 0;
    if (!name) { toast('Enter a name', 'error'); return; }
    try {
        await API.post('/equipment', { code: code || null, name, hourly_rate: rate });
        document.getElementById('new-eq-name').value = '';
        toast('Equipment added');
        SettingsPage.loadEquipment();
    } catch (err) { toast(err.message, 'error'); }
};

SettingsPage.rateEquipment = async function (id) {
    const v = prompt('Hourly rate:');
    if (v === null) return;
    try {
        await API.put(`/equipment/${id}`, { hourly_rate: parseFloat(v) || 0 });
        SettingsPage.loadEquipment();
    } catch (err) { toast(err.message, 'error'); }
};

SettingsPage.toggleEquipment = async function (id, active) {
    try {
        await API.put(`/equipment/${id}`, { is_active: active });
        SettingsPage.loadEquipment();
    } catch (err) { toast(err.message, 'error'); }
};

