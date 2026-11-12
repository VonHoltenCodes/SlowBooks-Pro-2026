// The Journal Entries list pages (#817): the count line says which entries
// are shown of how many, Previous / Next walk the filter a page at a time,
// and the page asked for rides along with the Show, Sort and range.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function entries(from, count) {
    return Array.from({ length: count }, (_, i) => ({
        id: from + i, date: '2026-03-01', description: 'Entry ' + (from + i),
        total_debit: 5, total_credit: 5, lines: [],
    }));
}

function load(total) {
    const asked = [];
    const nodes = {};
    const context = {
        $: () => null,
        localStorage: { getItem: () => null, setItem: () => {} },
        document: {
            getElementById: id => nodes[id] || null,
            activeElement: null,
        },
        API: {
            get: async url => {
                asked.push(url);
                const skip = Number((url.match(/skip=(\d+)/) || [])[1] || 0);
                return {
                    entries: entries(skip + 1, Math.max(0, Math.min(100, total - skip))),
                    total, skip, limit: 100, default_range: true,
                    start_date: '2026-01-01', end_date: '2026-12-31',
                };
            },
        },
        escapeHtml: value => String(value ?? ''),
        formatDate: value => value,
        formatCurrency: value => '$' + value,
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/journal.js'), 'utf8') + '\nthis.JournalPage = JournalPage;', context);
    for (const id of ['journal-count', 'journal-range-note', 'journal-list']) {
        nodes[id] = { textContent: '', innerHTML: '', setAttribute() {}, focus() {}, contains: () => false };
    }
    return { page: context.JournalPage, asked, nodes };
}

test('the count line says which entries of how many', async () => {
    const { page } = load(3001);
    const html = await page.render();
    assert.match(html, /Entries 1–100 of 3,001 this fiscal year/);
    assert.match(html, /This fiscal year, 2026-01-01 to 2026-12-31\. Set From or To to see entries from other dates\./);
    assert.match(html, /id="journal-prev"[^>]*disabled/);
    assert.doesNotMatch(html, /id="journal-next"[^>]*disabled/);
});

test('one page that holds every entry has no Previous or Next', async () => {
    const { page } = load(3);
    const html = await page.render();
    assert.match(html, /3 entries this fiscal year/);
    assert.doesNotMatch(html, /journal-pager/);
});

test('Next and Previous ask for the next page with the sort and range kept', async () => {
    const { page, asked, nodes } = load(250);
    page._from = '2026-01-01';
    page.sortChoice = () => 'entered';
    await page.render();
    await page.turn(1);
    await page.turn(1);
    assert.equal(asked.at(-1), '/journal?start_date=2026-01-01&sort=entered&skip=200');
    assert.equal(nodes['journal-count'].textContent, 'Entries 201–250 of 250 this fiscal year');
    assert.match(nodes['journal-list'].innerHTML, /id="journal-next"[^>]*disabled/);
    await page.turn(1);  // past the last page: nothing asked
    assert.equal(asked.at(-1), '/journal?start_date=2026-01-01&sort=entered&skip=200');
    await page.turn(-1);
    assert.equal(asked.at(-1), '/journal?start_date=2026-01-01&sort=entered&skip=100');
});

test('a new range starts again from the first page', async () => {
    const { page, asked, nodes } = load(250);
    await page.render();
    await page.turn(1);
    nodes['journal-from'] = { value: '2026-02-01' };
    nodes['journal-to'] = { value: '' };
    await page.setRange();
    assert.equal(asked.at(-1), '/journal?start_date=2026-02-01');
});
