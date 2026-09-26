// Load desktop_shim.js as the Mac/Windows desktop window does, then click
// each tax-form button: every form must reach the native PDF viewer through
// a GET the shim intercepts (a blob: window opens nothing in WKWebView).
const fs = require('fs'), vm = require('vm');
const fetched = [], opened = [], toasts = [];
const win = {
  location: { href: 'http://127.0.0.1:3001/', origin: 'http://127.0.0.1:3001' },
  open: (url) => { opened.push('browser:' + url); return null; },
  addEventListener: () => {},
  pywebview: { api: {
    open_document_pdf: async (title) => { opened.push('native PDF viewer'); return { success: true, path: '/Reports/' + title }; },
    reveal_path: () => {},
  } },
};
const fields = {
  'w2-year': '2026', 'w2-employee': '1', 'f940-year': '2026', 'f941-year': '2026',
  'f941-quarter': '3', 'f1099-year': '2026', 'f1099-vendor': '4',
};
const ctx = {
  window: win, console, URL, btoa, Uint8Array, String, Promise,
  navigator: { userAgent: 'Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15' },
  document: {
    addEventListener: () => {},
    getElementById: (id) => (id in fields ? { value: fields[id] } : null),
  },
  setTimeout: () => 0,
  fetch: async (url, opts) => {
    fetched.push(`${(opts && opts.method) || 'GET'} ${url}`);
    return {
      ok: true, status: 200,
      headers: { get: (h) => (h === 'Content-Type' ? 'application/pdf' : 'inline; filename=form.pdf') },
      arrayBuffer: async () => new Uint8Array([37, 80, 68, 70]).buffer,
    };
  },
  toast: (m) => toasts.push(m), toastAction: (m) => toasts.push(m),
  API: { get: async () => [], post: async () => ({}) },
  escapeHtml: (s) => String(s), openModal: () => {}, closeModal: () => {}, statusBadge: (s) => s,
};
vm.createContext(ctx);
for (const f of ['desktop_shim.js', 'tax_forms.js', 'onboarding.js']) {
  vm.runInContext(fs.readFileSync('app/static/js/' + f, 'utf8').replace(/^const (\w+)/m, 'var $1'), ctx);
}
const tick = () => new Promise((r) => setImmediate(r));
(async () => {
  const clicks = [
    ['W-2', () => ctx.TaxFormsPage.generateW2()],
    ['W-3', () => ctx.TaxFormsPage.generateW3()],
    ['940', () => ctx.TaxFormsPage.generate940()],
    ['941', () => ctx.TaxFormsPage.generate941()],
    ['1099-NEC', () => ctx.TaxFormsPage.generate1099()],
    ['1096', () => ctx.TaxFormsPage.generate1096()],
    ['New-Hire', () => ctx.OnboardingPage.downloadReport(1)],
  ];
  for (const [label, click] of clicks) {
    const before = [fetched.length, opened.length];
    try { await click(); } catch (e) { fetched.push(`${label} threw ${e.message}`); }
    for (let i = 0; i < 5; i++) await tick();
    console.log(`${label}: ${fetched.slice(before[0]).join(', ') || 'no request'} -> ${opened.slice(before[1]).join(', ') || 'nothing opened'}`);
  }
})();
