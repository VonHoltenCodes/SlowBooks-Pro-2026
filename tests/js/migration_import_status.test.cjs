// Migrate Data's Import while it runs (#706): the button reads
// "Importing…", a status line says what is being written (the passing dry
// run's counts) and how long it has been going, a second click does
// nothing, and the result takes the dry run's place with no toast on top.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function page() {
    const els = {};
    const attrs = {};
    const el = (sel) => ({
        innerHTML: '', textContent: '', disabled: false, value: 'xero', files: [{ name: 'chart.csv' }],
        setAttribute(k, v) { (attrs[sel] = attrs[sel] || {})[k] = String(v); },
    });
    const $ = (sel) => (els[sel] = els[sel] || el(sel));
    const toasts = [];
    let release;
    const calls = [];
    const context = {
        console, Promise, setTimeout, clearTimeout, setInterval, clearInterval, Date,
        FormData: class { append() {} },
        document: { getElementById: (id) => $('#' + id) },
        $,
        toast: (msg) => toasts.push(String(msg)),
        escapeHtml: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
        API: { get: async () => [], responseError: async () => 'failed' },
        fetch: (url) => {
            calls.push(url);
            return new Promise((resolve) => { release = resolve; });
        },
    };
    vm.createContext(context);
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/migration.js'), 'utf8');
    vm.runInContext(src + '\nthis.MigrationPage = MigrationPage;', context);
    return { M: context.MigrationPage, els, attrs, toasts, calls, release: (body) => release(body) };
}

const dryRun = {
    ok: true, accounts: 120, new_accounts: 120, journals: 924, duplicate_journals: 0,
    errors: [], warnings: [],
};
const imported = {
    ok: true, imported_accounts: 120, imported_journals: 924, errors: [], warnings: [],
};

test('Import shows a status line while it runs, ignores a second click, then the result', async () => {
    const p = page();
    p.M._dryRunOk = true;
    p.M._dryRunData = dryRun;
    p.els['#migration-result'] = undefined;
    const running = p.M.doImport();
    const btn = p.els['#migration-import-btn'];
    assert.equal(btn.textContent, 'Importing…');
    assert.equal(btn.disabled, true);
    assert.equal(p.attrs['#migration-import-btn']['aria-busy'], 'true');
    const out = p.els['#migration-result'].innerHTML;
    assert.match(out, /role="status"/);
    assert.match(out, /Importing 120 accounts and 924 journals… 0 s so far/);
    assert.match(p.M._progressText(12), /924 journals… 12 s so far/);

    // a second click while it runs: no second request
    await p.M.doImport();
    assert.equal(p.calls.length, 1);

    p.release({ ok: true, json: async () => imported });
    await running;
    const result = p.els['#migration-result'].innerHTML;
    assert.match(result, /Imported 120 accounts and 924 journals\./);
    assert.doesNotMatch(result, /role="status"/);
    assert.deepEqual(p.toasts, []);
    assert.equal(btn.textContent, 'Import');
    assert.equal(p.attrs['#migration-import-btn']['aria-busy'], 'false');
    // a finished import asks for a new dry run before another
    assert.equal(btn.disabled, true);
    assert.equal(p.M._importing, false);
});

test('A refused import says so where the status line was', async () => {
    const p = page();
    p.M._dryRunOk = true;
    p.M._dryRunData = dryRun;
    const running = p.M.doImport();
    p.release({ ok: false, status: 422, json: async () => ({}) });
    await running;
    assert.match(p.els['#migration-result'].innerHTML, /Import failed:<\/strong> failed/);
    assert.equal(p.els['#migration-import-btn'].textContent, 'Import');
    assert.equal(p.els['#migration-import-btn'].disabled, false);
    assert.equal(p.M._importing, false);
});
