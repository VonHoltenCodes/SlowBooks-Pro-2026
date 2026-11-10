// Evaluate credit_memos.js and vendor_credits.js with a small fake DOM and
// run their Apply Credit dialogs (NEW-217, 2.31.11): a row that is not in
// whole cents, or more than the credit remaining, sends nothing and is
// named; a refusal from the server part-way says what was applied before it
// and opens the dialog again; a clean run posts every row and closes.
// Prints one JSON object for the pytest side.
const fs = require('fs'), vm = require('vm');

let posts = [], toasts = [], gets = [], modal = null, navigated = [];
let values = {};  // input id → value typed
let postFail = null;  // (index) => message to throw, or null

function attr(tag, name) {
  const m = tag.match(new RegExp(`${name}="([^"]*)"`));
  return m ? m[1] : null;
}
function inputsOf(cls, key) {
  if (!modal) return [];
  return [...modal.html.matchAll(new RegExp(`<input [^>]*class="${cls} money"[^>]*>`, 'g'))].map((m) => {
    const id = attr(m[0], key);
    return { value: values[id] ?? '0', dataset: { inv: key === 'data-inv' ? id : undefined, bill: key === 'data-bill' ? id : undefined, label: attr(m[0], 'data-label'), balance: attr(m[0], 'data-balance') } };
  });
}
function remainingOf(id) {
  if (!modal) return null;
  const m = modal.html.match(new RegExp(`<strong id="${id}" data-remaining="([^"]*)"`));
  return m ? { dataset: { remaining: m[1] } } : null;
}

const memo = { id: 3, memo_number: 'CM-0003', customer_id: 7, balance_remaining: 1.0, status: 'issued' };
const invoices = [
  { id: 1164, invoice_number: '1164', status: 'sent', balance_due: 10.0 },
  { id: 1163, invoice_number: '1163', status: 'sent', balance_due: 4.0 },
];
const credit = { id: 9, credit_number: 'VC-0009', vendor_id: 4, balance_remaining: '1.00', status: 'issued' };
const bills = [
  { id: 51, bill_number: 'B-51', date: '2026-11-01', status: 'open', balance_due: '20.00' },
  { id: 52, bill_number: 'B-52', date: '2026-11-02', status: 'open', balance_due: '80.00' },
];

const ctx = {
  console, window: {}, location: { hash: '#/credit-memos' },
  App: { settings: {}, navigate: (h) => navigated.push(h), refresh: () => {}, username: 'admin' },
  T: (s) => s, Terms: { text: (s) => s, isConstruction: () => false },
  escapeHtml: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
  formatDate: (s) => s,
  formatCurrency: (n) => '$' + (Number(n) || 0).toFixed(2),
  toast: (msg, kind) => { toasts.push([msg, kind || 'ok']); },
  toastPosted: (msg) => { toasts.push([msg, 'posted']); },
  closeModal: () => { modal = null; },
  openModal: (title, html) => { modal = { title, html }; },
  $: (sel) => (sel === '#cm-apply-remaining' ? remainingOf('cm-apply-remaining') : sel === '#vc-apply-remaining' ? remainingOf('vc-apply-remaining') : null),
  $$: (sel) => (sel === '.apply-amt' ? inputsOf('apply-amt', 'data-inv') : sel === '.vc-apply-amt' ? inputsOf('vc-apply-amt', 'data-bill') : []),
  fetchAllPages: async (path) => { gets.push(path); return path.startsWith('/invoices') ? invoices : bills; },
  API: {
    get: async (path) => { gets.push(path); if (path === '/credit-memos/3') return memo; if (path === '/vendor-credits/9') return credit; throw new Error('unexpected GET ' + path); },
    post: async (path, body) => { posts.push([path, body]); const fail = postFail && postFail(posts.length - 1); if (fail) throw new Error(fail); return {}; },
  },
  document: { querySelector: () => null, querySelectorAll: () => [] },
  classIdFromForm: () => null, SalesLines: {}, TxnWarn: {}, ClassWarn: {}, CostCodes: { any: () => false }, Nonprofit: {},
  AccountQuickAdd: undefined, Replace: undefined, PurchaseAccounts: {}, PurchaseLines: {}, ItemQuickAdd: {}, TxnEdit: {},
  formatNumber: (n) => String(n), todayISO: () => '2026-11-09', LineOrder: { cellHtml: () => '', headHtml: () => '', bind: () => {} },
};
ctx.window = ctx;
vm.createContext(ctx);
for (const file of ['app/static/js/credit_memos.js', 'app/static/js/vendor_credits.js']) {
  const code = fs.readFileSync(file, 'utf8');
  vm.runInContext(code + `\n;this.__exports = Object.assign(this.__exports || {}, { CreditMemosPage: typeof CreditMemosPage !== 'undefined' ? CreditMemosPage : undefined, VendorCreditsPage: typeof VendorCreditsPage !== 'undefined' ? VendorCreditsPage : undefined, CreditApply: typeof CreditApply !== 'undefined' ? CreditApply : undefined });`, ctx, { filename: file });
}
const { CreditMemosPage, VendorCreditsPage } = ctx.__exports;
ctx.CreditApply = ctx.__exports.CreditApply;  // vendor_credits.js reaches it as a global, as in the page

const out = {};
async function reset() { posts = []; toasts = []; gets = []; navigated = []; values = {}; postFail = null; modal = null; }

(async () => {
  // 1. a half-cent row: nothing sent, the row named, the dialog still open
  await reset(); await CreditMemosPage.showApply(3);
  values = { 1164: '0.50', 1163: '0.005' };
  await CreditMemosPage.doApply(3);
  out.halfCent = { posts: posts.length, toast: toasts[toasts.length - 1], open: !!modal };

  // 2. more than the credit remaining: nothing sent
  await reset(); await CreditMemosPage.showApply(3);
  values = { 1164: '0.60', 1163: '0.50' };
  await CreditMemosPage.doApply(3);
  out.overRemaining = { posts: posts.length, toast: toasts[toasts.length - 1], open: !!modal };

  // 3. the server refuses the second row: the first stays applied, the
  //    sentence says so, and the dialog is opened again (a second GET)
  await reset(); await CreditMemosPage.showApply(3);
  values = { 1164: '0.50', 1163: '0.01' };
  postFail = (i) => (i === 1 ? 'Amount exceeds credit balance' : null);
  await CreditMemosPage.doApply(3);
  out.refusedMidway = { posts: posts.map(p => p[1]), toast: toasts[toasts.length - 1], reopened: gets.filter(g => g === '/credit-memos/3').length, open: !!modal, navigated };

  // 4. a clean run: both rows posted, closed, back to the list
  await reset(); await CreditMemosPage.showApply(3);
  values = { 1164: '0.50', 1163: '0.01' };
  await CreditMemosPage.doApply(3);
  out.clean = { posts: posts.map(p => p[1]), toast: toasts[toasts.length - 1], open: !!modal, navigated };

  // 5. the vendor credit's dialog, the same two ways
  await reset(); await VendorCreditsPage.showApply(9);
  values = { 51: '0.50', 52: '0.005' };
  await VendorCreditsPage.doApply(9);
  out.vcHalfCent = { posts: posts.length, toast: toasts[toasts.length - 1], open: !!modal };
  await reset(); await VendorCreditsPage.showApply(9);
  values = { 51: '0.50', 52: '0.01' };
  postFail = (i) => (i === 1 ? 'Amount exceeds credit balance' : null);
  await VendorCreditsPage.doApply(9);
  out.vcRefusedMidway = { posts: posts.map(p => p[1]), toast: toasts[toasts.length - 1], reopened: gets.filter(g => g === '/vendor-credits/9').length, open: !!modal };

  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e.stack || String(e)); process.exit(1); });
