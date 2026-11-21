/**
 * Line order — move lines up and down, drag them, and insert one below any
 * line, on every document line grid (invoices, estimates, sales receipts,
 * credit memos, bills, vendor credits, purchase orders, recurring invoices).
 *
 * A grid opts in with one call once its <tbody> is on the page:
 *
 *   LineOrder.attach('#inv-lines', {
 *       add: () => InvoicesPage.addLine(),   // appends one blank row
 *       changed: () => InvoicesPage.recalc(), // totals after a move/insert
 *       remove: true,  // a grid with no Remove line button of its own
 *   });
 *
 * The saved order is the order of the rows when the form is collected (each
 * grid sends line_order from its row position), so moving a row is all it
 * takes. Rows are told apart by their own data attribute, never by position,
 * so moving one does not disturb the grid's handlers.
 *
 * Every line form, attached here or not, removes a line through
 * LineOrder.removeLine: one rule for the names and for where the keyboard
 * goes (see "The one rule" below).
 *
 * A row marked data-line-part is not a line: it belongs to the line above it
 * (the nonprofit Split chooser). It has no place of its own, takes no
 * controls, travels with its line when the line moves and goes when the line
 * is removed.
 */
const LineOrder = {
    _opts: new WeakMap(),

    // Make a grid's rows movable. Rows added later get their controls too.
    attach(tbody, opts = {}) {
        if (typeof tbody === 'string') tbody = document.querySelector(tbody);
        if (!tbody || LineOrder._opts.has(tbody)) return;
        LineOrder._opts.set(tbody, opts);
        const head = tbody.closest && tbody.closest('table')?.tHead?.rows[0];
        if (head && !head.querySelector('.col-order')) {
            head.insertAdjacentHTML('beforeend', '<th scope="col" class="col-order" aria-label="Order"></th>');
        }
        tbody.addEventListener('click', (e) => LineOrder._click(tbody, e));
        tbody.addEventListener('dragstart', (e) => LineOrder._dragStart(tbody, e));
        tbody.addEventListener('dragover', (e) => LineOrder._dragOver(tbody, e));
        tbody.addEventListener('drop', (e) => LineOrder._drop(tbody, e));
        tbody.addEventListener('dragend', () => LineOrder._dragEnd(tbody));
        if (typeof MutationObserver !== 'undefined') {
            new MutationObserver(() => LineOrder.refresh(tbody)).observe(tbody, { childList: true });
        }
        LineOrder.refresh(tbody);
    },

    // A grid's lines, in order: its rows but the ones that belong to a line.
    rows(tbody) {
        return Array.from((tbody && tbody.children) || []).filter(r => r.tagName === 'TR' && !LineOrder._isPart(r));
    },

    _isPart(row) {
        return !!(row && row.hasAttribute && row.hasAttribute('data-line-part'));
    },

    // The rows that belong to a line: the marked rows right under it.
    _parts(row) {
        const parts = [];
        for (let r = row.nextElementSibling; LineOrder._isPart(r); r = r.nextElementSibling) parts.push(r);
        return parts;
    },

    // The line a row is, or belongs to.
    lineOf(row) {
        while (LineOrder._isPart(row) && row.previousElementSibling) row = row.previousElementSibling;
        return row;
    },

    // Give every line its controls and set which arrows are available.
    refresh(tbody) {
        const rows = LineOrder.rows(tbody);
        rows.forEach((row, i) => {
            if (!row.querySelector('.line-order-cell')) {
                row.insertAdjacentHTML('beforeend', LineOrder.cellHtml((LineOrder._opts.get(tbody) || {}).remove));
            }
            const up = row.querySelector('[data-line-order="up"]');
            const down = row.querySelector('[data-line-order="down"]');
            if (up) up.disabled = i === 0;
            if (down) down.disabled = i === rows.length - 1;
        });
    },

    cellHtml(remove = false) {
        return `<td class="line-order-cell"><span class="line-order-controls">
            <span class="line-order-handle" draggable="true" data-line-order="drag" title="Drag to move this line" aria-hidden="true"></span>
            <button type="button" class="btn btn-sm btn-secondary" data-line-order="up" aria-label="Move line up" title="Move line up">&#9650;</button>
            <button type="button" class="btn btn-sm btn-secondary" data-line-order="down" aria-label="Move line down" title="Move line down">&#9660;</button>
            <button type="button" class="btn btn-sm btn-secondary" data-line-order="insert" aria-label="Insert line below" title="Insert line below">+</button>${remove ? `
            <button type="button" class="btn btn-sm btn-danger" data-line-order="remove" aria-label="Remove line" title="Remove line">X</button>` : ''}
        </span></td>`;
    },

    // Swap a line with its neighbour, each with the rows that belong to it;
    // false at the ends.
    moveRow(row, dir) {
        const parent = row.parentNode;
        const rows = LineOrder.rows(parent);
        const other = rows[rows.indexOf(row) + (dir < 0 ? -1 : 1)];
        if (!other) return false;
        // the lower of the two goes above the upper
        const [mover, ref] = dir < 0 ? [row, other] : [other, row];
        [mover, ...LineOrder._parts(mover)].forEach(r => parent.insertBefore(r, ref));
        return true;
    },

    // Take a line out, with the rows that belong to it (NEW-278: the
    // purchase order's form had no way to); returns the line that takes its
    // place: the one below, else above.
    removeRow(row) {
        const parent = row.parentNode;
        const rows = LineOrder.rows(parent);
        const at = rows.indexOf(row);
        const next = rows[at + 1] || rows[at - 1] || null;
        [...LineOrder._parts(row), row].forEach(r => parent.removeChild(r));
        return next;
    },

    // Put a line before or after another (the drop), with the rows that
    // belong to it. A drop on a row that belongs to a line lands after
    // that line.
    moveNextTo(row, target, after) {
        if (LineOrder._isPart(target)) { target = LineOrder.lineOf(target); after = true; }
        if (row === target) return false;
        const parent = row.parentNode;
        const group = [row, ...LineOrder._parts(row)];
        let ref = target;
        if (after) {
            const under = LineOrder._parts(target);
            ref = (under.length ? under[under.length - 1] : target).nextElementSibling;
        }
        if (group.includes(ref)) ref = group[group.length - 1].nextElementSibling;
        group.forEach(r => parent.insertBefore(r, ref));
        return true;
    },

    // Add a blank row with the grid's own add function, then slot it under `row`.
    insertBelow(tbody, row) {
        const opts = LineOrder._opts.get(tbody) || {};
        if (!opts.add) return null;
        opts.add();
        const fresh = tbody.lastElementChild;
        if (!fresh || fresh === row) return null;
        // under the line and the rows that belong to it
        const parts = LineOrder._parts(row);
        const under = (parts.length ? parts[parts.length - 1] : row).nextElementSibling;
        if (under !== fresh) tbody.insertBefore(fresh, under);
        return fresh;
    },

    // The keyboard goes to the new row's first control. A picker's <select>
    // becomes a type-ahead box a moment after the row lands (combobox.js
    // re-parents it, which drops any focus it had), so the focus is placed
    // once that pass is over, on the box when there is one (W-53).
    focusFirst(row) {
        if (!row) return;
        setTimeout(() => {
            const first = row.querySelector('input:not([type=hidden]), select:not(.cbx-select), textarea');
            if (first && !first.disabled) first.focus();
        }, 0);
    },

    // ---- The one rule for every line form --------------------------------
    // After ANY change to the set or the order of a grid's lines (insert,
    // remove, move, drop), every line's fields and buttons are named by
    // their current place; and after Remove line the keyboard goes to the
    // same control on the line that took its place, else on the line before,
    // else to the form's Add line control: never to the document body.
    // LineOrder's own buttons come here, and so does every form with a
    // Remove line button of its own (removeLine below).

    // Name every line by where it is now. A field the page named from its
    // column and its line ("Rate, line 2") is renamed by nameFields; a name
    // a form wrote itself with the place at its start or end ("Line 2 memo",
    // "Remove line 2") has the number put right here.
    renumber(tbody) {
        if (!tbody || !tbody.children) return;
        if (typeof nameFields === 'function') nameFields(tbody);
        LineOrder.rows(tbody).forEach((row, i) => {
            if (!row.querySelectorAll) return;
            row.querySelectorAll('[aria-label]:not([data-line-name])').forEach(el => {
                const was = el.getAttribute('aria-label');
                const now = was.replace(/^(line) \d+\b/i, `$1 ${i + 1}`).replace(/\b(line) \d+$/i, `$1 ${i + 1}`);
                if (now !== was) el.setAttribute('aria-label', now);
            });
        });
    },

    // The lines changed: arrows, names, then the form's own totals (the
    // grid's `changed`, or the one a form hands in with its Remove line).
    _changed(tbody, changed) {
        if (LineOrder._opts.has(tbody)) LineOrder.refresh(tbody);
        LineOrder.renumber(tbody);
        const then = changed || (LineOrder._opts.get(tbody) || {}).changed;
        if (then) then();
    },

    // Remove line, for every line form. `from` is the pressed button (or
    // the row); `changed` is the form's recalc where the grid is not
    // attached with one. False when there was no row to take out.
    removeLine(from, changed) {
        const row = from && from.closest ? from.closest('tr') : null;
        const tbody = row && row.parentNode;
        if (!tbody) return false;
        const pressed = from !== row ? from
            : Array.from(row.querySelectorAll('button')).find(b => /^Remove\b/.test(b.getAttribute('aria-label') || ''));
        const like = LineOrder._like(pressed);
        const at = LineOrder.rows(tbody).indexOf(row);
        LineOrder.removeRow(row);
        LineOrder._changed(tbody, changed);
        LineOrder._focusAfterRemove(tbody, at, like);
        return true;
    },

    // What tells a control apart on its row: LineOrder's own mark, else its
    // name without the place, else the column it sits in.
    _like(control) {
        if (!control || !control.getAttribute) return null;
        const cell = control.closest('td');
        return {
            what: control.dataset ? control.dataset.lineOrder : '',
            name: LineOrder._nameKey(control),
            tag: control.tagName,
            cell: cell ? cell.cellIndex : -1,
        };
    },

    _nameKey(el) {
        return (el.getAttribute('aria-label') || '').replace(/\d+/g, '').replace(/\s+/g, ' ').trim();
    },

    _twin(row, like) {
        if (!row || !like) return null;
        if (like.what) return row.querySelector(`[data-line-order="${like.what}"]`);
        const controls = Array.from(row.querySelectorAll('button, input:not([type=hidden]), select, textarea'));
        const named = like.name && controls.find(c => c.tagName === like.tag && LineOrder._nameKey(c) === like.name);
        if (named) return named;
        const cell = like.cell >= 0 ? row.cells[like.cell] : null;
        return (cell && controls.find(c => c.tagName === like.tag && cell.contains(c))) || null;
    },

    _takes(el) {
        if (!el || el.disabled || el.offsetParent === null) return false;
        el.focus();
        return document.activeElement === el;
    },

    // The form's Add line control: the nearest button around the grid that
    // adds a line to it (marked data-line-add, or calling the form's
    // add-line function), the one after the table before any other.
    addControl(tbody) {
        const table = tbody.closest('table');
        const adds = b => !b.disabled && b.offsetParent !== null && !b.closest('tbody')
            && (b.hasAttribute('data-line-add') || /add\w*line\w*\(/i.test(b.getAttribute('onclick') || ''));
        for (let box = table && table.parentNode; box && box.querySelectorAll; box = box.parentNode) {
            const found = Array.from(box.querySelectorAll('button')).filter(adds);
            if (found.length) {
                return found.find(b => table.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) || found[0];
            }
        }
        return null;
    },

    _focusAfterRemove(tbody, at, like) {
        const rows = LineOrder.rows(tbody);
        if (LineOrder._takes(LineOrder._twin(rows[at], like))) return;
        if (at > 0 && LineOrder._takes(LineOrder._twin(rows[at - 1], like))) return;
        if (LineOrder._takes(LineOrder.addControl(tbody))) return;
        // a form with no Add line in reach: its first open control, so the
        // keyboard still stays inside the form
        const form = tbody.closest('form, .modal, [role="dialog"]');
        const first = form && Array.from(form.querySelectorAll('input:not([type=hidden]), select, textarea, button'))
            .find(el => !el.disabled && el.offsetParent !== null && el.tabIndex >= 0);
        if (first) first.focus();
    },

    // A copy of a line, to stand as a line of its own. A type-ahead picker
    // in the copy is put back to its plain select (the copied box is tied
    // to nothing and would keep the old line's name; the page gives the
    // select a box of its own once the row lands), and each select keeps
    // the choice the line had, which a copy alone drops.
    cloneRow(row) {
        const clone = row.cloneNode(true);
        clone.querySelectorAll('.cbx').forEach(wrap => {
            const sel = wrap.querySelector('select');
            if (!sel) { wrap.remove(); return; }
            sel.classList.remove('cbx-select');
            sel.removeAttribute('aria-hidden');
            sel.removeAttribute('tabindex');
            wrap.replaceWith(sel);
        });
        const copies = clone.querySelectorAll('select');
        row.querySelectorAll('select').forEach((sel, k) => { if (copies[k]) copies[k].value = sel.value; });
        return clone;
    },

    // One line becomes several (a split): the new lines take its place,
    // under the one rule. Every line is named by its place again, and a
    // keyboard that was on the line, or was left nowhere when the control
    // that asked for this went away, goes to `control`'s like on the first
    // new line (else that line's first field): never to the document body.
    replaceRow(row, fresh, changed, control) {
        const tbody = row && row.parentNode;
        if (!tbody || !fresh || !fresh.length) return false;
        const active = document.activeElement;
        const lost = !active || active === document.body || active === document.documentElement
            || !active.isConnected || row.contains(active)
            || LineOrder._parts(row).some(p => p.contains(active));
        const like = LineOrder._like(control);
        LineOrder._parts(row).forEach(p => tbody.removeChild(p));
        fresh.forEach(r => tbody.insertBefore(r, row));
        tbody.removeChild(row);
        LineOrder._changed(tbody, changed);
        if (lost && !LineOrder._takes(LineOrder._twin(fresh[0], like))) {
            const first = Array.from(fresh[0].querySelectorAll('input:not([type=hidden]), select, textarea, button'))
                .find(el => !el.disabled && el.offsetParent !== null && el.tabIndex >= 0);
            if (first) first.focus();
        }
        return true;
    },

    _click(tbody, e) {
        const btn = e.target.closest && e.target.closest('[data-line-order]');
        if (!btn || btn.disabled) return;
        const row = LineOrder.lineOf(btn.closest('tr'));
        const what = btn.dataset.lineOrder;
        if (what === 'insert') {
            const fresh = LineOrder.insertBelow(tbody, row);
            LineOrder._changed(tbody);
            LineOrder.focusFirst(fresh);
        } else if (what === 'remove') {
            LineOrder.removeLine(btn);
        } else if (what === 'up' || what === 'down') {
            if (!LineOrder.moveRow(row, what === 'up' ? -1 : 1)) return;
            LineOrder._changed(tbody);
            // keep the keyboard on the moved row: the same arrow, or the other at the end
            const same = row.querySelector(`[data-line-order="${what}"]`);
            const other = row.querySelector(`[data-line-order="${what === 'up' ? 'down' : 'up'}"]`);
            (same && !same.disabled ? same : other)?.focus();
        }
    },

    // Mouse drag: only the handle starts one, so selecting text in a box still works.
    _dragStart(tbody, e) {
        const handle = e.target.closest && e.target.closest('[data-line-order="drag"]');
        if (!handle) return;
        const row = handle.closest('tr');
        LineOrder._dragging = row;
        row.classList.add('line-order-dragging');
        if (e.dataTransfer) {
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', 'line');
            if (e.dataTransfer.setDragImage) e.dataTransfer.setDragImage(row, 10, 10);
        }
    },

    _dragOver(tbody, e) {
        if (!LineOrder._dragging) return;
        e.preventDefault();
        const target = e.target.closest && e.target.closest('tr');
        Array.from(tbody.children).forEach(r => r.classList.remove('line-order-over-top', 'line-order-over-bottom'));
        if (!target || LineOrder.lineOf(target) === LineOrder._dragging || target.parentNode !== tbody) return;
        target.classList.add(LineOrder._after(target, e) ? 'line-order-over-bottom' : 'line-order-over-top');
    },

    _drop(tbody, e) {
        const row = LineOrder._dragging;
        if (!row) return;
        e.preventDefault();
        const target = e.target.closest && e.target.closest('tr');
        if (target && target.parentNode === tbody && LineOrder.moveNextTo(row, target, LineOrder._after(target, e))) {
            LineOrder._changed(tbody);
        }
        LineOrder._dragEnd(tbody);
    },

    _dragEnd(tbody) {
        LineOrder._dragging = null;
        Array.from(tbody.children).forEach(r => r.classList.remove('line-order-dragging', 'line-order-over-top', 'line-order-over-bottom'));
    },

    // Whether the pointer is in the lower half of the row.
    _after(target, e) {
        const box = target.getBoundingClientRect();
        return e.clientY > box.top + box.height / 2;
    },
};
