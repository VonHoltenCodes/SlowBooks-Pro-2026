/**
 * Settings — QuickBooks 2003 had a 12-tab preferences dialog; we
 * condensed everything into a single page because nobody needs 12 tabs
 * for company name and tax rate.
 */
const SettingsPage = {
    // The settings this page saves, the logo and the backups are the
    // administrator's: the server refuses them to every other role. A
    // backup is the whole company, password hashes and payroll records
    // included, so only an administrator downloads one too. What is drawn
    // before the role is known is marked data-admin (App.adminPass).
    _isAdmin() {
        return typeof App === 'undefined' || !App.role || App.role === 'admin';
    },

    // The default tax rate as the field can hold it: up to four decimals. A
    // default saved before 2.18 with more would fail the field's step and
    // hold back Save Settings for the whole page; it shows (and saves)
    // rounded to four, which is all a document keeps.
    _ratePercent(value) {
        const n = Number(value);
        return Number.isFinite(n) ? String(+n.toFixed(4)) : '0.0';
    },

    // The server echoes a saved secret back as this fixed 8-character string
    // (settings_service.SECRET_PLACEHOLDER), never the real length, so the
    // field shows a value is set without leaking how long it is. A new user
    // read it as a truncated password (#447); the note below says what it is.
    _SECRET_PLACEHOLDER: '********',

    // Said of a secret field whose value is the placeholder: the value is
    // there, this is not it, and you only type to replace it. In the
    // section's note since #977 (the field points at it, _secretDescribed).
    _secretNote(value) {
        return value === SettingsPage._SECRET_PLACEHOLDER
            ? 'Saved. Shown as a placeholder; type a new value only to change it.'
            : '';
    },

    // The secret's entry in its section's note, when it has something to say.
    _secretEntry(value, name, id) {
        const text = SettingsPage._secretNote(value);
        return text ? { name: name, id: id, text: text } : null;
    },

    // ... and the field's pointer at that entry, only when there is one.
    _secretDescribed(value, id) {
        return SettingsPage._secretNote(value) ? ' aria-describedby="' + id + '"' : '';
    },

    // The fiscal-year start-month options, value "1".."12" (#444). Built with
    // string concatenation, not a template literal, so this helper adds no
    // backtick-returning statement ahead of the page's render function (the
    // unsaved-changes guard test slices the file at the first one).
    _monthOptions(selected) {
        const names = ['January', 'February', 'March', 'April', 'May', 'June',
            'July', 'August', 'September', 'October', 'November', 'December'];
        const opt = (name, i) => '<option value="' + (i + 1) + '"' +
            (String(i + 1) === String(selected) ? ' selected' : '') + '>' + name + '</option>';
        return names.map(opt).join('');
    },

    // The clearing-account card for one provider (#583): a toggle and, when
    // on, pickers for the clearing (asset) and fee (expense) accounts.
    // Built with string concatenation — like _monthOptions, no backtick
    // ahead of the render function (the unsaved-changes guard test slices
    // the file at the first backtick-returning statement).
    _clearingAccountOptions(accounts, selectedId, type, defaultLabel) {
        // The same filter the settings save enforces: an active, ordinary
        // account of the right type — not a bank-feed or a control account
        // (A/R, Undeposited Funds, …).
        const pick = (Array.isArray(accounts) ? accounts : []).filter(a =>
            a.account_type === type && !a.bank_kind && a.is_active !== false
            && !a.is_control);
        const sel = selectedId ? String(selectedId) : '';
        let html = '<option value=""' + (sel ? '' : ' selected') + '>' +
            escapeHtml(defaultLabel) + '</option>';
        let matched = false;
        for (const a of pick) {
            const label = accountWords(a, null, ' ');
            const isSel = String(a.id) === sel;
            if (isSel) matched = true;
            html += '<option value="' + a.id + '"' + (isSel ? ' selected' : '') +
                '>' + escapeHtml(label) + '</option>';
        }
        // A saved id that isn't in the list (the chart failed to load, or the
        // account went inactive or changed type): keep it selected so Save
        // doesn't silently blank the setting; the recorder falls back to the
        // default account until it's fixed here.
        if (sel && !matched) {
            html += '<option value="' + escapeHtml(sel) + '" selected>(unavailable) #' +
                escapeHtml(sel) + '</option>';
        }
        return html;
    },

    _clearingHtml(provider, label, s, accounts) {
        const on = s[provider + '_clearing_enabled'] === 'true';
        const clearingSel = s[provider + '_clearing_account_id'];
        const feeSel = s[provider + '_fee_account_id'];
        // Its own group, labelled by the provider heading, so each provider's
        // "Clearing Account"/"Fee Account" fields read as that provider's and
        // not three that a screen reader can't tell apart (#198 field names).
        let h = '<div role="group" aria-labelledby="settings-h-' + provider + '">';
        h += '<label style="font-weight:normal; font-size:11px; display:block; margin:6px 0 2px;">';
        h += '<input type="checkbox" data-bool name="' + provider + '_clearing_enabled"';
        h += (on ? ' checked' : '');
        h += ' onchange="SettingsPage._toggleClearing(\'' + provider + '\')"';
        // what clearing does is said once, in the Online Payments note (#977)
        h += ' aria-describedby="settings-clearing-help"';
        h += ' style="width:auto; vertical-align:middle; margin-right:6px;">';
        h += 'Post to a clearing account</label>';
        // A plain wrapper carries [hidden] so the pickers really disappear
        // when the toggle is off — a .form-grid sets display:grid, which
        // would otherwise win over the hidden attribute.
        h += '<div id="' + provider + '-clearing-fields"' + (on ? '' : ' hidden') + '>';
        h += '<div class="form-grid">';
        h += '<div class="form-group"><label>Clearing Account</label>';
        h += '<select name="' + provider + '_clearing_account_id">' +
            SettingsPage._clearingAccountOptions(accounts, clearingSel, 'asset',
                'Default (' + label + ' Clearing)') + '</select></div>';
        h += '<div class="form-group"><label>Fee Account</label>';
        h += '<select name="' + provider + '_fee_account_id">' +
            SettingsPage._clearingAccountOptions(accounts, feeSel, 'expense',
                'Default (Merchant Account Fees)') + '</select></div>';
        h += '</div>';  // close .form-grid
        h += '</div>';  // close #<provider>-clearing-fields
        // Awaiting payout (#584): the clearing account's balance and a link
        // to its register, plus Record payout for a payout with no feed.
        // Outside the pickers so it stays when clearing is turned off while
        // money is still waiting: payouts continue until the balance is zero.
        const clearingAcct = (Array.isArray(accounts) ? accounts : []).find(a =>
            clearingSel ? String(a.id) === String(clearingSel) : a.name === label + ' Clearing');
        const waiting = clearingAcct ? (parseFloat(clearingAcct.balance) || 0) : 0;
        if (clearingAcct && (on || waiting > 0)) {
            const bal = formatCurrency(waiting);
            const href = App.accountRegisterHref({ id: clearingAcct.id, bank_kind: null });
            h += '<div style="font-size:11px; margin:6px 0;">Awaiting payout: ';
            h += '<strong>' + escapeHtml(bal) + '</strong> ';
            h += '<a href="' + href + '">view register</a> ';
            h += '<button type="button" class="btn btn-sm btn-secondary" data-write ';
            h += 'onclick="SettingsPage.showProviderPayout(\'' + provider + '\', \'' +
                label + '\')" style="margin-left:6px;">Record payout</button></div>';
        }
        h += '</div>';  // close the provider group
        return h;
    },

    _toggleClearing(provider) {
        const box = document.querySelector('[name="' + provider + '_clearing_enabled"]');
        const fields = document.getElementById(provider + '-clearing-fields');
        if (box && fields) fields.hidden = !box.checked;
    },

    // Make Deposits' destination (#734, #735): a labelled picker of the
    // chart's active bank accounts, blank for none (the list's first, as
    // before), and a labelled box per bank account for the list it offers,
    // none ticked for every one. A saved id that is no longer an active bank
    // account stays selected (or ticked) and named, so Save says what is
    // wrong instead of dropping it. String concatenation, like
    // _payrollAccountsHtml.
    _depositAccountsHtml(s, accounts) {
        const all = Array.isArray(accounts) ? accounts : [];
        const banks = all.filter(a => a.bank_kind === 'bank' && a.is_active !== false);
        const sel = String(s.default_deposit_account_id || '');
        let h = '<div class="form-grid">';
        h += '<div class="form-group full-width"><label for="settings-default-deposit-account">Default deposit account</label>';
        h += '<select name="default_deposit_account_id" id="settings-default-deposit-account" data-no-search aria-describedby="settings-default-deposit-account-help">';
        h += '<option value=""' + (sel ? '' : ' selected') + '>None: the first bank account on the list</option>';
        let matched = false;
        for (const a of banks) {
            const isSel = String(a.id) === sel;
            if (isSel) matched = true;
            h += '<option value="' + Number(a.id) + '"' + (isSel ? ' selected' : '') + '>' + escapeHtml(accountWords(a)) + '</option>';
        }
        if (sel && !matched) {
            const gone = all.find(a => String(a.id) === sel);
            h += '<option value="' + escapeHtml(sel) + '" selected>(unavailable) ' + escapeHtml(gone ? accountWords(gone) : '#' + sel) + '</option>';
        }
        // its description is in the Deposits note (#977)
        h += '</select></div>';
        let chosen = [];
        try { chosen = JSON.parse(s.deposit_account_ids || '[]'); } catch (err) { chosen = []; }
        chosen = (Array.isArray(chosen) ? chosen : []).map(String);
        const ticked = new Set(chosen);
        h += '<fieldset class="form-group full-width" aria-describedby="settings-deposit-accounts-help" style="border:0; padding:0; margin:0;">';
        h += '<legend style="font-weight:700; font-size:11px; padding:0;">Deposit into these accounts</legend>';
        // each box's own words, as typed: the form's labels are upper-case
        // by CSS, and VoiceOver read "1000 - CHECKING" (#813)
        const box = (id, words) => '<label style="display:block; font-weight:normal; text-transform:none; letter-spacing:0;">'
            + '<input type="checkbox" name="deposit-account:' + escapeHtml(id) + '" data-deposit-account="' + escapeHtml(id) + '"'
            + (ticked.has(id) ? ' checked' : '') + ' style="width:auto; vertical-align:middle; margin-right:6px;">'
            + escapeHtml(words) + '</label>';
        for (const a of banks) h += box(String(Number(a.id)), accountWords(a));
        for (const id of chosen) {
            if (banks.some(a => String(a.id) === id)) continue;
            const gone = all.find(a => String(a.id) === id);
            h += box(id, '(unavailable) ' + (gone ? accountWords(gone) : '#' + id));
        }
        if (!banks.length && !chosen.length) h += '<div style="font-size:11px; color:var(--text-muted);">The chart has no active bank account.</div>';
        h += '</fieldset>';
        h += '</div>';
        return h;
    },

    // the ticked Deposit into these accounts boxes (#735), as the list to save
    _depositAccountsToSave(boxes) {
        return boxes.filter(b => b.checked).map(b => parseInt(b.getAttribute('data-deposit-account'), 10)).filter(n => n > 0);
    },

    // Where a pay run posts (#620): one picker per piece, the seeded default
    // first and named ("Default (1000 Checking)"). The choices come from
    // GET /settings/payroll-accounts, filtered by the same rule the save
    // enforces: a bank or a liability for net pay, an expense or cost of
    // goods sold account for the two expenses, a liability that is not a
    // credit card or a control account for each payable. String
    // concatenation, like _clearingHtml: no backtick-returning statement
    // ahead of render() (the unsaved-changes guard test slices there).
    _payrollAccountsHtml(data) {
        if (!data || !Array.isArray(data.pieces)) {
            return '<div style="font-size:11px; color:var(--text-muted);">The payroll accounts could not be loaded; the pay run posts to its usual accounts.</div>';
        }
        const choices = data.choices || {};
        let h = '<div class="form-grid">';
        for (const p of data.pieces) {
            const sel = p.chosen ? String(p.chosen) : '';
            const defaultLabel = p.default ? 'Default (' + p.default.label + ')' : 'Default (not in the chart)';
            h += '<div class="form-group"><label>' + escapeHtml(p.label);
            // a piece's hint is its entry in the section's note (#977)
            // data-admin on the picker itself: its type-ahead box takes the
            // mark (combobox.js _takeName), so a bookkeeper sees it locked
            h += '</label><select name="' + p.key + '"'
                + (p.hint ? ' aria-describedby="' + SettingsPage._payrollHintId(p.key) + '"' : '') + ' data-admin>';
            h += '<option value=""' + (sel ? '' : ' selected') + '>' + escapeHtml(defaultLabel) + '</option>';
            let matched = false;
            const seen = new Set();
            for (const kind of (p.kinds || [])) {
                for (const a of (choices[kind] || [])) {
                    if (seen.has(a.id)) continue;
                    seen.add(a.id);
                    const isSel = String(a.id) === sel;
                    if (isSel) matched = true;
                    h += '<option value="' + a.id + '"' + (isSel ? ' selected' : '') + '>' + escapeHtml(a.label) + '</option>';
                }
            }
            // A saved id not in the list (the account went inactive or
            // changed type): keep it selected so Save doesn't silently blank
            // the setting; the sentence under the picker says what is wrong,
            // and the pay run is refused until it is fixed here.
            if (sel && !matched) {
                h += '<option value="' + escapeHtml(sel) + '" selected>(unavailable) #' + escapeHtml(sel) + '</option>';
            }
            h += '</select>';
            if (p.problem) {
                h += '<div class="form-hint" style="color:var(--text-warning); margin-top:4px;">' + escapeHtml(p.problem) + '</div>';
            }
            h += '</div>';
        }
        h += '</div>';
        return h;
    },

    // Each piece's hint from the server, as entries of the Payroll Accounts
    // note (#977): the piece's own name over it.
    _payrollAccountEntries(data) {
        if (!data || !Array.isArray(data.pieces)) return [];
        return data.pieces.filter(p => p.hint).map(p => ({
            name: p.label, id: SettingsPage._payrollHintId(p.key), text: escapeHtml(p.hint),
        }));
    },
    _payrollHintId(key) {
        return 'settings-payroll-hint-' + String(key).replace(/[^A-Za-z0-9_-]/g, '-');
    },

    // Where each encrypted setting is entered again, for the notice below.
    _SECRET_PLACES: {
        smtp_password: 'the email password, under Email (SMTP)',
        stripe_secret_key: 'the Stripe secret key, under Online Payments',
        stripe_webhook_secret: 'the Stripe webhook secret, under Online Payments',
        paypal_client_secret: 'the PayPal client secret, under Online Payments',
        square_access_token: 'the Square access token, under Online Payments',
        square_webhook_signature_key: 'the Square webhook signature key, under Online Payments',
        simplefin_access_url: 'the SimpleFIN bank feed: connect again on Banking',
        mercury_api_token: 'the Mercury bank feed: connect again on Banking',
        mercury_webhook_secret: 'the Mercury webhook signing secret, on Banking',
        closing_date_password: 'the closing-date password, under Closing Date',
        ai_api_key: 'the AI Insights API key, under AI Insights',
    },

    // Passwords and keys saved under a settings key this install no longer
    // has read as not set (a Docker container recreated before 2.18 lost
    // its key). Say which, and where each is entered again.
    async loadUnreadableSecrets() {
        const el = document.getElementById('settings-unreadable');
        if (!el || !SettingsPage._isAdmin()) return;
        let keys = [];
        try {
            keys = (await API.get('/settings/unreadable-secrets')).keys || [];
        } catch (e) {
            return;
        }
        const places = [...new Set(keys.map(k => SettingsPage._SECRET_PLACES[k] || k))];
        if (!places.length) { el.hidden = true; return; }
        el.innerHTML = `Some saved passwords and keys can't be read on this install, so what uses them is off until
            they are entered again: ${places.map(p => `<em>${escapeHtml(p)}</em>`).join('; ')}. They were saved
            under a settings key this install no longer has.`;
        el.hidden = false;
    },

    // Said under the logo when the upgrade to 2.18.0 copied it in from the
    // folder every company shared before: that held ONE logo file for all of
    // them, so it may be another company's.
    _logoNote(logo) {
        let text = '';
        if (logo && logo.missing) {
            text = 'Your logo file was not in the shared folder when these books were upgraded. Upload it again.';
        } else if (logo && logo.from_shared_folder) {
            text = "This logo was copied from the folder earlier versions shared between companies. If this isn't your logo, upload it again.";
        }
        return text ? `<div id="company-logo-note" style="font-size:10px; color:var(--text-muted); margin-bottom:6px;">${escapeHtml(text)}</div>` : '';
    },

    // My Preferences (#480, #482): what each person sets for themselves,
    // kept per sign-in (the preferences store: /api/preferences/data_entry
    // and /api/preferences/reports), so it follows them to another computer
    // and a second user keeps their own. Outside the company-settings form:
    // no administrator's Save, and every role can set its own. Each
    // checkbox saves itself.
    _myPreferencesHtml() {
        const box = (id, key, label, hint, onchange = 'SettingsPage.saveDataEntryPref(this)') =>
            '<div class="form-group full-width" style="margin-bottom:6px;">'
            + '<label for="' + id + '" style="font-weight:normal;">'
            + '<input type="checkbox" id="' + id + '"' + (key ? ' data-pref="' + key + '"' : '') + ' data-readonly-ok aria-describedby="' + id + '-hint"'
            + ' style="width:auto; vertical-align:middle; margin-right:6px;" onchange="' + onchange + '">'
            + label + '</label>'
            + '<div id="' + id + '-hint" style="font-size:11px; color:var(--text-muted); margin:2px 0 0 22px;">' + hint + '</div></div>';
        return '<div class="settings-section" data-group="preferences" id="settings-my-preferences" data-own-preference role="group" aria-labelledby="settings-h-my-preferences">'
            + '<div class="settings-h-row"><h3 id="settings-h-my-preferences">My Preferences</h3>'
            + hintList('My Preferences', [
                { id: 'settings-my-preferences-help', text: 'Yours alone: kept with your sign-in, saved as soon as you change one.' },
                { name: 'Text size', id: 'pref-text-size-hint', text: SettingsPage._textSizeHelp() },
                typeof FastRack !== 'undefined' ? { name: 'Fast Rack', id: 'fr-hint', text: FastRack.hintText() } : null,
            ]) + '</div>'
            + box('pref-enter-moves', 'enter_moves', 'Enter key moves between fields',
                'Enter goes to the next field; Enter in the last field (or Save) saves. Off: Enter saves the form, and posts the register&rsquo;s new entry.')
            + box('pref-auto-decimal', 'auto_decimal', 'Automatically place the decimal point',
                'Type 12345 in an amount for $123.45. A decimal point you type yourself is kept.')
            + box('pref-report-refresh', null, 'Refresh reports automatically',
                'An open report runs again after a change to the books, here or in another window, and when you come back to the window. '
                + 'Off: use the report&rsquo;s Refresh button.', 'SettingsPage.saveReportRefresh(this)')
            + SettingsPage._textSizeHtml()
            // the top bar's buttons (#912): arranged here, saved with Save
            + (typeof FastRack !== 'undefined' ? FastRack.editorHtml() : '')
            + '</div>';
    },

    // Text size (#717): kept on this computer, not with the sign-in, and
    // set at once (text_size.js), so it has no Save either.
    _textSizeHtml() {
        const sizes = typeof SlowbooksTextSize !== 'undefined' ? SlowbooksTextSize : null;
        const now = sizes ? sizes.read() : 100;
        return '<div class="form-group" style="margin:10px 0 6px;">'
            + '<label for="pref-text-size">Text size</label>'
            + '<select id="pref-text-size" data-text-size-control data-readonly-ok aria-describedby="pref-text-size-hint" style="width:auto; max-width:120px;"'
            + ' onchange="SlowbooksTextSize.apply(this.value)">'
            + [100, 115, 125, 150].map(n => '<option value="' + n + '"' + (n === now ? ' selected' : '') + '>' + n + '%</option>').join('')
            + '</select></div>';
    },

    // what Text size does, for the My Preferences note (#977)
    _textSizeHelp() {
        const keys = typeof App !== 'undefined' && App.isMac && App.isMac() ? '&#8984;' : 'Ctrl';
        return 'Makes the text larger on this computer, for every company and sign-in. '
            + 'In the desktop app, ' + keys + ' and + or &minus; change it too, and ' + keys + ' and 0 puts it back to 100%.';
    },

    loadMyPreferences() {
        // (the probes that draw this page without utils.js or reports.js
        // have no copy of one or the other)
        if (typeof document.querySelectorAll !== 'function') return;
        if (typeof FastRack !== 'undefined') FastRack.loadEditor();
        if (typeof DataEntryPrefs !== 'undefined') {
            const sync = () => document.querySelectorAll('#settings-my-preferences input[data-pref]')
                .forEach(el => { el.checked = DataEntryPrefs[el.dataset.pref] === true; });
            sync();
            // the latest saved, in case it was changed on another computer
            API.get('/preferences/data_entry')
                .then(pref => { DataEntryPrefs.set(pref && pref.value); sync(); })
                .catch(() => { /* the copy App.init read stands */ });
        }
        const refresh = document.getElementById('pref-report-refresh');
        if (typeof ReportsPage !== 'undefined' && refresh) {
            refresh.checked = ReportsPage.autoRefresh !== false;
            API.get('/preferences/reports')
                .then(pref => {
                    ReportsPage.setAutoRefresh(pref && pref.value);
                    refresh.checked = ReportsPage.autoRefresh;
                })
                .catch(() => { /* the copy App.init read stands */ });
        }
    },

    async saveDataEntryPref(el) {
        const before = DataEntryPrefs.value();
        DataEntryPrefs[el.dataset.pref] = !!el.checked;
        try {
            await API.put('/preferences/data_entry', { value: DataEntryPrefs.value() });
            toast('Preference saved');
        } catch (err) {
            DataEntryPrefs.set(before);
            el.checked = before[el.dataset.pref];
            toast(err.message, 'error');
        }
    },

    async saveReportRefresh(el) {
        const before = ReportsPage.autoRefresh;
        ReportsPage.autoRefresh = !!el.checked;
        try {
            await API.put('/preferences/reports', { value: { auto_refresh: !!el.checked } });
            toast('Preference saved');
        } catch (err) {
            ReportsPage.autoRefresh = before;
            el.checked = before;
            toast(err.message, 'error');
        }
    },

    // A Transaction Warnings checkbox (#481): sent as "true" or "false"
    // (data-bool), off unless the setting says "true". `extra` is the
    // days box that finishes its sentence.
    // The box's name is its whole sentence, the days' unit included.
    _warnBox(s, key, id, label, extra) {
        const on = s[key] === 'true';
        const unit = extra ? ' ' + id + '-days-unit' : '';
        return '<div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-bottom:6px; font-size:12px;">'
            + '<input type="checkbox" id="' + id + '" name="' + key + '" value="true" data-bool'
            + ' aria-labelledby="' + id + '-label' + unit + '"'
            + ' aria-describedby="' + (SettingsPage._WARN_HELP[id] || '') + '"'
            + ' style="width:auto; margin:0;"' + (on ? ' checked' : '') + '>'
            + '<label id="' + id + '-label" for="' + id + '" style="font-weight:normal; margin:0;">' + label + '</label>' + extra + '</div>';
    },
    // each box's entry in the section's note (#977)
    _WARN_HELP: {
        'warn-date-past': 'warn-date-help',
        'warn-date-future': 'warn-date-help',
        'warn-duplicate-check': 'warn-duplicate-check-help',
        'warn-edit': 'warn-edit-help',
    },
    _warnDays(s, key, id, unit, fallback) {
        const value = escapeHtml(s[key] || String(fallback));
        return '<input type="number" id="' + id + '" name="' + key + '" min="1" max="3650" step="1" required value="' + value + '"'
            + ' aria-label="Number of ' + unit + '" style="width:70px; padding:2px 4px;">'
            + '<span id="' + id + '-unit" aria-hidden="true">' + unit + '</span>';
    },

    async render() {
        const s = await API.get('/settings');
        // Default Terms offers the terms list (#664)
        await PaymentTerms.load(true);
        const logo = s.company_logo_path ? await API.get('/uploads/logo').catch(() => null) : null;
        // The clearing-account pickers (#583) list the chart; a failed load
        // leaves them empty rather than breaking the whole settings page.
        const accounts = await API.get('/accounts').catch(() => []);
        // the payroll posting pickers (#620); a failed load says so in place
        const payrollAccounts = await API.get('/settings/payroll-accounts').catch(() => null);
        // what the books hold; the closing date's state line describes this
        SettingsPage._savedClosingDate = s.closing_date || '';
        setTimeout(() => {
            SettingsPage.loadBackups();
            SettingsPage.loadCompanyFile();
            SettingsPage.loadLegacyFiles();
            SettingsPage.loadEmailTemplates();
            SettingsPage.loadReminderRules();
            SettingsPage.loadInvoiceMessages();
            if (Terms.isNonprofit()) SettingsPage.loadInvoiceMessages('giving_statement');
            SettingsPage.loadPaymentTerms();
            SettingsPage.loadAiConfig();
            SettingsPage.loadCheckBankDetails();
            SettingsPage.loadClasses();
            SettingsPage.loadCostCodes();
            SettingsPage.loadCostTypes();
            if (Terms.isConstruction()) SettingsPage.loadTradePackages();
            SettingsPage.loadEquipment();
            SettingsPage.loadUsers();
            SettingsPage.loadApiTokens();
            SettingsPage.loadSignInPref();
            SettingsPage.loadOcrStatus();
            SettingsPage.loadSwitchboard();
            SettingsPage.loadOcrEnginePref();
            SettingsPage.loadPostSoundPref();
            SettingsPage.loadMyPreferences();
            SettingsPage.loadUnreadableSecrets();
            SettingsPage.loadEmailLog(true);
            // the groups down the left, from the sections now on the page
            // (before scrollToFocus: a jump opens its section's group)
            SettingsPage._initGroups();
            SettingsPage.scrollToFocus();
            SettingsPage._installLeaveGuard();
            SettingsPage._markClean();
        }, 0);
        // The form, then the page around it: the list of groups on the left
        // (filled in from the sections' data-group once they are drawn,
        // SettingsPage._initGroups), and My Preferences and the form in the
        // one panel the list shows a group of.
        const form = `
            <!-- data-admin-fields: the fields this form saves (PUT /api/settings)
                 are the administrator's. Any other sign-in sees them locked, with
                 the sentence below (App.adminPass). AI Insights and the lists
                 further down (templates, classes, cost types and codes, equipment)
                 save themselves, and a bookkeeper keeps them. -->
            <form id="settings-form" data-admin-fields onsubmit="SettingsPage.save(event)"
                oninput="SettingsPage._updateDirty()" onchange="SettingsPage._updateDirty()">
                <div class="hint hint--locked hidden" data-admin-note style="margin-bottom:10px;">Company settings are changed by an administrator.</div>
                <div class="settings-section" data-group="company" role="group" aria-labelledby="settings-h-company-information">
                    <div class="settings-h-row"><h3 id="settings-h-company-information">Company Information</h3>${hintList('Company Information', [
                        { name: 'Display name', id: 'settings-display-name-help', text: 'Shown on invoices, estimates, statements and receipts. Tax and payroll forms keep the company name.' },
                        s.company_type === 'construction' ? { name: 'Contractor license #', id: 'company-license-help', text: 'Printed in the header of the estimate PDF.' } : null,
                        s.company_type === 'construction' ? { name: 'Default estimate terms, inclusions and exclusions', id: 'estimate-terms-help', text: "A new estimate starts with this text; edit it on any estimate. It prints above the acceptance block on the owner's PDF." } : null,
                        s.company_type === 'construction' ? { name: 'Estimate style', id: 'estimate-style-help', text: 'How a new estimate is laid out; an estimate that already uses packages, allowances, alternates or markups opens as a commercial bid.' } : null,
                        s.company_type === 'construction' ? { name: 'Default markup (%)', id: 'estimate-default-markup-help', hidden: s.estimate_style !== 'line_markup', text: 'A new estimate line starts with this markup on its cost; change it on any line.' } : null,
                        { name: 'Company Type', id: 'company-type-help', text: 'Nonprofit shows donors, pledges, donations and funds in place of customers, invoices, sales receipts and classes, and adds the net-asset accounts and statements. Construction adds the contractor accounts (retainage, customer deposits, use tax, Construction in Progress), the standard cost codes (only if you have none of your own yet), and a Sub vendor type. Your data does not change; switch back any time.' },
                        { name: 'Entity type', id: 'entity-type-help', text: "Your company's legal form. It decides which tax return applies and, chosen before the first posting, the equity accounts: owner's equity and draw; partner capital and distributions; or common stock, paid-in capital and shareholder distributions. Once the books have postings the chart is yours to rename under Chart of Accounts; nothing is reseeded. It never changes your books." },
                        { name: 'Taxed as', id: 'llc-tax-election-help', hidden: s.entity_type !== 'llc', text: 'How this LLC is taxed. A single-member LLC taxed as a sole proprietor files Schedule C; the others file a partnership or corporate return.' },
                        { name: 'Use account numbers', id: 'use-account-numbers-help', text: 'On, the chart has a Number column, accounts list by number, and every picker and report names an account "6100 - Name". Off, for a business that does not number its accounts: the number is left off the chart, the pickers, the reports and the PDFs, accounts list by name within their type, and an imported account without a number stays without one. A number is optional either way; an account keeps the number it has.' },
                        { name: 'First month of fiscal year', id: 'fiscal-year-start-month-help', text: "Reports' This/Last Fiscal Year presets, the dashboard's profit and nonprofit mode's year to date follow it. Payroll forms stay on the calendar year." },
                    ])}</div>
                    <div class="form-grid">
                        <div class="form-group full-width"><label>Company Name *</label>
                            <input name="company_name" value="${escapeHtml(s.company_name || '')}" required></div>
                        <div class="form-group full-width"><label for="settings-display-name">Display name</label>
                            <input id="settings-display-name" name="display_name" data-write aria-describedby="settings-display-name-help" value="${escapeHtml(s.display_name || '')}"></div>
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
                        ${s.company_type === 'construction' ? `<div class="form-group"><label for="company-license">Contractor license #</label>
                            <input id="company-license" name="company_license" aria-describedby="company-license-help" value="${escapeHtml(s.company_license || '')}"></div>
                        <div class="form-group full-width"><label for="estimate-terms">Default estimate terms, inclusions and exclusions</label>
                            <textarea id="estimate-terms" name="estimate_terms" rows="5" aria-describedby="estimate-terms-help">${escapeHtml(s.estimate_terms || '')}</textarea></div>
                        <div class="form-group"><label for="estimate-style">Estimate style</label>
                            <select id="estimate-style" name="estimate_style" aria-describedby="estimate-style-help" onchange="SettingsPage._onEstimateStyleChange(this)">
                                <option value="line_markup" ${s.estimate_style === 'line_markup' ? 'selected' : ''}>Markup per line</option>
                                <option value="commercial_bid" ${s.estimate_style !== 'line_markup' ? 'selected' : ''}>Commercial bid (packages, allowances, alternates, markups under the subtotal)</option>
                            </select></div>
                        <div class="form-group" id="estimate-default-markup-group" ${s.estimate_style !== 'line_markup' ? 'hidden' : ''}><label for="estimate-default-markup">Default markup (%)</label>
                            <input id="estimate-default-markup" name="estimate_default_markup_pct" aria-describedby="estimate-default-markup-help" type="number" step="0.0001" min="0" max="1000" value="${escapeHtml(s.estimate_default_markup_pct || '')}"></div>` : ''}
                        <div class="form-group full-width"><label for="company-type">Company Type</label>
                            <select id="company-type" name="company_type" aria-describedby="company-type-help" data-prev="${s.company_type === 'nonprofit' ? 'nonprofit' : (s.company_type === 'construction' ? 'construction' : 'business')}" onchange="SettingsPage.changeCompanyType(this)">
                                <option value="business" ${(s.company_type !== 'nonprofit' && s.company_type !== 'construction') ? 'selected' : ''}>Business</option>
                                <option value="nonprofit" ${s.company_type === 'nonprofit' ? 'selected' : ''}>Nonprofit</option>
                                <option value="construction" ${s.company_type === 'construction' ? 'selected' : ''}>Construction</option>
                            </select>
                        </div>
                        <div class="form-group"><label for="entity-type">Entity type</label>
                            <select id="entity-type" name="entity_type" aria-describedby="entity-type-help" onchange="SettingsPage._onEntityTypeChange(this)">
                                ${SettingsPage._entityOptions(s.entity_type)}
                            </select>
                            <div id="s-corp-note" style="font-size:10px; color:var(--text-muted); margin-top:4px;" ${SettingsPage._isSCorp(s) ? '' : 'hidden'}>${escapeHtml(SettingsPage.S_CORP_NOTE)}</div>
                        </div>
                        <div class="form-group" id="llc-election-group" ${s.entity_type === 'llc' ? '' : 'hidden'}>
                            <label for="llc-tax-election">Taxed as</label>
                            <select id="llc-tax-election" name="llc_tax_election" aria-describedby="llc-tax-election-help" onchange="SettingsPage._toggleSCorpNote()">
                                ${SettingsPage._llcElectionOptions(s.llc_tax_election)}
                            </select>
                        </div>
                        <div class="form-group full-width">
                            <label for="use-account-numbers" style="font-weight:normal;">
                                <input type="checkbox" id="use-account-numbers" name="use_account_numbers" value="true" data-bool aria-describedby="use-account-numbers-help" style="width:auto; vertical-align:middle; margin-right:6px;" ${s.use_account_numbers !== 'false' ? 'checked' : ''}>
                                Use account numbers
                            </label>
                        </div>
                        <div class="form-group full-width"><label for="fiscal-year-start-month">First month of fiscal year</label>
                            <select id="fiscal-year-start-month" name="fiscal_year_start_month" aria-describedby="fiscal-year-start-month-help">
                                ${SettingsPage._monthOptions(s.fiscal_year_start_month || '1')}
                            </select>
                        </div>
                    </div>
                </div>

                <div class="settings-section" data-group="company" role="group" aria-labelledby="settings-h-company-logo">
                    <div class="settings-h-row"><h3 id="settings-h-company-logo">Company Logo</h3>${hintList('Company Logo', [
                        SettingsPage._isAdmin() ? { name: 'Logo image', id: 'logo-upload-help', admin: true, text: 'PNG, JPG, GIF, WebP, or SVG &middot; max 5 MB &middot; 200&times;80 px recommended.' } : null,
                        s.company_logo_path ? { name: 'Show company logo on invoices', id: 'invoice-show-logo-help', text: 'Applies to PDF, Print, and emailed invoice attachments.' } : null,
                        s.company_logo_path ? { name: 'Logo only', id: 'invoice-logo-only-help', text: 'Print the logo without the company name heading on invoices, estimates, statements and receipts. Only applies when a logo is set.' } : null,
                    ])}</div>
                    <div class="form-grid">
                        <div class="form-group">
                            ${s.company_logo_path && !(logo && logo.missing) ? `<img id="company-logo-preview" src="${escapeHtml(s.company_logo_path)}" style="max-width:200px; max-height:80px; margin-bottom:8px; display:block;">` : ''}
                            ${SettingsPage._logoNote(logo)}
                            ${SettingsPage._isAdmin() ? `<input type="file" id="logo-upload" aria-label="Logo image" aria-describedby="logo-upload-help" data-admin accept="image/*" onchange="SettingsPage.uploadLogo(this)">
                            ${s.company_logo_path ? `<button type="button" class="btn btn-sm btn-secondary" data-admin data-write style="margin-left:6px;" onclick="SettingsPage.removeLogo()">Remove logo</button>` : ''}`
                            : `<div id="logo-admin-only" style="font-size:10px; color:var(--text-muted);">Only an administrator can change the logo.</div>`}
                            ${s.company_logo_path ? `<div style="margin-top:8px;">
                                <label for="invoice-show-logo" style="font-weight:normal;">
                                    <input type="checkbox" id="invoice-show-logo" name="invoice_show_logo" aria-describedby="invoice-show-logo-help" value="true" style="width:auto; vertical-align:middle; margin-right:6px;" ${s.invoice_show_logo !== 'false' ? 'checked' : ''}>
                                    Show company logo on invoices
                                </label>
                                <label for="invoice-logo-only" style="font-weight:normal; display:block; margin-top:8px;">
                                    <input type="checkbox" id="invoice-logo-only" name="invoice_logo_only" value="true" data-bool data-write aria-describedby="invoice-logo-only-help" style="width:auto; vertical-align:middle; margin-right:6px;" ${s.invoice_logo_only === 'true' ? 'checked' : ''}>
                                    Logo only
                                </label>
                            </div>` : ''}
                        </div>
                    </div>
                </div>

                <div class="settings-section" data-group="sales" role="group" aria-labelledby="settings-h-invoice-defaults">
                    <div class="settings-h-row"><h3 id="settings-h-invoice-defaults">${T('Invoice')} Defaults</h3>${hintList(`${T('Invoice')} Defaults`, [
                        { name: 'New lines and items are taxable', id: 'new-lines-taxable-help', text: `For a company that charges no sales tax. A line still takes its item's own setting, you can tick Tax on any line, and a tax-exempt ${T('customer')} is never charged.` },
                        { name: 'Refuse a sale of more than is on hand', id: 'inventory-refuse-oversell-help', text: `Off, an ${T('invoice')} or sales receipt that sells more of a stock item than is on hand is saved and the form says so; the count goes below zero until the receiving bill is entered. On, the sale is refused, naming the item and how many short.` },
                        { name: 'PDF Paper Size', id: 'pdf-paper-size-help', text: 'Checks, mailing labels and the IRS forms print on Letter whatever this says: they are measured for that stock.' },
                    ])}</div>
                    <div class="form-grid">
                        <div class="form-group"><label>Default Terms</label>
                            <select name="default_terms">
                                ${PaymentTerms.optionsHtml(s.default_terms || 'Net 30')}
                            </select></div>
                        <div class="form-group"><label>Default Tax Rate (%)</label>
                            <input name="default_tax_rate" type="number" min="0" max="100" step="0.0001"
                                title="A percent from 0 to 100, up to four decimal places: 8.875 means 8.875%"
                                value="${escapeHtml(SettingsPage._ratePercent(s.default_tax_rate))}"></div>
                        ${s.company_type === 'construction' ? `<div class="form-group"><label for="use-tax-rate">Use tax rate (%)</label>
                            <input id="use-tax-rate" name="use_tax_rate" type="number" min="0" max="100" step="0.0001"
                                title="What material bills on a job whose contractor pays the tax accrue to Use Tax Payable. Blank: the default tax rate."
                                placeholder="the default tax rate"
                                value="${escapeHtml(s.use_tax_rate ? SettingsPage._ratePercent(s.use_tax_rate) : '')}"></div>` : ''}
                        <div class="form-group full-width"><label for="new-lines-taxable">New lines and items are taxable</label>
                            <select id="new-lines-taxable" name="new_lines_taxable" aria-describedby="new-lines-taxable-help">
                                <option value="true" ${s.new_lines_taxable !== 'false' ? 'selected' : ''}>Yes: a new line's Tax box starts ticked, and a new item is taxable</option>
                                <option value="false" ${s.new_lines_taxable === 'false' ? 'selected' : ''}>No: new lines and new items start untaxed</option>
                            </select>
                        </div>
                        <div class="form-group full-width">
                            <label for="inventory-refuse-oversell" style="font-weight:normal;">
                                <input type="checkbox" id="inventory-refuse-oversell" name="inventory_refuse_oversell" value="true" data-bool aria-describedby="inventory-refuse-oversell-help" style="width:auto; vertical-align:middle; margin-right:6px;" ${s.inventory_refuse_oversell === 'true' ? 'checked' : ''}>
                                Refuse a sale of more than is on hand
                            </label>
                        </div>
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
                        <div class="form-group"><label>PDF Paper Size</label>
                            <select name="pdf_paper_size" aria-describedby="pdf-paper-size-help" title="The paper every document and report PDF is drawn on; checks, labels and the tax forms stay on Letter, the stock they print on">
                                <option value="letter" ${s.pdf_paper_size !== 'a4' ? 'selected' : ''}>US Letter</option>
                                <option value="a4" ${s.pdf_paper_size === 'a4' ? 'selected' : ''}>A4</option>
                            </select></div>
                        <div class="form-group"><label>Report Basis</label>
                            <select name="report_basis" title="${escapeHtml(`The basis the ${T('Profit & Loss')}, ${T('Balance Sheet')}, Trial Balance, General Ledger and related reports run on unless the report says otherwise`)}">
                                <option value="accrual" ${s.report_basis !== 'cash' ? 'selected' : ''}>Accrual (income when billed)</option>
                                <option value="cash" ${s.report_basis === 'cash' ? 'selected' : ''}>Cash (income when paid)</option>
                            </select></div>
                    </div>
                </div>

                <!-- Desktop app only (shown by loadSignInPref); the session used
                     to outlive the app, so a relaunch reopened the company
                     without its password (explore 2.17.3, macbase1 S-j). -->
                <div class="settings-section" data-group="security" id="settings-sign-in" hidden role="group" aria-labelledby="settings-h-sign-in">
                    <div class="settings-h-row"><h3 id="settings-h-sign-in">Sign-in</h3>${hintList('Sign-in', [
                        { name: 'Ask for the password each time SlowBooks Pro starts', id: 'ask-password-on-start-help', text: `With Yes, the next start asks for the password even if you did not sign out.
                                On Server Edition, a restart of the server signs everyone out.` },
                    ])}</div>
                    <div class="form-grid">
                        <div class="form-group full-width">
                            <label for="ask-password-on-start">Ask for the password each time SlowBooks Pro starts</label>
                            <select id="ask-password-on-start" name="ask_password_on_start" aria-describedby="ask-password-on-start-help">
                                <option value="false" ${s.ask_password_on_start !== 'true' ? 'selected' : ''}>No: stay signed in until you sign out</option>
                                <option value="true" ${s.ask_password_on_start === 'true' ? 'selected' : ''}>Yes: quitting the app signs this company out</option>
                            </select>
                        </div>
                    </div>
                </div>

                <div class="settings-section" data-group="company" id="settings-closing-date" role="group" aria-labelledby="settings-h-closing-date">
                    <div class="settings-h-row"><h3 id="settings-h-closing-date">Closing Date</h3>${hintList('Closing Date', [
                        { id: 'closing-date-help', text: 'Prevent modifications to transactions before this date.' },
                        { name: 'Password (optional)', id: 'closing-date-password-help', text: [SettingsPage._secretNote(s.closing_date_password), `With a password set, a change dated on or before the closing date asks for it
                                and goes through when it is right. Without one, such changes are refused.`].filter(Boolean).join(' ') },
                    ])}</div>
                    <div class="form-grid">
                        <div class="form-group"><label for="closing-date">Closing Date</label>
                            <div style="display:flex; gap:6px; align-items:center;">
                                <input id="closing-date" name="closing_date" type="date" value="${escapeHtml(s.closing_date || '')}"
                                    aria-describedby="closing-date-state closing-date-help"
                                    oninput="SettingsPage.showClosingState()" onchange="SettingsPage.showClosingState()"
                                    oninvalid="SettingsPage.closingDateInvalid()">
                                <button type="button" class="btn btn-sm btn-secondary" id="closing-date-clear" data-admin data-write
                                    onclick="SettingsPage.clearClosingDate()" ${s.closing_date ? '' : 'disabled'}>Clear</button>
                            </div>
                            <!-- An empty date field shows today's date in grey on macOS, which
                                 read as "closed through today" (explore 2.17.3, macbase1 S-i). -->
                            <div id="closing-date-state" role="status" aria-live="polite"
                                style="font-size:11px; margin-top:4px;">${escapeHtml(SettingsPage._closingStateText(s.closing_date))}</div></div>
                        <div class="form-group"><label>Password (optional)</label>
                            <input name="closing_date_password" type="password" aria-describedby="closing-date-password-help" value="${escapeHtml(s.closing_date_password || '')}"
                                placeholder="Leave blank for no password" autocomplete="new-password"></div>
                    </div>
                </div>

                <div class="settings-section" data-group="preferences" id="settings-warnings" role="group" aria-labelledby="settings-h-warnings">
                    <div class="settings-h-row"><h3 id="settings-h-warnings">Transaction Warnings</h3>${hintList('Transaction Warnings', [
                        { id: 'settings-warnings-help', text: `Questions every form asks before it saves, for everyone using these books. Each one can be answered
                        &ldquo;save it anyway&rdquo;. Each is off until it is turned on here. Saved with Save Settings.` },
                        { name: 'Warn when a transaction is dated more than … days in the past or in the future', id: 'warn-date-help', text: `A typing slip in the year, or January&rsquo;s work landing in the old year. The closing date keeps its own lock.` },
                        { name: 'Warn about duplicate check numbers', id: 'warn-duplicate-check-help', text: `A check, register payment or bill payment whose number is already used on that bank account asks:
                        keep the number, or go back and edit it.` },
                        { name: 'Warn when editing a transaction', id: 'warn-edit-help', text: 'Saving changes to a transaction that was already saved asks first.' },
                    ])}</div>
                    ${SettingsPage._warnBox(s, 'warn_date_past', 'warn-date-past', 'Warn when a transaction is dated more than',
                        SettingsPage._warnDays(s, 'warn_date_past_days', 'warn-date-past-days', 'days in the past', 90))}
                    ${SettingsPage._warnBox(s, 'warn_date_future', 'warn-date-future', 'Warn when a transaction is dated more than',
                        SettingsPage._warnDays(s, 'warn_date_future_days', 'warn-date-future-days', 'days in the future', 30))}
                    ${SettingsPage._warnBox(s, 'warn_duplicate_check_number', 'warn-duplicate-check', 'Warn about duplicate check numbers', '')}
                    ${SettingsPage._warnBox(s, 'warn_edit_transaction', 'warn-edit', 'Warn when editing a transaction', '')}
                </div>

                <div class="settings-section" data-group="email" role="group" aria-labelledby="settings-h-email-smtp">
                    <div class="settings-h-row"><h3 id="settings-h-email-smtp">Email (SMTP)</h3>${hintList('Email (SMTP)', [
                        { id: 'settings-email-smtp-help', text: 'Configure SMTP for sending invoices by email.' },
                        SettingsPage._secretEntry(s.smtp_password, 'Password', 'smtp-password-help'),
                        { name: 'Always CC', id: 'smtp-always-cc-help', text: 'Copied on every email sent. Separate addresses with commas.' },
                        { name: 'Always BCC', id: 'smtp-always-bcc-help', text: "Blind-copied on every email sent; recipients don't see it." },
                    ])}</div>
                    <div class="form-grid">
                        <div class="form-group"><label>SMTP Host</label>
                            <input name="smtp_host" value="${escapeHtml(s.smtp_host || '')}" placeholder="smtp.gmail.com"></div>
                        <div class="form-group"><label>SMTP Port</label>
                            <input name="smtp_port" type="number" min="1" max="65535" step="1" value="${escapeHtml(s.smtp_port || '587')}"></div>
                        <div class="form-group"><label>Username</label>
                            <input name="smtp_user" value="${escapeHtml(s.smtp_user || '')}"></div>
                        <div class="form-group"><label>Password</label>
                            <input name="smtp_password" type="password"${SettingsPage._secretDescribed(s.smtp_password, 'smtp-password-help')} value="${escapeHtml(s.smtp_password || '')}"></div>
                        <div class="form-group"><label>From Email</label>
                            <input name="smtp_from_email" type="email" value="${escapeHtml(s.smtp_from_email || '')}"></div>
                        <div class="form-group"><label>From Name</label>
                            <input name="smtp_from_name" value="${escapeHtml(s.smtp_from_name || '')}"></div>
                        ${SettingsPage._useTlsFieldHtml(s)}
                        <div class="form-group"><label>Always CC</label>
                            <input name="smtp_always_cc" aria-describedby="smtp-always-cc-help" value="${escapeHtml(s.smtp_always_cc || '')}" placeholder="office@example.com"></div>
                        <div class="form-group"><label>Always BCC</label>
                            <input name="smtp_always_bcc" aria-describedby="smtp-always-bcc-help" value="${escapeHtml(s.smtp_always_bcc || '')}" placeholder="books@example.com"></div>
                    </div>
                    <button type="button" class="btn btn-sm btn-secondary" data-admin data-write onclick="SettingsPage.testEmail()" style="margin-top:8px;">
                        Send Test Email</button>
                </div>

                <div class="settings-section" data-group="email" id="settings-email-recent-sends" data-admin role="group" aria-labelledby="settings-h-email-recent-sends" hidden>
                    <div class="settings-h-row"><h3 id="settings-h-email-recent-sends">Recent sends</h3>${hintList('Recent sends', [
                        { id: 'settings-email-recent-sends-help', text: `Every invoice, statement, collection letter, portal invite and test email, newest first,
                        with whether it sent and — when it didn't — why.` },
                    ])}</div>
                    <label style="font-size:11px; display:inline-flex; align-items:center; gap:6px; margin-bottom:8px;">
                        <input type="checkbox" id="email-log-failed-only" onchange="SettingsPage.filterEmailLog(this.checked)">
                        Show failed only</label>
                    <div id="email-log-rows"><div style="font-size:11px; color:var(--text-muted);">Loading…</div></div>
                    <div style="margin-top:8px;">
                        <button type="button" class="btn btn-sm btn-secondary" id="email-log-more" onclick="SettingsPage.loadEmailLog(false)" hidden>Show more</button>
                    </div>
                </div>

                <div class="settings-section" data-group="payroll" role="group" aria-labelledby="settings-h-direct-deposit">
                    <div class="settings-h-row"><h3 id="settings-h-direct-deposit">Direct Deposit (ACH)</h3>${hintList('Direct Deposit (ACH)', [
                        { id: 'settings-direct-deposit-help', text: `What the bank prints on its ACH origination agreement. With these saved, a processed
                        pay run has a <strong>Direct deposit file</strong> button; upload the file to the bank.
                        Employees' own accounts are entered on the employee, under Direct Deposit.` },
                        { name: 'Company Account Number', id: 'ach-company-account-help', text: [SettingsPage._secretNote(s.ach_company_account), 'The account the net pay is paid from. Stored encrypted.'].filter(Boolean).join(' ') },
                    ])}</div>
                    <div class="form-grid">
                        <div class="form-group"><label>Bank Routing Number</label>
                            <input name="ach_bank_routing" inputmode="numeric" maxlength="9" value="${escapeHtml(s.ach_bank_routing || '')}" placeholder="9 digits"></div>
                        <div class="form-group"><label>Bank Name</label>
                            <input name="ach_bank_name" maxlength="23" value="${escapeHtml(s.ach_bank_name || '')}"></div>
                        <div class="form-group"><label>Company Account Number</label>
                            <input name="ach_company_account" type="password" inputmode="numeric" maxlength="17" aria-describedby="ach-company-account-help" value="${escapeHtml(s.ach_company_account || '')}" autocomplete="off"></div>
                        <div class="form-group"><label>Immediate Origin</label>
                            <input name="ach_immediate_origin" maxlength="10" value="${escapeHtml(s.ach_immediate_origin || '')}" placeholder="1 + EIN unless the bank says otherwise"></div>
                        <div class="form-group"><label>Company Identification</label>
                            <input name="ach_company_id" maxlength="10" value="${escapeHtml(s.ach_company_id || '')}" placeholder="1 + EIN unless the bank says otherwise"></div>
                    </div>
                </div>

                <div class="settings-section" data-group="payroll" id="settings-payroll-accounts" role="group" aria-labelledby="settings-h-payroll-accounts">
                    <div class="settings-h-row"><h3 id="settings-h-payroll-accounts">Payroll Accounts</h3>${hintList('Payroll Accounts', [
                        { id: 'settings-payroll-accounts-help', text: `Where a pay run posts. Each piece goes to its usual account unless another is chosen here.
                        Net pay leaves the bank account when the run is processed; choose 2300 Payroll Liabilities
                        (or another liability) to hold the wages until they are paid, then record the payment from
                        the bank against that account. A run already processed keeps the accounts it posted to,
                        and voiding it reverses those.` },
                        ...SettingsPage._payrollAccountEntries(payrollAccounts),
                    ])}</div>
                    ${SettingsPage._payrollAccountsHtml(payrollAccounts)}
                </div>

                <div class="settings-section" data-group="payroll" id="settings-leave-employer-share" role="group" aria-labelledby="settings-h-leave-employer-share">
                    <div class="settings-h-row"><h3 id="settings-h-leave-employer-share">Paid Leave Employer Share</h3>${hintList('Paid Leave Employer Share', [
                        { id: 'settings-leave-employer-share-help', text: `Washington PFML and Paid Leave Oregon charge the employer share only from a size of employer.
                        A pay run counts the active employees when it is figured; the agency counts over its own period.
                        The employee's share is withheld either way, and the stub says which applied and why.` },
                        { name: 'Washington PFML: Employer share applies', id: 'settings-wa-pfml-employer-share-help', text: 'ESD tells the employer each year whether it owes the employer share. Choose its answer here and every pay run follows it; the run warns when its own headcount says otherwise.' },
                        { name: 'Paid Leave Oregon: Employer share applies', id: 'settings-or-paid-leave-employer-share-help', text: 'OED tells the employer each year whether it owes the employer share. Choose its answer here and every pay run follows it; the run warns when its own headcount says otherwise.' },
                    ])}</div>
                    <div class="form-grid">
                        <div class="form-group full-width"><label for="settings-wa-pfml-employer-share">Washington PFML: Employer share applies (as the agency determined)</label>
                            <select name="payroll_wa_pfml_employer_share" id="settings-wa-pfml-employer-share" aria-describedby="settings-wa-pfml-employer-share-help">
                                <option value="headcount" ${!['yes', 'no'].includes(s.payroll_wa_pfml_employer_share) ? 'selected' : ''}>Follow the headcount at each pay run (50 or more employees working in Washington)</option>
                                <option value="yes" ${s.payroll_wa_pfml_employer_share === 'yes' ? 'selected' : ''}>Yes: ESD determined the employer share applies</option>
                                <option value="no" ${s.payroll_wa_pfml_employer_share === 'no' ? 'selected' : ''}>No: ESD determined it does not apply</option>
                            </select>
                        </div>
                        <div class="form-group full-width"><label for="settings-or-paid-leave-employer-share">Paid Leave Oregon: Employer share applies (as the agency determined)</label>
                            <select name="payroll_or_paid_leave_employer_share" id="settings-or-paid-leave-employer-share" aria-describedby="settings-or-paid-leave-employer-share-help">
                                <option value="headcount" ${!['yes', 'no'].includes(s.payroll_or_paid_leave_employer_share) ? 'selected' : ''}>Follow the headcount at each pay run (25 or more employees)</option>
                                <option value="yes" ${s.payroll_or_paid_leave_employer_share === 'yes' ? 'selected' : ''}>Yes: OED determined the employer share applies</option>
                                <option value="no" ${s.payroll_or_paid_leave_employer_share === 'no' ? 'selected' : ''}>No: OED determined it does not apply</option>
                            </select>
                        </div>
                    </div>
                </div>

                <div class="settings-section" data-group="connections" id="settings-deposits" role="group" aria-labelledby="settings-h-deposits">
                    <div class="settings-h-row"><h3 id="settings-h-deposits">Deposits</h3>${hintList('Deposits', [
                        { id: 'settings-deposits-help', text: 'Where Make Deposits puts the money. A deposit or a payment already saved keeps its own account.' },
                        { name: 'Default deposit account', id: 'settings-default-deposit-account-help', text: 'Make Deposits opens on this account. On Receive Payment and Enter Sales Receipt it is the first bank account under Undeposited Funds in Deposit To, which still starts on Undeposited Funds. When accounts are ticked below, it must be one of them.' },
                        { name: 'Deposit into these accounts', id: 'settings-deposit-accounts-help', text: 'Make Deposits, and Deposit To on Receive Payment and Enter Sales Receipt, offer only the accounts ticked here; none ticked offers every bank account. An account left off is still in the chart, its register and the reports, and a deposit already made into it keeps it.' },
                    ])}</div>
                    ${SettingsPage._depositAccountsHtml(s, accounts)}
                </div>

                <div class="settings-section" data-group="connections" role="group" aria-labelledby="settings-h-online-payments">
                    <div class="settings-h-row"><h3 id="settings-h-online-payments">Online Payments</h3>${hintList('Online Payments', [
                        { id: 'settings-online-payments-help', text: `Accept online payments on emailed invoice links. Enable any combination of
                        providers — the customer pay page shows one button per enabled provider.` },
                        SettingsPage._secretEntry(s.stripe_secret_key, 'Stripe Secret Key', 'stripe-secret-key-help'),
                        SettingsPage._secretEntry(s.stripe_webhook_secret, 'Stripe Webhook Secret', 'stripe-webhook-secret-help'),
                        SettingsPage._secretEntry(s.paypal_client_secret, 'PayPal Client Secret', 'paypal-client-secret-help'),
                        SettingsPage._secretEntry(s.square_access_token, 'Square Access Token', 'square-access-token-help'),
                        SettingsPage._secretEntry(s.square_webhook_signature_key, 'Square Webhook Signature Key', 'square-webhook-signature-key-help'),
                        { name: 'Post to a clearing account', id: 'settings-clearing-help', text: "Payments wait in the clearing account until the payout reaches your bank; the processor's fee is booked as it's charged." },
                    ])}</div>
                    <h4 id="settings-h-stripe" style="margin:8px 0 4px; font-size:12px;">Stripe</h4>
                    <div class="form-grid" role="group" aria-labelledby="settings-h-stripe">
                        <div class="form-group"><label>Stripe Payments</label>
                            <select name="stripe_enabled">
                                <option value="false" ${s.stripe_enabled !== 'true' ? 'selected' : ''}>Disabled</option>
                                <option value="true" ${s.stripe_enabled === 'true' ? 'selected' : ''}>Enabled</option>
                            </select></div>
                        <div class="form-group"><label>Publishable Key</label>
                            <input name="stripe_publishable_key" value="${escapeHtml(s.stripe_publishable_key || '')}" placeholder="pk_..."></div>
                        <div class="form-group"><label>Secret Key</label>
                            <input name="stripe_secret_key" type="password"${SettingsPage._secretDescribed(s.stripe_secret_key, 'stripe-secret-key-help')} value="${escapeHtml(s.stripe_secret_key || '')}" placeholder="sk_..."></div>
                        <div class="form-group"><label>Webhook Secret</label>
                            <input name="stripe_webhook_secret" type="password"${SettingsPage._secretDescribed(s.stripe_webhook_secret, 'stripe-webhook-secret-help')} value="${escapeHtml(s.stripe_webhook_secret || '')}" placeholder="whsec_..."></div>
                    </div>
                    ${SettingsPage._clearingHtml('stripe', 'Stripe', s, accounts)}
                    <h4 id="settings-h-paypal" style="margin:12px 0 4px; font-size:12px;">PayPal</h4>
                    <div class="form-grid" role="group" aria-labelledby="settings-h-paypal">
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
                            <input name="paypal_client_secret" type="password"${SettingsPage._secretDescribed(s.paypal_client_secret, 'paypal-client-secret-help')} value="${escapeHtml(s.paypal_client_secret || '')}"></div>
                        <div class="form-group"><label>Webhook ID</label>
                            <input name="paypal_webhook_id" value="${escapeHtml(s.paypal_webhook_id || '')}"
                                placeholder="From the PayPal developer dashboard"></div>
                    </div>
                    ${SettingsPage._clearingHtml('paypal', 'PayPal', s, accounts)}
                    <h4 id="settings-h-square" style="margin:12px 0 4px; font-size:12px;">Square</h4>
                    <div class="form-grid" role="group" aria-labelledby="settings-h-square">
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
                            <input name="square_access_token" type="password"${SettingsPage._secretDescribed(s.square_access_token, 'square-access-token-help')} value="${escapeHtml(s.square_access_token || '')}"></div>
                        <div class="form-group"><label>Location ID</label>
                            <input name="square_location_id" value="${escapeHtml(s.square_location_id || '')}"></div>
                        <div class="form-group"><label>Webhook Signature Key</label>
                            <input name="square_webhook_signature_key" type="password"${SettingsPage._secretDescribed(s.square_webhook_signature_key, 'square-webhook-signature-key-help')} value="${escapeHtml(s.square_webhook_signature_key || '')}"></div>
                        <div class="form-group"><label>Webhook Notification URL</label>
                            <input name="square_notification_url" value="${escapeHtml(s.square_notification_url || '')}"
                                placeholder="Exact URL registered in the Square dashboard"></div>
                    </div>
                    ${SettingsPage._clearingHtml('square', 'Square', s, accounts)}
                </div>

                <div class="settings-section" data-group="connections" id="settings-ai" role="group" aria-labelledby="settings-h-ai-insights">
                    <div class="settings-h-row"><h3 id="settings-h-ai-insights">AI Insights</h3>${hintList('AI Insights', [
                        { id: 'settings-ai-help', text: `Bring-your-own-key access to Anthropic Claude, xAI Grok, Groq, Cloudflare Workers AI, OpenAI, or Google Gemini.
                        Used by the Analytics dashboard to generate observations, risks, and recommendations.
                        API keys are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256).` },
                        // the chosen provider's line, filled in and kept up by the AI form
                        { name: 'Provider', id: 'ai-settings-hint', hidden: true, text: '' },
                        { name: 'Cloudflare Worker Gateway', id: 'ai-worker-help', hidden: true, text: `Deploy <code>cloudflare/worker.js</code> in your own
                    Cloudflare account — the real AI credentials live inside
                    Cloudflare as a Worker secret, not in SlowBooks' database.
                    SlowBooks only holds the shared Bearer token. See
                    <code>cloudflare/README.md</code> for the 5-minute setup.` },
                        { name: 'Custom OpenAI-Compatible Endpoint', id: 'ai-endpoint-help', hidden: true, text: `Point SlowBooks at any OpenAI-compatible chat API on the
                    public internet — another vendor's <code>/v1</code> base URL,
                    or a gateway you host. <code>/chat/completions</code> is
                    appended automatically if you don't include it. A model on
                    this machine or your LAN cannot be reached this way: the
                    address check below refuses it on purpose.` },
                    ])}</div>
                    <div id="ai-config-container" class="ai-settings-form">
                        <div style="font-size:11px; color:var(--text-muted);">Loading…</div>
                    </div>
                </div>

                <!-- SlowBooks Switchboard (#829) and Connect to AI assistant (#1087): the
                     line to the SlowBooks relay is held by SlowBooks Pro itself
                     (app/services/switchboard_link.py); loadSwitchboard draws its
                     state and an administrator's buttons into #switchboard-relay.
                     The local program (SlowBooks Switchboard, for Claude Desktop and
                     Claude Code with no relay) is behind the section's note, with
                     its download and guide: the section is about one button. -->
                <div class="settings-section" data-group="connections" id="settings-switchboard" role="group" aria-labelledby="settings-h-claude-agents">
                    <div class="settings-h-row"><h3 id="settings-h-claude-agents">AI assistants</h3>${hintList('AI assistants', [
                        { id: 'settings-claude-relay-help', text: `Connect to AI assistant lets an AI assistant, such as Claude on the web, the desktop or a phone, or Grok,
                        work this company's books through the SlowBooks relay, with nothing else to install. SlowBooks Pro
                        makes a <strong>bookkeeper API token</strong> for the connection and dials out to the relay itself:
                        no port is opened, and there is no account, email or password. Add the address below as a connector
                        in Claude or Grok, and type a sign-in code from here when it asks, so only someone at the books can
                        approve a connection. The assistant can do only what the token's role allows, and every change it
                        makes is in the audit log as <code>token:&lt;label&gt;</code>. Before it creates a ${T('customer')}, vendor, item,
                        employee, account or document with little on it, the assistant is told to ask you for what is missing
                        (it may not make anything up), and after a create it is told what was left blank. On the desktop the
                        connection is up only while SlowBooks Pro is open. Disconnect ends it, revokes each company's token and has the
                        relay delete its record of these books. The <strong>relay name</strong> shown while connected is how
                        that record is known: if Disconnect cannot reach the relay, SlowBooks Pro will keep trying to remove
                        the record itself, and the name stays here in case you ever need to ask support@slowbookspro.com about it.
                        The connection is made in one company. An administrator of each other company adds it there, and the
                        assistant names which company's books it works in on every call. On a server, adding a company,
                        showing a sign-in code and Disconnect take someone who is an administrator of every company on the
                        connection; an administrator of this company alone sees only this company and can take it off.
                        A company's access ends when the administrator who added it is no longer one there. The connection
                        holds a key to each company's books on this computer, so treat its sign-in codes like a master key.` },
                        { id: 'settings-switchboard-help', text: `<strong>Without the relay: SlowBooks Switchboard.</strong> SlowBooks Switchboard lets Claude Desktop, Claude Code and other agents that speak the
                        Model Context Protocol work this company's books. It is a free program of its own,
                        <code>sbplive</code>, that runs on this computer and reaches SlowBooks Pro through its API
                        with a <strong>bookkeeper API token</strong> you make under Users and security, API Tokens
                        (read-only for an agent that only reports). It does only what that token's role allows,
                        and every change it makes is in the audit log as <code>token:&lt;label&gt;</code>.
                        Run <code>sbplive setup</code>: it asks for the token and connects Claude Desktop and Claude Code.
                        <a href="https://github.com/VonHoltenCodes/SlowBooks-Switchboard/releases" target="_blank" rel="noopener">Download SlowBooks Switchboard</a>
                        &middot;
                        <a href="https://github.com/VonHoltenCodes/SlowBooks-Switchboard/blob/main/docs/GUIDE.md" target="_blank" rel="noopener">The guide</a>` },
                        { id: 'settings-switchboard-privacy', text: `The token stays on this computer, and Switchboard sends nothing anywhere but this
                        SlowBooks Pro. For Claude on the web it has a self-hosted door, <code>sbplive serve-http</code>,
                        behind your own Cloudflare Tunnel; nothing of the books can be reached from outside this
                        computer unless you set that door up. What Claude reads from the books goes to Claude,
                        as with any agent.
                        <a href="https://github.com/VonHoltenCodes/SlowBooks-Switchboard/blob/main/docs/REMOTE.md" target="_blank" rel="noopener">Claude on the web: the self-hosted door</a>` },
                    ])}</div>
                    <div id="switchboard-relay" aria-live="polite" style="font-size:12px;">Checking…</div>
                    <p id="switchboard-relay-caution" style="font-size:11px; margin:8px 0;">The relay is free and comes with no guarantee: it may be slow, change, or stop, and it may not stay free without donations. Use it at your own risk.
                        <a href="https://www.slowbookspro.com/terms/" target="_blank" rel="noopener">Terms</a>
                        &middot;
                        <a href="https://www.slowbookspro.com/privacy/" target="_blank" rel="noopener">Privacy</a></p>
                    <p id="switchboard-relay-ask" style="font-size:11px; margin:8px 0;">An assistant asks before it creates a record with little on it, and says what it left blank.</p>
                </div>

                <div class="settings-section" data-group="connections" id="settings-ocr" role="group" aria-labelledby="settings-h-receipt-scanning">
                    <div class="settings-h-row"><h3 id="settings-h-receipt-scanning">Receipt Scanning</h3>${hintList('Receipt Scanning', [
                        { id: 'settings-ocr-help', text: `${Terms.text('Local OCR for the Scan Receipt button on the Enter Sales Receipt, Enter Bill and Enter Expense forms.')}
                        Everything runs on this computer — no cloud, no data leaves the machine.
                        The desktop app ships with a built-in engine (Windows OCR / Apple Vision), so scanning
                        works out of the box; Tesseract is an optional extra engine you can install yourself.` },
                        // said only when no engine is found (loadOcrStatus)
                        { id: 'ocr-status-help', hidden: true, text: 'macOS and Windows normally use the engine built into the OS; installing Tesseract enables scanning anywhere.' },
                        { name: 'OCR engine', id: 'ocr-engine-pref-help', text: `Optional: Tesseract can sharpen box re-reads on faded receipts. Install it with
                        <code>sudo apt-get install tesseract-ocr</code> (Ubuntu),
                        <code>brew install tesseract</code> (macOS), or the
                        UB&nbsp;Mannheim build from
                        <code>github.com/UB-Mannheim/tesseract</code> (Windows), then choose it here.` },
                    ])}</div>
                    <div id="ocr-status" style="font-size:12px;">Checking…</div>
                    <div style="margin-top:8px; display:flex; align-items:center; gap:8px;">
                        <label for="ocr-engine-pref" style="font-size:11px;">OCR engine:</label>
                        <select id="ocr-engine-pref" data-admin aria-describedby="ocr-engine-pref-help" style="font-size:11px;"
                            onchange="SettingsPage.saveOcrEngine(this.value)">
                            <option value="auto">Automatic (recommended) — built-in engine first</option>
                            <option value="tesseract">Prefer Tesseract (if installed)</option>
                        </select>
                    </div>
                </div>

                <div class="settings-section" data-group="preferences" id="settings-sounds" role="group" aria-labelledby="settings-h-sounds">
                    <div class="settings-h-row"><h3 id="settings-h-sounds">Sounds</h3>${hintList('Sounds', [
                        { id: 'settings-sounds-help', text: `A personal preference, kept per sign-in: it follows you to another computer, and everyone sets their own.` },
                        { name: 'Play a sound when a transaction posts', id: 'post-sound-pref-help', text: 'A short chime when a transaction posts — an invoice, bill, payment, deposit, check, journal entry, transfer or pay run. Deletes, voids and saved settings stay silent. Off by default (#476).' },
                    ])}</div>
                    <label style="font-weight:normal; font-size:11px;">
                        <input type="checkbox" id="post-sound-pref" aria-describedby="post-sound-pref-help" style="width:auto; vertical-align:middle; margin-right:6px;"
                            onchange="SettingsPage.savePostSound(this.checked)">
                        Play a sound when a transaction posts
                    </label>
                </div>

                <div class="settings-section" data-group="sales" role="group" aria-labelledby="settings-h-late-fees">
                    <div class="settings-h-row"><h3 id="settings-h-late-fees">Late Fees</h3>${hintList('Late Fees', [
                        { id: 'settings-late-fees-help', text: `Automatically apply late fees to overdue invoices. Use "Apply Late Fees" on the AR Aging report.` },
                    ])}</div>
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

                <div class="settings-section" data-group="sales" id="settings-collections" role="group" aria-labelledby="settings-h-collections">
                    <div class="settings-h-row"><h3 id="settings-h-collections">Collections</h3>${hintList('Collections', [
                        { id: 'settings-collections-help', text: `Reminders go to a customer's email when an ${T('invoice')} is coming due or is overdue. Run them from the <a href="#/collections">Collections</a> page, or let SlowBooks Pro send them for you.` },
                        { name: 'Send reminders automatically', id: 'invoice-reminders-auto-help', text: 'Once a day, when SlowBooks Pro is opened. Needs email set up.' },
                        { name: `Remind about a sub-${T('customer').toLowerCase()} billed with its parent`, id: 'invoice-reminders-billed-sub-to-help', text: 'A tenant billed with its property, say. Whoever is written to must have an email and not be opted out.' },
                        { name: 'Reminder rules', id: 'reminder-rules-help', text: `Days from due: negative is before the due date, 0 is the day, positive is after. Each ${T('invoice')} gets one email per run: the latest rule it has reached. On or before the due date, "0 days overdue" is written as due today (or tomorrow, or in so many days).
                        Placeholders: {{ customer_name }} {{ invoice_number }} {{ invoice_label }} {{ balance_due }} {{ due_date }} {{ days_overdue }} {{ due_status }} {{ company_name }}` },
                    ])}</div>
                    <div class="form-grid">
                        <div class="form-group"><label for="invoice-reminders-auto">Send reminders automatically</label>
                            <select name="invoice_reminders_auto" id="invoice-reminders-auto" aria-describedby="invoice-reminders-auto-help invoice-reminders-last-run" data-no-search onchange="SettingsPage.previewAutoReminders(this)">
                                <option value="false" ${s.invoice_reminders_auto !== 'true' ? 'selected' : ''}>Off</option>
                                <option value="true" ${s.invoice_reminders_auto === 'true' ? 'selected' : ''}>On</option>
                            </select>
                            <div id="invoice-reminders-last-run" style="font-size:10px; color:var(--text-muted); margin-top:4px;">${s.invoice_reminders_last_run ? `Last ran ${escapeHtml(formatDate(s.invoice_reminders_last_run))}.` : ''}</div></div>
                        <div class="form-group"><label for="invoice-reminders-billed-sub-to">Remind about a sub-${T('customer').toLowerCase()} billed with its parent</label>
                            <select name="invoice_reminders_billed_sub_to" id="invoice-reminders-billed-sub-to" aria-describedby="invoice-reminders-billed-sub-to-help" data-no-search>
                                <option value="property" ${(s.invoice_reminders_billed_sub_to || 'property') === 'property' ? 'selected' : ''}>The parent (whose account it is billed to)</option>
                                <option value="tenant" ${s.invoice_reminders_billed_sub_to === 'tenant' ? 'selected' : ''}>The sub-${T('customer').toLowerCase()} itself</option>
                                <option value="both" ${s.invoice_reminders_billed_sub_to === 'both' ? 'selected' : ''}>Both</option>
                            </select></div>
                    </div>
                    <h4 id="settings-h-reminder-rules" style="margin:12px 0 4px; font-size:12px;">Reminder rules</h4>
                    <div id="reminder-rules-list" role="group" aria-labelledby="settings-h-reminder-rules" aria-describedby="reminder-rules-help"></div>
                </div>

                <div class="settings-section" data-group="email" role="group" aria-labelledby="settings-h-email-templates">
                    <div class="settings-h-row"><h3 id="settings-h-email-templates">Email Templates</h3>${hintList('Email Templates', [
                        { id: 'settings-email-templates-help', text: `Customize email templates for invoices, estimates, payment receipts, and collection notices.
                        Templates use Jinja2 syntax. Available variables: {{ invoice }}, {{ customer_name }}, {{ company }}, {{ pay_url }}; the estimate email (estimate_email) gets {{ estimate }} in place of {{ invoice }}. The donation acknowledgment letter (nonprofit) also gets {{ donor }}, {{ donor_name }}, {{ gift }} and {{ irs.text }}.` },
                    ])}</div>
                    <div style="display:flex; gap:8px; margin-bottom:12px;" data-write>
                        <button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.seedTemplates()">Seed Default Templates</button>
                    </div>
                    <div id="email-template-list"></div>
                </div>

                <div class="settings-section" data-group="sales" role="group" aria-labelledby="settings-h-payment-terms">
                    <div class="settings-h-row"><h3 id="settings-h-payment-terms">Payment Terms</h3>${hintList('Payment Terms', [
                        { id: 'settings-payment-terms-help', text: `The terms every ${T('invoice')}, bill, ${T('customer')} and vendor form offers, each with its rule. A standard term is due a number of days after
                        the date; a date-driven term is due on a day of the month; an end-of-month term is due a number of days after the last day of the month the document is dated in ("Net 75 EOM"). Any can offer an early-payment discount, which Receive Payments and Pay Bills
                        suggest until its date passes. Each ${T('invoice')} or bill keeps the term it was saved under; a term on one keeps its rule, so make it inactive and add a new one to change it.
                        A term added later never reaches what was saved before it: saving one again with exactly the same terms leaves it as it was, and picking any other terms (even the same words in other case) ties it to the term they name. A date-driven term is due on its next day of the month, or the one a month later when the document is dated fewer than the given number of days before that day.` },
                    ])}</div>
                    <div style="display:flex; gap:8px; margin-bottom:12px;" data-admin data-write>
                        <button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.editPaymentTerm()">+ Add Term</button>
                    </div>
                    <div id="payment-term-list"></div>
                </div>

                <div class="settings-section" data-group="sales" role="group" aria-labelledby="settings-h-invoice-messages">
                    <div class="settings-h-row"><h3 id="settings-h-invoice-messages">${T('Invoice')} Messages</h3>${hintList(`${T('Invoice')} Messages`, [
                        { id: 'settings-invoice-messages-help', text: `Saved notes to choose from on the ${T('invoice')} form: how to pay, a thank-you, a season's greeting. The form offers them beside Notes and copies the chosen text in,
                        where it can still be edited. The default one fills Notes on a new ${T('invoice')}; with no default, the Default ${T('Invoice')} Notes above do.` },
                    ])}</div>
                    <div style="display:flex; gap:8px; margin-bottom:12px;" data-admin data-write>
                        <button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.editInvoiceMessage()">+ Add Message</button>
                    </div>
                    <div id="invoice-message-list"></div>
                </div>

                ${Terms.isNonprofit() ? `
                <div class="settings-section" data-group="sales" role="group" aria-labelledby="settings-h-giving-statement">
                    <div class="settings-h-row"><h3 id="settings-h-giving-statement">Giving Statement</h3>${hintList('Giving Statement', [
                        { id: 'settings-giving-statement-help', text: `The ministry's own words on the year-end giving statement. The Opening messages are saved
                        the same way as ${T('Invoice')} Messages above; the default one prints above the gift table
                        (a thank-you from the pastor or the board). The Footer text replaces the fixed footer (the
                        ministry's address, website, EIN, a verse). Both are plain text with line breaks, printed as
                        typed. The IRS acknowledgment sentence is always printed and is not editable here.
                        A giving-statement message is at most 1500 characters and the footer at most 500 characters and 6 lines (a line longer than 90 characters wraps and counts as two). Save first, then preview.` },
                    ])}</div>
                    <div style="display:flex; gap:8px; margin-bottom:12px;" data-admin data-write>
                        <button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.editInvoiceMessage(null, 'giving_statement')">+ Add Opening Message</button>
                    </div>
                    <div id="giving-message-list"></div>
                    <div class="form-grid" style="margin-top:12px;">
                        <div class="form-group full-width"><label>Footer text</label>
                            <textarea name="giving_statement_footer" rows="6" maxlength="500"
                                placeholder="Harbor Light Ministries · 123 Main St · EIN 12-3456789 · harborlight.org">${escapeHtml(s.giving_statement_footer || '')}</textarea></div>
                    </div>
                    <div style="display:flex; gap:8px;">
                        <button type="button" class="btn btn-sm btn-secondary" data-readonly-ok
                            onclick="SettingsPage.previewGivingStatement()">Preview one donor's statement</button>
                    </div>
                </div>` : ''}

                <div class="settings-section" data-group="preferences" role="group" aria-labelledby="settings-h-menu">
                    <div class="settings-h-row"><h3 id="settings-h-menu">Sidebar</h3>${hintList('Sidebar', [
                        { id: 'settings-menu-help', text: `A ticked item shows in the menu; untick the ones this company doesn't use (payroll done
                        elsewhere, no inventory, …) to hide them. A hidden page still opens by its address, and
                        this list always has every item, so a hidden one can be ticked to show it again.` },
                    ])}</div>
                    <div id="hidden-menu-choices" role="group" aria-labelledby="settings-h-menu" aria-describedby="settings-menu-help" style="columns:2; font-size:12px; margin-bottom:12px;">
                        ${SettingsPage._hiddenMenuChoices(s.hidden_menu)}
                    </div>
                </div>

                <div class="settings-section" data-group="lists" role="group" aria-labelledby="settings-h-classes">
                    <div class="settings-h-row"><h3 id="settings-h-classes">${T('Classes')}</h3>${hintList(T('Classes'), [
                        { id: 'settings-classes-help', text: `Track income and expenses by department, location, or line of
                        business. ${T('Classes')} appear on entry forms and the ${T('P&L by Class')} report.` },
                        { name: `Warn when a transaction is saved without a ${T('class').toLowerCase()}`, id: 'class-warn-blank-help', text: `On, every entry form starts with no ${T('class').toLowerCase()} chosen and asks before saving a transaction that has none on its header or its lines — it would be reported under Uncategorized. Off, the forms start on Uncategorized and ask nothing. Saved with Save Settings.` },
                    ])}</div>
                    <div style="margin-bottom:12px;">
                        <label for="class-warn-blank" style="font-weight:normal;">
                            <input type="checkbox" id="class-warn-blank" name="class_warn_blank" value="true" aria-describedby="class-warn-blank-help" style="width:auto; vertical-align:middle; margin-right:6px;" ${s.class_warn_blank === 'true' ? 'checked' : ''}>
                            Warn when a transaction is saved without a ${T('class').toLowerCase()}
                        </label>
                    </div>
                    <div style="display:flex; gap:8px; margin-bottom:12px;" data-write>
                        <input type="text" id="new-class-name" placeholder="New ${T('class')} name" style="width:220px;">
                        <button type="button" class="btn btn-primary" onclick="SettingsPage.addClass()">Add ${T('Class')}</button>
                    </div>
                    <div id="classes-list"></div>
                </div>

                <div class="settings-section" data-group="lists" role="group" aria-labelledby="settings-h-cost-types">
                    <div class="settings-h-row"><h3 id="settings-h-cost-types">Cost Types</h3>${hintList('Cost Types', [
                        { id: 'settings-cost-types-help', text: `How job costs roll up: labor, material, subcontract, equipment, other — add your own
                        (permits, bonding, warranty…). A labor-type carries a burden % (employer taxes, benefits,
                        insurance) posted as its own line. The cost account is where a job-cost line lands when its
                        code has none; the offset accounts are the credit side of job cost entries (payroll clearing,
                        applied equipment, applied overhead).` },
                    ])}</div>
                    <div style="display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap;" data-write>
                        <input type="text" id="new-ct-code" placeholder="Code (e.g. permits)" style="width:150px;">
                        <input type="text" id="new-ct-name" placeholder="Name" style="width:200px;">
                        <label style="font-weight:normal;font-size:11px;"><input type="checkbox" id="new-ct-labor"> labor-type (burden applies)</label>
                        <button type="button" class="btn btn-primary" onclick="SettingsPage.addCostType()">Add Cost Type</button>
                        <button type="button" class="btn btn-secondary" onclick="SettingsPage.setupOffsets()" title="Creates Applied Labor Cost, Applied Labor Burden, Applied Equipment Cost and Applied Overhead if missing, points each cost type at its matching COGS account and fills in any blank accounts">Create default offset accounts</button>
                    </div>
                    <div id="cost-types-list"></div>
                </div>

                <div class="settings-section" data-group="lists" role="group" aria-labelledby="settings-h-cost-codes">
                    <div class="settings-h-row"><h3 id="settings-h-cost-codes">Cost Codes</h3>${hintList('Cost Codes', [
                        { id: 'settings-cost-codes-help', text: `${Terms.text('The job-costing chart: which part of a job a cost belongs to')}
                        ("03 Concrete", "26 Electrical"), independent of the account it posts
                        to. Picked per line on bills, expenses, purchase orders and journal
                        entries; ${Terms.text('the Job detail rolls costs up by code and cost type.')}` },
                    ])}</div>
                    <div style="display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap;" data-write>
                        <input type="text" id="new-cc-code" placeholder="Code" style="width:90px;">
                        <input type="text" id="new-cc-name" placeholder="Name" style="width:220px;">
                        <select id="new-cc-type" aria-label="Cost code type">
                            <option value="labor">Labor</option><option value="material">Material</option>
                            <option value="subcontract">Subcontract</option><option value="equipment">Equipment</option>
                            <option value="other" selected>Other</option>
                        </select>
                        <select id="new-cc-parent" aria-label="Parent cost code"><option value="">(top level)</option></select>
                        <button type="button" class="btn btn-primary" onclick="SettingsPage.addCostCode()">Add Cost Code</button>
                        <button type="button" class="btn btn-secondary" onclick="SettingsPage.loadStandardCostCodes()" title="The standard division titles and a set of common sections, parented by code">Load standard divisions and common sections</button>
                        <button type="button" class="btn btn-secondary" onclick="SettingsPage.showCostCodeImport()">Import CSV</button>
                    </div>
                    <div id="cost-codes-list"></div>
                </div>

                ${SettingsPage.tradePackagesSectionHtml()}

                <div class="settings-section" data-group="lists" role="group" aria-labelledby="settings-h-equipment">
                    <div class="settings-h-row"><h3 id="settings-h-equipment">Equipment</h3>${hintList('Equipment', [
                        { id: 'settings-equipment-help', text: `${Terms.text('Owned machines charged to jobs by the hour from a Job Cost Entry.')} The recovery account is
                        the credit side (defaults to the equipment cost type's offset).` },
                    ])}</div>
                    <div style="display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap;" data-write>
                        <input type="text" id="new-eq-code" placeholder="Code" style="width:90px;">
                        <input type="text" id="new-eq-name" placeholder="Name (Skid steer, F-250…)" style="width:220px;">
                        <input type="number" step="0.01" id="new-eq-rate" placeholder="$/hr" style="width:90px;">
                        <button type="button" class="btn btn-primary" onclick="SettingsPage.addEquipment()">Add Equipment</button>
                    </div>
                    <div id="equipment-list"></div>
                </div>

                <div class="settings-section" data-group="payroll" role="group" aria-labelledby="settings-h-check-printing">
                    <div class="settings-h-row"><h3 id="settings-h-check-printing">Check Printing</h3>${hintList('Check Printing', [
                        { name: 'Check Stock', id: 'settings-check-stock-help', text: 'Pre-printed stock already carries the "Pay to the order of", memo and signature lines. Choose it to print only the values — payee, amount, amount in words, date and memo — in their places, with no labels, lines or borders. The two stubs are unchanged either way.' },
                        { name: 'Check Position', id: 'settings-check-position-help', text: 'Where the check sits on the sheet. Check at the bottom prints two stubs and then the check. Check on top prints the check first and two vouchers below it, the layout of standard top-check voucher stock, so that stock can be used. The stock and the nudges apply to the check either way.' },
                        { name: 'Check memo', id: 'settings-check-memo-help', text: 'What Pay Bills puts on the memo line of the check: the number the bill was entered under (the vendor\'s own numbering), the bill\'s whole memo, or nothing. A check paying several bills lists their numbers (196132, 196140). A bill numbered by SlowBooks because its number was left blank has no reference, so its memo is used instead. The memo can still be changed on the payment before the check prints, and a memo typed there is kept.' },
                        { name: 'Field layout', id: 'settings-check-fields-help', text: 'Move, size or bold one field of the check without moving the others, for stock whose boxes do not all sit where the check prints them. Left/right and up/down are inches from where the field prints today (-1 to 1; a positive offset moves it right and down), on top of the nudges. Size is in points (6 to 18); empty prints today\'s size. A field keeps its baseline as its size changes. It applies to the check at the bottom and on top and to every stock.' },
                        { name: 'Leave the dollar sign off the amount', id: 'settings-check-amount-no-dollar-sign-help', text: 'For check stock that prints its own dollar sign beside the amount box: the amount prints as 1,234.56 instead of $1,234.56. The amount in words and the stubs are unchanged.' },
                        { name: 'Fill the amount with asterisks', id: 'settings-check-amount-asterisk-fill-help', text: 'Prints two asterisks in front of the amount, as QuickBooks Desktop does, so nothing can be written in front of it: $**1,234.56, or **1,234.56 with the dollar sign left off. The amount in words and the stubs are unchanged.' },
                        { name: 'Print on blank check stock', id: 'settings-check-blank-stock-help', text: 'Prints everything a pre-printed check carries: the company logo, name and address, the bank\'s name and city, the check number and the MICR line with the routing, account and check numbers, taken from the bank account\'s details below. The labels, lines and borders print as on plain stock, and every field stays where it prints today. A bank reads the MICR line only when it is printed with MICR toner.' },
                        { name: 'Encode the amount in the MICR line', id: 'settings-check-micr-amount-help', text: 'The amount field at the right end of the MICR line is normally encoded by the bank where the check is deposited. Turn this on to print it yourself, so the amount cannot be changed without it showing.' },
                        { name: 'Bank account details for blank stock', id: 'settings-check-bank-details-help', text: 'Each bank account you write checks on keeps its own details. They are saved with their own button, apart from the settings above.' },
                        { name: 'Print a test check', id: 'settings-check-test-help', text: 'Prints a sample check with placeholder values on plain paper. Hold it against your check stock to see how far to nudge the values left/right (horizontal) or up/down (vertical); a positive nudge moves them right and down. The test print uses the stock, check position, nudges, field layout, the amount\'s dollar sign and asterisks and the blank-stock choices as set here, saved or not; on blank stock it prints sample bank details, never a saved account\'s.' },
                        { name: 'Deposit slip stock', id: 'settings-deposit-slip-stock-help', text: 'Pre-printed slips already carry the bank\'s labels, boxes and MICR line. Choose them to have Print on a deposit lay only the figures (the date, the account, the cash, each check\'s number and amount, the checks subtotal and the total) where the layout below puts them, with no labels, no MICR line and no routing or account numbers. Checks past the slip\'s lines continue on another page at the same lines, for the back of the slip or a second one. Plain paper prints the deposit slip as it always has.' },
                        { name: 'Deposit slip layout', id: 'settings-deposit-slip-fields-help', text: 'Where each figure prints on your bank\'s slip, in inches from the slip\'s top-left corner as it feeds into the printer (0 to 11). Left is where a date, the account or a check number starts and where an amount ends, so the cents line up; top is the top of the figure. Amounts print without a dollar sign. Measure your slip with a ruler, then print a test slip and hold it against one. The check number and amount rows place the first check line; the rest follow at the spacing below. Clear Print to leave a figure off.' },
                        { name: 'Print a test slip', id: 'settings-deposit-slip-test-help', text: 'Prints a sample deposit (two checks and some cash, made-up figures) laid out for pre-printed slips, using the layout and nudges as set here, saved or not. Print it on plain paper and hold it against a slip, or print it on a spare slip.' },
                    ])}</div>
                    <div class="form-grid">
                        <div class="form-group full-width"><label>Check Stock</label>
                            <select name="check_stock" aria-describedby="settings-check-stock-help">
                                <option value="plain" ${s.check_stock !== 'preprinted' ? 'selected' : ''}>Plain / voucher stock</option>
                                <option value="preprinted" ${s.check_stock === 'preprinted' ? 'selected' : ''}>Pre-printed stock: values only</option>
                            </select>
                        </div>
                        <div class="form-group full-width"><label for="settings-check-position">Check Position</label>
                            <select name="check_position" id="settings-check-position" aria-describedby="settings-check-position-help" onchange="SettingsPage.checkFieldSizesFollowPosition(this.value)">
                                <option value="bottom" ${s.check_position !== 'top' ? 'selected' : ''}>Check at the bottom (stub, stub, check)</option>
                                <option value="top" ${s.check_position === 'top' ? 'selected' : ''}>Check on top (check, voucher, voucher)</option>
                            </select>
                        </div>
                        <div class="form-group full-width"><label for="settings-check-memo">Check memo</label>
                            <select name="check_memo" id="settings-check-memo" aria-describedby="settings-check-memo-help">
                                <option value="reference" ${!['memo', 'none'].includes(s.check_memo) ? 'selected' : ''}>The bill's number (196132)</option>
                                <option value="memo" ${s.check_memo === 'memo' ? 'selected' : ''}>The bill's memo</option>
                                <option value="none" ${s.check_memo === 'none' ? 'selected' : ''}>Nothing</option>
                            </select>
                        </div>
                        <div class="form-group"><label>Horizontal nudge (in)</label>
                            <input name="check_nudge_x_in" type="number" min="-0.5" max="0.5" step="0.01"
                                title="Inches, -0.5 to 0.5, in 0.01 steps"
                                value="${escapeHtml(s.check_nudge_x_in || '0')}"></div>
                        <div class="form-group"><label>Vertical nudge (in)</label>
                            <input name="check_nudge_y_in" type="number" min="-0.5" max="0.5" step="0.01"
                                title="Inches, -0.5 to 0.5, in 0.01 steps"
                                value="${escapeHtml(s.check_nudge_y_in || '0')}"></div>
                        ${SettingsPage._checkFieldLayoutHtml(s.check_field_layout, s.check_position)}
                        <div class="form-group full-width">
                            <div style="display:flex; align-items:center; gap:6px; font-size:12px;">
                                <input type="checkbox" id="settings-check-amount-no-dollar-sign" name="check_amount_no_dollar_sign" value="true" data-bool aria-describedby="settings-check-amount-no-dollar-sign-help" style="width:auto; margin:0;" ${s.check_amount_no_dollar_sign === 'true' ? 'checked' : ''}>
                                <label for="settings-check-amount-no-dollar-sign" style="font-weight:normal; margin:0;">Leave the dollar sign off the amount</label>
                            </div>
                            <div style="display:flex; align-items:center; gap:6px; font-size:12px; margin-top:6px;">
                                <input type="checkbox" id="settings-check-amount-asterisk-fill" name="check_amount_asterisk_fill" value="true" data-bool aria-describedby="settings-check-amount-asterisk-fill-help" style="width:auto; margin:0;" ${s.check_amount_asterisk_fill === 'true' ? 'checked' : ''}>
                                <label for="settings-check-amount-asterisk-fill" style="font-weight:normal; margin:0;">Fill the amount with asterisks</label>
                            </div>
                        </div>
                        <div class="form-group full-width">
                            <div style="display:flex; align-items:center; gap:6px; font-size:12px;">
                                <input type="checkbox" id="settings-check-blank-stock" name="check_blank_stock" value="true" data-bool aria-describedby="settings-check-blank-stock-help" style="width:auto; margin:0;" ${s.check_blank_stock === 'true' ? 'checked' : ''}>
                                <label for="settings-check-blank-stock" style="font-weight:normal; margin:0;">Print on blank check stock</label>
                            </div>
                            <div style="display:flex; align-items:center; gap:6px; font-size:12px; margin-top:6px;">
                                <input type="checkbox" id="settings-check-micr-amount" name="check_micr_amount" value="true" data-bool aria-describedby="settings-check-micr-amount-help" style="width:auto; margin:0;" ${s.check_micr_amount === 'true' ? 'checked' : ''}>
                                <label for="settings-check-micr-amount" style="font-weight:normal; margin:0;">Encode the amount in the MICR line</label>
                            </div>
                        </div>
                        <div class="form-group full-width" id="settings-check-bank-details" role="group" aria-labelledby="settings-check-bank-details-h" aria-describedby="settings-check-bank-details-help">
                            <div id="settings-check-bank-details-h" style="font-weight:bold; font-size:12px; margin-bottom:4px;">Bank account details for blank stock</div>
                            <div class="form-grid">
                                <div class="form-group full-width"><label for="check-bank-account">Bank account</label>
                                    <select id="check-bank-account" data-admin onchange="SettingsPage.loadCheckBankDetails()">${SettingsPage._checkBankOptions(accounts)}</select></div>
                                <div class="form-group"><label for="check-bank-name">Bank name</label>
                                    <input id="check-bank-name" data-admin maxlength="100" placeholder="First Prairie Bank"></div>
                                <div class="form-group"><label for="check-bank-city">Bank city and state</label>
                                    <input id="check-bank-city" data-admin maxlength="100" placeholder="Joliet, IL"></div>
                                <div class="form-group"><label for="check-bank-routing">Routing number</label>
                                    <input id="check-bank-routing" data-admin maxlength="9" inputmode="numeric" placeholder="9 digits"></div>
                                <div class="form-group"><label for="check-bank-account-number">Account number</label>
                                    <input id="check-bank-account-number" data-admin maxlength="17" inputmode="numeric" autocomplete="off" aria-describedby="check-bank-account-number-help">
                                    <div id="check-bank-account-number-help" style="font-size:10px; color:var(--text-muted); margin-top:2px;"></div></div>
                                <div class="form-group"><label for="check-bank-fraction">Fractional routing number (optional)</label>
                                    <input id="check-bank-fraction" data-admin maxlength="30" placeholder="70-1234/719"></div>
                                <div class="form-group full-width" data-write>
                                    <!-- an administrator's save (the route sits under /api/settings): hidden from everyone else, like Remove logo -->
                                    <button type="button" class="btn btn-sm btn-secondary" data-admin data-write onclick="SettingsPage.saveCheckBankDetails()">Save bank account details</button>
                                </div>
                            </div>
                        </div>
                        <div class="form-group full-width">
                            <button type="button" class="btn btn-sm btn-secondary" aria-describedby="settings-check-test-help" onclick="SettingsPage.testPrintCheck()">Print a test check</button>
                        </div>
                        ${SettingsPage._depositSlipHtml(s)}
                    </div>
                </div>

                <div class="settings-section" data-group="backups" id="settings-backups" role="group" aria-labelledby="settings-h-backup-restore">
                    <div class="settings-h-row"><h3 id="settings-h-backup-restore">Backup / Restore</h3>${hintList('Backup / Restore', [
                        { id: 'settings-backups-help', text: `Backups of this company only, named for it. Restore replaces everything in this
                        company with the backup; a safety backup of the books as they are is taken first,
                        so a restore can be undone by restoring that one.` },
                    ])}</div>
                    <div style="display:flex; gap:8px; margin-bottom:12px;" data-admin data-write>
                        <button type="button" class="btn btn-primary" onclick="SettingsPage.createBackup()">Create Backup</button>
                    </div>
                    <div class="hidden" data-admin-note style="font-size:11px; color:var(--text-muted); margin-bottom:8px;">Backups are made, downloaded and restored by an administrator.</div>
                    <div id="backup-list"></div>
                </div>

                <div class="settings-section" data-group="security" id="settings-company-file" data-admin hidden role="group" aria-labelledby="settings-h-company-file">
                    <h3 id="settings-h-company-file">Company File Encryption</h3>
                    <div id="company-file-status" style="font-size:11px; color:var(--text-muted); margin-bottom:8px;"></div>
                    <div id="company-file-actions" style="display:flex; gap:8px;" data-admin data-write></div>
                </div>

                ${SettingsPage._isAdmin() ? '<div class="settings-section" data-group="backups" id="settings-legacy-files" data-admin hidden></div>' : ''}

                <div class="settings-section" data-group="security" id="settings-users" data-admin hidden role="group" aria-labelledby="settings-h-users-server-edition">
                    <div class="settings-h-row"><h3 id="settings-h-users-server-edition">Users &mdash; Server Edition</h3>${hintList('Users — Server Edition', [
                        { id: 'settings-users-help', text: `Add a second user and this deployment becomes
                        <strong>Server Edition</strong>: everyone signs in with a
                        username, every change is attributed in the audit log, and
                        roles limit what each person can do.` },
                    ])}</div>
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
                                <option value="accountant">Accountant — reads everything, posts journal entries</option>
                                <option value="readonly">Read-only — reports and lookups</option>
                                <option value="admin">Admin — everything</option>
                            </select></div>
                    </div>
                    <button type="button" class="btn btn-primary" onclick="SettingsPage.createUser()">Add User</button>
                    <button type="button" class="btn btn-secondary" onclick="SettingsPage.showInvite()">Invite your accountant…</button>
                </div>

                <div class="settings-section" data-group="security" id="settings-api-tokens" data-admin hidden role="group" aria-labelledby="settings-h-api-tokens-agents-integrations">
                    <div class="settings-h-row"><h3 id="settings-h-api-tokens-agents-integrations">API Tokens &mdash; agents &amp; integrations</h3>${hintList('API Tokens — agents & integrations', [
                        { id: 'settings-api-tokens-help', text: `Scoped credentials for non-humans: AI agents, the receipt
                        service, scripts. A token wears a role just like a user —
                        give read-only to anything that only reports, and every
                        change it makes is attributed in the audit log as
                        <code>token:&lt;label&gt;</code>. A token is never an
                        administrator: it cannot manage users or other tokens,
                        change settings or restore a backup. Tick
                        <em>Can make backups</em> for a program that takes a backup
                        before it writes (an import tool); the token may then make,
                        list and download this company's backups. A bookkeeper token
                        can also be let <em>run payroll</em> (pay runs, tax forms,
                        benefits, deductions, and employees added and edited, never
                        deleted, with no portal logins, bank accounts or documents)
                        or <em>import</em> (the migration imports). A revoked token
                        stays revoked: make a new one instead. A bookkeeper token is
                        what <code>sbplive setup</code> asks for, to let Claude work
                        the books through SlowBooks Switchboard (Connections, AI
                        assistants); make a read-only one for an agent that only reports.
                        A token whose name starts with "AI assistant" stops working
                        when the administrator who made it is no longer one here.` },
                    ])}</div>
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
                            <select id="token-new-role" onchange="SettingsPage.tokenGrantsFollowRole()">
                                <option value="readonly">Read-only — reports and lookups</option>
                                <option value="accountant">Accountant — reads everything, posts journal entries</option>
                                <option value="bookkeeper">Bookkeeper — daily books, no admin</option>
                            </select></div>
                        <div class="form-group"><label style="font-weight:normal;">
                            <input type="checkbox" id="token-new-backups" style="width:auto; vertical-align:middle; margin-right:6px;">
                            Can make backups (make, list and download; never restore)</label></div>
                        <div class="form-group"><label style="font-weight:normal;">
                            <input type="checkbox" id="token-new-payroll" disabled style="width:auto; vertical-align:middle; margin-right:6px;">
                            Can run payroll (bookkeeper only; no deletes, portal logins, bank accounts or documents)</label></div>
                        <div class="form-group"><label style="font-weight:normal;">
                            <input type="checkbox" id="token-new-imports" disabled style="width:auto; vertical-align:middle; margin-right:6px;">
                            Can import (bookkeeper only; the migration imports)</label></div>
                    </div>
                    <button type="button" class="btn btn-primary" onclick="SettingsPage.createApiToken()">Create Token</button>
                </div>

            </form>`;
        // The Save bar is the panel's, not the form's (NEW-210): held inside
        // the form it could not stay in view when My Preferences, above the
        // form, filled a short window, and the way round that (the form
        // drawing no box of its own) left the panel empty to an accessibility
        // client on the Mac. Its button is still the form's own submit.
        const savebar = `
                <!-- Always in reach: the one Save for the settings above sat at
                     the very bottom, after 25 other buttons, and the first
                     "Save" on the page was AI Insights' (explore 2.17.3, L16). -->
                <div class="form-actions" data-admin id="settings-savebar" data-write
                    style="position:sticky; bottom:0; z-index:5; align-items:center; background:var(--content-bg);
                           padding-bottom:10px; box-shadow:0 -4px 8px -6px rgba(0,0,0,0.25);">
                    <span id="settings-dirty-note" role="status" aria-live="polite"
                        style="margin-right:auto; font-size:11px; color:var(--text-muted);"></span>
                    <span id="settings-dirty-groups" class="settings-dirty-groups" role="status" aria-live="polite"></span>
                    <button type="submit" form="settings-form" class="btn btn-primary" id="settings-save-btn">Save Settings</button>
                </div>`;
        return `
            <div class="page-header">
                <h2>Company Settings</h2>
            </div>
            <div id="settings-unreadable" class="hint hint--locked" role="status" style="margin-bottom:12px;" data-admin hidden></div>
            <div class="settings-layout" id="settings-layout">
                ${SettingsPage._groupNavHtml()}
                <div class="settings-panel" id="settings-panel" role="tabpanel">
                    ${SettingsPage._myPreferencesHtml()}
                    ${form}
                    ${savebar}
                </div>
            </div>`;
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

    // --- Recent sends (the email_log, #447) ------------------------------
    // How many a page fetches, and the paging state the "Show more" button
    // advances. `_emailLogStatus` is '' (all) or 'failed'.
    _EMAIL_LOG_PAGE: 50,
    _emailLogOffset: 0,
    _emailLogStatus: '',

    // What sent it, in words. The row stores the raw entity_type the send
    // passed (invoice, statement, settings_test, …); this is its face.
    _emailSourceLabel(entityType) {
        const map = {
            invoice: T('Invoice'),
            estimate: 'Estimate',
            reminder: 'Payment reminder',
            statement: 'Statement',
            giving_statement: 'Giving statement',
            collection: 'Collection letter',
            settings_test: 'Test',
            portal: 'Portal',
            employee: 'Employee',
        };
        if (map[entityType]) return map[entityType];
        if ((entityType || '').startsWith('acknowledgment')) return 'Acknowledgment';
        return entityType || '—';
    },

    // A failed row's two lines: a sentence a person can act on, and the mail
    // server's own reply under it. The sentence is the one the API returns
    // (`failure`, from app/services/email_errors.py: the single mapping, so
    // the list and the test-email message never word an error two ways); it
    // never holds anything the server said. The server's reply
    // (`error_message`: plain text as it was received, one refused address
    // per line) comes only to an administrator, whose section this is.
    _emailLogFailure(row) {
        const raw = (row.error_message || '').trim();
        const sentence = (row.failure || '').trim() || 'The email could not be sent.';
        // a reader given the sentence in both fields sees it once
        return { sentence, raw: raw.startsWith(sentence) ? raw.slice(sentence.length).trim() : raw };
    },

    // A stored send time in the operator's local zone. The server sends ISO
    // with its zone (+00:00); a bare stamp is read as UTC, as the audit list
    // does, so a 7 pm Central send never shows tomorrow's date.
    _emailLogWhen(iso) {
        if (!iso) return '—';
        const withZone = /Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z';
        return new Date(withZone).toLocaleString();
    },

    // The rows as a small table; pure so the node suite can assert on it.
    _emailLogRowsHtml(rows) {
        if (!rows.length) {
            return '<div style="font-size:11px; color:var(--text-muted);">No emails have been sent yet.</div>';
        }
        return `<table class="data-table" style="font-size:11px;">
            <thead><tr><th scope="col">When</th><th scope="col">To</th><th scope="col">Subject</th><th scope="col">What</th><th scope="col">Status</th></tr></thead>
            <tbody>${rows.map(r => {
                const failed = r.status === 'failed';
                let statusCell = failed
                    ? '<span style="color:var(--danger, #b00);">Failed</span>'
                    : 'Sent';
                let detail = '';
                if (failed) {
                    const f = SettingsPage._emailLogFailure(r);
                    detail = `<div style="color:var(--danger, #b00); margin-top:2px;">${escapeHtml(f.sentence)}</div>` +
                        (f.raw ? `<div style="color:var(--text-muted); font-family:var(--font-mono); word-break:break-word; white-space:pre-line;">${escapeHtml(f.raw)}</div>` : '');
                } else if (r.note) {
                    // sent, though the server refused a copy: who was refused,
                    // and the server's reply to each under it
                    const raw = (r.error_message || '').trim();
                    detail = `<div style="color:var(--danger, #b00); margin-top:2px;">${escapeHtml(r.note)}</div>` +
                        (raw ? `<div style="color:var(--text-muted); font-family:var(--font-mono); word-break:break-word; white-space:pre-line;">${escapeHtml(raw)}</div>` : '');
                }
                return `<tr>
                    <td style="white-space:nowrap;">${escapeHtml(SettingsPage._emailLogWhen(r.sent_at))}</td>
                    <td>${escapeHtml(r.recipient || '—')}${r.cc ? `<div style="color:var(--text-muted);">cc ${escapeHtml(r.cc)}</div>` : ''}${r.bcc ? `<div style="color:var(--text-muted);">bcc ${escapeHtml(r.bcc)}</div>` : ''}</td>
                    <td>${escapeHtml(r.subject || '—')}</td>
                    <td>${escapeHtml(SettingsPage._emailSourceLabel(r.entity_type))}</td>
                    <td>${statusCell}${detail}</td>
                </tr>`;
            }).join('')}</tbody></table>`;
    },

    // The "failed only" toggle: reset to the first page under the new filter.
    filterEmailLog(failedOnly) {
        SettingsPage._emailLogStatus = failedOnly ? 'failed' : '';
        SettingsPage.loadEmailLog(true);
    },

    // Load a page. reset=true replaces the list (first page / filter change);
    // reset=false appends the next page for "Show more". Admin only — the
    // route refuses every other role, so the whole section is hidden when the
    // fetch is refused rather than showing an error to a bookkeeper.
    async loadEmailLog(reset) {
        const section = document.getElementById('settings-email-recent-sends');
        const host = document.getElementById('email-log-rows');
        if (!section || !host || !SettingsPage._isAdmin()) return;
        if (reset) SettingsPage._emailLogOffset = 0;
        const limit = SettingsPage._EMAIL_LOG_PAGE;
        const params = `?limit=${limit}&offset=${SettingsPage._emailLogOffset}` +
            (SettingsPage._emailLogStatus ? `&status=${SettingsPage._emailLogStatus}` : '');
        let data;
        try {
            data = await API.get('/email-log' + params);
        } catch (e) {
            SettingsPage._showSection(section, false);
            return;
        }
        const rows = data.rows || [];
        const html = SettingsPage._emailLogRowsHtml(rows);
        if (reset) host.innerHTML = html;
        else host.insertAdjacentHTML('beforeend', html);
        SettingsPage._emailLogOffset += rows.length;
        const more = document.getElementById('email-log-more');
        if (more) more.hidden = SettingsPage._emailLogOffset >= (data.total || 0);
        SettingsPage._showSection(section, true);
    },

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
        // Only an administrator saves these fields: any other sign-in sees
        // them locked, and nothing on the page is an edit. Locking takes the
        // fields out of FormData, so a page locked after its snapshot (the
        // role arrives after a first page) compared as changed, and asked a
        // read-only user, then a bookkeeper, to leave without saving.
        if (!SettingsPage._isAdmin()) return false;
        if (typeof App !== 'undefined' && App.isReadOnly && App.isReadOnly()) return false;
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
        // the dot on each group holding a change, and their count by Save
        SettingsPage._showDirtyGroups();
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
                    // The browser has already moved to the link's new entry
                    // (or the one Back/Forward went to); the page is still
                    // Settings, so the history goes back to the Settings
                    // entry itself, nothing re-rendered on the way, and the
                    // toolbar's Back keeps what that entry has behind it.
                    if (location.hash !== '#/settings') App.stayPut();
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
        window.addEventListener('slowbooks:settings-changed', e => {
            SettingsPage._changedElsewhere(e && e.detail);
        });
    },

    // The settings changed outside this window while the page is open (#828):
    // the shell found it on focus (App.refreshSettings). A page with no
    // unsaved edits draws again from the saved values, in place. Unsaved
    // edits are not drawn over unasked: when a field the change reached is
    // on the form, the page asks first, as leaving it does; Cancel keeps
    // the edits (a Save then writes them over the other change). A change
    // that reached no field on the form leaves the edits alone. A page
    // opening reads the settings afresh anyway, so only focus counts here.
    _changedElsewhere(detail) {
        const d = detail || {};
        if (d.reason !== 'focus') return false;
        const form = document.getElementById('settings-form');
        if (!form || !String(location.hash || '').startsWith('#/settings')) return false;
        const before = d.previous || {};
        const after = d.settings || {};
        const changed = Object.keys(after).filter(k => JSON.stringify(before[k]) !== JSON.stringify(after[k]));
        if (!changed.length) return false;
        if (SettingsPage.isDirty()) {
            const snap = SettingsPage._snapshot || {};
            const named = k => Object.prototype.hasOwnProperty.call(snap, k)
                || !!(form.elements && typeof form.elements.namedItem === 'function' && form.elements.namedItem(k));
            if (!changed.some(named)) return false;
            if (!confirm('These settings were changed somewhere else (another window, a script or an API token) '
                + 'while you had unsaved changes here. Show the saved settings? Your unsaved changes on this page will be lost.'
                + '\n\nCancel keeps your changes; saving them then replaces the other change.')) return false;
        }
        SettingsPage._snapshot = null;
        App.navigate('#/settings');
        return true;
    },

    // ------------------------------------------------------------------
    // Company File Encryption (#290) — administrators only; the server
    // enforces the same rule, this avoids a useless 403. The switch asks
    // for the company's name typed, the way a restore asks before it
    // replaces the books.
    // ------------------------------------------------------------------
    async loadCompanyFile() {
        const section = $('#settings-company-file');
        if (!section || !SettingsPage._isAdmin()) return;
        let st;
        try {
            st = await API.get('/company-file/encryption');
        } catch (e) {
            return; // non-admin or pre-upgrade server: section stays hidden
        }
        SettingsPage._companyFile = st;
        const status = $('#company-file-status');
        const actions = $('#company-file-actions');
        actions.innerHTML = '';
        if (st.engine === 'postgresql') {
            status.textContent = 'This company runs on PostgreSQL. Encrypting it at rest is the database host\'s job: '
                + 'PostgreSQL\'s own disk encryption, or the volume it sits on.';
        } else if (!st.supported) {
            status.textContent = 'This build cannot encrypt the company file.';
        } else if (st.encrypted) {
            const ring = st.keyring;
            let keyWords;
            if (st.key_source === 'secret') keyWords = 'The key comes from COMPANY_FILE_ENCRYPTION_SECRET.';
            else if (ring) {
                const who = ring.admins.length ? ring.admins.map(escapeHtml).join(', ') : 'nobody yet';
                keyWords = `An administrator's sign-in unlocks it (${who}${ring.stale.length ? `; ${ring.stale.map(escapeHtml).join(', ')} at their next sign-in` : ''}), `
                    + `and so does the recovery key${ring.recovery_made_at ? ` made ${SettingsPage._when(ring.recovery_made_at)}` : ''}. `
                    + 'Keep the recovery key where the backups are not: it opens the file, and every backup of it, if every administrator\'s password is lost.';
            } else keyWords = 'The key comes from the launcher.';
            // backups made before Turn on stay plain SQLite in the same list
            // (NEW-158 / W-25): say so, and how many, from the list itself
            // (loadBackups may not have run yet)
            let backups = [];
            try { backups = await API.get('/backups'); } catch (e) { backups = []; }
            SettingsPage._backups = backups;
            const plain = backups.filter(b => b.encrypted === false).length;
            const backupWords = plain
                ? `Backups made while it is on are encrypted; ${plain} backup${plain === 1 ? '' : 's'} in the list below ${plain === 1 ? 'was' : 'were'} made before and ${plain === 1 ? 'is' : 'are'} plain SQLite, readable with no key: delete ${plain === 1 ? 'it' : 'them'} once a newer backup exists. `
                : 'Backups made while it is on are encrypted. ';
            status.innerHTML = `<strong>On.</strong> ${escapeHtml(st.file)} is encrypted on disk (SQLCipher ${escapeHtml(st.cipher_version || '')}). `
                + backupWords + keyWords;
            actions.innerHTML = (ring ? '<button type="button" class="btn btn-secondary" onclick="SettingsPage.confirmRecoveryKey()">New recovery key…</button> ' : '')
                + '<button type="button" class="btn btn-secondary" onclick="SettingsPage.confirmEncryption(false)">Turn off…</button>';
        } else {
            status.innerHTML = `<strong>Off.</strong> ${escapeHtml(st.file)} is a plain SQLite file: anyone with the file can read the books. `
                + (st.can_turn_on
                    ? (st.desktop_key
                        ? 'Turning it on makes a recovery key, shown once, and your sign-in unlocks the books from then on.'
                        : 'A key is set, so encryption can be turned on.')
                    : 'This is a server, so there is nobody at the window to unlock the file: to turn it on, set COMPANY_FILE_ENCRYPTION_SECRET '
                      + 'in the server\'s .env file (C:\\ProgramData\\SlowBooksPro\\.env on a Windows server started with --serve-lan; the Docker .env otherwise), '
                      + 'stop and start SlowBooks Pro, and come back here. The Encrypting the company file guide has the steps.');
            actions.innerHTML = `<button type="button" class="btn btn-primary" ${st.can_turn_on ? '' : 'disabled'} onclick="SettingsPage.confirmEncryption(true)">Turn on…</button>`;
        }
        SettingsPage._showSection(section, true);
        // the list marks the plain backups once the state is known
        SettingsPage.loadBackups();
    },

    async _companyNameNow() {
        // The company as it is now, not the shell's copy from when the
        // window opened (a rename since would name it wrong).
        let company = (App.settings && App.settings.company_name) || '';
        try {
            const now = await API.get('/settings');
            if (now && now.company_name) company = now.company_name;
        } catch (e) { /* the shell's copy will do */ }
        return company;
    },

    _nameConfirmField(company) {
        // The name to type sits under the box as text, not only as a
        // placeholder that disappears, and the check below says when what
        // was typed is not it yet (Discussion #750: a user saw a grey button
        // and a field that "did nothing").
        return `<div class="form-group">
                    <label for="encryption-company-name">Company name</label>
                    <input type="text" id="encryption-company-name" name="company_name" autocomplete="off" required
                        placeholder="${escapeHtml(company)}" aria-describedby="encryption-name-help"
                        oninput="SettingsPage._encryptionFormCheck(this.form)">
                    <div id="encryption-name-help" style="font-size:11px; color:var(--text-muted); margin-top:2px;">Exactly as Settings shows it: <strong>${escapeHtml(company)}</strong>.</div>
                </div>`;
    },

    // Submit opens once the name matches and, where the form asks for it,
    // the password is typed; the help line under the name says where it
    // stands.
    _encryptionFormCheck(form) {
        const typed = form.company_name.value.trim();
        const named = typed.toLowerCase() === SettingsPage._encryptionName;
        const pw = form.querySelector('[name=password]');
        const help = form.querySelector('#encryption-name-help');
        if (help) {
            const shown = `<strong>${escapeHtml(SettingsPage._encryptionCompany || '')}</strong>`;
            if (!typed) {
                help.style.color = 'var(--text-muted)';
                help.innerHTML = `Exactly as Settings shows it: ${shown}.`;
            } else if (named) {
                help.style.color = 'var(--text-muted)';
                help.innerHTML = 'Matches.';
            } else {
                help.style.color = 'var(--text-danger)';
                help.innerHTML = `Not the same yet. Type it exactly as Settings shows it: ${shown}.`;
            }
        }
        form.querySelector('button[type=submit]').disabled = !(named && (!pw || pw.value.length > 0));
    },

    async confirmEncryption(enable) {
        const company = await SettingsPage._companyNameNow();
        SettingsPage._encryptionName = company.trim().toLowerCase();
        SettingsPage._encryptionCompany = company.trim();
        const st = SettingsPage._companyFile || {};
        const desktopKey = enable && st.desktop_key;
        // Turn off proves the administrator the way Turn on does (the
        // agents' question, round 1): the password is asked when a person
        // is signed in
        const asksPassword = desktopKey || (!enable && App.username);
        const what = enable
            ? `<p style="margin:0 0 8px;"><strong>This rewrites the company file encrypted.</strong> From then on the file, and every backup
                of it, opens only with the key; without the key the books are unreadable, including to SlowBooks Pro.</p>`
              + (desktopKey
                ? `<p style="margin:0 0 8px;">SlowBooks Pro makes a <strong>recovery key</strong> and shows it once: write it down or save the file,
                    and keep it where the backups are not. Your sign-in unlocks the books from then on; every other administrator's does
                    after their next sign-in.</p>`
                : '')
            : `<p style="margin:0 0 8px;"><strong>This rewrites the company file as plain SQLite.</strong> Anyone with the file can read
                the books again${st.keyring ? ', and the recovery key is retired' : ''}.</p>`;
        if (enable && desktopKey) {
            // where the text file lands: a Save panel on the desktop, else the
            // browser's download; never a synced Documents folder unasked
        }
        openModal(enable ? 'Turn on company file encryption' : 'Turn off company file encryption', `
            <form id="encryption-form" onsubmit="SettingsPage.changeEncryption(event, ${enable})">
                ${what}
                <p style="margin:0 0 8px;">Nobody else should be using the company while this runs. Type the company's name to confirm.</p>
                ${SettingsPage._nameConfirmField(company)}
                ${asksPassword ? `<div class="form-group">
                    <label for="encryption-password">Your sign-in password ${SettingsPage._hintWithId(desktopKey
                        ? 'The password you signed in with, not a new one: SlowBooks Pro makes the recovery key for you and shows it once. Your sign-in unlocks the books from then on.'
                        : 'The password you signed in with.', 'Your sign-in password', 'encryption-password-help')}</label>
                    <input type="password" id="encryption-password" name="password" autocomplete="current-password" required
                        aria-describedby="encryption-password-help"
                        oninput="SettingsPage._encryptionFormCheck(this.form)">
                </div>` : ''}
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn ${enable ? 'btn-primary' : 'btn-danger'}" disabled>${enable ? 'Encrypt the company file' : 'Remove the encryption'}</button>
                </div>
            </form>`);
    },

    async changeEncryption(e, enable) {
        e.preventDefault();
        const form = e.target;
        const btn = form.querySelector('button[type=submit]');
        const label = btn.textContent;
        btn.disabled = true;
        btn.textContent = enable ? 'Encrypting…' : 'Rewriting…';
        const body = { enabled: enable, company_name: form.company_name.value };
        if (form.password) body.password = form.password.value;
        try {
            const result = await API.post('/company-file/encryption', body);
            closeModal();
            if (result.recovery_key) SettingsPage.showRecoveryKey(result, 'The company file is encrypted.');
            else toast(result.encrypted ? 'The company file is encrypted.' : 'The company file is plain SQLite again.');
            SettingsPage.loadCompanyFile();
        } catch (err) {
            toast(err.message, 'error');
            btn.disabled = false;
            btn.textContent = label;
        }
    },

    // The recovery key, the one time it is shown (#290): copied, or saved
    // as a text file (through the desktop bridge where there is one).
    showRecoveryKey(result, lead) {
        SettingsPage._recoveryFile = result.recovery_file || result.recovery_key;
        openModal('Your recovery key', `
            <p style="margin:0 0 8px;">${escapeHtml(lead)} <strong>This recovery key is shown once.</strong> It opens the company file,
                and every backup of it, if every administrator's password is lost, and then lets you set a new administrator password.
                Anyone holding it can read the books, so keep it
                where the backups are not: a safe, a password manager, a sealed envelope.</p>
            <p style="margin:0 0 12px; font-family:var(--mono, monospace); font-size:16px; letter-spacing:0.04em; text-align:center;
                padding:10px; border:1px solid var(--gray-200); border-radius:4px; user-select:all;" id="recovery-key-words">${escapeHtml(result.recovery_key)}</p>
            <div class="form-actions" style="justify-content:space-between;">
                <div>
                    <button type="button" class="btn btn-secondary" onclick="SettingsPage.copyRecoveryKey()">Copy</button>
                    <button type="button" class="btn btn-secondary" onclick="SettingsPage.saveRecoveryKey()">Save as a text file</button>
                </div>
                <button type="button" class="btn btn-primary" onclick="closeModal()">I have kept it</button>
            </div>`);
    },

    async copyRecoveryKey() {
        const words = ($('#recovery-key-words') || {}).textContent || '';
        await copyToClipboard(words, 'Recovery key');
    },

    async saveRecoveryKey() {
        const company = ((App.settings && App.settings.company_name) || 'company').replace(/[^A-Za-z0-9 _-]+/g, '').trim() || 'company';
        const name = `${company} recovery key.txt`;
        const text = SettingsPage._recoveryFile || '';
        // On the desktop the file goes where the person chooses, through a
        // Save panel: the Reports folder every export lands in is synced to
        // a cloud on many machines, and a recovery key should not be
        // (macbase1 and skytech, round 1).
        const api = window.pywebview && window.pywebview.api;
        if (api && api.save_secret_file) {
            const r = await api.save_secret_file(name, text);
            if (r && r.success) toast(`Saved to ${r.path}`);
            else if (r && r.cancelled) toast('Not saved: the key is still on screen.');
            else toast(`Could not save the file: ${(r && r.error) || 'unknown error'}`, 'error');
            return;
        }
        const blob = new Blob([text], { type: 'text/plain' });
        if (window.SlowbooksDesktop && await window.SlowbooksDesktop.saveFile(blob, name)) return;
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = name;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 30000);
    },

    async confirmRecoveryKey() {
        const company = await SettingsPage._companyNameNow();
        SettingsPage._encryptionName = company.trim().toLowerCase();
        openModal('Make a new recovery key', `
            <form id="encryption-form" onsubmit="SettingsPage.newRecoveryKey(event)">
                <p style="margin:0 0 8px;"><strong>This retires the recovery key you have.</strong> The company file is re-keyed under the
                    new one and shown once; your sign-in unlocks it at once, every other administrator's after their next sign-in.
                    Backups made before today open with the old recovery key only.</p>
                <p style="margin:0 0 8px;">Nobody else should be using the company while this runs. Type the company's name and your password to confirm.</p>
                ${SettingsPage._nameConfirmField(company)}
                <div class="form-group">
                    <label for="encryption-password">Your password</label>
                    <input type="password" id="encryption-password" name="password" autocomplete="current-password" required
                        oninput="SettingsPage._encryptionFormCheck(this.form)">
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary" disabled>Make a new recovery key</button>
                </div>
            </form>`);
    },

    async newRecoveryKey(e) {
        e.preventDefault();
        const form = e.target;
        const btn = form.querySelector('button[type=submit]');
        const label = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Re-keying…';
        try {
            const result = await API.post('/company-file/encryption/recovery-key', { company_name: form.company_name.value, password: form.password.value });
            closeModal();
            SettingsPage.showRecoveryKey(result, 'The company file is re-keyed.');
            SettingsPage.loadCompanyFile();
        } catch (err) {
            toast(err.message, 'error');
            btn.disabled = false;
            btn.textContent = label;
        }
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
            const rows = users.map(u => `<tr>
                <td>${escapeHtml(u.username)}</td>
                <td>${escapeHtml(u.display_name)}</td>
                <td>
                    <select onchange="SettingsPage.updateUser(${u.id}, {role: this.value})">
                        ${['admin', 'bookkeeper', 'accountant', 'readonly'].map(r =>
                            `<option value="${r}" ${u.role === r ? 'selected' : ''}>${r}</option>`).join('')}
                    </select>
                </td>
                <td>${u.last_login_at ? formatDate(u.last_login_at) : '—'}${u.must_change_password ? '<div style="font-size:10px; color:var(--gray-500);">one-time password, not yet replaced</div>' : ''}</td>
                <td>${u.expires_on
                    ? `<input type="date" value="${escapeHtml(u.expires_on)}" aria-label="Day ${escapeHtml(u.username)}'s sign-in ends" style="font-size:11px; padding:2px 4px;"
                        onchange="SettingsPage.updateUser(${u.id}, {expires_on: this.value || null})">${u.ended ? '<div style="font-size:10px; color:var(--danger);">ended</div>' : ''}`
                    : `<span style="color:var(--text-muted);">none</span> <button type="button" class="btn btn-sm btn-secondary" aria-label="Set a day ${escapeHtml(u.username)}'s sign-in ends"
                        onclick="SettingsPage.askEndDate(${u.id}, '${escapeHtml(u.username)}')">Set…</button>`}</td>
                <td>${u.is_active
                    ? `<button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.updateUser(${u.id}, {is_active: false})">Deactivate</button>`
                    : `<button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.updateUser(${u.id}, {is_active: true})">Reactivate</button>`}
                    <button type="button" class="btn btn-sm btn-secondary" onclick="SettingsPage.resetUserPassword(${u.id}, '${escapeHtml(u.username)}')">Reset password</button>
                </td>
            </tr>`).join('');
            $('#users-list').innerHTML = `<div class="table-container"><table>
                <thead><tr><th scope="col">Username</th><th scope="col">Name</th><th scope="col">Role</th><th scope="col">Last login</th><th scope="col">Ends</th><th scope="col"></th></tr></thead>
                <tbody>${rows}</tbody></table></div>`;
            SettingsPage._showSection(section, true);
        } catch (e) { /* non-admin or pre-upgrade server: section stays hidden */ }
    },

    // The start-up password choice applies to the desktop app (and its
    // Server Edition), where one server process serves the company.
    async loadSignInPref() {
        const section = $('#settings-sign-in');
        if (!section) return;
        try {
            const sys = await API.get('/system');
            if (sys && sys.desktop) SettingsPage._showSection(section, true);
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
            if (!tokens.length) {
                $('#api-token-list').innerHTML =
                    '<div style="font-size:11px; color:var(--text-muted);">No tokens yet.</div>';
                SettingsPage._showSection(section, true);
                return;
            }
            const rows = tokens.map(t => `<tr>
                <td>${escapeHtml(t.label)}</td>
                <td style="font-family:var(--font-mono); font-size:10px;">${escapeHtml(t.token_hint)}&hellip;</td>
                <td>${escapeHtml(t.role)}${t.refused ? `<div style="font-size:10px;color:var(--text-danger)">no longer accepted: tokens can't be administrators. Revoke it and make a bookkeeper, accountant or read-only token (tick Can make backups if it makes backups). Settings, payroll and HR need an administrator signed in.</div>` : ''}${t.maker_gone ? `<div style="font-size:10px;color:var(--text-danger)">not working: the administrator who made it is no longer an administrator of this company. Revoke it and make a new one.</div>` : ''}</td>
                <td>${t.is_active
                    ? `<input type="checkbox" aria-label="${escapeHtml(t.label)} can make backups" style="width:auto;" ${t.allow_backups ? 'checked' : ''} onchange="SettingsPage.updateApiToken(${t.id}, {allow_backups: this.checked})">`
                    : (t.allow_backups ? 'Yes' : 'No')}</td>
                ${['payroll', 'imports'].map(k => `<td>${t.is_active && t.role === 'bookkeeper'
                    ? `<input type="checkbox" aria-label="${escapeHtml(t.label)} can ${k === 'payroll' ? 'run payroll' : 'import'}" style="width:auto;" ${t['allow_' + k] ? 'checked' : ''} onchange="SettingsPage.updateApiToken(${t.id}, {allow_${k}: this.checked})">`
                    : (t['allow_' + k] ? 'Yes' : 'No')}</td>`).join('')}
                <td>${t.last_used_at ? formatDate(t.last_used_at) : '—'}</td>
                <td>${t.is_active
                    ? `<button type="button" class="btn btn-sm btn-secondary" onclick="if (confirm('Revoke this token? A revoked token stays revoked.')) SettingsPage.updateApiToken(${t.id}, {is_active: false})" aria-label="Revoke ${escapeHtml(t.label)}">Revoke</button>`
                    : 'Revoked'}
                </td>
            </tr>`).join('');
            $('#api-token-list').innerHTML = `<div class="table-container"><table>
                <thead><tr><th scope="col">Label</th><th scope="col">Token</th><th scope="col">Role</th><th scope="col">Backups</th><th scope="col">Payroll</th><th scope="col">Imports</th><th scope="col">Last used</th><th scope="col"></th></tr></thead>
                <tbody>${rows}</tbody></table></div>`;
            SettingsPage._showSection(section, true);
        } catch (e) { /* non-admin or pre-upgrade server: section stays hidden */ }
    },

    // Payroll and imports are a bookkeeper token's only (#755): the boxes
    // clear and lock for any other role, so the form can't ask for them.
    tokenGrantsFollowRole() {
        const book = $('#token-new-role').value === 'bookkeeper';
        for (const id of ['#token-new-payroll', '#token-new-imports']) {
            const box = $(id);
            if (!box) continue;
            box.disabled = !book;
            if (!book) box.checked = false;
        }
    },

    async createApiToken() {
        try {
            const created = await API.post('/tokens', {
                label: $('#token-new-label').value.trim(),
                role: $('#token-new-role').value,
                allow_backups: $('#token-new-backups').checked,
                allow_payroll: $('#token-new-payroll').checked,
                allow_imports: $('#token-new-imports').checked,
            });
            $('#api-token-secret').textContent = created.token;
            $('#api-token-reveal').style.display = '';
            $('#token-new-label').value = '';
            $('#token-new-backups').checked = false;
            $('#token-new-payroll').checked = false;
            $('#token-new-imports').checked = false;
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

    // A field's hint (#977) whose text keeps the id the field (and the
    // tests) already point at with aria-describedby.
    _hintWithId(text, about, id) {
        return hint(text, about, id);
    },

    // Invite your accountant (#345): a sign-in with a one-time password
    // and, if the engagement has one, an end date; the answer is shown
    // once, with the address to send.
    showInvite() {
        openModal('Invite your accountant', `
            <form onsubmit="SettingsPage.sendInvite(event)">
                <div class="form-grid">
                    <div class="form-group"><label>Username * ${hint('The sign-in gets a one-time password, shown once; the person chooses their own at the first sign-in. Leave the end date empty for a sign-in with no end.', 'Invitation')}</label><input name="username" required autocomplete="off"></div>
                    <div class="form-group"><label>Display name</label><input name="display_name" autocomplete="off"></div>
                    <div class="form-group"><label>Role</label>
                        <select name="role" data-no-search>
                            <option value="accountant">Accountant — reads everything, posts journal entries</option>
                            <option value="readonly">Read-only — reports and lookups</option>
                            <option value="bookkeeper">Bookkeeper — daily books, no admin</option>
                        </select></div>
                    <div class="form-group"><label>Sign-in ends on <span style="font-weight:normal; color:var(--text-muted);">(optional)</span> ${SettingsPage._hintWithId('Leave it empty for a sign-in with no end date.', 'Sign-in ends on', 'invite-expires-help')}</label><input name="expires_on" type="date" aria-describedby="invite-expires-help"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create the invitation</button>
                </div>
            </form>`);
    },

    // An empty date box is drawn by WebKit with today's date greyed in, which
    // read as "ends today" (NEW-151): a sign-in with no end date shows
    // "none", and the box appears on request.
    askEndDate(id, username) {
        openModal(`When does ${username}'s sign-in end?`, `
            <form onsubmit="event.preventDefault(); SettingsPage.updateUser(${id}, {expires_on: this.expires_on.value || null}); closeModal();">
                <div class="form-group"><label for="user-end-date">Sign-in ends on</label>
                    <input id="user-end-date" name="expires_on" type="date" required></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Set the end date</button>
                </div>
            </form>`);
    },

    async sendInvite(e) {
        e.preventDefault();
        const form = e.target;
        try {
            const r = await API.post('/users/invite', {
                username: form.username.value.trim(),
                display_name: form.display_name.value.trim(),
                role: form.role.value,
                expires_on: form.expires_on.value || null,
            });
            const note = `Address: ${r.address}${r.note ? ` (${r.note})` : ''}\nUsername: ${r.username}\nOne-time password: ${r.password}${r.expires_on ? `\nThe sign-in ends on ${r.expires_on}.` : ''}\n\nSign in once with this password; you will be asked to choose your own.`;
            // Each line copies on its own (NEW-167): the password is typed at
            // the sign-in, the rest goes in a message; the whole invitation
            // sits under them, selectable like any text.
            SettingsPage._invite = { address: r.address, username: r.username, password: r.password, note };
            openModal('Send this to your accountant', `
                <p style="font-size:12px; color:var(--text-muted); margin-bottom:8px;">The one-time password is shown here once; it is not stored anywhere readable. Each line copies on its own, or copy the whole invitation.</p>
                <div style="display:grid; grid-template-columns:max-content minmax(0,1fr) max-content; gap:6px 10px; align-items:center; margin-bottom:10px;">
                    ${copyLineHtml('invite-address', 'Address', r.address, "SettingsPage.copyInvite('address')")}
                    ${copyLineHtml('invite-username', 'Username', r.username, "SettingsPage.copyInvite('username')")}
                    ${copyLineHtml('invite-password', 'One-time password', r.password, "SettingsPage.copyInvite('password')")}
                </div>
                <textarea id="invite-note" readonly rows="7" aria-label="The whole invitation" style="width:100%; font-family:var(--font-mono); font-size:12px;">${escapeHtml(note)}</textarea>
                <div class="form-actions" style="justify-content:space-between;">
                    <button type="button" class="btn btn-secondary" onclick="SettingsPage.copyInvite('note')">Copy the whole invitation</button>
                    <button type="button" class="btn btn-primary" onclick="closeModal()">Done</button>
                </div>`);
            SettingsPage.loadUsers();
        } catch (err) { toast(err.message, 'error'); }
    },

    // One line of the invitation, or all of it, to the clipboard; where the
    // page may not write it (plain HTTP on a network address) the text is
    // selected for the copy shortcut instead.
    async copyInvite(which) {
        const inv = SettingsPage._invite || {};
        const labels = { address: 'Address', username: 'Username', password: 'One-time password', note: 'Invitation' };
        await copyToClipboard(inv[which] || '', labels[which] || 'Text', $(`#invite-${which}`));
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

    // One checkbox per sidebar section and per link (#334), read from the
    // sidebar itself so the list is always the menu as shipped. A ticked box
    // means "show it"; untick to hide (#457). The setting itself still lists
    // the HIDDEN keys, so a list saved by an earlier version keeps working.
    //
    // An item the menu setting hid is still offered, unticked, so it can be
    // shown again: the list used to skip every hidden <li>, the hidden items
    // fell off it, and the next save wrote only what was on the page, which
    // showed the first batch again (#457). Only an item hidden for another
    // reason (the nonprofit pages on a business company, NEW-145; the admin
    // pages for a clerk) is left out, and Save keeps its stored choice.
    _hiddenMenuStored: [],

    _hiddenMenuChoices(raw) {
        let hidden = [];
        try { hidden = JSON.parse(raw || '[]'); } catch (err) { hidden = []; }
        hidden = (Array.isArray(hidden) ? hidden : []).map(String);
        SettingsPage._hiddenMenuStored = hidden;
        const set = new Set(hidden);
        const box = (key, label, indent) => `<label style="display:block; font-weight:normal; ${indent ? 'margin-left:18px;' : 'margin-top:6px;'}">
                <input type="checkbox" name="show-menu:${escapeHtml(key)}" data-show-menu="${escapeHtml(key)}" ${set.has(key) ? '' : 'checked'} style="width:auto; vertical-align:middle; margin-right:6px;">
                ${escapeHtml(label)}</label>`;
        let html = '';
        const items = (typeof document !== 'undefined' && typeof document.querySelectorAll === 'function') ? [...document.querySelectorAll('#sidebar li')] : [];
        // A link hidden for its company's kind or the sign-in's role carries
        // its own mark (App.syncSidebarHidden), so what is offered does not
        // hang on which of the start-up steps finished first; a section is
        // offered when at least one of its links is.
        let section = '';
        items.forEach(li => {
            if (li.closest && li.closest('#recent-screens')) return;
            const parent = li.parentElement;
            if (parent && typeof parent.closest === 'function' && parent.closest('[hidden]')) return;
            if (li.classList.contains('nav-section')) {
                const name = li.textContent.trim();
                section = name ? box(`section:${name}`, `${name} (the whole section)`, false) : '';
                return;
            }
            const d = li.dataset || {};
            if (d.hiddenKind || d.hiddenRole) return;
            const link = li.querySelector('.nav-link[data-page]');
            if (!link || link.dataset.page === 'settings') return;
            html += section + box(link.dataset.page, link.textContent.trim(), true);
            section = '';
        });
        return html || '<span style="color:var(--text-muted);">The sidebar is not on this page.</span>';
    },

    // The hidden list Save writes: every unticked box on the page, plus the
    // stored keys this page did not offer (an item hidden for another reason
    // keeps the choice made for it) (#457).
    _hiddenMenuToSave(boxes) {
        const offered = new Set(boxes.map(b => b.dataset.showMenu));
        const keep = (SettingsPage._hiddenMenuStored || []).filter(k => !offered.has(k));
        const hide = boxes.filter(b => !b.checked).map(b => b.dataset.showMenu);
        return [...new Set([...keep, ...hide])];
    },

    // Record a provider payout that arrived without a bank feed (#584):
    // date, bank account, net and fee. Posts to the provider payout route,
    // which clears the clearing account for net + fee and books the fee.
    async showProviderPayout(provider, label) {
        const accounts = await API.get('/accounts?bank=1&active_only=true').catch(() => []);
        const opts = (Array.isArray(accounts) ? accounts : [])
            .map(a => `<option value="${a.id}">${accountLabel(a)}</option>`).join('');
        const today = new Date().toISOString().slice(0, 10);
        openModal(`Record ${escapeHtml(label)} payout`, `
            <form onsubmit="SettingsPage.submitProviderPayout(event, '${provider}')">
                <div class="form-grid">
                    <div class="form-group"><label>Date</label>
                        <input id="pp-date" type="date" value="${today}" required></div>
                    <div class="form-group"><label>Deposited to</label>
                        <select id="pp-bank" required>${opts}</select></div>
                    <div class="form-group"><label>Net to bank</label>
                        <input id="pp-net" type="number" step="0.01" min="0" required></div>
                    <div class="form-group"><label>Fee</label>
                        <input id="pp-fee" type="number" step="0.01" min="0" value="0.00"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Record payout</button>
                </div>
            </form>`);
    },

    async submitProviderPayout(e, provider) {
        e.preventDefault();
        const net = parseFloat(document.getElementById('pp-net').value) || 0;
        const fee = parseFloat(document.getElementById('pp-fee').value) || 0;
        if (fee < 0) { toast('The fee cannot be negative', 'error'); return; }
        try {
            await API.post(`/payments/${provider}/payout`, {
                date: document.getElementById('pp-date').value,
                bank_account_id: parseInt(document.getElementById('pp-bank').value, 10),
                net: net.toFixed(2),
                fee: fee.toFixed(2),
            });
            closeModal();
            toast('Payout recorded');
            // Refresh the awaiting-payout balance, but not over unsaved edits.
            if (!SettingsPage.isDirty()) App.navigate('#/settings');
        } catch (err) { toast(err.message, 'error'); }
    },

    async save(e) {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.target).entries());
        // Remove file input from data
        delete data.file;
        // An unchecked box is left out of FormData; send it as "false".
        const logoOption = e.target.querySelector('[name="invoice_show_logo"]');
        if (logoOption) data.invoice_show_logo = logoOption.checked ? 'true' : 'false';
        const classWarn = e.target.querySelector('[name="class_warn_blank"]');
        if (classWarn) data.class_warn_blank = classWarn.checked ? 'true' : 'false';
        // the transaction warnings' boxes (#481), on or off
        if (typeof e.target.querySelectorAll === 'function') {
            e.target.querySelectorAll('input[type="checkbox"][data-bool][name]')
                .forEach(box => { data[box.name] = box.checked ? 'true' : 'false'; });
        }
        // the hidden menu items, as a list (#334); the boxes are not form fields
        const menuBoxes = typeof e.target.querySelectorAll === 'function' ? [...e.target.querySelectorAll('input[data-show-menu]')] : [];
        if (menuBoxes.length) {
            const hidden = SettingsPage._hiddenMenuToSave(menuBoxes);
            data.hidden_menu = JSON.stringify(hidden);
            for (const key of Object.keys(data)) if (key.startsWith('show-menu:')) delete data[key];
        }
        // Deposit into these accounts (#735), as a list; the boxes are not form fields
        const depositBoxes = typeof e.target.querySelectorAll === 'function' ? [...e.target.querySelectorAll('input[data-deposit-account]')] : [];
        for (const key of Object.keys(data)) if (key.startsWith('deposit-account:')) delete data[key];
        if (depositBoxes.length || 'default_deposit_account_id' in data) {
            data.deposit_account_ids = JSON.stringify(SettingsPage._depositAccountsToSave(depositBoxes));
        }
        const btn = document.getElementById('settings-save-btn');
        if (btn) btn.disabled = true;
        try {
            const saved = await API.put('/settings', data);
            if (data.hidden_menu !== undefined) {
                try { SettingsPage._hiddenMenuStored = JSON.parse(data.hidden_menu); } catch (err) { /* keep */ }
            }
            SettingsPage._savedClosingDate = saved && typeof saved.closing_date === 'string'
                ? saved.closing_date : (data.closing_date || '');
            SettingsPage._markClean();
            SettingsPage.showClosingState();
            // the shell's copy too: a new company name shows in the status
            // bar, the title and the topbar now, not at the next start
            if (saved && typeof App !== 'undefined' && typeof App.showCompany === 'function') {
                App.showCompany(saved);
            }
            toast('Settings saved');
            // A newly chosen entity type reseeds the equity accounts only
            // while the chart is untouched and nothing has posted (#488):
            // say what happened either way.
            const equity = saved && saved.entity_equity;
            if (equity && equity.applied) toast(`${T('Equity')} accounts set for ${saved.entity.label}: ${equity.accounts.join(', ')}`);
            else if (equity && equity.note) toast(equity.note);
            // Clearing turned off with money still in the clearing account (#584)
            for (const w of (saved && saved.clearing_warnings) || []) toast(w, 'error');
            SettingsPage.loadUnreadableSecrets();
        } catch (err) {
            toast(err.message, 'error');
        } finally {
            if (btn) btn.disabled = false;
        }
    },

    // Closing date: say plainly whether one is set, and clear it in one
    // click — clearing used to mean emptying three date segments by hand,
    // which WebKit could leave half-empty and silently invalid.
    //
    // The state line describes the closing date the books HOLD, and then
    // any change typed but not saved yet. It used to describe the field,
    // so after a Clear that never reached the server it said "No closing
    // date: every period is open." while the books stayed closed (2.18.0
    // gate, macbase1 NEW-1).
    _savedClosingDate: '',

    _closingStateText(value) {
        return value
            ? `Closed through ${formatDate(value)}: changes dated on or before it are refused.`
            : 'No closing date: every period is open.';
    },

    // What Save Settings will change, or '' when the field matches the books.
    _closingPendingText(saved, typed, incomplete) {
        if (incomplete) return 'The date typed is not complete: finish it, or press Clear.';
        if (typed === saved) return '';
        if (!typed) return 'Save Settings to remove the closing date.';
        return saved
            ? `Save Settings to move it to ${formatDate(typed)}.`
            : `Save Settings to close the books through ${formatDate(typed)}.`;
    },

    showClosingState() {
        const input = document.getElementById('closing-date');
        const typed = input ? input.value : '';
        // a half-typed date reads as '' but is not empty: the form will not submit
        const incomplete = !!(input && input.validity && input.validity.badInput);
        const saved = SettingsPage._savedClosingDate || '';
        const state = document.getElementById('closing-date-state');
        if (state) {
            const pending = SettingsPage._closingPendingText(saved, typed, incomplete);
            state.textContent = SettingsPage._closingStateText(saved) + (pending ? ` ${pending}` : '');
        }
        const clear = document.getElementById('closing-date-clear');
        if (clear) clear.disabled = !typed && !incomplete;
    },

    // Save Settings was refused by the browser because the date is half
    // typed. WebKit shows an empty message and nothing else, so say it.
    closingDateInvalid() {
        SettingsPage.showClosingState();
        toast('Nothing was saved: the closing date is not complete. Finish it, or press Clear.', 'error');
    },

    clearClosingDate() {
        const input = document.getElementById('closing-date');
        if (!input) return;
        // `value = ''` alone left WebKit's date segments holding the old
        // date: the field was invalid with an empty message, so Save
        // Settings sent nothing, for this or any other setting, until a
        // reload (2.18.0 gate, macbase1 NEW-1). Changing the type rebuilds
        // the segments empty, which every engine accepts.
        input.type = 'text';
        input.value = '';
        input.type = 'date';
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
                const fallback = `Upload failed (HTTP ${resp.status}). The file may be too large or the server returned an unexpected response.`;
                // a 422 is a list of entries: API.errorMessage makes sentences of it
                const msg = data && data.detail ? API.errorMessage(data.detail, fallback) : fallback;
                throw new Error(msg);
            }
            toast('Logo uploaded');
            if (!SettingsPage.isDirty()) { App.navigate('#/settings'); return; }
            // Unsaved edits elsewhere on the page: show the new logo in place
            // rather than re-render the page over them.
            document.getElementById('company-logo-note')?.remove();
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

    async removeLogo() {
        if (!confirm('Remove the company logo? Documents print without one until you upload another.')) return;
        try {
            await API.del('/uploads/logo');
            toast('Logo removed');
            if (!SettingsPage.isDirty()) { App.navigate('#/settings'); return; }
            document.getElementById('company-logo-preview')?.remove();
            document.getElementById('company-logo-note')?.remove();
        } catch (err) { toast(err.message, 'error'); }
    },

    // Use TLS, with its warning beside it while it is No: shown in the open
    // (it is a warning, not teaching text), tied to the select so a screen
    // reader hears it on the control, and a polite live region so choosing
    // No is announced. The element is always there and holds the sentence
    // or nothing. It changes nothing about what is saved or how mail is sent.
    TLS_OFF_WARNING: 'With Use TLS off, your mail password and your mail are sent without encryption.',

    tlsIsOff(saved) {
        return saved != null && String(saved).toLowerCase() !== 'true';
    },

    _useTlsFieldHtml(s) {
        // off is whatever the mailer reads as off: any saved value but
        // "true" in any case ("False", "no" or "0" written through the API
        // send unencrypted just as "false" does). Never set: on.
        const off = SettingsPage.tlsIsOff(s.smtp_use_tls);
        return `<div class="form-group"><label>Use TLS</label>
                            <select name="smtp_use_tls" aria-describedby="smtp-tls-off-warning" onchange="SettingsPage.tlsChanged(this)">
                                <option value="true" ${off ? '' : 'selected'}>Yes</option>
                                <option value="false" ${off ? 'selected' : ''}>No</option>
                            </select>
                            <div id="smtp-tls-off-warning" role="status" style="color:var(--text-warning); font-size:11px; font-weight:600; margin-top:4px;">${off ? escapeHtml(SettingsPage.TLS_OFF_WARNING) : ''}</div></div>`;
    },

    tlsChanged(select) {
        const note = document.getElementById('smtp-tls-off-warning');
        if (note) note.textContent = select.value === 'false' ? SettingsPage.TLS_OFF_WARNING : '';
    },

    async testEmail() {
        try {
            const res = await API.post('/settings/test-email');
            // an Always CC or Always BCC address the server refused is said
            toast(res && res.note ? `Test email sent. ${res.note}` : 'Test email sent',
                res && res.note ? 'warning' : 'success');
        } catch (err) {
            // The failure now carries the reason; the full history is just
            // below, so point there and refresh it (#447).
            toast(err.message, 'error');
            const section = document.getElementById('settings-email-recent-sends');
            if (section) SettingsPage.showSetting(section, { focus: false });
        } finally {
            SettingsPage.loadEmailLog(true);
        }
    },

    // Legal entity type (#488) — a plain form field saved with Save Settings,
    // not its own switch like company type. The <select> for it and the LLC
    // "Taxed as" picker, the "Not set" default first.
    _ENTITY_TYPES: [
        ['sole_proprietor', 'Sole proprietor'],
        ['general_partnership', 'General partnership'],
        ['limited_partnership', 'Limited partnership'],
        ['llp', 'Limited liability partnership'],
        ['llc', 'LLC'],
        ['c_corp', 'C corporation'],
        ['s_corp', 'S corporation'],
    ],

    _LLC_ELECTIONS: [
        ['disregarded', 'A sole proprietor (single-member)'],
        ['partnership', 'A partnership'],
        ['s_corp', 'An S corporation'],
        ['c_corp', 'A C corporation'],
    ],

    _entityOptions(current) {
        const opts = [`<option value="" ${!current ? 'selected' : ''}>Not set</option>`];
        for (const [value, label] of SettingsPage._ENTITY_TYPES) {
            opts.push(`<option value="${value}" ${current === value ? 'selected' : ''}>${label}</option>`);
        }
        return opts.join('');
    },

    _llcElectionOptions(current) {
        const opts = [`<option value="" ${!current ? 'selected' : ''}>Not set</option>`];
        for (const [value, label] of SettingsPage._LLC_ELECTIONS) {
            opts.push(`<option value="${value}" ${current === value ? 'selected' : ''}>${label}</option>`);
        }
        return opts.join('');
    },

    // An S corporation's pay rule (#488 / #557), shown under the picker for
    // an S corp or an LLC taxed as one.
    S_CORP_NOTE: 'S corporation: an active shareholder-employee takes reasonable compensation as W-2 wages through Payroll before any distribution. Distributions post to Shareholder Distributions (equity), never to the profit and loss.',

    _isSCorp(s) {
        return s.entity_type === 's_corp' || (s.entity_type === 'llc' && s.llc_tax_election === 's_corp');
    },

    // The "Taxed as" picker only applies to an LLC: show it only then.
    _onEntityTypeChange(sel) {
        const group = document.getElementById('llc-election-group');
        if (group) group.hidden = sel.value !== 'llc';
        Hints.showEntry('llc-tax-election-help', sel.value === 'llc');
        SettingsPage._toggleSCorpNote();
        SettingsPage._updateDirty();
    },

    // The default markup belongs to Markup per line only (#774).
    _onEstimateStyleChange(sel) {
        const group = document.getElementById('estimate-default-markup-group');
        if (group) group.hidden = sel.value !== 'line_markup';
        Hints.showEntry('estimate-default-markup-help', sel.value === 'line_markup');
        SettingsPage._updateDirty();
    },

    _toggleSCorpNote() {
        const note = document.getElementById('s-corp-note');
        const type = document.getElementById('entity-type');
        const election = document.getElementById('llc-tax-election');
        if (!note || !type) return;
        note.hidden = !SettingsPage._isSCorp({
            entity_type: type.value,
            llc_tax_election: election ? election.value : '',
        });
    },

    // Company type saves on its own and reloads: the vocabulary and the
    // nonprofit nav items are applied at boot (App.applyTerminology), so
    // the whole shell has to come up again in the new words.
    async changeCompanyType(sel) {
        const value = sel.value;
        // Three types now, so the one the select held is remembered on it
        // rather than inferred (data-prev, set when the form rendered).
        const previous = sel.dataset.prev || 'business';
        // The switch reloads the page; other changes on it are saved with
        // the new type rather than dropped by the reload.
        const others = SettingsPage.isDirty('company_type');
        const prompts = {
            nonprofit: 'Switch this company to nonprofit mode? Screens will say donor, pledge, donation and fund; the net-asset accounts are added. Nothing in your data changes.',
            construction: 'Switch this company to construction mode? A subcontractor says "Sub"; the contractor accounts (retainage receivable and payable, customer deposits, use tax, construction in progress, equipment and other job costs) are added, and the standard division and section cost codes if you have none of your own yet. Nothing in your data changes.',
            business: 'Switch this company back to business mode? Screens return to customer, invoice, sales receipt and class. Nothing in your data changes.',
        };
        const msg = (prompts[value] || prompts.business)
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
            } else if (value === 'construction') {
                try { await API.post('/construction/setup', {}); }
                catch (e) { /* accounts and cost codes can be created later */ }
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

    // #476: the per-user "sound when a transaction posts" toggle. Stored in
    // the preferences API (post_sound), not the company-settings form, so it
    // follows a person across machines and each user keeps their own. The
    // checkbox saves itself on change, like the OCR engine above.
    // Every signed-in role writes its own preferences (#480), read-only
    // and accountant included, so every one of them gets the toggle.
    async loadPostSoundPref() {
        const box = $('#post-sound-pref');
        if (!box) return;
        try {
            const pref = await API.get('/preferences/post_sound');
            box.checked = !!(pref && pref.value && pref.value.enabled);
        } catch (e) { /* leave it off */ }
    },

    async savePostSound(on) {
        // The change event is a user gesture: make (or resume) the audio
        // context now, before the await, so WKWebView does not leave it
        // suspended — the later chime is no longer inside a gesture.
        if (typeof setPostSoundEnabled === 'function') setPostSoundEnabled(!!on);
        if (on && typeof primePostAudio === 'function') primePostAudio();
        try {
            await API.put('/preferences/post_sound', { value: { enabled: !!on } });
            toast(on ? 'Sound on when a transaction posts' : 'Sound off when a transaction posts');
            // the preview: what a posting will sound like
            if (on && typeof playPostSound === 'function') playPostSound();
        } catch (err) {
            if (typeof setPostSoundEnabled === 'function') setPostSoundEnabled(!on);
            const box = $('#post-sound-pref');
            if (box) box.checked = !on;
            toast(err.message, 'error');
        }
    },

    async loadOcrEnginePref() {
        const sel = $('#ocr-engine-pref');
        if (!sel) return;
        try {
            const settings = await API.get('/settings');
            if (settings.ocr_engine) sel.value = settings.ocr_engine;
        } catch (e) { /* leave the default selected */ }
    },

    // ---- Connect to AI assistant (#1087) --------------------------------
    // fallback: unused since NEW-269 (the relay name a Disconnect could not
    // delete comes from the server's status, st.pending)
    // error: a press's failure in the server's sentence, kept on the panel
    // until Dismiss or the next press (W-208: the 3-second refresh dropped
    // it); readError: the panel's own read failing, which a refresh clears
    // busy: the press being worked on ('connect', 'code', 'disconnect'): its
    // buttons stay off through every draw until it answers, so a timed
    // refresh never gives back a button whose press is still in flight
    // asked: counts the reads and the presses, so a read that was asked
    // before a press answered never draws over what the press made
    // goes: a refusal that says another press or a connection is in the way
    // ('linked' / 'unlinked': what the panel shows once that is over); the
    // sentence goes when the panel itself shows it, looked for a few times
    _claude: { status: null, code: null, error: '', readError: '', timer: null, fallback: null, busy: '', asked: 0, goes: '', looks: 0 },

    // The state in words: what the panel says, for any role.
    claudeStateWords(st) {
        const state = (st && st.state) || 'not_connected';
        if (state === 'connected') return 'Connected';
        if (state === 'connecting') return 'Connecting…';
        if (state === 'unreachable') return 'The relay cannot be reached';
        return 'Not connected';
    },

    // The code's life in words: ten minutes, and until when on this clock.
    claudeCodeLife(expires) {
        const until = expires ? new Date(expires) : null;
        const when = until && !isNaN(until.getTime())
            ? ` (until ${until.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })})`
            : '';
        return `It works once, for ten minutes${when}. Type it on the relay's sign-in page when Claude or Grok asks.`;
    },

    // The panel: words for everyone, an administrator's buttons (data-admin,
    // so the bookkeeper sweep knows them as the administrator's). `error` is
    // a refusal or failure to show (the server's own sentence).
    //
    // One connection can reach several companies. The server gives the list
    // of them (st.companies) only to someone who is an administrator of
    // every company on it; anyone else hears about this company alone. Each
    // button's name says which company it is for, so no two are alike.
    switchboardHtml(st, code, error, fallback, busy) {
        st = st || {};
        const admin = !!st.can_manage;
        // a press is being worked on: no second one until it answers
        const off = busy ? ' disabled' : '';
        const words = SettingsPage.claudeStateWords(st);
        const colour = st.state === 'connected' ? 'var(--text-success)'
            : (st.state === 'unreachable' ? 'var(--text-warning)' : 'var(--text-muted)');
        const list = Array.isArray(st.companies) && st.companies.length ? st.companies : null;
        const here = st.company || st.here_name || 'this company';
        // every block of the panel carries an id: a refresh replaces only the
        // blocks that changed (_claudeRedraw), found again by these
        let html = `<div id="claude-relay-status"><span style="color:var(--text-muted);">Status:</span> <strong id="claude-relay-state" style="color:${colour};">${escapeHtml(words)}</strong>`
            + (st.linked && st.company && st.this_company ? ` <span style="color:var(--text-muted);">&middot; Company: ${escapeHtml(st.company)}</span>` : '')
            + '</div>';
        if (st.sentence) html += `<div id="claude-relay-sentence" style="margin-top:4px;">${escapeHtml(st.sentence)}</div>`;
        if (error) {
            html += '<div id="claude-relay-error" role="alert" style="margin-top:4px;">'
                + `<div style="color:var(--text-danger);">${escapeHtml(error)}</div>`
                + '<div style="margin-top:6px;"><button type="button" class="btn btn-secondary btn-sm" id="claude-relay-error-dismiss" aria-label="Dismiss the relay message" onclick="SettingsPage.dismissClaudeError()">Dismiss</button></div>'
                + '</div>';
        }
        // Disconnect could not reach the relay: the relay name (in case support is
        // ever needed; SlowBooks Pro keeps trying the deletion itself),
        // kept by the server until the relay deletes the record (NEW-269:
        // it lived only in the page, lost on a restart); Dismiss hides it
        // while the quiet retry goes on
        const pending = (st.pending || []).filter(p => p && p.slug && !p.dismissed);
        if (!pending.length && fallback && fallback.slug) pending.push(fallback);
        const shown = pending[pending.length - 1];
        if (shown) {
            html += '<div id="claude-relay-fallback" role="alert" style="margin-top:8px;">'
                + `<div>${escapeHtml(shown.sentence || '')}</div>`
                + '<div style="display:grid; grid-template-columns:auto minmax(0, 1fr) auto; gap:6px 8px; align-items:center; margin-top:6px;">'
                // the same line as copyLineHtml draws, its button named for the
                // notice: while connected again the panel has a second Relay
                // name line, and no two buttons share a name
                + '<span style="font-size:12px; color:var(--text-muted);">Relay name</span>'
                + `<code id="claude-relay-fallback-name" style="font-family:var(--font-mono); font-size:12px; user-select:all; overflow-wrap:anywhere;">${escapeHtml(shown.slug || '')}</code>`
                + '<button type="button" class="btn btn-secondary btn-sm" id="claude-relay-fallback-copy" aria-label="Copy the relay name in the removal notice" onclick="SettingsPage.copyClaudeFallbackName()">Copy</button>'
                + '</div>'
                + (admin ? '<div style="margin-top:6px;"><button type="button" class="btn btn-secondary btn-sm" id="claude-relay-fallback-dismiss" aria-label="Dismiss the removal notice" onclick="SettingsPage.dismissClaudeFallback()">Dismiss</button></div>' : '')
                + '</div>';
        }
        // the companies on the connection, for someone who holds power over
        // all of them; each name in a block of its own, keyed by its file, so
        // a selection in one name stays when another's note changes
        if (list) html += SettingsPage.claudeCompaniesHtml(st, list);
        else if (st.linked && st.this_company && st.state !== 'damaged') {
            // on the connection, without power over the whole of it: this
            // company alone, never another company's name or how many
            html += `<div id="claude-relay-on-link" style="margin-top:4px;">${escapeHtml(SettingsPage.claudeOnLinkWords(st))}</div>`;
        }
        if (!admin) return html;
        if (!st.linked) {
            html += `<div id="claude-relay-connect-row" style="margin-top:8px;"><button type="button" class="btn btn-primary btn-sm" id="claude-connect" data-admin${off} onclick="SettingsPage.connectClaude()">${busy === 'connect' ? 'Connecting…' : 'Connect to AI assistant'}</button></div>`;
            return html;
        }
        if (st.can_add) {
            html += `<div id="claude-relay-add" style="margin-top:8px;">${escapeHtml(SettingsPage.claudeAddWords(here, st.next_label))}</div>`;
        }
        if (list && st.address) {
            html += '<div id="claude-relay-lines" style="display:grid; grid-template-columns:auto minmax(0, 1fr) auto; gap:6px 8px; align-items:center; margin-top:8px;">'
                + copyLineHtml('claude-relay-address', 'Connector address', st.address, 'SettingsPage.copyClaudeAddress()')
                + (st.slug ? copyLineHtml('claude-relay-name', 'Relay name', st.slug, 'SettingsPage.copyClaudeRelayName()') : '')
                + '</div>';
        }
        if (code && code.code && st.can_code) {
            html += '<div id="claude-relay-code-box" style="margin-top:8px;">'
                + `<div><span style="color:var(--text-muted);">Sign-in code:</span> <code id="claude-relay-code" style="font-family:var(--font-mono); font-size:14px; user-select:all;">${escapeHtml(code.code)}</code></div>`
                + `<div style="margin-top:2px;">${escapeHtml(SettingsPage.claudeCodeLife(code.expires))}</div>`
                + (list && list.length > 1 ? '<div id="claude-relay-code-reach" style="margin-top:2px;">Anyone who signs an assistant in with this code can work every company listed above.</div>' : '')
                + '</div>';
        }
        // a manager who is offered no code is told why: which companies it
        // waits for
        if (list && !st.can_code && Array.isArray(st.code_needs) && st.code_needs.length) {
            html += `<div id="claude-relay-code-needs" style="margin-top:4px; overflow-wrap:anywhere;">${escapeHtml(SettingsPage.claudeCodeNeedsWords(st.code_needs))}</div>`;
        }
        const presses = [];
        // a long company name with no spaces wraps inside its button
        const wraps = ' style="max-width:100%; white-space:normal; overflow-wrap:anywhere; text-align:left;"';
        if (st.can_add) {
            presses.push(`<button type="button" class="btn btn-primary btn-sm" id="claude-add" data-admin${off}${wraps} onclick="SettingsPage.addClaudeCompany()">${escapeHtml(busy === 'add' ? 'Adding…' : `Add ${here} to the AI connection`)}</button>`);
        }
        if (st.can_code) presses.push(`<button type="button" class="btn btn-secondary btn-sm" id="claude-code" data-admin${off} onclick="SettingsPage.claudeCode()">Show a sign-in code</button>`);
        // this company off the connection; with it alone on the connection,
        // that is what Disconnect does, so only Disconnect is offered
        if (st.can_take_off && st.this_company && !(list && list.length === 1)) {
            presses.push(`<button type="button" class="btn btn-secondary btn-sm" id="claude-take-off" data-admin${off}${wraps} onclick="SettingsPage.takeClaudeOff('')">${escapeHtml(`Take ${here} off the AI connection`)}</button>`);
        }
        // a company whose token is already turned off there: someone with
        // power over the connection only drops the dead entry
        if (list && list.length > 1) {
            for (const co of list) {
                if ((co.state !== 'refused' && co.state !== 'maker_gone') || co.file === SettingsPage._claudeHereFile(st, list)) continue;
                presses.push(`<button type="button" class="btn btn-secondary btn-sm" id="claude-take-off-${SettingsPage.claudeKey(co.file)}" data-admin${off}${wraps} data-file="${escapeHtml(co.file)}" onclick="SettingsPage.takeClaudeOff(this.dataset.file)">${escapeHtml(`Take ${co.name || co.file} off the AI connection`)}</button>`);
            }
        }
        if (st.can_disconnect) presses.push(`<button type="button" class="btn btn-secondary btn-sm" id="claude-disconnect" data-admin${off} onclick="SettingsPage.disconnectClaude()">Disconnect the AI assistant</button>`);
        if (presses.length) html += `<div id="claude-relay-buttons" style="margin-top:8px; display:flex; flex-wrap:wrap; gap:8px;">${presses.join('')}</div>`;
        return html;
    },

    // An id made from a company's file that no other file gives: letters
    // and digits as they are, anything else as its character code.
    claudeKey(file) {
        return String(file || '').replace(/[^A-Za-z0-9]/g, ch => '_' + ch.charCodeAt(0).toString(16) + '_');
    },

    // Which listed company is the one this panel is open in (none when it
    // is not on the connection).
    _claudeHereFile(st, list) {
        const mine = list.find(co => co && co.here === true);
        return mine ? mine.file : null;
    },

    // What a company's name carries in the list: this company, and why it
    // cannot answer now.
    claudeNote(state) {
        return ({
            not_open_here: 'not open on this computer',
            other_window: 'open in another window',
            not_served: 'not served by this server now',
            locked: 'locked',
            refused: 'turned off in that company',
            maker_gone: 'the person who added it is no longer an administrator there',
        })[state] || '';
    },

    claudeCompaniesHtml(st, list) {
        const mine = SettingsPage._claudeHereFile(st, list);
        const names = list.map(co => {
            const here = co.file === mine;
            // the company the panel is open in is "this company", not "that"
            const note = here && co.state === 'refused'
                ? 'turned off in this company'
                : (here && co.state === 'maker_gone'
                    ? 'the person who added it is no longer an administrator here'
                    : SettingsPage.claudeNote(co.state));
            const notes = [here ? 'this company' : '', note].filter(Boolean);
            const said = (co.name || co.file) + (notes.length ? ` (${notes.join('; ')})` : '');
            return `<span id="claude-relay-co-${SettingsPage.claudeKey(co.file)}">${escapeHtml(said)}</span>`;
        });
        const lead = st.state === 'connected' ? 'Connected. ' : '';
        let html = `<div id="claude-relay-companies" style="margin-top:4px; overflow-wrap:anywhere;">${lead}The AI assistant can work the books of: ${names.join(', ')}.</div>`;
        if (!st.server_edition && list.length > 1) {
            html += '<div id="claude-relay-desktop" style="margin-top:4px;">On this computer only the open company answers. When the assistant asks for another, open that company here.</div>';
        }
        return html;
    },

    claudeOnLinkWords(st) {
        const label = st.token_label || 'AI assistant (relay)';
        return `${st.company || 'This company'} is on this computer's AI assistant connection. The assistant works these books as a bookkeeper; every change is recorded here as token:${label}.`;
    },

    claudeAddWords(name, label) {
        return `Add ${name} to the AI assistant connection, and the assistant can work ${name}'s books as a bookkeeper. Every change it makes is recorded in ${name}'s audit log as token:${label || 'AI assistant (relay)'}.`;
    },

    claudeCodeNeedsWords(names) {
        return `A sign-in code reaches every company on the connection, so it is offered only while each of them can answer now and you are an administrator of each. Not now: ${names.join(', ')}.`;
    },

    // Where a selection's end is: the nearest element above it that has an
    // id (every block of the panel has one), and the steps down from there
    // to its node. Blocks coming and going around it do not move it.
    _claudePath(el, node) {
        const path = [];
        while (node && node !== el && !(node.nodeType === 1 && node.id)) {
            const parent = node.parentNode;
            if (!parent) return null;
            path.unshift(Array.prototype.indexOf.call(parent.childNodes, node));
            node = parent;
        }
        if (!node) return null;
        return { id: node === el ? '' : node.id, path };
    },

    _claudeNode(el, where) {
        let node = where.id ? document.getElementById(where.id) : el;
        if (node && node !== el && !el.contains(node)) return null;
        for (const i of where.path) {
            node = node && node.childNodes[i];
        }
        return node || null;
    },

    // What the person holds in the panel: the control with the keyboard and
    // the text selected.
    _claudeHeld(el) {
        const held = { focus: null, range: null, order: [] };
        if (typeof el.contains !== 'function') return held;
        const active = document.activeElement;
        if (active && active !== el && el.contains(active) && active.id) {
            held.focus = active.id;
            // the panel's controls in their order, to find a neighbor by
            // if this one is gone after the draw
            if (typeof el.querySelectorAll === 'function') {
                held.order = Array.from(el.querySelectorAll('button[id], a[href][id]')).map(n => n.id);
            }
        }
        const sel = typeof window !== 'undefined' && window.getSelection ? window.getSelection() : null;
        if (sel && sel.rangeCount && !sel.isCollapsed
            && el.contains(sel.anchorNode) && el.contains(sel.focusNode)) {
            const r = sel.getRangeAt(0);
            const from = SettingsPage._claudePath(el, r.startContainer);
            const to = SettingsPage._claudePath(el, r.endContainer);
            if (from && to) held.range = { from, to, start: r.startOffset, end: r.endOffset, text: String(sel) };
        }
        return held;
    },

    // Whether nothing on the page has the keyboard (a control that goes
    // while it has it leaves the keyboard on the page's body).
    _claudeAdrift() {
        const active = document.activeElement;
        return !active || active === document.body || active === document.documentElement
            || (typeof active.isConnected === 'boolean' && !active.isConnected);
    },

    // Put the keyboard on the control `id` or, when it is gone or off, on
    // its neighbor in the panel: the next control in the order they stood
    // in (`order`), else the one before, else one of the panel's own presses
    // (Connect, Add, Show a sign-in code, Take off, Disconnect), else any
    // control it has, else the panel itself. Never left on the page's body, where a
    // keyboard user would start again from the top of the page.
    _claudeFocusNear(el, id, order) {
        if (!el || typeof el.contains !== 'function') return;
        const usable = other => {
            const n = other ? document.getElementById(other) : null;
            return n && el.contains(n) && !n.disabled ? n : null;
        };
        const at = (order || []).indexOf(id);
        const after = at < 0 ? [] : order.slice(at + 1);
        const before = at < 0 ? [] : order.slice(0, at).reverse();
        let to = usable(id);
        for (const other of [...after, ...before, 'claude-connect', 'claude-add', 'claude-code', 'claude-take-off', 'claude-disconnect']) {
            if (to) break;
            to = usable(other);
        }
        if (!to && typeof el.querySelector === 'function') {
            to = el.querySelector('button:not([disabled]), a[href]');
        }
        if (!to) {
            // nothing to press is left (a person who is not an
            // administrator): the panel itself, so Tab goes on from here
            el.setAttribute('tabindex', '-1');
            to = el;
        }
        try { to.focus({ preventScroll: true }); } catch (e) { to.focus(); }
    },

    // Give back what a redraw took: the keyboard to the control with the
    // same id (to its neighbor when that control is gone), and the
    // selection where the same words are still there. What the redraw left
    // alone is left alone here too.
    _claudeGiveBack(el, held) {
        if (held.focus && !(document.activeElement && document.activeElement.id === held.focus)
            && SettingsPage._claudeAdrift()) {
            // a button that is there but off is the one whose press is being
            // worked on: the keyboard is not sent elsewhere meanwhile, the
            // press hands it back when it answers (_claudePress)
            const again = document.getElementById(held.focus);
            if (!(again && el.contains(again) && again.disabled)) {
                SettingsPage._claudeFocusNear(el, held.focus, held.order);
            }
        }
        if (held.range) {
            try {
                const sel = window.getSelection();
                if (sel && String(sel) === held.range.text && !sel.isCollapsed
                    && el.contains(sel.anchorNode) && el.contains(sel.focusNode)) return;
                const from = SettingsPage._claudeNode(el, held.range.from);
                const to = SettingsPage._claudeNode(el, held.range.to);
                if (from && to) {
                    const r = document.createRange();
                    r.setStart(from, held.range.start);
                    r.setEnd(to, held.range.end);
                    if (String(r) === held.range.text) {
                        sel.removeAllRanges();
                        sel.addRange(r);
                    }
                }
            } catch (e) { /* the words are gone: nothing to select */ }
        }
    },

    // Draw the panel anew, replacing only the blocks that changed: a block
    // that reads the same stays the very node it was, so a selection or the
    // keyboard inside it is never touched, and its alert is not announced
    // again. (The first draw, over "Checking…", is the whole panel.)
    _claudeRedraw(el, html) {
        const blocks = el.children ? Array.from(el.children) : null;
        const whole = !blocks || !blocks.length || typeof document.createElement !== 'function'
            || blocks.length !== el.childNodes.length || blocks.some(n => !n.id);
        if (whole) { el.innerHTML = html; return; }
        const next = document.createElement('div');
        next.innerHTML = html;
        const was = new Map(blocks.map(n => [n.id, n]));
        let at = el.firstChild;
        for (const block of Array.from(next.children)) {
            const same = block.id ? was.get(block.id) : null;
            if (same && same.parentNode === el && same.isEqualNode(block)) {
                // what stood between here and it is gone from the panel
                while (at && at !== same) { const gone = at; at = at.nextSibling; gone.remove(); }
                at = same.nextSibling;
            } else {
                el.insertBefore(block, at);
            }
        }
        while (at) { const gone = at; at = at.nextSibling; gone.remove(); }
    },

    _drawSwitchboard() {
        const el = $('#switchboard-relay');
        if (!el) return;
        const c = SettingsPage._claude;
        // a refusal that said something was in the way goes once the panel
        // shows it (connected, or no longer connected): it is no longer news
        // (but "a connection already exists; use Add" stays while the panel
        // offers Add: that is the press it points to)
        if (c.goes && c.error && c.status
            && (c.goes === 'linked') === !!c.status.linked
            && !(c.goes === 'linked' && c.status.can_add)) { c.error = ''; }
        if (!c.error) { c.goes = ''; c.looks = 0; }
        const html = SettingsPage.switchboardHtml(c.status, c.code, c.error || c.readError, c.fallback, c.busy);
        // A refresh that changes nothing draws nothing: the keyboard focus, a
        // selection (the relay name being copied) and the screen reader's
        // place stay where they are, and an alert is not announced again.
        // When something did change, only the blocks that changed are
        // replaced, and what the person held in one of those is given back.
        if (el._claudeDrawn !== html) {
            const held = SettingsPage._claudeHeld(el);
            SettingsPage._claudeRedraw(el, html);
            el._claudeDrawn = html;
            SettingsPage._claudeGiveBack(el, held);
        }
        // while the line is coming up or the relay is away, the words follow
        // it: asked again in a few seconds, while the panel is on the page
        const state = c.status && c.status.state;
        // and while a relay name waits on the quiet retry, now and then, so
        // the notice goes when the relay has deleted the record (NEW-269)
        const waiting = ((c.status && c.status.pending) || []).some(p => p && !p.dismissed);
        // while another press is in the way, the panel looks again for a
        // little longer than a press can take, so its sentence goes when
        // the other press is over
        const looking = !!(c.goes && c.error && c.looks < 6);
        if ((state === 'connecting' || state === 'unreachable' || waiting || looking) && !c.timer) {
            c.timer = setTimeout(() => {
                c.timer = null;
                if (looking) c.looks += 1;
                if (document.getElementById('switchboard-relay')) SettingsPage.loadSwitchboard();
            }, waiting && !looking && state !== 'connecting' && state !== 'unreachable' ? 15000 : 3000);
        }
    },

    async loadSwitchboard() {
        const el = $('#switchboard-relay');
        if (!el) return;
        const c = SettingsPage._claude;
        const asked = c.asked = (c.asked || 0) + 1;
        let status = null;
        let readError = '';
        try {
            status = await API.get('/switchboard/status');
        } catch (e) {
            readError = 'Could not read the AI assistant connection.';
        }
        // a press answered, or a later read was asked, meanwhile: this
        // answer is older than what the panel shows
        if (asked !== c.asked) return;
        c.status = status;
        c.readError = readError;
        SettingsPage._drawSwitchboard();
    },

    // One press of the panel at a time (the server's rule too): `kind` is
    // 'connect', 'add', 'code', 'take-off' or 'disconnect', `id` the button
    // pressed. While it is worked on the panel's buttons are off, through every refresh;
    // a second press does nothing. `work` sets what the panel shows next;
    // true from it has the panel read again. The keyboard comes back to the
    // button pressed, or to its neighbor when that button is gone.
    async _claudePress(kind, id, work) {
        const c = SettingsPage._claude;
        if (c.busy) return;
        c.busy = kind;
        // the next press clears the last one's sentence
        c.error = '';
        c.goes = '';
        c.looks = 0;
        const panel = $('#switchboard-relay');
        const had = !!(panel && typeof panel.contains === 'function'
            && document.activeElement && panel.contains(document.activeElement));
        SettingsPage._drawSwitchboard();
        let read = false;
        try {
            read = await work(c);
        } finally {
            c.busy = '';
        }
        // what a read asked before this answered is older than it
        c.asked = (c.asked || 0) + 1;
        if (read) await SettingsPage.loadSwitchboard();
        else SettingsPage._drawSwitchboard();
        if (had && SettingsPage._claudeAdrift()) SettingsPage._claudeFocusNear($('#switchboard-relay'), id, []);
    },

    // A refusal's sentence, kept on the panel. A 409 says something is in
    // the way of the press (another press, a connection already there):
    // `goes` is what the panel shows once that is so, and the sentence goes
    // with it.
    _claudeRefused(c, err, goes) {
        c.error = err.message;
        c.goes = err && err.status === 409 ? goes : '';
        c.looks = 0;
    },

    async connectClaude() {
        await SettingsPage._claudePress('connect', 'claude-connect', async c => {
            try {
                c.status = await API.post('/switchboard/connect');
                c.code = null;
                toast('The AI assistant is connected. Add the address in Claude or Grok, then show a sign-in code.');
                return false;
            } catch (err) {
                // nothing was made: the sentence stays, with Connect offered
                // again (the panel read again, so it says what is really there)
                SettingsPage._claudeRefused(c, err, 'linked');
                return true;
            }
        });
    },

    async claudeCode() {
        await SettingsPage._claudePress('code', 'claude-code', async c => {
            try {
                c.code = await API.post('/switchboard/code');
            } catch (err) {
                c.code = null;
                c.error = err.message;
            }
            return false;
        });
    },

    async copyClaudeAddress() {
        const st = SettingsPage._claude.status || {};
        await copyToClipboard(st.address || '', 'Address', $('#claude-relay-address'));
    },

    async copyClaudeRelayName() {
        const st = SettingsPage._claude.status || {};
        await copyToClipboard(st.slug || '', 'Relay name', $('#claude-relay-name'));
    },

    _claudeShownPending() {
        const c = SettingsPage._claude;
        const pending = ((c.status && c.status.pending) || []).filter(p => p && p.slug && !p.dismissed);
        return pending[pending.length - 1] || c.fallback || {};
    },

    async copyClaudeFallbackName() {
        const f = SettingsPage._claudeShownPending();
        await copyToClipboard(f.slug || '', 'Relay name', $('#claude-relay-fallback-name'));
    },

    dismissClaudeError() {
        SettingsPage._claude.error = '';
        SettingsPage._drawSwitchboard();
        SettingsPage._claudeFocusNear($('#switchboard-relay'), '', []);
    },

    async dismissClaudeFallback() {
        const f = SettingsPage._claudeShownPending();
        SettingsPage._claude.fallback = null;
        if (f.slug) {
            try { await API.post('/switchboard/dismiss', { slug: f.slug }); }
            catch (err) { SettingsPage._claude.error = err.message; }
        }
        await SettingsPage.loadSwitchboard();
    },

    // The companies the connection reaches, when this person may see them.
    _claudeList() {
        const st = SettingsPage._claude.status || {};
        return Array.isArray(st.companies) ? st.companies : [];
    },

    async addClaudeCompany() {
        const st = SettingsPage._claude.status || {};
        const name = st.here_name || 'this company';
        if (!confirm(`Add ${name}? Every assistant already signed in to this connection, and any signed in later, will be able to read and change ${name}'s books as a bookkeeper. You can take it off again here at any time.`)) return;
        await SettingsPage._claudePress('add', 'claude-add', async c => {
            try {
                c.status = await API.post('/switchboard/add');
                toast(`${name} is on the AI assistant connection.`);
                return false;
            } catch (err) {
                SettingsPage._claudeRefused(c, err, '');
                return true;
            }
        });
    },

    // Take a company off the connection: `file` is a listed company's file,
    // or '' for the company this panel is open in.
    async takeClaudeOff(file) {
        const st = SettingsPage._claude.status || {};
        const co = file ? SettingsPage._claudeList().find(e => e && e.file === file) : null;
        const target = file || st.here_file || '';
        const name = file ? ((co && (co.name || co.file)) || file) : (st.company || 'this company');
        if (!confirm(SettingsPage.claudeTakeOffQuestion(st, SettingsPage._claudeList(), file, name))) return;
        const id = file ? `claude-take-off-${SettingsPage.claudeKey(file)}` : 'claude-take-off';
        await SettingsPage._claudePress('take-off', id, async c => {
            try {
                const r = await API.post('/switchboard/take-off', { file: target });
                if (r && r.state === undefined && 'unlinked' in r) {
                    // it was the last company: that is a Disconnect
                    SettingsPage._claudeDisconnected(r);
                    return true;
                }
                c.status = r;
                const result = ((r && r.results) || [])[0] || {};
                toast(result.result === 'later'
                    ? `${name} is off the AI assistant connection. It is not open now; its token will be turned off the next time it is opened.`
                    : `${name} is off the AI assistant connection, and its token is turned off.`);
                return false;
            } catch (err) {
                SettingsPage._claudeRefused(c, err, '');
                return true;
            }
        });
    },

    // What Take off asks first. A company whose token is already off there
    // (a manager's button) is only removed from the connection; only someone
    // who sees the list is told that other companies stay connected.
    claudeTakeOffQuestion(st, list, file, name) {
        if (file) {
            const co = (list || []).find(e => e && e.file === file);
            if (co && co.state === 'maker_gone') {
                return `Take ${name} off the AI connection? The person who added it is no longer an administrator there, so that company refuses its token; this removes it from the connection and turns the token off. The other companies stay connected.`;
            }
            return `Take ${name} off the AI connection? Its token is already turned off in that company; this removes it from the connection. The other companies stay connected.`;
        }
        const label = (st || {}).token_label || 'AI assistant (relay)';
        const others = list && list.length > 1 ? ' The other companies stay connected.' : '';
        return `Take ${name} off? Its token, ${label}, is turned off at once and the assistant can no longer reach these books.${others}`;
    },

    // What Disconnect asks first: with several companies, every one of them
    // by name, and what becomes of one that is not open now.
    claudeDisconnectQuestion(st, list) {
        if (list.length > 1) {
            const all = list.length === 2 ? 'both companies' : `all ${list.length} companies`;
            const names = list.map(co => co.name || co.file).join(', ');
            return `Disconnect the AI assistant from ${all}? The relay forgets this connection and each company's AI assistant token is turned off: ${names}. A company that is not open now has its token turned off the next time it opens; until then it cannot be used, because its secret is deleted from this computer.`;
        }
        return 'Disconnect the AI assistant? The connection ends, the relay deletes its record of these books, and the token "'
            + ((st || {}).token_label || 'AI assistant (relay)') + '" is revoked.';
    },

    async disconnectClaude() {
        if (!confirm(SettingsPage.claudeDisconnectQuestion(SettingsPage._claude.status || {}, SettingsPage._claudeList()))) return;
        await SettingsPage._claudePress('disconnect', 'claude-disconnect', async c => {
            await SettingsPage._claudeDisconnect(c);
            return true;
        });
    },

    async _claudeDisconnect(c) {
        try {
            const r = await API.post('/switchboard/disconnect');
            SettingsPage._claudeDisconnected(r);
        } catch (err) {
            SettingsPage._claudeRefused(c, err, 'unlinked');
        }
    },

    // What a Disconnect did, on the panel and in a passing message.
    _claudeDisconnected(r) {
        SettingsPage._claude.fallback = null;
        const done = 'The AI assistant is disconnected' + (r && r.revoked ? ' and the token revoked. ' : '. ');
        // companies not open now: their tokens are turned off when they open
        const later = ((r && r.results) || []).filter(x => x && x.result === 'later').map(x => x.name || x.file);
        if (later.length && r && (r.unlinked || r.pending)) {
            // the relay was told (or its name is in the notice below): what
            // is left to say is which companies are not open now
            SettingsPage._claude.error = 'Disconnected.' + later.map(name => ` ${name} is not open now; its token will be turned off the next time it is opened.`).join('');
            toast('The AI assistant is disconnected. See the message on the panel.');
        } else if (r && r.say && r.sentence) {
            // something the relay could not do, with no notice kept for
            // it: the server's sentence stays on the panel until dismissed
            SettingsPage._claude.error = r.sentence;
            toast('The AI assistant is disconnected. See the message on the panel.');
        } else if (r && r.unlinked === false && r.slug) {
            // the relay still holds its record: the relay name is on the
            // panel, from the server's status (NEW-269), and the toast
            // says why, as the relay answered
            toast(done + (r.why === 'refused'
                ? 'The relay did not accept the request to delete its record: see the relay name below.'
                : (r.why === 'unconfirmed'
                    ? 'The relay did not confirm that it deleted its record: see the relay name below.'
                    : 'The relay was out of reach: see the relay name below.')));
        } else {
            toast(r && r.deleted
                ? 'The AI assistant is disconnected, and the relay deleted its record of these books.'
                : 'The AI assistant is disconnected.');
        }
        SettingsPage._claude.code = null;
    },

    async loadOcrStatus() {
        const el = $('#ocr-status');
        if (!el) return;
        try {
            const s = await API.get('/ocr/status');
            if (s.available) {
                Hints.showEntry('ocr-status-help', false);
                const langs = (s.languages || []).join(', ') || '—';
                const engineNames = { tesseract: 'Tesseract OCR', vision: 'Apple Vision (built into macOS)', winrt: 'Windows OCR (built into Windows)' };
                const engineLabel = engineNames[s.engine] || 'OCR engine';
                const pdfNames = { windows: 'built into Windows', macos: 'built into macOS', poppler: 'via poppler-utils' };
                const pdfNote = s.pdf
                    ? ` &middot; PDFs: ${escapeHtml(pdfNames[s.pdf] || s.pdf)}`
                    : '<div style="font-size:11px; color:var(--text-warning); margin-top:4px;">PDF scanning is not available on this machine (images still scan). '
                      + 'Linux: <code>sudo apt-get install poppler-utils</code>; other platforms: <code>brew install poppler</code> / poppler for Windows on PATH.</div>';
                el.innerHTML = `<strong style="color:var(--text-success);">${escapeHtml(engineLabel)} is ready</strong>`
                    + (s.version ? ` <span style="color:var(--text-muted);">(${escapeHtml(s.version)})</span>` : '')
                    + ` &middot; languages: ${escapeHtml(langs)}`
                    + pdfNote;
            } else {
                // why, and what enables it, is in the Receipt Scanning note (#977)
                Hints.showEntry('ocr-status-help', true);
                el.innerHTML = '<strong style="color:var(--text-warning);">No OCR engine is available — scanning is disabled.</strong>'
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
                SettingsPage._showSection(el.closest ? el.closest('.settings-section') : null, true);
                return;
            }
            el.innerHTML = `<div class="table-container"><table>
                <thead><tr><th scope="col">Filename</th><th scope="col">Size</th><th scope="col">Created</th><th scope="col">Actions</th></tr></thead>
                <tbody>${backups.map(b => `<tr>
                    <td>${escapeHtml(b.filename)}${b.backup_type === 'pre-restore'
                        ? ' <span style="font-size:10px; color:var(--text-muted);">(taken before a restore)</span>' : ''}${b.encrypted === false && SettingsPage._companyFile && SettingsPage._companyFile.encrypted
                        ? ' <span style="font-size:10px; color:var(--danger);">(plain SQLite, made before encryption: readable with no key)</span>' : ''}</td>
                    <td>${(b.file_size / 1024).toFixed(1)} KB</td>
                    <td>${escapeHtml(SettingsPage._when(b.created_at))}</td>
                    <td class="actions">
                        ${SettingsPage._isAdmin() ? `<a href="/api/backups/download/${encodeURIComponent(b.filename)}" class="btn btn-sm btn-secondary" data-admin download>Download</a>` : ''}
                        <button type="button" class="btn btn-sm btn-secondary" data-admin data-write data-filename="${escapeHtml(b.filename)}"
                            onclick="SettingsPage.confirmRestore(this.dataset.filename)">Restore…</button>
                    </td>
                </tr>`).join('')}</tbody>
            </table></div>`;
            SettingsPage._showSection(el.closest ? el.closest('.settings-section') : null, true);
        } catch (e) { /* ignore */ }
    },

    // ------------------------------------------------------------------
    // Files from earlier versions. Before 2.18 every company on an install
    // kept its logo, attachments and employee documents in one shared
    // folder; each company has its own copies now, but the old ones (W-4s
    // and I-9s among them) are still there. Shown to an administrator only
    // while the folder holds files. Removing them waits until every company
    // has been opened since the update: that is when each copies its own in.
    // ------------------------------------------------------------------
    _andList(names) {
        return names.length <= 2 ? names.join(' and ')
            : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
    },

    async loadLegacyFiles() {
        const section = $('#settings-legacy-files');
        if (!section || !SettingsPage._isAdmin()) return;
        let state;
        try {
            state = await API.get('/uploads/legacy');
        } catch (e) { return; }  // an older server, or one that refuses this role: stays hidden
        SettingsPage._legacyFiles = state;
        if (!state || !(state.files > 0)) {
            section.innerHTML = '';
            SettingsPage._showSection(section, false);
            return;
        }
        const pending = state.pending_companies || [];
        const count = `${state.files} ${state.files === 1 ? 'file' : 'files'} (${formatFileSize(state.bytes)})`;
        // The server says what to do about each one (#769: books from before
        // 2.18.0 are opened once with 2.31.14, which this version cannot do).
        let wait = state.pending_message || '';
        if (!wait && pending.length) {
            wait = `${SettingsPage._andList(pending)} still ${pending.length === 1 ? 'needs' : 'need'} to copy`
                + ' files from this folder; remove them once that is done.';
        }
        section.innerHTML = `
            <div class="settings-h-row"><h3>Files from earlier versions</h3>${hintList('Files from earlier versions', [
                { id: 'legacy-files-help', text: `Before version 2.18, every company here kept its logo, attachments and employee documents in one
                shared folder. Each company now keeps its own copies inside its company file, so nothing uses that
                folder any more, but it still holds ${escapeHtml(count)}. A document deleted since then still has
                its old copy there.` },
            ])}</div>
            ${wait ? `<div id="legacy-files-pending" style="font-size:11px; margin-bottom:8px;">${escapeHtml(wait)}</div>` : ''}
            <button type="button" class="btn btn-sm btn-secondary" id="legacy-files-remove" aria-describedby="legacy-files-help" data-write
                ${pending.length ? 'disabled' : ''} onclick="SettingsPage.removeLegacyFiles()">Remove them</button>`;
        SettingsPage._showSection(section, true);
    },

    async removeLegacyFiles() {
        const n = (SettingsPage._legacyFiles && SettingsPage._legacyFiles.files) || 0;
        if (!confirm(`Delete the ${n} ${n === 1 ? 'file' : 'files'} in the folder earlier versions shared between companies? `
            + 'Each company keeps its own copies inside its company file, so nothing in your books changes. '
            + 'This cannot be undone: a backup made before version 2.18, restored later, would come back '
            + 'without its logo, attachments and employee documents.')) return;
        try {
            const result = await API.del('/uploads/legacy');
            toast(`Removed ${result.removed} ${result.removed === 1 ? 'file' : 'files'} from the shared folder`);
        } catch (err) { toast(err.message, 'error'); }
        await SettingsPage.loadLegacyFiles();
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
    async confirmRestore(filename) {
        const b = (SettingsPage._backups || []).find(x => x.filename === filename) || { filename };
        const when = b.created_at ? SettingsPage._when(b.created_at) : 'when it was made';
        // The company as it is now. The shell's copy of the settings is read
        // when the window opens, so after a rename this named the company by
        // its old name (2.18.0 gate, skytech N4).
        let company = (App.settings && App.settings.company_name) || '';
        try {
            const now = await API.get('/settings');
            if (now && now.company_name) company = now.company_name;
        } catch (e) { /* the shell's copy will do */ }
        company = company || 'this company';
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
        const body = { filename };
        const send = () => API.post('/backups/restore', body);
        try {
            let result;
            // Another company's backup: say whose, and ask a second time. A
            // backup encrypted under another key (#290): ask for the
            // recovery key it was made under, and send again with it.
            for (;;) {
                try {
                    result = await send();
                    break;
                } catch (err) {
                    if (err.status === 409 && err.detail && err.detail.code === 'other_company') {
                        if (!confirm(`${err.message}\n\nRestore it over this company anyway?`)) {
                            btn.disabled = false;
                            btn.textContent = label;
                            return;
                        }
                        body.allow_other_company = true;
                        continue;
                    }
                    if (err.status === 409 && err.detail && (err.detail.code === 'other_key' || err.detail.code === 'wrong_recovery_key')) {
                        const typed = prompt(`${err.message}\n\nRecovery key:`, '');
                        if (!typed) {
                            btn.disabled = false;
                            btn.textContent = label;
                            return;
                        }
                        body.recovery_key = typed.trim();
                        continue;
                    }
                    throw err;
                }
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
            // Edit is type="button": the list is inside the Settings form, where
            // a button with no type submits it, so opening a template also saved
            // every setting on the page (and told a bookkeeper "Your role
            // doesn't allow this action").
            el.innerHTML = `<div class="table-container"><table>
                <thead><tr><th scope="col">Name</th><th scope="col">Type</th><th scope="col">Subject</th><th scope="col">Actions</th></tr></thead>
                <tbody>${templates.map(t => `<tr>
                    <td><strong>${escapeHtml(t.name)}</strong></td>
                    <td>${escapeHtml(t.template_type)}</td>
                    <td style="font-size:11px;">${escapeHtml(t.subject_template)}</td>
                    <td class="actions">
                        <button type="button" class="btn btn-sm btn-secondary" data-readonly-ok onclick="SettingsPage.editTemplate(${t.id})">Edit</button>
                    </td>
                </tr>`).join('')}</tbody>
            </table></div>`;
        } catch (e) { /* ignore */ }
    },

    async editTemplate(id) {
        let t, invoices = [], estimates = [];
        try {
            t = await API.get(`/email-templates/${id}`);
            if (t.template_type === 'invoice') {
                // Enough to pick one to preview against; the endpoint would
                // otherwise return up to 500, each with its lines.
                invoices = await API.get('/invoices?is_sales_receipt=false&limit=50');
            } else if (t.template_type === 'estimate') {
                estimates = await API.get('/estimates?limit=50');
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
                    <div class="form-group full-width"><label>Body Template (HTML + Jinja2) ${SettingsPage._hintWithId(SettingsPage._templateVariablesHelp(t), 'Body Template', 'email-template-variables-help')}</label>
                        <textarea name="body_template" rows="10" aria-describedby="email-template-variables-help" style="font-family:monospace; font-size:11px;">${escapeHtml(t.body_template)}</textarea></div>
                </div>
                ${t.template_type === 'invoice' ? `<div style="margin-top:12px;">
                    <label>Preview against ${T('invoice')}
                        <select id="email-template-invoice">${invoices.map(inv => `<option value="${inv.id}">#${escapeHtml(inv.invoice_number)} — ${escapeHtml(inv.customer_name || '')}</option>`).join('')}</select></label>
                    <button type="button" class="btn btn-secondary" aria-describedby="email-template-preview-help" onclick="SettingsPage.previewTemplate()" ${invoices.length ? '' : 'disabled'}>Preview</button>
                    ${SettingsPage._hintWithId('Renders what you have typed, without saving or sending.', 'Preview', 'email-template-preview-help')}
                    <div id="email-template-preview"></div>
                </div>` : ''}
                ${t.template_type === 'estimate' ? `<div style="margin-top:12px;">
                    <label>Preview against estimate
                        <select id="email-template-estimate">${estimates.map(est => `<option value="${est.id}">#${escapeHtml(est.estimate_number)} — ${escapeHtml(est.customer_name || '')}</option>`).join('')}</select></label>
                    <button type="button" class="btn btn-secondary"${estimates.length ? ' aria-describedby="email-template-preview-help"' : ''} onclick="SettingsPage.previewTemplate()" ${estimates.length ? '' : 'disabled'}>Preview</button>
                    ${estimates.length ? SettingsPage._hintWithId('Renders what you have typed, without saving or sending.', 'Preview', 'email-template-preview-help') : '<p style="font-size:11px; color:var(--text-muted);">There is no estimate to preview against yet.</p>'}
                    <div id="email-template-preview"></div>
                </div>` : ''}
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save Template</button>
                </div>
            </form>`);
    },

    // What a template can use (#977: behind the Body Template's tortoise).
    _templateVariablesHelp(t) {
        return `${t.template_type !== 'estimate' ? `Variables: {{ invoice.invoice_number }}, {{ invoice.total }}, {{ invoice.due_date }}, {{ customer_name }},
                    {{ company.company_name }}, {{ company.display_name }}, {{ doc_label }}, {{ pay_url }}. Filters: | currency, | fdate
                    <br><span>{{ doc_label }} reads &ldquo;Invoice&rdquo; or &ldquo;Sales Receipt&rdquo; to match the document.
                    {{ pay_url }} is only set when a payment provider is enabled &mdash; guard it with {% if pay_url %}.</span>` : `For an estimate: {{ estimate.estimate_number }}, {{ estimate.date }}, {{ estimate.expiration_date }}, {{ estimate.total }}, {{ customer_name }},
                    {{ company.company_name }}, {{ company.display_name }}, {{ doc_label }}. Filters: | currency, | fdate
                    <br><span>{{ estimate.expiration_date }} can be empty &mdash; guard it with {% if estimate.expiration_date %}. The Message typed in the send dialog goes above the template.</span>`}`;
    },

    async previewTemplate() {
        const form = document.getElementById('email-template-editor');
        const invoicePicker = document.getElementById('email-template-invoice');
        const estimatePicker = document.getElementById('email-template-estimate');
        const picker = invoicePicker || estimatePicker;
        const out = document.getElementById('email-template-preview');
        if (!form || !picker || !out) return;
        out.innerHTML = '';
        const against = invoicePicker
            ? { invoice_id: Number(invoicePicker.value) }
            : { estimate_id: Number(estimatePicker.value), template_name: form.elements.name.value, template_type: 'estimate' };
        try {
            const p = await API.post('/email-templates/preview', {
                ...against,
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
                lines.push(`<strong>${escapeHtml(c.name)} is not set for this ${(invoicePicker ? T('Invoice') : 'Estimate').toLowerCase()}:</strong>
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

    // ----- Invoice messages (#392) + giving-statement messages (#415) -----
    // The saved notes a document can choose from, one of them the default.
    // The same UI drives two lists, told apart by kind: 'invoice' fills an
    // invoice's Notes; 'giving_statement' opens the year-end giving statement.
    // Writes are an administrator's (the server refuses the rest), so every
    // control here is data-admin data-write.

    _messages: [],
    _givingMessages: [],

    // Per-kind details so one set of handlers serves both lists.
    _msgConfig(kind) {
        if (kind === 'giving_statement') {
            return {
                listId: '#giving-message-list',
                cache: 'giving statement',
                title: 'Opening Message',
                defaultLabel: 'Default: opens the giving statement',
                keptNote: 'Statements already printed keep it.',
            };
        }
        return {
            listId: '#invoice-message-list',
            cache: 'invoice',
            title: `${T('Invoice')} Message`,
            defaultLabel: `Default: fills Notes on a new ${T('invoice')}`,
            keptNote: `${T('Invoices')} that already carry its text keep it.`,
        };
    },

    _msgCache(kind, set) {
        if (kind === 'giving_statement') {
            if (set !== undefined) SettingsPage._givingMessages = set;
            return SettingsPage._givingMessages;
        }
        if (set !== undefined) SettingsPage._messages = set;
        return SettingsPage._messages;
    },

    // Switching the automatic run on: show who gets an email on the first run
    // before the setting is saved (review of #654). Turning rules on for a
    // company with old open invoices sends the firmest reminder on each one.
    async previewAutoReminders(select) {
        if (!select || select.value !== 'true') return;
        const back = () => { select.value = 'false'; SettingsPage._updateDirty(); };
        let summary;
        try { summary = await CollectionsPage.dueSummary(); } catch (err) { toast(err.message, 'error'); return; }
        const n = summary.due.items.length;
        if (!n) { toast('Nothing is due today. Once saved, the first automatic run sends whatever is due when SlowBooks Pro next opens.', 'info'); return; }
        openModal('Reminders the first run will send', `
            <p>The first automatic run, the next time SlowBooks Pro opens after you save, emails <strong>${n}</strong> reminder${n === 1 ? '' : 's'}:</p>
            ${summary.html}
            <p style="font-size:11px; color:var(--text-muted);">To hold any of them back, turn reminders off on that ${T('customer').toLowerCase()} first, or send them yourself from Collections and review the result.</p>
            <div class="form-actions">
                <button type="button" class="btn btn-secondary" onclick="closeModal()">Leave it off</button>
                <button type="button" class="btn btn-primary" onclick="closeModal({ chosen: true })">Keep it on</button>
            </div>`, {
            // Only "Keep it on" keeps it on: ×, Escape, Leave it off and a page
            // change all put the switch back (W-131, the 2.31.10 gate).
            onClose: (kept) => { if (!kept) back(); },
        });
        SettingsPage._autoPreviewCancel = back;
    },
    _autoPreviewCancel() {},

    // Reminder rules (#653). The rows sit inside the Settings form, so the
    // controls carry no name (nothing here is sent to PUT /api/settings) and
    // Enter is held back in the single-line boxes. Writes are an
    // administrator's: every control is data-admin data-write.
    _rules: [],

    async loadReminderRules() {
        const el = $('#reminder-rules-list');
        if (!el) return;
        // A stubbed API (the admin-controls probe) may answer with no list
        try { const got = await API.get('/collections/rules'); SettingsPage._rules = Array.isArray(got) ? got : []; } catch (e) {
            el.innerHTML = `<div style="font-size:11px; color:var(--text-muted);">${escapeHtml(e.message)}</div>`;
            return;
        }
        const rows = SettingsPage._rules.map(r => SettingsPage._ruleRowHtml(r)).join('');
        el.innerHTML = `<div class="table-container"><table>
            <thead><tr><th scope="col">Days from due</th><th scope="col">Enabled</th><th scope="col">Subject</th><th scope="col">Body</th><th scope="col">Actions</th></tr></thead>
            <tbody>${rows}${SettingsPage._ruleRowHtml(null)}</tbody></table></div>`;
    },

    // One editable row; `r` null is the blank Add rule row
    _ruleRowHtml(r) {
        const id = r ? r.id : 'new';
        const f = (part) => `id="rule-${part}-${id}"`;
        const guard = `onkeydown="if (event.key === 'Enter') event.preventDefault()"`;
        return `<tr data-rule="${id}">
            <td><input type="number" step="1" style="width:70px" ${f('days')} ${guard} data-admin data-write aria-label="Days from due"
                value="${r ? r.days_from_due : ''}" placeholder="e.g. 7">
                ${r ? `<div style="font-size:10px; color:var(--text-muted);">${escapeHtml(r.label)}</div>` : ''}</td>
            <td><input type="checkbox" ${f('enabled')} data-admin data-write aria-label="Enabled" ${!r || r.enabled ? 'checked' : ''}></td>
            <td><input type="text" maxlength="500" style="width:100%" ${f('subject')} ${guard} data-admin data-write aria-label="Subject" value="${escapeHtml(r ? r.subject : '')}"></td>
            <td><textarea rows="4" style="width:100%" ${f('body')} data-admin data-write aria-label="Body">${escapeHtml(r ? r.body : '')}</textarea></td>
            <td class="actions">
                <button type="button" class="btn btn-sm btn-primary" data-admin data-write onclick="SettingsPage.saveReminderRule(${r ? r.id : 'null'})">${r ? 'Save' : 'Add rule'}</button>
                ${r ? `<button type="button" class="btn btn-sm btn-secondary" data-destructive data-admin data-write onclick="SettingsPage.deleteReminderRule(${r.id})">Delete</button>` : ''}
            </td></tr>`;
    },

    async saveReminderRule(id) {
        const key = id === null ? 'new' : id;
        const days = $(`#rule-days-${key}`).value;
        if (days === '' || !Number.isInteger(Number(days))) { toast('Enter the days from due as a whole number', 'error'); return; }
        const body = {
            days_from_due: parseInt(days, 10),
            enabled: $(`#rule-enabled-${key}`).checked,
            subject: $(`#rule-subject-${key}`).value,
            body: $(`#rule-body-${key}`).value,
        };
        try {
            if (id === null) await API.post('/collections/rules', body);
            else await API.put(`/collections/rules/${id}`, body);
            toast('Reminder rule saved');
            SettingsPage.loadReminderRules();
        } catch (err) { toast(err.message, 'error'); }
    },

    async deleteReminderRule(id) {
        if (!confirm('Delete this reminder rule?')) return;
        try {
            await API.del(`/collections/rules/${id}`);
            toast('Reminder rule deleted');
            SettingsPage.loadReminderRules();
        } catch (err) { toast(err.message, 'error'); }
    },

    async loadInvoiceMessages() {
        // kind via arguments so the no-arg call (invoices) reads unchanged
        const kind = arguments.length ? arguments[0] : 'invoice';
        const cfg = SettingsPage._msgConfig(kind);
        const el = $(cfg.listId);
        if (!el) return;
        let messages;
        try { messages = await API.get(`/invoice-messages?kind=${kind}`); } catch (e) { return; }
        if (!Array.isArray(messages)) return;
        SettingsPage._msgCache(kind, messages);
        if (messages.length === 0) {
            el.innerHTML = `<div style="font-size:11px; color:var(--text-muted);">No saved messages yet.</div>`;
            return;
        }
        // The Default radios have no name: the list sits inside the Settings
        // form, and a named radio in it would be sent to PUT /api/settings as
        // a setting (and count as an edit). The handler unchecks the rest.
        el.innerHTML = `<div class="table-container"><table>
            <thead><tr><th scope="col">Default</th><th scope="col">Name</th><th scope="col">Message</th><th scope="col">Actions</th></tr></thead>
            <tbody>${messages.map(m => `<tr>
                <td><input type="radio" class="msg-default" aria-label="Default: ${escapeHtml(m.name)}"
                    data-admin data-write ${m.is_default ? 'checked' : ''} onchange="SettingsPage.setDefaultInvoiceMessage(${m.id}, this, '${kind}')"></td>
                <td><strong>${escapeHtml(m.name)}</strong></td>
                <td style="font-size:11px; white-space:pre-wrap;">${escapeHtml(m.body)}</td>
                <td class="actions">
                    <button type="button" class="btn btn-sm btn-secondary" data-admin data-write onclick="SettingsPage.editInvoiceMessage(${m.id}, '${kind}')">Edit</button>
                    <button type="button" class="btn btn-sm btn-secondary" data-destructive data-admin data-write onclick="SettingsPage.deleteInvoiceMessage(${m.id}, '${kind}')">Delete</button>
                </td>
            </tr>`).join('')}</tbody>
        </table></div>`;
    },

    editInvoiceMessage(id = null, kind = 'invoice') {
        const cfg = SettingsPage._msgConfig(kind);
        const m = id ? SettingsPage._msgCache(kind).find(x => x.id === id) : null;
        const cap = kind === 'giving_statement' ? ' maxlength="1500"' : '';
        openModal(`${m ? 'Edit' : 'New'} ${cfg.title}`, `
            <form id="invoice-message-editor" onsubmit="SettingsPage.saveInvoiceMessage(event, ${id}, '${kind}')">
                <div class="form-grid">
                    <div class="form-group full-width"><label>Name</label>
                        <input name="name" required maxlength="100" value="${escapeHtml(m ? m.name : '')}" placeholder="e.g. Pay by Zelle or card"></div>
                    <div class="form-group full-width"><label>Message</label>
                        <textarea name="body" rows="5" required${cap}>${escapeHtml(m ? m.body : '')}</textarea></div>
                    <div class="form-group full-width"><label style="display:flex; align-items:center; gap:6px;">
                        <input type="checkbox" name="is_default" ${m && m.is_default ? 'checked' : ''}> ${cfg.defaultLabel}</label></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save Message</button>
                </div>
            </form>`);
    },

    async saveInvoiceMessage(e, id, kind = 'invoice') {
        e.preventDefault();
        const form = e.target;
        const data = {
            name: form.name.value.trim(),
            body: form.body.value.trim(),
            kind,
            is_default: form.is_default.checked,
        };
        try {
            if (id) await API.put(`/invoice-messages/${id}`, data);
            else await API.post('/invoice-messages', data);
            toast('Message saved');
            closeModal();
            SettingsPage.loadInvoiceMessages(kind);
        } catch (err) { toast(err.message, 'error'); }
    },

    async setDefaultInvoiceMessage(id, radio, kind = 'invoice') {
        const cfg = SettingsPage._msgConfig(kind);
        if (radio) document.querySelectorAll(`${cfg.listId} .msg-default`).forEach(r => { if (r !== radio) r.checked = false; });
        try {
            await API.put(`/invoice-messages/${id}`, { is_default: true });
            toast('Default message set');
        } catch (err) { toast(err.message, 'error'); }
        SettingsPage.loadInvoiceMessages(kind);
    },

    async deleteInvoiceMessage(id, kind = 'invoice') {
        const cfg = SettingsPage._msgConfig(kind);
        const m = SettingsPage._msgCache(kind).find(x => x.id === id);
        const name = m ? m.name : 'this message';
        if (!confirm(`Delete the message "${name}"? ${cfg.keptNote}`)) return;
        try {
            await API.del(`/invoice-messages/${id}`);
            toast('Message deleted');
            SettingsPage.loadInvoiceMessages(kind);
        } catch (err) { toast(err.message, 'error'); }
    },

    // ==== Payment Terms (#664) ====
    async loadPaymentTerms() {
        const el = $('#payment-term-list');
        if (!el) return;
        let terms;
        try { terms = await API.get('/payment-terms?include_inactive=true'); } catch (e) { return; }
        if (!Array.isArray(terms)) return;
        SettingsPage._paymentTerms = terms;
        el.innerHTML = `<div class="table-container"><table>
            <thead><tr><th scope="col">Name</th><th scope="col">Rule</th>
                <th scope="col" class="amount" title="${T('Invoices')} and bills saved under it; their rule stays">${T('Invoices')} &amp; bills</th>
                <th scope="col" class="amount" title="${T('Customers')}, vendors, recurring ${T('invoices')}, memorized bills and Default Terms that carry it: a change of its rule reaches their next documents">${T('Customers')}, vendors &amp; schedules</th>
                <th scope="col">Actions</th></tr></thead>
            <tbody>${terms.map(t => `<tr${t.is_active ? '' : ' style="color:var(--text-muted);"'}>
                <td><strong>${escapeHtml(t.name)}</strong>${t.is_active ? '' : ' (inactive)'}</td>
                <td style="font-size:11px;">${escapeHtml(t.description)}</td>
                <td class="amount">${t.documents}</td>
                <td class="amount">${t.elsewhere}</td>
                <td class="actions">
                    <button type="button" class="btn btn-sm btn-secondary" data-admin data-write aria-label="Edit ${escapeHtml(t.name)}" onclick="SettingsPage.editPaymentTerm(${t.id})">Edit</button>
                    <button type="button" class="btn btn-sm btn-secondary" data-admin data-write aria-label="Make ${escapeHtml(t.name)} ${t.is_active ? 'inactive' : 'active'}" onclick="SettingsPage.setPaymentTermActive(${t.id}, ${!t.is_active})">${t.is_active ? 'Make Inactive' : 'Make Active'}</button>
                </td>
            </tr>`).join('')}</tbody>
        </table></div>`;
    },

    editPaymentTerm(id = null) {
        const t = id ? (SettingsPage._paymentTerms || []).find(x => x.id === id) : null;
        const v = t || { name: '', kind: 'standard', net_days: 30, due_day: '', cutoff_days: 0, discount_percent: '', discount_days: '', discount_day: '' };
        const val = x => (x === null || x === undefined ? '' : escapeHtml(String(x)));
        const locked = t && t.documents > 0
            ? `<p class="hint" style="margin:0 0 8px;">${escapeHtml(t.name)} is on ${t.documents} ${T('invoices')} and bills, so its rule stays as they were made under it.</p>` : '';
        openModal(t ? `Edit ${t.name}` : 'New Payment Term', `
            <form id="payment-term-editor" onsubmit="SettingsPage.savePaymentTerm(event, ${id})">
                ${locked}
                <div class="form-grid">
                    <div class="form-group full-width"><label for="pt-name">Name</label>
                        <input id="pt-name" name="name" required maxlength="50" value="${val(v.name)}" placeholder="e.g. 2% 10 Net 30"></div>
                    <div class="form-group"><label for="pt-kind">Kind</label>
                        <select id="pt-kind" name="kind" onchange="SettingsPage.paymentTermKind(this.value)">
                            <option value="standard" ${v.kind === 'standard' ? 'selected' : ''}>Standard: due in a number of days</option>
                            <option value="date_driven" ${v.kind === 'date_driven' ? 'selected' : ''}>Date-driven: due on a day of the month</option>
                            <option value="end_of_month" ${v.kind === 'end_of_month' ? 'selected' : ''}>End of month: due a number of days after the month ends</option>
                        </select></div>
                    <div class="form-group" data-kind="standard"><label for="pt-net-days">Due in (days)</label>
                        <input id="pt-net-days" name="net_days" type="number" min="0" max="3650" step="1" value="${val(v.net_days)}"></div>
                    <div class="form-group" data-kind="end_of_month"><label for="pt-eom-days">Due (days) after the end of the month ${hint(`Counted from the last day of the month the ${T('invoice')} or bill is dated in, whatever day of the month that is: 75 here is "Net 75 EOM". 0 is due on the last day of the month.`, 'Days after the end of the month')}</label>
                        <input id="pt-eom-days" name="eom_days" type="number" min="0" max="3650" step="1" value="${val(v.kind === 'end_of_month' ? v.net_days : 30)}" oninput="SettingsPage.paymentTermExample()"></div>
                    <p class="form-group full-width" data-kind="end_of_month" id="pt-eom-example" aria-live="polite" style="margin:0; font-size:12px; color:var(--text-muted);"></p>
                    <div class="form-group" data-kind="date_driven"><label for="pt-due-day">Due on day of the month</label>
                        <input id="pt-due-day" name="due_day" type="number" min="1" max="31" step="1" value="${val(v.due_day)}"></div>
                    <div class="form-group" data-kind="date_driven"><label for="pt-cutoff">Next month's when dated fewer than (days) before it</label>
                        <input id="pt-cutoff" name="cutoff_days" type="number" min="0" max="28" step="1" value="${val(v.cutoff_days)}"></div>
                    <div class="form-group"><label for="pt-discount">Early-payment discount (%)</label>
                        <input id="pt-discount" name="discount_percent" type="number" min="0" max="99.99" step="0.01" value="${val(v.discount_percent)}" placeholder="none"></div>
                    <div class="form-group" data-kind="standard"><label for="pt-discount-days">if paid within (days)</label>
                        <input id="pt-discount-days" name="discount_days" type="number" min="0" max="3650" step="1" value="${val(v.discount_days)}"></div>
                    <div class="form-group" data-kind="end_of_month"><label for="pt-eom-discount-days">if paid within (days) after the end of the month</label>
                        <input id="pt-eom-discount-days" name="eom_discount_days" type="number" min="0" max="3650" step="1" value="${val(v.kind === 'end_of_month' ? v.discount_days : '')}"></div>
                    <div class="form-group" data-kind="date_driven"><label for="pt-discount-day">if paid by day of the month</label>
                        <input id="pt-discount-day" name="discount_day" type="number" min="1" max="31" step="1" value="${val(v.discount_day)}"></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Save Term</button>
                </div>
            </form>`);
        SettingsPage.paymentTermKind(v.kind);
    },

    // Show the fields of the chosen kind only.
    paymentTermKind(kind) {
        document.querySelectorAll('#payment-term-editor [data-kind]').forEach(g => {
            g.hidden = g.dataset.kind !== kind;
        });
        if (kind === 'end_of_month') SettingsPage.paymentTermExample();
    },

    // The end-of-month kind's example (#1091): today's date and the due date
    // the server's parser gives "Net N EOM" from it (the same rule as the
    // term), so the form never computes a due date of its own.
    async paymentTermExample() {
        const el = document.getElementById('pt-eom-example');
        const input = document.getElementById('pt-eom-days');
        if (!el || !input) return;
        const days = input.value === '' ? NaN : Number(input.value);
        if (!Number.isInteger(days) || days < 0 || days > 3650) { el.textContent = ''; return; }
        const today = todayISO();
        const asked = input.value;
        const d = await PaymentTerms.dates(today, `Net ${days} EOM`);
        if (input.value !== asked) return;  // a later keystroke answers instead
        el.textContent = d && d.due_date
            ? `For example, a document dated ${formatDate(today)} is due ${formatDate(d.due_date)}.`
            : '';
    },

    async savePaymentTerm(e, id) {
        e.preventDefault();
        const form = e.target;
        const kind = form.kind.value;
        const num = name => (form[name].value === '' ? null : Number(form[name].value));
        const eom = kind === 'end_of_month';
        const pct = form.discount_percent.value === '' ? null : form.discount_percent.value;
        // the other kind's fields go as null, so the server checks one rule
        const data = {
            name: form.name.value.trim(),
            kind,
            net_days: kind === 'standard' ? num('net_days') : eom ? num('eom_days') : null,
            due_day: kind === 'date_driven' ? num('due_day') : null,
            cutoff_days: kind === 'date_driven' ? (num('cutoff_days') || 0) : 0,
            discount_percent: pct,
            discount_days: pct === null ? null
                : kind === 'standard' ? num('discount_days') : eom ? num('eom_discount_days') : null,
            discount_day: kind === 'date_driven' && pct !== null ? num('discount_day') : null,
        };
        try {
            if (id) {
                // a term in use sends only what changed; its rule stays
                const t = (SettingsPage._paymentTerms || []).find(x => x.id === id) || {};
                const changed = {};
                for (const [k, v] of Object.entries(data)) {
                    const was = t[k] === undefined ? null : t[k];
                    const same = k === 'discount_percent'
                        ? (was === null ? v === null : v !== null && Number(was) === Number(v))
                        : was === v;
                    if (!same) changed[k] = v;
                }
                await API.put(`/payment-terms/${id}`, changed);
            } else {
                await API.post('/payment-terms', data);
            }
            toast('Payment term saved');
            closeModal();
            await PaymentTerms.load(true);
            SettingsPage.loadPaymentTerms();
        } catch (err) { toast(err.message, 'error'); }
    },

    async setPaymentTermActive(id, active) {
        try {
            await API.put(`/payment-terms/${id}`, { is_active: active });
            toast(active ? 'Term made active' : 'Term made inactive; documents that carry it keep it');
            await PaymentTerms.load(true);
            SettingsPage.loadPaymentTerms();
        } catch (err) { toast(err.message, 'error'); }
    },

    previewGivingStatement() {
        const year = new Date().getFullYear() - 1;
        window.open(`/api/donors/giving-statement/preview?year=${year}`, '_blank');
    },

    // Opens a sample check PDF in a new tab (#440) with the stock, check
    // position (#727), nudge, field layout (#749), dollar sign (#799) and asterisks (#1239) as they stand in the form — saved or not — so a nudge can be tried
    // before it is saved.
    testPrintCheck() {
        const form = document.getElementById('settings-form');
        const params = new URLSearchParams();
        for (const name of ['check_stock', 'check_position', 'check_nudge_x_in', 'check_nudge_y_in', 'check_blank_stock', 'check_micr_amount', 'check_field_layout', 'check_amount_no_dollar_sign', 'check_amount_asterisk_fill']) {
            const el = form && form.querySelector(`[name="${name}"]`);
            if (el) params.set(name, el.type === 'checkbox' ? (el.checked ? 'true' : 'false') : el.value);
        }
        // the sample memo follows the Check memo choice (#800)
        const memoChoice = form && form.querySelector('[name="check_memo"]');
        if (memoChoice) params.set('check_memo', memoChoice.value);
        const qs = params.toString();
        window.open('/api/checks/test-print' + (qs ? `?${qs}` : ''), '_blank');
    },

    // ----- the check face's per-field layout (#749) ---------------------
    // One row per field, each moved, sized and bolded on its own. The row's
    // inputs carry no name; what they hold is kept, as the JSON the server
    // stores (only what differs from today's face), in the hidden
    // check_field_layout field, which the form saves and the test print sends.
    // [key, label, size it prints at today, bold today, size with the check
    // on top where that differs]
    CHECK_LAYOUT_FIELDS: [
        ['payee', 'Payee', '12', true],
        ['payee_address', 'Payee address', '9', false],
        ['date', 'Date', '10', false],
        ['amount', 'Amount in figures', '14', true, '12'],
        ['amount_words', 'Amount in words', '9', false],
        ['memo', 'Memo', '8', false],
    ],

    _parseCheckLayout(text) {
        if (!text) return {};
        try {
            const v = JSON.parse(text);
            return v && typeof v === 'object' && !Array.isArray(v) ? v : {};
        } catch (e) { return {}; }
    },

    // The layout as the server stores it: fields and attributes in name
    // order, only those set; "" for none.
    _checkLayoutJson(layout) {
        const out = {};
        for (const field of Object.keys(layout).sort()) {
            const attrs = layout[field] || {};
            const kept = {};
            for (const a of Object.keys(attrs).sort()) {
                if (attrs[a] !== undefined && attrs[a] !== null && attrs[a] !== '') kept[a] = attrs[a];
            }
            if (Object.keys(kept).length) out[field] = kept;
        }
        return Object.keys(out).length ? JSON.stringify(out) : '';
    },

    // `position` is the saved Check Position: a field that prints at
    // another size with the check on top shows that size as its hint.
    _checkFieldLayoutHtml(stored, position) {
        const layout = SettingsPage._parseCheckLayout(stored);
        const num = v => (typeof v === 'number' && isFinite(v) ? String(v) : '');
        const rows = SettingsPage.CHECK_LAYOUT_FIELDS.map(([key, label, bottomSize, bold, topSize]) => {
            const size = position === 'top' && topSize ? topSize : bottomSize;
            const sizes = topSize ? ` data-size-bottom="${bottomSize}" data-size-top="${topSize}" data-label="${label}"` : '';
            const a = layout[key] || {};
            const on = typeof a.bold === 'boolean' ? a.bold : bold;
            const id = 'check-field-' + key.replace(/_/g, '-');
            return `<tr>
                <th scope="row" id="${id}-h" style="text-align:left; font-weight:normal;">${label}</th>
                <td><input type="number" id="${id}-x" data-check-field="${key}" data-attr="x" data-admin min="-1" max="1" step="0.01" placeholder="0" style="width:70px;"
                    aria-label="${label} left/right offset (in)" value="${num(a.x)}" oninput="SettingsPage.syncCheckFieldLayout()"></td>
                <td><input type="number" id="${id}-y" data-check-field="${key}" data-attr="y" data-admin min="-1" max="1" step="0.01" placeholder="0" style="width:70px;"
                    aria-label="${label} up/down offset (in)" value="${num(a.y)}" oninput="SettingsPage.syncCheckFieldLayout()"></td>
                <td><input type="number" id="${id}-size" data-check-field="${key}" data-attr="size" data-admin min="6" max="18" step="0.1" placeholder="${size}"${sizes} style="width:70px;"
                    aria-label="${label} text size (pt), today ${size}" value="${num(a.size)}" oninput="SettingsPage.syncCheckFieldLayout()"></td>
                <td style="text-align:center;"><input type="checkbox" id="${id}-bold" data-check-field="${key}" data-attr="bold" data-default="${bold}" data-admin style="width:auto; margin:0;"
                    aria-label="${label} bold" ${on ? 'checked' : ''} onchange="SettingsPage.syncCheckFieldLayout()"></td>
            </tr>`;
        }).join('');
        return `<div class="form-group full-width" id="settings-check-fields" role="group" aria-labelledby="settings-check-fields-h">
                            <h4 id="settings-check-fields-h" style="margin:4px 0; font-size:12px;">Field layout</h4>
                            <input type="hidden" name="check_field_layout" id="settings-check-field-layout" value="${escapeHtml(SettingsPage._checkLayoutJson(layout))}">
                            <table class="data-table" style="font-size:11px; width:auto;" aria-describedby="settings-check-fields-help">
                                <thead><tr><th scope="col">Field</th><th scope="col">Left/right (in)</th><th scope="col">Up/down (in)</th><th scope="col">Size (pt)</th><th scope="col">Bold</th></tr></thead>
                                <tbody>${rows}</tbody>
                            </table>
                            <div style="margin-top:6px;" data-write>
                                <button type="button" class="btn btn-sm btn-secondary" data-admin data-write onclick="SettingsPage.resetCheckFieldLayout()">Reset field layout to defaults</button>
                            </div>
                        </div>`;
    },

    // Check Position changed: the Size hint (and its spoken "today") of a
    // field whose size depends on it follows, 14 at the bottom and 12 on top
    // for the amount in figures. The stored layout is untouched.
    checkFieldSizesFollowPosition(position) {
        document.querySelectorAll('#settings-check-fields [data-size-top]').forEach(el => {
            const size = el.getAttribute(position === 'top' ? 'data-size-top' : 'data-size-bottom');
            el.setAttribute('placeholder', size);
            el.setAttribute('aria-label', `${el.getAttribute('data-label')} text size (pt), today ${size}`);
        });
    },

    // The table's inputs, as the hidden field's JSON: blank offsets and
    // sizes and a Bold box left at today's weight are left out.
    syncCheckFieldLayout() {
        const hidden = document.getElementById('settings-check-field-layout');
        if (!hidden) return;
        const layout = {};
        document.querySelectorAll('#settings-check-fields [data-check-field]').forEach(el => {
            const field = el.getAttribute('data-check-field');
            const attr = el.getAttribute('data-attr');
            let v;
            if (attr === 'bold') {
                if (el.checked === (el.getAttribute('data-default') === 'true')) return;
                v = el.checked;
            } else {
                const t = String(el.value || '').trim();
                if (t === '') return;
                // what is not a number is sent as typed, so saving says why
                v = isFinite(Number(t)) ? Number(t) : t;
                if (v === 0 && attr !== 'size') return;
            }
            (layout[field] = layout[field] || {})[attr] = v;
        });
        hidden.value = SettingsPage._checkLayoutJson(layout);
    },

    // Every field back where it prints today: offsets and sizes empty, Bold
    // at today's weight. Saved with Save Settings like the rest.
    resetCheckFieldLayout() {
        document.querySelectorAll('#settings-check-fields [data-check-field]').forEach(el => {
            if (el.getAttribute('data-attr') === 'bold') el.checked = el.getAttribute('data-default') === 'true';
            else el.value = '';
        });
        SettingsPage.syncCheckFieldLayout();
        SettingsPage._updateDirty();
    },

    // ----- the deposit slip on pre-printed stock (#798) ------------------
    // Deposit slip stock and its nudges are named fields of the form; the
    // layout table's inputs carry no name and are kept, as the JSON the
    // server stores (only what differs from the defaults below), in the
    // hidden deposit_slip_layout field, which the form saves and the test
    // slip sends. Positions are inches from the slip's top-left: a text
    // field's x is where it starts, an amount's where it ends.
    // [key, label, x, y, kind] -- the defaults of
    // settings_service.DEPOSIT_SLIP_DEFAULTS
    DEPOSIT_SLIP_FIELDS: [
        ['date', 'Date', 1, 0.9, 'text'],
        ['account', 'Account', 1, 0.55, 'text'],
        ['cash', 'Cash', 7.9, 0.55, 'amount'],
        ['check_number', 'Check number (first line)', 4.9, 0.85, 'text'],
        ['check_amount', 'Check amount (first line)', 7.9, 0.85, 'amount'],
        ['checks_subtotal', 'Checks subtotal', 7.9, 2.9, 'amount'],
        ['total', 'Total', 7.9, 3.2, 'amount'],
    ],
    DEPOSIT_SLIP_SIZE: 10,
    DEPOSIT_SLIP_LINES: { count: 8, spacing: 0.25 },

    _depositSlipHtml(s) {
        s = s || {};
        const layout = SettingsPage._parseCheckLayout(s.deposit_slip_layout);
        const num = (v, d) => (typeof v === 'number' && isFinite(v) ? String(v) : String(d));
        const rows = SettingsPage.DEPOSIT_SLIP_FIELDS.map(([key, label, x, y, kind]) => {
            const a = layout[key] || {};
            const id = 'deposit-slip-' + key.replace(/_/g, '-');
            const where = kind === 'amount' ? 'right end' : 'left edge';
            const show = a.show !== false;
            return `<tr>
                <th scope="row" id="${id}-h" style="text-align:left; font-weight:normal;">${label}</th>
                <td><input type="number" id="${id}-x" data-slip-field="${key}" data-attr="x" data-default="${x}" data-admin min="0" max="11" step="0.01" style="width:70px;"
                    aria-label="${label}: ${where}, inches from the left" value="${num(a.x, x)}" oninput="SettingsPage.syncDepositSlipLayout()"></td>
                <td><input type="number" id="${id}-y" data-slip-field="${key}" data-attr="y" data-default="${y}" data-admin min="0" max="11" step="0.01" style="width:70px;"
                    aria-label="${label}: inches from the top" value="${num(a.y, y)}" oninput="SettingsPage.syncDepositSlipLayout()"></td>
                <td><input type="number" id="${id}-size" data-slip-field="${key}" data-attr="size" data-default="${SettingsPage.DEPOSIT_SLIP_SIZE}" data-admin min="6" max="18" step="0.1" style="width:70px;"
                    aria-label="${label} text size (pt)" value="${num(a.size, SettingsPage.DEPOSIT_SLIP_SIZE)}" oninput="SettingsPage.syncDepositSlipLayout()"></td>
                <td style="text-align:center;"><input type="checkbox" id="${id}-show" data-slip-field="${key}" data-attr="show" data-admin style="width:auto; margin:0;"
                    aria-label="Print the ${label.toLowerCase()}" ${show ? 'checked' : ''} onchange="SettingsPage.syncDepositSlipLayout()"></td>
            </tr>`;
        }).join('');
        const lines = layout.check_lines || {};
        const L = SettingsPage.DEPOSIT_SLIP_LINES;
        const pre = s.deposit_slip_stock === 'preprinted';
        return `<div class="form-group full-width" id="settings-deposit-slip" role="group" aria-labelledby="settings-deposit-slip-h">
                            <h4 id="settings-deposit-slip-h" style="margin:12px 0 4px; font-size:12px;">Deposit slips</h4>
                            <div class="form-grid">
                                <div class="form-group full-width"><label for="settings-deposit-slip-stock">Deposit slip stock</label>
                                    <select name="deposit_slip_stock" id="settings-deposit-slip-stock" aria-describedby="settings-deposit-slip-stock-help">
                                        <option value="plain" ${pre ? '' : 'selected'}>Plain paper: SlowBooks' own deposit slip</option>
                                        <option value="preprinted" ${pre ? 'selected' : ''}>Pre-printed deposit slips from the bank: figures only</option>
                                    </select>
                                </div>
                                <div class="form-group"><label for="settings-deposit-slip-nudge-x">Deposit slip left/right nudge (in)</label>
                                    <input name="deposit_slip_nudge_x_in" id="settings-deposit-slip-nudge-x" type="number" min="-0.5" max="0.5" step="0.01"
                                        title="Inches, -0.5 to 0.5, in 0.01 steps" value="${escapeHtml(s.deposit_slip_nudge_x_in || '0')}"></div>
                                <div class="form-group"><label for="settings-deposit-slip-nudge-y">Deposit slip up/down nudge (in)</label>
                                    <input name="deposit_slip_nudge_y_in" id="settings-deposit-slip-nudge-y" type="number" min="-0.5" max="0.5" step="0.01"
                                        title="Inches, -0.5 to 0.5, in 0.01 steps" value="${escapeHtml(s.deposit_slip_nudge_y_in || '0')}"></div>
                                <div class="form-group full-width" id="settings-deposit-slip-fields" role="group" aria-labelledby="settings-deposit-slip-fields-h">
                                    <div id="settings-deposit-slip-fields-h" style="font-weight:bold; font-size:12px; margin-bottom:4px;">Deposit slip layout</div>
                                    <input type="hidden" name="deposit_slip_layout" id="settings-deposit-slip-layout" value="${escapeHtml(SettingsPage._checkLayoutJson(layout))}">
                                    <table class="data-table" style="font-size:11px; width:auto;" aria-describedby="settings-deposit-slip-fields-help">
                                        <thead><tr><th scope="col">Figure</th><th scope="col">Left (in)</th><th scope="col">Top (in)</th><th scope="col">Size (pt)</th><th scope="col">Print</th></tr></thead>
                                        <tbody>${rows}</tbody>
                                    </table>
                                    <div style="display:flex; gap:12px; flex-wrap:wrap; margin-top:6px; font-size:11px;">
                                        <label for="deposit-slip-lines-count" style="font-weight:normal;">Check lines on the slip
                                            <input type="number" id="deposit-slip-lines-count" data-slip-lines="count" data-default="${L.count}" data-admin min="1" max="30" step="1" style="width:60px;"
                                                value="${num(lines.count, L.count)}" oninput="SettingsPage.syncDepositSlipLayout()"></label>
                                        <label for="deposit-slip-lines-spacing" style="font-weight:normal;">Line spacing (in)
                                            <input type="number" id="deposit-slip-lines-spacing" data-slip-lines="spacing" data-default="${L.spacing}" data-admin min="0.1" max="1" step="0.01" style="width:60px;"
                                                value="${num(lines.spacing, L.spacing)}" oninput="SettingsPage.syncDepositSlipLayout()"></label>
                                    </div>
                                    <div style="margin-top:6px;" data-write>
                                        <button type="button" class="btn btn-sm btn-secondary" data-admin data-write onclick="SettingsPage.resetDepositSlipLayout()">Reset deposit slip layout to defaults</button>
                                    </div>
                                </div>
                                <div class="form-group full-width">
                                    <button type="button" class="btn btn-sm btn-secondary" aria-describedby="settings-deposit-slip-test-help" onclick="SettingsPage.testPrintDepositSlip()">Print a test slip</button>
                                </div>
                            </div>
                        </div>`;
    },

    // The layout table's inputs, as the hidden field's JSON: a figure at
    // its default place and size, and Print left on, are left out.
    syncDepositSlipLayout() {
        const hidden = document.getElementById('settings-deposit-slip-layout');
        if (!hidden) return;
        const layout = {};
        const take = (group, attr, el) => {
            let v;
            if (attr === 'show') {
                if (el.checked) return;
                v = false;
            } else {
                const t = String(el.value || '').trim();
                if (t === '') return;
                // what is not a number is sent as typed, so saving says why
                v = isFinite(Number(t)) ? Number(t) : t;
                if (v === Number(el.getAttribute('data-default'))) return;
            }
            (layout[group] = layout[group] || {})[attr] = v;
        };
        document.querySelectorAll('#settings-deposit-slip-fields [data-slip-field]').forEach(el => {
            take(el.getAttribute('data-slip-field'), el.getAttribute('data-attr'), el);
        });
        document.querySelectorAll('#settings-deposit-slip-fields [data-slip-lines]').forEach(el => {
            take('check_lines', el.getAttribute('data-slip-lines'), el);
        });
        hidden.value = SettingsPage._checkLayoutJson(layout);
    },

    // Every figure back at its default place and size, printed. Saved with
    // Save Settings like the rest.
    resetDepositSlipLayout() {
        document.querySelectorAll('#settings-deposit-slip-fields [data-slip-field], #settings-deposit-slip-fields [data-slip-lines]').forEach(el => {
            if (el.getAttribute('data-attr') === 'show') el.checked = true;
            else el.value = el.getAttribute('data-default');
        });
        SettingsPage.syncDepositSlipLayout();
        SettingsPage._updateDirty();
    },

    // Opens the sample deposit on pre-printed slip stock (#798) in a new
    // tab, with the layout and nudges as they stand in the form.
    testPrintDepositSlip() {
        const form = document.getElementById('settings-form');
        const params = new URLSearchParams();
        for (const name of ['deposit_slip_layout', 'deposit_slip_nudge_x_in', 'deposit_slip_nudge_y_in']) {
            const el = form && form.querySelector(`[name="${name}"]`);
            if (el) params.set(name, el.value);
        }
        const qs = params.toString();
        window.open('/api/deposits/test-slip' + (qs ? `?${qs}` : ''), '_blank');
    },

    // ----- a bank account's details for blank check stock (#739) --------
    // Saved on their own (PUT /api/settings/check-bank-details/<id>), not with the
    // settings form: the fields carry no name, so the form leaves them out.

    _checkBankOptions(accounts) {
        const banks = (Array.isArray(accounts) ? accounts : [])
            .filter(a => a.bank_kind === 'bank' && a.is_active !== false);
        if (!banks.length) return '<option value="">No bank accounts yet</option>';
        return banks.map(a => `<option value="${a.id}">${escapeHtml(accountLabel(a))}</option>`).join('');
    },

    async loadCheckBankDetails() {
        const pick = document.getElementById('check-bank-account');
        if (!pick || !pick.value) return;
        try {
            const d = await API.get(`/settings/check-bank-details/${pick.value}`);
            document.getElementById('check-bank-name').value = d.bank_name || '';
            document.getElementById('check-bank-city').value = d.bank_city_state || '';
            document.getElementById('check-bank-routing').value = d.routing_number || '';
            document.getElementById('check-bank-fraction').value = d.fractional_routing || '';
            const number = document.getElementById('check-bank-account-number');
            number.value = '';
            number.placeholder = d.has_account_number ? `Ends in ${d.account_number_last_four}` : 'Digits and dashes';
            document.getElementById('check-bank-account-number-help').textContent = d.has_account_number
                ? 'Saved. Leave it empty to keep it, or type a new one to replace it.'
                : 'Not entered yet.';
        } catch (err) { toast(err.message, 'error'); }
    },

    async saveCheckBankDetails() {
        const pick = document.getElementById('check-bank-account');
        if (!pick || !pick.value) { toast('Add a bank account first', 'error'); return; }
        const body = {
            bank_name: document.getElementById('check-bank-name').value,
            bank_city_state: document.getElementById('check-bank-city').value,
            routing_number: document.getElementById('check-bank-routing').value,
            fractional_routing: document.getElementById('check-bank-fraction').value,
        };
        // an empty box keeps the saved number
        const number = document.getElementById('check-bank-account-number').value.trim();
        if (number) body.account_number = number;
        try {
            await API.put(`/settings/check-bank-details/${pick.value}`, body);
            toast('Bank account details saved');
            SettingsPage.loadCheckBankDetails();
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
            const providers = cfg.providers || [];
            const chosen = cfg.provider || (providers[0] && providers[0].key) || '';
            SettingsPage._aiHintFollow(providers.find(p => p.key === chosen) || providers[0] || {});
            SettingsPage._wireAiConfig(cfg);
        } catch (err) {
            host.innerHTML =
                `<div style="font-size:11px; color:var(--danger,#c00);">` +
                `Failed to load AI config: ${escapeHtml(err.message || String(err))}</div>`;
        }
    },

    // The AI Insights note's entries for the chosen provider (#977): its own
    // line (free tier, where to get a key) and, for the two gateways, what
    // their address is.
    _aiHintFollow(spec) {
        const hintEl = document.getElementById('ai-settings-hint');
        if (hintEl) {
            hintEl.innerHTML =
                escapeHtml(spec.free_tier_hint || '') +
                (spec.docs_url
                    ? ` &middot; <a href="${escapeHtml(spec.docs_url)}" target="_blank" rel="noopener">Get a key</a>`
                    : '');
        }
        Hints.showEntry('ai-settings-hint', !!(spec.free_tier_hint || spec.docs_url));
        Hints.showEntry('ai-worker-help', !!spec.needs_worker_url);
        Hints.showEntry('ai-endpoint-help', !!spec.needs_endpoint_url);
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
        // the provider the saved key was entered for (NEW-46)
        const keyOwner = SettingsPage._aiKeyOwner(cfg);
        const currentModel = cfg.model || '';

        const providerOptions = providers.map(p =>
            `<option value="${escapeHtml(p.key)}"${p.key === currentProvider ? ' selected' : ''}>` +
            `${escapeHtml(p.label)}</option>`
        ).join('');

        // The administrator's, like every company setting (the key is a
        // company-wide credential and the endpoint receives the dashboard's
        // figures): any other sign-in sees the values locked (App.adminPass).
        return `
            <div class="hidden" data-admin-note style="font-size:11px; color:var(--text-muted); margin-bottom:8px;">AI settings are changed by an administrator.</div>
            <label class="form-field">
                <span>Provider</span>
                <select id="ai-settings-provider" data-admin aria-describedby="ai-settings-hint">${providerOptions}</select>
            </label>
            <label class="form-field">
                <span><span id="ai-settings-model-label">Model</span> <button type="button" class="btn btn-sm" id="ai-settings-models-refresh" data-admin title="List the provider's models again">Refresh models</button></span>
                <select id="ai-settings-model-select" data-admin aria-labelledby="ai-settings-model-label">
                    ${SettingsPage._modelOptionsHtml(SettingsPage._aiModels[currentProvider] || [], currentModel, SettingsPage._modelsEmptyText[currentProvider])}
                </select>
                <input type="text" id="ai-settings-model-custom" data-admin aria-label="Model ID" spellcheck="false" autocapitalize="off" autocorrect="off"
                       value="${escapeHtml(currentModel || '')}"
                       placeholder="Type a model ID"
                       style="margin-top:6px; ${SettingsPage._isCustomModel(SettingsPage._aiModels[currentProvider] || [], currentModel) ? '' : 'display:none;'}">
                <div id="ai-settings-models-note" class="ai-settings-hint"></div>
            </label>
            <label class="form-field" id="ai-settings-cf-wrap" style="${needsAccount ? '' : 'display:none'}">
                <span>Cloudflare Account ID</span>
                <input type="text" id="ai-settings-cf-account" data-admin spellcheck="false" autocapitalize="off" autocorrect="off"
                       value="${escapeHtml(cfg.cloudflare_account_id || '')}"
                       placeholder="32-char hex (from dash.cloudflare.com)">
            </label>
            <fieldset id="ai-settings-worker-wrap" class="ai-worker-section"
                      style="${needsWorker ? '' : 'display:none'}">
                <legend>Cloudflare Worker Gateway</legend>
                <label class="form-field">
                    <span>Worker URL <em class="ai-worker-required">(https only)</em></span>
                    <input type="url" id="ai-settings-worker-url" data-admin aria-describedby="ai-worker-help"
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
                <label class="form-field">
                    <span>Base URL <em class="ai-worker-required">(https only)</em></span>
                    <input type="url" id="ai-settings-endpoint-url" data-admin aria-describedby="ai-endpoint-help"
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
            <label class="form-field" for="ai-settings-key">
                <span>API Key / Shared Secret <span id="ai-settings-key-saved"${hasKey ? '' : ' class="hidden"'}><em class="ai-key-saved">(saved &#10003;)</em> <button type="button" class="btn btn-sm" id="ai-settings-key-remove" data-write data-admin title="Remove the stored key">Remove</button></span></span>
                <input type="password" id="ai-settings-key" data-admin
                       placeholder="${hasKey ? 'Leave blank to keep existing' : 'Paste key or openssl rand -hex 32'}"
                       autocomplete="new-password">
            </label>
            <div id="ai-settings-key-unmatched" class="ai-settings-hint${cfg.api_key_unmatched ? '' : ' hidden'}">
                The key saved before 2.22.1 couldn't be matched to a provider, so it
                isn't sent to any. Enter the key for the provider you use.
            </div>
            <div class="ai-settings-buttons">
                <button type="button" class="btn btn-secondary btn-sm" data-write data-admin id="ai-settings-test">Test</button>
                <span id="ai-settings-test-result" class="ai-settings-test-result"></span>
                <div class="ai-settings-spacer"></div>
                <button type="button" class="btn btn-primary btn-sm" data-write data-admin id="ai-settings-save">Save AI settings</button>
            </div>
        `;
    },

    // The provider the saved key was entered for: the server says
    // (api_key_provider); a key it calls this provider's is this one's.
    _aiKeyOwner(cfg) {
        return cfg.api_key_provider || (cfg.has_api_key ? cfg.provider : '') || '';
    },

    // The models each provider offers, as the provider listed them this
    // session (#254): there is no built-in list and no built-in default.
    // Keyed by provider; filled by _loadAiModels once a key is saved.
    _aiModels: {},

    // True when the chosen model isn't on the provider's list (a model the
    // provider has since dropped, or one typed by hand): the dropdown shows
    // "Custom…" and the text box holds it.
    _isCustomModel(models, model) {
        if (!model) return false;
        return !(models || []).some(m => m.id === model);
    },

    // What an empty list says, per provider (gate round 2, NEW-63: one
    // text for the page showed OpenAI a failure that was Anthropic's)
    _modelsEmptyText: {},

    _modelOptionsHtml(models, currentModel, emptyText) {
        models = models || [];
        const isCustom = SettingsPage._isCustomModel(models, currentModel);
        const opts = models.map(m =>
            `<option value="${escapeHtml(m.id)}"${m.id === currentModel ? ' selected' : ''}>${escapeHtml(m.label || m.id)}</option>`
        ).join('');
        // Nothing chosen yet: a placeholder, not a guess. Save and Test ask
        // for a choice; the server does too.
        const placeholder = `<option value="" disabled${currentModel ? '' : ' selected'}>${models.length ? 'Choose a model…' : (emptyText || 'Enter a key to list models')}</option>`;
        return placeholder + opts +
            `<option value="__custom__"${isCustom ? ' selected' : ''}>Custom…</option>`;
    },

    // Ask the provider for its models (GET /api/analytics/ai-models, with
    // the saved key) and fill the picker, keeping whatever is chosen. A
    // provider that can't list (a gateway without /models, a bad key) leaves
    // the typed model and says why.
    // What the model list is asked with: the provider picked and the key
    // typed, saved or not (gate round 1, NEW-56 / W-4: a first key could
    // not reach the list while Save asked for a model and the list for a
    // saved key). Nothing in it is stored by the list.
    _modelsRequest(provider) {
        const val = (id) => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
        const body = { provider };
        const key = val('ai-settings-key');
        if (key) body.api_key = key;
        for (const [field, id] of [['cloudflare_account_id', 'ai-settings-cf-account'], ['worker_url', 'ai-settings-worker-url'], ['endpoint_url', 'ai-settings-endpoint-url']]) {
            if (val(id)) body[field] = val(id);
        }
        return body;
    },

    async _loadAiModels(provider, keepModel) {
        const sel = document.getElementById('ai-settings-model-select');
        const custom = document.getElementById('ai-settings-model-custom');
        const note = document.getElementById('ai-settings-models-note');
        if (!sel) return;
        sel.innerHTML = '<option value="" selected disabled>Loading models…</option>';
        note.textContent = '';
        try {
            const res = await API.post('/analytics/ai-models', SettingsPage._modelsRequest(provider));
            if (res.provider !== provider) return; // the provider changed under the request
            SettingsPage._aiModels[provider] = res.models || [];
            SettingsPage._modelsEmptyText[provider] = res.models.length ? '' : 'No models listed — type a model ID';
            sel.innerHTML = SettingsPage._modelOptionsHtml(res.models, keepModel, SettingsPage._modelsEmptyText[provider]);
            custom.style.display = sel.value === '__custom__' ? '' : 'none';
            note.textContent = `${res.models.length} models listed by ${res.provider_label}.`;
        } catch (err) {
            SettingsPage._aiModels[provider] = [];
            SettingsPage._modelsEmptyText[provider] = 'No models listed — type a model ID';
            sel.innerHTML = SettingsPage._modelOptionsHtml([], keepModel, SettingsPage._modelsEmptyText[provider]);
            if (!keepModel) sel.value = '__custom__';
            custom.style.display = '';
            note.textContent = `Couldn't list models: ${String(err.message || err).replace(/\.$/, '')}. Type a model ID.`;
        }
    },

    _wireAiConfig(cfg) {
        const providers = cfg.providers || [];
        const providerSel = document.getElementById('ai-settings-provider');
        const modelSel = document.getElementById('ai-settings-model-select');
        const modelCustom = document.getElementById('ai-settings-model-custom');
        const cfWrap = document.getElementById('ai-settings-cf-wrap');
        const workerWrap = document.getElementById('ai-settings-worker-wrap');
        const endpointWrap = document.getElementById('ai-settings-endpoint-wrap');
        const saveBtn = document.getElementById('ai-settings-save');
        const testBtn = document.getElementById('ai-settings-test');
        const testRes = document.getElementById('ai-settings-test-result');

        if (!providerSel) return; // render failed; nothing to wire

        // "(saved ✓)" and the key box's hint show only for the provider the
        // saved key was entered for; the server sends a key there only.
        // `cfg` is what the server last said: Test's save updates it, so the
        // mark follows the key Test saved (gate round 2, NEW-53).
        const syncKeyMark = () => {
            const keySaved = SettingsPage._aiKeyOwner(cfg) === providerSel.value;
            document.getElementById('ai-settings-key').placeholder =
                keySaved ? 'Leave blank to keep existing' : 'Paste key or openssl rand -hex 32';
            const savedEl = document.getElementById('ai-settings-key-saved');
            if (savedEl) savedEl.classList.toggle('hidden', !keySaved);
            const unmatched = document.getElementById('ai-settings-key-unmatched');
            if (unmatched) unmatched.classList.toggle('hidden', !cfg.api_key_unmatched);
        };

        // Show the custom text input only when "Custom…" is selected.
        const syncCustomVisibility = () => {
            modelCustom.style.display =
                modelSel.value === '__custom__' ? '' : 'none';
        };
        modelSel.addEventListener('change', syncCustomVisibility);

        // The model the picker holds now: the chosen one, or the typed one.
        const chosenModel = () =>
            modelSel.value === '__custom__' ? modelCustom.value.trim() : (modelSel.value || '');
        // The provider's models, with the key typed or saved for it (#254).
        const loadModels = () => SettingsPage._loadAiModels(providerSel.value, chosenModel());
        if (SettingsPage._aiKeyOwner(cfg) === providerSel.value && !SettingsPage._aiModels[providerSel.value]) {
            loadModels();
        }
        document.getElementById('ai-settings-models-refresh').addEventListener('click', loadModels);

        providerSel.addEventListener('change', () => {
            const spec = providers.find(p => p.key === providerSel.value) || {};
            SettingsPage._aiHintFollow(spec);
            // The old provider's models aren't this one's: the picker
            // empties, and fills from the new provider once it has a key.
            // Back on the saved provider, the saved model is in the picker
            // again (gate round 2, NEW-63: a switch away and back lost it,
            // and the next Save wrote an empty model over it).
            const back = providerSel.value === cfg.provider ? (cfg.model || '') : '';
            const known = SettingsPage._aiModels[providerSel.value] || [];
            modelSel.innerHTML =
                SettingsPage._modelOptionsHtml(known, back, SettingsPage._modelsEmptyText[providerSel.value]);
            modelCustom.value = back && SettingsPage._isCustomModel(known, back) ? back : '';
            document.getElementById('ai-settings-models-note').textContent = '';
            syncCustomVisibility();
            cfWrap.style.display = spec.needs_account_id ? '' : 'none';
            workerWrap.style.display = spec.needs_worker_url ? '' : 'none';
            if (endpointWrap) endpointWrap.style.display = spec.needs_endpoint_url ? '' : 'none';
            // A key is one provider's credential (2.22.1 gate, NEW-46): a
            // key typed for the last provider is not carried to this one.
            document.getElementById('ai-settings-key').value = '';
            syncKeyMark();
            if (SettingsPage._aiKeyOwner(cfg) === providerSel.value) loadModels();
        });

        const resolveModel = () => chosenModel();
        // Save and Test need a model (there is no built-in default, #254).
        const modelChosen = () => {
            if (resolveModel()) return true;
            toast('Choose a model first: enter the key, pick one from the list the provider gives, or type a model ID.', 'error');
            modelSel.focus();
            return false;
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

        // Save takes a key with no model chosen yet: the key is stored, the
        // provider's models listed, and a choice asked for (gate round 1,
        // NEW-56 / W-4: Save refused the first key, and only Test got past).
        // The analyses still need a model; the server says so.
        saveBtn.addEventListener('click', async () => {
            const needsModel = !resolveModel();
            const typedKey = !!keyPayload().api_key;
            // no model and no key typed: a model is what is missing (a save
            // here wrote an empty model over the saved one — NEW-63)
            if (needsModel && !typedKey) { modelChosen(); return; }
            try {
                const updated = await API.put('/analytics/ai-config', { ...collectPayload(), ...keyPayload() });
                SettingsPage.aiConfigState = updated;
                if (needsModel) {
                    Object.assign(cfg, updated);
                    document.getElementById('ai-settings-key').value = '';
                    syncKeyMark();
                    await loadModels();
                    toast('Key saved. Choose a model from the list, then Save.', 'success');
                    return;
                }
                toast('AI settings saved', 'success');
                // Re-render to reflect "(saved ✓)" state and clear the key input
                SettingsPage.loadAiConfig();
            } catch (err) {
                toast('Save failed: ' + (err.message || err), 'error');
            }
        });

        testBtn.addEventListener('click', async () => {
            // Save first so the test uses any just-entered key, then call /test.
            // A key just entered with no model chosen: save the key, list the
            // provider's models, and ask for a choice; Test again then tests.
            const needsModel = !resolveModel();
            if (needsModel && !keyPayload().api_key) { modelChosen(); return; }
            testRes.textContent = 'Saving…';
            testRes.className = 'ai-settings-test-result';
            try {
                const updated = await API.put('/analytics/ai-config', { ...collectPayload(), ...keyPayload() });
                Object.assign(cfg, updated);
                SettingsPage.aiConfigState = updated;
                syncKeyMark();
                if (keyPayload().api_key || (needsModel && !SettingsPage._aiModels[providerSel.value])) {
                    await loadModels();
                }
                if (needsModel) {
                    testRes.textContent = 'Key saved. Choose a model, then Test.';
                    modelSel.focus();
                    return;
                }
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
        let target = null;
        try { target = window.sessionStorage ? window.sessionStorage.getItem('settings_focus') : null; } catch (e) { target = null; }
        if (!target) return;
        try { window.sessionStorage.removeItem('settings_focus'); } catch (e) { /* nothing kept */ }
        const el = document.getElementById(target);
        // its group opens, and the page lands on its heading (once it is
        // shown, for a section a loader reveals: Recent sends)
        if (el) SettingsPage.showSetting(el);
        // a block inside a section (the Fast Rack's, #912) is brought into
        // view itself: the landing puts its section's top there
        if (el && el.hasAttribute && el.hasAttribute('data-land-block') && typeof FastRack !== 'undefined') {
            FastRack._landOnEditor();
        }
    },
};

// The save bar is held to the window's bottom (sticky) over whatever
// scrolls under it, and Tab moved to controls there, out of sight (22
// stops at 1440 x 900: review). A control the keyboard puts under it is
// scrolled up clear of it, its ring and halo (8px) too. Measured, not
// scroll padding or margin: for a text box the browser brings only its
// caret line into view, and the invoice notes' first line was in view,
// the rest of the box under the bar. Not the bar's own Save, and not a
// pointer's focus, which must stay under the pointer that pressed it: a
// text box matches :focus-visible on a click too, and the page moved
// under a click into the invoice notes (App.keyboardFocus). (App.init
// hands every focusin here.) Anywhere in the panel, My Preferences too,
// which is beside the form, not in it: at 150% in a 900 x 600 window its
// first box landed half under the bar (W-126).
SettingsPage._clearSaveBar = function (e) {
    const a = e.target, bar = document.getElementById('settings-savebar');
    if (!bar || !a || !a.closest || bar.contains(a) || !a.closest('#settings-panel')) return;
    if (!App.keyboardFocus()) return;
    const b = bar.getBoundingClientRect(), content = document.getElementById('content');
    if (!b.height || !content) return;
    const over = a.getBoundingClientRect().bottom + 8 - b.top;
    if (over > 0) content.scrollTop += Math.ceil(over);
};

// --- The groups (#720) ----------------------------------------------------
// One group of sections at a time, chosen from the list down the left, as
// QuickBooks Desktop's Preferences window does. The list is built from the
// sections on the page: each .settings-section carries data-group="<key>",
// and GROUPS below holds the keys in the list's order with their names. A
// new setting is its section plus that one word; nothing else is
// registered. Every section stays in the DOM, inside the one form (My
// Preferences beside it, in the same panel), so Save Settings, the
// unsaved-changes guard and the role rules see them all as before; a group
// that is not showing is only hidden (the settings-off class). A group none
// of whose sections is shown for this sign-in or company type is left out
// of the list, and comes into it when a loader shows one (Users, Recent
// sends). A section without a known group is shown in every group (and
// fails tests/test_settings_groups.py), never hidden.
SettingsPage.GROUPS = [
    { key: 'company', label: 'Company' },
    { key: 'sales', label: 'Sales documents' },
    { key: 'email', label: 'Email' },
    { key: 'payroll', label: 'Payroll and checks' },
    { key: 'lists', label: 'Lists' },
    { key: 'connections', label: 'Connections' },
    { key: 'preferences', label: 'Preferences' },
    { key: 'security', label: 'Users and security' },
    { key: 'backups', label: 'Backups' },
];

// the group last opened on this computer
SettingsPage._GROUP_STORE = 'slowbooks-settings-group';
SettingsPage._group = 'company';  // the group asked for
SettingsPage._shown = null;       // the group showing (the one asked for, if it is listed)
SettingsPage._pendingFocus = null;

SettingsPage._groupNavHtml = function () {
    return '<div class="settings-nav" id="settings-nav">'
        + '<label for="settings-find" id="settings-find-label" class="settings-find-label">Find a setting</label>'
        + '<input type="search" id="settings-find" class="settings-find" autocomplete="off" spellcheck="false"'
        + ' aria-describedby="settings-find-status">'
        + '<div id="settings-find-status" class="settings-find-status" role="status" aria-live="polite"></div>'
        + '<div class="settings-groups" id="settings-groups" role="tablist" aria-label="Settings groups" aria-orientation="vertical"></div>'
        + '</div>';
};

SettingsPage._groupLabel = function (key) {
    const g = SettingsPage.GROUPS.find(x => x.key === key);
    return g ? g.label : '';
};

// The section's group, or null for one without a known group.
SettingsPage._groupOf = function (section) {
    const key = section && section.getAttribute ? section.getAttribute('data-group') : null;
    return SettingsPage.GROUPS.some(g => g.key === key) ? key : null;
};

SettingsPage._sections = function () {
    const root = document.getElementById('settings-layout');
    return root && typeof root.querySelectorAll === 'function'
        ? [...root.querySelectorAll('.settings-section')] : [];
};

// Shown for this sign-in and company type: not hidden by its loader
// (hidden, display:none) or by the role rules (the hidden class).
SettingsPage._available = function (section) {
    return !section.hidden && section.style.display !== 'none' && !section.classList.contains('hidden');
};

// A loader shows or hides its section here, never by its own hand (#725):
// with the group switch, this is the one path that changes which sections
// show. `on` with content just drawn into the section counts as a change
// when the section is showing (the backups list, the users list).
SettingsPage._showSection = function (section, on) {
    if (!section) return;
    section.hidden = !on;
    if (section.style && section.style.display === 'none') section.style.display = '';
    if (typeof document === 'undefined') return;  // the node tests' stand-in page
    SettingsPage._refreshGroups({ filled: on ? section : null });
};

// The accessibility tree under the panel (#725, NEW-210). On the Mac
// (WKWebView), after some switches of group a client walking AXChildren
// found settings-panel empty while its fields were on screen: WebKit's
// accessibility cache missed a children-changed notice for a section shown
// or hidden inside the form, and nothing dirtied the panel again. When the
// set of sections showing changes, the panel's renderer is dropped and
// restored in one synchronous turn (hidden, a layout read, shown), so
// WebKit builds that part of the tree again. Nothing paints in between, the
// inputs are the same elements (their values and the form's state are
// untouched), every scroll position is put back, and while focus is inside
// the panel it is left alone until focus leaves it (hiding a focused
// field's box would drop its focus). Only Apple's WebKit: WebView2 and
// Chromium-based browsers never lost the tree, and see no change.
SettingsPage._AX_REBUILD = null;   // null: by the engine; true/false: forced (tests)
SettingsPage._showing = null;      // which sections showed at the last refresh
SettingsPage._treeStale = false;   // a rebuild waits for focus to leave the panel
SettingsPage._treeRebuilds = 0;    // how many were made (tests)

SettingsPage._needsTreeRebuild = function () {
    if (SettingsPage._AX_REBUILD !== null) return !!SettingsPage._AX_REBUILD;
    const ua = (typeof navigator !== 'undefined' && navigator.userAgent) || '';
    return /AppleWebKit/.test(ua) && !/Chrome|Chromium|CriOS|Edg|Android/.test(ua);
};

SettingsPage._rebuildPanelTree = function () {
    const panel = document.getElementById('settings-panel');
    if (!panel || !SettingsPage._needsTreeRebuild()) return;
    if (SettingsPage._focusInPanel(panel)) { SettingsPage._treeStale = true; return; }
    SettingsPage._treeStale = false;
    const content = document.getElementById('content');
    const scrolled = [content, document.scrollingElement, ...panel.querySelectorAll('*')]
        .filter(el => el && (el.scrollTop || el.scrollLeft))
        .map(el => [el, el.scrollTop, el.scrollLeft]);
    // The panel leaves the document and comes back in the same turn, the
    // same node with the same children, values and listeners: WebKit drops
    // its accessibility object for the panel and builds a new one, children
    // and all. Hiding and showing the panel in place did not (the 2.31.9
    // gate on the Mac: the panel's children list stayed empty).
    const parent = panel.parentNode;
    const next = panel.nextSibling;
    parent.removeChild(panel);
    void parent.offsetHeight;  // the layout read: the old object goes now
    parent.insertBefore(panel, next);
    for (const [el, top, left] of scrolled) {
        if (el.scrollTop !== top) el.scrollTop = top;
        if (el.scrollLeft !== left) el.scrollLeft = left;
    }
    SettingsPage._treeRebuilds += 1;
};

// Focus on something drawn in the panel. A field of a group just switched
// away from can keep focus with no box (a click on a tab in WebKit does not
// move focus to the tab); dropping the panel's box changes nothing for it.
SettingsPage._focusInPanel = function (panel) {
    const a = document.activeElement;
    return !!a && panel.contains(a) && a.getClientRects().length > 0;
};

// The rebuild put off while focus was in the panel, made once it leaves.
SettingsPage._onPanelFocusOut = function (e) {
    const panel = e.currentTarget;
    if (!SettingsPage._treeStale || (e.relatedTarget && panel.contains(e.relatedTarget))) return;
    setTimeout(() => {
        if (SettingsPage._treeStale && !SettingsPage._focusInPanel(panel)) SettingsPage._rebuildPanelTree();
    }, 0);
};

SettingsPage._findWords = function () {
    const box = document.getElementById('settings-find');
    return box ? box.value.trim().toLowerCase().split(/\s+/).filter(Boolean) : [];
};

// A section matches when every word typed is in its headings or in the
// names of its fields.
SettingsPage._matches = function (section, words) {
    // the notes behind the tortoises too (#977): a description that moved
    // off the page is still something to find its setting by
    const text = [...section.querySelectorAll('h3, h4, label, legend, .hint-pop')].map(el => el.textContent)
        // not the tortoises' "About ..." (#977): every section has one
        .concat([...section.querySelectorAll('[aria-label]:not([data-hint]):not(.hint-pop)')].map(el => el.getAttribute('aria-label')))
        .join(' ').toLowerCase().replace(/\s+/g, ' ');
    return words.every(w => text.includes(w));
};

SettingsPage._initGroups = function () {
    const tabs = document.getElementById('settings-groups');
    const sections = SettingsPage._sections();
    if (!tabs || !sections.length) return;
    let stored = null;
    try { stored = window.localStorage ? window.localStorage.getItem(SettingsPage._GROUP_STORE) : null; } catch (e) { stored = null; }
    SettingsPage._group = SettingsPage.GROUPS.some(g => g.key === stored) ? stored : 'company';
    SettingsPage._pendingFocus = null;
    SettingsPage._showing = null;
    SettingsPage._treeStale = false;
    const panel = document.getElementById('settings-panel');
    if (panel) panel.addEventListener('focusout', SettingsPage._onPanelFocusOut);
    for (const s of sections) {
        if (!SettingsPage._groupOf(s)) {
            console.warn('Settings: a section has no known data-group, so it shows in every group', s.id || s.textContent.trim().slice(0, 40));
        }
    }
    const present = new Set(sections.map(SettingsPage._groupOf));
    tabs.innerHTML = SettingsPage.GROUPS.filter(g => present.has(g.key)).map(g =>
        '<button type="button" role="tab" class="settings-tab" id="settings-tab-' + g.key + '"'
        + ' data-group-tab="' + g.key + '" aria-controls="settings-panel" aria-selected="false" tabindex="-1">'
        + '<span class="settings-tab-label">' + escapeHtml(g.label) + '</span>'
        + '<span class="settings-tab-dot" aria-hidden="true" hidden></span>'
        + '</button>').join('');
    tabs.addEventListener('click', SettingsPage._onTabClick);
    tabs.addEventListener('keydown', SettingsPage._onTabKey);
    const find = document.getElementById('settings-find');
    if (find) {
        find.addEventListener('input', () => SettingsPage._refreshGroups());
        find.addEventListener('keydown', SettingsPage._onFindKey);
    }
    // Save refused by a field in a group that is not showing (a half-typed
    // closing date): that group opens, so the browser can focus the field
    // and say what is wrong; "An invalid form control is not focusable"
    // is never the answer. invalid does not bubble: caught on the way down.
    const form = document.getElementById('settings-form');
    if (form) form.addEventListener('invalid', SettingsPage._onInvalid, true);
    // the list follows the sections: a loader showing or hiding one, the
    // role rules hiding the administrator's
    if (SettingsPage._groupObserver) SettingsPage._groupObserver.disconnect();
    SettingsPage._groupObserver = null;
    if (typeof MutationObserver === 'function') {
        const layout = document.getElementById('settings-layout');
        SettingsPage._groupObserver = new MutationObserver(muts => {
            if (muts.some(m => m.target.classList && m.target.classList.contains('settings-section'))) {
                SettingsPage._refreshGroups();
            }
        });
        SettingsPage._groupObserver.observe(layout, {
            attributes: true, subtree: true, attributeFilter: ['hidden', 'style', 'class'],
        });
    }
    // the list is never taller than the window it scrolls in, at any text
    // size (a root zoom scales 100vh with it, so it is measured)
    if (SettingsPage._navResize) SettingsPage._navResize.disconnect();
    SettingsPage._navResize = null;
    const content = document.getElementById('content');
    if (content && typeof ResizeObserver === 'function') {
        SettingsPage._navResize = new ResizeObserver(() => SettingsPage._fitNav());
        SettingsPage._navResize.observe(content);
    }
    SettingsPage._fitNav();
    SettingsPage._orientGroups();
    SettingsPage._refreshGroups();
};

// A page 900px wide or less puts the list above the panel (style.css,
// .settings-layout--narrow). Measured, not a width rule: a width rule reads
// the window, and at 150% a 900px window is a 600px page (W-126). The
// body's own width is the page's, in the page's pixels, at any text size.
SettingsPage.NARROW = 900;
SettingsPage._narrow = function () {
    const page = document.body ? document.body.clientWidth : 0;
    return page > 0 && page <= SettingsPage.NARROW;
};

SettingsPage._fitNav = function () {
    const nav = document.getElementById('settings-nav');
    const panel = document.getElementById('settings-panel');
    const content = document.getElementById('content');
    if (!nav || !panel || !content) return;
    const layout = document.getElementById('settings-layout');
    const narrow = SettingsPage._narrow();
    if (layout && layout.classList.contains('settings-layout--narrow') !== narrow) {
        layout.classList.toggle('settings-layout--narrow', narrow);
        SettingsPage._orientGroups();
    }
    const cs = getComputedStyle(content);
    const room = Math.max(120, Math.floor(content.clientHeight
        - (parseFloat(cs.paddingTop) || 0) - (parseFloat(cs.paddingBottom) || 0)));
    // the panel is at least the window's height, so Save sits at its bottom
    panel.style.minHeight = room + 'px';
    nav.style.maxHeight = getComputedStyle(nav).position === 'sticky' ? room + 'px' : '';
};

// On a narrow page the list is a row above the panel (_fitNav, which
// runs again as the page's size changes).
SettingsPage._orientGroups = function () {
    const tabs = document.getElementById('settings-groups');
    const layout = document.getElementById('settings-layout');
    if (!tabs || !layout) return;
    const narrow = layout.classList.contains('settings-layout--narrow');
    tabs.setAttribute('aria-orientation', narrow ? 'horizontal' : 'vertical');
};

// Show the group asked for (or the first listed, while it has nothing to
// show), or, with words in Find a setting, the sections of every group
// that match them. `filled`: a section a loader has just drawn into.
SettingsPage._refreshGroups = function (opts = {}) {
    const tabs = document.getElementById('settings-groups');
    const sections = SettingsPage._sections();
    if (!tabs || !sections.length) return;
    const listed = new Set();
    for (const s of sections) {
        const g = SettingsPage._groupOf(s);
        if (g && SettingsPage._available(s)) listed.add(g);
    }
    let shown = listed.has(SettingsPage._group) ? SettingsPage._group : null;
    if (!shown) shown = (SettingsPage.GROUPS.find(g => listed.has(g.key)) || {}).key || null;
    SettingsPage._shown = shown;
    const words = SettingsPage._findWords();
    const found = [];
    for (const s of sections) {
        const g = SettingsPage._groupOf(s);
        let on;
        if (words.length) {
            on = SettingsPage._matches(s, words);
            if (on && SettingsPage._available(s)) found.push(s);
        } else {
            on = !g || g === shown;
        }
        if (s.classList.contains('settings-off') === on) s.classList.toggle('settings-off', !on);
    }
    tabs.querySelectorAll('[role="tab"]').forEach(t => {
        const key = t.getAttribute('data-group-tab');
        t.hidden = !listed.has(key);
        const selected = key === shown;
        t.setAttribute('aria-selected', selected ? 'true' : 'false');
        t.tabIndex = selected ? 0 : -1;
    });
    const nav = document.getElementById('settings-nav');
    if (nav) nav.classList.toggle('settings-nav--finding', words.length > 0);
    const panel = document.getElementById('settings-panel');
    if (panel) {
        panel.setAttribute('aria-labelledby', words.length || !shown ? 'settings-find-label' : 'settings-tab-' + shown);
    }
    const status = document.getElementById('settings-find-status');
    if (status) {
        let text = '';
        if (words.length && !found.length) {
            text = 'No setting matches what you typed.';
        } else if (words.length) {
            const groups = SettingsPage.GROUPS.filter(g => found.some(s => SettingsPage._groupOf(s) === g.key))
                .map(g => g.label);
            const what = found.length === 1 ? 'One section matches' : found.length + ' sections match';
            text = what + (groups.length ? ', in ' + SettingsPage._andList(groups) : '') + '.';
        }
        if (status.textContent !== text) status.textContent = text;
    }
    // the set showing changed (not at the first draw, whose tree is new):
    // WebKit's accessibility tree under the panel is built again
    const showing = sections.map((s, i) =>
        !s.classList.contains('settings-off') && SettingsPage._available(s) ? i : '').join(',');
    const filled = opts.filled && sections.includes(opts.filled)
        && !opts.filled.classList.contains('settings-off') && SettingsPage._available(opts.filled);
    const before = SettingsPage._showing;
    SettingsPage._showing = showing;
    if (before !== null && (before !== showing || filled)) SettingsPage._rebuildPanelTree();
    // a jump to a section a loader had not shown yet (Recent sends)
    const pending = SettingsPage._pendingFocus;
    if (pending && SettingsPage._available(pending.section)) {
        SettingsPage._pendingFocus = null;
        SettingsPage._land(pending.section, pending.target, pending.opts);
    }
};

// Open a group. `focus`: the switch was made from the keyboard, so focus
// goes to the group's first heading.
SettingsPage.openGroup = function (key, opts = {}) {
    if (!SettingsPage.GROUPS.some(g => g.key === key)) return;
    SettingsPage._group = key;
    SettingsPage._pendingFocus = null;
    try { if (window.localStorage) window.localStorage.setItem(SettingsPage._GROUP_STORE, key); } catch (e) { /* not remembered */ }
    const find = document.getElementById('settings-find');
    if (find && find.value) find.value = '';
    SettingsPage._refreshGroups();
    const first = SettingsPage._sections().find(s =>
        SettingsPage._groupOf(s) === SettingsPage._shown && SettingsPage._available(s));
    // the group starts at its top, wherever the last one was scrolled to
    const layout = document.getElementById('settings-layout');
    const content = document.getElementById('content');
    if (layout && content && layout.getBoundingClientRect().top < content.getBoundingClientRect().top) {
        SettingsPage._scrollTo(layout);
    }
    if (opts.focus && first) SettingsPage._focusHeading(first);
};

// Bring an element to the top of the page's scrolling content. Only that
// scrolls: scrollIntoView would move the window's shell too (its body
// hides its overflow but still scrolls), and leave the toolbars off the top.
SettingsPage._scrollTo = function (el) {
    const content = document.getElementById('content');
    if (!content || !content.contains(el)) { el.scrollIntoView({ block: 'start' }); return; }
    const pad = parseFloat(getComputedStyle(content).paddingTop) || 0;
    content.scrollTop += el.getBoundingClientRect().top - content.getBoundingClientRect().top - pad;
};

SettingsPage._focusHeading = function (section, heading) {
    const h = heading || section.querySelector('h3');
    if (!h) return;
    // a heading takes focus without becoming a Tab stop
    if (!h.hasAttribute('tabindex')) h.setAttribute('tabindex', '-1');
    h.focus({ preventScroll: true });
};

// Open the group of a section (or of anything in one) and land on it: its
// heading in view and focused. Other pages ask for this with the
// settings_focus hand-off (scrollToFocus).
SettingsPage.showSetting = function (target, opts = {}) {
    const el = typeof target === 'string' ? document.getElementById(target) : target;
    if (!el) return;
    const section = el.closest ? el.closest('.settings-section') : null;
    if (!section) { el.scrollIntoView({ block: 'start' }); return; }
    const key = SettingsPage._groupOf(section);
    if (key) SettingsPage.openGroup(key);
    if (SettingsPage._available(section)) SettingsPage._land(section, el, opts);
    else SettingsPage._pendingFocus = { section, target: el, opts };
};

SettingsPage._land = function (section, el, opts = {}) {
    SettingsPage._scrollTo(section);
    if (opts.focus === false) return;
    SettingsPage._focusHeading(section, /^H[1-6]$/.test(el.tagName) ? el : null);
};

SettingsPage._onTabClick = function (e) {
    const tab = e.target.closest('[role="tab"]');
    if (!tab) return;
    // Enter or Space on the tab (a click with no pointer behind it)
    SettingsPage.openGroup(tab.getAttribute('data-group-tab'), { focus: e.detail === 0 });
};

// Arrow keys move along the list (Home and End to its ends); Enter or
// Space opens the group the focus is on.
SettingsPage._onTabKey = function (e) {
    const tabs = [...document.querySelectorAll('#settings-groups [role="tab"]')].filter(t => !t.hidden);
    const at = tabs.indexOf(e.target.closest ? e.target.closest('[role="tab"]') : null);
    if (at < 0 || e.altKey || e.ctrlKey || e.metaKey) return;
    let to = null;
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') to = (at + 1) % tabs.length;
    else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') to = (at - 1 + tabs.length) % tabs.length;
    else if (e.key === 'Home') to = 0;
    else if (e.key === 'End') to = tabs.length - 1;
    if (to === null) return;
    e.preventDefault();
    tabs[to].focus();
};

// Escape empties Find a setting; Enter opens the first match's group.
SettingsPage._onFindKey = function (e) {
    const box = e.target;
    if (e.key === 'Escape' && box.value) {
        e.preventDefault();
        e.stopPropagation();
        box.value = '';
        SettingsPage._refreshGroups();
    } else if (e.key === 'Enter') {
        e.preventDefault();
        const words = SettingsPage._findWords();
        const first = words.length && SettingsPage._sections().find(s =>
            SettingsPage._available(s) && SettingsPage._matches(s, words));
        if (first) SettingsPage.showSetting(first);
    }
};

SettingsPage._onInvalid = function (e) {
    // the browser checks every field and reports the first: only the first
    // of one check opens its group
    if (SettingsPage._invalidSeen) return;
    SettingsPage._invalidSeen = true;
    setTimeout(() => { SettingsPage._invalidSeen = false; }, 0);
    const field = e.target;
    const section = field.closest ? field.closest('.settings-section') : null;
    if (!section || !section.classList.contains('settings-off')) return;
    const key = SettingsPage._groupOf(section);
    if (key) SettingsPage.openGroup(key);
    // the browser focuses it and shows its message now that it is shown;
    // where it did not, do it here
    setTimeout(() => {
        if (document.activeElement === field) return;
        field.focus();
        if (typeof field.reportValidity === 'function') field.reportValidity();
    }, 0);
};

// The groups holding a change Save Settings would send.
SettingsPage._dirtyGroups = function () {
    if (!SettingsPage.isDirty()) return [];
    const form = document.getElementById('settings-form');
    if (!form || !form.elements || typeof form.elements.namedItem !== 'function') return [];
    const before = SettingsPage._snapshot || {};
    const now = SettingsPage._formData() || {};
    const groups = new Set();
    for (const name of new Set([...Object.keys(before), ...Object.keys(now)])) {
        if (before[name] === now[name]) continue;
        let el = form.elements.namedItem(name);
        if (el && !el.closest && el.length) el = el[0];
        const section = el && el.closest ? el.closest('.settings-section') : null;
        const key = SettingsPage._groupOf(section);
        if (key) groups.add(key);
    }
    return SettingsPage.GROUPS.map(g => g.key).filter(k => groups.has(k));
};

SettingsPage._showDirtyGroups = function () {
    const tabs = document.getElementById('settings-groups');
    if (!tabs || typeof tabs.querySelectorAll !== 'function') return;
    const dirty = SettingsPage._dirtyGroups();
    tabs.querySelectorAll('[role="tab"]').forEach(t => {
        const key = t.getAttribute('data-group-tab');
        const on = dirty.includes(key);
        t.classList.toggle('settings-tab--dirty', on);
        const dot = t.querySelector('.settings-tab-dot');
        if (dot) dot.hidden = !on;
        // the dot, in words, for a screen reader
        if (on) t.setAttribute('aria-label', SettingsPage._groupLabel(key) + ', unsaved changes');
        else t.removeAttribute('aria-label');
    });
    const count = document.getElementById('settings-dirty-groups');
    if (count) {
        const names = SettingsPage._andList(dirty.map(SettingsPage._groupLabel));
        count.textContent = !dirty.length ? ''
            : dirty.length === 1 ? '1 group has unsaved changes: ' + names + '.'
                : dirty.length + ' groups have unsaved changes: ' + names + '.';
    }
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
                    ${np ? `<button type="button" class="btn btn-sm btn-secondary" data-readonly-ok onclick="SettingsPage.editFund(${c.id})">Edit</button>` : ''}
                    ${c.is_system_default ? '' : `
                        <button type="button" class="btn btn-sm btn-secondary" data-write onclick="SettingsPage.renameClass(${c.id})">Rename</button>
                        <button type="button" class="btn btn-sm btn-secondary" data-write onclick="SettingsPage.toggleArchiveClass(${c.id}, ${!c.is_archived})">${c.is_archived ? 'Unarchive' : 'Archive'}</button>`}
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
            el.innerHTML = '<div style="font-size:11px; color:var(--text-muted);">No cost codes yet. Add your own, or load the standard divisions and common sections.</div>';
            return;
        }
        const parentSel = document.getElementById('new-cc-parent');
        if (parentSel) parentSel.innerHTML = '<option value="">(top level)</option>' + codes.filter(c => c.is_active).map(c => `<option value="${c.id}">${'\u00a0\u00a0'.repeat(c.depth || 0)}${escapeHtml(c.label)}</option>`).join('');
        el.innerHTML = `<div class="table-container"><table>
            <thead><tr><th scope="col">Code</th><th scope="col">Name</th><th scope="col">Level</th><th scope="col">Type</th><th scope="col">Default account</th><th scope="col">Status</th><th scope="col">Actions</th></tr></thead>
            <tbody>` + codes.map(c => `<tr>
                <td style="padding-left:${8 + (c.depth || 0) * 16}px">${c.depth ? '<span style="color:var(--text-muted)">└ </span>' : ''}<code>${escapeHtml(c.code)}</code></td>
                <td>${escapeHtml(c.name)}</td>
                <td>${escapeHtml(c.level || '')}</td>
                <td>${escapeHtml(c.cost_type)}</td>
                <td>${escapeHtml(c.account_name || '')}</td>
                <td>${c.is_active ? 'Active' : 'Inactive'}</td>
                <td class="actions">
                    <button type="button" class="btn btn-sm btn-secondary" data-write onclick="SettingsPage.renameCostCode(${c.id})">Rename</button>
                    <button type="button" class="btn btn-sm btn-secondary" data-write onclick="SettingsPage.toggleCostCode(${c.id}, ${!c.is_active})">${c.is_active ? 'Deactivate' : 'Activate'}</button>
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
        <div style="margin:0 0 6px 0">${SettingsPage._hintWithId("Paste CSV rows as <code>code,name,cost_type,parent_code</code> (header optional). Existing codes are updated, parents linked afterwards, so order doesn't matter.", 'Import Cost Codes', 'cc-import-csv-help')}</div>
        <textarea id="cc-import-csv" rows="12" aria-label="Cost code CSV rows" aria-describedby="cc-import-csv-help" style="width:100%;font-family:monospace;font-size:12px" placeholder="03,Concrete,subcontract,
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

// --- Trade packages (Settings > Trade Packages, construction only) -------
// A package is what a sub bids; a section is what costs post to. The mapping
// is a multi-select of the cost-code SECTIONS (level === 'section').
// Rendered only for a construction company (the house pattern, vendors.js):
// applyTerminology runs once at boot, before Settings renders, so a
// data-construction gate would never reveal it.
SettingsPage.tradePackagesSectionHtml = function () {
    if (!Terms.isConstruction()) return '';
    return `
                <div class="settings-section" data-group="lists" role="group" aria-labelledby="settings-h-trade-packages">
                    <div class="settings-h-row"><h3 id="settings-h-trade-packages">Trade Packages</h3>${hintList('Trade Packages', [
                        { id: 'settings-trade-packages-help', text: `Bid packages a general contractor awards — what a sub bids and
                        gets awarded. Each maps to the cost-code sections its costs
                        post to; a package can span divisions.` },
                    ])}</div>
                    <div style="display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap;" data-write>
                        <input type="text" id="new-tp-name" placeholder="Package name" style="width:220px;" aria-label="Trade package name">
                        <button type="button" class="btn btn-primary" onclick="SettingsPage.addTradePackage()">Add Trade Package</button>
                    </div>
                    <div id="trade-packages-list"></div>
                </div>`;
};

SettingsPage.loadTradePackages = async function () {
    const el = document.getElementById('trade-packages-list');
    if (!el) return;
    try {
        const [packages, codes] = await Promise.all([
            API.get('/trade-packages'),
            API.get('/cost-codes'),
        ]);
        const sections = codes.filter(c => c.level === 'section');
        if (!packages.length) {
            el.innerHTML = '<div style="font-size:11px; color:var(--text-muted);">No trade packages yet. Add one, or run construction setup to seed the defaults.</div>';
            return;
        }
        const sectionOptions = (selectedIds) => sections.map(s =>
            `<option value="${s.id}" ${selectedIds.includes(s.id) ? 'selected' : ''}>${escapeHtml(s.label)}</option>`).join('');
        el.innerHTML = `<div class="table-container"><table style="font-size:12px">
            <thead><tr><th scope="col">Package</th><th scope="col">Sections</th><th scope="col">Status</th><th scope="col">Actions</th></tr></thead>
            <tbody>` + packages.map(p => `<tr data-tp="${p.id}">
                <td>${escapeHtml(p.name)}</td>
                <td><select class="tp-sections" multiple size="4" style="min-width:220px" aria-label="Sections for ${escapeHtml(p.name)}" data-write>${sectionOptions(p.section_ids || [])}</select></td>
                <td>${p.is_active ? 'Active' : 'Inactive'}</td>
                <td class="actions">
                    <button type="button" class="btn btn-sm btn-primary" data-write onclick="SettingsPage.saveTradePackageSections(${p.id})">Save sections</button>
                    <button type="button" class="btn btn-sm btn-secondary" data-write onclick="SettingsPage.renameTradePackage(${p.id})">Rename</button>
                    <button type="button" class="btn btn-sm btn-danger" data-destructive data-write onclick="SettingsPage.deleteTradePackage(${p.id})">Delete</button>
                </td>
            </tr>`).join('') + `</tbody></table></div>`;
    } catch (err) {
        el.innerHTML = `<div style="color:var(--danger); font-size:11px;">${escapeHtml(err.message)}</div>`;
    }
};

SettingsPage.addTradePackage = async function () {
    const name = (document.getElementById('new-tp-name')?.value || '').trim();
    if (!name) { toast('Enter a package name', 'error'); return; }
    try {
        await API.post('/trade-packages', { name });
        document.getElementById('new-tp-name').value = '';
        toast('Trade package added');
        SettingsPage.loadTradePackages();
    } catch (err) { toast(err.message, 'error'); }
};

SettingsPage.renameTradePackage = async function (id) {
    const name = prompt('New trade package name:');
    if (!name || !name.trim()) return;
    try {
        await API.put(`/trade-packages/${id}`, { name: name.trim() });
        toast('Trade package renamed');
        SettingsPage.loadTradePackages();
    } catch (err) { toast(err.message, 'error'); }
};

SettingsPage.saveTradePackageSections = async function (id) {
    const tr = document.querySelector(`[data-tp="${id}"]`);
    if (!tr) return;
    const section_ids = [...tr.querySelector('.tp-sections').selectedOptions].map(o => parseInt(o.value));
    try {
        await API.put(`/trade-packages/${id}`, { section_ids });
        toast('Sections saved');
        SettingsPage.loadTradePackages();
    } catch (err) { toast(err.message, 'error'); }
};

SettingsPage.deleteTradePackage = async function (id) {
    if (!confirm('Delete this trade package?')) return;
    try {
        await API.del(`/trade-packages/${id}`);
        toast('Trade package deleted');
        SettingsPage.loadTradePackages();
    } catch (err) { toast(err.message, 'error'); }
};

// --- Cost types (Settings > Cost Types) ----------------------------------
SettingsPage._accountsCache = null;
SettingsPage._accountOptions = async function (selected) {
    if (!SettingsPage._accountsCache) { try { SettingsPage._accountsCache = await API.get('/accounts'); } catch (e) { SettingsPage._accountsCache = []; } }
    return '<option value="">--</option>' + SettingsPage._accountsCache.map(a => `<option value="${a.id}" ${selected === a.id ? 'selected' : ''}>${accountLabel(a, null, ' ')}</option>`).join('');
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
                <td><input type="number" step="0.01" class="ct-burden" value="${t.burden_pct ?? ''}" style="width:70px" placeholder="%" aria-label="Flat burden percent, ${escapeHtml(t.code)}"></td>
                <td>${t.is_labor ? `<select class="ct-burden-method" aria-label="Burden method, ${escapeHtml(t.code)}" title="Flat: the % above posts with each time entry. Payroll: the pay run distributes actual employer taxes + job-routed benefit codes by hours — only for stubs built from time entries (Use approved time entries on the pay run); hours typed on a stub leave that employee's burden in the pool.">
                    <option value="flat" ${t.burden_method !== 'payroll' ? 'selected' : ''}>Flat %</option>
                    <option value="payroll" ${t.burden_method === 'payroll' ? 'selected' : ''}>Actual payroll</option>
                </select>` : '—'}</td>
                <td><select class="ct-default">${await SettingsPage._accountOptions(t.default_account_id)}</select></td>
                <td><select class="ct-offset">${await SettingsPage._accountOptions(t.offset_account_id)}</select></td>
                <td><select class="ct-burden-offset">${await SettingsPage._accountOptions(t.burden_offset_account_id)}</select></td>
                <td class="actions">
                    <button type="button" class="btn btn-sm btn-primary" data-write onclick="SettingsPage.saveCostType(${t.id})">Save</button>
                    <button type="button" class="btn btn-sm btn-secondary" data-write onclick="SettingsPage.toggleCostType(${t.id}, ${!t.is_active})">${t.is_active ? 'Deactivate' : 'Activate'}</button>
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
                    <button type="button" class="btn btn-sm btn-secondary" data-write onclick="SettingsPage.rateEquipment(${q.id})">Set rate</button>
                    <button type="button" class="btn btn-sm btn-secondary" data-write onclick="SettingsPage.toggleEquipment(${q.id}, ${!q.is_active})">${q.is_active ? 'Deactivate' : 'Activate'}</button>
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

