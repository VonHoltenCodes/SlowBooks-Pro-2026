// #977, Settings: draw the page the way it is drawn in the app (business,
// construction and nonprofit; a logo, saved secrets, an LLC, the payroll
// pieces' hints), plus the parts drawn later (AI Insights, Receipt Scanning
// with no engine, the earlier versions' files, the Fast Rack, the dialogs),
// and print each as one JSON object of named HTML strings for
// tests/test_hints_977_settings.py to read.
const fs = require('fs'), vm = require('vm');

const PLACEHOLDER = '*'.repeat(8);   // the server's fixed echo of a saved secret
const nodes = {};
const modals = [];
function context(company, settings) {
    const ctx = {
        console, JSON, Promise,
        setTimeout: () => 0,
        T: (s) => s,
        Terms: {
            text: (s) => s,
            isNonprofit: () => company === 'nonprofit',
            isConstruction: () => company === 'construction',
        },
        escapeHtml: (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]),
        formatDate: (d) => String(d),
        formatFileSize: (n) => n + ' B',
        formatCurrency: (n) => '$' + Number(n).toFixed(2),
        accountWords: (a) => (a.account_number ? a.account_number + ' - ' : '') + a.name,
        accountLabel: (a) => (a.account_number ? a.account_number + ' - ' : '') + a.name,
        App: { role: 'admin', isMac: () => false, accountRegisterHref: () => '#/', username: 'owner' },
        toast() {},
        openModal: (title, html) => modals.push({ title, html }),
        closeModal() {},
        $: (sel) => nodes[sel.replace(/^#/, '')] || null,
        document: { getElementById: (id) => nodes[id] || null },
        API: {
            get: async (url) => {
                if (url === '/settings') return settings;
                if (url === '/accounts') {
                    return [{ id: 1, name: 'Checking', account_number: '1000', account_type: 'asset', bank_kind: 'bank', is_active: true, balance: 0 }];
                }
                if (url === '/settings/payroll-accounts') {
                    return {
                        pieces: [
                            { key: 'payroll_net_pay_account_id', label: 'Net pay', hint: 'paid from the bank, or held in a liability', kinds: [], default: { label: '1000 Checking' } },
                            { key: 'payroll_wage_expense_account_id', label: 'Wages', kinds: [], default: { label: '6000 Payroll Expenses' } },
                        ],
                        choices: {},
                    };
                }
                if (url === '/uploads/logo') return { from_shared_folder: false };
                if (url === '/uploads/legacy') return { files: 3, bytes: 300, pending_companies: [] };
                if (url.startsWith('/email-templates/')) return { name: 'invoice_email', template_type: 'invoice', subject_template: 's', body_template: 'b' };
                if (url.startsWith('/invoices')) return [{ id: 1, invoice_number: '1001', customer_name: 'A' }];
                return [];
            },
        },
    };
    vm.createContext(ctx);
    for (const file of ['payment_terms.js', 'hint.js', 'fast_rack.js', 'settings.js']) {
        vm.runInContext(fs.readFileSync('app/static/js/' + file, 'utf8')
            + (file === 'settings.js' ? '\nthis.SettingsPage = SettingsPage;' : '')
            + (file === 'fast_rack.js' ? '\nthis.FastRack = FastRack;' : ''), ctx);
    }
    return ctx;
}

const base = {
    company_name: 'Harbor Light Bakery',
    company_logo_path: '/static/uploads/logo.png',
    entity_type: 'llc',
    smtp_password: PLACEHOLDER,
    closing_date_password: PLACEHOLDER,
    ach_company_account: PLACEHOLDER,
    stripe_secret_key: PLACEHOLDER,
    stripe_webhook_secret: PLACEHOLDER,
    paypal_client_secret: PLACEHOLDER,
    square_access_token: PLACEHOLDER,
    square_webhook_signature_key: PLACEHOLDER,
    invoice_reminders_last_run: '2026-11-18',
};

(async () => {
    const out = {};
    const business = context('business', { ...base });
    out.business = await business.SettingsPage.render();
    const construction = context('construction', { ...base, company_type: 'construction', estimate_style: 'line_markup' });
    out.construction = await construction.SettingsPage.render();
    const nonprofit = context('nonprofit', { ...base, company_type: 'nonprofit' });
    out.nonprofit = await nonprofit.SettingsPage.render();
    const plain = context('business', { company_name: 'No secrets yet' });
    // ... and the payroll accounts failed to load
    const get = plain.API.get;
    plain.API.get = async (url) => {
        if (url === '/settings/payroll-accounts') throw new Error('offline');
        return get(url);
    };
    out.plain = await plain.SettingsPage.render();

    const S = business.SettingsPage;
    out.ai = S._renderAiConfig({
        provider: 'cloudflare_worker',
        providers: [{ key: 'cloudflare_worker', label: 'Cloudflare Worker', needs_worker_url: true, free_tier_hint: 'Your own Worker', docs_url: 'https://example.com' }],
    });
    // Receipt Scanning with no engine: the status line is drawn in place
    nodes['ocr-status'] = { innerHTML: '', textContent: '' };
    business.API.get = async (url) => (url === '/ocr/status' ? { available: false } : []);
    await S.loadOcrStatus();
    out.ocr_unavailable = nodes['ocr-status'].innerHTML;
    // the earlier versions' files, when there are some
    nodes['settings-legacy-files'] = { innerHTML: '', hidden: true, classList: { toggle() {}, add() {}, remove() {} } };
    S._showSection = () => {};
    business.API.get = async () => ({ files: 3, bytes: 300, pending_companies: [] });
    await S.loadLegacyFiles();
    out.legacy = nodes['settings-legacy-files'].innerHTML;
    out.fast_rack = business.FastRack.editorHtml();
    // the dialogs
    S.showInvite();
    S.showCostCodeImport();
    business.API.get = async (url) => (url.startsWith('/invoices')
        ? [{ id: 1, invoice_number: '1001', customer_name: 'A' }]
        : { name: 'invoice_email', template_type: 'invoice', subject_template: 's', body_template: 'b' });
    await S.editTemplate(1);
    S._companyNameNow = async () => 'Harbor Light Bakery';
    S._companyFile = { desktop_key: true };
    await S.confirmEncryption(true);
    out.dialogs = modals.map((m) => m.html).join('\n');
    console.log(JSON.stringify(out));
})().catch((err) => { console.error(err && err.stack || err); process.exit(1); });
