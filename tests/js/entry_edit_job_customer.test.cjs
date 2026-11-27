// Edit entry dropped two fields without a word: the form said "Entry saved"
// and reopening showed the old value. (1) The Job box: on Edit, _entryData
// replaced the picked job with the one the entry had when it opened, a
// leftover from #388 when the form had no Job box. (2) The Customer box shows
// on money out too, but _entryData sent customer_id only on money in. Now an
// edit sends what the Job box holds, and a plain entry sends its Customer
// either way. An entry whose job was made inactive keeps it as a choice, so
// saving it as is does not clear the job.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function select(values, value) {
    const options = values.map(v => ({ value: String(v) }));
    return {
        options,
        value: String(value),
        appendChild(o) { options.push(o); },
    };
}

function loadBankingPage(modalForm) {
    const context = {
        window: {},
        console,
        T: s => s,
        Terms: { isNonprofit: () => false },
        classIdFromForm: f => (f.class_id && f.class_id.value ? parseInt(f.class_id.value, 10) : null),
        jobIdFromForm: f => (f.job_id && f.job_id.value ? parseInt(f.job_id.value, 10) : null),
        document: {
            querySelector: () => modalForm || null,
            getElementById: () => null,
            createElement: () => ({}),
        },
    };
    vm.createContext(context);
    const js = fs.readFileSync(
        path.join(__dirname, '../../app/static/js/banking.js'),
        'utf8'
    );
    vm.runInContext(js + '\nthis.BankingPage = BankingPage;', context);
    const BP = context.BankingPage;
    BP._splitLines = () => null;
    BP._payeeVendorId = () => null;
    return BP;
}

function form({ amount = '-50.00', job, customer = '', dataset = {} } = {}) {
    const f = {
        dataset,
        date: { value: '2026-09-02' },
        amount: { value: amount },
        category_account_id: { value: '7' },
        payee: { value: 'Fuel Stop' },
        description: { value: '' },
        check_number: { value: '' },
        customer_id: { value: customer },
    };
    if (job !== undefined) f.job_id = { value: job };
    return f;
}

test('an edit sends the job picked in the Job box, not the one it opened with', () => {
    const BP = loadBankingPage();
    const opened = { entryId: '12', jobId: '4' };
    assert.equal(BP._entryData(form({ job: '9', dataset: opened }), 1).job_id, 9);
    // clearing the box clears the job
    assert.equal(BP._entryData(form({ job: '', dataset: opened }), 1).job_id, null);
    // a new entry: the pick, as before
    assert.equal(BP._entryData(form({ job: '9' }), 1).job_id, 9);
});

test('an edit with no Job box keeps the entry\'s own job', () => {
    const BP = loadBankingPage();
    const body = BP._entryData(form({ dataset: { entryId: '12', jobId: '4' } }), 1);
    assert.equal(body.job_id, 4);
    const none = BP._entryData(form({ dataset: { entryId: '12', jobId: '' } }), 1);
    assert.equal(none.job_id, null);
});

test('a customer picked on money out is sent, and clearing it sends null', () => {
    const BP = loadBankingPage();
    const edit = { entryId: '12', jobId: '' };
    assert.equal(BP._entryData(form({ customer: '5', dataset: edit }), 1).customer_id, 5);
    assert.equal(BP._entryData(form({ customer: '5' }), 1).customer_id, 5);
    assert.equal(BP._entryData(form({ customer: '', dataset: edit }), 1).customer_id, null);
    // money in, as before
    assert.equal(BP._entryData(form({ amount: '50.00', customer: '5' }), 1).customer_id, 5);
});

test('a split sends no entry-level customer', () => {
    const BP = loadBankingPage();
    BP._splitLines = () => [{ category_account_id: 7, amount: '-50.00' }];
    assert.ok(!('customer_id' in BP._entryData(form({ customer: '5' }), 1)));
});

test('an entry whose job is now inactive keeps it in the Job box', () => {
    const f = form({ job: '', dataset: {} });
    f.job_id = select(['', '9'], '');
    f.customer_id = select([''], '');
    f.querySelector = () => null;
    f.date.defaultValue = '';
    const BP = loadBankingPage(f);
    BP._fillEntryForm({
        id: 12, date: '2026-09-02', amount: '-50.00', payee: 'Fuel Stop',
        job_id: 4, job_name: 'Lakeview Bakery:Kitchen', category_account_id: 7,
    });
    assert.equal(f.job_id.value, '4');
    assert.equal(f.job_id.options.at(-1).textContent, 'Lakeview Bakery:Kitchen (inactive)');
    // saving it as is sends the same job
    assert.equal(BP._entryData(f, 1).job_id, 4);
});

test('an active job is picked, not added twice', () => {
    const f = form({ job: '', dataset: {} });
    f.job_id = select(['', '4', '9'], '4');
    f.customer_id = select([''], '');
    f.querySelector = () => null;
    const BP = loadBankingPage(f);
    BP._fillEntryForm({ id: 12, date: '2026-09-02', amount: '-50.00', job_id: 4, category_account_id: 7 });
    assert.equal(f.job_id.options.length, 3);
    assert.equal(f.job_id.value, '4');
});
