// Clear the closing date the way the Settings page does, then save, on a
// fake DOM whose date input behaves like WebKit's (macOS): `value = ''`
// empties the value but leaves the month/day/year segments holding the old
// date, so the field is badInput and the form will not submit. Changing the
// input's type rebuilds the segments. Prints one JSON line per step.
const fs = require('fs'), vm = require('vm');

class WebKitDateInput {
  constructor(value) { this._type = 'date'; this._value = value; this._segments = !!value; }
  get type() { return this._type; }
  set type(t) {
    this._type = t;
    this._segments = t === 'date' && !!this._value;
    if (t === 'date' && !/^\d{4}-\d{2}-\d{2}$/.test(this._value)) this._value = '';
  }
  get value() { return this._value; }
  set value(v) {
    this._value = String(v);
    if (this._type === 'date' && this._value) this._segments = true;
    // '' leaves the segments as they were: WebKit's half-cleared field
  }
  get validity() {
    const badInput = this._type === 'date' && !this._value && this._segments;
    return { badInput, valid: !badInput };
  }
}

const input = new WebKitDateInput('2026-09-28');
const state = { textContent: '' };
const clear = { disabled: false };
const note = { textContent: '' };
const saveBtn = { disabled: false };
// a form: the logo option's box is looked up on it (#192)
const form = { id: 'settings-form', querySelector: () => null };
const els = {
  'closing-date': input, 'closing-date-state': state, 'closing-date-clear': clear,
  'settings-dirty-note': note, 'settings-save-btn': saveBtn, 'settings-form': form,
};
const toasts = [];
const puts = [];
let putAnswer = null;

class FakeFormData {
  constructor(f) { this.f = f; }
  entries() {
    return [['company_name', 'Harbor Light Bakery Two'], ['closing_date', input.value]][Symbol.iterator]();
  }
}

const ctx = {
  console,
  JSON,
  FormData: FakeFormData,
  // utils.js's formatDate, as the page has it
  formatDate: (d) => new Date(d + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }),
  escapeHtml: (s) => String(s ?? ''),
  T: (s) => s,
  Terms: { text: (s) => s, isNonprofit: () => false, isConstruction: () => false },
  toast: (msg, kind) => toasts.push([msg, kind || 'success']),
  setTimeout: () => 0, // the page's loaders are not part of this
  API: {
    get: async () => ({ company_name: 'Harbor Light Bakery Two', closing_date: '2026-09-28' }),
    put: async (url, data) => {
      puts.push(data);
      if (putAnswer instanceof Error) throw putAnswer;
      return putAnswer;
    },
  },
  document: { getElementById: (id) => els[id] || null },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/settings.js', 'utf8') + '\nthis.SettingsPage = SettingsPage;', ctx);
const S = ctx.SettingsPage;

const log = (step, extra) => console.log(JSON.stringify(Object.assign({
  step,
  value: input.value,
  type: input.type,
  valid: input.validity.valid,
  state: state.textContent,
  clear_disabled: clear.disabled,
  dirty: note.textContent,
}, extra || {})));

(async () => {
  const html = await S.render();
  S._markClean();
  S.showClosingState();
  log('loaded', { rendered_state: html.includes('Closed through Sep 28, 2026: changes dated on or before it are refused.') });

  S.clearClosingDate();
  log('cleared');

  // the server refuses the save: the books are still closed, and the page says so
  putAnswer = new Error('Another company file is already named that.');
  await S.save({ preventDefault() {}, target: form });
  log('save-failed', { sent: puts[puts.length - 1], toast: toasts[toasts.length - 1] });

  putAnswer = { company_name: 'Harbor Light Bakery Two', closing_date: '' };
  await S.save({ preventDefault() {}, target: form });
  log('saved', { sent: puts[puts.length - 1], toast: toasts[toasts.length - 1] });

  input.value = '2026-10-31';
  S.showClosingState();
  log('typed-a-date');

  // a date half erased by hand: '' to the page, but not empty
  input._value = '';
  input._segments = true;
  S.showClosingState();
  log('half-typed');
  if (typeof S.closingDateInvalid === 'function') S.closingDateInvalid();
  log('save-refused-by-the-browser', { toast: toasts[toasts.length - 1] });
})();
