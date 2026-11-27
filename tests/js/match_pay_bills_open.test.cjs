// Open bills findable from a bank-feed line. Match on a To review line can
// pay a vendor's open bills (or receive a customer's payment, #1056), but a
// money-out line with nothing to match said only "Nothing in the books has
// this amount... use Add", the bills sat behind a closed "Pay bills..."
// button, and none were fetched until a vendor was picked: testers on bank
// and card accounts read that as open bills "not showing". Now, with no
// candidates, the pay panel opens by itself, the sentence names Pay bills
// (or Receive payment), and the vendor is guessed when the line does not
// say: the one whose name begins the payee, else the only one with an open
// bill of the line's amount; else the panel says to pick one. When one open
// bill is due exactly the line's amount, only that bill is filled.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function el(id) {
    return { id, innerHTML: '', hidden: true, value: '', attrs: {}, focused: false,
        setAttribute(k, v) { this.attrs[k] = v; }, focus() { this.focused = true; } };
}

function load({ candidates = [], bills = [], invoices = [], vendors = [], customers = [], rowVendor = '' } = {}) {
    const calls = { get: [], pages: [], modal: [] };
    const elements = {};
    const doc = {
        getElementById(id) {
            if (id === 'feed-pay-party') {
                const panel = elements['feed-pay-panel'];
                if (!panel || !panel.innerHTML.includes('id="feed-pay-party"')) return null;
                if (!elements[id]) {
                    const m = panel.innerHTML.match(/<option value="(\d+)" selected>([^<]*)</);
                    const p = el(id);
                    p.value = m ? m[1] : '';
                    p.options = [{ text: m ? m[2] : '' }];
                    p.selectedIndex = 0;
                    elements[id] = p;
                }
                return elements[id];
            }
            if (id === `rv-vendor-7`) return rowVendor === null ? null : Object.assign(el(id), { value: rowVendor });
            const panel = elements['feed-pay-panel'];
            if (!elements[id] && panel && panel.innerHTML.includes(`id="${id}"`)) elements[id] = el(id);
            return elements[id] || null;
        },
        querySelectorAll: () => [],
    };
    const context = {
        window: {},
        document: doc,
        console,
        setTimeout: () => 0,
        sessionStorage: { getItem: () => null, setItem: () => {} },
        escapeHtml: (s) => String(s),
        formatCurrency: (n) => '$' + Number(n).toFixed(2),
        formatDate: (d) => String(d),
        sourceWord: (s) => String(s),
        customerLabel: (c) => c.name,
        T: (s) => s,
        toast: () => {},
        AccountQuickAdd: { picker: (html) => html, register: () => {} },
        openModal: (title, html) => {
            calls.modal.push([title, html]);
            for (const k of Object.keys(elements)) delete elements[k];
            for (const [, id] of html.matchAll(/id="([^"]+)"/g)) elements[id] = el(id);
        },
        API: { get: async (url) => { calls.get.push(url); return url.includes('/candidates') ? candidates : []; } },
        fetchAllPages: async (url) => {
            calls.pages.push(url);
            if (url.startsWith('/bills')) return url.includes('vendor_id=') ? bills.filter(b => url.includes(`vendor_id=${b.vendor_id}&`)) : bills;
            return url.includes('customer_id=') ? invoices.filter(i => url.includes(`customer_id=${i.customer_id}&`)) : invoices;
        },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/hint.js'), 'utf8'), context);
    const js = fs.readFileSync(path.join(__dirname, '../../app/static/js/banking.js'), 'utf8');
    vm.runInContext(js + '\nthis.BankingPage = BankingPage;', context);
    const BP = context.BankingPage;
    BP._vendorsByName = new Map(vendors.map(v => [v.name.toLowerCase(), v]));
    BP._customers = customers;
    return { BP, calls, elements };
}

const VENDORS = [{ id: 3, name: 'Parts Depot' }, { id: 4, name: 'Parts' }, { id: 5, name: 'Egg Farm' }];
const CHARGE = { id: 7, amount: '-90.00', payee: 'PARTS DEPOT #4411' };

test('a money-out line with nothing to match opens Pay bills and names it', async () => {
    const bills = [{ id: 21, vendor_id: 5, balance_due: '90.00', status: 'unpaid', date: '2026-10-01' }];
    const { BP, calls, elements } = load({ bills, vendors: VENDORS, rowVendor: '' });
    BP._reviewLines = { 7: { id: 7, amount: '-90.00', payee: 'ACH DEBIT 5521' } };
    await BP.showMatch(7, 2);
    const html = calls.modal[0][1];
    assert.match(html, /Nothing in the books has this amount[^<]*If it pays open bills, pick or check the vendor under <strong>Pay bills<\/strong> above\./);
    assert.match(html, /use <strong>Add<\/strong> to post it/);
    const panel = elements['feed-pay-panel'];
    assert.equal(panel.hidden, false, 'the pay panel is open by itself');
    assert.equal(elements['feed-pay-open'].attrs['aria-expanded'], 'true');
    // the payee names no one: the only vendor with an open bill of 90.00 is picked
    assert.match(panel.innerHTML, /<option value="5" selected>Egg Farm/);
    assert.ok(calls.pages.includes('/bills?open_only=true'));
    assert.ok(calls.pages.includes('/bills?vendor_id=5&open_only=true'), 'its bills are fetched');
    assert.match(elements['feed-pay-docs'].innerHTML, /data-doc="21"[^>]*value="90.00"/);
});

test('no bill of that amount: the vendor whose name begins the payee, the longest', async () => {
    const bills = [
        { id: 22, vendor_id: 3, balance_due: '40.00', status: 'unpaid', date: '2026-10-01' },
        { id: 23, vendor_id: 5, balance_due: '12.00', status: 'unpaid', date: '2026-10-02' },
    ];
    const { BP, elements } = load({ bills, vendors: VENDORS, rowVendor: '' });
    BP._reviewLines = { 7: CHARGE };
    await BP.showMatch(7, 2);
    assert.match(elements['feed-pay-panel'].innerHTML, /<option value="3" selected>Parts Depot/);
    assert.doesNotMatch(elements['feed-pay-panel'].innerHTML, /value="4" selected/);
});

test('two vendors with a bill of that amount and no name match: it says to pick one', async () => {
    const bills = [
        { id: 24, vendor_id: 5, balance_due: '90.00', status: 'unpaid', date: '2026-10-01' },
        { id: 25, vendor_id: 4, balance_due: '90.00', status: 'unpaid', date: '2026-10-01' },
    ];
    const { BP, elements } = load({ bills, vendors: VENDORS, rowVendor: '' });
    BP._reviewLines = { 7: { id: 7, amount: '-90.00', payee: 'ACH DEBIT 5521' } };
    await BP.showMatch(7, 2);
    assert.doesNotMatch(elements['feed-pay-panel'].innerHTML, / selected>/);
    assert.equal(elements['feed-pay-docs'].innerHTML.includes('Pick the vendor to see their open bills.'), true);
});

test('a name inside a longer word is not a match ("Parts" is not "PARTSWORLD")', async () => {
    const { BP, elements } = load({ vendors: VENDORS, rowVendor: '' });
    BP._reviewLines = { 7: { id: 7, amount: '-90.00', payee: 'PARTSWORLD 12' } };
    await BP.showMatch(7, 2);
    assert.doesNotMatch(elements['feed-pay-panel'].innerHTML, / selected>/);
});

test('the vendor typed on the row still wins, with no guessing fetch', async () => {
    const { BP, calls, elements } = load({ vendors: VENDORS, rowVendor: 'Parts' });
    BP._reviewLines = { 7: CHARGE };
    await BP.showMatch(7, 2);
    assert.match(elements['feed-pay-panel'].innerHTML, /<option value="4" selected>Parts</);
    assert.ok(!calls.pages.includes('/bills?open_only=true'));
});

test('with candidates the panel stays closed behind its button', async () => {
    const cands = [{ line_id: 50, date: '2026-10-01', payee: 'Check 101', days_off: 0, source_type: 'bank_entry' }];
    const { BP, calls, elements } = load({ candidates: cands, vendors: VENDORS });
    BP._reviewLines = { 7: CHARGE };
    await BP.showMatch(7, 2);
    assert.equal(elements['feed-pay-panel'].hidden, true);
    assert.doesNotMatch(calls.modal[0][1], /Nothing in the books/);
    assert.equal(calls.pages.length, 0);
});

test('a money-in line opens Receive payment with the one customer owed that amount', async () => {
    const invoices = [
        { id: 31, customer_id: 9, bill_to_customer_id: 8, balance_due: '175.00', status: 'sent', date: '2026-09-01', invoice_number: '1001' },
        { id: 32, customer_id: 10, balance_due: '60.00', status: 'sent', date: '2026-09-02', invoice_number: '1002' },
    ];
    const customers = [{ id: 8, name: 'Corner Deli' }, { id: 9, name: 'Corner Deli Annex' }, { id: 10, name: 'Hill Cafe' }];
    const { BP, calls, elements } = load({ invoices, customers });
    BP._reviewLines = { 7: { id: 7, amount: '175.00', payee: 'DEPOSIT' } };
    await BP.showMatch(7, 2);
    assert.match(calls.modal[0][1], /If it is a customer's payment, pick or check the customer under <strong>Receive payment<\/strong> above\./);
    assert.equal(elements['feed-pay-panel'].hidden, false);
    // billed to the parent: the payment comes from the parent
    assert.match(elements['feed-pay-panel'].innerHTML, /<option value="8" selected>Corner Deli</);
    assert.ok(calls.pages.includes('/invoices?open_only=true'));
});

test('a range change keeps the focus on the range, not the vendor box', async () => {
    const { BP, elements } = load({ vendors: VENDORS, rowVendor: '' });
    BP._reviewLines = { 7: CHARGE };
    await BP.showMatch(7, 2, { refocus: true });
    assert.equal(elements['match-range'].focused, true);
    assert.equal((elements['feed-pay-party'] || { focused: false }).focused, false);
});

test('Pay bills... on a panel already open keeps the vendor picked, no re-render', async () => {
    const { BP, calls, elements } = load({ vendors: VENDORS, rowVendor: '' });
    BP._reviewLines = { 7: CHARGE };
    await BP.showMatch(7, 2);
    const before = elements['feed-pay-panel'].innerHTML;
    const fetched = calls.pages.length;
    await BP.openFeedPay(7, 2);
    assert.equal(elements['feed-pay-panel'].innerHTML, before);
    assert.equal(calls.pages.length, fetched);
    assert.equal(elements['feed-pay-party'].focused, true);
});

test('the vendor the bank names beats another vendor with a bill of that amount', async () => {
    // Parts Depot is named; Egg Farm has the only open 90.00 bill
    const bills = [
        { id: 26, vendor_id: 3, balance_due: '40.00', status: 'unpaid', date: '2026-10-01' },
        { id: 27, vendor_id: 5, balance_due: '90.00', status: 'unpaid', date: '2026-10-01' },
    ];
    const { BP, calls, elements } = load({ bills, vendors: VENDORS, rowVendor: '' });
    BP._reviewLines = { 7: CHARGE };
    await BP.showMatch(7, 2);
    assert.match(elements['feed-pay-panel'].innerHTML, /<option value="3" selected>Parts Depot/);
    assert.doesNotMatch(elements['feed-pay-panel'].innerHTML, /value="5" selected/);
    assert.ok(!calls.pages.includes('/bills?open_only=true'), 'a name match needs no fetch of every open bill');
    assert.ok(calls.pages.includes('/bills?vendor_id=3&open_only=true'));
});

test('one open bill due exactly the line amount is the one filled, not the oldest', async () => {
    const bills = [
        { id: 41, vendor_id: 3, balance_due: '40.00', status: 'unpaid', date: '2026-09-01' },
        { id: 42, vendor_id: 3, balance_due: '90.00', status: 'unpaid', date: '2026-10-01' },
    ];
    const { BP, elements } = load({ bills, vendors: VENDORS, rowVendor: '' });
    BP._reviewLines = { 7: CHARGE };
    await BP.showMatch(7, 2);
    const box = elements['feed-pay-docs'].innerHTML;
    assert.match(box, /data-doc="42"[^>]*value="90.00"/);
    assert.match(box, /data-doc="41"[^>]*value=""/);
});

test('two open bills due the line amount: filled oldest first as before', async () => {
    const bills = [
        { id: 43, vendor_id: 3, balance_due: '90.00', status: 'unpaid', date: '2026-09-01' },
        { id: 44, vendor_id: 3, balance_due: '90.00', status: 'unpaid', date: '2026-10-01' },
        { id: 45, vendor_id: 3, balance_due: '30.00', status: 'unpaid', date: '2026-08-01' },
    ];
    const { BP, elements } = load({ bills, vendors: VENDORS, rowVendor: '' });
    BP._reviewLines = { 7: CHARGE };
    await BP.showMatch(7, 2);
    const box = elements['feed-pay-docs'].innerHTML;
    assert.match(box, /data-doc="45"[^>]*value="30.00"/);
    assert.match(box, /data-doc="43"[^>]*value="60.00"/);
    assert.match(box, /data-doc="44"[^>]*value=""/);
});
