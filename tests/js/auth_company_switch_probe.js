// The Company select on a Server Edition host's screens (#358), on a fake
// page: the sign-in of a company whose file is gone (2.25.0 gate, macbase1
// NEW-169, #370), and the setup form of a company nobody has set up yet
// (skytech W-28, #369). What each screen does with the answer to
// POST /api/auth/company, and whether the setup form offers the select.
//
//   node tests/js/auth_company_switch_probe.js
//
// Prints one JSON line per scenario:
//   {"scenario", "reloads", "hash", "overlays", "screen", "has_select",
//    "status_checks", "message"}
const fs = require('fs'), vm = require('vm');

const SRC = fs.readFileSync('app/static/js/auth.js', 'utf8');

function fakeEl(tag) {
  const el = {
    tagName: tag, id: '', children: [], removed: false, textContent: '', value: '',
    disabled: false, listeners: {}, _html: '', _byId: {},
    get innerHTML() { return this._html; },
    set innerHTML(html) { this._html = html; this._index(); },
    get outerHTML() { return '<option value="' + this.value + '">' + this.textContent + '</option>'; },
    _index() {
      for (const m of this._html.matchAll(/id="([^"]+)"/g)) {
        if (!this._byId[m[1]]) { const c = fakeEl('child'); c.id = m[1]; c.root = this; this._byId[m[1]] = c; }
      }
    },
    setAttribute(k, v) { if (k === 'id') this.id = v; },
    appendChild(c) { this.children.push(c); return c; },
    remove() { this.removed = true; },
    querySelector(sel) { return sel.startsWith('#') ? (this._byId[sel.slice(1)] || null) : null; },
    insertAdjacentHTML(_where, html) { const root = this.root || this; root._html += html; root._index(); },
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    async fire(type) { for (const fn of this.listeners[type] || []) await fn({ preventDefault() {} }); },
    focus() {},
  };
  return el;
}

async function scenario(name, companyAnswer, statuses) {
  const body = fakeEl('body');
  let statusChecks = 0, reloads = 0;
  const missingFile = {
    authenticated: false, setup_needed: false, multi_user: false, desktop: false,
    company_name: 'Charlie Cafe',
    company_unavailable: "That company's file is missing from the company folder.",
    companies: [
      { name: 'Alpha Hardware', file: 'alpha.db', current: false, opens: true },
      { name: 'Charlie Cafe', file: 'charlie.db', current: true, opens: true },
    ],
  };
  const win = {
    addEventListener() {},
    location: { hash: '#/reports', reload() { reloads += 1; } },
  };
  const ctx = {
    console, JSON, Promise,
    window: win,
    setTimeout: (fn) => fn(),
    fetch: async (url, opts) => {
      if (url === '/api/auth/status') {
        const status = (statuses || [missingFile])[Math.min(statusChecks, (statuses || [missingFile]).length - 1)];
        statusChecks += 1;
        return { ok: true, json: async () => status };
      }
      if (url === '/api/auth/company') {
        if (companyAnswer.status && companyAnswer.status >= 400) {
          return { ok: false, status: companyAnswer.status, json: async () => ({ detail: companyAnswer.detail }) };
        }
        return { ok: true, status: 200, json: async () => companyAnswer };
      }
      throw new Error('unexpected fetch ' + url);
    },
    document: {
      body,
      createElement: fakeEl,
      addEventListener() {},
      querySelector(sel) {
        const m = sel.match(/^#auth-overlay #(.+)$/);
        const o = body.children.find((c) => c.id === 'auth-overlay' && !c.removed);
        return m && o ? o.querySelector('#' + m[1]) : null;
      },
      getElementById(id) {
        if (id === 'status-text' || id === 'status-company') return fakeEl('span');
        return body.children.find((c) => c.id === id && !c.removed) || null;
      },
    },
  };
  ctx.escapeHtml = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');  // utils.js's, for hint.js (#977)
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync('app/static/js/hint.js', 'utf8'), ctx);  // hint(), hintList() (#977)
  vm.runInContext(SRC, ctx);

  await win.SlowbooksAuth.promptAuth();
  const overlay = () => body.children.find((c) => c.id === 'auth-overlay' && !c.removed);
  const select = overlay().querySelector('#auth-company');
  const hasSelect = overlay().innerHTML.includes('id="auth-company"'); // at first paint
  if (select) {
    select.value = 'alpha.db';
    await select.fire('change');
  }
  await new Promise((r) => setImmediate(r)); // promptAuth's second status check, if any
  const after = overlay();
  const html = after ? after.innerHTML : '';
  const err = after && after.querySelector('#auth-error');
  console.log(JSON.stringify({
    scenario: name,
    reloads,
    hash: win.location.hash,
    overlays: body.children.filter((c) => c.id === 'auth-overlay' && !c.removed).length,
    screen: html.includes('id="auth-password-confirm"') ? 'setup'
      : html.includes('id="auth-title"') ? 'login' : 'none',
    has_select: hasSelect,
    status_checks: statusChecks,
    message: err ? err.textContent : '',
  }));
}

const companies = [
  { name: 'Alpha Hardware', file: 'alpha.db', current: false, opens: false },
  { name: 'Charlie Cafe', file: 'charlie.db', current: true, opens: false },
];
// a stranger's choice of a company nobody has set up yet: its setup form
const charlieSetup = { authenticated: false, setup_needed: true, multi_user: false, desktop: false,
  company_name: 'Charlie Cafe', has_data: false, companies };
// alpha, set up, asks for its password
const alphaSignIn = { authenticated: false, setup_needed: false, multi_user: false, desktop: false,
  company_name: 'Alpha Hardware', companies: companies.map((c) => Object.assign({}, c, { current: c.file === 'alpha.db' })) };
// the desktop's first run, or a server with one company: no list at all
const aloneSetup = { authenticated: false, setup_needed: true, multi_user: false, desktop: true, company_name: 'My Company' };

(async () => {
  // the session's sign-in opens the chosen company too: signed in there now
  await scenario('opens', { authenticated: true });
  // the chosen company asks for its own password: its sign-in, this person chosen
  await scenario('asks', { authenticated: false, username: 'admin' });
  // a company this server does not serve
  await scenario('refused', { status: 404, detail: 'This server does not serve that company.' });
  // #369: the setup form of an unset company offers the other companies,
  // and choosing one that is set up draws its sign-in
  await scenario('setup-offers-the-others', { authenticated: false, username: '' }, [charlieSetup, alphaSignIn]);
  // #369: the administrator's own sign-in opens the chosen company
  await scenario('setup-opens', { authenticated: true }, [charlieSetup]);
  // one company only: the setup form is as it was
  await scenario('setup-alone', { authenticated: true }, [aloneSetup]);
})();
