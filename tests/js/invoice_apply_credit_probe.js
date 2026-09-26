// Evaluate payments.js and invoices.js with a small fake DOM and run the
// invoice view's Apply Credit: the note, the dialog (amounts filled oldest
// first up to the balance, credit in another currency left out) and what
// Apply sends. Prints one JSON object for the pytest side.
const fs = require('fs'), vm = require('vm');

const posts = [];
const toasts = [];
let modal = null;
const els = {
  '#inv-credit-note': { innerHTML: '' },
  '#apply-credit-status': { textContent: '', style: {} },
};

function attr(tag, name) {
  const m = tag.match(new RegExp(`${name}="([^"]*)"`));
  return m ? m[1] : null;
}
function inputs() {
  if (!modal) return [];
  return [...modal.html.matchAll(/<input class="credit-take"[^>]*>/g)].map((m) => ({
    value: attr(m[0], 'value'),
    dataset: { kind: attr(m[0], 'data-kind'), id: attr(m[0], 'data-id'), max: attr(m[0], 'data-max') },
  }));
}
let fakeInputs = [];

const invoice = {
  id: 5, invoice_number: '1005', customer_id: 7, customer_name: 'Acme Diner',
  status: 'sent', balance_due: '40.00', currency: 'USD',
};
const credits = {
  customer_id: 7, customer_name: 'Acme Diner', home_currency: 'USD', total: 110,
  credits: [
    { kind: 'payment', id: 11, date: '2026-09-02', number: '4420', method: 'Check', currency: 'USD', amount: 100, available: 30 },
    { kind: 'credit_memo', id: 3, date: '2026-09-05', number: 'CM-0003', method: '', currency: 'USD', amount: 20, available: 20 },
    { kind: 'payment', id: 12, date: '2026-09-06', number: '', method: 'Wire', currency: 'EUR', amount: 50, available: 50 },
  ],
};

const ctx = {
  console,
  window: {},
  location: { hash: '#/invoices' },
  App: { settings: { home_currency: 'USD' }, navigate: () => {} },
  T: (s) => s,
  Terms: { text: (s) => s },
  escapeHtml: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
  formatDate: (s) => s,
  formatCurrency: (n) => '$' + (Number(n) || 0).toFixed(2),
  toast: (msg, kind) => { toasts.push([msg, kind || 'ok']); },
  closeModal: () => { modal = null; },
  openModal: (title, html) => { modal = { title, html }; fakeInputs = inputs(); },
  $: (sel) => els[sel] || null,
  $$: (sel) => (sel === '.credit-take' ? fakeInputs : []),
  API: {
    get: async (path) => {
      if (path === '/invoices/5') return invoice;
      if (path === '/customers/7/credits') return credits;
      throw new Error('unexpected GET ' + path);
    },
    post: async (path, body) => { posts.push([path, body]); return {}; },
  },
};
vm.createContext(ctx);
vm.runInContext(
  fs.readFileSync('app/static/js/invoices.js', 'utf8') + '\n'
    + fs.readFileSync('app/static/js/payments.js', 'utf8')
    + '\nthis.InvoicesPage = InvoicesPage; this.PaymentsPage = PaymentsPage;',
  ctx,
);
const P = ctx.InvoicesPage;

(async () => {
  const out = {};

  await P.loadCreditNote(invoice);
  const note = els['#inv-credit-note'].innerHTML;
  out.note = {
    text: (note.match(/This customer has [^<]*/) || [null])[0].trim(),
    button: note.includes('onclick="InvoicesPage.showApplyCredit(5)"'),
  };
  // nothing is offered on a paid or void invoice
  els['#inv-credit-note'].innerHTML = '';
  await P.loadCreditNote({ ...invoice, balance_due: '0.00', status: 'paid' });
  await P.loadCreditNote({ ...invoice, status: 'void' });
  out.noteWhenNothingDue = els['#inv-credit-note'].innerHTML;
  // a EUR invoice is offered only the EUR credit
  await P.loadCreditNote({ ...invoice, currency: 'EUR', balance_due: '850.00' });
  out.noteForEur = (els['#inv-credit-note'].innerHTML.match(/This customer has [^<]*/) || [null])[0].trim();

  await P.showApplyCredit(5);
  out.dialog = {
    title: modal.title,
    rows: fakeInputs.map((i) => [i.dataset.kind, Number(i.dataset.id), i.value]),
    status: els['#apply-credit-status'].textContent,
  };
  fakeInputs[1].value = '25';
  P._applyCreditStatus();
  out.overStatus = els['#apply-credit-status'].textContent;
  fakeInputs[1].value = '20';
  P._applyCreditStatus();
  out.overDue = els['#apply-credit-status'].textContent;
  fakeInputs[1].value = '10';

  await P.saveApplyCredit({ preventDefault() {} }, 5);
  out.posts = posts.splice(0);
  out.toasts = toasts.splice(0);
  console.log(JSON.stringify(out));
})();
