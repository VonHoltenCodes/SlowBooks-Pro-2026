// #897: the job cost entry form offers 2010 Accrued Job Costs as an offset
// (made on first use when the chart has none) and shows the Reverses on
// date only while a line credits it, defaulting to the first of the next
// month; the request sends accrue / reverses_on. A refusal (a line on
// Accounts Payable or Receivable) shows the server's sentence, on the job
// cost form and on the journal form, new entry and edit.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const AP_REFUSAL = "Line 2 can't post to 2000 Accounts Payable, which holds vendor bills; enter the cost as a bill (Enter Bill, coded to the job) so it ages, shows on the vendor and can be paid.";
const CHART = [
    { id: 1, account_number: '2000', name: 'Accounts Payable', account_type: 'liability' },
    { id: 2, account_number: '5300', name: 'Subcontractor Costs', account_type: 'cogs' },
];
const ACCRUED = { id: 9, account_number: '2010', name: 'Accrued Job Costs', account_type: 'liability' };

const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[c]);

// A select or input as the form reads it.
const field = value => ({ value, dataset: {} });

function jobCosts({ accounts = CHART, rows = [], wrap = null, post = null } = {}) {
    const calls = { post: [], toast: [], put: [] };
    const context = {
        console,
        $: sel => (sel === '#jc-reverses-wrap' ? wrap : null),
        $$: sel => (sel === '#jc-lines .jc-credit' ? rows.map(r => r.querySelector('.jc-credit')) : rows),
        API: {
            get: async () => [],
            post: post || (async (url, body) => { calls.post.push({ url, body }); return { number: 'JC-000001', total: 4000, reverses_on: body.reverses_on }; }),
            put: async (url, body) => { calls.put.push({ url, body }); return { number: 'JC-000001', reverses_on: body.reverses_on }; },
        },
        T: word => word,
        Terms: { text: word => word },
        escapeHtml,
        formatCurrency: value => `$${value}`,
        accountWords: a => `${a.account_number} ${a.name}`,
        todayISO: () => '2026-10-30',
        TxnWarn: { ok: async () => true },
        toast: (...args) => calls.toast.push(args),
        toastPosted: () => {},
        closeModal: () => {},
        openModal: () => {},
        location: { hash: '#/job-costs' },
        App: { navigate: () => {} },
        window: {},
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/job_costs.js'), 'utf8'), context);
    const page = context.window.JobCostsPage;
    page._accounts = accounts;
    return { page, calls };
}

function costRow(credit) {
    const fields = {
        '.jc-who': field(''), '.jc-qty': field('1'), '.jc-rate': field('4000'),
        '.jc-code': field(''), '.jc-type': field(''), '.jc-desc': field('Framing sub'),
        '.jc-debit': field('2'), '.jc-credit': field(credit), '.jc-billable': { checked: false },
    };
    return { querySelector: sel => fields[sel] || null };
}

function reversesWrap(date) {
    const input = field('');
    const form = { querySelector: () => field(date) };
    return { hidden: true, input, querySelector: () => input, closest: () => form };
}

test('the offset list offers 2010 to make when the chart has none', () => {
    const { page } = jobCosts();
    const html = page.offsetOptions();
    assert.match(html, /<option value="accrue">2010 Accrued Job Costs \(bill not in yet\)<\/option>/);
    assert.equal(page.isAccrualOffset('accrue'), true);
    assert.equal(page.isAccrualOffset('1'), false); // Accounts Payable is no accrual
});

test("with 2010 in the chart it is that account's own option", () => {
    const { page } = jobCosts({ accounts: [...CHART, ACCRUED] });
    const html = page.offsetOptions();
    assert.doesNotMatch(html, /value="accrue"/);
    assert.match(html, /<option value="9" >2010 Accrued Job Costs<\/option>/);
    assert.equal(page.isAccrualOffset('9'), true);
});

test('the first of the next month, across the year end', () => {
    const { page } = jobCosts();
    assert.equal(page.firstOfNextMonth('2026-10-30'), '2026-11-01');
    assert.equal(page.firstOfNextMonth('2026-12-31'), '2027-01-01');
    assert.equal(page.firstOfNextMonth(''), '');
});

test('Reverses on shows while a line credits 2010, with its default, and hides otherwise', () => {
    const wrap = reversesWrap('2026-10-30');
    const rows = [costRow('accrue')];
    const { page } = jobCosts({ rows, wrap });
    page.accrualChanged();
    assert.equal(wrap.hidden, false);
    assert.equal(wrap.input.value, '2026-11-01');
    // a date the person typed is kept
    wrap.input.value = '2026-11-15';
    page.reversesTouched(wrap.input);
    page.accrualChanged();
    assert.equal(wrap.input.value, '2026-11-15');
    // no line on 2010: hidden
    rows[0] = costRow('2');
    page.accrualChanged();
    assert.equal(wrap.hidden, true);
});

test('an accrual line posts accrue and the Reverses on date; a cleared date is null', async () => {
    const { page, calls } = jobCosts({ rows: [costRow('accrue')] });
    const form = {
        date: field('2026-10-30'), job_id: field('4'), memo: field(''),
        reverses_on: field('2026-11-01'),
    };
    await page.save({ preventDefault() {}, target: form });
    assert.equal(calls.post.length, 1);
    const body = calls.post[0].body;
    assert.equal(body.reverses_on, '2026-11-01');
    assert.equal(body.lines[0].accrue, true);
    assert.equal(body.lines[0].credit_account_id, null);
    form.reverses_on.value = '';
    await page.save({ preventDefault() {}, target: form });
    assert.equal(calls.post[1].body.reverses_on, null);
});

test('an ordinary entry sends no Reverses on date', async () => {
    const { page, calls } = jobCosts({ rows: [costRow('2')] });
    const form = { date: field('2026-10-30'), job_id: field('4'), memo: field(''), reverses_on: field('2026-11-01') };
    await page.save({ preventDefault() {}, target: form });
    assert.equal('reverses_on' in calls.post[0].body, false);
    assert.equal(calls.post[0].body.lines[0].accrue, false);
});

test("a refused job cost entry shows the server's sentence", async () => {
    const sentence = AP_REFUSAL.replace('Line 2', 'Line 1');
    const { page, calls } = jobCosts({
        rows: [costRow('1')],
        post: async () => { throw Object.assign(new Error(sentence), { status: 422, detail: sentence }); },
    });
    const form = { date: field('2026-10-30'), job_id: field('4'), memo: field('') };
    await page.save({ preventDefault() {}, target: form });
    assert.deepEqual(calls.toast, [[sentence, 'error']]);
});

test("the entry's view shows an open accrual's date to change, a reversed one's date, and its Activity", () => {
    const { page } = jobCosts();
    const open = page.accrualHtml({ id: 3, accrued: '4000.00', reverses_on: '2026-11-01', reversed_on: null, status: 'posted' });
    assert.match(open, /Reverses on/);
    assert.match(open, /value="2026-11-01"/);
    assert.match(open, /saveReversesOn\(event, 3\)/);
    const done = page.accrualHtml({ id: 3, accrued: '4000.00', reverses_on: '2026-11-01', reversed_on: '2026-11-01', status: 'posted' });
    assert.match(done, /reversed on 2026-11-01/);
    assert.doesNotMatch(done, /<input/);
    assert.equal(page.accrualHtml({ id: 4, accrued: '0', status: 'posted' }), '');
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/job_costs.js'), 'utf8');
    assert.match(src, /activityPanelHtml\('job_cost', jc\.id\)/);
});

test('saving a cleared date sends null', async () => {
    const { page, calls } = jobCosts();
    page.view = () => {}; // the view reopens; not this test's
    await page.saveReversesOn({ preventDefault() {}, target: { reverses_on: field('') } }, 3);
    assert.equal(JSON.stringify(calls.put), JSON.stringify([{ url: '/job-costs/3/reverses-on', body: { reverses_on: null } }]));
});

// ── the journal form ───────────────────────────────────────────────────

function journal({ post, put } = {}) {
    const calls = { toast: [] };
    const context = {
        $: () => null,
        $$: () => [],
        API: { get: async () => [], post, put },
        App: { isAccountant: () => false, navigate: () => {} },
        location: { hash: '#/journal' },
        T: word => word,
        Terms: { text: word => word },
        escapeHtml,
        formatDate: v => v, formatCurrency: v => `$${v}`,
        todayISO: () => '2026-10-31',
        classIdFromForm: () => null,
        jobIdFromForm: () => null,
        TxnWarn: { ok: async () => true },
        ClassWarn: { ok: async () => true },
        toast: (...args) => calls.toast.push(args),
        toastPosted: () => {},
        closeModal: () => {},
        openModal: () => {},
        document: { querySelector: () => null },
    };
    vm.createContext(context);
    context.PendingFiles = { reset() {}, stripHtml: () => '', ids: () => [], saved() {}, render() {}, discard() {} };
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/journal.js'), 'utf8') + '\nthis.JournalPage = JournalPage;', context);
    context.JournalPage.collectLines = () => [
        { account_id: 2, debit: 300, credit: 0 },
        { account_id: 1, debit: 0, credit: 300 },
    ];
    return { JournalPage: context.JournalPage, calls };
}

const refused = async () => { throw Object.assign(new Error(AP_REFUSAL), { status: 422, detail: AP_REFUSAL }); };

test("a new journal entry on Accounts Payable shows the server's sentence", async () => {
    const f = journal({ post: refused });
    const form = { dataset: {}, date: field('2026-10-31'), description: field('month-end'), reference: field(''), basis: field('both') };
    await f.JournalPage.save({ preventDefault() {}, target: form });
    assert.deepEqual(f.calls.toast, [[AP_REFUSAL, 'error']]);
});

test("an edit moving a line onto Accounts Payable shows the server's sentence", async () => {
    const f = journal({ put: refused });
    await f.JournalPage._putEntry(7, { lines: [] });
    assert.deepEqual(f.calls.toast, [[AP_REFUSAL, 'error']]);
});
