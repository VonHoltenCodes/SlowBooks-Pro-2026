// Evaluate payments.js and invoices.js with a small fake DOM and a fake
// server that keeps balances, and drive the two loops that apply a credit
// one request at a time (#803): Receive Payment's Apply Credit with a credit
// memo across three invoices, and the invoice view's Apply Credit from three
// credit memos. The server refuses the second row once: the first stays
// applied and is said, the third is never sent, the dialog opens again on
// what remains, and a second Apply sends only what is left. A row over its
// balance sends nothing. Prints one JSON object for tests/test_apply_once_803.py.
const fs = require('fs'), vm = require('vm');

let posts = [], toasts = [], modal = null, refused = 0, refuseNext = null, refreshed = 0;
let state;

function attr(tag, name) {
  const m = tag.match(new RegExp(`${name}="([^"]*)"`));
  return m ? m[1] : null;
}
// the inputs of the dialog on screen, their value as rendered unless typed
let inputs = [];
function readInputs(cls) {
  if (!modal) return [];
  return [...modal.html.matchAll(new RegExp(`<input class="${cls} money"[^>]*>`, 'g'))].map((m) => ({
    value: attr(m[0], 'value') || '',
    dataset: {
      invoice: attr(m[0], 'data-invoice'), number: attr(m[0], 'data-number'), max: attr(m[0], 'data-max'),
      kind: attr(m[0], 'data-kind'), id: attr(m[0], 'data-id'), label: attr(m[0], 'data-label'),
    },
  }));
}

function fresh() {
  state = {
    invoices: [
      { id: 21, invoice_number: '1021', customer_id: 7, customer_name: 'Customer A', date: '2026-10-01', status: 'sent', balance_due: 30.0, currency: 'USD' },
      { id: 22, invoice_number: '1022', customer_id: 7, customer_name: 'Customer A', date: '2026-10-02', status: 'sent', balance_due: 30.0, currency: 'USD' },
      { id: 23, invoice_number: '1023', customer_id: 7, customer_name: 'Customer A', date: '2026-10-03', status: 'sent', balance_due: 30.0, currency: 'USD' },
    ],
    memos: [
      { kind: 'credit_memo', id: 1, date: '2026-09-01', number: 'CM-0001', method: '', currency: 'USD', amount: 10, available: 10 },
      { kind: 'credit_memo', id: 2, date: '2026-09-02', number: 'CM-0002', method: '', currency: 'USD', amount: 10, available: 10 },
      { kind: 'credit_memo', id: 3, date: '2026-09-03', number: 'CM-0003', method: '', currency: 'USD', amount: 10, available: 10 },
    ],
  };
}
function credits() {
  return {
    customer_id: 7, customer_name: 'Customer A', home_currency: 'USD',
    credits: state.memos.filter((m) => m.available > 0).map((m) => ({ ...m })),
  };
}

const ctx = {
  console, window: {}, location: { hash: '#/payments' },
  App: { settings: { home_currency: 'USD' }, navigate: () => {}, refresh: () => { refreshed += 1; } },
  T: (s) => s,
  Terms: { text: (s) => s, isNonprofit: () => false, isConstruction: () => false },
  escapeHtml: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
  formatDate: (s) => s,
  formatCurrency: (n) => '$' + (Number(n) || 0).toFixed(2),
  toast: (msg, kind) => { toasts.push([msg, kind || 'ok']); },
  closeModal: () => { modal = null; },
  openModal: (title, html) => {
    modal = { title, html };
    inputs = readInputs(html.includes('credit-take') ? 'credit-take' : 'credit-alloc');
  },
  $: () => null,
  $$: (sel) => (sel === '.credit-alloc' || sel === '.credit-take' ? inputs : []),
  fetchAllPages: async () => state.invoices.map((i) => ({ ...i })),
  API: {
    get: async (path) => {
      if (path === '/customers/7/credits') return credits();
      const m = path.match(/^\/invoices\/(\d+)$/);
      if (m) return { ...state.invoices.find((i) => i.id === Number(m[1])) };
      throw new Error('unexpected GET ' + path);
    },
    post: async (path, body) => {
      posts.push([path, body]);
      if (refuseNext !== null && posts.length - 1 === refuseNext) {
        refuseNext = null; refused += 1;
        throw new Error('Something on the server refused this row');
      }
      const m = path.match(/^\/credit-memos\/(\d+)\/apply$/);
      if (!m) throw new Error('unexpected POST ' + path);
      const memo = state.memos.find((c) => c.id === Number(m[1]));
      const inv = state.invoices.find((i) => i.id === body.invoice_id);
      memo.available = Math.round((memo.available - body.amount) * 100) / 100;
      inv.balance_due = Math.round((inv.balance_due - body.amount) * 100) / 100;
      return {};
    },
  },
  confirm: () => true,
  TxnWarn: { ok: async () => true }, ClassWarn: { ok: async () => true }, classIdFromForm: () => null,
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(
  fs.readFileSync('app/static/js/invoices.js', 'utf8') + '\n'
    + fs.readFileSync('app/static/js/payments.js', 'utf8')
    + '\nthis.InvoicesPage = InvoicesPage; this.PaymentsPage = PaymentsPage;',
  ctx,
);
const { InvoicesPage, PaymentsPage } = ctx;
const ev = { preventDefault() {} };
const sent = () => posts.map(([path, body]) => [path, body.invoice_id, body.amount]);
function reset() { posts = []; toasts = []; modal = null; inputs = []; refuseNext = null; refused = 0; refreshed = 0; fresh(); }

(async () => {
  const out = {};

  // 1. Receive Payment's Apply Credit: credit memo CM-0001 ($10) over the
  //    three invoices, $3 + $3 + $4 typed; the server refuses the second.
  reset();
  await PaymentsPage.showApplyCredit('credit_memo', 1, 7);
  inputs[0].value = '3.00'; inputs[1].value = '3.00'; inputs[2].value = '4.00';
  refuseNext = 1;
  await PaymentsPage.saveApplyCredit(ev, 'credit_memo', 1, 7, false);
  out.receiveRefused = {
    sent: sent(), toast: toasts[toasts.length - 1], open: !!modal,
    reopenedWith: inputs.map((i) => [i.dataset.number, i.dataset.max, i.value]),
    available: (modal && (modal.html.match(/<strong>([^<]*)<\/strong> available/) || [])[1]) || null,
    memoLeft: state.memos[0].available,
    balances: state.invoices.map((i) => i.balance_due),
  };
  //    a second Apply, on the dialog as it opened again, nothing retyped:
  //    only what is left
  posts = []; toasts = [];
  await PaymentsPage.saveApplyCredit(ev, 'credit_memo', 1, 7, false);
  out.receiveRetry = {
    sent: sent(), toast: toasts[toasts.length - 1],
    memoLeft: state.memos[0].available, balances: state.invoices.map((i) => i.balance_due),
  };

  // 2. A row over its invoice's balance sends nothing and is named.
  reset();
  await PaymentsPage.showApplyCredit('credit_memo', 1, 7);
  inputs[0].value = '3.00'; inputs[1].value = '30.01'; inputs[2].value = '';
  await PaymentsPage.saveApplyCredit(ev, 'credit_memo', 1, 7, false);
  out.receiveOverBalance = { posts: posts.length, toast: toasts[toasts.length - 1] };
  //    and the rows together over what the credit has
  inputs[0].value = '6.00'; inputs[1].value = '6.00'; inputs[2].value = '';
  await PaymentsPage.saveApplyCredit(ev, 'credit_memo', 1, 7, false);
  out.receiveOverCredit = { posts: posts.length, toast: toasts[toasts.length - 1] };

  // 3. The invoice view's Apply Credit: invoice 1021 ($30) from the three
  //    memos, $10 each as filled; the server refuses the second.
  reset();
  await InvoicesPage.showApplyCredit(21);
  out.invoiceDialog = inputs.map((i) => [i.dataset.id, i.value, i.dataset.label]);
  refuseNext = 1;
  await InvoicesPage.saveApplyCredit(ev, 21);
  out.invoiceRefused = {
    sent: sent(), toast: toasts[toasts.length - 1], open: !!modal, refreshed,
    reopenedWith: inputs.map((i) => [i.dataset.id, i.value]),
    memosLeft: state.memos.map((m) => m.available), balance: state.invoices[0].balance_due,
  };
  //    a second Apply on the dialog as it opened again
  posts = []; toasts = [];
  await InvoicesPage.saveApplyCredit(ev, 21);
  out.invoiceRetry = {
    sent: sent(), toast: toasts[toasts.length - 1], refreshed,
    memosLeft: state.memos.map((m) => m.available), balance: state.invoices[0].balance_due,
  };

  // 4. Refused on the first row: nothing applied, said so, nothing more sent.
  reset();
  await InvoicesPage.showApplyCredit(21);
  refuseNext = 0;
  await InvoicesPage.saveApplyCredit(ev, 21);
  out.invoiceRefusedFirst = { sent: sent(), toast: toasts[toasts.length - 1] };

  // 5. More than a credit has left sends nothing.
  reset();
  await InvoicesPage.showApplyCredit(21);
  inputs[1].value = '10.01';
  await InvoicesPage.saveApplyCredit(ev, 21);
  out.invoiceOverCredit = { posts: posts.length, toast: toasts[toasts.length - 1] };

  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e.stack || String(e)); process.exit(1); });
