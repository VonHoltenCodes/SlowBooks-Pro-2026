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
 * icon; Escape closes it and keeps focus on the button; a click elsewhere,
 * or Tab out, closes it; one is open at a time. Nothing opens on hover.
 */
let _hintSeq = 0;

// `text` is page markup (a hint may carry <strong> or a link): the caller
// escapes what a user typed, as with any template. `about` is the field's
// own name, shown in bold at the head of the note and read as the button's
// name ("About CC"); with none the button is "About this field". `id` names
// the note when a field already points at it with aria-describedby (a hidden
// note still describes its field); with none it is hint-N.
function hint(text, about = '', id = '') {
    id = id || `hint-${++_hintSeq}`;
    const name = about ? `About ${about}` : 'About this field';
    const head = about ? `<strong class="hint-pop-title">${escapeHtml(about)}</strong>` : '';
    return `<span class="hint-wrap"><button type="button" class="hint-icon" data-hint aria-label="${escapeHtml(name)}" aria-expanded="false" aria-controls="${id}" aria-describedby="${id}"><img src="/static/img/tortoise-hint.png" srcset="/static/img/tortoise-hint@2x.png 2x" width="22" height="22" alt="" aria-hidden="true"></button><span class="hint-pop" id="${id}" role="note" hidden><img class="hint-pop-mark" src="/static/img/tortoise-info.png" srcset="/static/img/tortoise-info@2x.png 2x" width="28" height="28" alt="" aria-hidden="true"><span class="hint-pop-body">${head}${text}</span></span></span>`;
}

const Hints = {
    _open: null,   // the button whose note is open

    note(button) { return document.getElementById(button.getAttribute('aria-controls')); },

    // A hint whose text follows a choice on the form: swap the body's words
    // and keep the field name at its head. `text` is plain text.
    setText(id, text) {
        const note = document.getElementById(id);
        const body = note && note.querySelector('.hint-pop-body');
        if (!body) return;
        const head = body.querySelector('.hint-pop-title');
        body.textContent = text;
        if (head) body.insertBefore(head, body.firstChild);
    },

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
