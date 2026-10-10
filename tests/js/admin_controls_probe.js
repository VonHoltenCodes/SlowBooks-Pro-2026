// The pages with the administrator's controls, drawn for each role and passed
// through app.js the way a page is (App.setRole, then its observer): Settings
// with its lists filled in and the template editor open, QuickBooks Online
// connected and not, Companies, and Migrate Data by its address. For each:
// which controls each sign-in is shown and offered, which fields are locked,
// and the sentences shown. Also the order of a first page, drawn before
// /api/auth/status names the role. A page is a small tree built from its own
// HTML, with just the selectors the scripts ask for. Prints one JSON object.
const fs = require('fs'), vm = require('vm');

// ---- a small DOM ---------------------------------------------------------
const VOID = new Set(['area', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'wbr']);
const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', nbsp: ' ', mdash: '—', middot: '·', hellip: '…', times: '×', rarr: '→', ldquo: '“', rdquo: '”' };
const decode = s => s.replace(/&(#\d+|[a-z]+);/g, (m, e) =>
  e[0] === '#' ? String.fromCharCode(+e.slice(1)) : (ENTITIES[e] ?? m));

let changed = () => {};  // a child list changed: the observers' hook

class Text {
  constructor(text) { this.nodeType = 3; this.text = text; this.parentElement = null; }
  get textContent() { return this.text; }
}

class El {
  constructor(tag, attrs = {}) {
    this.nodeType = 1;
    this.tagName = tag.toUpperCase();
    this.attrs = { ...attrs };
    this.childNodes = [];
    this.parentElement = null;
    this.style = {};
    for (const decl of (attrs.style || '').split(';')) {
      const [k, v] = decl.split(':').map(x => (x || '').trim());
      if (k === 'display') this.style.display = v;
    }
    this._disabled = 'disabled' in attrs;
    this._checked = 'checked' in attrs;
    const el = this;
    const set = () => new Set((el.attrs.class || '').split(/\s+/).filter(Boolean));
    this.classList = {
      contains: c => set().has(c),
      add: (...cs) => { const s = set(); cs.forEach(c => s.add(c)); el.attrs.class = [...s].join(' '); },
      remove: (...cs) => { const s = set(); cs.forEach(c => s.delete(c)); el.attrs.class = [...s].join(' '); },
      toggle: (c, on) => {
        if (on === undefined) on = !set().has(c);
        if (on) el.classList.add(c); else el.classList.remove(c);
        return on;
      },
      forEach: fn => set().forEach(fn),
    };
  }
  get children() { return this.childNodes.filter(n => n.nodeType === 1); }
  get id() { return this.attrs.id || ''; }
  get name() { return this.attrs.name || ''; }
  get type() {
    const t = this.attrs.type || { BUTTON: 'submit', INPUT: 'text', SELECT: 'select-one' }[this.tagName] || '';
    return t.toLowerCase();
  }
  get disabled() { return this._disabled; }
  set disabled(v) { this._disabled = !!v; }
  get checked() { return this._checked; }
  set checked(v) { this._checked = !!v; }
  get hidden() { return 'hidden' in this.attrs; }
  set hidden(v) { if (v) this.attrs.hidden = ''; else delete this.attrs.hidden; }
  get value() {
    if (this._value !== undefined) return this._value;
    if (this.tagName === 'SELECT') {
      const opts = this.querySelectorAll('option');
      const pick = opts.find(o => o.hasAttribute('selected')) || opts[0];
      return pick ? (pick.getAttribute('value') ?? pick.textContent) : '';
    }
    if (this.tagName === 'TEXTAREA') return this.textContent;
    return this.getAttribute('value') ?? '';
  }
  set value(v) { this._value = String(v); }
  get dataset() {
    const out = {};
    for (const [k, v] of Object.entries(this.attrs)) {
      if (k.startsWith('data-')) out[k.slice(5).replace(/-(\w)/g, (_m, c) => c.toUpperCase())] = v;
    }
    return out;
  }
  getAttribute(k) { return k in this.attrs ? String(this.attrs[k]) : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  hasAttribute(k) { return k in this.attrs; }
  removeAttribute(k) { delete this.attrs[k]; }
  get textContent() { return this.childNodes.map(n => n.textContent).join(''); }
  set textContent(t) { this._adopt([new Text(String(t))]); }
  set innerHTML(html) { const box = parse(String(html)); this._adopt(box.childNodes); }
  _adopt(nodes) {
    this.childNodes = nodes;
    nodes.forEach(n => { n.parentElement = this; });
    changed(this);
  }
  insertAdjacentHTML(where, html) {
    const nodes = parse(String(html)).childNodes;
    const into = where === 'afterbegin' || where === 'beforeend' ? this : this.parentElement;
    let at = { afterbegin: 0, beforeend: this.childNodes.length }[where];
    if (at === undefined) at = into.childNodes.indexOf(this) + (where === 'afterend' ? 1 : 0);
    nodes.forEach(n => { n.parentElement = into; });
    into.childNodes.splice(at, 0, ...nodes);
    changed(into);
  }
  append(...nodes) {
    nodes.forEach(n => { n.parentElement = this; this.childNodes.push(n); });
    changed(this);
  }
  remove() {
    const p = this.parentElement;
    if (!p) return;
    p.childNodes = p.childNodes.filter(n => n !== this);
    this.parentElement = null;
    changed(p);
  }
  contains(node) { for (let e = node; e; e = e.parentElement) if (e === this) return true; return false; }
  all() { return this.children.flatMap(c => [c, ...c.all()]); }
  matches(sel) { return parseList(sel).some(chain => matchChain(this, chain)); }
  querySelectorAll(sel) {
    const list = parseList(sel);
    return this.all().filter(e => list.some(chain => matchChain(e, chain)));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  closest(sel) { for (let e = this; e; e = e.parentElement) if (e.matches(sel)) return e; return null; }
  addEventListener() {}
  scrollIntoView() {}
  focus() {}
}

// A tag at the reading position: an end tag (anything up to its ">"), or a
// start tag with its attributes. Comments and text are read around it.
const TAG = /<\/([a-z][\w-]*)[^>]*>|<([a-z][\w-]*)((?:\s+[^\s"'<>/=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?)*)\s*(\/?)>/iy;
const ATTR = /([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;

function parse(html) {
  const root = new El('template');
  let cur = root;
  let i = 0;
  while (i < html.length) {
    if (html.startsWith('<!--', i)) {  // a comment ends at --> or --!>
      const ends = ['-->', '--!>'].map(e => [html.indexOf(e, i + 4), e.length]).filter(([at]) => at >= 0);
      i = ends.length ? Math.min(...ends.map(([at, n]) => at + n)) : html.length;
      continue;
    }
    TAG.lastIndex = i;
    const m = TAG.exec(html);
    if (m) {
      i = TAG.lastIndex;
      if (m[1]) {  // an end tag: back up to the element it closes, if open
        const tag = m[1].toUpperCase();
        let e = cur;
        while (e !== root && e.tagName !== tag) e = e.parentElement;
        if (e !== root) cur = e.parentElement;
        continue;
      }
      const attrs = {};
      for (const a of (m[3] || '').matchAll(ATTR)) attrs[a[1].toLowerCase()] = decode(a[2] ?? a[3] ?? a[4] ?? '');
      const el = new El(m[2], attrs);
      el.parentElement = cur;
      cur.childNodes.push(el);
      if (!VOID.has(m[2].toLowerCase()) && !m[4]) cur = el;
      continue;
    }
    // text, up to the next "<" (a "<" that starts no tag is text too)
    const next = html.indexOf('<', i + 1);
    const t = new Text(decode(html.slice(i, next < 0 ? html.length : next)));
    t.parentElement = cur;
    cur.childNodes.push(t);
    i = next < 0 ? html.length : next;
  }
  return root;
}

// Selectors: lists of descendant chains of tag, #id, .class, [attr],
// [attr=value] and :not(...).
const parsed = new Map();
function parseCompound(s) {
  const c = { tag: null, id: null, classes: [], attrs: [], nots: [] };
  let rest = s, m;
  if ((m = /^([a-zA-Z][\w-]*|\*)/.exec(rest))) {
    if (m[1] !== '*') c.tag = m[1].toUpperCase();
    rest = rest.slice(m[0].length);
  }
  while (rest) {
    if ((m = /^#([\w-]+)/.exec(rest))) c.id = m[1];
    else if ((m = /^\.([\w-]+)/.exec(rest))) c.classes.push(m[1]);
    else if ((m = /^\[\s*([\w-]+)\s*(?:=\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]+))\s*)?\]/.exec(rest))) c.attrs.push([m[1], m[2] ?? m[3] ?? m[4]]);
    else if ((m = /^:not\(([^()]*)\)/.exec(rest))) c.nots.push(parseCompound(m[1].trim()));
    else throw new Error(`selector not understood: ${s}`);
    rest = rest.slice(m[0].length);
  }
  return c;
}
function parseList(sel) {
  if (!parsed.has(sel)) parsed.set(sel, sel.split(',').map(p => p.trim().split(/\s+/).map(parseCompound)));
  return parsed.get(sel);
}
function matchCompound(el, c) {
  if (el.nodeType !== 1) return false;
  if (c.tag && el.tagName !== c.tag) return false;
  if (c.id && el.id !== c.id) return false;
  if (c.classes.some(k => !el.classList.contains(k))) return false;
  for (const [k, v] of c.attrs) {
    if (!el.hasAttribute(k) || (v !== undefined && el.getAttribute(k) !== v)) return false;
  }
  return !c.nots.some(n => matchCompound(el, n));
}
function matchChain(el, chain, i = chain.length - 1) {
  if (!matchCompound(el, chain[i])) return false;
  if (i === 0) return true;
  for (let a = el.parentElement; a; a = a.parentElement) if (matchChain(a, chain, i - 1)) return true;
  return false;
}

// ---- the books the pages show ---------------------------------------------
const SETTINGS = {
  company_name: 'Harbor Light Bakery', company_type: 'business', default_terms: 'Net 30',
  default_tax_rate: '8.25', invoice_next_number: '1001', estimate_next_number: '1001',
  company_logo_path: '/api/uploads/logo/3', invoice_show_logo: 'true',
  closing_date: '2026-06-30', smtp_host: 'smtp.example.com', ocr_engine: 'auto',
};
const ANSWERS = {
  '/settings': SETTINGS,
  '/uploads/logo': { path: '/api/uploads/logo/3', missing: false, from_shared_folder: false },
  '/backups': [{ filename: 'harbor-light_2026-09-25_2210.db', file_size: 184320, backup_type: 'manual', created_at: '2026-09-25T22:10:04+00:00' }],
  '/email-templates': [{ id: 1, name: 'Invoice', template_type: 'invoice', subject_template: 'Invoice {{ invoice.invoice_number }}' }],
  '/email-templates/1': { id: 1, name: 'Invoice', template_type: 'invoice', subject_template: 'Invoice', body_template: '<p>Hello</p>' },
  '/invoices?is_sales_receipt=false&limit=50': [{ id: 7, invoice_number: '1001', customer_name: 'Maple Street Cafe' }],
  '/analytics/ai-config': {
    provider: 'anthropic', model: 'claude-sonnet', has_api_key: true, api_key_provider: 'anthropic',
    providers: [{ key: 'anthropic', label: 'Anthropic Claude', model_choices: ['claude-sonnet'], default_model: 'claude-sonnet' }],
  },
  '/classes?include_archived=true': [{ id: 1, name: 'Retail', is_archived: false, is_system_default: false }],
  '/cost-codes?include_inactive=true': [{ id: 1, code: '03', name: 'Concrete', cost_type: 'material', is_active: true, depth: 0, label: '03 Concrete' }],
  '/cost-types?include_inactive=true': [{ id: 1, code: 'labor', name: 'Labor', is_labor: true, burden_pct: 10, burden_method: 'flat', is_active: true }],
  '/equipment?include_inactive=true': [{ id: 1, code: 'SK', name: 'Skid steer', hourly_rate: 85, is_active: true }],
  '/accounts': [{ id: 1, account_number: '5000', name: 'Materials' }],
  '/system': { desktop: true },
  '/ocr/status': { available: false },
  '/users': [],
  '/tokens': [],
  '/companies': [{ name: 'Harbor Light Bakery', database_name: 'harbor_light', is_current: true }],
  '/migration/sources': [{ key: 'xero', label: 'Xero' }],
};
const PAGES = ['app', 'settings', 'qbo', 'companies', 'migration'];

// ---- a fresh window with the app's scripts in it -------------------------
function newWindow(role, answers = {}) {
  const html = new El('html');
  const body = new El('body');
  html.append(body);
  const sidebar = parse(`<nav id="sidebar"><ul>
      <li><a href="#/settings" class="nav-link active" data-page="settings">Settings</a></li>
      <li data-write><a href="#/migrate" class="nav-link" data-page="migrate">Migrate Data</a></li>
    </ul></nav>`).children[0];
  const page = new El('div', { id: 'page-content' });
  const modal = new El('div', { id: 'modal-body' });
  body.append(sidebar, page, modal);

  const observers = [];
  changed = (node) => {
    for (const o of observers) {
      if (o.pending || !o.targets.some(t => t.contains(node))) continue;
      o.pending = true;
      queueMicrotask(() => { o.pending = false; if (o.targets.length) o.cb([]); });
    }
  };
  class FakeObserver {
    constructor(cb) { this.cb = cb; this.targets = []; this.pending = false; observers.push(this); }
    observe(t) { this.targets.push(t); }
    disconnect() { this.targets = []; }
  }
  class FakeFormData {
    constructor(form) { this.form = form; }
    entries() {
      return this.form.querySelectorAll('input[name], select[name], textarea[name]')
        .filter(el => !el.disabled && el.type !== 'file' && (!/^(checkbox|radio)$/.test(el.type) || el.checked))
        .map(el => [el.name, el.value])[Symbol.iterator]();
    }
  }

  const w = { page, modal, sidebar, asked: [], modalTitle: '' };
  const document = {
    body,
    getElementById: id => html.all().find(e => e.id === id) || null,
    querySelector: sel => html.querySelector(sel),
    querySelectorAll: sel => html.querySelectorAll(sel),
    addEventListener() {},
  };
  const location = { hash: '#/settings' };
  const reply = (path) => {
    if (path === '/auth/status') return { user: { role } };
    if (path in answers) return answers[path];
    return ANSWERS[path] ?? {};
  };
  const ctx = {
    console, JSON, Date, Promise, setTimeout, clearTimeout, queueMicrotask,
    setInterval: () => 0,
    MutationObserver: FakeObserver,
    FormData: FakeFormData,
    document,
    window: { addEventListener() {} },
    location,
    history: {
      pushState(_s, _t, url) { location.hash = url; },
      replaceState(_s, _t, url) { location.hash = url; },
    },
    localStorage: { getItem: () => null, setItem() {} },
    sessionStorage: { getItem: () => null, removeItem() {} },
    confirm: (msg) => { w.asked.push(msg); return false; },
    prompt: () => null,
    $: sel => document.querySelector(sel),
    $$: sel => document.querySelectorAll(sel),
    escapeHtml: s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]),
    T: s => s,
    Terms: { text: s => s, isNonprofit: () => false },
    formatDate: s => s,
    formatCurrency: n => `$${Number(n).toFixed(2)}`,
    toast() {},
    closeModal() {},
    copyToClipboard() {},
    API: {
      get: async (path) => reply(path),
      post: async () => ({}), put: async () => ({}), del: async () => ({}),
    },
    // initSystemInfo asks with fetch, before any page
    fetch: async (url) => ({
      ok: true,
      json: async () => (url === '/api/auth/status'
        ? { multi_user: true, user: { username: 'kim', role } }
        : { version: '2.18.0', server_mode: true, update_check_enabled: false }),
    }),
  };
  ctx.openModal = (title, content) => {  // as utils.js does it
    w.modalTitle = title;
    modal.innerHTML = content;
    ctx.App.lockForms(modal);
    ctx.App.hideWriteControls(modal);
  };
  vm.createContext(ctx);
  for (const name of PAGES) {
    const src = fs.readFileSync(`app/static/js/${name}.js`, 'utf8');
    const obj = /^const ([A-Z]\w*) = \{/m.exec(src)[1];
    vm.runInContext(`${src}\nthis.${obj} = ${obj};`, ctx);
  }
  w.ctx = ctx;
  w.App = ctx.App;
  w.settle = async () => { for (let i = 0; i < 5; i++) await new Promise(r => setTimeout(r, 5)); };
  return w;
}

// ---- what a page offers ----------------------------------------------------
const visible = (el) => {
  for (let e = el; e; e = e.parentElement) {
    if (e.classList.contains('hidden') || e.hasAttribute('hidden') || e.style.display === 'none') return false;
  }
  return true;
};
const label = (el) => {
  if (/^(BUTTON|A)$/.test(el.tagName)) return el.textContent.replace(/\s+/g, ' ').trim();
  if (el.name) return el.name;
  if (el.id) return `#${el.id}`;
  if (el.getAttribute('class')) return `.${el.getAttribute('class').split(/\s+/)[0]}`;
  return `[value=${el.getAttribute('value')}]`;
};
// Every control, by the section it is in ("Classes › Add Class"): shown,
// and offered (shown and enabled)
function controls(root, prefix) {
  const shown = {}, offered = {};
  for (const el of root.querySelectorAll('button, a.btn, input, select, textarea')) {
    const section = el.closest('.settings-section, .iif-section');
    const heading = section && section.querySelector('h3');
    const where = prefix || (heading ? heading.textContent.replace(/\s+/g, ' ').replace(/^[^\w]+/, '').trim() : '');
    const key = where ? `${where} › ${label(el)}` : label(el);
    shown[key] = visible(el);
    offered[key] = visible(el) && !el.disabled;
  }
  return { shown, offered };
}
const notes = root => root.querySelectorAll('[data-admin-note]').filter(visible).map(n => n.textContent.trim());

// ---- Settings, with its lists and the template editor ----------------------
async function settings(role, late) {
  const w = newWindow(role);
  const { App, page, modal } = w;
  const { SettingsPage } = w.ctx;
  App.navigate = async () => {};  // leaving: only whether it asks
  if (!late) App.setRole(role);
  page.innerHTML = await SettingsPage.render();
  await w.settle();
  // the first page's order: the role arrives after Settings is in
  if (late) { App.setRole(role); await w.settle(); }
  await SettingsPage.editTemplate(1);
  await w.settle();

  const onPage = controls(page, '');
  const inDialog = controls(modal, w.modalTitle);
  const form = page.querySelector('#settings-form');
  const fields = form.querySelectorAll('input[name], select[name], textarea[name]');
  const state = {
    offered: { ...onPage.offered, ...inDialog.offered },
    fields: {
      total: fields.length,
      names: fields.map(f => f.name),
      enabled: fields.filter(f => !f.disabled).map(f => f.name),
    },
    notes: notes(page),
    readonly_notes: page.querySelectorAll('.readonly-note').filter(visible).length,
    dirty: SettingsPage.isDirty(),
    // a button with no type submits the form it is in: Save Settings
    submits: form.querySelectorAll('button').filter(b => b.type === 'submit').map(label),
  };
  // leaving Settings: whether it asks about unsaved changes
  await App.navigate('#/reports');
  state.asked_on_leaving = w.asked.length;
  // an administrator's edit is still noticed
  form.querySelector('[name="company_name"]').value = 'Harbor Light Bakery & Cafe';
  state.dirty_after_edit = SettingsPage.isDirty();
  return state;
}

// ---- a page drawn once the role is known: QBO, Companies ------------------
async function drawn(role, draw, answers) {
  const w = newWindow(role, answers);
  w.App.setRole(role);
  w.page.innerHTML = await draw(w.ctx);
  await w.settle();
  return { ...controls(w.page, ''), notes: notes(w.page) };
}

// ---- Migrate Data by its address, and the sidebar ------------------------
async function migrate(role, late) {
  const w = newWindow(role);
  const { App, page, sidebar } = w;
  if (late) {
    await App.navigate('#/migrate');   // drawn before the role is known
    App.setRole(role);
  } else {
    await App.initSystemInfo();        // the role, and the sidebar for it
    await App.navigate('#/migrate');
  }
  await w.settle();
  const link = sidebar.querySelector('a[data-page="migrate"]');
  return {
    text: page.textContent.replace(/\s+/g, ' ').trim(),
    buttons: page.querySelectorAll('button, a.btn').filter(visible).map(b => b.textContent.trim()),
    sidebar_link_shown: visible(link),
  };
}

(async () => {
  const out = { settings: {}, qbo: {}, qbo_connected: {}, companies: {}, migrate: {} };
  out.settings.admin = await settings('admin', false);
  out.settings.bookkeeper = await settings('bookkeeper', false);
  out.settings.bookkeeper_first_page = await settings('bookkeeper', true);
  out.settings.readonly = await settings('readonly', false);
  const connected = { '/qbo/status?include_company_name=false': { connected: true, company_name: 'Harbor Light', realm_id: '4620816365' } };
  const notConnected = { '/qbo/status?include_company_name=false': { connected: false, company_name: '', realm_id: '' } };
  for (const role of ['admin', 'bookkeeper', 'readonly']) {
    out.qbo[role] = await drawn(role, c => c.QBOPage.render(), notConnected);
    out.qbo_connected[role] = await drawn(role, c => c.QBOPage.render(), connected);
    out.companies[role] = await drawn(role, c => c.CompaniesPage.render());
    out.migrate[role] = await migrate(role, false);
  }
  out.migrate.bookkeeper_first_page = await migrate('bookkeeper', true);
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
