// The document's "Sales tax exempt" switch (#864): on, every line's Tax box
// is unticked and held off, a new line starts untaxed, and the boxes come
// back as they were when it goes off. A non-taxable customer holds the
// switch on. A change of job sets the switch to the job's. The job form's
// "Sales tax: charged / exempt" follows a new job's customer until set.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const JS = path.join(__dirname, '../../app/static/js');
const read = (f) => fs.readFileSync(path.join(JS, f), 'utf8');

function loadTaxExempt() {
    const utils = read('utils.js');
    const src = utils.slice(utils.indexOf('const TaxExempt = {'), utils.indexOf('window.TaxExempt = TaxExempt;'));
    const ctx = {};
    vm.createContext(ctx);
    vm.runInContext(src + '\nthis.TaxExempt = TaxExempt;', ctx);
    return ctx.TaxExempt;
}

const box = (checked) => ({ checked, disabled: false, dataset: {}, title: '' });

function tbodyOf(boxes) {
    return { querySelectorAll: (sel) => (sel === '.line-taxable' ? boxes : []) };
}

function formOf({ on = false, jobs = [], jobId = '' } = {}) {
    const sw = { checked: on, disabled: false, dataset: {}, title: '' };
    const options = [{ value: '', dataset: {} }, ...jobs.map(j => ({ value: String(j.id), dataset: { taxable: j.taxable ? '1' : '0' } }))];
    const select = {
        options,
        get selectedIndex() { return Math.max(0, options.findIndex(o => o.value === select.value)); },
        value: String(jobId),
        listeners: [],
        addEventListener(type, fn) { if (type === 'change') select.listeners.push(fn); },
        change(v) { select.value = String(v); select.listeners.forEach(fn => fn()); },
    };
    return {
        sw,
        select,
        querySelector: (sel) => (sel === '[name="tax_exempt"]' ? sw : sel === 'select[name="job_id"]' ? select : null),
    };
}

const customers = [{ id: 1, name: 'Shop', is_taxable: true }, { id: 2, name: 'County', is_taxable: false }];

test('the switch on unticks and holds every line, off gives them back', () => {
    const TaxExempt = loadTaxExempt();
    const boxes = [box(true), box(false)];
    const form = formOf({ on: true });
    assert.equal(TaxExempt.enforce(customers, 1, tbodyOf(boxes), form), true);
    assert.deepEqual(boxes.map(b => [b.checked, b.disabled]), [[false, true], [false, true]]);
    assert.match(boxes[0].title, /exempt/);
    form.sw.checked = false;
    assert.equal(TaxExempt.enforce(customers, 1, tbodyOf(boxes), form), false);
    assert.deepEqual(boxes.map(b => [b.checked, b.disabled]), [[true, false], [false, false]]);
});

test('a new line added while exempt starts untaxed', () => {
    const TaxExempt = loadTaxExempt();
    const boxes = [box(true)];
    const form = formOf({ on: true });
    TaxExempt.enforce(customers, 1, tbodyOf(boxes), form);
    boxes.push(box(true)); // the new line's box, ticked by the company default
    TaxExempt.enforce(customers, 1, tbodyOf(boxes), form);
    assert.equal(boxes[1].checked, false);
    assert.equal(boxes[1].disabled, true);
});

test('a non-taxable customer holds the switch on, and lets it go after', () => {
    const TaxExempt = loadTaxExempt();
    const boxes = [box(true)];
    const form = formOf({ on: false });
    TaxExempt.enforce(customers, 2, tbodyOf(boxes), form);
    assert.equal(form.sw.checked, true);
    assert.equal(form.sw.disabled, true);
    assert.equal(TaxExempt.value(form), true);
    TaxExempt.enforce(customers, 1, tbodyOf(boxes), form);
    assert.equal(form.sw.checked, false);
    assert.equal(form.sw.disabled, false);
    assert.equal(boxes[0].checked, true);
});

test('without a switch the forms keep the customer-only rule', () => {
    const TaxExempt = loadTaxExempt();
    const boxes = [box(true)];
    assert.equal(TaxExempt.enforce(customers, 1, tbodyOf(boxes)), false);
    assert.equal(boxes[0].checked, true);
    assert.equal(TaxExempt.enforce(customers, 2, tbodyOf(boxes)), true);
    assert.equal(boxes[0].checked, false);
});

test('a change of job sets the switch to the job, then recalculates', () => {
    const TaxExempt = loadTaxExempt();
    const form = formOf({ jobs: [{ id: 7, taxable: false }, { id: 8, taxable: true }] });
    let recalcs = 0;
    TaxExempt.watchJob(form, () => { recalcs += 1; });
    form.select.change(7);
    assert.equal(form.sw.checked, true);
    form.select.change(8);
    assert.equal(form.sw.checked, false);
    form.select.change('');
    assert.equal(form.sw.checked, false);
    assert.equal(recalcs, 3);
});

test('the switch is drawn named tax_exempt, ticked when on', () => {
    const TaxExempt = loadTaxExempt();
    const on = TaxExempt.switchHtml(true, 'X.recalc()');
    assert.match(on, /name="tax_exempt"/);
    assert.match(on, /checked/);
    assert.match(on, /onchange="X.recalc\(\)"/);
    assert.doesNotMatch(TaxExempt.switchHtml(false, 'X.recalc()'), / checked /);
});

test('the three document forms carry the switch and send it', () => {
    for (const [file, formId] of [['invoices.js', 'invoice-form'], ['estimates.js', 'est-form'], ['sales_receipts.js', 'sales-receipt-form']]) {
        const src = read(file);
        assert.match(src, /TaxExempt\.switchHtml\(/, file);
        assert.ok(src.includes(`TaxExempt.watchJob($('#${formId}')`), file);
        assert.ok(src.includes(`$('#${formId}'));`), `${file} passes its form to enforce`);
        assert.match(src, /tax_exempt: TaxExempt\.value\(form\)/, file);
    }
    // the job picker says which jobs are exempt
    assert.match(read('utils.js'), /data-taxable="\$\{j\.is_taxable === false \? '0' : '1'\}"/);
});

test('a new job takes its customer\'s Sales tax until it is set by hand', () => {
    const src = read('jobs.js');
    const start = src.indexOf('salesTaxGroupHtml(job, id) {');
    const end = src.indexOf('// Active customers only', start);
    const body = src.slice(start, end);
    const ctx = {};
    vm.createContext(ctx);
    vm.runInContext(`this.JobsPage = { ${body} };`, ctx);
    const JobsPage = ctx.JobsPage;
    JobsPage._formCustomers = customers;
    assert.match(JobsPage.salesTaxGroupHtml({ is_taxable: false }, null), /<option value="false" selected>Exempt<\/option>/);
    assert.match(JobsPage.salesTaxGroupHtml({ is_taxable: true }, null), /<option value="true" selected>Charged<\/option>/);
    const tax = { value: 'true', dataset: {} };
    const form = { querySelector: () => tax };
    JobsPage.customerPicked({ form, value: '2' });
    assert.equal(tax.value, 'false');
    tax.dataset.touched = '1';
    JobsPage.customerPicked({ form, value: '1' });
    assert.equal(tax.value, 'false');
    assert.equal(JobsPage.formTaxable(form), false);
});
