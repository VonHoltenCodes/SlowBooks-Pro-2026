// The bank register's rows and an expense's view, rendered against canned
// API answers: which carry Print Check (discussion #354). Prints JSON
// {register: {"<txn>": [<button text or html>...]}, expense_<case>: [...]}.
const fs = require('fs'), vm = require('vm');

const row = (transaction_id, extra) => Object.assign(
  { date: '2026-09-14', payee: 'Main Street Properties', description: 'October rent', reference: '1072',
    source_type: 'bank_entry', source_link: null, transaction_id, payment: 250, deposit: 0, balance: 0,
    voided: false, voidable: true, printable: true, reconciliation_id: null, cleared: false }, extra);
const answers = {
  '/banking/check-register?account_id=5': {
    account_name: 'Checking', account_number: '1000', bank_kind: 'bank', balance: 100,
    entries: [
      row(61, {}),
      row(62, { source_type: 'expense', source_link: '/#/expenses/62', voidable: false }),
      row(63, { payee: 'Walk-in sale', payment: 0, deposit: 500, printable: false }),
      row(64, { source_type: 'transfer', description: 'to savings', printable: false }),
      row(65, { voided: true, voidable: false, printable: false }),
    ],
  },
  '/banking/overview': [{ account_id: 5, feed: null, last_reconciled: null }],
  '/expenses/62': { id: 62, date: '2026-09-02', payee: 'Sweet Forest Cafe', expense_account_name: 'Meals',
    paid_from_account_name: 'Checking', amount: 30.3, reference: '1074', memo: 'team lunch', status: 'recorded', printable: true },
  '/expenses/66': { id: 66, date: '2026-09-02', payee: 'Corner Hardware', expense_account_name: 'Supplies',
    paid_from_account_name: 'Company Card', amount: 12, reference: '', memo: '', status: 'recorded', printable: false },
  '/expenses/67': { id: 67, date: '2026-09-02', payee: 'Sweet Forest Cafe', expense_account_name: 'Meals',
    paid_from_account_name: 'Checking', amount: 30.3, reference: '1075', memo: '', status: 'void', printable: false },
};

let modal = null;
const ctx = {
  console, setTimeout, Promise,
  window: {},
  location: { hash: '#/' },
  document: { addEventListener() {}, querySelector: () => null, querySelectorAll: () => [], getElementById: () => null },
  escapeHtml: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  formatDate: (s) => s || '',
  sourceWord: (s) => String(s || ''),
  datesFromQuery: (q) => ({ start_date: (q && q.start_date) || '', end_date: (q && q.end_date) || '' }),
  formatCurrency: (n) => '$' + Number(n || 0).toFixed(2),
  statusBadge: (s) => `<span class="badge badge-${s}">${s}</span>`,
  openModal: (title, html) => { modal = { title, html }; },
  closeModal() {},
  toast() {},
  T: (s) => s,
  Terms: { text: (s) => s, isNonprofit: () => false, isConstruction: () => false },
  App: { navigate() {} },
  API: {
    get: async (path) => {
      if (!(path in answers)) throw new Error('unexpected ' + path);
      return JSON.parse(JSON.stringify(answers[path]));
    },
  },
};
vm.createContext(ctx);
for (const f of ['banking.js', 'expenses.js']) {
  vm.runInContext(fs.readFileSync(`app/static/js/${f}`, 'utf8'), ctx, { filename: f });
}
vm.runInContext('this.BankingPage = BankingPage; this.ExpensesPage = ExpensesPage;', ctx);
// the expense view loads its attachments after opening; nothing to answer here
ctx.ExpensesPage.loadAttachments = async () => {};

const flat = (html) => html.replace(/\s+/g, ' ');
const buttons = (html) => (html.match(/<button[^>]*>[^<]*<\/button>/g) || []).map(flat);

(async () => {
  const out = { register: {} };
  const reg = await ctx.BankingPage.renderRegister('5');
  for (const tr of reg.match(/<tr style="[^"]*">[\s\S]*?<\/tr>/g) || []) {
    const id = (tr.match(/transaction_id=(\d+)|voidEntry\('[a-z_]+', (\d+)/) || []);
    const cells = tr.match(/<td[^>]*>[\s\S]*?<\/td>/g) || [];
    const last = cells[cells.length - 1] || '';
    const txn = Number(id[1] || id[2] || 0);
    const key = txn || `row${Object.keys(out.register).length}`;
    out.register[key] = buttons(last).map((b) => b.includes('Void') ? 'Void' : b);
  }
  // rows with neither button carry no id in their last cell: key them by order
  const order = [61, 62, 63, 64, 65];
  const keyed = {};
  let i = 0;
  for (const [k, v] of Object.entries(out.register)) keyed[String(order[i++])] = v;
  out.register = keyed;
  for (const [name, id] of [['expense_check', 62], ['expense_card', 66], ['expense_void', 67]]) {
    await ctx.ExpensesPage.showDetail(id);
    out[name] = buttons(modal.html);
  }
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e.stack || e); process.exit(1); });
