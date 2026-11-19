/**
 * Helper text behind the tortoise (#977).
 *
 * A form's teaching text ("Optional. Separate addresses with commas.") used
 * to sit in the open under its field: about sixty grey lines that teach once
 * and then make every page read as a wall. hint(text) puts one behind a small
 * button after the field's label instead, the tortoise's shell with its "i".
 * Errors, warnings and state lines are never hints; they stay where they are.
 *
 *   `<label for="cc">CC ${hint('Optional. Separate addresses with commas.', 'CC')}</label>`
 *
 * The text stays in the page, hidden until opened, and the button names it
 * with aria-describedby, so a screen reader reads it at the button without
 * opening anything. Click, Enter or Space opens a small note under the
 * icon; Escape closes it and keeps focus on the button; a click elsewhere,
 * or Tab out, closes it; one is open at a time. Nothing opens on hover.
 */
let _hintSeq = 0;

// `text` is page markup (a hint may carry <strong> or a link): the caller
// escapes what a user typed, as with any template. `about` is the field's
// own name, shown in bold at the head of the note and read as the button's
// name ("About CC"); with none the button is "About this field".
function hint(text, about = '') {
    const id = `hint-${++_hintSeq}`;
    const name = about ? `About ${about}` : 'About this field';
    const head = about ? `<strong class="hint-pop-title">${escapeHtml(about)}</strong>` : '';
    return `<span class="hint-wrap"><button type="button" class="hint-icon" data-hint aria-label="${escapeHtml(name)}" aria-expanded="false" aria-controls="${id}" aria-describedby="${id}"><img src="/static/img/tortoise-hint.png" srcset="/static/img/tortoise-hint@2x.png 2x" width="22" height="17" alt="" aria-hidden="true"></button><span class="hint-pop" id="${id}" role="note" hidden><img class="hint-pop-mark" src="/static/img/tortoise-info.png" srcset="/static/img/tortoise-info@2x.png 2x" width="28" height="28" alt="" aria-hidden="true"><span class="hint-pop-body">${head}${text}</span></span></span>`;
}

const Hints = {
    _open: null,   // the button whose note is open

    note(button) { return document.getElementById(button.getAttribute('aria-controls')); },

    open(button) {
        if (Hints._open && Hints._open !== button) Hints.close();
        const note = Hints.note(button);
        if (!note) return;
        note.hidden = false;
        note.classList.remove('hint-pop--right');
        button.setAttribute('aria-expanded', 'true');
        Hints._open = button;
        // a note that would run off the right edge opens leftward instead
        const box = note.getBoundingClientRect && note.getBoundingClientRect();
        if (box && typeof window !== 'undefined' && box.right > window.innerWidth - 8) note.classList.add('hint-pop--right');
    },

    close() {
        const button = Hints._open;
        if (!button) return;
        const note = Hints.note(button);
        if (note) note.hidden = true;
        button.setAttribute('aria-expanded', 'false');
        Hints._open = null;
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

    onFocusout(e) {
        if (!Hints._open) return;
        const wrap = Hints._open.closest('.hint-wrap');
        if (wrap && e.relatedTarget && !wrap.contains(e.relatedTarget)) Hints.close();
    },

    init() {
        document.addEventListener('click', Hints.onClick);
        // capture: before a dialog's own Escape handler
        document.addEventListener('keydown', Hints.onKeydown, true);
        document.addEventListener('focusout', Hints.onFocusout);
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
    return `<div class="hint-wrap hint-wrap--list"><button type="button" class="hint-icon" data-hint aria-label="${name}" aria-expanded="false" aria-controls="${id}"><img src="/static/img/tortoise-hint.png" srcset="/static/img/tortoise-hint@2x.png 2x" width="22" height="17" alt="" aria-hidden="true"></button>`
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

// A section's note is tall: one opened from a heading low in the window
// opens upward when there is room above, and otherwise brings itself into
// view, so the list never starts below the window's edge.
const _hintOpenOne = Hints.open;
Hints.open = function (button) {
    _hintOpenOne(button);
    const note = Hints._open === button ? Hints.note(button) : null;
    if (!note || !note.classList || typeof note.classList.contains !== 'function' || !note.classList.contains('hint-pop--list')) return;
    note.classList.remove('hint-pop--up');
    if (typeof window === 'undefined' || !note.getBoundingClientRect || !button.getBoundingClientRect) return;
    const box = note.getBoundingClientRect();
    if (box.bottom <= window.innerHeight - 8) return;
    if (button.getBoundingClientRect().top - 8 >= box.height + 4) note.classList.add('hint-pop--up');
    else if (note.scrollIntoView) note.scrollIntoView({ block: 'nearest' });
};
