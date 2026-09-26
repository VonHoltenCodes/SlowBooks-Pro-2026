// Every page that sends a request with fetch() itself — uploads post
// FormData, downloads read a file, so they can't use API.request — driven
// against one refused response. Prints, as JSON, what each one showed the
// person: {"<path>": "<message>", ...}.
//
//   node tests/js/direct_fetch_errors_probe.js '{"status": 422, "body": {...}}'
//
// A string body stands for a response that is not JSON (a proxy's page).
// Strip tags until nothing changes: one pass can leave a tag behind
// ('<scr<script>ipt>'), which is how CodeQL reads a single replace.
const stripTags = (s) => { let prev; s = String(s); do { prev = s; s = s.replace(/<[^>]*>/g, ''); } while (s !== prev); return s; };
const fs = require('fs'), vm = require('vm');

const refusal = JSON.parse(process.argv[2]);

function response(r) {
  return {
    status: r.status,
    ok: r.status >= 200 && r.status < 300,
    statusText: '',
    headers: { get: () => null },
    json: async () => {
      if (typeof r.body === 'string') throw new SyntaxError("Unexpected token '<', \"<html>\" is not valid JSON");
      return r.body;
    },
    text: async () => (typeof r.body === 'string' ? r.body : JSON.stringify(r.body)),
    blob: async () => ({}),
    arrayBuffer: async () => new ArrayBuffer(0),
  };
}

function el(extra) {
  return Object.assign({
    style: {}, value: '', textContent: '', innerHTML: '', disabled: false, checked: false,
    files: [{ name: 'upload.csv' }], dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
    querySelector: () => null, querySelectorAll: () => [], addEventListener() {},
  }, extra || {});
}

// A fresh page: api.js plus the page's own file(s), with the DOM and the
// helpers stubbed. Returns the context, what was toasted, and the elements.
function page(files, extras) {
  const toasts = [];
  const els = {};
  const $ = (sel) => (els[sel] = els[sel] || el());
  const ctx = Object.assign({
    console, setTimeout, clearTimeout, URL, URLSearchParams, Promise, ArrayBuffer, Uint8Array,
    FormData: class { append() {} },
    localStorage: { getItem: () => null },
    location: { hash: '#/' },
    navigator: { userAgent: 'node' },
    document: {
      getElementById: (id) => $('#' + id), querySelector: () => null, querySelectorAll: () => [],
      addEventListener() {}, createElement: () => el(), body: el({ appendChild() {} }),
    },
    fetch: async () => response(refusal),
    $, $$: () => [],
    toast: (msg) => toasts.push(String(msg)),
    toastAction: (msg) => toasts.push(String(msg)),
    escapeHtml: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
    openModal() {}, closeModal() {},
    App: { setStatus() {}, navigate() {} },
    T: (s) => s,
    Terms: { text: (s) => s, isNonprofit: () => false },
    formatCurrency: (n) => '$' + Number(n || 0).toFixed(2),
    formatDate: (s) => s,
  }, extras || {});
  ctx.window = ctx.window || {};
  vm.createContext(ctx);
  for (const f of ['app/static/js/api.js', ...files]) {
    vm.runInContext(fs.readFileSync(f, 'utf8'), ctx, { filename: f });
  }
  return { ctx, toasts, els, get: (name) => vm.runInContext(name, ctx) };
}

// "<div ...>File is required.</div>" -> "File is required."
const text = (html) => stripTags(html).replace(/&lt;/g, '<').replace(/&gt;/g, '>')
  .replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim();

async function thrown(fn) {
  try { await fn(); return '(nothing thrown)'; } catch (e) { return e.message; }
}

const last = (list) => (list.length ? list[list.length - 1] : '(nothing shown)');

const scenarios = {
  async 'app.js chart import'() {
    const p = page(['app/static/js/app.js'], { App: undefined });
    const App = p.get('App');
    return thrown(() => App._postChartImport({ file: { files: [{}] }, replace: { checked: false } }, true));
  },
  async 'app.js CSV import'() {
    const p = page(['app/static/js/app.js'], { App: undefined });
    await p.get('App').importCSV({ preventDefault() {}, target: { entity_type: { value: 'customers' }, file: { files: [{}] } } });
    return text(p.els['#csv-import-results'].innerHTML);
  },
  async 'fixed_assets.js import'() {
    const p = page(['app/static/js/fixed_assets.js']);
    await p.get('FixedAssetsPage').importCsv({ preventDefault() {} });
    return last(p.toasts);
  },
  async 'bills.js attachment'() {
    const p = page(['app/static/js/bills.js']);
    await p.get('BillsPage').uploadAttachment(1);
    return last(p.toasts);
  },
  async 'invoices.js attachment'() {
    const p = page(['app/static/js/invoices.js']);
    await p.get('InvoicesPage').uploadAttachment(1);
    return last(p.toasts);
  },
  async 'expenses.js attachment'() {
    const p = page(['app/static/js/expenses.js']);
    await p.get('ExpensesPage').uploadAttachment(1);
    return last(p.toasts);
  },
  async 'employees.js dropped document'() {
    const p = page(['app/static/js/employees.js']);
    await p.get('EmployeesPage')._uploadDroppedFile(1, { name: 'w4.pdf' });
    return last(p.toasts);
  },
  async 'employees.js document'() {
    const p = page(['app/static/js/employees.js']);
    await p.get('EmployeesPage')._uploadDocument(1);
    return last(p.toasts);
  },
  async 'iif.js QuickBooks report CSV'() {
    const p = page(['app/static/js/iif.js']);
    await p.get('IIFPage').importQbReportCsv();
    return last(p.toasts);
  },
  async 'iif.js export'() {
    const p = page(['app/static/js/iif.js']);
    await p.get('IIFPage')._download('/api/iif/export/all', 'slowbooks_export.iif');
    return last(p.toasts);
  },
  async 'iif.js validate'() {
    const p = page(['app/static/js/iif.js']);
    const IIF = p.get('IIFPage');
    IIF._selectedFile = { name: 'books.iif' };
    await IIF.validateFile();
    return last(p.toasts);
  },
  async 'iif.js import'() {
    const p = page(['app/static/js/iif.js']);
    const IIF = p.get('IIFPage');
    IIF._selectedFile = { name: 'books.iif' };
    IIF._validated = true;
    await IIF.importFile();
    return last(p.toasts);
  },
  async 'migration.js dry run'() {
    const p = page(['app/static/js/migration.js']);
    const M = p.get('MigrationPage');
    M._formData = () => new p.ctx.FormData();
    await M.dryRun();
    return last(p.toasts);
  },
  async 'migration.js import'() {
    const p = page(['app/static/js/migration.js']);
    const M = p.get('MigrationPage');
    M._formData = () => new p.ctx.FormData();
    M._dryRunOk = true;
    await M.doImport();
    return last(p.toasts);
  },
  async 'banking.js bank file preview'() {
    const p = page(['app/static/js/banking.js']);
    p.els['#ofx-file'] = el({ files: [{ name: 'statement.ofx' }] });
    await p.get('BankingPage').previewOFX({ preventDefault() {}, target: el() }, 1, 2);
    return text(p.els['#ofx-preview'].innerHTML);
  },
  async 'banking.js bank file import'() {
    const p = page(['app/static/js/banking.js']);
    p.els['#ofx-file'] = el({ files: [{ name: 'statement.ofx' }] });
    await p.get('BankingPage').confirmOFXImport(1, 2, null);
    return last(p.toasts);
  },
  async 'ocr.js receipt scan'() {
    const p = page(['app/static/js/ocr.js']);
    await p.get('ScanHelper').scan({ name: 'receipt.jpg' }, () => {});
    return p.els['#scan-status'].textContent;
  },
  async 'qbo.js import'() {
    const p = page(['app/static/js/qbo.js']);
    const Q = p.get('QBOPage');
    let shown = '(nothing shown)';
    Q._getChecked = () => ['customers'];
    Q._showResult = (_id, result) => { shown = result.errors.map(e => e.message).join(' | '); };
    await Q.importSelected();
    return shown;
  },
  async 'qbo.js export'() {
    const p = page(['app/static/js/qbo.js']);
    const Q = p.get('QBOPage');
    let shown = '(nothing shown)';
    Q._getChecked = () => ['customers'];
    Q._showResult = (_id, result) => { shown = result.errors.map(e => e.message).join(' | '); };
    await Q.exportSelected();
    return shown;
  },
  async 'desktop_shim.js document window'() {
    // The shell's window.open: fetches the document itself and says why
    // it could not.
    const win = {
      location: { href: 'http://127.0.0.1:3001/', origin: 'http://127.0.0.1:3001' },
      open: () => null,
      addEventListener() {},
      pywebview: { api: { open_document_pdf() {} } },
    };
    const toasts = [];
    const ctx = {
      console, URL, Promise, window: win, navigator: { userAgent: 'node' },
      setTimeout: (fn, ms) => (ms > 1000 ? 0 : setTimeout(fn, ms)),
      document: { addEventListener() {} },
      localStorage: { getItem: () => null },
      fetch: async () => response(refusal),
      toast: (msg) => toasts.push(String(msg)),
    };
    vm.createContext(ctx);
    for (const f of ['app/static/js/desktop_shim.js', 'app/static/js/api.js']) {
      vm.runInContext(fs.readFileSync(f, 'utf8'), ctx, { filename: f });
    }
    win.open('/api/invoices/1/pdf', '_blank');
    await new Promise((r) => setTimeout(r, 50));
    return last(toasts);
  },
};

(async () => {
  const only = process.argv[3];
  const out = {};
  for (const [name, run] of Object.entries(scenarios)) {
    if (only && name !== only) continue;
    try {
      out[name] = await run();
    } catch (e) {
      out[name] = `(probe error: ${e && e.stack ? e.stack.split('\n').slice(0, 2).join(' ') : e})`;
    }
  }
  console.log(JSON.stringify(out));
  process.exit(0);
})();
