// The Payout… modal prefills the gross with the deposit's net, never the
// clearing balance (#586 review): clearing already holds the net of every
// capture not yet paid out, so prefilling the balance booked the rest as fees.
// The balance is shown as information only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function openPayout(amount, balance) {
    let html = '';
    const context = {
        window: {},
        document: {},
        console,
        escapeHtml: (s) => String(s),
        formatCurrency: (n) => '$' + Number(n).toFixed(2),
        openModal: (_title, body) => { html = body; },
    };
    vm.createContext(context);
    const js = fs.readFileSync(
        path.join(__dirname, '../../app/static/js/banking.js'),
        'utf8'
    );
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/hint.js'), 'utf8'), context);  // hint(), hintList() (#977)
    vm.runInContext(js + '\nthis.BankingPage = BankingPage;', context);
    const BP = context.BankingPage;
    BP._reviewLines = {
        7: {
            amount,
            suggested_payout: {
                provider: 'stripe', display_name: 'Stripe', clearing_balance: balance,
            },
        },
    };
    BP.showPayout(7, 1);
    return html;
}

test('the gross prefills to the net even when clearing holds more', () => {
    const html = openPayout('96.80', '300.00');
    assert.match(html, /id="payout-gross"[^>]*value="96\.80"/);
    assert.match(html, /id="payout-fee"[^>]*value="0\.00"/);
    // the balance is information only
    assert.match(html, /\$300\.00/);
    assert.doesNotMatch(html, /id="payout-gross"[^>]*value="300\.00"/);
});
