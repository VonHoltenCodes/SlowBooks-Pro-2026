const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function fixture(settings = {}) {
    const writes = [], dialogs = [];
    const invoice = {
        id: 42, invoice_number: 'INV-42', customer_id: 1, customer_name: 'Example customer',
        date: '2026-09-26', due_date: '2026-10-26', terms: 'Net 30', status: 'draft',
        subtotal: 100, total: 100, tax_rate: 0, tax_amount: 0, amount_paid: 0, balance_due: 100,
        lines: [{ description: 'Consulting', quantity: 1, rate: 100, amount: 100 }],
    };
    const context = {
        API: {
            get: async url => url === '/settings' ? settings : url === '/invoices/42' ? invoice : [],
            put: async (url, data) => { writes.push({ url, data }); return { ...settings, ...data }; },
        },
        App: { settings: { ...settings } }, window: {},
        T: value => value, Terms: { text: value => value, isNonprofit: () => false },
        escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, character => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[character]),
        formatDate: value => value, formatCurrency: value => `$${value}`, statusBadge: value => value,
        todayISO: () => '2026-09-26', classFormGroupHtml: async () => '', jobFormGroupHtml: async () => '',
        currencyFormGroupsHtml: () => '', openModal: (title, html) => dialogs.push({ title, html }),
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/invoices.js'), 'utf8'), context);
    const page = context.window.InvoicesPage;
    page.loadAttachments = () => {};
    page.recalc = () => {};
    page._recomputeDueDate = () => {};
    page.customerSelected = () => {};
    return { page, context, writes, dialogs };
}

const checkbox = html => html.match(/<input[^>]*id="inv-show-logo"[^>]*>/)?.[0];
const preview = html => html.match(/<img[^>]*class="invoice-logo-preview"[^>]*>/)?.[0];

for (const [name, open] of [
    ['creating', page => page.showForm()],
    ['editing', page => page.showForm(42)],
    ['viewing a draft', page => page.view(42)],
]) {
    test(`${name} an invoice shows the saved logo preference, or hides the option without a logo`, async () => {
        const settings = { company_logo_path: '/static/uploads/company_logo.png' };
        const f = fixture(settings);
        await open(f.page);
        let html = f.dialogs.at(-1).html;
        assert.match(checkbox(html), /checked/);
        assert.doesNotMatch(preview(html), /hidden/);
        assert.match(html, /Applies to all invoices/);
        settings.invoice_show_logo = 'false';
        await open(f.page);
        html = f.dialogs.at(-1).html;
        assert.doesNotMatch(checkbox(html), /checked/);
        assert.match(preview(html), /hidden/);
        settings.company_logo_path = '';
        await open(f.page);
        assert.equal(checkbox(f.dialogs.at(-1).html), undefined);
        assert.equal(preview(f.dialogs.at(-1).html), undefined);
    });
}

function control(checked) {
    const image = { hidden: checked };
    const classes = new Set();
    const status = { textContent: '', classList: { add: value => classes.add(value), remove: value => classes.delete(value) } };
    const actions = [{ disabled: false }, { disabled: true }];
    const option = { querySelector: selector => selector === '.invoice-logo-preview' ? image : status };
    const modal = { querySelectorAll: () => actions };
    const input = { checked, disabled: false, closest: selector => selector === '.invoice-logo-option' ? option : modal };
    return { input, image, status, actions, classes };
}

for (const checked of [false, true]) {
    test(`turning the logo ${checked ? 'on' : 'off'} saves the company setting without rebuilding the invoice form`, async () => {
        const f = fixture({ invoice_show_logo: String(!checked) });
        const c = control(checked);
        await f.page.setLogoOption(c.input);
        assert.equal(f.writes.length, 1);
        assert.equal(f.writes[0].url, '/settings');
        assert.deepEqual(Object.keys(f.writes[0].data), ['invoice_show_logo']);
        assert.equal(f.writes[0].data.invoice_show_logo, String(checked));
        assert.equal(f.context.App.settings.invoice_show_logo, String(checked));
        assert.equal(c.image.hidden, !checked);
        assert.equal(c.input.disabled, false);
        assert.deepEqual(c.actions.map(button => button.disabled), [false, true]);
        assert.equal(c.status.textContent, 'Saved for all invoices.');
        assert.equal(f.dialogs.length, 0);
    });
}

test('document actions wait for the preference save; a failure restores the preference and shows the error', async () => {
    const f = fixture({ invoice_show_logo: 'true' });
    const c = control(false);
    let rejectSave;
    f.context.API.put = () => new Promise((resolve, reject) => { rejectSave = reject; });
    const saving = f.page.setLogoOption(c.input);
    assert.equal(c.input.disabled, true);
    assert.equal(c.actions.every(button => button.disabled), true);
    assert.match(c.status.textContent, /Saving/);
    rejectSave(new Error('Settings changes require an administrator'));
    await saving;
    assert.equal(c.input.checked, true);
    assert.equal(c.input.disabled, false);
    assert.equal(c.image.hidden, false);
    assert.equal(f.context.App.settings.invoice_show_logo, 'true');
    assert.deepEqual(c.actions.map(button => button.disabled), [false, true]);
    assert.match(c.status.textContent, /Settings changes require an administrator/);
    assert.equal(c.classes.has('invoice-logo-error'), true);
});

test('a configured logo path is escaped in the invoice dialog', () => {
    const f = fixture();
    const html = f.page.logoOptionHtml({ company_logo_path: '/logo.png" onerror="alert(1)' });
    assert.match(preview(html), /&quot;/);
    assert.doesNotMatch(preview(html), /" onerror=/);
});
