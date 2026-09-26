// Evaluate batch_payments.js with stubs and render the page over a list
// with a home-currency and a EUR invoice. Prints one JSON object.
const fs = require('fs'), vm = require('vm');

const invoices = [
  { id: 1, invoice_number: '1001', customer_id: 7, customer_name: 'Acme Diner', status: 'sent', balance_due: 100, currency: 'USD', due_date: '2026-10-01' },
  { id: 2, invoice_number: '1002', customer_id: 8, customer_name: 'Bäckerei Müller', status: 'sent', balance_due: 850, currency: 'EUR', due_date: '2026-10-01' },
  { id: 3, invoice_number: '1003', customer_id: 7, customer_name: 'Acme Diner', status: 'draft', balance_due: 40, currency: null, due_date: '2026-10-01' },
  { id: 4, invoice_number: '1004', customer_id: 7, customer_name: 'Acme Diner', status: 'paid', balance_due: 0, currency: 'USD', due_date: '2026-10-01' },
];
const ctx = {
  console,
  window: {},
  document: { addEventListener: () => {} },
  App: { settings: { home_currency: 'USD' } },
  T: (s) => s,
  escapeHtml: (s) => String(s),
  formatDate: (s) => s,
  formatCurrency: (n) => '$' + (Number(n) || 0).toFixed(2),
  todayISO: () => '2026-09-26',
  API: {
    get: async (path) => (path.startsWith('/invoices') ? invoices : []),
  },
};
vm.createContext(ctx);
vm.runInContext(
  fs.readFileSync('app/static/js/batch_payments.js', 'utf8') + '\nthis.BatchPaymentsPage = BatchPaymentsPage;',
  ctx,
);

(async () => {
  const html = await ctx.BatchPaymentsPage.render();
  const listed = [...html.matchAll(/class="batch-check" data-inv="(\d+)"/g)].map((m) => Number(m[1]));
  const note = (html.match(/<p [^>]*>([^<]*not listed here[^<]*)<\/p>/) || [])[1] || null;
  console.log(JSON.stringify({ listed, note }));
})();
