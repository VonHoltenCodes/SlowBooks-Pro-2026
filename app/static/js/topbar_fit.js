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
 * step at a time (style.css "TOP BAR — folding"):
 *   1. the clock and the brand's year go, the spacing tightens
 *      (tb-fold-1);
 *   2. the Fast Rack's buttons (#912; fast_rack.js) move into More one at a
 *      time, the last on the rack first (fr-folded on the button), so the
 *      ones a person put first stay longest; as many steps as the rack has
 *      buttons, none when it has none;
 *   3. the sign-in name, About and Contact support move into More
 *      (tb-fold-who);
 *   4. the wordmark goes (the sidebar shows it) (tb-fold-brand);
 *   5. the theme switch moves into More (tb-fold-theme).
 * The bar's data-fold is the number of steps taken (1, then 1 + each rack
 * button folded, then the three after). More is in the bar from the first
 * thing it holds; it lists, when opened, every control the bar has folded
 * away, the rack's by their names in the rack's order, and the name signed
 * in, so nothing the bar offers is ever out of reach, and last
 * Customize Fast Rack…, which opens Settings at the rack's block. Sign Out,
 * Back, the search box and the skip link never fold.
 *
 * Measured again when the bar's size changes (the window, the text size)
 * and when what is in it changes (the company's name, the sign-in name,
 * a role hiding buttons, the Fast Rack drawn again), before the next
 * paint, so no fold is ever seen.
 */
(function () {
    'use strict';

    // What More can hold after the rack's buttons, in the bar's order:
    // [selector, its label there]
    var FOLDABLE_REST = [
        ['#theme-toggle', 'theme'],
        ['#about-btn', null],
        ['#support-btn', null],
    ];

    var bar = document.getElementById('topbar');
    var more = document.getElementById('topbar-more');
    var menu = document.getElementById('topbar-more-menu');
    var rack = document.getElementById('fast-rack');
    if (!bar || !more || !menu) return;

    var level = 0;
    var fitting = false;
    var watcher = null;

    // The rack's buttons, in its order (what the role leaves of them)
    function rackButtons() {
        if (!rack) return [];
        return Array.prototype.filter.call(rack.querySelectorAll('.tb-btn'), function (b) {
            return !b.hidden && !b.classList.contains('hidden');
        });
    }

    // 1 tightens, one step per rack button, then the three after it
    function steps() { return 1 + rackButtons().length + 3; }

    function setLevel(n) {
        var buttons = rackButtons();
        var r = buttons.length;
        var gone = Math.max(0, Math.min(r, n - 1));   // rack buttons folded, from the end
        bar.classList.toggle('tb-fold-1', n >= 1);
        buttons.forEach(function (b, i) { b.classList.toggle('fr-folded', i >= r - gone); });
        if (rack) rack.classList.toggle('fr-all-folded', r > 0 && gone === r);
        bar.classList.toggle('tb-fold-who', n >= r + 2);
        bar.classList.toggle('tb-fold-brand', n >= r + 3);
        bar.classList.toggle('tb-fold-theme', n >= r + 4);
        more.hidden = n < 2;
    }

    function overflows() {
        return bar.scrollWidth > bar.clientWidth;
    }

    function fit() {
        if (fitting) return;
        fitting = true;
        try {
            var n = 0;
            var last = steps();
            setLevel(0);
            while (n < last && overflows()) setLevel(++n);
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
        var foldable = rackButtons().map(function (b) { return [b, null]; })
            .concat(FOLDABLE_REST.map(function (entry) {
                return [document.querySelector(entry[0]), entry[1]];
            }));
        foldable.forEach(function (entry) {
            var el = entry[0];
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
        // last: where the rack is arranged (#912)
        if (window.FastRack) {
            var custom = document.createElement('button');
            custom.type = 'button';
            custom.className = 'tb-more-item tb-more-customize';
            custom.textContent = 'Customize Fast Rack…';
            custom.addEventListener('click', function () {
                close(false);
                window.FastRack.customize();
            });
            menu.appendChild(custom);
        }
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
