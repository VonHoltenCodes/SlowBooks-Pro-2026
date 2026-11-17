/**
 * A show-password eye on every password field (#970, the owner's request).
 *
 * Every <input type="password"> gets a button after it that shows what was
 * typed while it is pressed, and hides it again: click or Enter/Space
 * toggles, the button is named "Show password" / "Hide password" for a
 * screen reader, and aria-pressed says which. Fields rendered later (the
 * sign-in page, dialogs, Settings sections) are found by a MutationObserver,
 * so no page has to ask for it. A field that must not have one says
 * data-no-eye.
 *
 * Nothing about the value is logged, stored or sent differently: the
 * toggle only changes the input's type between "password" and "text".
 */
(function (root) {
    'use strict';

    var EYE = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">'
        + '<path d="M12 5C6.5 5 2.3 9.3 1 12c1.3 2.7 5.5 7 11 7s9.7-4.3 11-7c-1.3-2.7-5.5-7-11-7z" fill="none" stroke="currentColor" stroke-width="2"/>'
        + '<circle cx="12" cy="12" r="3.2" fill="none" stroke="currentColor" stroke-width="2"/></svg>';
    var EYE_OFF = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">'
        + '<path d="M12 5C6.5 5 2.3 9.3 1 12c1.3 2.7 5.5 7 11 7s9.7-4.3 11-7c-1.3-2.7-5.5-7-11-7z" fill="none" stroke="currentColor" stroke-width="2"/>'
        + '<circle cx="12" cy="12" r="3.2" fill="none" stroke="currentColor" stroke-width="2"/>'
        + '<path d="M4 20L20 4" stroke="currentColor" stroke-width="2"/></svg>';

    // The toggle itself, kept free of the DOM lookup so it can be tested on
    // its own: `input` and `button` are any objects with `type`, `setAttribute`
    // and `innerHTML`.
    function toggle(input, button) {
        var showing = input.type === 'text';
        input.type = showing ? 'password' : 'text';
        button.setAttribute('aria-pressed', showing ? 'false' : 'true');
        button.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
        button.setAttribute('title', showing ? 'Show password' : 'Hide password');
        button.innerHTML = showing ? EYE : EYE_OFF;
        return !showing;
    }

    function attach(input) {
        if (!input || input.dataset.eye === '1' || input.hasAttribute('data-no-eye')) return null;
        input.dataset.eye = '1';
        var wrap = document.createElement('span');
        wrap.className = 'pw-eye-wrap';
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'pw-eye';
        button.setAttribute('aria-pressed', 'false');
        button.setAttribute('aria-label', 'Show password');
        button.setAttribute('title', 'Show password');
        button.innerHTML = EYE;
        button.addEventListener('click', function () { toggle(input, button); });
        // when the field is reset to hidden by the page (a dialog reopened),
        // the button follows
        input.addEventListener('pw-eye:hide', function () { if (input.type === 'text') toggle(input, button); });
        // moving a focused field drops its focus (#988): the sign-in page
        // and the closing-date prompt focus the field before the observer
        // sees it, so note the focus and the caret and put them back
        var hadFocus = document.activeElement === input;
        var caret = null;
        try { caret = hadFocus ? [input.selectionStart, input.selectionEnd] : null; } catch (e) { caret = null; }
        var parent = input.parentNode;
        parent.insertBefore(wrap, input);
        wrap.appendChild(input);
        wrap.appendChild(button);
        if (hadFocus) {
            input.focus();
            if (caret && caret[0] !== null) { try { input.setSelectionRange(caret[0], caret[1]); } catch (e) { /* not a text field */ } }
        }
        return button;
    }

    function scan(node) {
        if (!node || !node.querySelectorAll) return;
        if (node.matches && node.matches('input[type="password"]')) attach(node);
        var inputs = node.querySelectorAll('input[type="password"]');
        for (var i = 0; i < inputs.length; i++) attach(inputs[i]);
    }

    function start() {
        if (typeof document === 'undefined' || !document.body) return;
        scan(document.body);
        if (typeof MutationObserver === 'undefined') return;
        new MutationObserver(function (records) {
            for (var r = 0; r < records.length; r++) {
                var added = records[r].addedNodes;
                for (var i = 0; i < added.length; i++) scan(added[i]);
            }
        }).observe(document.body, { childList: true, subtree: true });
    }

    var api = { toggle: toggle, attach: attach, scan: scan, start: start };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    root.PasswordEye = api;
    if (typeof document !== 'undefined') {
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
        else start();
    }
})(typeof window !== 'undefined' ? window : this);
