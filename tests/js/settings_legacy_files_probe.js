// Render the Settings page for each role while the folder earlier versions
// shared between companies holds files (with no company waiting, and with
// two still to be opened) and while it is empty, and print what the page
// offers, as one JSON line each. Then press Remove them as an administrator.
const fs = require('fs'), vm = require('vm');

const settings = { company_name: 'Harbor Light Bakery' };
const states = {
  ready: { files: 3, bytes: 5120, pending_companies: [], can_remove: true },
  waiting: { files: 1, bytes: 12, pending_companies: ['Beta & Sons <b>', 'Harbor Light'], can_remove: false },
  empty: { files: 0, bytes: 0, pending_companies: [], can_remove: false },
};
let state = states.ready;
let calls = [];
const confirms = [];
const toasts = [];

const escapeHtml = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const ctx = {
  console,
  JSON,
  setTimeout: () => 0,
  T: (s) => s,
  Terms: { text: (s) => s, isNonprofit: () => false, isConstruction: () => false },
  escapeHtml,
  formatFileSize: (n) => `${n} bytes`,
  toast: (msg, kind) => toasts.push([msg, kind || 'ok']),
  confirm: (msg) => { confirms.push(msg); return true; },
  API: {
    get: async (path) => {
      calls.push(`GET ${path}`);
      if (path === '/settings') return settings;
      if (path === '/uploads/legacy') return state;
      return {};
    },
    del: async (path) => { calls.push(`DELETE ${path}`); return { removed: 3, bytes: 5120 }; },
  },
  App: { role: 'admin' },
  $: () => null,
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/payment_terms.js', 'utf8') + '\nthis.PaymentTerms = PaymentTerms;', ctx);
vm.runInContext(fs.readFileSync('app/static/js/settings.js', 'utf8') + '\nthis.SettingsPage = SettingsPage;', ctx);
const S = ctx.SettingsPage;

// The section's words, tags removed until none are left.
function text(html) {
  let out = html, prev;
  do { prev = out; out = out.replace(/<[^>]*>/g, ''); } while (out !== prev);
  return out.replace(/\s+/g, ' ').trim();
}

async function show(role, name) {
  ctx.App.role = role;
  state = states[name];
  calls = [];
  const html = await S.render();
  const placeholder = html.includes('id="settings-legacy-files"');
  const el = { hidden: true, innerHTML: '' };
  ctx.$ = (sel) => (sel === '#settings-legacy-files' && placeholder ? el : null);
  await S.loadLegacyFiles();
  const button = /<button[^>]*id="legacy-files-remove"[^>]*>/.exec(el.innerHTML);
  return { el, row: {
    state: name,
    role,
    placeholder,
    asked: calls.includes('GET /uploads/legacy'),
    shown: !el.hidden && el.innerHTML.includes('<h3>Files from earlier versions</h3>'),
    button: !!button && el.innerHTML.includes('>Remove them</button>'),
    disabled: !!button && /\sdisabled[\s>]/.test(button[0]),
    dataWrite: !!button && /\sdata-write[\s>]/.test(button[0]),
    text: el.hidden ? '' : text(el.innerHTML),
    html: el.hidden ? '' : el.innerHTML,
  } };
}

(async () => {
  for (const name of ['ready', 'waiting', 'empty']) {
    for (const role of ['admin', 'bookkeeper', 'readonly']) {
      console.log(JSON.stringify((await show(role, name)).row));
    }
  }
  // Remove them, as an administrator: asks first, then deletes and looks again.
  const { el } = await show('admin', 'ready');
  calls = [];
  state = states.empty;
  await S.removeLegacyFiles();
  console.log(JSON.stringify({
    remove: true,
    confirm: confirms[confirms.length - 1] || '',
    calls,
    toasts,
    hiddenAfter: el.hidden,
  }));
})().catch((e) => { console.log(JSON.stringify({ error: String(e && e.stack || e) })); process.exit(1); });
