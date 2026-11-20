/**
 * Helper text behind the tortoise (#977).
 *
 * A form's teaching text ("Optional. Separate addresses with commas.") used
 * to sit in the open under its field: about sixty grey lines that teach once
 * and then make every page read as a wall. hint(text) puts one behind a small
 * button after the field's label instead, the whole tortoise with the "i" on its shell.
 * Errors, warnings and state lines are never hints; they stay where they are.
 *
 *   `<label for="cc">CC ${hint('Optional. Separate addresses with commas.', 'CC')}</label>`
 *
 * The text stays in the page, hidden until opened, and the button names it
 * with aria-describedby, so a screen reader reads it at the button without
 * opening anything. Click, Enter or Space opens a small note under the
 * icon and moves focus to it, so a screen reader reads it as it opens
 * (NEW-263: VoiceOver said only "expanded"); Escape closes it and puts
 * focus back on the button; a click elsewhere, or focus leaving the
 * tortoise and its note in any way, closes it (W-189); one is open at a
 * time. Nothing opens on hover.
 *
 * A tortoise inside a field's <label> is not part of the field's name
 * (NEW-263): Hints.nameFields gives the field the label's own words by
 * aria-labelledby, so neither "About CC" nor an open note's text is read
 * as the field's name.
 */
let _hintSeq = 0;
let _hintNameSeq = 0;

// `text` is page markup (a hint may carry <strong> or a link): the caller
// escapes what a user typed, as with any template. `about` is the field's
// own name, shown in bold at the head of the note and read as the button's
// name ("About CC"); with none the button is "About this field". `id` names
// the note's text when a field already points at it with aria-describedby
// (a hidden note still describes its field); with none it is hint-N. The
// note itself is `${id}-note`. The description points at the text, not at
// the note: WebKit gives no description from a hidden element whose role
// takes no name from its content (role="note"), so VoiceOver read none
// (NEW-263); the text's own span describes in WebKit and Chromium alike.
function hint(text, about = '', id = '') {
    id = id || `hint-${++_hintSeq}`;
    const note = `${id}-note`;
    const name = about ? `About ${about}` : 'About this field';
    // a space after the bold name: WebKit reads the block's words run together without one
    const head = about ? `<strong class="hint-pop-title">${escapeHtml(about)}</strong> ` : '';
    return `<span class="hint-wrap"><button type="button" class="hint-icon" data-hint aria-label="${escapeHtml(name)}" aria-expanded="false" aria-controls="${note}" aria-describedby="${id}"><img src="/static/img/tortoise-hint.png" srcset="/static/img/tortoise-hint@2x.png 2x" width="22" height="22" alt="" aria-hidden="true"></button><span class="hint-pop" id="${note}" role="note" tabindex="-1" hidden><img class="hint-pop-mark" src="/static/img/tortoise-info.png" srcset="/static/img/tortoise-info@2x.png 2x" width="28" height="28" alt="" aria-hidden="true"><span class="hint-pop-body" id="${id}">${head}${text}</span></span></span>`;
}

const Hints = {
    _open: null,   // the button whose note is open

    note(button) { return document.getElementById(button.getAttribute('aria-controls')); },

    // A hint whose text follows a choice on the form: swap the body's words
    // and keep the field name at its head. `text` is plain text.
    setText(id, text) {
        const el = document.getElementById(id);
        const body = el && (el.classList && el.classList.contains('hint-pop-body') ? el : el.querySelector('.hint-pop-body'));
        if (!body) return;
        const head = body.querySelector('.hint-pop-title');
        body.textContent = head ? ` ${text}` : text;
        if (head) body.insertBefore(head, body.firstChild);
    },

    open(button) {
        if (Hints._open && Hints._open !== button) Hints.close();
        const note = Hints.note(button);
        if (!note) return;
        note.hidden = false;
        button.setAttribute('aria-expanded', 'true');
        Hints._open = button;
        Hints.position(button);
        // the note is placed against the window, so it follows its button
        // as the page or a dialog scrolls, and as the window changes size
        if (typeof window !== 'undefined' && window.addEventListener) window.addEventListener('resize', Hints.onMove);
        if (typeof document !== 'undefined' && document.addEventListener) document.addEventListener('scroll', Hints.onMove, true);
        // a field's note takes focus, named by its text, so a screen reader
        // reads it as it opens (a section's list is reached by Tab, as
        // before). Named only while open: a shut note is no label on the page.
        if (note.getAttribute && note.getAttribute('tabindex') === '-1' && note.focus) {
            note.setAttribute('aria-labelledby', button.getAttribute('aria-describedby'));
            note.focus({ preventScroll: true });
        }
    },

    close() {
        const button = Hints._open;
        if (!button) return;
        const note = Hints.note(button);
        // focus in the note goes back to its button, not to the page's top
        const held = note && typeof document !== 'undefined' && note.contains && note.contains(document.activeElement);
        if (note) {
            note.hidden = true;
            if (note.removeAttribute) note.removeAttribute('aria-labelledby');
        }
        button.setAttribute('aria-expanded', 'false');
        Hints._open = null;
        if (typeof window !== 'undefined' && window.removeEventListener) window.removeEventListener('resize', Hints.onMove);
        if (typeof document !== 'undefined' && document.removeEventListener) document.removeEventListener('scroll', Hints.onMove, true);
        if (held && button.isConnected !== false && button.focus) button.focus({ preventScroll: true });
    },

    toggle(button) {
        if (Hints._open === button) Hints.close();
        else Hints.open(button);
    },

    onClick(e) {
        const button = e.target.closest && e.target.closest('[data-hint]');
        if (button) {
            // a hint inside a <label> must not also click the label's field
            e.preventDefault();
            Hints.toggle(button);
            return;
        }
        // a click inside the open note (to select its text, or on a link in it) keeps it
        if (Hints._open && !(e.target.closest && e.target.closest('.hint-pop'))) Hints.close();
    },

    onKeydown(e) {
        if (e.key !== 'Escape' || !Hints._open) return;
        const button = Hints._open;
        Hints.close();
        // Escape closes the note, not the dialog it sits in
        e.stopPropagation();
        if (button.focus) button.focus();
    },

    // Whether an element is the open tortoise, its note or inside them
    _within(button, el) {
        if (!el) return false;
        const wrap = button.closest && button.closest('.hint-wrap');
        const note = Hints.note(button);
        return !!((wrap && wrap.contains(el)) || (note && note.contains && note.contains(el)));
    },

    // Focus leaving the tortoise and its note closes the note (W-189). Tab
    // from the page's last control leaves the page, or wraps to its top,
    // with no relatedTarget to compare: then where focus has gone is looked
    // at once the move is over, and the note stays open only if focus is
    // still inside it (the window itself losing focus keeps it).
    onFocusout(e) {
        const button = Hints._open;
        if (!button) return;
        if (e.relatedTarget) {
            if (!Hints._within(button, e.relatedTarget)) Hints.close();
            return;
        }
        setTimeout(() => {
            if (Hints._open !== button) return;
            const now = typeof document !== 'undefined' ? document.activeElement : null;
            if (!Hints._within(button, now)) Hints.close();
        }, 0);
    },

    // A field whose <label> holds a tortoise is named by the label's own
    // words (NEW-263): the label's text, outside the tortoise and any field
    // in it, goes in spans the field points at with aria-labelledby. A field
    // already named another way is left alone.
    nameFields(root) {
        if (!root || !root.querySelectorAll) return;
        const labels = [...root.querySelectorAll('label')];
        if (root.matches && root.matches('label')) labels.unshift(root);
        labels.forEach((label) => {
            const field = label.control;
            if (!field || field.hasAttribute('aria-labelledby') || field.hasAttribute('aria-label')) return;
            const kids = [...label.childNodes];
            if (!kids.some((n) => n.nodeType === 1 && n.classList.contains('hint-wrap'))) return;
            const ids = [];
            let run = null;
            kids.forEach((n) => {
                const apart = n.nodeType === 1 && n.matches('.hint-wrap, input, select, textarea, button');
                if (apart || (n.nodeType !== 1 && !run && !n.textContent.trim())) { run = null; return; }
                if (!run) {
                    run = document.createElement('span');
                    run.id = `hint-name-${++_hintNameSeq}`;
                    label.insertBefore(run, n);
                    ids.push(run.id);
                }
                run.appendChild(n);
            });
            if (ids.length) field.setAttribute('aria-labelledby', ids.join(' '));
        });
    },

    init() {
        document.addEventListener('click', Hints.onClick);
        // capture: before a dialog's own Escape handler
        document.addEventListener('keydown', Hints.onKeydown, true);
        document.addEventListener('focusout', Hints.onFocusout);
        // every page and dialog is drawn after this runs: name their fields as they come
        if (typeof MutationObserver !== 'undefined' && document.documentElement) {
            new MutationObserver((records) => records.forEach((r) => r.addedNodes.forEach((n) => {
                if (n.nodeType === 1) Hints.nameFields(n);
            }))).observe(document.documentElement, { childList: true, subtree: true });
            Hints.nameFields(document);
        }
    },
};

if (typeof document !== 'undefined' && document.addEventListener) Hints.init();

/**
 * One tortoise for a whole section (#977, Settings): a page whose fields
 * each carry a line of teaching would grow a tortoise on every row, so a
 * section heading gets one button whose note lists the section's fields,
 * each name in bold and its description after it, in the page's order.
 *
 *   `<div class="settings-h-row"><h3 id="h">Closing Date</h3>${hintList('Closing Date', [
 *       { text: 'Prevent modifications to transactions before this date.' },
 *       { name: 'Password (optional)', id: 'closing-password-help', text: '…' },
 *   ])}</div>`
 *
 * An entry with no name is the section's own lead and comes first. Each
 * entry has its own id (the caller's, or one made here), and the field it
 * describes points at it with aria-describedby, so a screen reader still
 * reads a field's description at the field with the note closed. An entry
 * marked hidden is left out until Hints.showEntry(id, true) (a field the
 * page shows only on some choice); admin: true marks it data-admin, so
 * App.adminPass hides it from everyone else. `text` is page markup, as for
 * hint(); `name` is plain words and escaped here. The same Hints open the
 * note: click, Enter or Space; Escape, a click elsewhere or Tab out close
 * it; one at a time. The note scrolls on its own and takes focus (Tab), so
 * a long list reads and scrolls from the keyboard. Nothing to list: ''.
 */
let _hintListSeq = 0;

function hintList(about, entries) {
    const items = (entries || []).filter(e => e && (e.text || e.id));
    if (!items.length) return '';
    const id = `hint-list-${++_hintListSeq}`;
    const name = escapeHtml(`About ${about}`);
    let lead = '';
    let list = '';
    items.forEach((e, i) => {
        const eid = escapeHtml(e.id || `${id}-${i + 1}`);
        const marks = (e.hidden ? ' hidden' : '') + (e.admin ? ' data-admin' : '');
        if (!e.name) {
            lead += `<p class="hint-list-lead" id="${eid}"${marks}>${e.text || ''}</p>`;
        } else {
            list += `<div class="hint-list-item"${marks}><dt>${escapeHtml(e.name)}</dt><dd id="${eid}">${e.text || ''}</dd></div>`;
        }
    });
    return `<div class="hint-wrap hint-wrap--list"><button type="button" class="hint-icon" data-hint aria-label="${name}" aria-expanded="false" aria-controls="${id}"><img src="/static/img/tortoise-hint.png" srcset="/static/img/tortoise-hint@2x.png 2x" width="22" height="22" alt="" aria-hidden="true"></button>`
        + `<div class="hint-pop hint-pop--list" id="${id}" role="note" aria-label="${name}" tabindex="0" hidden><img class="hint-pop-mark" src="/static/img/tortoise-info.png" srcset="/static/img/tortoise-info@2x.png 2x" width="28" height="28" alt="" aria-hidden="true">`
        + `<div class="hint-pop-body"><strong class="hint-pop-title">${escapeHtml(about)}</strong>${lead}${list ? `<dl class="hint-list">${list}</dl>` : ''}</div></div></div>`;
}

// Show or leave out one entry of a section's note, as its field comes and
// goes (the LLC's Taxed as, a provider's own fields).
Hints.showEntry = function (id, on) {
    const el = typeof document !== 'undefined' && document.getElementById ? document.getElementById(id) : null;
    if (!el) return;
    const box = (el.closest && el.closest('.hint-list-item')) || el;
    box.hidden = !on;
};

/*
 * Where an open note goes (#977 review). A note sat under its button inside
 * the button's own box, so a dialog's edge or a table's scroll box cut it
 * off (the "flip left" looked only at the window). It is now placed against
 * the window (position: fixed, from the button's place on screen), so no
 * box around it clips it, and kept wholly inside the window, 8px clear of
 * every edge:
 *   - across: lined up with its button, moved in from an edge it would cross;
 *   - down: under the button's field row (the label and the field under it,
 *     so the note does not cover the field it describes) when there is room,
 *     else above the button, else under the button itself; a note taller
 *     than either side (a section's long list) takes the roomier side and
 *     scrolls inside the height it has.
 * Where a box around it re-anchors fixed placement (a transform or filter
 * on an ancestor), the note's measured place at 0,0 is the offset taken off.
 * A scroll or a change of window size places it again; once its button is
 * scrolled out of sight the note closes.
 */
const HINT_EDGE = 8;  // clear of the window's edges
const HINT_GAP = 4;   // between the button (or its field) and the note

// btn: the button's rect; size: the note's {width, height}; win: the
// window's {width, height}; row: the rect of the field row the note should
// leave in view, or null. Returns { left, top, side, maxHeight }.
Hints.place = function (btn, size, win, row) {
    const width = Math.min(size.width, win.width - 2 * HINT_EDGE);
    const left = Math.max(HINT_EDGE, Math.min(btn.left - 2, win.width - HINT_EDGE - width));
    const underRow = (row ? Math.max(row.bottom, btn.bottom) : btn.bottom) + HINT_GAP;
    const underButton = btn.bottom + HINT_GAP;
    const aboveButton = btn.top - HINT_GAP;  // the note's bottom edge
    const roomBelow = win.height - HINT_EDGE - underRow;
    const roomAbove = aboveButton - HINT_EDGE;
    if (size.height <= roomBelow) return { left, top: underRow, side: 'below', maxHeight: null };
    if (size.height <= roomAbove) return { left, top: aboveButton - size.height, side: 'above', maxHeight: null };
    if (size.height <= win.height - HINT_EDGE - underButton) return { left, top: underButton, side: 'below', maxHeight: null };
    // taller than either side: the roomier one, the note scrolling within it
    const belowRoom = win.height - HINT_EDGE - underButton;
    if (belowRoom >= roomAbove) return { left, top: underButton, side: 'below', maxHeight: Math.max(0, belowRoom) };
    return { left, top: HINT_EDGE, side: 'above', maxHeight: Math.max(0, roomAbove) };
};

// The field row a button's note should leave in view: the form group the
// button's label heads (its label and the field under it), or the field a
// label names; a heading's or a column head's button has none.
Hints.rowOf = function (button) {
    const group = button.closest && button.closest('.form-group');
    if (group && group.getBoundingClientRect) {
        const r = group.getBoundingClientRect();
        if (r.height > 0 && r.height <= 120) return r;
    }
    const label = button.closest && button.closest('label');
    const field = label && label.control;
    if (field && field.getBoundingClientRect) {
        const r = field.getBoundingClientRect();
        if (r.height > 0) return r;
    }
    return null;
};

// Whether the button is still in sight: inside the window and inside every
// scrolling box around it.
Hints.inSight = function (button) {
    if (typeof window === 'undefined' || !button.getBoundingClientRect) return true;
    const b = button.getBoundingClientRect();
    const across = (r) => b.bottom > r.top && b.top < r.bottom && b.right > r.left && b.left < r.right;
    if (!across({ top: 0, left: 0, bottom: window.innerHeight, right: window.innerWidth })) return false;
    if (typeof getComputedStyle !== 'function') return true;
    for (let p = button.parentElement; p && p !== document.body; p = p.parentElement) {
        const s = getComputedStyle(p);
        if (/(auto|scroll|hidden|clip)/.test(s.overflowX + s.overflowY) && !across(p.getBoundingClientRect())) return false;
    }
    return true;
};

Hints.position = function (button) {
    const note = Hints.note(button);
    if (!note || !note.style || !note.getBoundingClientRect || !button.getBoundingClientRect || typeof window === 'undefined') return;
    note.style.left = '0px';
    note.style.top = '0px';
    note.style.maxHeight = '';
    note.style.overflowY = '';
    // where 0,0 lands: the window's corner, unless a box around re-anchors it
    const origin = note.getBoundingClientRect();
    const p = Hints.place(button.getBoundingClientRect(), { width: origin.width, height: origin.height },
        { width: window.innerWidth, height: window.innerHeight }, Hints.rowOf(button));
    note.style.left = `${Math.round(p.left - origin.left)}px`;
    note.style.top = `${Math.round(p.top - origin.top)}px`;
    if (p.maxHeight !== null) {
        note.style.maxHeight = `${Math.floor(p.maxHeight)}px`;
        note.style.overflowY = 'auto';
    }
    if (note.setAttribute) note.setAttribute('data-side', p.side);
};

// A scroll (not the note's own, a long list scrolling) or a new window size
Hints.onMove = function (e) {
    const button = Hints._open;
    if (!button) return;
    const note = Hints.note(button);
    if (e && e.type === 'scroll' && note && e.target && note.contains && note.contains(e.target)) return;
    if (!Hints.inSight(button)) { Hints.close(); return; }
    Hints.position(button);
};
