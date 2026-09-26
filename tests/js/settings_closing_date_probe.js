// Clear the closing date the way the Settings page does, on a fake DOM, and
// print the field, the state line and the Clear button before and after.
const fs = require('fs'), vm = require('vm');

const input = { value: '2026-06-30' };
const state = { textContent: '' };
const clear = { disabled: false };
const els = { 'closing-date': input, 'closing-date-state': state, 'closing-date-clear': clear };

const ctx = {
  console,
  // utils.js's formatDate, as the page has it
  formatDate: (d) => new Date(d + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }),
  document: { getElementById: (id) => els[id] || null },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/settings.js', 'utf8') + '\nthis.SettingsPage = SettingsPage;', ctx);
const S = ctx.SettingsPage;

S.showClosingState();
const before = { value: input.value, state: state.textContent, clear_disabled: clear.disabled };
S.clearClosingDate();
const after = { value: input.value, state: state.textContent, clear_disabled: clear.disabled };
console.log(JSON.stringify({ before, after }));
