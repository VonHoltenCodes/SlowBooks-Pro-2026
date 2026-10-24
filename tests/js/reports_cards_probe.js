// ReportsPage._cards() in business vs construction mode: the ordered list
// of card views each produces. Construction must lead with the three
// contractor reports and otherwise hold the same set as business.
//
//   node tests/js/reports_cards_probe.js
//
// Prints {"business": [view, ...], "construction": [view, ...]}.
const fs = require('fs'), vm = require('vm');

const src = fs.readFileSync('app/static/js/reports.js', 'utf8');

let mode = 'business';
const ctx = {
  console, JSON, URLSearchParams,
  window: {},
  document: { querySelector: () => null, querySelectorAll: () => [] },
  escapeHtml: (s) => String(s ?? ''),
  T: (s) => s,
  Terms: {
    text: (s) => s,
    isNonprofit: () => mode === 'nonprofit',
    isConstruction: () => mode === 'construction',
  },
};
vm.createContext(ctx);
vm.runInContext(src + '\nthis.ReportsPage = ReportsPage;', ctx);
const R = ctx.ReportsPage;
R._pins = [];

const views = () => R._cards().map((c) => c.view);

mode = 'business';
const business = views();
mode = 'construction';
const construction = views();
console.log(JSON.stringify({ business, construction }));
