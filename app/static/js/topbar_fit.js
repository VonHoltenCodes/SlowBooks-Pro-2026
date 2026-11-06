/**
 * The top bar fits its window (W-126, 2.31.8).
 *
 * The bar's width rules (style.css, after .topbar-clock) read the window,
 * and the window is not the page once the Text size zooms it: at 150% a
 * 900px window is a 600px page. Copies of those rules per size could not
 * know a long sign-in name either ("Second Admin · admin" took the room
 * Sign Out needed), so at 900 x 600 the bar ran past the window's edge and
 * the page scrolled sideways. Container queries would answer it, but they
 * are newer than the oldest WebView2 the desktop app opens on
 * (tests/test_webview2_floor.py), so the bar is measured instead: one
 * mechanism, at any size, with any name, in any engine's fonts.
 *
 * The bar starts whole and, while it is wider than the window, folds one
 * step at a time (the tb-fold-N classes, style.css "TOP BAR — folding"):
 *   1. the clock and the brand's year go, the spacing tightens;
 *   2. Quick Entry and Bill Entry go (both on the sidebar);
 *   3. New Customer, Create Invoice and Receive Payment go (Alt+N, Alt+P,
 *      and on the sidebar's pages);
 *   4. the sign-in name, About and Contact support move into More;
 *   5. Home and Reports move into More (Dashboard and Reports are also on
 *      the sidebar);
 *   6. the wordmark goes (the sidebar shows it);
 *   7. the theme switch moves into More.
 * More lists, when opened, every control the bar has folded away and the
 * name signed in, so nothing the bar offers is ever out of reach. Sign Out,
 * Back, the search box and the skip link never fold.
 *
 * Measured again when the bar's size changes (the window, the text size)
 * and when what is in it changes (the company's name, the sign-in name,
 * a role hiding buttons), before the next paint, so no fold is ever seen.
 */
(function () {
    'use strict';

    var STEPS = 7;
    var MORE_FROM = 4;   // the step from which More is in the bar

    // What More can hold, in the bar's order: [selector, its label there]
    var FOLDABLE = [
        ['#topbar .tb-btn[data-nav="#/"]', null],
        ['#topbar .tb-btn[data-action="CustomersPage.showForm"]', null],
        ['#topbar .tb-btn[data-action="InvoicesPage.showForm"]', null],
        ['#topbar .tb-btn[data-action="PaymentsPage.showForm"]', null],
        ['#topbar .tb-btn[data-nav="#/quick-entry"]', null],
        ['#topbar .tb-btn[data-nav="#/bill-entry"]', null],
        ['#topbar .tb-btn[data-nav="#/reports"]', null],
        ['#theme-toggle', 'theme'],
        ['#about-btn', null],
        ['#support-btn', null],
    ];

    var bar = document.getElementById('topbar');
    var more = document.getElementById('topbar-more');
    var menu = document.getElementById('topbar-more-menu');
    if (!bar || !more || !menu) return;

    var level = 0;
    var fitting = false;
    var watcher = null;

    function setLevel(n) {
        for (var i = 1; i <= STEPS; i++) bar.classList.toggle('tb-fold-' + i, i <= n);
        more.hidden = n < MORE_FROM;
    }

    function overflows() {
        return bar.scrollWidth > bar.clientWidth;
    }

    function fit() {
        if (fitting) return;
        fitting = true;
        try {
            var n = 0;
            setLevel(0);
            while (n < STEPS && overflows()) setLevel(++n);
            level = n;
            bar.setAttribute('data-fold', String(n));
            if (more.hidden) close(false);
        } finally {
            if (watcher) watcher.takeRecords();   // our own changes, not news
            fitting = false;
        }
    }

    // Shown by the page (not hidden for this role or this install) but
    // folded out of the bar right now.
    function folded(el) {
        if (!el || el.hidden || el.classList.contains('hidden')) return false;
        return el.offsetWidth === 0 && el.offsetHeight === 0;
    }

    function labelOf(el, kind) {
        if (kind === 'theme') {
            return document.documentElement.getAttribute('data-theme') === 'dark'
                ? 'Light mode' : 'Dark mode';
        }
        return (el.getAttribute('aria-label') || el.textContent || el.title || '').trim();
    }

    function fill() {
        menu.textContent = '';
        var chip = document.getElementById('user-chip');
        if (chip && folded(chip) && chip.textContent.trim()) {
            var who = document.createElement('p');
            who.className = 'tb-more-who';
            who.textContent = 'Signed in as ' + chip.textContent.trim();
            menu.appendChild(who);
        }
        FOLDABLE.forEach(function (entry) {
            var el = document.querySelector(entry[0]);
            if (!folded(el)) return;
            var item = document.createElement('button');
            item.type = 'button';
            item.className = 'tb-more-item';
            item.textContent = labelOf(el, entry[1]);
            var keys = el.getAttribute('aria-keyshortcuts');
            if (keys) item.setAttribute('aria-keyshortcuts', keys);
            if (el.title && el.title !== item.textContent) item.title = el.title;
            item.addEventListener('click', function () {
                close(false);
                el.click();
            });
            menu.appendChild(item);
        });
    }

    function open() {
        fill();
        menu.hidden = false;
        more.setAttribute('aria-expanded', 'true');
        var first = menu.querySelector('button');
        if (first) first.focus();
    }

    function close(refocus) {
        if (menu.hidden) return;
        menu.hidden = true;
        more.setAttribute('aria-expanded', 'false');
        if (refocus) more.focus();
    }

    more.addEventListener('click', function () {
        if (menu.hidden) open(); else close(true);
    });
    // Escape closes it and goes back to More; so does Tab past its end
    menu.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true); }
    });
    more.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !menu.hidden) { e.preventDefault(); e.stopPropagation(); close(true); }
    });
    document.addEventListener('focusin', function (e) {
        if (!menu.hidden && !menu.contains(e.target) && e.target !== more) close(false);
    });
    document.addEventListener('mousedown', function (e) {
        if (!menu.hidden && !menu.contains(e.target) && !more.contains(e.target)) close(false);
    });

    if (typeof ResizeObserver === 'function') new ResizeObserver(fit).observe(bar);
    else window.addEventListener('resize', fit);
    if (typeof MutationObserver === 'function') {
        // the open list's own changes are not the bar's
        watcher = new MutationObserver(function (records) {
            for (var i = 0; i < records.length; i++) {
                if (!menu.contains(records[i].target)) { fit(); return; }
            }
        });
        watcher.observe(bar, {
            childList: true, subtree: true, characterData: true,
            attributes: true, attributeFilter: ['class', 'hidden', 'style'],
        });
    }
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);
    fit();

    window.SlowbooksTopbar = { fit: fit, level: function () { return level; } };
}());
