// Evaluate payments.js with a stub DOM and drive Receive Payment: typing the
// amount fills Apply oldest invoice first; money left over needs the
// "keep it as a credit" box; the open-invoice list keeps drafts and drops
// voids. Prints one JSON object per line for tests/test_receive_payment_form.py.
const fs = require('fs'), vm = require('vm');

const el = (props = {}) => ({ style: {}, textContent: '', hidden: false, value: '', checked: false, dataset: {}, ...props });
const amount = el();
const allocs = [
  el({ dataset: { invoice: '11', max: '312.30' } }), // oldest first, as rendered
  el({ dataset: { invoice: '12', max: '500.00' } }),
];
const host = el({ querySelectorAll: (sel) => (sel === '.alloc-amount' ? allocs : []) });
const byId = {
  '#payment-invoices': host,
  '#keep-credit-row': el({ hidden: true }),
  '#keep-credit-text': el(),
  '#keep-credit': el(),
  '#alloc-status': el(),
  '[name="amount"]': amount,
};
const toasts = [], posts = [];
const ctx = {
  console,
  window: {},
  $: (sel) => byId[sel] || null,
  $$: (sel) => (sel === '#payment-invoices .alloc-amount' ? allocs : []),
  T: (s) => s,
  Terms: { text: (s) => s, isNonprofit: () => false },
  formatCurrency: (n) => '$' + Number(n || 0).toFixed(2),
  formatDate: (s) => s,
  escapeHtml: (s) => String(s),
  todayISO: () => '2026-09-26',
  toast: (msg, kind) => toasts.push([msg, kind || 'success']),
  openModal: () => {}, closeModal: () => {},
  App: { navigate: () => {} },
  API: { post: async (path, body) => { posts.push([path, body]); return {}; }, get: async () => [] },
  location: { hash: '#/payments' },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/payments.js', 'utf8') + '\nthis.PaymentsPage = PaymentsPage;', ctx);
const P = ctx.PaymentsPage;
P._invoices = [{ id: 11 }, { id: 12 }];
const state = () => ({
  apply: allocs.map((a) => a.value),
  keepShown: !byId['#keep-credit-row'].hidden,
  keepText: byId['#keep-credit-text'].textContent,
  status: byId['#alloc-status'].textContent,
});
const out = (name, extra = {}) => console.log(JSON.stringify({ name, ...state(), ...extra }));

amount.value = '400';
P._autoApply();
out('partial');

amount.value = '900';
P._autoApply();
out('over');

allocs[1].value = '100.00'; // the user changes an Apply amount
P._updateAllocStatus();
out('edited');

(async () => {
  const form = {
    customer_id: { value: '3' }, date: { value: '2026-09-26' }, amount: { value: '900' },
    method: { value: 'Check' }, check_number: { value: '5521' }, reference: { value: '' },
    deposit_to_account_id: { value: '' }, notes: { value: '' },
  };
  allocs[1].value = '500.00';
  P._updateAllocStatus();
  await P.save({ preventDefault() {}, target: form });
  out('save-unticked', { posts: posts.length, toast: toasts[toasts.length - 1] });
  byId['#keep-credit'].checked = true;
  await P.save({ preventDefault() {}, target: form });
  out('save-ticked', { posts: posts.length, body: posts[0] && posts[0][1] });

  const open = P._openInvoices([
    { id: 3, date: '2026-09-20', status: 'sent', balance_due: '10.00' },
    { id: 1, date: '2026-08-01', status: 'draft', balance_due: '5.00' },
    { id: 2, date: '2026-08-15', status: 'void', balance_due: '7.00' },
    { id: 4, date: '2026-07-01', status: 'paid', balance_due: '0.00' },
    { id: 5, date: '2026-08-01', status: 'partial', balance_due: '1.00' },
  ]);
  console.log(JSON.stringify({ name: 'open', ids: open.map((i) => i.id) }));
})();
