// The pay run dialog, rendered against canned API answers: whether it offers
// the direct deposit file, what its note says, and the Paid by column
// (discussion #211). Prints JSON {processed: {...}, checks_only: {...}, draft: {...}}.
const fs = require('fs'), vm = require('vm');

const stub = (id, employee_name, direct_deposit) => ({
  id, employee_id: id, employee_name, direct_deposit, hours: 80, gross_pay: 2000,
  federal_tax: 200, state_tax: 0, state_other_employee: 10, ss_tax: 124, medicare_tax: 29,
  garnishments: 0, benefits: [], pretax_deductions: 0, posttax_deductions: 0, reimbursements: 0,
  net_pay: 1637,
});
const run = (id, status, stubs) => ({
  id, period_start: '2026-04-01', period_end: '2026-04-15', pay_date: '2026-04-20', status,
  total_gross: 4000, total_net: 3274, total_taxes: 726, total_employer_taxes: 300,
  total_employer_benefits: 0, burden_job_cost_id: null, stubs,
});
const answers = {
  '/payroll/1': run(1, 'processed', [stub(11, 'Dee Posit', true), stub(12, 'Chet Byczek', false)]),
  '/payroll/2': run(2, 'processed', [stub(21, 'Chet Byczek', false)]),
  '/payroll/3': run(3, 'draft', [stub(31, 'Dee Posit', true)]),
};

let modal = null;
const ctx = {
  console, setTimeout, Promise,
  window: {},
  location: { hash: '#/' },
  document: { addEventListener() {}, querySelector: () => null, querySelectorAll: () => [], getElementById: () => null },
  escapeHtml: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  formatDate: (s) => s || '',
  formatCurrency: (n) => '$' + Number(n || 0).toFixed(2),
  statusBadge: (s) => `<span class="badge badge-${s}">${s}</span>`,
  openModal: (title, html) => { modal = { title, html }; },
  closeModal() {},
  toast() {},
  T: (s) => s,
  App: { navigate() {} },
  API: {
    get: async (path) => {
      if (!(path in answers)) throw new Error('unexpected ' + path);
      return JSON.parse(JSON.stringify(answers[path]));
    },
  },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/hint.js', 'utf8'), ctx);  // hint(), hintList() (#977)
vm.runInContext(fs.readFileSync('app/static/js/payroll.js', 'utf8'), ctx, { filename: 'payroll.js' });
vm.runInContext('this.PayrollPage = PayrollPage;', ctx);

const flat = (html) => html.replace(/\s+/g, ' ');
// Strip tags until nothing changes: one pass can leave a tag behind
// ('<scr<script>ipt>'), which is how CodeQL reads a single replace.
const stripTags = (s) => { let prev; s = String(s); do { prev = s; s = s.replace(/<[^>]*>/g, ''); } while (s !== prev); return s; };
(async () => {
  const out = {};
  for (const [key, id] of [['processed', 1], ['checks_only', 2], ['draft', 3]]) {
    modal = null;
    await ctx.PayrollPage.view(id);
    // a column head's tortoise (Other's, #977) is not a column
    const html = flat(modal.html).replace(/<span class="hint-wrap">.*?<\/span><\/span><\/span>/g, '');
    const note = (html.match(/<p[^>]*>([^<]*(?:Direct deposit file|pay each stub by check|Once processed)[^<]*)<\/p>/) || [])[1] || '';
    const paid_by = [];
    const header = (html.match(/<thead>.*?<\/thead>/) || [''])[0].match(/<th[^>]*>([^<]*)<\/th>/g).map(stripTags);
    const col = header.indexOf('Paid by');
    for (const tr of (html.match(/<tbody>(.*?)<\/tbody>/) || ['', ''])[1].match(/<tr>.*?<\/tr>/g) || []) {
      const cells = tr.match(/<td[^>]*>.*?<\/td>/g) || [];
      paid_by.push(stripTags(cells[col] || '').trim());
    }
    out[key] = { button: /downloadDirectDeposit\(\d+\)/.test(html), note: note.trim(), paid_by };
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
