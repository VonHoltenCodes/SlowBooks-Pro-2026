/**
 * Static-page event wiring.
 *
 * Used to be inline onclick="..." in index.html. Pulled out so the static
 * shell page doesn't depend on CSP `'unsafe-inline'`. The JS-rendered
 * modals across the rest of the app still emit inline handlers (call it
 * a known migration debt) and we keep `'unsafe-inline'` in the policy
 * for them — see docs/security-hardening.md for the story.
 *
 * This file is loaded AFTER app.js so all the page modules (App,
 * CustomersPage, InvoicesPage, PaymentsPage) are already defined.
 */
(function () {
    'use strict';

    // --- Splash: the terms, once per license version, then OK ----------------
    // LICENSE section 13: the desktop app displays the short terms before
    // first use and records that they were shown. A file on GitHub is not
    // a contract with someone who only ever double-clicked the installer.
    // Recorded per browser profile — the desktop app's WebView profile is
    // persistent, so once per install; Server Edition users see it once
    // per browser. Not a gate on every launch: once is the point.
    const LICENSE_VERSION = '2.0';
    const ACK_KEY = 'slowbooks.license_ack';
    // The release notes the same way (#220): once per version, per browser.
    // The splash opened on every load, because the notes never recorded
    // that they'd been seen; now it opens at start only while something on
    // it is new, the terms or the notes. About shows it in full any time.
    const NOTES_KEY = 'slowbooks.whatsnew_ack';
    const splash = document.getElementById('splash');
    const dismiss = document.getElementById('splash-dismiss');
    const terms = document.getElementById('splash-terms');
    let acknowledged = false;
    try { acknowledged = localStorage.getItem(ACK_KEY) === LICENSE_VERSION; } catch (e) { /* no storage: show it */ }
    let notesVersion = null;  // the version whose notes are showing, once known
    let dismissed = false;    // OK was clicked before the notes arrived
    if (terms && !acknowledged) {
        terms.hidden = false;
        const v = document.getElementById('splash-license-version');
        if (v) v.textContent = LICENSE_VERSION;
        if (dismiss) dismiss.textContent = 'I understand';
    }
    if (splash && acknowledged) {
        // Nothing to acknowledge yet; the notes, once loaded, open it if
        // they're new. Hidden before first paint, so it doesn't flash.
        splash.classList.add('hidden');
    }
    if (dismiss) {
        dismiss.addEventListener('click', () => {
            if (terms && !acknowledged) {
                try { localStorage.setItem(ACK_KEY, LICENSE_VERSION); } catch (e) { /* shown again next time; acceptable */ }
            }
            if (notesVersion) {
                try { localStorage.setItem(NOTES_KEY, notesVersion); } catch (e) { /* shown again next time; acceptable */ }
            }
            dismissed = true;
            splash.classList.add('hidden');
        });
    }

    // --- What's new on the splash ------------------------------------------
    // /static/whats-new.json ships with the build (edited at release time,
    // see docs/release-checklist.md); /health gives the running version.
    // Both are public, so this works before login and in the About dialog.
    (async () => {
        try {
            const [notesRes, healthRes] = await Promise.all([
                fetch('/static/whats-new.json', { credentials: 'same-origin' }),
                fetch('/health', { credentials: 'same-origin' }),
            ]);
            if (!notesRes.ok) return;
            const notes = await notesRes.json();
            const version = healthRes.ok ? (await healthRes.json()).version : null;
            const key = (version && notes[version]) ? version : Object.keys(notes)[0];
            const entry = notes[key];
            if (!entry || !entry.items || !entry.items.length) return;
            const box = document.getElementById('splash-whatsnew');
            const title = document.getElementById('splash-whatsnew-title');
            const list = document.getElementById('splash-whatsnew-list');
            if (!box || !title || !list) return;
            title.textContent = `What's new in ${key}${entry.title ? ' — ' + entry.title : ''}`;
            list.innerHTML = '';
            entry.items.forEach(text => {
                const li = document.createElement('li');
                li.textContent = text;
                list.appendChild(li);
            });
            box.hidden = false;
            notesVersion = key;
            let seen = false;
            try { seen = localStorage.getItem(NOTES_KEY) === key; } catch (e) { /* no storage: show it */ }
            if (seen) return;  // the splash stays shut (About still shows them)
            // New notes: open the splash at start, unless OK was already
            // clicked (the terms had it open), in which case they count as
            // seen with that click.
            if (dismissed) {
                try { localStorage.setItem(NOTES_KEY, key); } catch (e) { /* shown again next time; acceptable */ }
            } else if (splash) {
                splash.classList.remove('hidden');
            }
        } catch (e) { /* splash stays as shipped */ }
    })();

    // --- About / theme / modal close --------------------------------------
    const about = document.getElementById('about-btn');
    if (about) about.addEventListener('click', () => window.App && App.showAbout && App.showAbout());

    // Contact support — the mailto form (support.js). Beside About in the top
    // bar, and a line on the About/splash dialog.
    const support = document.getElementById('support-btn');
    if (support) support.addEventListener('click', () => window.SupportPage && SupportPage.open());
    const supportLink = document.getElementById('splash-support-link');
    if (supportLink) supportLink.addEventListener('click', (e) => {
        e.preventDefault();
        if (splash) splash.classList.add('hidden');
        if (window.SupportPage) SupportPage.open();
    });

    const theme = document.getElementById('theme-toggle');
    if (theme) theme.addEventListener('click', () => window.App && App.toggleTheme && App.toggleTheme());

    // Back, within the app (NEW-30)
    const back = document.getElementById('back-btn');
    if (back) back.addEventListener('click', () => window.App && App.goBack && App.goBack());

    const closeBtn = document.getElementById('modal-close-btn');
    if (closeBtn) closeBtn.addEventListener('click', () => typeof closeModal === 'function' && closeModal());

    // Skip to main content: focus goes to the page's content itself, and
    // the address stays. Followed as a link, "#page-content" became the
    // address, which the router read as a page: "Page not found" (review
    // of NEW-43, 2.22.0). The content takes focus as a place (tabindex
    // -1: no ring, and the next Tab is its first control) only while the
    // skip link puts it there: standing, it made a mouse click on any blank
    // part of a page focus the content, so the next Tab went to the page's
    // first control and scrolled back to the top (2.22.0 review).
    const skip = document.getElementById('skip-link');
    const main = document.getElementById('page-content');
    if (skip && main) skip.addEventListener('click', (e) => {
        e.preventDefault();
        main.setAttribute('tabindex', '-1');
        main.addEventListener('blur', () => main.removeAttribute('tabindex'), { once: true });
        main.focus();
    });

    // Sign out — POSTs to /api/auth/logout, then goes back to where you
    // choose: in the native desktop window, the company picker (the
    // launcher stops this company's server and reloads the picker page);
    // in a browser, the sign-in screen, which lists the users on a
    // multi-user install. It used to reload the same company's password
    // prompt, and the only way anywhere else was to quit the app.
    const logout = document.getElementById('logout-btn');
    if (logout) logout.addEventListener('click', async () => {
        if (!confirm('Sign out of Slowbooks?')) return;
        try {
            await API.post('/auth/logout', {});
        } catch (_err) {
            // Even on error we want to clear the local UI — the cookie may
            // already be expired; carry on.
        }
        const shell = window.pywebview && window.pywebview.api;
        if (shell && typeof shell.show_picker === 'function') {
            shell.show_picker();
            return;
        }
        window.location.reload();
    });

    // --- Global search ----------------------------------------------------
    const search = document.getElementById('global-search');
    if (search) {
        search.addEventListener('input', e => window.App && App.globalSearch && App.globalSearch(e.target.value));
        search.addEventListener('keydown', e => {
            if (e.key === 'Escape') {
                e.target.value = '';
                window.App && App.globalSearch && App.globalSearch('');
            }
            // ArrowDown: on to the first hit
            if (e.key === 'ArrowDown' && window.App && App.searchFocusFirst && App.searchFocusFirst()) e.preventDefault();
        });
    }

    // --- Toolbar buttons: data-nav vs data-action -------------------------
    // <button data-nav="#/foo">     -> App.navigate('#/foo')
    // <button data-action="X.foo">  -> X.foo()  (calls a no-arg function by dotted path)
    function callByPath(path) {
        const parts = path.split('.');
        let obj = window;
        for (const p of parts.slice(0, -1)) {
            if (!obj) return;
            obj = obj[p];
        }
        const fn = obj && obj[parts[parts.length - 1]];
        if (typeof fn === 'function') fn.call(obj);
    }
    document.querySelectorAll('[data-nav]').forEach(btn => {
        btn.addEventListener('click', () => window.App && App.navigate && App.navigate(btn.dataset.nav));
    });
    document.querySelectorAll('[data-action]').forEach(btn => {
        btn.addEventListener('click', () => callByPath(btn.dataset.action));
    });
})();
