// The Interoperability page's error boxes, rendered against a canned import
// answer with many errors (discussion #349): the count line, the Save the
// list button, every line in the box, and what Save the list saves.
// Prints JSON {many: {...}, one: {...}, iif: {...}, saved: {...}}.
const fs = require('fs'), vm = require('vm');

const errors = (n) => Array.from({ length: n }, (_, i) =>
  `Sales Receipt 110-09000${String(i + 1).padStart(2, '0')} of 02/04/2013 does not balance (27.93 over 1 lines)`);

function harness(result) {
  const els = {};
  const toasts = [];
  const anchors = [];
  const el = () => ({ innerHTML: '', value: '', checked: false, files: [{ name: 'x.csv' }], style: {}, classList: { add() {}, remove() {} } });
  const $ = (sel) => (els[sel] = els[sel] || el());
  const ctx = {
    // the page revokes the saved file's URL 30 s later; an unref'd timer
    // lets node exit when the probe is done instead of waiting it out
    // (the test took 30 s for 0.2 s of work, #1095)
    console, setTimeout: (fn, ms) => { const t = setTimeout(fn, ms); if (t.unref) t.unref(); return t; }, Promise, URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} },
    Blob: class { constructor(parts) { this.text = parts.join(''); } },
    FormData: class { append() {} },
    Date,
    window: {},
    location: { hash: '#/' },
    document: {
      addEventListener() {}, querySelector: () => null, querySelectorAll: () => [], getElementById: (id) => $('#' + id),
      createElement: () => { const a = { click() { anchors.push(a); } }; return a; },
    },
    $, $$: () => [],
    fetch: async () => ({ ok: true, json: async () => result }),
    escapeHtml: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    toast: (m) => toasts.push(String(m)),
    App: { setStatus() {}, navigate() {} },
    API: { responseError: async () => 'failed' },
    T: (s) => s,
  };
  vm.createContext(ctx);
  // the page's own local-date helper, from utils.js (the file is named for the local day)
  const utils = fs.readFileSync('app/static/js/utils.js', 'utf8');
  vm.runInContext(utils.match(/function todayISO\(\) \{[\s\S]*?\n\}/)[0], ctx, { filename: 'utils.js' });
  vm.runInContext(fs.readFileSync('app/static/js/iif.js', 'utf8'), ctx, { filename: 'iif.js' });
  vm.runInContext('this.IIFPage = IIFPage;', ctx);
  return { ctx, els, toasts, anchors };
}

const flat = (html) => html.replace(/\s+/g, ' ');
// Strip tags until nothing changes: one pass can leave a tag behind
// ('<scr<script>ipt>'), which is how CodeQL reads a single replace.
const stripTags = (s) => { let prev; s = String(s); do { prev = s; s = s.replace(/<[^>]*>/g, ''); } while (s !== prev); return s; };
function box(html) {
  const m = flat(html).match(/<div class="iif-errors">(.*?)<\/div>\s*$/);
  const inner = m ? m[1] : '';
  const count = (inner.match(/<strong>([^<]*)<\/strong>/) || [])[1] || '';
  // the count line is the first <div>; the error lines follow it
  const body = inner.slice(inner.indexOf('</div>') >= 0 && inner.startsWith('<div') ? inner.indexOf('</div>') + 6 : 0);
  const lines = body.split('<br>').map(stripTags).filter((s) => s.trim());
  return { count_line: count, button: /IIFPage\.saveErrors\('[a-z]+'\)/.test(inner), lines: lines.length, first: lines[0] || '' };
}

(async () => {
  const out = {};
  let h = harness({ detected: 'ledger', journals: 0, errors: errors(25), warnings: [] });
  await h.ctx.IIFPage.importQbReportCsv();
  out.many = box(h.els['#qbcsv-import-result'].innerHTML);
  // Save the list from that same page
  await h.ctx.IIFPage.saveErrors('qbcsv');
  const a = h.anchors[0] || {};
  out.saved = { name: a.download || '', lines: (h.ctx.IIFPage._errors.qbcsv || []).length, toast: h.toasts[h.toasts.length - 1] || '' };

  h = harness({ detected: 'ledger', journals: 0, errors: errors(1), warnings: [] });
  await h.ctx.IIFPage.importQbReportCsv();
  out.one = box(h.els['#qbcsv-import-result'].innerHTML);

  h = harness({});
  h.ctx.IIFPage._showImportResult({ imported: 0, errors: errors(22).map((message, i) => ({ row: i + 1, message })), warnings: [] });
  out.iif = box(h.els['#iif-import-result'].innerHTML);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
