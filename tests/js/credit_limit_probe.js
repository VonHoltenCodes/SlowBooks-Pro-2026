// Evaluate invoices.js with stubs and run InvoicesPage.creditLimitOk against
// a customer's open invoices. Prints one JSON object for the pytest side.
const fs = require('fs'), vm = require('vm');

const asked = [];
let answer = false;
const invoices = [
  { id: 1, status: 'sent', balance_due: '312.13', exchange_rate: '1' },
  { id: 2, status: 'void', balance_due: '0', exchange_rate: '1' },
  { id: 3, status: 'draft', balance_due: '100.00', exchange_rate: null },
  { id: 4, status: 'paid', balance_due: '0.00', exchange_rate: '1' },
];
const ctx = {
  console,
  window: {},
  App: { settings: { home_currency: 'USD' } },
  T: (s) => s,
  formatCurrency: (n) => '$' + (Number(n) || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
  confirm: (msg) => { asked.push(msg); return answer; },
  API: { get: async (path) => { if (!path.startsWith('/invoices?customer_id=')) throw new Error(path); return invoices; } },
};
vm.createContext(ctx);
vm.runInContext(
  fs.readFileSync('app/static/js/invoices.js', 'utf8') + '\nthis.InvoicesPage = InvoicesPage;',
  ctx,
);
const P = ctx.InvoicesPage;
const saltAndPine = { id: 9, name: 'Salt & Pine Catering Co.', credit_limit: '500.00' };

(async () => {
  const out = {};
  // 412.13 open + 87.87 = 500.00: at the limit, not past it
  out.atLimit = await P.creditLimitOk(saltAndPine, 87.87);
  out.askedAtLimit = asked.length;
  // 412.13 open + 100.00 = 512.13: past it, and the user says no
  out.pastNo = await P.creditLimitOk(saltAndPine, 100);
  out.message = asked[asked.length - 1];
  answer = true;
  out.pastYes = await P.creditLimitOk(saltAndPine, 100);
  // editing invoice 1: its old balance is not counted twice
  const before = asked.length;
  out.editing = await P.creditLimitOk(saltAndPine, 350, 1);
  out.askedEditing = asked.length - before;
  // no limit set: never asks
  out.noLimit = await P.creditLimitOk({ id: 9, name: 'x', credit_limit: null }, 1e9);
  console.log(JSON.stringify(out));
})();
