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
 *   });
 *
 * The saved order is the order of the rows when the form is collected (each
 * grid sends line_order from its row position), so moving a row is all it
 * takes. Rows are told apart by their own data attribute, never by position,
 * so moving one does not disturb the grid's handlers.
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
                row.insertAdjacentHTML('beforeend', LineOrder.cellHtml());
            }
            const up = row.querySelector('[data-line-order="up"]');
            const down = row.querySelector('[data-line-order="down"]');
            if (up) up.disabled = i === 0;
            if (down) down.disabled = i === rows.length - 1;
        });
    },

    cellHtml() {
        return `<td class="line-order-cell"><span class="line-order-controls">
            <span class="line-order-handle" draggable="true" data-line-order="drag" title="Drag to move this line" aria-hidden="true"></span>
            <button type="button" class="btn btn-sm btn-secondary" data-line-order="up" aria-label="Move line up" title="Move line up">&#9650;</button>
            <button type="button" class="btn btn-sm btn-secondary" data-line-order="down" aria-label="Move line down" title="Move line down">&#9660;</button>
            <button type="button" class="btn btn-sm btn-secondary" data-line-order="insert" aria-label="Insert line below" title="Insert line below">+</button>
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

    _changed(tbody) {
        LineOrder.refresh(tbody);
        const opts = LineOrder._opts.get(tbody) || {};
        if (opts.changed) opts.changed();
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
