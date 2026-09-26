// Navigate app.js's router to each hash given and report what came up: the
// page's HTML and every call made on a page object. The pages are stubs
// that record calls; render() returns "<Name>".
//
//   node tests/js/app_routes_probe.js '["#/deposits/7", ...]'
//
// Prints {"<hash>": {"page": "<html>", "calls": [["DepositsPage.view", "7"], ...]}}.
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
const ctx = {
  console, setTimeout, clearTimeout, Promise,
  window: {},
  document: { addEventListener() {}, querySelectorAll: () => [] },
  location: { hash: '#/' },
  $: (sel) => (sel === '#page-content' ? pageContent : null),
  $$: () => [],
  escapeHtml: (s) => String(s ?? ''),
  toast: (msg) => calls.push(['toast', String(msg)]),
  T: (s) => s,
  Terms: { text: (s) => s, isNonprofit: () => false },
};
// every page object app.js names gets a recording stub
for (const name of new Set(src.match(/\b[A-Z][A-Za-z]*Page\b/g) || [])) ctx[name] = stub(name);
vm.createContext(ctx);
vm.runInContext(src + '\nthis.App = App;', ctx);

(async () => {
  const out = {};
  for (const hash of hashes) {
    calls = [];
    pageContent.innerHTML = '';
    await ctx.App.navigate(hash);
    await new Promise((r) => setTimeout(r, 5)); // the document opens after the page
    out[hash] = { page: pageContent.innerHTML, calls };
  }
  console.log(JSON.stringify(out));
})();
