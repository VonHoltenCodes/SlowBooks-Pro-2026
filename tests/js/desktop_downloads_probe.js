// Downloads in the desktop app. Part one drives desktop_shim.js (the shell's
// window.open and link clicks) against canned answers of each kind and
// records which native bridge call each one reached. Part two drives the
// IIF page's export, which fetches the file itself, in the desktop app and
// in a browser. Prints one JSON object.
const fs = require('fs'), vm = require('vm');

const ORIGIN = 'http://127.0.0.1:3001';
const answers = {
  '/api/attachments/download/1': { type: 'image/jpeg', disposition: 'inline; filename="receipt.jpg"', body: 'JPEGDATA' },
  '/api/attachments/download/2': { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', disposition: "inline; filename*=utf-8''mileage%20log.xlsx", body: 'PK..' },
  '/api/iif/export/all': { type: 'text/plain; charset=windows-1252', disposition: 'inline; filename="slowbooks_export.iif"', body: '!ACCNT\tNAME' },
  '/api/csv/export/customers': { type: 'text/csv; charset=utf-8', disposition: 'inline; filename=customers.csv', body: 'Name\nAcme' },
  '/api/invoices/1/pdf': { type: 'application/pdf', disposition: 'inline; filename=Invoice_1001.pdf', body: '%PDF-1.7' },
  '/api/invoices/1/print-preview': { type: 'text/html; charset=utf-8', disposition: '', body: '<html>Invoice</html>' },
};

function response(url) {
  const a = answers[new URL(url, ORIGIN).pathname];
  const headers = { 'content-type': a.type, 'content-disposition': a.disposition };
  const bytes = Buffer.from(a.body);
  const blob = { size: bytes.length, type: a.type, arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.length) };
  return {
    ok: true, status: 200,
    headers: { get: (k) => headers[k.toLowerCase()] || null },
    blob: async () => blob,
    arrayBuffer: async () => blob.arrayBuffer(),
    text: async () => a.body,
    json: async () => JSON.parse(a.body),
  };
}

async function shim() {
  const bridge = [];
  const fetched = [];
  let clickHandler = null;
  const api = {
    save_document_file: async (name, b64) => { bridge.push(['save_document_file', name, Buffer.from(b64, 'base64').toString()]); return { success: true, path: '/Documents/SlowBooks Pro/Reports/' + name }; },
    open_document_pdf: async (name) => { bridge.push(['open_document_pdf', name]); return { success: true, path: '/Documents/SlowBooks Pro/Documents/' + name }; },
    open_document_html: async (title, html) => { bridge.push(['open_document_html', html]); return { success: true }; },
    reveal_path: async () => ({ success: true }),
  };
  const win = {
    location: { href: ORIGIN + '/#/', origin: ORIGIN },
    open: () => null,
    addEventListener() {},
    pywebview: { api },
  };
  const ctx = {
    console, URL, Promise, Buffer, btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
    Uint8Array, String,
    window: win,
    navigator: { userAgent: 'node' },
    setTimeout: (fn, ms) => (ms > 1000 ? 0 : setTimeout(fn, ms)),
    document: { addEventListener: (type, fn) => { if (type === 'click') clickHandler = fn; } },
    fetch: async (url, opts) => { fetched.push([url, (opts && opts.headers) || {}]); return response(url); },
    toast() {}, toastAction() {},
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync('app/static/js/desktop_shim.js', 'utf8'), ctx, { filename: 'desktop_shim.js' });

  const out = {};
  for (const url of Object.keys(answers)) {
    bridge.length = 0;
    win.open(url, '_blank');
    await new Promise((r) => setTimeout(r, 20));
    out[url] = bridge.slice();
  }
  out.headers_sent = fetched.every(([, h]) => h['X-Slowbooks-Desktop'] === '1');

  // A blob: link (a file the page already holds) is left to the web view.
  fetched.length = 0;
  let prevented = false;
  const blobLink = { href: 'blob:' + ORIGIN + '/5f0c', closest: () => blobLink };
  clickHandler({ target: blobLink, preventDefault: () => { prevented = true; } });
  await new Promise((r) => setTimeout(r, 20));
  out.blob_link = { prevented, fetched: fetched.length };

  const saver = win.SlowbooksDesktop && win.SlowbooksDesktop.saveFile;
  const file = { arrayBuffer: async () => new Uint8Array([33, 72, 68, 82]).buffer }; // "!HDR"
  bridge.length = 0;
  out.saveFile_desktop = saver ? await saver(file, 'a.iif') : null;
  out.saveFile_bridge = bridge.slice();
  delete win.pywebview;
  out.saveFile_browser = saver ? await saver(file, 'a.iif') : null;
  return out;
}

async function iifExport(desktop) {
  const saved = [];
  const clicked = [];
  const toasts = [];
  const fetched = [];
  const win = {};
  if (desktop) win.SlowbooksDesktop = { saveFile: async (blob, name) => { saved.push(name); return true; } };
  const ctx = {
    console, Promise, setTimeout,
    window: win,
    URL: { createObjectURL: () => 'blob:' + ORIGIN + '/1', revokeObjectURL() {} },
    document: { createElement: () => ({ click() { clicked.push(this.download); } }) },
    fetch: async (url, opts) => { fetched.push((opts && opts.headers) || {}); return response(url); },
    $: () => null, $$: () => [],
    toast: (m) => toasts.push(m),
    escapeHtml: (s) => String(s),
    App: { setStatus() {} },
    API: { responseError: async () => 'refused' },
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync('app/static/js/iif.js', 'utf8') + '\nthis.IIFPage = IIFPage;', ctx);
  await ctx.IIFPage._download('/api/iif/export/all', 'fallback.iif');
  return { header: (fetched[0] || {})['X-Slowbooks-Desktop'] || null, saved, clicked, toasts };
}

(async () => {
  const out = { shim: await shim(), iif_desktop: await iifExport(true), iif_browser: await iifExport(false) };
  console.log(JSON.stringify(out));
  process.exit(0);
})();
