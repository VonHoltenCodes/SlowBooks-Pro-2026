// The transaction warnings' date question (#481) on an edit (#813): an
// edited old document asked about its date even when the date was not
// changed. The date it was saved with was asked about when it was saved; an
// edit asks only when the date is changed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load() {
    const asked = [];
    const ctx = {
        console, window: {}, document: { addEventListener() {} },
        App: { settings: { warn_date_past: 'true', warn_date_past_days: '90' } },
        formatDate: s => s,
        confirm: (m) => { asked.push(m); return true; },
    };
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/utils.js'), 'utf8') + '\nthis.TxnWarn = TxnWarn;', ctx);
    return { TxnWarn: ctx.TxnWarn, asked };
}

const form = (value, defaultValue) => ({ date: { value, defaultValue, dataset: {} } });

test('an edit with its saved date unchanged is not asked about', async () => {
    const { TxnWarn, asked } = load();
    assert.equal(await TxnWarn.ok(form('2020-03-01', '2020-03-01'), { editing: true }), true);
    assert.equal(asked.length, 0);
    // opts.saved names the saved date when the form has another way
    assert.equal(await TxnWarn.ok(null, { date: '2020-03-01', saved: '2020-03-01' }), true);
    assert.equal(asked.length, 0);
});

test('an edit whose date is changed, and a new form, still ask', async () => {
    const { TxnWarn, asked } = load();
    await TxnWarn.ok(form('2020-02-01', '2020-03-01'), { editing: true });
    assert.equal(asked.length, 1);
    assert.match(asked[0], /^The date Feb 1, 2020 is \d+ days in the past/);
    // a new form's drawn date is not a saved one
    await TxnWarn.ok(form('2020-03-01', '2020-03-01'));
    assert.equal(asked.length, 2);
});
