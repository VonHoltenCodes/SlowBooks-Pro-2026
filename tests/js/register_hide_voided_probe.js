// The register's Hide voided box (#694), rendered against a canned register
// with a voided check and the reversal that voided it, in three windows:
// no Store at all (this file loaded alone, as the other probes load it), a
// window whose localStorage is null (WebKitGTK's private mode, utils.js's
// own Store), and one where the sign-in chose to hide them. Prints JSON
// {<case>: {rows: [[txn, balance]...], checked, note}}.
const fs = require('fs'), vm = require('vm');

const row = (transaction_id, balance, extra) => Object.assign(
  { date: '2026-09-14', payee: 'Main Street Properties', description: '', reference: '',
    source_type: 'bank_entry', source_link: null, transaction_id, payment: 0, deposit: 0, balance,
    voided: false, voidable: true, printable: false, editable: true, reconciliation_id: null, cleared: false }, extra);
const register = {
  account_name: 'Checking', account_number: '1000', bank_kind: 'bank', balance: 860, opening_balance: 0,
  entries: [
    row(71, 1000, { deposit: 1000, amount: 1000 }),
    row(72, 750, { payment: 250, amount: -250, voided: true, voidable: false, editable: false, reference: '1072' }),
    row(73, 650, { payment: 100, amount: -100, printable: true }),
    row(74, 900, { source_type: 'bank_entry_void', deposit: 250, amount: 250, voided: true, voidable: false, editable: false }),
    row(75, 860, { payment: 40, amount: -40 }),
  ],
};
const answers = {
  '/banking/check-register?account_id=5': register,
  '/banking/overview': [{ account_id: 5, feed: null, last_reconciled: null }],
};

// utils.js's Store, as it is, run in the probe's window
const utils = fs.readFileSync('app/static/js/utils.js', 'utf8');
const storeSrc = utils.match(/const Store = \{[\s\S]*?\n\};/)[0];

function context(withStore, localStorage) {
  const ctx = {
    console, setTimeout, Promise,
    window: { localStorage },
    location: { hash: '#/' },
    document: { addEventListener() {}, querySelector: () => null, querySelectorAll: () => [], getElementById: () => null },
    escapeHtml: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    customerLabel: (c) => (c && (c.full_name || c.name)) || '',
    accountWords: (a) => a.name || '',
    accountLabel: (a) => a.account_number ? `${a.account_number} - ${a.name || ''}` : (a.name || ''),
    formatDate: (s) => s || '',
    sourceWord: (s) => String(s || ''),
    datesFromQuery: (q) => ({ start_date: (q && q.start_date) || '', end_date: (q && q.end_date) || '' }),
    formatCurrency: (n) => '$' + Number(n || 0).toFixed(2),
    openModal() {}, closeModal() {}, toast() {},
    T: (s) => s,
    Terms: { text: (s) => s, isNonprofit: () => false, isConstruction: () => false },
    App: { navigate() {}, _tabsOwner: 'kim|Main Street' },
    API: {
      get: async (path) => {
        if (!(path in answers)) throw new Error('unexpected ' + path);
        return JSON.parse(JSON.stringify(answers[path]));
      },
    },
  };
  vm.createContext(ctx);
  if (withStore) vm.runInContext(storeSrc + '\nthis.Store = Store;', ctx);
  for (const f of ['account_quick_add.js', 'banking.js']) {
    vm.runInContext(fs.readFileSync(`app/static/js/${f}`, 'utf8'), ctx, { filename: f });
  }
  vm.runInContext('this.BankingPage = BankingPage;', ctx);
  return ctx;
}

function memoryStorage(seed) {
  const m = new Map(Object.entries(seed || {}));
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) };
}

// The words of a rendered fragment: tags stripped until none is left, so a
// tag split by another (the way CodeQL reads one pass) cannot survive.
function textOf(fragment) {
  let s = fragment;
  let before;
  do {
    before = s;
    s = s.replace(/<[^>]*>/g, '');
  } while (s !== before);
  return s.trim();
}

async function read(ctx) {
  const html = await ctx.BankingPage.renderRegister('5');
  const rows = [];
  for (const tr of html.match(/<tr data-txn="\d+"[\s\S]*?<\/tr>/g) || []) {
    const txn = Number(tr.match(/data-txn="(\d+)"/)[1]);
    const cells = tr.match(/<td[^>]*>[\s\S]*?<\/td>/g) || [];
    rows.push([txn, textOf(cells[8])]);  // Balance, after Category (#729)
  }
  const box = (html.match(/<input type="checkbox" id="reg-hide-voided"[^>]*>/) || [''])[0];
  const label = textOf((html.match(/<label for="reg-hide-voided"[\s\S]*?<\/label>/) || [''])[0]);
  const note = (html.match(/<p id="reg-voided-note"[^>]*>([^<]*)<\/p>/) || [null, null])[1];
  return { rows, checked: / checked/.test(box), label, note };
}

(async () => {
  const out = {};
  out.no_store = await read(context(false, undefined));
  out.null_storage = await read(context(true, null));
  out.hidden = await read(context(true, memoryStorage({ 'slowbooks-register-hide-voided:kim': '1' })));
  // another sign-in on the same computer has not chosen it
  const other = context(true, memoryStorage({ 'slowbooks-register-hide-voided:kim': '1' }));
  other.App._tabsOwner = 'jo|Main Street';
  out.other_user = await read(other);
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e.stack || e); process.exit(1); });
