// Adding employees on an import is a person's tick, never the software's
// yes or no (NEW-222, W-138, 2.31.12). Interoperability's QuickBooks report
// import shows "Add people who are not on the Employees list", ticked for an
// administrator and unticked, disabled, with the reason for anyone else, and
// sends add_employees as it reads; App.adminPass unticks it when the role
// arrives after the page. Migrate Data shows "Bring in the people on the
// Employee Contact List" once the dry run found the list, and sends
// include_employees only when the tick may be changed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const ROOT = path.join(__dirname, '../..');
const read = (f) => fs.readFileSync(path.join(ROOT, f), 'utf8');
const escapeHtml = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function iif(role) {
    const els = {};
    const sent = [];
    const $ = (sel) => els[sel] || null;
    const ctx = {
        console, Promise, setTimeout,
        FormData: class { append(k, v) { sent.push([k, v]); } },
        Blob: class {},
        document: { getElementById: () => null },
        $, $$: () => [],
        escapeHtml,
        toast() {},
        App: { role, isAdmin: () => role === 'admin', setStatus() {}, navigate() {} },
        API: { responseError: async () => 'failed' },
        fetch: async () => ({ ok: true, json: async () => ({ detected: 'time', errors: [], warnings: [] }) }),
    };
    vm.createContext(ctx);
    vm.runInContext(read('app/static/js/iif.js') + '\nthis.IIFPage = IIFPage;', ctx);
    return { P: ctx.IIFPage, els, sent };
}

test('an administrator gets the tick ticked; the reason stays hidden', async () => {
    const { P } = iif('admin');
    const html = await P.render();
    const tick = html.match(/<input type="checkbox" id="qbcsv-add-employees"[^>]*>/)[0];
    assert.match(tick, / checked>/);
    assert.doesNotMatch(tick, /disabled/);
    assert.match(tick, /data-admin data-admin-untick/);
    assert.match(html, /Add people who are not on the Employees list/);
    assert.match(html, /id="qbcsv-add-employees-why" data-admin-note class="hidden"/);
});

test('a bookkeeper gets it unticked and disabled, with the reason beside it', async () => {
    const { P } = iif('bookkeeper');
    const html = await P.render();
    const tick = html.match(/<input type="checkbox" id="qbcsv-add-employees"[^>]*>/)[0];
    assert.match(tick, / disabled>/);
    assert.doesNotMatch(tick, /checked/);
    assert.match(tick, /aria-describedby="qbcsv-add-employees-why"/);
    assert.match(html, /id="qbcsv-add-employees-why" data-admin-note class=""[^>]*>Adding people to the Employees list needs an administrator/);
});

test('the import sends add_employees as the tick reads, false when disabled', async () => {
    for (const [checked, disabled, want] of [[true, false, 'true'], [false, false, 'false'], [true, true, 'false']]) {
        const p = iif('admin');
        p.els['#qbcsv-file-input'] = { files: [{ name: 'Time.xlsx' }] };
        p.els['#qbcsv-add-employees'] = { checked, disabled };
        p.els['#qbcsv-import-result'] = { innerHTML: '' };
        await p.P.importQbReportCsv();
        assert.deepEqual(p.sent.find(([k]) => k === 'add_employees'), ['add_employees', want]);
    }
});

test('App.adminPass unticks a data-admin-untick field for any role but admin', () => {
    const src = read('app/static/js/app.js');
    const body = src.match(/\n    adminPass\(root\) \{([\s\S]*?)\n    \},/)[1];
    const App = { isAdmin: () => false, isReadOnly: () => false };
    const adminPass = new Function('App', `return function (root) {${body}\n};`)(App);
    const tick = { tagName: 'INPUT', type: 'checkbox', checked: true, disabled: false,
        hasAttribute: (a) => a === 'data-admin-untick', classList: { add() {} } };
    const other = { tagName: 'INPUT', type: 'checkbox', checked: true, disabled: false,
        hasAttribute: () => false, classList: { add() {} } };
    const note = { classList: { removed: [], remove(c) { this.removed.push(c); } } };
    const root = { querySelectorAll: (sel) => (sel === '[data-admin]' ? [tick, other] : sel === '[data-admin-note]' ? [note] : []) };
    adminPass(root);
    assert.equal(tick.disabled, true);
    assert.equal(tick.checked, false);
    assert.equal(other.checked, true);  // a locked field otherwise shows its value
    assert.deepEqual(note.classList.removed, ['hidden']);
});

function migrate() {
    const els = {};
    const sent = [];
    const $ = (sel) => els[sel] || null;
    const ctx = {
        console, Promise, setTimeout, clearTimeout, setInterval, clearInterval, Date,
        FormData: class { append(k, v) { sent.push([k, v]); } },
        document: { getElementById: () => null },
        $, escapeHtml, toast() {},
        API: { get: async () => [], responseError: async () => 'failed' },
    };
    vm.createContext(ctx);
    vm.runInContext(read('app/static/js/migration.js') + '\nthis.MigrationPage = MigrationPage;', ctx);
    return { M: ctx.MigrationPage, els, sent };
}

test('Migrate Data shows the Employee Contact List tick once the dry run found it', () => {
    const p = migrate();
    p.els['#migration-choices'] = { innerHTML: '' };
    p.M._employeeChoice({ employees: 3, included: true, may_add: true, note: '' });
    let html = p.els['#migration-choices'].innerHTML;
    assert.match(html, /Bring in the people on the Employee Contact List \(3 employees\)/);
    assert.match(html, /id="migration-include-employees"[^>]*checked/);
    assert.doesNotMatch(html, /disabled/);
    assert.match(html, /onchange="MigrationPage.dryRun\(\)"/);

    p.M._employeeChoice({ employees: 1, included: false, may_add: false, note: '' });
    html = p.els['#migration-choices'].innerHTML;
    assert.match(html, /\(1 employee\)/);
    assert.doesNotMatch(html, /checked/);
    assert.match(html, /disabled aria-describedby="migration-include-employees-why"/);
    assert.match(html, /needs an administrator, or a token that may also run payroll/);

    p.M._employeeChoice(undefined);
    assert.equal(p.els['#migration-choices'].innerHTML, '');
});

test('Migrate Data sends include_employees only when the tick may be changed', () => {
    for (const [tick, want] of [
        [null, undefined],
        [{ checked: true, disabled: false }, 'true'],
        [{ checked: false, disabled: false }, 'false'],
        [{ checked: false, disabled: true }, undefined],
    ]) {
        const p = migrate();
        p.els['#migration-files'] = { files: [{ name: 'export.zip' }] };
        if (tick) p.els['#migration-include-employees'] = tick;
        p.M._formData();
        const got = p.sent.find(([k]) => k === 'include_employees');
        assert.equal(got && got[1], want);
    }
});
