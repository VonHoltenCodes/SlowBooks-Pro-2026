// app.js's read-only pass on a fake page (2.18.0 gate, W-L17 leftovers):
// what data-write, a file chooser, a page's own form and a dialog that fills
// in later come to for a read-only sign-in, and for an admin; and the
// administrator's pages (Payroll, HR) opened by address. Elements are a small
// tree with just the selectors app.js asks for. Prints one JSON object.
const fs = require('fs'), vm = require('vm');

function matchOne(el, sel) {
  let s = sel.trim(), m;
  let notAttr = null, attrHas = null, attrEq = null;
  if ((m = /:not\(\[([\w-]+)\]\)$/.exec(s))) { notAttr = m[1]; s = s.slice(0, m.index); }
  if ((m = /\[([\w-]+)="([^"]*)"\]$/.exec(s))) { attrEq = [m[1], m[2]]; s = s.slice(0, m.index); }
  else if ((m = /\[([\w-]+)\]$/.exec(s))) { attrHas = m[1]; s = s.slice(0, m.index); }
  const [tag, ...classes] = s.split('.');
  if (tag && el.tagName.toLowerCase() !== tag) return false;
  if (classes.some(c => !el.classList.contains(c))) return false;
  if (attrHas && !el.hasAttribute(attrHas)) return false;
  if (attrEq && el.getAttribute(attrEq[0]) !== attrEq[1]) return false;
  if (notAttr && el.hasAttribute(notAttr)) return false;
  return true;
}

class El {
  constructor(tag, attrs = {}, children = [], text = '') {
    this.tagName = tag.toUpperCase();
    this.attrs = { ...attrs };
    this.children = [];
    this.parentElement = null;
    this.ownText = text;
    this.disabled = 'disabled' in attrs;
    this.style = {};
    const cls = new Set((attrs.class || '').split(/\s+/).filter(Boolean));
    this.classList = {
      contains: c => cls.has(c), add: c => cls.add(c), remove: c => cls.delete(c),
      toggle: (c, on) => (on ? cls.add(c) : cls.delete(c)),
    };
    children.forEach(c => this.append(c));
  }
  append(c) { c.parentElement = this; this.children.push(c); return c; }
  get textContent() { return this.ownText + this.children.map(c => c.textContent).join(''); }
  getAttribute(k) { return k in this.attrs ? String(this.attrs[k]) : null; }
  hasAttribute(k) { return k in this.attrs; }
  all() { return this.children.flatMap(c => [c, ...c.all()]); }
  matches(sel) { return sel.split(',').some(s => matchOne(this, s)); }
  querySelectorAll(sel) { return this.all().filter(e => e.matches(sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  closest(sel) { for (let e = this; e; e = e.parentElement) if (e.matches(sel)) return e; return null; }
  insertAdjacentHTML(_where, html) {
    const note = new El('div', { class: 'readonly-note' }, [], html);
    note.parentElement = this;
    this.children.unshift(note);
  }
  get hidden() { return this.classList.contains('hidden'); }
}
const button = (label, attrs = {}) => new El('button', { class: 'btn btn-secondary', ...attrs }, [], label);

// ---- the page, the dialog and the sidebar ------------------------------
const deleteAccount = button('Delete', { 'data-write': '', onclick: 'App.deleteAccount(2)' });
const budgetCell = new El('input', { type: 'number', class: 'budget-cell', 'data-write': '' });
const importPanel = new El('div', { class: 'settings-section', 'data-write': '' }, [button('Import', { type: 'submit' })]);
const chooser = new El('input', { type: 'file', id: 'inv-attach-file' });
const view = button('View', { onclick: 'InvoicesPage.view(1)' });
const exportLink = new El('a', { class: 'btn btn-secondary', href: '/api/csv/export/customers' }, [], 'Export Customers');
const filter = new El('select', { id: 'inv-status-filter' });

const field = new El('input', { name: 'company_name' });
const choice = new El('select', { name: 'default_terms' });
const save = button('Save Settings', { type: 'submit', class: 'btn btn-primary' });
const openTemplate = button('Edit', { 'data-readonly-ok': '', onclick: 'SettingsPage.editTemplate(5)' });
const settingsForm = new El('form', { id: 'settings-form' }, [field, choice, openTemplate, save]);
const pickCustomer = new El('select', { name: 'customer_id' });
const statement = new El('form', { 'data-readonly-ok': '' }, [pickCustomer, button('Generate PDF', { type: 'submit' })]);

const page = new El('div', { id: 'page-content' },
  [deleteAccount, budgetCell, importPanel, chooser, view, exportLink, filter, settingsForm, statement]);
const cancel = button('Cancel', { type: 'button', onclick: 'closeModal()' });
const modal = new El('div', { id: 'modal-body' }, [cancel]);
const batchLink = new El('li', { 'data-write': '' }, [new El('a', { class: 'nav-link' }, [], 'Batch Payments')]);
const reportsLink = new El('li', {}, [new El('a', { class: 'nav-link' }, [], 'Reports')]);
const sidebar = new El('nav', { id: 'sidebar' }, [batchLink, reportsLink]);

const observed = [];
let observerCallback = null;
class FakeObserver {
  constructor(cb) { observerCallback = cb; }
  observe(target) { observed.push(target.getAttribute('id')); }
}

let hash = '#/';
const pageBox = { innerHTML: '' };
const rendered = [];
const ctx = {
  console, setTimeout, clearTimeout, Promise, JSON, Date,
  setInterval: () => 0,
  MutationObserver: FakeObserver,
  location: { get hash() { return hash; }, set hash(v) { hash = v; } },
  history: { pushState(_s, _t, url) { hash = url; }, replaceState(_s, _t, url) { hash = url; } },
  localStorage: { getItem: () => null, setItem() {} },
  document: {
    body: new El('body'),
    addEventListener() {},
    getElementById: id => ({ 'page-content': page, 'modal-body': modal, sidebar }[id] || null),
    querySelector: () => null,
    querySelectorAll: () => [],
  },
  $: sel => (sel === '#page-content' ? pageBox : null),
  $$: () => [],
  escapeHtml: s => String(s ?? ''),
  toast: () => {},
  closeModal: () => {},
  T: s => s,
  Terms: { isNonprofit: () => false, isConstruction: () => false },
  PayrollPage: { render: async () => { rendered.push('payroll'); return '<payroll>'; } },
  AuditPage: { render: async () => { rendered.push('audit'); return '<audit>'; } },
};
ctx.window = { addEventListener() {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/app.js', 'utf8') + '\nthis.App = App;', ctx);
const App = ctx.App;

const state = () => ({
  delete_hidden: deleteAccount.hidden,
  budget_cell: { hidden: budgetCell.hidden, disabled: budgetCell.disabled },
  import_panel_hidden: importPanel.hidden,
  chooser: { hidden: chooser.hidden, disabled: chooser.disabled },
  reads_untouched: [view, exportLink, filter].every(e => !e.hidden && !e.disabled),
  settings_form: {
    fields_disabled: field.disabled && choice.disabled,
    save_disabled: save.disabled,
    template_opens: !openTemplate.disabled && !openTemplate.hidden,
    notes: settingsForm.querySelectorAll('.readonly-note').length,
  },
  statement_usable: !pickCustomer.disabled && !statement.querySelector('.readonly-note'),
  cancel_enabled: !cancel.disabled,
  sidebar: { batch_hidden: batchLink.hidden, reports_hidden: reportsLink.hidden },
});

(async () => {
  const out = {};
  // an admin: nothing changes
  App.readOnlyPass(page);
  App.hideWriteControls(sidebar);
  out.admin = state();

  // the page opens as the role arrives (auth status answers after it)
  App.setRole('readonly');
  out.readonly = state();
  // run again, as every re-render does: still one sentence
  App.readOnlyPass(page);
  out.notes_after_second_pass = settingsForm.querySelectorAll('.readonly-note').length;
  out.observed = observed;

  // a dialog that fills in after it opens (AR Aging's Apply Late Fees)
  const lateFees = button('Apply Late Fees', { onclick: 'ReportsPage.applyLateFees()' });
  const letters = new El('div', { 'data-write': '' }, [new El('select', { id: 'collection-letter-type' })]);
  modal.append(lateFees);
  modal.append(letters);
  observerCallback([]);
  out.late_dialog = { late_fees_hidden: lateFees.hidden, letters_hidden: letters.hidden };

  // Payroll by its address: the administrator's page
  await App.navigate('#/payroll');
  out.payroll_readonly = { says: pageBox.innerHTML.includes('Payroll is for administrators'), rendered: rendered.slice() };
  App.role = 'admin';
  await App.navigate('#/payroll');
  out.payroll_admin = { rendered: rendered.slice(), page: pageBox.innerHTML };

  // the first page, Payroll by its address, is still loading when the role
  // arrives (auth status answers after it): the page loaded is not shown
  let release;
  ctx.PayrollPage.render = () => new Promise(done => { release = () => done('<payroll>'); });
  const loading = App.navigate('#/payroll');
  App.role = 'readonly';
  release();
  await loading;
  out.payroll_role_arrives_late = pageBox.innerHTML.includes('Payroll is for administrators');

  // the Audit Log by its address: closed to a read-only sign-in, reads too
  rendered.length = 0;
  App.role = 'readonly';
  await App.navigate('#/audit');
  out.audit_readonly = {
    says: pageBox.innerHTML.includes("Audit Log isn't open to a read-only sign-in"),
    rendered: rendered.slice(),
  };
  App.role = 'bookkeeper';
  await App.navigate('#/audit');
  out.audit_bookkeeper = { rendered: rendered.slice(), page: pageBox.innerHTML };
  console.log(JSON.stringify(out));
})().catch(err => { console.error(err); process.exit(1); });
