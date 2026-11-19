// A reseller permit's Verify dialog offers Copy buttons for the permit
// number, the business name and the tax ID — all operator-entered text.
//
// The old markup built onclick="ResellerPermitsPage._copy('${escapeHtml(value)}', 'Label')".
// escapeHtml() emits &#39; for a single quote, and the HTML parser decodes
// &#39; back to ' BEFORE any JS runs, so the escaped value landed inside the
// decoded handler as a live quote. A name like Joe's Pizza broke the button;
// a crafted value like x');alert(1);(' ran script (stored XSS).
//
// The fix carries the value in data-copy and reads it back through
// this.dataset in the handler, so it is text the parser never treats as code.
// This suite proves both halves: the new wiring copies the exact text and
// runs nothing, and the old shape (the control) really did execute.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

// Exactly what app/static/js/utils.js emits.
function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// What a browser does to an attribute's value before any JS reads it: the
// five named/numeric entities escapeHtml produces, decoded back to text.
function decodeEntities(value) {
    return value
        .replace(/&#39;/g, "'").replace(/&quot;/g, '"')
        .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
}

// Render the Verify dialog for a permit whose number and tax ID are `value`.
async function renderVerify(value) {
    let shown = '';
    const permit = {
        id: 1, jurisdiction: 'WA', permit_number: value,
        entity_type: 'customer', entity_id: 1,
        verification_url: '', expires_at: null, is_expired: false,
        days_to_expire: null,
    };
    const customers = [{ id: 1, name: value, tax_id: value }];
    const context = {
        escapeHtml,
        customerLabel: (c) => c.full_name || c.name,  // utils.js: "Parent: Sub" for a sub-customer (#638)
        toast: () => {},
        API: { get: async (url) => (url.startsWith('/customers') ? customers : url === '/vendors' ? [] : permit) },  // the page asks for the tree (#638)
        openModal: (_title, html) => { shown = html; },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/hint.js'), 'utf8'), context);  // hint() (#977)
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../../app/static/js/reseller_permits.js'), 'utf8')
        + '\nthis.ResellerPermitsPage = ResellerPermitsPage;',
        context,
    );
    await context.ResellerPermitsPage.verifyWorkflow(1);
    return shown;
}

// Pull out every Copy button as (dataCopy, dataLabel, onclick), all still
// HTML-escaped as they sit in the markup. A `"` in the value is &quot;, so it
// cannot close the attribute — that is the property the fix relies on.
function copyButtons(html) {
    const re = /data-copy="([^"]*)" data-copy-label="([^"]*)"\s+onclick="([^"]*)"/g;
    const out = [];
    let m;
    while ((m = re.exec(html)) !== null) out.push({ copy: m[1], label: m[2], onclick: m[3] });
    return out;
}

// Click the button the way the browser would: decode the data- attributes
// into the element's dataset, then run the handler with `this` bound to it.
// A spy stands in for _copy; alert() must never fire.
function click(button) {
    const el = { dataset: { copy: decodeEntities(button.copy), copyLabel: decodeEntities(button.label) } };
    const calls = [];
    let alerted = 0;
    const context = {
        ResellerPermitsPage: { _copy: (text, label) => calls.push({ text, label }) },
        alert: () => { alerted += 1; },
    };
    vm.createContext(context);
    vm.runInContext(`(function(){ ${decodeEntities(button.onclick)} }).call(this.__el);`, Object.assign(context, { __el: el }));
    return { calls, alerted };
}

for (const value of ["Joe's Pizza", `x');alert(1);('`, 'Plain Co']) {
    test(`Copy buttons copy ${JSON.stringify(value)} verbatim and run nothing`, async () => {
        const html = await renderVerify(value);
        const buttons = copyButtons(html);

        // All three buttons (permit #, business name, tax ID) use the data-
        // pattern; none interpolate the value into the handler as a string.
        assert.equal(buttons.length, 3, html);
        for (const button of buttons) {
            assert.equal(button.onclick, 'ResellerPermitsPage._copy(this.dataset.copy, this.dataset.copyLabel)');
        }

        // All three carry the operator's text: permit number, the customer's
        // name (the dialog once showed "[object Object]" here), and tax ID.
        const carriers = buttons.filter((b) => decodeEntities(b.copy) === value);
        assert.equal(carriers.length, 3, `expected every button to carry the value, got ${JSON.stringify(buttons)}`);
        for (const button of carriers) {
            const { calls, alerted } = click(button);
            assert.equal(alerted, 0, 'the click ran script');
            assert.deepEqual(calls, [{ text: value, label: button.label }], 'the exact text did not reach _copy');
        }
    });
}

// The control: the retired markup, built and decoded the same way, really did
// execute. Without this the assertions above could pass for an unrelated
// reason (as a source-string check once did — see test_onclick_attributes_parse.py).
test('the old inline-string handler was exploitable (control)', () => {
    const payload = `x');alert(1);('`;
    const onclick = `ResellerPermitsPage._copy('${escapeHtml(payload)}', 'Permit #')`;
    let alerted = 0;
    const context = {
        ResellerPermitsPage: { _copy: () => {} },
        alert: () => { alerted += 1; },
    };
    vm.createContext(context);
    vm.runInContext(decodeEntities(onclick), context);
    assert.equal(alerted, 1, 'the control did not reproduce the break');
});
