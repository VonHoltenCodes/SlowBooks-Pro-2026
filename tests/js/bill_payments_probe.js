// A bill's payments as its view lists them (bills.js _paymentsHtml): one row
// per payment, with the row's buttons. Prints JSON {"<case>": [<button>...]}.
const fs = require('fs'), vm = require('vm');

const ctx = {
  console,
  window: {},
  escapeHtml: (s) => String(s ?? ''),
  formatDate: (s) => s,
  formatCurrency: (n) => '$' + Number(n || 0).toFixed(2),
  statusBadge: (s) => `<span class="badge">${s}</span>`,
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/bills.js', 'utf8') + '\nthis.BillsPage = BillsPage;', ctx);
const B = ctx.BillsPage;

const bill = { id: 3 };
const pay = (id, extra) => Object.assign(
  { id, date: '2026-09-10', method: 'check', check_number: '', is_voided: false,
    allocations: [{ bill_id: 3, amount: '150.00' }] }, extra);
const cases = {
  check: pay(21, { check_number: '1050' }),
  check_without_number: pay(22, {}),
  imported_without_method: pay(23, { method: null, check_number: '88' }),
  ach: pay(24, { method: 'ach' }),
  credit_card: pay(25, { method: 'credit_card' }),
  void_check: pay(26, { check_number: '1051', is_voided: true }),
};
const out = {};
for (const [name, p] of Object.entries(cases)) {
  const html = B._paymentsHtml(bill, [p]);
  out[name] = (html.match(/<button[^>]*>[^<]*<\/button>/g) || [])
    .map((b) => b.replace(/\s+/g, ' '));
}
console.log(JSON.stringify(out));
