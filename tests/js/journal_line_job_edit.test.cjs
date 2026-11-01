// A journal line names its own job (#672): the form has a Job column
// beside the line's Class once the company has a job, blank meaning the
// header's, and each line sends it as job_id; the entry's view shows it.
// The Journal Entries page and the entry's view offer Edit (#673) on an
// entry the server marks editable, and Save sends the PUT.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JOBS = [{ id: 5, customer_id: 1, full_name: 'Acme: Kitchen' }, { id: 6, customer_id: 1, full_name: 'Acme: Bath' }];

function fakeSelect(value, options = []) {
    const select = {
        value,
        options: options.map(v => ({ value: v })),
        insertAdjacentHTML(_, html) { select.options.push({ value: /value="(\d+)"/.exec(html)[1], html }); },
    };
    return select;
}

// A grid row as collectLines and fillLine read it: a field per class.
function fakeRow(fields) {
    return { querySelector: sel => fields[sel.slice(1)] || null };
}

function page({ jobs = JOBS, entries = [], entry = null, accountant = false, rows = [], put = null } = {}) {
    const calls = { modal: [], put: [], post: [], toast: [] };
    const context = {
        $: () => null,
        $$: () => rows,
        API: {
            get: async url => {
                if (url === '/jobs') return jobs;
                if (url === '/accounts') return [{ id: 1, name: 'Cash' }];
                if (url.startsWith('/journal?') || url === '/journal') return entries;
                return entry;
            },
            put: put || (async (url, body) => { calls.put.push({ url, body }); return {}; }),
            post: async (url, body) => { calls.post.push({ url, body }); return {}; },
        },
        App: { isAccountant: () => accountant, navigate: () => {} },
        location: { hash: '#/journal' },
        T: word => word,
        Terms: { text: word => word },
        escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, character => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[character]),
        formatDate: value => value, formatCurrency: value => `$${value}`,
        sourceWord: value => String(value || ''),
        accountLabel: (a, name) => name || a.name,
        todayISO: () => '2026-10-31',
        classFormGroupHtml: async () => '',
        jobFormGroupHtml: async () => '',
        classIdFromForm: () => null,
        jobIdFromForm: () => null,
        TxnWarn: { ok: async () => true },
        ClassWarn: { ok: async () => true },
        toast: (...args) => calls.toast.push(args),
        toastPosted: () => {},
        closeModal: () => {},
        CostCodes: { headHtml: () => '', cellHtml: () => '', viewCellHtml: () => '', labelOf: () => '', any: () => false, load: async () => {}, fromRow: () => null },
        CostTypes: { headHtml: () => '', cellHtml: () => '', load: async () => {}, fromRow: () => null },
        Nonprofit: {
            loadFunds: async () => {}, headHtml: () => '<th scope="col">Class</th>',
            cellHtml: () => '<td class="np-class"></td>', linePayload: () => ({}), fundFromRow: () => null,
        },
        openModal: (title, html) => calls.modal.push({ title, html }),
        document: { querySelector: () => null },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/journal.js'), 'utf8') + '\nthis.JournalPage = JournalPage;', context);
    return { JournalPage: context.JournalPage, calls };
}

test('the form has a Job column beside Class on every line once a job exists', async () => {
    const f = page();
    await f.JournalPage.showForm();
    const html = f.calls.modal[0].html;
    assert.match(html, /<th scope="col">Job<\/th><th scope="col">Class<\/th>/);
    assert.equal((html.match(/<select class="je-job"/g) || []).length, 2);
    assert.match(html, /<select class="je-job"[^>]*><option value="">Same as header<\/option><option value="5">Acme: Kitchen<\/option>/);
    // + Add Line draws the same cell
    assert.match(f.JournalPage.jobCellHtml(), /class="je-job"/);
});

test('with no job yet the form has no Job column', async () => {
    const f = page({ jobs: [] });
    await f.JournalPage.showForm();
    assert.doesNotMatch(f.calls.modal[0].html, /je-job|>Job</);
    assert.equal(f.JournalPage.jobCellHtml(), '');
});

test("each line sends its job as job_id; blank is null (the header's); no column sends none", () => {
    const line = (job) => fakeRow({
        'je-account': { value: '1' }, 'je-debit': { value: '10' }, 'je-credit': { value: '0' }, 'je-desc': { value: 'x' },
        ...(job === undefined ? {} : { 'je-job': { value: job } }),
    });
    const f = page({ rows: [line('6'), line(''), line(undefined)] });
    const lines = f.JournalPage.collectLines();
    assert.equal(lines[0].job_id, 6);
    assert.equal(lines[1].job_id, null);
    assert.equal('job_id' in lines[2], false);
});

test("an entry's line opens on its job, an inactive one kept", () => {
    const f = page();
    const make = () => ({
        'je-account': {}, 'je-desc': {}, 'je-debit': {}, 'je-credit': {},
        'je-job': fakeSelect('', ['', '5', '6']),
    });
    const a = make();
    f.JournalPage.fillLine(fakeRow(a), { account_id: 1, debit: 5, credit: 0, job_id: 6 });
    assert.equal(a['je-job'].value, '6');
    const b = make();
    f.JournalPage.fillLine(fakeRow(b), { account_id: 1, debit: 5, credit: 0, job_id: 9, job_name: 'Old: Job' });
    assert.equal(b['je-job'].value, '9');
    assert.match(b['je-job'].options.at(-1).html, /Old: Job/);
});

const entry = over => ({
    id: 7, date: '2026-10-01', description: 'Accrual', reference: '', source_type: 'manual',
    editable: true, voided: false, total_debit: 10, total_credit: 10,
    lines: [
        { account_id: 1, account_name: 'Cash', debit: 10, credit: 0, job_id: 5, job_name: 'Acme: Kitchen' },
        { account_id: 2, account_name: 'Sales', debit: 0, credit: 10, job_id: null, job_name: '' },
    ],
    ...over,
});

test("the entry's view shows each line's job and offers Edit", async () => {
    const f = page({ entry: entry() });
    await f.JournalPage.view(7);
    const html = f.calls.modal[0].html;
    assert.match(html, /<th scope="col">Job<\/th>/);
    assert.match(html, /<td class="je-line-job">Acme: Kitchen<\/td>/);
    assert.match(html, /onclick="JournalPage\.editEntry\(7\)">Edit</);
});

test('a view with no line job has no Job column', async () => {
    const f = page({ entry: entry({ lines: [{ account_id: 1, account_name: 'Cash', debit: 10, credit: 0 }] }) });
    await f.JournalPage.view(7);
    assert.doesNotMatch(f.calls.modal[0].html, /je-line-job|>Job</);
});

for (const [name, over, accountant] of [
    ['not editable (an import, a reversal, a document posting)', { editable: false }, false],
    ['voided', { voided: true }, false],
    ['the accountant sign-in', {}, true],
]) {
    test(`no Edit when ${name}`, async () => {
        const f = page({ entry: entry(over), entries: [entry(over)], accountant });
        await f.JournalPage.view(7);
        assert.doesNotMatch(f.calls.modal[0].html, /JournalPage\.editEntry/);
        assert.doesNotMatch(await f.JournalPage.render(), /JournalPage\.editEntry/);
    });
}

test('the list offers Edit on an editable entry only', async () => {
    const f = page({ entries: [entry(), entry({ id: 8, editable: false, source_type: 'iif_import' })] });
    const html = await f.JournalPage.render();
    const [first, second] = html.split('<tr>').slice(2);
    assert.match(first, /onclick="JournalPage\.editEntry\(7\)">Edit</);
    assert.doesNotMatch(second, /JournalPage\.editEntry/);
});

test('Edit opens the entry in the journal form and Save sends a PUT', async () => {
    const f = page({ entry: entry() });
    await f.JournalPage.editEntry(7);
    assert.equal(f.calls.modal[0].title, 'Edit Journal Entry #7');
    const row = (debit, credit, job) => fakeRow({
        'je-account': { value: '1' }, 'je-debit': { value: debit }, 'je-credit': { value: credit },
        'je-desc': { value: '' }, 'je-job': { value: job },
    });
    const g = page({ rows: [row('10', '0', '5'), row('0', '10', '')] });
    const form = {
        dataset: { entryId: '7' }, date: { value: '2026-10-01' }, description: { value: 'Accrual' },
        reference: { value: '' }, basis: { value: 'both' },
    };
    await g.JournalPage.save({ preventDefault() {}, target: form });
    assert.equal(g.calls.put.length, 1);
    assert.equal(g.calls.put[0].url, '/journal/7');
    assert.equal(JSON.stringify(g.calls.put[0].body.lines.map(l => l.job_id)), "[5,null]");
    assert.equal(g.calls.post.length, 0);
});

test('a refused edit (a closed period) shows the server sentence', async () => {
    const sentence = 'Transaction date 2026-01-01 is on or before the closing date (2026-06-30). Modifications to closed periods are not allowed.';
    const f = page({ put: async () => { throw Object.assign(new Error(sentence), { status: 400, detail: sentence }); } });
    await f.JournalPage._putEntry(7, { lines: [] });
    assert.deepEqual(f.calls.toast, [[sentence, 'error']]);
});
