// Evaluate invoices.js with stubs and run the shared sales-line arithmetic
// (SalesLines) the way the forms do: a row is an object whose querySelector
// answers the line's inputs. Prints one JSON object for the pytest side.
const fs = require('fs'), vm = require('vm');

const ctx = {
  console,
  window: {},
  App: { settings: { home_currency: 'USD' } },
  formatCurrency: (n) => '$' + (Number(n) || 0).toFixed(2),
};
vm.createContext(ctx);
vm.runInContext(
  fs.readFileSync('app/static/js/invoices.js', 'utf8') + '\nthis.SalesLines = SalesLines;',
  ctx,
);
const S = ctx.SalesLines;

function row(qty, rate, taxable, itemId) {
  const els = {
    '.line-item': { value: itemId == null ? '' : String(itemId) },
    '.line-desc': { value: '' },
    '.line-qty': { value: String(qty) },
    '.line-rate': { value: String(rate) },
    '.line-amount': { textContent: '' },
  };
  if (taxable !== undefined) els['.line-taxable'] = { checked: taxable, disabled: false, dataset: {} };
  return { querySelector: (s) => els[s] || null, els };
}
const tbody = (rows) => ({ querySelectorAll: () => rows });

const out = {};
out.cents = [1.005, 3 * 0.335, 2.675, 1.345, -1.005, 0.1 + 0.2, 10824891.745].map((x) => S.cents(x));

// A taxed line that rounds half up, an untaxed one, and one with no Tax box.
const rows = [row(3, 0.335, true), row(2, 100, false), row(1, 19.99)];
const t = S.totals(tbody(rows), '8.25');
out.totals = t;
out.amounts = rows.map((r) => r.els['.line-amount'].textContent);

// Picking an item fills description, price and the item's tax flag.
const picked = row(1, 0, true, 7);
const item = S.fillFromItem(picked, [{ id: 7, name: 'Sourdough Loaf', description: '', rate: '8.50', is_taxable: false }]);
out.picked = {
  found: !!item,
  desc: picked.els['.line-desc'].value,
  rate: picked.els['.line-rate'].value,
  taxable: picked.els['.line-taxable'].checked,
};
out.none = S.fillFromItem(row(1, 0, true, null), []) === null;

console.log(JSON.stringify(out));
