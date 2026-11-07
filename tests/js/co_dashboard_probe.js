// The contractor's dashboard tiles, the vendor list's COI cell and the job
// Overview's construction labels, drawn from data argv[2] (JSON) in the mode
// argv[3] ("business" or "construction"). Prints JSON of what each says.
const stripTags = (s) => { let prev; s = String(s); do { prev = s; s = s.replace(/<[^>]*>/g, ''); } while (s !== prev); return s; };
const fs = require('fs'), vm = require('vm');
const input = JSON.parse(process.argv[2]);
const mode = process.argv[3] || 'construction';
const ctx = {
  console, Date, Math, Set, URLSearchParams,
  window: {},
  document: { addEventListener: () => {}, documentElement: { getAttribute: () => 'light' } },
  escapeHtml: (s) => String(s ?? ''), T: (s) => s,
  Terms: { text: (s) => s, isNonprofit: () => false, isConstruction: () => mode === 'construction' },
  formatCurrency: (n) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n || 0),
  formatDate: (d) => d,
  todayISO: () => '2026-11-07',
  ReportsPage: { viewUrl: (n, p) => `#/reports/${n}?as_of_date=${p.as_of_date}`, _leaveFrom: () => {} },
  API: { get: async (url) => { ctx.calls.push(url); return input.retainage; } },
  calls: [],
};
vm.createContext(ctx);
const load = (f, name) => vm.runInContext(fs.readFileSync(`app/static/js/${f}`, 'utf8') + `\nthis.${name} = ${name};`, ctx);
load('dashboard.js', 'DashboardPage');
load('vendors.js', 'VendorsPage');
load('jobs.js', 'JobsPage');

(async () => {
  const out = { tiles: {}, coi: {} };
  const R = ctx.DashboardPage.renderers;
  for (const [id, d] of Object.entries(input.tiles || {})) {
    const html = R[id](d);
    out.tiles[id] = { text: stripTags(html).replace(/\s+/g, ' ').trim(), href: (html.match(/href="([^"]*)"/) || [])[1], title: (html.match(/title="([^"]*)"/) || [])[1] };
  }
  for (const [k, v] of Object.entries(input.vendors || {})) {
    out.coi[k] = { text: stripTags(ctx.VendorsPage.coiCell(v)).trim(), color: (ctx.VendorsPage.coiCell(v).match(/color:([^";]*)/) || [])[1] };
  }
  if (input.tree) {
    ctx.JobsPage._job = input.job;
    ctx.JobsPage._tree = input.tree;
    const html = await ctx.JobsPage.overviewHtml();
    out.overview = stripTags(html).replace(/\s+/g, ' ').trim();
    out.overviewHtml = html;
    out.calls = ctx.calls;
  }
  console.log(JSON.stringify(out));
})();
