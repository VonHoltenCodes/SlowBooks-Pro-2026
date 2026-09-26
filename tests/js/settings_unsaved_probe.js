// Exercise settings.js's unsaved-changes tracking and leave guard with a
// fake form, and print what happened at each step as JSON lines.
const fs = require('fs'), vm = require('vm');

const fields = { company_name: 'Harbor Light Bakery', company_type: 'business', default_tax_rate: '8.25' };
const note = { textContent: '' };
const form = { id: 'settings-form' };
let confirmAnswer = false;
const confirms = [];
const navigated = [];
const replaced = [];
const listeners = {};

class FakeFormData {
  constructor(f) { this.f = f; }
  entries() { return Object.entries(fields)[Symbol.iterator](); }
}

const ctx = {
  console,
  FormData: FakeFormData,
  JSON,
  document: {
    getElementById: (id) => (id === 'settings-form' ? form : id === 'settings-dirty-note' ? note : null),
  },
  window: { addEventListener: (name, fn) => { listeners[name] = fn; } },
  location: { hash: '#/settings' },
  history: { replaceState: (_s, _t, url) => { replaced.push(url); ctx.location.hash = url; } },
  confirm: (msg) => { confirms.push(msg); return confirmAnswer; },
  App: { navigate: async (hash) => { navigated.push(hash); } },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/settings.js', 'utf8') + '\nthis.SettingsPage = SettingsPage;', ctx);
const S = ctx.SettingsPage;

const log = (step, extra) => console.log(JSON.stringify(Object.assign({ step, dirty: S.isDirty(), note: note.textContent }, extra || {})));

(async () => {
  S._installLeaveGuard();
  S._markClean();
  log('loaded');

  fields.default_tax_rate = '9.25';
  S._updateDirty();
  log('edited');

  // A sidebar link: the hash has already changed when App.navigate runs.
  ctx.location.hash = '#/invoices';
  confirmAnswer = false;
  await ctx.App.navigate(ctx.location.hash);
  log('leave-cancelled', { navigated: navigated.slice(), replaced: replaced.slice(), hash: ctx.location.hash, confirms: confirms.length });

  await ctx.App.navigate('#/settings');
  log('same-page', { navigated: navigated.slice(), confirms: confirms.length });

  const unload = { prevented: false, preventDefault() { this.prevented = true; }, returnValue: undefined };
  listeners.beforeunload(unload);
  log('unload-while-dirty', { prevented: unload.prevented, returnValue: unload.returnValue });

  // Only the company type differs: "other changes" are none.
  fields.default_tax_rate = '8.25';
  fields.company_type = 'nonprofit';
  log('type-only', { others: S.isDirty('company_type') });
  fields.company_type = 'business';

  fields.company_name = 'Harbor Light Bakery & Cafe';
  confirmAnswer = true;
  await ctx.App.navigate('#/customers');
  log('leave-confirmed', { navigated: navigated.slice(), confirms: confirms.length });

  S._markClean();
  const clean = { prevented: false, preventDefault() { this.prevented = true; } };
  listeners.beforeunload(clean);
  log('unload-when-clean', { prevented: clean.prevented });

  fields.company_name = 'Changed again';
  S._leaving = true;
  const leaving = { prevented: false, preventDefault() { this.prevented = true; } };
  listeners.beforeunload(leaving);
  log('unload-when-leaving-on-purpose', { prevented: leaving.prevented });

  // The guard wraps App.navigate once, however often the page renders.
  S._installLeaveGuard();
  log('installed-twice', { wrapped_once: S._leaveGuardInstalled === true });
})();
