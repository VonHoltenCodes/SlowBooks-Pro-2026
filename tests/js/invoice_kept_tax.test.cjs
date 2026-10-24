// An invoice with a tax amount and no rate behind it (QuickBooks Online's,
// when its tax lines don't make one rate) keeps that tax when it is edited,
// as the server keeps it (2.18.0). The form shows the kept tax in its
// totals and says so under Tax Rate, until a rate is entered or no line is
// taxable any more.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function row(qty, rate, taxable) {
    const els = {
        '.line-item': { value: '' },
        '.line-qty': { value: String(qty) },
        '.line-rate': { value: String(rate) },
        '.line-taxable': { checked: taxable, disabled: false, dataset: {} },
        '.line-amount': { textContent: '' },
    };
    return { querySelector: selector => els[selector] || null, els };
}

function fixture(invoice) {
    const dialogs = [];
    const form = {
        rate: { value: '0' },
        rows: [row(1, 1000, true)],
        cells: { 'inv-subtotal': {}, 'inv-tax': {}, 'inv-total': {}, 'inv-kept-tax': { style: {} } },
    };
    const $ = selector => {
        if (selector === '#invoice-form [name="tax_rate"]') return form.rate;
        if (selector === '#inv-lines') return { querySelectorAll: () => form.rows };
        return null;
    };
    const context = {
        API: { get: async url => url === '/settings' ? {} : url === '/invoices/42' ? invoice : [] },
        App: { settings: {} }, window: {},
        T: value => value, Terms: { text: value => value, isNonprofit: () => false, isConstruction: () => false },
        escapeHtml: value => String(value ?? ''),
        formatDate: value => value, statusBadge: value => value,
        formatCurrency: value => `$${Number(value || 0).toFixed(2)}`,
        todayISO: () => '2026-09-26', classFormGroupHtml: async () => '', jobFormGroupHtml: async () => '',
        currencyFormGroupsHtml: () => '', openModal: (title, html) => dialogs.push({ title, html }),
        TaxExempt: { enforce() {} },
        document: { getElementById: id => form.cells[id] || null },
        $, $$: selector => selector === '#inv-lines tr' ? form.rows : [],
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/invoices.js'), 'utf8'), context);
    const page = context.window.InvoicesPage;
    page.loadAttachments = () => {};
    page._recomputeDueDate = () => {};
    page.customerSelected = () => {};
    return { page, form, dialogs };
}

const fromQbo = {
    id: 42, invoice_number: '1042', customer_id: 1, date: '2026-08-03', due_date: '2026-08-03',
    terms: 'Net 30', status: 'sent', subtotal: '1000.00', tax_rate: '0.0000', tax_amount: '88.75',
    total: '1088.75', amount_paid: '0.00', balance_due: '1088.75',
    lines: [{ description: 'Catering', quantity: 1, rate: 1000, amount: 1000, is_taxable: true }],
};
const hint = html => html.match(/<div class="hint" id="inv-kept-tax">([^<]*)<\/div>/)?.[1];

test('editing it keeps the tax it came in with, and says so under Tax Rate', async () => {
    const f = fixture(fromQbo);
    await f.page.showForm(42);
    assert.match(hint(f.dialogs.at(-1).html), /Tax stays at \$88\.75, the amount it came in with/);
    const t = f.page.recalc();
    assert.deepEqual([t.subtotal, t.tax, t.total], [1000, 88.75, 1088.75]);
    assert.equal(f.form.cells['inv-tax'].textContent, '$88.75');
    assert.equal(f.form.cells['inv-kept-tax'].style.display, '');
    // a new line changes the subtotal, not the tax
    f.form.rows.push(row(1, 10, false));
    assert.deepEqual([f.page.recalc().tax, f.page.recalc().total], [88.75, 1098.75]);
});

test('a rate entered works the tax out from it, and the note goes', async () => {
    const f = fixture(fromQbo);
    await f.page.showForm(42);
    f.form.rate.value = '5';
    const t = f.page.recalc();
    assert.deepEqual([t.tax, t.total], [50, 1050]);
    assert.equal(f.form.cells['inv-kept-tax'].style.display, 'none');
});

test('with no line taxable there is no tax', async () => {
    const f = fixture(fromQbo);
    await f.page.showForm(42);
    f.form.rows[0].els['.line-taxable'].checked = false;
    const t = f.page.recalc();
    assert.deepEqual([t.tax, t.total], [0, 1000]);
    assert.equal(f.form.cells['inv-kept-tax'].style.display, 'none');
});

test('an invoice with a rate, and a new invoice, work tax out as before', async () => {
    const f = fixture({ ...fromQbo, tax_rate: '0.0890', tax_amount: '89.00', total: '1089.00' });
    await f.page.showForm(42);
    assert.equal(hint(f.dialogs.at(-1).html), undefined);
    f.form.rate.value = '8.9';
    assert.equal(f.page.recalc().tax, 89);
    f.form.rate.value = '0';
    assert.equal(f.page.recalc().tax, 0);
    await f.page.showForm();
    assert.equal(hint(f.dialogs.at(-1).html), undefined);
    assert.equal(f.page.recalc().tax, 0);
});
