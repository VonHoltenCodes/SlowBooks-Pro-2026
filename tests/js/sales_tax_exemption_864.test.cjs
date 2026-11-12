// The document's "Sales tax exempt" switch (#864): on, every line's Tax box
// is unticked and held off, a new line starts untaxed, and the boxes come
// back as they were when it goes off. A non-taxable customer holds the
// switch on. A change of job sets the switch to the job's. The job form's
// "Sales tax: charged / exempt" shows and requires the job's certificate
// when Exempt, and sends none when Charged.
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

function loadJobsSalesTax() {
    const src = read('jobs.js');
    const start = src.indexOf('// "Sales tax: charged / exempt" (#864).');
    const end = src.indexOf('// Active customers only', start);
    const ctx = { escapeHtml: (v) => String(v), T: (w) => w };
    vm.createContext(ctx);
    vm.runInContext(`this.JobsPage = { ${src.slice(start, end)} };`, ctx);
    return ctx.JobsPage;
}

function jobFormOf(values) {
    const fields = Object.fromEntries(Object.entries(values).map(([name, value]) =>
        [name, { name, value, disabled: false, required: false }]));
    const box = { style: { display: 'none' }, querySelectorAll: () => Object.values(fields).filter(f => f.name.startsWith('exempt_')) };
    return {
        fields,
        box,
        querySelector: (sel) => {
            if (sel === '.job-exempt-certificate') return box;
            const m = sel.match(/\[name="([^"]+)"\]/);
            return m ? fields[m[1]] || null : null;
        },
    };
}

test('the job form asks for the certificate when Exempt and requires it', () => {
    const JobsPage = loadJobsSalesTax();
    const charged = JobsPage.salesTaxGroupHtml({ is_taxable: true });
    assert.match(charged, /<option value="true" selected>Charged<\/option>/);
    assert.match(charged, /name="exempt_certificate_number" maxlength="50" disabled/);
    assert.match(charged, /display:none;/);
    const exempt = JobsPage.salesTaxGroupHtml({
        is_taxable: false,
        exempt_certificate_number: 'E-77',
        exempt_certificate_state: 'IL',
        exempt_certificate_kind: 'public_entity',
        exempt_certificate_expires: '2027-06-30',
    });
    assert.match(exempt, /<option value="false" selected>Exempt<\/option>/);
    for (const name of ['exempt_certificate_number', 'exempt_certificate_state', 'exempt_certificate_kind']) {
        assert.match(exempt, new RegExp(`name="${name}"[^>]* required`), name);
    }
    assert.doesNotMatch(exempt, /name="exempt_certificate_expires"[^>]* required/);
    assert.match(exempt, /value="E-77"/);
    assert.match(exempt, /<option value="public_entity" selected>Public entity<\/option>/);
    for (const k of ['public_entity', 'nonprofit', 'resale', 'other']) assert.ok(exempt.includes(`value="${k}"`), k);
});

test('switching to Exempt shows and requires the certificate; Charged sends none', () => {
    const JobsPage = loadJobsSalesTax();
    const form = jobFormOf({
        is_taxable: 'true',
        exempt_certificate_number: ' E-9 ',
        exempt_certificate_state: 'WI',
        exempt_certificate_kind: 'nonprofit',
        exempt_certificate_expires: '',
    });
    const select = { form, value: 'false' };
    JobsPage.salesTaxPicked(select);
    assert.equal(form.box.style.display, '');
    assert.equal(form.fields.exempt_certificate_number.required, true);
    assert.equal(form.fields.exempt_certificate_expires.required, false);
    assert.equal(form.fields.exempt_certificate_expires.disabled, false);
    form.fields.is_taxable.value = 'false';
    assert.equal(JobsPage.formTaxable(form), false);
    assert.deepEqual({ ...JobsPage.formCertificate(form) }, {
        exempt_certificate_number: 'E-9',
        exempt_certificate_state: 'WI',
        exempt_certificate_kind: 'nonprofit',
        exempt_certificate_expires: null,
    });
    select.value = 'true';
    form.fields.is_taxable.value = 'true';
    JobsPage.salesTaxPicked(select);
    assert.equal(form.box.style.display, 'none');
    assert.equal(form.fields.exempt_certificate_number.disabled, true);
    assert.deepEqual({ ...JobsPage.formCertificate(form) }, {});
});

test('the job form sends the certificate and the header badge names it', () => {
    const src = read('jobs.js');
    assert.ok(src.includes('...JobsPage.formCertificate(form),'));
    assert.ok(!src.includes('customerPicked'), 'a new job no longer follows its customer');
    const JobsPage = loadJobsSalesTax();
    const title = JobsPage.certificateTitle({
        exempt_certificate_number: 'E-1', exempt_certificate_state: 'IL',
        exempt_certificate_kind: 'resale', exempt_certificate_expires: '2027-01-31',
    });
    assert.match(title, /^Resale certificate E-1 \(IL\), expires 2027-01-31\./);
});

test('the customer form and the permits page carry no certificate pieces', () => {
    assert.ok(!read('customers.js').includes('exemption_certificate_id'));
    assert.ok(!read('reseller_permits.js').includes('kind_label'));
});
