// app.js's router on an address with a query string (R7): what App.parseHash
// makes of each hash given, and what navigating to it does — the page
// rendered, every call on a page object (with the query object the router
// hands the route), and the address afterwards. The pages are stubs that
// record calls; render() returns "<Name>".
//
//   node tests/js/report_views_probe.js '["#/reports/profit-loss?start_date=2026-07-01", ...]'
//
// Prints {"<hash>": {"parsed": {path, query}, "page": "<html>", "calls": [...],
//                    "hash": "<location.hash after>", "pushes": n}}.
const fs = require('fs'), vm = require('vm');

const hashes = JSON.parse(process.argv[2]);
const src = fs.readFileSync('app/static/js/app.js', 'utf8');

let calls = [];
const stub = (name) => new Proxy({}, {
  get: (_t, prop) => {
    if (prop === 'then') return undefined; // not a promise
    return (...args) => {
      calls.push([`${name}.${String(prop)}`, ...args]);
      return prop === 'render' ? `<${name}>` : '';
    };
  },
});

const pageContent = { innerHTML: '' };
let pushes = 0;
const ctx = {
  console, setTimeout, clearTimeout, Promise, URLSearchParams, JSON,
  window: {},
  document: { addEventListener() {}, querySelectorAll: () => [] },
  location: { hash: '#/' },
  history: {
    pushState: (_s, _t, url) => { ctx.location.hash = url; pushes += 1; },
    replaceState: (_s, _t, url) => { ctx.location.hash = url; },
  },
  $: (sel) => (sel === '#page-content' ? pageContent : null),
  $$: () => [],
  escapeHtml: (s) => String(s ?? ''),
  toast: (msg) => calls.push(['toast', String(msg)]),
  T: (s) => s,
  Terms: { text: (s) => s, isNonprofit: () => false, isConstruction: () => false },
};
for (const name of new Set(src.match(/\b[A-Z][A-Za-z]*Page\b/g) || [])) ctx[name] = stub(name);
vm.createContext(ctx);
vm.runInContext(src + '\nthis.App = App;', ctx);

(async () => {
  const out = {};
  for (const hash of hashes) {
    calls = [];
    pushes = 0;
    pageContent.innerHTML = '';
    ctx.location.hash = '#/';
    const parsed = ctx.App.parseHash(hash);
    await ctx.App.navigate(hash);
    await new Promise((r) => setTimeout(r, 5)); // the view opens after the page
    out[hash] = { parsed, page: pageContent.innerHTML, calls, hash: ctx.location.hash, pushes };
  }
  console.log(JSON.stringify(out));
})();
