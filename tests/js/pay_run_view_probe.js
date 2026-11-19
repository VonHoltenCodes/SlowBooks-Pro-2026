// Render PayrollPage.view() for a pay run with an Oregon employee (0.1%
// statewide transit tax) and print each row's money columns by header.
// Strip tags until nothing changes: one pass can leave a tag behind
// ('<scr<script>ipt>'), which is how CodeQL reads a single replace.
const stripTags = (s) => { let prev; s = String(s); do { prev = s; s = s.replace(/<[^>]*>/g, ''); } while (s !== prev); return s; };
const fs = require('fs'), vm = require('vm');
let modal = '';
let modalOpts = null;
const run = {
  id: 3, period_start: '2026-09-13', period_end: '2026-09-26', status: 'draft',
  total_gross: 2166.67, total_taxes: 404.06, total_employer_taxes: 0, total_employer_benefits: 0, total_net: 1762.61,
  stubs: [{
    id: 9, employee_id: 2, employee_name: 'Lena Hart', hours: 0, gross_pay: 2166.67,
    federal_tax: 91.67, state_tax: 144.47, state_other_employee: 2.17, ss_tax: 134.33, medicare_tax: 31.42,
    pretax_deductions: 0, posttax_deductions: 0, garnishments: 0, reimbursements: 0, net_pay: 1762.61, benefits: [],
  }, {
    id: 10, employee_id: 3, employee_name: 'Jonah Pike', hours: 40, gross_pay: 1000,
    federal_tax: 50, state_tax: 60, state_other_employee: 1, ss_tax: 62, medicare_tax: 14.5,
    pretax_deductions: 40, posttax_deductions: 10, garnishments: 100, reimbursements: 25, net_pay: 687.5, benefits: [],
  }],
};
const ctx = {
  console,
  API: { get: async () => run },
  escapeHtml: (s) => String(s), formatDate: (d) => d, statusBadge: (s) => s, T: (s) => s,
  formatCurrency: (n) => '$' + Number(n || 0).toFixed(2),
  openModal: (title, html, opts) => { modal = html; modalOpts = opts || null; }, closeModal: () => {},
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/hint.js', 'utf8'), ctx);  // hint(), hintList() (#977)
vm.runInContext(fs.readFileSync('app/static/js/payroll.js', 'utf8') + '\nthis.P = PayrollPage;', ctx);
(async () => {
  await ctx.P.view(3);
  // a column head's tortoise (Other's, #977) is not part of its name
  const unhinted = modal.replace(/<span class="hint-wrap">[\s\S]*?<\/span><\/span><\/span>/g, '');
  const heads = [...unhinted.matchAll(/<th scope="col"[^>]*>([^<]*)<\/th>/g)].map((m) => m[1].trim());
  const body = modal.slice(modal.indexOf('<tbody>'), modal.indexOf('</tbody>'));
  for (const tr of body.split('<tr>').slice(1)) {
    const cells = [...tr.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map((m) => stripTags(m[1]).trim());
    const row = Object.fromEntries(heads.map((h, i) => [h, cells[i]]));
    // how the Stub PDF button is named, and how the table is laid out
    row._stub_label = (tr.match(/<button[^>]*aria-label="([^"]*)"/) || [])[1] || null;
    const layout = modal.match(/<div class="(table-container[^"]*)"><table(?: class="([^"]*)")?>/) || [];
    row._container = layout[1] || null;
    row._table = layout[2] || null;
    row._wide = !!(modalOpts && modalOpts.wide);
    console.log(JSON.stringify(row));
  }
})();
