/**
 * Type-ahead in the pickers. A <select> of customers, vendors, items,
 * accounts, employees, jobs or classes, and any other list of SHOW_MIN or
 * more, becomes a box you type into, as in QuickBooks: "harb" finds Harbor
 * Light Bakery, "6500" finds 6500 - Rent or Lease, and Enter, Tab or a click
 * takes the highlighted one.
 *
 * Which pickers: Combobox.wants(). A template opts one in with data-search,
 * or out with data-no-search.
 *
 * The <select> stays, and stays the answer. Every form still reads
 * select.value and every onchange still runs: choosing an option selects it
 * and fires the select's input and change, and whatever sets the select (a
 * quick add picking the new customer, an item filling its line's account)
 * shows in the box. The select is kept out of sight, out of the Tab order and
 * out of the accessibility tree, and the label that named it (#198) names the
 * box.
 *
 * WAI-ARIA 1.2 editable combobox with list autocomplete: the box is
 * role=combobox, the list one role=listbox on <body> (a table's overflow
 * can't clip it, and it sits above a dialog), and the highlighted option is
 * the box's aria-activedescendant.
 */
const Combobox = {
    SHOW_MIN: 15,     // a list this long gets type-ahead whatever it holds
    MAX_SHOWN: 50,    // matches drawn at once; the rest say "keep typing"
    MAX_ALL: 300,     // drawn when nothing is typed yet

    // Words in a picker's name, id or class that say it lists things you'd
    // type the name of...
    PICKS: new Set([
        'customer', 'customers', 'vendor', 'vendors', 'payee', 'donor', 'donors', 'entity',
        'item', 'items', 'account', 'accounts', 'acct', 'category', 'debit', 'credit',
        'employee', 'employees', 'who', 'job', 'jobs', 'class', 'classes', 'fund', 'invoice', 'code',
    ]),
    // ...unless another word says it lists kinds of them, or settings for
    // them: account_type, is_1099_vendor, job-filter-status, invoice-terms,
    // email-template-invoice.
    KINDS: new Set([
        'type', 'types', 'kind', 'method', 'mode', 'status', 'role', 'basis', 'frequency',
        'template', 'is', 'function', 'period', 'rule', 'terms',
    ]),

    _seq: 0,
    _open: null,      // the box whose list is showing
    _pop: null,

    wants(sel) {
        if (sel._cbx || sel.multiple || sel.size > 1) return false;
        if (sel.hasAttribute('data-no-search')) return false;
        if (sel.hasAttribute('data-search')) return true;
        const words = `${sel.name || ''} ${sel.id || ''} ${sel.getAttribute('class') || ''}`
            .toLowerCase().split(/[^a-z0-9]+/);
        if (words.some(w => this.PICKS.has(w)) && !words.some(w => this.KINDS.has(w))) return true;
        return sel.options.length >= this.SHOW_MIN;
    },

    scan(root = document) {
        for (const sel of root.querySelectorAll('select')) {
            if (this.wants(sel)) new ComboBox(sel);
        }
    },

    // The one list every box shows its matches in, made on first use.
    popup() {
        if (this._pop) return this._pop;
        const pop = document.createElement('div');
        pop.className = 'cbx-popup';
        pop.hidden = true;
        pop.innerHTML = '<ul id="cbx-listbox" role="listbox"></ul>'
            + '<div class="cbx-note" aria-hidden="true"></div>'
            + '<div id="cbx-status" class="cbx-sr" role="status" aria-live="polite"></div>';
        // A press on the list keeps the typing where it is.
        pop.addEventListener('mousedown', e => e.preventDefault());
        pop.addEventListener('click', (e) => {
            const li = e.target.closest('[role="option"]');
            if (li && this._open) this._open._accept(this._open.shown[+li.dataset.i]);
        });
        pop.addEventListener('mousemove', (e) => {
            const li = e.target.closest('[role="option"]');
            if (li && this._open && +li.dataset.i !== this._open.active) this._open._setActive(+li.dataset.i, false);
        });
        document.body.appendChild(pop);
        this._pop = pop;
        return pop;
    },

    _inPopup(node) {
        return !!(this._pop && node && this._pop.contains(node));
    },
};

function _cbxText(opt) {
    return (opt.textContent || '').replace(/\s+/g, ' ').trim();
}

function _cbxHidden(el) {
    return el.hidden || el.style.display === 'none' || el.classList.contains('hidden');
}

class ComboBox {
    constructor(sel) {
        this.sel = sel;
        sel._cbx = this;
        this.shown = [];
        this.active = -1;
        this.editing = false;
        this._items = null;
        this._query = '';

        const box = document.createElement('input');
        box.type = 'text';
        box.id = sel.id ? `${sel.id}-box` : `cbx-${++Combobox._seq}`;
        box.className = 'cbx-input';
        box.autocomplete = 'off';
        box.spellcheck = false;
        box.setAttribute('role', 'combobox');
        box.setAttribute('aria-autocomplete', 'list');
        box.setAttribute('aria-expanded', 'false');
        box.setAttribute('aria-controls', 'cbx-listbox');
        this.box = box;

        const wrap = document.createElement('span');
        wrap.className = 'cbx';
        this.wrap = wrap;
        this._size();
        this._takeName();
        sel.parentNode.insertBefore(wrap, sel);
        wrap.append(box, sel);
        sel.classList.add('cbx-select');
        sel.setAttribute('aria-hidden', 'true');
        sel.tabIndex = -1;

        this._wrapSetters();
        box.disabled = sel.disabled;
        box.required = sel.required;
        this._mirrorHidden();
        this.sync();

        box.addEventListener('input', (e) => { e.stopPropagation(); this._typed(); });
        box.addEventListener('change', e => e.stopPropagation());  // the select's change is the one that counts
        box.addEventListener('keydown', e => this._key(e));
        box.addEventListener('focus', () => { this.sync(); box.select(); });
        box.addEventListener('blur', () => this._left());
        // A press opens the whole list, or closes it again; a press while
        // typing only moves the caret.
        box.addEventListener('mousedown', () => {
            if (box.disabled || this.editing) return;
            if (Combobox._open === this) this.close();
            else this.open('');
        });
        // Whatever focuses the select (a dialog's first field) reaches the box.
        sel.addEventListener('focus', () => box.focus());
        sel.addEventListener('change', () => this.sync());
        // The box reports a required choice; the select, out of sight, can't.
        sel.addEventListener('invalid', e => e.preventDefault());
        new MutationObserver(recs => this._changed(recs)).observe(sel, {
            childList: true, subtree: true, characterData: true, attributes: true,
            attributeFilter: ['disabled', 'required', 'hidden', 'style', 'class', 'label'],
        });
    }

    // In a form group or a table cell the select filled its column, and so
    // does the box; elsewhere the box takes the select's own width.
    _size() {
        const sel = this.sel, wrap = this.wrap, parent = sel.parentElement;
        for (const p of ['width', 'minWidth', 'maxWidth', 'flex', 'marginLeft', 'marginRight', 'marginTop', 'marginBottom']) {
            if (sel.style[p]) wrap.style[p] = sel.style[p];
        }
        // The select's own classes, as cbx--<class> on the visible box, so a
        // stylesheet can size what is drawn: a width rule on the select itself
        // reaches a 1px hidden element and does nothing (2.23.1 gate, NEW-103 /
        // W-5: the bill form's Account box stayed 55px wide).
        for (const c of sel.classList) if (c !== 'cbx-select') wrap.classList.add(`cbx--${c}`);
        if (parent && (parent.classList.contains('form-group') || /^(TD|TH)$/.test(parent.tagName))) {
            wrap.classList.add('cbx--fill');
        } else if (!sel.style.width && sel.offsetWidth) {
            wrap.style.width = `${sel.offsetWidth}px`;
        }
    }

    // The select's name is the box's: a <label for> now points at the box,
    // a label around the select has the box as its first field, and an
    // aria-label (a grid cell's "Item, line 1") comes along.
    _takeName() {
        const sel = this.sel, box = this.box;
        for (const label of Array.from(sel.labels || [])) {
            if (sel.id && label.htmlFor === sel.id) label.htmlFor = box.id;
        }
        for (const a of ['aria-label', 'aria-labelledby', 'aria-describedby', 'title', 'aria-required']) {
            const v = sel.getAttribute(a);
            if (v) box.setAttribute(a, v);
        }
        // and its marks: a sign-in that can't write (or isn't the
        // administrator) gets the box locked, as it got the select
        for (const a of ['data-write', 'data-admin', 'data-readonly-ok']) {
            if (sel.hasAttribute(a)) box.setAttribute(a, sel.getAttribute(a));
        }
    }

    // Code that sets the select by value or index shows in the box.
    _wrapSetters() {
        const me = this, proto = HTMLSelectElement.prototype;
        for (const prop of ['value', 'selectedIndex']) {
            const d = Object.getOwnPropertyDescriptor(proto, prop);
            Object.defineProperty(this.sel, prop, {
                configurable: true,
                enumerable: true,
                get() { return d.get.call(this); },
                set(v) { d.set.call(this, v); me.sync(); },
            });
        }
    }

    _mirrorHidden() {
        this.wrap.hidden = _cbxHidden(this.sel);
    }

    _changed(recs) {
        let options = false;
        for (const r of recs) {
            if (r.type !== 'attributes' || r.target !== this.sel) { options = true; continue; }
            if (r.attributeName === 'disabled') this.box.disabled = this.sel.disabled;
            else if (r.attributeName === 'required') this.box.required = this.sel.required;
            else if (r.attributeName !== 'label') this._mirrorHidden();
        }
        if (options) this._items = null;
        this.sync();
        if (Combobox._open === this) this._draw(this._query);
    }

    // The options as the list shows them: hidden and disabled ones (a
    // permit's vendor group while the form is for a customer) left out.
    items() {
        if (this._items) return this._items;
        const out = [];
        for (const opt of this.sel.options) {
            const group = opt.parentElement && opt.parentElement.tagName === 'OPTGROUP' ? opt.parentElement : null;
            if (opt.disabled || _cbxHidden(opt)) continue;
            if (group && (group.disabled || _cbxHidden(group))) continue;
            const text = _cbxText(opt);
            out.push({
                opt,
                text,
                lower: text.toLowerCase(),
                words: text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean),
                group: group ? group.label : '',
                action: /^__/.test(opt.value),  // "+ New Vendor" and the like
                // an action taken only when chosen (an arrow and Enter, or a
                // click), never as what an unmatched name falls to: "+ Add
                // item" (#445), whose form would take the focus from a
                // keyboard user tabbing past the cell
                explicit: opt.hasAttribute('data-explicit'),
                blank: opt.value === '',        // "Select...": no choice
            });
        }
        this._items = out;
        return out;
    }

    // What `query` finds: every word of it somewhere in the name, the names
    // that start with it first, then those with a word that does. An action
    // ("+ New Vendor") is always there.
    match(query) {
        const items = this.items();
        const q = query.toLowerCase().replace(/\s+/g, ' ').trim();
        if (!q) {
            const list = items.slice(0, Combobox.MAX_ALL);
            return { list, more: items.length - list.length, total: items.length };
        }
        const words = q.split(' ');
        const hits = [];
        items.forEach((it, i) => {
            if (it.action || it.blank) return;
            if (!words.every(w => it.lower.includes(w))) return;
            const rank = it.lower.startsWith(q) ? 0
                : words.every(w => it.words.some(x => x.startsWith(w))) ? 1 : 2;
            hits.push([rank, i, it]);
        });
        hits.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
        const list = hits.slice(0, Combobox.MAX_SHOWN).map(h => h[2]);
        const more = hits.length - list.length;
        list.push(...items.filter(it => it.action));
        return { list, more, total: hits.length };
    }

    sync() {
        const sel = this.sel, box = this.box;
        const opt = sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex] : null;
        const blank = Array.from(sel.options).find(o => o.value === '');
        box.placeholder = blank ? _cbxText(blank) : '';
        if (!this.editing) box.value = opt && opt.value !== '' ? _cbxText(opt) : '';
        box.setCustomValidity(sel.required && !sel.value ? 'Choose one from the list.' : '');
    }

    open(query) {
        if (this.box.disabled) return;
        const was = Combobox._open;
        if (was && was !== this) was._revert();
        Combobox._open = this;
        const pop = Combobox.popup();
        pop.hidden = false;
        this.box.setAttribute('aria-expanded', 'true');
        const list = pop.querySelector('#cbx-listbox');
        const name = this.box.labels && this.box.labels.length ? _cbxText(this.box.labels[0]).replace(/\s*\*$/, '')
            : (this.box.getAttribute('aria-label') || '');
        if (name) list.setAttribute('aria-label', name);
        else list.removeAttribute('aria-label');
        this._draw(query);
        if (!this._onScroll) {
            this._onScroll = (e) => { if (!Combobox._inPopup(e.target)) this._place(); };
            window.addEventListener('scroll', this._onScroll, true);
            window.addEventListener('resize', this._onScroll);
        }
    }

    close() {
        if (Combobox._open === this) {
            Combobox._open = null;
            const pop = Combobox.popup();
            pop.hidden = true;
            pop.querySelector('#cbx-listbox').innerHTML = '';
        }
        this.box.setAttribute('aria-expanded', 'false');
        this.box.removeAttribute('aria-activedescendant');
        this.active = -1;
        if (this._onScroll) {
            window.removeEventListener('scroll', this._onScroll, true);
            window.removeEventListener('resize', this._onScroll);
            this._onScroll = null;
        }
    }

    _draw(query) {
        this._query = query;
        const pop = Combobox.popup();
        const list = pop.querySelector('#cbx-listbox');
        const q = query.trim();
        const { list: shown, more, total } = this.match(query);
        this.shown = shown;
        let html = '', group = null;
        shown.forEach((it, i) => {
            // grouped (Employees, Equipment) when the whole list shows
            if (!q && it.group !== group) {
                if (group) html += '</ul></li>';
                if (it.group) html += `<li role="group" aria-labelledby="cbx-g-${i}"><span class="cbx-group" id="cbx-g-${i}">${escapeHtml(it.group)}</span><ul role="none">`;
                group = it.group;
            }
            const cls = `cbx-opt${it.action ? ' cbx-action' : ''}${it.opt.selected ? ' cbx-current' : ''}`;
            const where = q && it.group ? ` <span class="cbx-in">${escapeHtml(it.group)}</span>` : '';
            html += `<li role="option" id="cbx-opt-${i}" class="${cls}" data-i="${i}" aria-selected="false">${this._mark(it.text, q)}${where}</li>`;
        });
        if (group) html += '</ul></li>';
        list.innerHTML = html;
        const note = pop.querySelector('.cbx-note');
        note.textContent = q && !total ? 'No match' : more > 0 ? `${more} more — keep typing` : '';
        note.hidden = !note.textContent;
        this._say(q ? (total ? `${total} match${total === 1 ? '' : 'es'}` : 'No match') : '');
        // Typing: the top match is highlighted, so Enter or Tab takes it (or,
        // with nothing found, "+ New Vendor"; an explicit action such as
        // "+ Add item" waits to be chosen). Opening: the current choice.
        let i = q ? shown.findIndex(it => !it.action) : shown.findIndex(it => it.opt.selected && !it.blank);
        if (i < 0 && q) i = shown.findIndex(it => it.action && !it.explicit);
        if (i < 0 && !q && shown.length) i = 0;
        this._setActive(i, true);
        this._place();
    }

    // The words typed, in bold where they're found.
    _mark(text, q) {
        if (!q) return escapeHtml(text);
        const lower = text.toLowerCase();
        const spots = [];
        for (const w of q.toLowerCase().split(/\s+/).filter(Boolean)) {
            const at = lower.indexOf(w);
            if (at >= 0) spots.push([at, at + w.length]);
        }
        spots.sort((a, b) => a[0] - b[0]);
        let out = '', pos = 0;
        for (const [a, b] of spots) {
            if (a < pos) continue;
            out += escapeHtml(text.slice(pos, a)) + '<mark>' + escapeHtml(text.slice(a, b)) + '</mark>';
            pos = b;
        }
        return out + escapeHtml(text.slice(pos));
    }

    _say(text) {
        clearTimeout(this._sayLater);
        const status = Combobox.popup().querySelector('#cbx-status');
        this._sayLater = setTimeout(() => { status.textContent = text; }, 300);
    }

    // Under the box (or over it, near the bottom of the window), fixed to
    // the window, so no table or dialog clips it.
    _place() {
        const box = this.box, pop = Combobox.popup();
        if (!box.isConnected || box.offsetParent === null) { this._revert(); return; }
        const r = box.getBoundingClientRect();
        const below = window.innerHeight - r.bottom - 8, above = r.top - 8;
        const up = below < 160 && above > below;
        pop.style.left = `${Math.max(4, Math.min(r.left, window.innerWidth - Math.max(r.width, 200) - 4))}px`;
        pop.style.minWidth = `${r.width}px`;
        pop.style.maxWidth = `${Math.max(r.width, Math.min(480, window.innerWidth - 8))}px`;
        pop.style.maxHeight = `${Math.max(120, Math.min(280, up ? above : below))}px`;
        if (up) { pop.style.top = ''; pop.style.bottom = `${window.innerHeight - r.top + 1}px`; }
        else { pop.style.bottom = ''; pop.style.top = `${r.bottom + 1}px`; }
    }

    _setActive(i, scroll) {
        const list = Combobox.popup().querySelector('#cbx-listbox');
        const prev = list.querySelector('[aria-selected="true"]');
        if (prev) prev.setAttribute('aria-selected', 'false');
        this.active = i;
        const li = i >= 0 ? document.getElementById(`cbx-opt-${i}`) : null;
        if (!li) { this.box.removeAttribute('aria-activedescendant'); return; }
        li.setAttribute('aria-selected', 'true');
        this.box.setAttribute('aria-activedescendant', li.id);
        if (scroll) li.scrollIntoView({ block: 'nearest' });
    }

    _move(delta) {
        const n = this.shown.length;
        if (!n) return;
        const i = this.active < 0 ? (delta > 0 ? 0 : n - 1) : Math.min(n - 1, Math.max(0, this.active + delta));
        this._setActive(i, true);
    }

    _typed() {
        this.editing = true;
        this.open(this.box.value);
    }

    _key(e) {
        const open = Combobox._open === this;
        switch (e.key) {
        case 'ArrowDown':
            e.preventDefault();
            if (!open) this.open('');
            else if (!e.altKey) this._move(1);
            return;
        case 'ArrowUp':
            e.preventDefault();
            if (!open) this.open('');
            else if (e.altKey) this._accept(this.shown[this.active]);
            else this._move(-1);
            return;
        case 'PageDown':
        case 'PageUp':
            if (open) { e.preventDefault(); this._move(e.key === 'PageDown' ? 10 : -10); }
            return;
        case 'Enter':
            // A select never sent its form on Enter, and the box doesn't either.
            e.preventDefault();
            if (!open) this.open('');
            else if (this.active >= 0) this._accept(this.shown[this.active]);
            return;
        case 'Tab':
            if (open && this.active >= 0) this._accept(this.shown[this.active]);
            else if (open) this._revert();
            return;  // and on to the next field
        case 'Escape':
            // An open list closes, and the dialog stays; a closed one lets
            // Escape close the dialog, as before.
            if (open || this.editing) {
                e.preventDefault();
                e.stopPropagation();
                this._revert();
            }
            return;
        default:
        }
    }

    // Leaving the box: a name typed in full is taken, a box emptied goes
    // back to no choice, and anything else goes back to what was chosen.
    _left() {
        if (!this.editing) { this.close(); this.sync(); return; }
        const text = this.box.value.replace(/\s+/g, ' ').trim().toLowerCase();
        const items = this.items();
        const it = text ? items.find(x => !x.action && x.lower === text) : items.find(x => x.blank);
        if (it) this._accept(it);
        else this._revert();
    }

    _revert() {
        this.editing = false;
        this.close();
        this.sync();
    }

    _accept(it) {
        const typed = this.editing ? this.box.value.replace(/\s+/g, ' ').trim() : '';
        this.editing = false;
        this.close();
        if (!it) { this.sync(); return; }
        const sel = this.sel;
        if (!it.opt.selected) {
            it.opt.selected = true;  // the option itself: two can share a value
            sel.dispatchEvent(new Event('input', { bubbles: true }));
            sel.dispatchEvent(new Event('change', { bubbles: true }));
        }
        this.sync();
        if (it.action && typed) this._handOn(typed);
    }

    // "+ New Vendor" taken with a name typed that is not in the list: the
    // quick add opens with that name in it, as QuickBooks' Quick Add does.
    _handOn(typed) {
        const lower = typed.toLowerCase();
        if (this.items().some(it => !it.action && it.lower === lower)) return;
        const scope = this.wrap.closest('.form-group') || this.wrap.parentElement;
        const name = scope && Array.from(scope.querySelectorAll('input[aria-required="true"]')).find(n =>
            n !== this.box && n.offsetParent !== null && !n.value && / name$/i.test(n.getAttribute('aria-label') || ''));
        if (name) {
            name.value = typed;
            name.focus();
        }
    }
}

// Whatever a page or a dialog draws, as it is drawn, after #198's naming
// (utils.js observes first, so a select is named before its box takes the
// name). The list's own redrawing is left alone.
// (The node tests that load page scripts have no MutationObserver or body.)
if (typeof MutationObserver === 'function' && typeof document !== 'undefined' && document.body) {
    new MutationObserver((recs) => {
        if (recs.every(r => Combobox._inPopup(r.target))) return;
        Combobox.scan(document);
    }).observe(document.body, { childList: true, subtree: true });
    Combobox.scan(document);
    // A form reset puts each select back without a word: the boxes follow.
    document.addEventListener('reset', (e) => {
        setTimeout(() => e.target.querySelectorAll('select.cbx-select')
            .forEach(s => s._cbx && s._cbx.sync()), 0);
    });
}
