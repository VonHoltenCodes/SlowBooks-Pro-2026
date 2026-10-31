// Every document form's Tax Rate field, rendered against real API answers
// (read from stdin as JSON: the company settings and saved documents), the
// tax the forms work out from it, the Sales Tax report's Rate column and a
// scanned receipt's rate. Prints one JSON object for
// tests/test_tax_rate_four_places.py.
const fs = require('fs'), vm = require('vm');

const given = JSON.parse(fs.readFileSync(0, 'utf8'));
const answers = {
  '/customers?active_only=true&tree=1': [],
  '/items?active_only=true': [],
  '/accounts': [],
  '/accounts?active_only=true': [],
  '/vendors': [],
  '/vendors?active_only=true': [],
  '/settings': given.settings,
  [`/invoices/${given.invoice.id}`]: given.invoice,
  [`/estimates/${given.estimate.id}`]: given.estimate,
  [`/recurring/${given.recurring.id}`]: given.recurring,
  [`/purchase-orders/${given.po.id}`]: given.po,
};

// Elements the pages look up by selector; a test step puts what it needs here.
let els = {};
let modal = '';
const ctx = {
  console,
  window: {},
  location: { hash: '#/' },
  document: {
    getElementById: (id) => els['#' + id] || null,
    querySelector: (sel) => els[sel] || null,
  },
  escapeHtml: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  // utils.js's customerLabel (#638): "Parent: Sub" for a sub-customer
  customerLabel: (c) => (c && (c.full_name || c.name)) || '',
  accountWords: (a, name = null, sep = ' - ') => { const n = name !== null ? name : (a.name || ''); return a.account_number ? `${a.account_number}${sep}${n}` : n; },
  accountLabel: (a, name = null, sep = ' - ') => { const n = name !== null ? name : (a.name || ''); const w = a.account_number ? `${a.account_number}${sep}${n}` : n; return String(w).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); },
  formatCurrency: (n) => '$' + (Number(n) || 0).toFixed(2),
  formatDate: (s) => s || '',
  todayISO: () => '2026-09-26',
  statusBadge: (s) => s,
  T: (s) => s,
  Terms: { text: (s) => s, isNonprofit: () => false, isConstruction: () => false },
  App: { settings: { home_currency: 'USD' }, navigate() {} },
  toast() {},
  closeModal() {},
  confirm: () => true,
  openModal: (title, html) => { modal = html; },
  classFormGroupHtml: async () => '',
  jobFormGroupHtml: async () => '',
  currencyFormGroupsHtml: () => '',
  CostCodes: { load: async () => {}, headHtml: () => '', cellHtml: () => '<td></td>' },
  CostTypes: { load: async () => {}, any: () => false, optionsHtml: () => '', headHtml: () => '', cellHtml: () => '', fromRow: () => null },
  TradePackages: { load: async () => {}, any: () => false, optionsHtml: () => '', headHtml: () => '', cellHtml: () => '', fromRow: () => null, name: () => '' },
  TaxExempt: { enforce() {} },
  ScanHelper: { scanRowHtml: () => '', wire() {} },
  $: (sel) => els[sel] || null,
  $$: (sel) => els[sel] || [],
  API: {
    get: async (path) => {
      if (path.startsWith('/reports/sales-tax?')) return given.report;
      if (!(path in answers)) throw new Error('unexpected ' + path);
      return JSON.parse(JSON.stringify(answers[path]));
    },
  },
};
vm.createContext(ctx);
const pages = {
  'item_quick_add.js': ['ItemQuickAdd'],  // every line's item picker (#445)
  'account_quick_add.js': ['AccountQuickAdd'],  // a purchase line's Account (#485)
  'vendors.js': ['PurchaseLines'],
  'invoices.js': ['SalesLines', 'InvoicesPage'],
  'estimates.js': ['EstimatesPage'],
  'recurring.js': ['RecurringPage'],
  'sales_receipts.js': ['SalesReceiptsPage'],
  'credit_memos.js': ['CreditMemosPage'],
  'purchase_orders.js': ['PurchaseOrdersPage'],
  'vendor_credits.js': ['VendorCreditsPage'],
  'reports.js': ['ReportsPage'],
};
vm.runInContext(fs.readFileSync('app/static/js/payment_terms.js', 'utf8') + '\nthis.PaymentTerms = PaymentTerms;', ctx);
for (const [file, names] of Object.entries(pages)) {
  const expose = names.map((n) => `this.${n} = ${n};`).join(' ');
  vm.runInContext(fs.readFileSync('app/static/js/' + file, 'utf8') + '\n' + expose, ctx);
}
ctx.InvoicesPage.customerSelected = () => {};
ctx.InvoicesPage._recomputeDueDate = () => {};

// The Tax Rate input of the form last opened: its step and its value.
function field(html) {
  const at = html.indexOf('name="tax_rate"');
  if (at < 0) return null;
  const tag = html.slice(html.lastIndexOf('<input', at), html.indexOf('>', at) + 1);
  const attr = (name) => {
    const i = tag.indexOf(` ${name}="`);
    if (i < 0) return null;
    const from = i + name.length + 3;
    return tag.slice(from, tag.indexOf('"', from));
  };
  return { step: attr('step'), value: attr('value') };
}

function row(qty, rate, taxable) {
  const cells = {
    '.line-qty': { value: String(qty) },
    '.line-rate': { value: String(rate) },
    '.line-amount': { textContent: '' },
  };
  if (taxable !== undefined) cells['.line-taxable'] = { checked: taxable, disabled: false, dataset: {} };
  return { querySelector: (s) => cells[s] || null };
}
const tbody = (rows) => ({ querySelectorAll: () => rows });

(async () => {
  const out = { forms: {} };
  const open = async (key, show) => {
    modal = '';
    els = {};
    await show();
    out.forms[key] = field(modal);
  };

  await open('invoiceNew', () => ctx.InvoicesPage.showForm());
  // the new invoice works its tax out from the rate it opened with
  els = {
    '#inv-lines': tbody([row(1, 1000, true)]),
    '#invoice-form [name="tax_rate"]': { value: out.forms.invoiceNew.value },
  };
  out.newInvoiceTotals = ctx.InvoicesPage.recalc();

  await open('invoiceEdit', () => ctx.InvoicesPage.showForm(given.invoice.id));
  await open('estimateEdit', () => ctx.EstimatesPage.showForm(given.estimate.id));
  await open('recurringEdit', () => ctx.RecurringPage.showForm(given.recurring.id));
  await open('salesReceiptNew', () => ctx.SalesReceiptsPage.showForm());
  await open('creditMemoNew', () => ctx.CreditMemosPage.showForm());
  await open('purchaseOrderEdit', () => ctx.PurchaseOrdersPage.showForm(given.po.id));
  await open('vendorCreditNew', () => ctx.VendorCreditsPage.showForm());

  // Crediting an invoice puts its rate in the field as the invoice shows it.
  const cmRate = { value: '0' };
  els = { '#cm-form [name="tax_rate"]': cmRate };
  ctx.CreditMemosPage._invoices = given.invoices;
  ctx.CreditMemosPage.invoiceSelected(String(given.invoice.id));
  out.creditMemoFromInvoice = String(cmRate.value);

  // Half a cent of tax rounds up on every form, as it does on the server.
  const sales = (amount, pct) => ctx.SalesLines.totals(tbody([row(1, amount, true)]), pct).tax;
  out.salesTax = [sales(1000, '8.875'), sales(1000, '7.0625'), sales(175800, '1.0875'), sales(1290, '6.35')];
  const purchase = (form, recalc, amount, pct) => {
    const tax = { textContent: '' };
    els = {
      [`#${form}-lines tr`]: [row(1, amount)],
      [`#${form}-form [name="tax_rate"]`]: { value: pct },
      [`#${form}-tax`]: tax,
    };
    recalc();
    return tax.textContent;
  };
  out.purchaseTax = [
    purchase('po', () => ctx.PurchaseOrdersPage.recalc(), 102, '8.25'),
    purchase('po', () => ctx.PurchaseOrdersPage.recalc(), 1000, '8.875'),
    purchase('vc', () => ctx.VendorCreditsPage.recalc(), 280, '7.0625'),
  ];

  // The Sales Tax report prints each rate as the documents do.
  let report = '';
  ctx.ReportsPage.openPeriodModal = async (_title, _period, load) => {
    report = await load('custom', { start: '2026-09-01', end: '2026-09-30' });
  };
  await ctx.ReportsPage.salesTax();
  out.reportRates = [];
  for (let at = report.indexOf('%</td>'); at >= 0; at = report.indexOf('%</td>', at + 1)) {
    out.reportRates.push(report.slice(report.lastIndexOf('>', at) + 1, at + 1));
  }
  out.percent = [0.08875, '0.070625', '0.0825', 0.07, '0.0000'].map((r) => ctx.SalesLines.taxPercent(r));

  // A scanned receipt's tax keeps four places: $88.75 on $1,000.00 is 8.875%.
  const ocr = { console, window: {} };
  vm.createContext(ocr);
  vm.runInContext(fs.readFileSync('app/static/js/ocr.js', 'utf8') + '\nthis.ScanHelper = ScanHelper;', ocr);
  out.scanned = [
    ocr.ScanHelper.taxPercent('88.75', '1000', '88.75').pct,
    ocr.ScanHelper.taxPercent('70.63', '1000', '70.63').pct,
    ocr.ScanHelper.taxPercent('8.875%', '1000', 'Tax 8.875%').pct,
  ];
  const srRate = { value: '' };
  els = {
    '#sales-receipt-form': { querySelector: (s) => (s === '[name="tax_rate"]' ? srRate : null) },
    '#sr-lines tr': { querySelector: (s) => (s === '.line-rate' ? { value: '1000' } : null) },
  };
  ctx.ScanHelper = ocr.ScanHelper;
  const note = ctx.SalesReceiptsPage._applyScanField('tax', '88.75', { text: '88.75' });
  out.scannedIntoForm = { value: String(srRate.value), note: note && note.note };

  console.log(JSON.stringify(out));
})().catch((err) => { console.error(err && err.stack || String(err)); process.exit(1); });
