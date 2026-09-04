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

    // --- Splash dismiss ----------------------------------------------------
    const dismiss = document.getElementById('splash-dismiss');
    if (dismiss) {
        dismiss.addEventListener('click', () => {
            document.getElementById('splash').classList.add('hidden');
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
        } catch (e) { /* splash stays as shipped */ }
    })();

    // --- About / theme / modal close --------------------------------------
    const about = document.getElementById('about-btn');
    if (about) about.addEventListener('click', () => window.App && App.showAbout && App.showAbout());

    const theme = document.getElementById('theme-toggle');
    if (theme) theme.addEventListener('click', () => window.App && App.toggleTheme && App.toggleTheme());

    const closeBtn = document.getElementById('modal-close-btn');
    if (closeBtn) closeBtn.addEventListener('click', () => typeof closeModal === 'function' && closeModal());

    // Sign out — POSTs to /api/auth/logout, then reloads to splash.
    // Auth is session-cookie based; the server clears the cookie and the
    // reload bounces the user back to the login screen.
    const logout = document.getElementById('logout-btn');
    if (logout) logout.addEventListener('click', async () => {
        if (!confirm('Sign out of Slowbooks?')) return;
        try {
            await API.post('/auth/logout', {});
        } catch (_err) {
            // Even on error we want to clear the local UI — the cookie may
            // already be expired; just reload.
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
