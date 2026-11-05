// Reports' All dates period (#707): in the period list of the reports that
// take a range, not of an as-of one (the Balance Sheet) or the Classes
// page, from the books' first transaction to today, and with no
// Previous / Next of its own.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load(firstDate) {
    const gets = [];
    const context = {
        console, JSON, URLSearchParams, Date,
        window: {},
        document: { querySelector: () => null, querySelectorAll: () => [] },
        escapeHtml: (s) => String(s ?? ''),
        T: (s) => s,
        Terms: { text: (s) => s, isNonprofit: () => false, isConstruction: () => false },
        App: { settings: { fiscal_year_start_month: '1' } },
        API: { get: async (url) => { gets.push(url); return { first_date: firstDate }; } },
    };
    vm.createContext(context);
    const src = fs.readFileSync(path.join(__dirname, '../../app/static/js/reports.js'), 'utf8');
    vm.runInContext(src + '\nthis.ReportsPage = ReportsPage;', context);
    return { R: context.ReportsPage, gets };
}

const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

test('All dates is in the period list first, and out of an as-of one', () => {
    const { R } = load(null);
    const html = R.periodOptions('all_dates');
    assert.match(html, /^<option value="all_dates" selected>All dates<\/option>/);
    assert.doesNotMatch(R.periodOptions('', { allDates: false }), /all_dates/);
});

test('All dates runs from the first transaction to today', async () => {
    const { R, gets } = load('2020-03-14');
    assert.equal(await R.loadFirstDate(), '2020-03-14');
    assert.deepEqual([...gets], ['/reports/first-date']);
    assert.deepEqual({ ...R.getDateRange('all_dates') }, { start: '2020-03-14', end: iso(new Date()) });
    assert.equal(R._PERIOD_MONTHS.all_dates, undefined);
});

test('With no books yet, All dates is the fiscal year to date', async () => {
    const { R } = load(null);
    await R.loadFirstDate();
    const today = new Date();
    assert.deepEqual({ ...R.getDateRange('all_dates') }, {
        start: `${today.getFullYear()}-01-01`, end: iso(today),
    });
});
