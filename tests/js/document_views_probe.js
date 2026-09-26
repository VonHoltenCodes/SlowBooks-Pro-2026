// The views the bank register's links open, rendered against canned API
// answers: a deposit (deposits.js), a bill payment (bills.js), and the
// register's own lines (banking.js). Prints JSON {"<case>": {...}}.
// Strip tags until nothing changes: one pass can leave a tag behind
// ('<scr<script>ipt>'), which is how CodeQL reads a single replace.
const stripTags = (s) => { let prev; s = String(s); do { prev = s; s = s.replace(/<[^>]*>/g, ''); } while (s !== prev); return s; };
const fs = require('fs'), vm = require('vm');

const answers = {
  '/deposits/31': {
    id: 31, date: '2026-09-12', reference: 'SLIP-9', account_id: 1, account_name: 'Checking',
    amount: '512.13', items: 2, voided: false, reconciled: false,
    payments: [
      { transaction_line_id: 5, transaction_id: 20, date: '2026-09-10', description: 'Payment from Salt & Pine',
        reference: '', source_type: 'payment', amount: 312.13, payment_id: 4, received_from: 'Salt & Pine',
        check_number: '4420', payment_reference: '', method: 'check', document: 'Invoice #1002' },
      { transaction_line_id: 6, transaction_id: 21, date: '2026-09-11', description: 'Sales Receipt #1003',
        reference: '', source_type: 'payment', amount: 200, payment_id: 5, received_from: 'Walk-in',
        check_number: '', payment_reference: '', method: 'cash', document: 'Sales Receipt #1003' },
    ],
  },
  '/deposits/32': {
    id: 32, date: '2026-09-13', reference: '', account_id: 1, account_name: 'Checking',
    amount: '80.00', items: null, voided: true, reconciled: false, payments: [],
  },
  '/deposits/33': {
    id: 33, date: '2026-08-01', reference: '', account_id: 1, account_name: 'Checking',
    amount: '99.00', items: null, voided: false, reconciled: true, payments: [],
  },
  '/bill-payments/41': {
    id: 41, vendor_id: 2, vendor_name: 'Sign Supply', date: '2026-09-15', amount: '200.00',
    method: 'check', check_number: '1050', is_voided: false,
    allocations: [{ bill_id: 8, amount: '150.00' }, { bill_id: 9, amount: '25.00' }],
  },
  '/bill-payments/42': {
    id: 42, vendor_id: 2, vendor_name: 'Sign Supply', date: '2026-09-16', amount: '60.00',
    method: 'ach', check_number: '', is_voided: false, allocations: [{ bill_id: 8, amount: '60.00' }],
  },
  '/bill-payments/43': {
    id: 43, vendor_id: 2, vendor_name: 'Sign Supply', date: '2026-09-17', amount: '10.00',
    method: 'check', check_number: '1051', is_voided: true, allocations: [{ bill_id: 9, amount: '10.00' }],
  },
  '/bills/8': { id: 8, bill_number: 'SS-1050', date: '2026-09-01' },
  '/bills/9': { id: 9, bill_number: 'SS-1051', date: '2026-09-02' },
  '/banking/check-register?account_id=5': {
    account_name: 'Checking', account_number: '1000', bank_kind: 'bank', balance: 100,
    entries: [
      { date: '2026-09-12', payee: '', description: 'Deposit to Checking', reference: '', source_type: 'deposit',
        source_link: '/#/deposits/31', transaction_id: 31, payment: 0, deposit: 512.13, balance: 512.13 },
      { date: '2026-09-20', payee: '', description: 'Sales tax payment', reference: '', source_type: 'sales_tax_payment',
        source_link: null, transaction_id: 57, payment: 40, deposit: 0, balance: 472.13 },
      { date: '2026-09-21', payee: '', description: '', reference: '', source_type: 'payroll',
        source_link: null, transaction_id: 58, payment: 10, deposit: 0, balance: 462.13 },
    ],
  },
  '/banking/overview': [{ account_id: 5, feed: null, last_reconciled: null }],
};

let modal = null;
const ctx = {
  console, setTimeout, Promise,
  window: {},
  location: { hash: '#/' },
  document: { addEventListener() {}, querySelector: () => null, querySelectorAll: () => [] },
  escapeHtml: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  formatDate: (s) => s || '',
  formatCurrency: (n) => '$' + Number(n || 0).toFixed(2),
  statusBadge: (s) => `<span class="badge badge-${s}">${s}</span>`,
  openModal: (title, html) => { modal = { title, html }; },
  closeModal() {},
  toast() {},
  T: (s) => s,
  Terms: { text: (s) => s, isNonprofit: () => false },
  App: { navigate() {} },
  API: {
    get: async (path) => {
      if (!(path in answers)) throw new Error('unexpected ' + path);
      return JSON.parse(JSON.stringify(answers[path]));
    },
  },
};
vm.createContext(ctx);
for (const f of ['deposits.js', 'bills.js', 'banking.js']) {
  vm.runInContext(fs.readFileSync(`app/static/js/${f}`, 'utf8'), ctx, { filename: f });
}
vm.runInContext('this.DepositsPage = DepositsPage; this.BillsPage = BillsPage; this.BankingPage = BankingPage;', ctx);

const flat = (html) => html.replace(/\s+/g, ' ');
const buttons = (html) => (html.match(/<button[^>]*>[^<]*<\/button>/g) || []).map(flat);
const cells = (html) => (html.match(/<tbody>[\s\S]*?<\/tbody>/g) || []).map((body) =>
  (body.match(/<tr>[\s\S]*?<\/tr>/g) || []).map((tr) =>
    (tr.match(/<td[^>]*>([\s\S]*?)<\/td>/g) || []).map((td) => flat(stripTags(td)).trim())));

(async () => {
  const out = {};
  for (const id of [31, 32, 33]) {
    await ctx.DepositsPage.view(id);
    out[`deposit ${id}`] = { title: modal.title, buttons: buttons(modal.html), rows: cells(modal.html)[0] || [], text: flat(modal.html.replace(/<[^>]*>/g, ' ')) };
  }
  for (const id of [41, 42, 43]) {
    await ctx.BillsPage.viewPayment(id);
    out[`bill payment ${id}`] = { title: modal.title, buttons: buttons(modal.html), rows: cells(modal.html)[0] || [], text: flat(modal.html.replace(/<[^>]*>/g, ' ')) };
  }
  const reg = await ctx.BankingPage.renderRegister('5');
  out.register = (reg.match(/<td><a href="[^"]*">[^<]*<\/a><\/td>/g) || []);
  console.log(JSON.stringify(out));
})();
