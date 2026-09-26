// Evaluate time_entries.js with stubs: the list shows the hours the API
// returns and offers Approve on a new (draft) entry; clock times work out
// Regular Hours.
const fs = require('fs'), vm = require('vm');
const entries = [
  { id: 1, employee_id: 7, date: '2026-09-14', hours_regular: 8, hours_overtime: 2,
    hours_doubletime: 0, notes: 'Hung the Main St sign', status: 'draft', pay_run_id: null },
  { id: 2, employee_id: 7, date: '2026-09-15', hours_regular: 6, hours_overtime: 0,
    hours_doubletime: 0, notes: null, status: 'approved', pay_run_id: null },
];
const ctx = {
  console,
  API: { get: async (p) => (p.startsWith('/employees') ? [{ id: 7, first_name: 'Hana', last_name: 'Lee' }] : entries) },
  escapeHtml: (s) => String(s), formatDate: (d) => d, statusBadge: (s) => `[${s}]`, T: (s) => s,
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/time_entries.js', 'utf8') + '\nthis.P = TimeEntriesPage;', ctx);
(async () => {
  const html = await ctx.P.render();
  const rows = html.split('<tr>').slice(2);
  const cells = (r) => [...r.matchAll(/<td class="amount">([^<]*)<\/td>/g)].map((m) => m[1]).join(' ');
  console.log('draft row hours:', cells(rows[0]));
  console.log('draft row notes:', rows[0].includes('Hung the Main St sign'));
  console.log('draft row approve:', rows[0].includes('TimeEntriesPage.approve(1)'));
  console.log('approved row approve:', rows[1].includes('TimeEntriesPage.approve(2)'));
  const form = (i, o, b) => ({ clock_in: { value: i }, clock_out: { value: o }, break_minutes: { value: b }, regular_hours: { value: '0' } });
  const day = form('08:00', '16:30', '30'); ctx.P._hoursFromClock(day);
  const night = form('22:00', '06:00', '0'); ctx.P._hoursFromClock(night);
  const half = form('08:00', '', '0'); ctx.P._hoursFromClock(half);
  console.log('clock 08:00-16:30 less 30:', day.regular_hours.value);
  console.log('clock 22:00-06:00:', night.regular_hours.value);
  console.log('clock without an out time:', half.regular_hours.value);
})();
