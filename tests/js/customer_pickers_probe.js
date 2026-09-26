// The customer pickers on the Jobs page (list filter, job form) and the
// Reseller Permit form, against canned answers where customer 5 and vendor
// 10 are inactive. Prints JSON: each case's <option>s as [value, text,
// selected], and every API path the pages asked for.
// Strip tags until nothing changes: one pass can leave a tag behind
// ('<scr<script>ipt>'), which is how CodeQL reads a single replace.
const stripTags = (s) => { let prev; s = String(s); do { prev = s; s = s.replace(/<[^>]*>/g, ''); } while (s !== prev); return s; };
const fs = require('fs'), vm = require('vm');

const answers = {
  '/jobs?include_inactive=true': [
    { id: 1, customer_id: 5, customer_name: 'Old Diner', name: 'Patio', is_active: true, status: 'in_progress' },
    { id: 2, customer_id: 6, customer_name: 'Acme', name: 'Sign', is_active: true, status: 'in_progress' },
  ],
  '/jobs/budget-vs-actual?include_inactive=true': [],
  '/jobs/1': { id: 1, customer_id: 5, customer_name: 'Old Diner', name: 'Patio', status: 'in_progress', is_active: true },
  '/customers?active_only=true': [{ id: 6, name: 'Acme' }, { id: 7, name: 'Bravo Bakery' }],
  '/vendors?active_only=true': [{ id: 9, name: 'Paper Co' }],
  '/customers/5': { id: 5, name: 'Old Diner', is_active: false },
  '/vendors/10': { id: 10, name: 'Gone Supply', is_active: false },
  '/reseller-permits/3': { id: 3, entity_type: 'customer', entity_id: 5, jurisdiction: 'WA', permit_number: 'R-1', is_active: true },
  '/reseller-permits/4': { id: 4, entity_type: 'vendor', entity_id: 10, jurisdiction: 'WA', permit_number: 'R-2', is_active: true },
  // what the pickers must no longer ask for: every customer, inactive ones too
  '/customers': [{ id: 5, name: 'Old Diner' }, { id: 6, name: 'Acme' }, { id: 7, name: 'Bravo Bakery' }],
  '/vendors': [{ id: 9, name: 'Paper Co' }, { id: 10, name: 'Gone Supply' }],
};

const asked = [];
let modalHtml = '';
const ctx = {
  console, Promise, setTimeout,
  window: {},
  document: { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] },
  $: () => null,
  $$: () => [],
  escapeHtml: (s) => String(s ?? ''),
  formatCurrency: (n) => '$' + Number(n || 0).toFixed(2),
  formatDate: (s) => s || '',
  T: (s) => s,
  Terms: { text: (s) => s, isNonprofit: () => false },
  toast() {},
  openModal: (_title, html) => { modalHtml = html; },
  closeModal() {},
  App: { navigate() {} },
  API: {
    get: async (path) => {
      asked.push(path);
      if (!(path in answers)) throw new Error('unexpected ' + path);
      return JSON.parse(JSON.stringify(answers[path]));
    },
  },
};
vm.createContext(ctx);
for (const f of ['jobs.js', 'reseller_permits.js']) {
  vm.runInContext(fs.readFileSync(`app/static/js/${f}`, 'utf8'), ctx, { filename: f });
}
vm.runInContext('this.JobsPage = JobsPage; this.ResellerPermitsPage = ResellerPermitsPage;', ctx);

const options = (html, selectPattern, end = '</select>') => {
  const m = new RegExp(selectPattern + '[\\s\\S]*?' + end).exec(html);
  if (!m) return null;
  return (m[0].match(/<option[^>]*>[^<]*<\/option>/g) || []).map((o) => [
    (/value="([^"]*)"/.exec(o) || [])[1],
    stripTags(o),
    /\sselected/.test(o),
  ]).filter((o) => o[0] !== '');
};

(async () => {
  const out = {};
  const page = await ctx.JobsPage.render();
  out.jobFilter = options(page, '<select id="job-filter-customer"');
  await ctx.JobsPage.showForm();
  out.newJob = options(modalHtml, '<select name="customer_id"');
  await ctx.JobsPage.showForm(1);
  out.editJob = options(modalHtml, '<select name="customer_id"');
  await ctx.JobsPage.showForm(null, 5);
  out.newJobForInactive = options(modalHtml, '<select name="customer_id"');

  await ctx.ResellerPermitsPage.showForm();
  out.newPermit = { customers: options(modalHtml, '<optgroup label="Customers"', '</optgroup>'), vendors: options(modalHtml, '<optgroup label="Vendors"', '</optgroup>') };
  await ctx.ResellerPermitsPage.showForm(3);
  out.editCustomerPermit = { customers: options(modalHtml, '<optgroup label="Customers"', '</optgroup>'), vendors: options(modalHtml, '<optgroup label="Vendors"', '</optgroup>') };
  await ctx.ResellerPermitsPage.showForm(4);
  out.editVendorPermit = { customers: options(modalHtml, '<optgroup label="Customers"', '</optgroup>'), vendors: options(modalHtml, '<optgroup label="Vendors"', '</optgroup>') };
  out.asked = asked;
  console.log(JSON.stringify(out));
})();
