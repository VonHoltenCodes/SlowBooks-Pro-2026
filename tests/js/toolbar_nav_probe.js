// app.js and bootstrap.js on a fake page whose address behaves like a
// browser's: setting location.hash to a new value fires hashchange (after the
// current task), setting it to the value it already has fires nothing, and
// history.pushState / replaceState change the address without firing
// anything. A sidebar link is `location.hash = its href`; a toolbar button is
// the click handler bootstrap.js wires to [data-nav]. Pages are stubs that
// record their render. Prints one JSON line per step:
//   {"step", "hash", "rendered": [pages rendered by that step], "back": [the history stack]}
const fs = require('fs'), vm = require('vm');

let rendered = [];
const stub = (name) => new Proxy({}, {
  get: (_t, prop) => {
    if (prop === 'then') return undefined; // not a promise
    return (...args) => {
      if (prop === 'render') rendered.push(name);
      return prop === 'render' ? `<${name}>` : '';
    };
  },
});

let hash = '#/settings';
const stack = [hash];
const listeners = {};
const fire = () => setTimeout(() => listeners.hashchange && listeners.hashchange(), 0);
const location = {
  get hash() { return hash; },
  set hash(v) {
    const next = String(v).startsWith('#') ? String(v) : `#${v}`;
    if (next === hash) return; // the same address: no hashchange, no navigation
    hash = next;
    stack.push(next);
    fire();
  },
};
const history = {
  pushState(_s, _t, url) { hash = url; stack.push(url); },
  replaceState(_s, _t, url) { hash = url; stack[stack.length - 1] = url; },
  back() { stack.pop(); hash = stack[stack.length - 1]; fire(); },
};

// the toolbar, as index.html has it
const toolbar = {};
const navButtons = ['#/', '#/quick-entry', '#/reports'].map((nav) => {
  const b = { dataset: { nav }, addEventListener(name, fn) { if (name === 'click') toolbar[nav] = fn; } };
  return b;
});

const pageContent = { innerHTML: '' };
const ctx = {
  console, setTimeout, clearTimeout, Promise, JSON, Date,
  setInterval: () => 0,
  location, history,
  localStorage: { getItem: () => null, setItem() {} },
  fetch: async () => ({ ok: false, json: async () => ({}) }),
  document: {
    title: '',
    documentElement: { getAttribute: () => null, setAttribute() {} },
    addEventListener() {},
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: (sel) => (sel === '[data-nav]' ? navButtons : []),
  },
  $: (sel) => (sel === '#page-content' ? pageContent : null),
  $$: () => [],
  escapeHtml: (s) => String(s ?? ''),
  toast: () => {},
  closeModal: () => {},
  T: (s) => s,
  Terms: { init() {}, text: (s) => s, isNonprofit: () => false, isConstruction: () => false },
  API: { get: async () => ({ company_name: 'Harbor Light Bakery Two', company_type: 'business' }) },
};
ctx.window = { addEventListener(name, fn) { listeners[name] = fn; } };
const src = fs.readFileSync('app/static/js/app.js', 'utf8');
for (const name of new Set(src.match(/\b[A-Z][A-Za-z]*Page\b/g) || [])) ctx[name] = stub(name);
vm.createContext(ctx);
vm.runInContext(src + '\nthis.App = App;', ctx);
ctx.window.App = ctx.App;
// App's own pages, as stubs too
ctx.App.renderAccounts = async () => { rendered.push('ChartOfAccounts'); return '<ChartOfAccounts>'; };
ctx.App.renderQuickEntry = async () => { rendered.push('QuickEntry'); return '<QuickEntry>'; };
vm.runInContext(fs.readFileSync('app/static/js/bootstrap.js', 'utf8'), ctx);

const settle = () => new Promise((r) => setTimeout(r, 20));
const log = (step) => {
  console.log(JSON.stringify({ step, hash, rendered, back: stack.slice() }));
  rendered = [];
};
const sidebar = async (href) => { location.hash = href; await settle(); };
const click = async (nav) => { toolbar[nav](); await settle(); };

(async () => {
  ctx.App.init();
  await settle();
  log('start-on-settings');

  await click('#/');
  log('toolbar-home');
  await sidebar('#/settings');
  log('sidebar-settings');

  await click('#/quick-entry');
  log('toolbar-quick-entry');
  await sidebar('#/settings');
  log('sidebar-settings-again');

  await sidebar('#/accounts');
  log('sidebar-accounts');
  await click('#/reports');
  log('toolbar-reports');
  await sidebar('#/accounts');
  log('sidebar-accounts-again');

  history.back();
  await settle();
  log('back');

  // an old bookmark: the alias shows Banking, and Back does not return to it
  await sidebar('#/check-register');
  log('old-check-register-bookmark');
})();
