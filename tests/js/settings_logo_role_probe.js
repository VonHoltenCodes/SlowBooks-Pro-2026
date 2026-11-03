// Render the Settings page for each role and print what its Company Logo
// section offers, as one JSON line per role.
const fs = require('fs'), vm = require('vm');

const settings = { company_name: 'Harbor Light Bakery', company_logo_path: '/api/uploads/logo/3', invoice_show_logo: 'true' };
const logo = { path: '/api/uploads/logo/3', filename: 'company_logo.png', from_shared_folder: false, missing: false };

const ctx = {
  console,
  JSON,
  setTimeout: () => 0,
  T: (s) => s,
  Terms: { text: (s) => s, isNonprofit: () => false, isConstruction: () => false },
  escapeHtml: (s) => String(s == null ? '' : s),
  API: { get: async (path) => (path === '/settings' ? settings : path === '/uploads/logo' ? logo : {}) },
  App: { role: 'admin' },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/payment_terms.js', 'utf8') + '\nthis.PaymentTerms = PaymentTerms;', ctx);
vm.runInContext(fs.readFileSync('app/static/js/settings.js', 'utf8') + '\nthis.SettingsPage = SettingsPage;', ctx);
const S = ctx.SettingsPage;

(async () => {
  for (const role of ['admin', 'bookkeeper', 'readonly']) {
    ctx.App.role = role;
    const html = await S.render();
    // by its heading's words: the section and its heading carry names for screen readers
    const start = html.indexOf('>Company Logo</h3>');
    const section = html.slice(start, html.indexOf('class="settings-section"', start));
    console.log(JSON.stringify({
      role,
      picker: section.includes('id="logo-upload"'),
      remove: section.includes('Remove logo</button>'),
      adminOnly: section.includes('Only an administrator can change the logo.'),
      preview: section.includes('id="company-logo-preview"'),
    }));
  }
})().catch((e) => { console.log(JSON.stringify({ error: String(e && e.stack || e) })); process.exit(1); });
