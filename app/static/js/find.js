/**
 * Find on this screen: the desktop window's find bar.
 *
 * The desktop window (pywebview) has no find bar of its own, so before
 * this Ctrl+F (⌘F on a Mac) went to the record search (#695), and over a
 * dialog it did nothing at all. Now Ctrl+F opens this bar there; the
 * record search keeps Ctrl+K (⌘K) and /. In a browser Ctrl+F stays the
 * browser's own find in page and this file is never used.
 *
 * What it finds: the words drawn on the screen in front of the reader.
 * With no dialog open, the whole window: the top bar, the sidebar, the
 * page, the status bar, and whatever is open over them (a menu, the
 * record search's list, a type-ahead's list, a notice). With the app's
 * dialog open (#modal), the dialog alone, the window beneath it being
 * covered. The app's small questions (the closed period's password,
 * askChoice: aria-modal boxes of their own over everything) take Escape
 * and Tab for themselves, so the bar does not open over one, and one that
 * comes up while it is open is not searched. Nothing that is not drawn:
 * a hidden element, a folded section, a screen reader's own words
 * (.visually-hidden). The value in a text box, a text area or a
 * drop-down counts too: on a bill or an invoice most of what is on screen
 * is in a field.
 *
 * How it matches: case and accents are always ignored (a book's names,
 * numbers and memos never differ by case alone, so there is no "match
 * case" to turn on), and a run of spaces or a line break is one space. A
 * query that reads as an amount ignores $, the thousands commas and
 * accounting parentheses on both sides, so 1234.5 finds $1,234.50 and -200
 * finds (200.00).
 *
 * How it shows: each match gets a tinted box laid over its text in a
 * layer of its own (#find-marks), the current one in a stronger colour, and
 * the page itself is never changed. Wrapping matches in <mark> would break
 * the pages' inline handlers, a field's caret and the redraws below. The
 * CSS Custom Highlight API was the first answer and is not used: the macOS
 * window's WKWebView takes the highlights, reports them registered and
 * styled, and never paints them (the desktop's own test, "1 of 1" and
 * nothing to see). The boxes follow a scroll and a resize, and are clipped
 * to the box that scrolls their text. A field that matches is outlined
 * (a box's value is not text a highlight can reach).
 *
 * The screen changes under it: a page redraws, a report refreshes itself,
 * a dialog opens or closes, a field is typed in. The bar watches its scope
 * and searches again a moment later, holding its place at the nearest
 * match, without scrolling. It stays open across a page change.
 *
 * The matching half (fold, isNumericQuery, findInChunks) touches no DOM,
 * for tests/js/find_on_screen.test.cjs.
 */
(function () {
    'use strict';

    const MAX_MATCHES = 1000;

    // ── Matching ──────────────────────────────────────────────────────────

    const WHITESPACE = /\s/;
    const DIGIT = /[0-9]/;
    // Typographic forms read as the plain ones: a pasted "O'Brien" finds
    // "O’Brien", and the minus sign U+2212 or a dash is a hyphen
    const PLAIN = {
        '‘': "'", '’': "'", '‚': "'", '‛': "'",
        '“': '"', '”': '"', '„': '"', '‟': '"',
        '‐': '-', '‑': '-', '‒': '-', '–': '-', '—': '-', '−': '-',
    };

    function foldChar(ch) {
        if (PLAIN[ch]) return PLAIN[ch];
        return ch.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
    }

    // A query that reads as an amount: an optional sign or parentheses, an
    // optional $, digits with thousands commas, an optional decimal part
    function isNumericQuery(q) {
        const s = String(q || '').replace(/\s+/g, '');
        return /^\(?[-−]?\$?[-−]?(\d[\d,]*(\.\d*)?|\.\d+)\)?$/.test(s);
    }

    // The text as it is compared: folded, with where each folded code unit
    // came from (starts[i], ends[i]: its source's offsets in `text`), so a
    // match in the folded text is a match in the original.
    function fold(text, opts) {
        const numeric = !!(opts && opts.numeric);
        let out = '';
        const starts = [];
        const ends = [];
        const last = () => out[out.length - 1];
        const emit = (s, from, to) => {
            for (let k = 0; k < s.length; k++) { out += s[k]; starts.push(from); ends.push(to); }
        };
        for (let i = 0; i < text.length;) {
            const ch = String.fromCodePoint(text.codePointAt(i));
            const next = i + ch.length;
            if (WHITESPACE.test(ch)) {
                // one space for a run; in an amount, none after a sign
                // ("- $200", "( 200 )")
                if (out.length && last() !== ' ' && !(numeric && last() === '-')) emit(' ', i, next);
                i = next;
                continue;
            }
            if (numeric) {
                if (ch === '$') { i = next; continue; }
                if (ch === ',' && DIGIT.test(last() || '') && DIGIT.test(text[next] || '')) { i = next; continue; }
                if (ch === '(' && /^\s*\$?\s*[0-9.]/.test(text.slice(next, next + 8))) { emit('-', i, next); i = next; continue; }
                if (ch === ')') {
                    const spaced = last() === ' ';
                    if (DIGIT.test((spaced ? out[out.length - 2] : last()) || '')) {
                        if (spaced) { out = out.slice(0, -1); starts.pop(); ends.pop(); }
                        i = next;
                        continue;
                    }
                }
            }
            emit(foldChar(ch), i, next);
            i = next;
        }
        return { text: out, starts, ends };
    }

    // Every match of `query` in the chunks, in order, as offsets in each
    // chunk's own text: [{ chunk, start, end }]. At most `limit` (the
    // caller asks for one more than it shows, to know there are more).
    function findInChunks(chunks, query, opts) {
        const q = String(query || '').trim();
        if (!q) return [];
        const o = { numeric: opts && 'numeric' in opts ? !!opts.numeric : isNumericQuery(q) };
        const needle = fold(q, o).text;
        if (!needle.trim()) return [];
        const limit = (opts && opts.limit) || Infinity;
        const found = [];
        for (const chunk of chunks) {
            const hay = fold(chunk.text, o);
            let at = hay.text.indexOf(needle);
            while (at !== -1) {
                found.push({ chunk, start: hay.starts[at], end: hay.ends[at + needle.length - 1] });
                if (found.length >= limit) return found;
                at = hay.text.indexOf(needle, at + needle.length);
            }
        }
        return found;
    }

    // ── What is on the screen ─────────────────────────────────────────────

    // Elements a word runs on through; any other element ends one, so
    // "Acme" in one cell and "Supply" in the next are not "AcmeSupply"
    const INLINE = new Set(['a', 'abbr', 'b', 'bdi', 'bdo', 'cite', 'code', 'data', 'dfn', 'em', 'font', 'i',
        'kbd', 'label', 'mark', 'q', 's', 'samp', 'small', 'span', 'strong', 'sub', 'sup', 'time', 'u', 'var', 'wbr']);
    const SKIP = new Set(['script', 'style', 'template', 'noscript', 'svg', 'canvas', 'datalist', 'iframe', 'object']);
    // A box whose value is drawn as text; a date's value is not what it
    // shows (2026-11-19 for 11/19/2026), a password is never read
    const TEXT_INPUTS = new Set(['text', 'search', 'email', 'tel', 'url', 'number', 'button', 'submit', 'reset']);

    function shown(el) {
        if (SKIP.has(el.localName) || el.hidden) return false;
        if (el.matches('.visually-hidden, .skip-link, .skip-link--inline, #find-bar, #find-marks')) return false;
        if (typeof el.checkVisibility === 'function') {
            if (el.checkVisibility({ visibilityProperty: true })) return true;
            // display: contents draws no box of its own; its children do
            return getComputedStyle(el).display === 'contents';
        }
        const cs = getComputedStyle(el);
        return cs.display === 'contents' || (el.getClientRects().length > 0 && cs.visibility !== 'hidden');
    }

    function fieldText(el) {
        const tag = el.localName;
        if (tag === 'textarea') return el.value;
        if (tag === 'select') return [...el.selectedOptions].map(o => o.text).join(', ');
        if (tag === 'input' && TEXT_INPUTS.has(el.type)) return el.value;
        return null;
    }

    // The text under `root` in reading order, as chunks: a run of text
    // nodes inside one block ({ text, pieces: [{ node, start }] }), or a
    // field's value ({ text, field })
    function collectChunks(root) {
        const chunks = [];
        // another dialog inside the root (a small question over the window)
        // is not what is being searched
        const elsewhere = (el) => el !== root && el.getAttribute('aria-modal') === 'true';
        let run = null;
        const flush = () => {
            if (run && run.text.trim()) chunks.push(run);
            run = null;
        };
        const visit = (el) => {
            // a folded <details> draws its <summary> alone; the text beside
            // it is a node no visibility check can be asked about
            const folded = el.localName === 'details' && !el.open;
            for (let n = el.firstChild; n; n = n.nextSibling) {
                if (folded && n.localName !== 'summary') continue;
                if (n.nodeType === 3) {
                    if (!run) run = { text: '', pieces: [] };
                    run.pieces.push({ node: n, start: run.text.length });
                    run.text += n.data;
                } else if (n.nodeType === 1 && n.localName === 'br') {
                    // a line break is a space in the run, not its end ("Total
                    // due" over two lines is one phrase); the space has no
                    // node of its own, and a match never starts or ends on it
                    if (run) run.text += ' ';
                } else if (n.nodeType === 1 && shown(n) && !elsewhere(n)) {
                    const tag = n.localName;
                    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
                        const text = fieldText(n);
                        if (text && text.trim()) { flush(); chunks.push({ text, field: n }); }
                        continue;
                    }
                    const inline = INLINE.has(tag);
                    if (!inline) flush();
                    visit(n);
                    if (!inline) flush();
                }
            }
        };
        if (root) visit(root);
        flush();
        return chunks;
    }

    // An offset in a run's text, as a DOM point: a start lands in the node
    // whose text begins there, an end in the node whose text it closes
    function pointAt(run, offset, isEnd) {
        for (const p of run.pieces) {
            const len = p.node.data.length;
            const inside = isEnd ? offset > p.start && offset <= p.start + len : offset >= p.start && offset < p.start + len;
            if (inside) return { node: p.node, offset: offset - p.start };
        }
        const tail = run.pieces[run.pieces.length - 1];
        return { node: tail.node, offset: tail.node.data.length };
    }

    // A match: its range, and one range per text node it covers (the boxes
    // are drawn from those, so an element the range crosses is never boxed
    // whole, as a range's own getClientRects would)
    function toMatch(hit) {
        if (hit.chunk.field) return { field: hit.chunk.field };
        const a = pointAt(hit.chunk, hit.start, false);
        const b = pointAt(hit.chunk, hit.end, true);
        const range = document.createRange();
        range.setStart(a.node, a.offset);
        range.setEnd(b.node, b.offset);
        const parts = [];
        for (const p of hit.chunk.pieces) {
            const from = Math.max(hit.start, p.start);
            const to = Math.min(hit.end, p.start + p.node.data.length);
            if (to <= from) continue;
            const r = document.createRange();
            r.setStart(p.node, from - p.start);
            r.setEnd(p.node, to - p.start);
            parts.push(r);
        }
        return { range, parts };
    }

    function anchorOf(m) {
        return m.field ? { node: m.field, offset: 0 } : { node: m.range.startContainer, offset: m.range.startOffset };
    }

    // Whether the match starts before the point
    function isBefore(m, point) {
        const a = anchorOf(m);
        if (a.node === point.node) return a.offset < point.offset;
        return !!(a.node.compareDocumentPosition(point.node) & Node.DOCUMENT_POSITION_FOLLOWING);
    }

    // ── The bar ───────────────────────────────────────────────────────────

    const isMac = () => /Mac|iP(hone|ad|od)/.test((typeof navigator !== 'undefined' && navigator.platform) || '');
    // The app's dialog when it is open, else null
    const topDialog = () => {
        const modal = document.getElementById('modal');
        return modal && shown(modal) ? modal : null;
    };
    // One of the app's small questions is up (api.js's closing-date
    // password, utils.js askChoice): an aria-modal box of its own
    const questionUp = () => [...document.querySelectorAll('[aria-modal="true"]')]
        .some(el => el.id !== 'modal' && shown(el));

    const FindBar = {
        MAX_MATCHES,
        _bar: null,
        _input: null,
        _open: false,
        _matches: [],
        _index: -1,
        _over: false,
        _returnTo: null,
        _typing: null,
        _refresh: null,
        _observer: null,
        _onEdit: null,
        _onMove: null,
        _layer: null,
        _frame: 0,

        // Ctrl+F (⌘F): either modifier, as Ctrl+K and ⌘K both search, and
        // never with Alt (Ctrl+Alt is AltGr, typing a character)
        isFindKey(e) {
            return (e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'f' || e.key === 'F');
        },
        // Next and previous: Ctrl+G (⌘G) or F3, with Shift for previous
        stepKey(e) {
            const g = (e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'g' || e.key === 'G');
            if (!g && e.key !== 'F3') return 0;
            return e.shiftKey ? -1 : 1;
        },

        isOpen() { return FindBar._open; },

        // The open dialog, else the whole window
        scope() {
            return topDialog() || document.body;
        },

        // The bar's own elements and its boxes: their changes are not the
        // screen changing
        _own(node) {
            const el = node && (node.nodeType === 1 ? node : node.parentElement);
            return !!el && (FindBar._bar.contains(el) || (!!FindBar._layer && FindBar._layer.contains(el)));
        },

        _build() {
            if (FindBar._bar) return;
            const mod = isMac() ? '⌘' : 'Ctrl+';
            const bar = document.createElement('div');
            bar.id = 'find-bar';
            bar.className = 'find-bar';
            bar.setAttribute('role', 'search');
            bar.setAttribute('aria-label', 'Find on this screen');
            bar.hidden = true;
            bar.innerHTML = `
                <div class="find-row">
                    <input type="text" id="find-input" class="find-input" placeholder="Find on this screen"
                        aria-label="Find on this screen" aria-describedby="find-count" autocomplete="off" spellcheck="false"
                        aria-keyshortcuts="${isMac() ? 'Meta' : 'Control'}+F">
                    <span id="find-count" class="find-count" role="status" aria-live="polite"></span>
                    <button type="button" class="find-btn" data-find="prev" aria-label="Previous match" title="Previous match (Shift+Enter)">&#x2303;</button>
                    <button type="button" class="find-btn" data-find="next" aria-label="Next match" title="Next match (Enter)">&#x2304;</button>
                    <button type="button" class="find-btn" data-find="close" aria-label="Close find" title="Close (Esc)">&#x2715;</button>
                </div>
                <div class="find-empty" hidden>
                    <span>No matches on this screen.</span>
                    <button type="button" class="find-records" data-find="records">Search all records (${mod}K)</button>
                </div>`;
            document.body.appendChild(bar);
            FindBar._bar = bar;
            FindBar._input = bar.querySelector('#find-input');
            FindBar._input.addEventListener('input', () => {
                clearTimeout(FindBar._typing);
                FindBar._typing = setTimeout(() => { FindBar._typing = null; FindBar.search({ reveal: true }); }, 120);
            });
            bar.addEventListener('keydown', FindBar._key);
            bar.addEventListener('click', (e) => {
                const b = e.target.closest('[data-find]');
                if (!b) return;
                const act = b.dataset.find;
                if (act === 'close') return FindBar.close();
                if (act === 'records') return FindBar.searchRecords();
                FindBar.step(act === 'prev' ? -1 : 1);
                FindBar._input.focus();
            });
        },

        // Keys inside the bar. They stop here: Escape closes the bar and not
        // the dialog under it (modalKeydown, app.js), and Tab stays among the
        // bar's controls rather than the dialog's trap taking it.
        _key(e) {
            const stop = () => { e.preventDefault(); e.stopPropagation(); };
            if (e.key === 'Escape') { stop(); FindBar.close(); return; }
            if (e.key === 'Enter' && e.target === FindBar._input) { stop(); FindBar.enter(e.shiftKey ? -1 : 1); return; }
            if (FindBar.isFindKey(e)) { stop(); FindBar._input.focus(); FindBar._input.select(); return; }
            const step = FindBar.stepKey(e);
            if (step) { stop(); FindBar.enter(step); return; }
            if (e.key === 'Tab') {
                stop();
                const stops = [...FindBar._bar.querySelectorAll('input, button')]
                    .filter(el => !el.disabled && el.getClientRects().length > 0);
                const i = stops.indexOf(document.activeElement);
                const next = stops[(i + (e.shiftKey ? -1 : 1) + stops.length) % stops.length];
                if (next) next.focus();
            }
        },

        // Open (or, open already, take focus): the selected text is the
        // query when there is one, else the last query, selected
        open() {
            // a small question has Escape and Tab, and two lines to read
            if (questionUp()) return;
            FindBar._build();
            const active = document.activeElement;
            if (active && !FindBar._bar.contains(active)) FindBar._returnTo = active;
            const picked = FindBar._selectedText();
            if (picked) FindBar._input.value = picked;
            if (!FindBar._open) {
                FindBar._open = true;
                FindBar._bar.hidden = false;
                FindBar._watch();
            }
            FindBar._input.focus();
            FindBar._input.select();
            FindBar.search({ reveal: true });
        },

        close() {
            if (!FindBar._open) return;
            FindBar._open = false;
            clearTimeout(FindBar._typing);
            clearTimeout(FindBar._refresh);
            FindBar._typing = null;
            FindBar._unwatch();
            FindBar._matches = [];
            FindBar._index = -1;
            FindBar._paint();
            FindBar._bar.hidden = true;
            const back = FindBar._returnTo;
            FindBar._returnTo = null;
            if (back && back !== document.body && document.contains(back) && !back.closest('.hidden')) back.focus();
            else {
                const d = topDialog();
                if (d && typeof d.focus === 'function') d.focus();
            }
        },

        // The query, handed to the record search (the empty state's link)
        searchRecords() {
            const q = FindBar._input.value.trim();
            FindBar._returnTo = null;
            FindBar.close();
            const search = document.getElementById('global-search');
            if (!search || topDialog()) return;
            search.value = q;
            search.focus();
            if (window.App && typeof App.globalSearch === 'function') App.globalSearch(q);
        },

        // Enter, Ctrl+G, F3: a query still being typed is searched first,
        // landing on its first match; otherwise on to the next
        enter(dir) {
            if (FindBar._typing) {
                clearTimeout(FindBar._typing);
                FindBar._typing = null;
                FindBar.search({ reveal: true });
                if (dir < 0) FindBar.step(-1);
                return;
            }
            FindBar.step(dir);
        },

        step(dir) {
            const n = FindBar._matches.length;
            if (!n) return;
            FindBar._index = (FindBar._index + dir + n) % n;
            // scrolled to first, so the boxes are drawn where the text is
            FindBar._reveal(FindBar._matches[FindBar._index]);
            FindBar._paint();
            FindBar._status();
        },

        // Search the scope again. opts.reveal: a new query, so land on the
        // first match in view (else the first) and scroll to it.
        // opts.keepPlace: the screen changed, so stay at the nearest match
        // and scroll nowhere.
        search(opts) {
            if (!FindBar._bar) return;
            const o = opts || {};
            const prev = FindBar._matches[FindBar._index];
            const anchor = prev && anchorOf(prev);
            const oldIndex = FindBar._index;
            const q = FindBar._input.value.trim();
            let hits = [];
            if (q && FindBar._open) {
                hits = findInChunks(collectChunks(FindBar.scope()), q, { limit: MAX_MATCHES + 1 });
            }
            FindBar._over = hits.length > MAX_MATCHES;
            FindBar._matches = hits.slice(0, MAX_MATCHES).map(toMatch);
            const n = FindBar._matches.length;
            let index = n ? 0 : -1;
            if (n && o.keepPlace && anchor) {
                if (document.contains(anchor.node)) {
                    const at = FindBar._matches.findIndex(m => !isBefore(m, anchor));
                    index = at === -1 ? n - 1 : at;
                } else {
                    index = Math.min(Math.max(oldIndex, 0), n - 1);
                }
            } else if (n && o.reveal) {
                const seen = FindBar._matches.findIndex(m => FindBar._inView(m));
                index = seen === -1 ? 0 : seen;
            }
            FindBar._index = index;
            if (o.reveal && index >= 0) FindBar._reveal(FindBar._matches[index]);
            FindBar._paint();
            FindBar._status();
        },

        _status() {
            const q = FindBar._input.value.trim();
            const n = FindBar._matches.length;
            const count = FindBar._bar.querySelector('#find-count');
            const empty = FindBar._bar.querySelector('.find-empty');
            count.textContent = !q ? '' : n ? `${FindBar._index + 1} of ${n}${FindBar._over ? '+' : ''}` : 'No matches';
            FindBar._bar.classList.toggle('find-bar--none', !!q && !n);
            empty.hidden = !q || !!n;
            // the record search lies under a dialog's overlay, out of reach
            empty.querySelector('.find-records').hidden = !!topDialog();
            FindBar._bar.querySelector('[data-find="prev"]').disabled = n < 2;
            FindBar._bar.querySelector('[data-find="next"]').disabled = n < 2;
        },

        _paint() {
            for (const el of document.querySelectorAll('[data-find-hit]')) el.removeAttribute('data-find-hit');
            const current = FindBar._matches[FindBar._index];
            for (const m of FindBar._matches) {
                if (m.field && m.field.getAttribute('data-find-hit') !== 'current') m.field.setAttribute('data-find-hit', m === current ? 'current' : 'match');
            }
            FindBar._drawMarks();
        },

        // The boxes over the matches' text, drawn again from where the text
        // is now. Each is clipped to every box that clips its text (a table
        // scrolled sideways, the page's own scroll), and one wholly out of
        // sight is not drawn. The layer is inside the page's text-size zoom
        // (html's CSS zoom), so a position read from the screen is turned
        // into the layer's own pixels by the ratio of the two.
        _drawMarks() {
            if (FindBar._frame) cancelAnimationFrame(FindBar._frame);
            FindBar._frame = 0;
            let layer = FindBar._layer;
            if (!layer) {
                layer = FindBar._layer = document.createElement('div');
                layer.id = 'find-marks';
                layer.className = 'find-marks';
                layer.setAttribute('aria-hidden', 'true');
                document.body.appendChild(layer);
            }
            layer.textContent = '';
            if (!FindBar._open || !FindBar._matches.length) return;
            const base = layer.getBoundingClientRect();
            const scale = layer.offsetWidth ? base.width / layer.offsetWidth : 1;
            const clips = new Map();
            const clipOf = (el) => {
                if (!el || el === document.documentElement || el === document.body) {
                    return { left: base.left, top: base.top, right: base.right, bottom: base.bottom };
                }
                if (clips.has(el)) return clips.get(el);
                const outer = clipOf(el.parentElement);
                let clip = outer;
                const cs = getComputedStyle(el);
                if (/(auto|scroll|hidden|clip)/.test(cs.overflowX + cs.overflowY)) {
                    const b = el.getBoundingClientRect();
                    clip = {
                        left: Math.max(outer.left, b.left), top: Math.max(outer.top, b.top),
                        right: Math.min(outer.right, b.right), bottom: Math.min(outer.bottom, b.bottom),
                    };
                }
                clips.set(el, clip);
                return clip;
            };
            const frag = document.createDocumentFragment();
            FindBar._matches.forEach((m, i) => {
                if (!m.parts) return;
                for (const part of m.parts) {
                    const clip = clipOf(part.startContainer.parentElement);
                    for (const r of part.getClientRects()) {
                        const left = Math.max(r.left, clip.left);
                        const right = Math.min(r.right, clip.right);
                        const top = Math.max(r.top, clip.top);
                        const bottom = Math.min(r.bottom, clip.bottom);
                        if (right <= left || bottom <= top) continue;
                        const box = document.createElement('div');
                        box.className = i === FindBar._index ? 'find-mark find-mark--current' : 'find-mark';
                        box.style.left = `${(left - base.left) / scale}px`;
                        box.style.top = `${(top - base.top) / scale}px`;
                        box.style.width = `${(right - left) / scale}px`;
                        box.style.height = `${(bottom - top) / scale}px`;
                        frag.appendChild(box);
                    }
                }
            });
            layer.appendChild(frag);
        },

        // A scroll anywhere or a resize: the boxes again, once a frame
        _redrawSoon() {
            if (FindBar._frame || !FindBar._open) return;
            FindBar._frame = requestAnimationFrame(() => { FindBar._frame = 0; FindBar._drawMarks(); });
        },

        _rect(m) {
            return m.field ? m.field.getBoundingClientRect() : m.range.getBoundingClientRect();
        },

        // In view: inside every box that clips it, and not under the bar
        _inView(m) {
            const r = FindBar._rect(m);
            if (!r.width && !r.height) return false;
            const el = m.field || m.range.startContainer.parentElement;
            for (let p = el && el.parentElement; p && p !== document.documentElement; p = p.parentElement) {
                const cs = getComputedStyle(p);
                if (/(auto|scroll|hidden|clip)/.test(cs.overflowX + cs.overflowY)) {
                    const b = p.getBoundingClientRect();
                    if (r.top < b.top || r.bottom > b.bottom || r.left < b.left || r.right > b.right) return false;
                }
            }
            const bar = FindBar._bar.getBoundingClientRect();
            const under = r.left < bar.right && r.right > bar.left && r.top < bar.bottom && r.bottom > bar.top;
            return !under;
        },

        _reveal(m) {
            if (!m || FindBar._inView(m)) return;
            const el = m.field || m.range.startContainer.parentElement;
            if (el) el.scrollIntoView({ block: 'center', inline: 'nearest' });
        },

        // The selected text, when it is a short line outside the bar
        _selectedText() {
            const sel = window.getSelection ? window.getSelection() : null;
            if (!sel || sel.isCollapsed || !sel.rangeCount) return '';
            const node = sel.anchorNode;
            if (node && FindBar._bar && FindBar._bar.contains(node)) return '';
            const text = sel.toString().trim();
            return text && text.length <= 200 && !/\n/.test(text) ? text : '';
        },

        // While open: the whole window is watched (a page drawn, a dialog
        // or a menu opened or closed, a section folded: the class is how
        // most of the app shows and hides), but for the bar's own changes,
        // and so is typing in a field (a value is no mutation)
        _watch() {
            const again = () => {
                clearTimeout(FindBar._refresh);
                FindBar._refresh = setTimeout(() => FindBar.search({ keepPlace: true }), 150);
            };
            FindBar._observer = new MutationObserver((records) => {
                if (records.some(r => !FindBar._own(r.target))) again();
            });
            FindBar._observer.observe(document.body, {
                childList: true, subtree: true, characterData: true,
                attributes: true, attributeFilter: ['hidden', 'open', 'style', 'class'],
            });
            FindBar._onEdit = (e) => { if (!FindBar._bar.contains(e.target)) again(); };
            document.addEventListener('input', FindBar._onEdit, true);
            document.addEventListener('change', FindBar._onEdit, true);
            // the boxes follow their text: a scroll (of any box: capture)
            // or the window changing size
            FindBar._onMove = () => FindBar._redrawSoon();
            document.addEventListener('scroll', FindBar._onMove, true);
            window.addEventListener('resize', FindBar._onMove);
        },

        _unwatch() {
            if (FindBar._observer) FindBar._observer.disconnect();
            FindBar._observer = null;
            if (FindBar._onEdit) {
                document.removeEventListener('input', FindBar._onEdit, true);
                document.removeEventListener('change', FindBar._onEdit, true);
            }
            FindBar._onEdit = null;
            if (FindBar._onMove) {
                document.removeEventListener('scroll', FindBar._onMove, true);
                window.removeEventListener('resize', FindBar._onMove);
            }
            FindBar._onMove = null;
        },
    };

    window.FindBar = FindBar;
    window.FindOnScreen = { fold, isNumericQuery, findInChunks, collectChunks };
})();
