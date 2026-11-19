// Find on this screen (find.js), the matching half, which touches no DOM:
// case and accents ignored unless asked, a run of spaces one space, a
// typographic quote or dash its plain form, and an amount found however
// it is written ($, thousands commas, accounting parentheses). The bar
// itself, on a real page and over a dialog, is tests/test_browser_find.py.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function load() {
    const context = { window: {}, navigator: { platform: 'MacIntel' } };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/find.js'), 'utf8'), context);
    return context.window.FindOnScreen;
}

const { fold, isNumericQuery, findInChunks } = load();

// The matched text of each hit, read back out of the original (copied
// into this realm: an array made in the page's context is not deepEqual
// to one made here)
function found(texts, query, opts) {
    const chunks = texts.map(text => ({ text }));
    return Array.from(findInChunks(chunks, query, opts), h => h.chunk.text.slice(h.start, h.end));
}

test('case and accents are ignored, and "Aa" matches case', () => {
    assert.deepEqual(found(['Acme Supply', 'ACME', 'acme'], 'acme'), ['Acme', 'ACME', 'acme']);
    assert.deepEqual(found(['Café Olé', 'Cafe'], 'cafe'), ['Café', 'Cafe']);
    assert.deepEqual(found(['Acme', 'ACME', 'acme'], 'Acme', { caseSensitive: true }), ['Acme']);
    // accents are still ignored with case matched
    assert.deepEqual(found(['Café', 'café'], 'Cafe', { caseSensitive: true }), ['Café']);
});

test('a run of spaces, a tab or a line break is one space', () => {
    assert.deepEqual(found(['Acme   Supply', 'Acme\nSupply', 'Acme Supply'], 'acme supply'),
        ['Acme   Supply', 'Acme\nSupply', 'Acme Supply']);
    assert.deepEqual(found(['Acme  Supply'], '  acme   supply '), ['Acme  Supply']);
});

test('typographic quotes and dashes read as the plain ones', () => {
    assert.deepEqual(found(['O’Brien'], "o'brien"), ['O’Brien']);
    assert.deepEqual(found(['2026–2027'], '2026-2027'), ['2026–2027']);
});

test('a query that reads as an amount', () => {
    for (const q of ['1234', '1,234.50', '$1,234.50', '-200', '(200.00)', '.5', '$ 12', '−200', '1234.']) {
        assert.equal(isNumericQuery(q), true, q);
    }
    for (const q of ['', 'acme', 'INV-1001', '2026-11-19', '12a', '1.2.3', '$']) {
        assert.equal(isNumericQuery(q), false, q);
    }
});

test('an amount ignores $, thousands commas and spaces', () => {
    // the match is what was typed: $ is skipped, a shorter query is a
    // shorter match, as a browser's find
    assert.deepEqual(found(['Total $1,234.50'], '1234.5'), ['1,234.5']);
    assert.deepEqual(found(['Total $1,234.50'], '1234.50'), ['1,234.50']);
    assert.deepEqual(found(['Total $1,234.50'], '$1,234.50'), ['1,234.50']);
    assert.deepEqual(found(['$ 12,000'], '$12,000'), ['12,000']);
    // a comma that is not a thousands comma stays
    assert.deepEqual(found(['1, 2, 3'], '123'), []);
    // the number in an invoice number is found as typed
    assert.deepEqual(found(['Invoice 1001'], '1001'), ['1001']);
});

test('accounting parentheses are a minus', () => {
    assert.deepEqual(found(['Net (200.00)'], '-200'), ['(200']);
    assert.deepEqual(found(['Net (200.00)'], '(200.00)'), ['(200.00']);
    assert.deepEqual(found(['Net ($1,200.00)'], '-1200'), ['($1,200']);
    assert.deepEqual(found(['Net -200.00'], '(200)'), ['-200']);
    assert.deepEqual(found(['Net (200.00)'], '200.00'), ['200.00']);
    // parentheses around words are left alone
    assert.deepEqual(found(['(see note)'], '-'), []);
});

test('matches do not overlap and come in order across chunks', () => {
    assert.deepEqual(found(['aaaa'], 'aa'), ['aa', 'aa']);
    const chunks = [{ text: 'one x' }, { text: 'x two x' }];
    const hits = findInChunks(chunks, 'x');
    assert.deepEqual(Array.from(hits, h => [chunks.indexOf(h.chunk), h.start]), [[0, 4], [1, 0], [1, 6]]);
});

test('an empty or blank query finds nothing', () => {
    assert.deepEqual(found(['anything'], ''), []);
    assert.deepEqual(found(['anything'], '   '), []);
});

test('the count stops at the limit', () => {
    const hits = findInChunks([{ text: 'x'.repeat(50) }], 'x', { limit: 11 });
    assert.equal(hits.length, 11);
});

test('fold maps every folded character back to its source', () => {
    const f = fold('Cáfé  X', {});
    assert.equal(f.text, 'cafe x');
    assert.equal(f.starts.length, f.text.length);
    // "é" written as e + combining accent folds to "e" from both code units
    assert.equal(f.ends[f.text.indexOf('x')], 'Cáfé  X'.length);
    // a character outside the BMP keeps its two code units together
    const g = fold('a\u{1F600}b', {});
    assert.equal(g.text, 'a\u{1F600}b');
    assert.deepEqual([g.starts[1], g.ends[1], g.starts[2], g.ends[2]], [1, 3, 1, 3]);
});
