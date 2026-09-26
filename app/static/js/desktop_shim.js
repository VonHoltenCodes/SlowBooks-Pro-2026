/**
 * Desktop (pywebview) shim — only active inside the native desktop window.
 *
 * Problem this solves: the app opens print previews, PDFs, CSV exports and
 * attachments with window.open()/target="_blank". In a browser install the
 * new tab shares the session cookie and everything works. In the desktop
 * shell, pywebview routes "new window" requests to the SYSTEM browser,
 * which has a separate cookie jar — so those URLs came back
 * {"detail":"Not authenticated"}.
 *
 * Three things were tried and rejected before this design:
 *   1. An <iframe> overlay — blocked outright by this app's own
 *      Content-Security-Policy: frame-ancestors 'none' ("This content is
 *      blocked. Contact the site owner to fix the issue.").
 *   2. Handing the raw URL to Python to open in a second native pywebview
 *      window — field-tested and still came back "Not authenticated".
 *      pywebview windows in one process nominally share a WebView2
 *      profile, but a fresh top-level browsing context evidently doesn't
 *      reliably carry the first window's session cookie in practice.
 *   3. Fetching a Content-Disposition: attachment response from this
 *      page's own JS and saving it via createObjectURL + <a download> —
 *      field-tested and the fetch() itself failed ("Failed to fetch")
 *      even though the server logged a normal 200 for the same request.
 *      WebView2 (with ALLOW_DOWNLOADS on, needed for the final save step
 *      below) intercepts "attachment" responses as a native download at
 *      the network layer, regardless of whether the request came from a
 *      real click or a script's fetch() call — the response never reaches
 *      the page's fetch() promise.
 *
 * What actually works: fetch the URL from THIS page's own JavaScript —
 * exactly like the app's normal API calls, which is why those always
 * succeed — then hand the already-fetched content over to Python to
 * display. No second authenticated network request is ever made.
 *   - Every fetch here sends the X-Slowbooks-Desktop header, and the server
 *     answers Content-Disposition: inline instead of attachment for it
 *     (app/main.py), so the fetch() completes normally. A file to save —
 *     anything that is not a page or a PDF: a CSV or IIF export, an
 *     attachment — goes to save_document_file(), which writes it to
 *     Documents/SlowBooks Pro/Reports and says where; createObjectURL +
 *     <a download> is the fallback without the bridge.
 *   - Settings → Backups "Download" skips HTTP entirely — see the
 *     save_backup_file() branch in the click handler below. The backup
 *     file (application/octet-stream, never browser-renderable, so
 *     "inline" wouldn't help) is read straight off disk by Python and
 *     copied to the user's Downloads folder, since the desktop app and
 *     the file are already on the same machine.
 *   - An HTML response (print-preview pages) is handed to
 *     open_document_html(), which opens it in a new native window via
 *     pywebview's html= parameter.
 *   - A PDF response is base64-encoded and handed to open_document_pdf(),
 *     which saves it under Documents/SlowBooks Pro and opens it from there
 *     (file:// needs no auth at all) in a window of its own, under a
 *     toolbar with Open in <the PDF app> and Show in folder.
 *
 * In a normal browser this file is a no-op, but for
 * window.SlowbooksDesktop.saveFile(), which answers false there.
 */
(function () {
    'use strict';

    // pywebview injects window.pywebview once the window is ready; by the
    // time a user can click anything it's present, so no readiness gating
    // is needed at call time -- only checked defensively below.
    function inDesktopShell() {
        return typeof window.pywebview !== 'undefined'
            || navigator.userAgent.includes('WebView2');
    }

    // The employee self-service portal is a separate cookie-based site meant
    // for the employee's OWN browser. Fetching it here and showing the HTML in
    // a document window (the path every other same-origin _blank link takes)
    // rendered a dashboard whose tabs had no origin to navigate against —
    // "the portal only works on the first tab". Leave those links alone so
    // pywebview hands them to the system browser, which claims the token and
    // gets its own portal cookie.
    function isPortalUrl(url) {
        try {
            return new URL(url, window.location.href).pathname.startsWith('/portal/');
        } catch (e) {
            return false;
        }
    }

    function isSameOrigin(url) {
        try {
            const u = new URL(url, window.location.href);
            return u.origin === window.location.origin;
        } catch (e) {
            return false;
        }
    }

    function filenameFromDisposition(disposition, fallback) {
        if (!disposition) return fallback;
        const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
        return match ? decodeURIComponent(match[1]) : fallback;
    }

    function arrayBufferToBase64(buffer) {
        let binary = '';
        const bytes = new Uint8Array(buffer);
        const chunkSize = 32768; // avoid call-stack limits on String.fromCharCode
        for (let i = 0; i < bytes.length; i += chunkSize) {
            binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
        }
        return btoa(binary);
    }

    function saveBlob(blob, filename) {
        const objectUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = objectUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(objectUrl); }, 30000);
    }

    // Save a file this page already holds through the native bridge: it goes
    // to Documents/SlowBooks Pro/Reports, and a toast says where, with Show
    // in folder. Resolves false when there is no bridge (a browser), so the
    // caller downloads the file itself. Pages that fetch a file themselves
    // (the IIF export) use it as window.SlowbooksDesktop.saveFile.
    async function saveFile(blob, name) {
        const api = window.pywebview && window.pywebview.api;
        if (!api || !api.save_document_file) return false;
        const buffer = await blob.arrayBuffer();
        const result = await api.save_document_file(name, arrayBufferToBase64(buffer));
        if (result && result.success && result.path) {
            const message = result.note || ('Saved to ' + result.path);
            if (typeof toastAction === 'function') {
                toastAction(message, 'Show in folder', function () {
                    api.reveal_path(result.path);
                }, result.note ? 20000 : 10000);
            } else if (typeof toast === 'function') {
                toast(message);
            }
        } else if (typeof toast === 'function') {
            toast('Could not save the file: ' + ((result && result.error) || 'unknown error'), 'error');
        }
        return true;
    }
    window.SlowbooksDesktop = { saveFile: saveFile };

    // Backups download URLs are handled by save_backup_file() instead of a
    // fetch (see module docstring) -- matched here so the click handler can
    // route them differently before falling into the generic fetch path.
    function backupFilenameFromUrl(url) {
        const m = /\/api\/backups\/download\/([^/?#]+)/.exec(url);
        return m ? decodeURIComponent(m[1]) : null;
    }

    async function openSameOriginUrl(url) {
        let response;
        try {
            response = await fetch(url, {
                credentials: 'same-origin',
                headers: { 'X-Slowbooks-Desktop': '1' },
            });
        } catch (e) {
            if (typeof toast === 'function') toast('Could not load the document: ' + e.message, 'error');
            return;
        }
        if (!response.ok) {
            // api.js's sentence for the refusal: a 422's list of entries
            // printed as "[object Object]" here.
            const message = typeof API !== 'undefined' && API.responseError
                ? await API.responseError(response, 'Could not load the document')
                : 'Could not load the document (HTTP ' + response.status + ')';
            if (typeof toast === 'function') toast(message, 'error');
            return;
        }

        const disposition = response.headers.get('Content-Disposition') || '';
        const contentType = (response.headers.get('Content-Type') || '').toLowerCase();
        const fallbackName = url.split('/').pop().split('?')[0] || 'download';

        // A page (print preview) opens in a window and a PDF in the viewer;
        // anything else is a file to save — a CSV or IIF export, an
        // attachment (a scanned receipt, a spreadsheet). Every request from
        // here carries the desktop header, so the server answers "inline"
        // (app/main.py) and a file to save can't be told by an "attachment"
        // disposition: only CSVs were, by type, and an image or an .iif fell
        // through to the page branch and showed as garbage text.
        const isPdf = contentType.includes('pdf');
        const isPage = contentType.includes('text/html');
        if (/attachment/i.test(disposition) || (!isPdf && !isPage)) {
            const name = filenameFromDisposition(disposition, fallbackName);
            const blob = await response.blob();
            // Prefer the bridge: it writes to Documents/SlowBooks Pro/Reports
            // and says where, exactly like Save PDF. A blob <a download> is
            // the fallback for a shell without the bridge.
            if (!(await saveFile(blob, name))) saveBlob(blob, name);
            return;
        }

        if (isPdf) {
            const buffer = await response.arrayBuffer();
            const base64 = arrayBufferToBase64(buffer);
            const title = filenameFromDisposition(disposition, fallbackName);
            if (window.pywebview && window.pywebview.api && window.pywebview.api.open_document_pdf) {
                const result = await window.pywebview.api.open_document_pdf(title, base64);
                if (result && result.success && result.path) {
                    // The viewer window has no address bar; tell the user where
                    // the file actually is and offer to open that folder. A
                    // `note` means Documents refused the write and the file
                    // went to the app's data folder instead: say so, at length.
                    const message = result.note || ('Saved to ' + result.path);
                    if (typeof toastAction === 'function') {
                        toastAction(message, 'Show in folder', function () {
                            window.pywebview.api.reveal_path(result.path);
                        }, result.note ? 20000 : 10000);
                    } else if (typeof toast === 'function') {
                        toast(message);
                    }
                } else if (result && result.error && typeof toast === 'function') {
                    toast('Could not save the PDF: ' + result.error, 'error');
                }
            } else {
                bridgeMissing('save the PDF');
            }
            return;
        }

        // Anything else (print-preview HTML, etc.)
        const html = await response.text();
        if (window.pywebview && window.pywebview.api && window.pywebview.api.open_document_html) {
            await window.pywebview.api.open_document_html('SlowBooks Pro 2026', html);
        } else {
            bridgeMissing('open the document');
        }
    }

    // A branch that can do nothing must say so. "Click does nothing" cost a
    // full gate round to diagnose (2.9.0, macOS): the bridge object existed
    // but had no methods, and both branches above returned in silence.
    function bridgeMissing(what) {
        const msg = 'The desktop viewer is unavailable: the native bridge (window.pywebview.api) '
            + 'has no methods, so SlowBooks cannot ' + what + '. Restart the app; if it persists, '
            + 'report it with the launcher log.';
        console.error(msg);
        if (typeof toast === 'function') toast(msg, 'error');
    }

    // Liveness check at startup: once pywebview reports ready (or after a
    // grace period if it never does), the api object must carry methods.
    // An empty api is exactly the failure mode that hid for months.
    let bridgeChecked = false;
    function checkBridge() {
        if (bridgeChecked || !inDesktopShell()) return;
        bridgeChecked = true;
        const api = window.pywebview && window.pywebview.api;
        const methods = api ? Object.keys(api).length : 0;
        if (methods === 0) {
            bridgeMissing('save or open documents');
        } else if (window.console && console.debug) {
            console.debug('desktop bridge ready: ' + methods + ' methods');
        }
    }
    window.addEventListener('pywebviewready', checkBridge);
    setTimeout(checkBridge, 8000);

    const realOpen = window.open.bind(window);
    window.open = function (url, target, features) {
        if (url && inDesktopShell() && isSameOrigin(url) && !isPortalUrl(url)) {
            openSameOriginUrl(url);
            return null;
        }
        return realOpen(url, target, features);
    };

    // Same treatment for <a target="_blank"> anchors (attachment downloads,
    // employee documents, print-preview links) AND plain <a download>
    // anchors (CSV exports, Settings -> Backups "Download"). Field test
    // showed the latter were NOT handled correctly by WebView2's native
    // download flow -- ALLOW_DOWNLOADS routes them through a fresh request
    // that doesn't carry the session cookie, same underlying problem as
    // the PDF/print-preview 401 this file was written to fix, just via a
    // different link pattern. Both go through the same fetch-then-save
    // path below.
    document.addEventListener('click', function (e) {
        if (!inDesktopShell()) return;
        const a = e.target && e.target.closest
            ? e.target.closest('a[target="_blank"], a[download]')
            : null;
        // A blob: link is a file the page already made (saveBlob's own
        // fallback): the web view downloads it; fetching it back here went
        // round in a circle.
        if (!a || !a.href || /^blob:/i.test(a.href) || !isSameOrigin(a.href)) return;
        if (isPortalUrl(a.href)) {
            // system browser via the launcher bridge when it's there;
            // otherwise fall through to WebView2's new-window handling,
            // which pywebview also routes to the system browser.
            if (window.pywebview && window.pywebview.api && window.pywebview.api.open_external) {
                e.preventDefault();
                window.pywebview.api.open_external(a.href);
            }
            return;
        }
        e.preventDefault();

        const backupFilename = backupFilenameFromUrl(a.href);
        if (backupFilename && window.pywebview && window.pywebview.api
                && window.pywebview.api.save_backup_file) {
            window.pywebview.api.save_backup_file(backupFilename).then(function (result) {
                if (result && result.success) {
                    if (typeof toast === 'function') toast('Saved to Downloads: ' + result.path);
                } else if (typeof toast === 'function') {
                    toast((result && result.error) || 'Could not save the backup', 'error');
                }
            });
            return;
        }

        openSameOriginUrl(a.href);
    }, true);
})();
