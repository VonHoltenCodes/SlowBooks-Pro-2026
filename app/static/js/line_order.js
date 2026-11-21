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

    // Give every row its controls and set which arrows are available.
    refresh(tbody) {
        const rows = Array.from(tbody.children).filter(r => r.tagName === 'TR');
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

    // Swap a row with its neighbour; false at the ends.
    moveRow(row, dir) {
        const parent = row.parentNode;
        if (dir < 0) {
            const prev = row.previousElementSibling;
            if (!prev) return false;
            parent.insertBefore(row, prev);
        } else {
            const next = row.nextElementSibling;
            if (!next) return false;
            parent.insertBefore(next, row);
        }
        return true;
    },

    // Take a row out (NEW-278: the purchase order's form had no way to);
    // returns the row that takes its place: the one below, else above.
    removeRow(row) {
        const next = row.nextElementSibling || row.previousElementSibling;
        row.parentNode.removeChild(row);
        return next;
    },

    // Put a row before or after another (the drop).
    moveNextTo(row, target, after) {
        if (row === target) return false;
        row.parentNode.insertBefore(row, after ? target.nextElementSibling : target);
        return true;
    },

    // Add a blank row with the grid's own add function, then slot it under `row`.
    insertBelow(tbody, row) {
        const opts = LineOrder._opts.get(tbody) || {};
        if (!opts.add) return null;
        opts.add();
        const fresh = tbody.lastElementChild;
        if (!fresh || fresh === row) return null;
        if (row.nextElementSibling !== fresh) tbody.insertBefore(fresh, row.nextElementSibling);
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
        Array.from(tbody.children).filter(r => r.tagName === 'TR').forEach((row, i) => {
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
        const at = Array.from(tbody.children).filter(r => r.tagName === 'TR').indexOf(row);
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
        const rows = Array.from(tbody.children).filter(r => r.tagName === 'TR');
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

    _click(tbody, e) {
        const btn = e.target.closest && e.target.closest('[data-line-order]');
        if (!btn || btn.disabled) return;
        const row = btn.closest('tr');
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
        if (!target || target === LineOrder._dragging || target.parentNode !== tbody) return;
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
