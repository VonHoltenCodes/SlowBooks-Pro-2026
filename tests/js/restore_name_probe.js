// Settings' Restore dialog and a rename, on a fake page with app.js and
// settings.js loaded together. The shell's copy of the settings is what the
// window read when it opened ("Harbor Light Bakery"); the books may have been
// renamed since. Prints one JSON line per step with the names the dialog and
// the shell show.
const fs = require('fs'), vm = require('vm');

const els = {
  '#status-company': { textContent: 'Company: Harbor Light Bakery' },
  '#topbar-company': { textContent: 'Harbor Light Bakery' },
};
let modal = '';
let books = { company_name: 'Harbor Light Cafe' }; // renamed in another window
let settingsReachable = true;

class FakeFormData {
  constructor(f) { this.f = f; }
  entries() { return [['company_name', 'Harbor Light Bakery & Cafe']][Symbol.iterator](); }
}

const ctx = {
  console, JSON, Promise, setTimeout, clearTimeout,
  FormData: FakeFormData,
  location: { hash: '#/settings' },
  history: { pushState() {}, replaceState() {} },
  document: {
    title: 'Harbor Light Bakery — Slowbooks Pro 2026',
    addEventListener() {},
    getElementById: () => null,
    querySelectorAll: () => [],
  },
  $: (sel) => els[sel] || null,
  $$: () => [],
  escapeHtml: (s) => String(s ?? ''),
  toast: () => {},
  openModal: (_title, html) => { modal = html; },
  closeModal: () => {},
  T: (s) => s,
  Terms: { init() {}, text: (s) => s, isNonprofit: () => false, isConstruction: () => false },
  formatDate: (d) => d,
  API: {
    get: async (url) => {
      if (url === '/settings' && settingsReachable) return Object.assign({}, books);
      throw new Error('offline');
    },
    put: async (_url, data) => { books = Object.assign({}, books, data); return Object.assign({}, books); },
  },
};
ctx.window = {};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/app.js', 'utf8') + '\nthis.App = App;', ctx);
vm.runInContext(fs.readFileSync('app/static/js/settings.js', 'utf8') + '\nthis.SettingsPage = SettingsPage;', ctx);
const { App, SettingsPage: S } = ctx;
App.settings = { company_name: 'Harbor Light Bakery' }; // as the window opened

const named = () => ['Harbor Light Bakery & Cafe', 'Harbor Light Cafe', 'Harbor Light Bakery']
  .filter((n) => modal.includes(`everything in ${n} with`));
const log = (step) => console.log(JSON.stringify({
  step,
  dialog: named(),
  status: els['#status-company'].textContent,
  topbar: els['#topbar-company'].textContent,
  title: ctx.document.title,
  shell_copy: App.settings.company_name,
}));

(async () => {
  await S.confirmRestore('harbor-light-bakery_20260926_120000.db');
  log('restore-after-a-rename-elsewhere');

  await S.save({ preventDefault() {}, target: { querySelector: () => null } });
  log('renamed-and-saved-here');

  settingsReachable = false;
  modal = '';
  await S.confirmRestore('harbor-light-bakery_20260926_120000.db');
  log('restore-with-the-settings-out-of-reach');
})();
