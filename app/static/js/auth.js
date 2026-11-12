/**
 * Slowbooks Pro 2026 — Auth overlay (Phase 9.7)
 *
 * Injects a full-screen login/setup overlay when the API returns 401, or
 * when /api/auth/status reports first-time setup is needed.
 *
 * Two views:
 *   - login: password only
 *   - setup: full first-run wizard (company info, operator name/email,
 *            defaults, password)
 *
 * The initial view is decided by /api/auth/status. Race-safe: a flurry of
 * 401s from parallel API calls can only ever paint one overlay.
 */
(function () {
    "use strict";

    const AUTH_STATUS_URL = "/api/auth/status";
    const AUTH_SETUP_URL = "/api/auth/setup";
    const AUTH_LOGIN_URL = "/api/auth/login";
    const AUTH_COMPANY_URL = "/api/auth/company";
    // The username a company switch carried over (#358): the sign-in screen
    // of the company switched to has it chosen already.
    const SWITCH_USERNAME_KEY = "slowbooks_switch_username";

    const OVERLAY_ID = "auth-overlay";
    const MIN_PASSWORD_LEN = 8;

    // ----- network ---------------------------------------------------------

    async function checkStatus() {
        try {
            const res = await fetch(AUTH_STATUS_URL, {
                credentials: "same-origin",
            });
            if (!res.ok) return { authenticated: false, setup_needed: false };
            return await res.json();
        } catch (e) {
            return { authenticated: false, setup_needed: false };
        }
    }

    async function postJSON(url, body) {
        const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            let detail = "Request failed";
            try {
                const data = await res.json();
                // A 422 is a list of entries; API.errorMessage turns them into
                // sentences ("Company name must be 200 characters or fewer.")
                // where a bare list used to print "[object Object]".
                // (API is a top-level const in api.js: a shared global
                // binding, not a property of window.)
                detail = typeof API !== "undefined" && typeof API.errorMessage === "function"
                    ? API.errorMessage(data.detail, detail)
                    : (typeof data.detail === "string" ? data.detail : detail);
            } catch (e) {}
            const err = new Error(detail);
            err.status = res.status;
            throw err;
        }
        return res.json();
    }

    // ----- shared chrome ---------------------------------------------------

    function buildShell(innerHTML) {
        const root = document.createElement("div");
        root.id = OVERLAY_ID;
        root.setAttribute(
            "style",
            "position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,0.85);" +
                "display:flex;align-items:flex-start;justify-content:center;" +
                // the margins shrink with Text size (--ui-scale, style.css)
                // so the form, which grows, still fits the window at 150%
                // with Forgot your password? open (#813)
                "padding:calc(48px / var(--ui-scale, 1)) 16px;overflow-y:auto;" +
                "font-family:system-ui,-apple-system,Segoe UI,sans-serif;"
        );
        root.innerHTML = innerHTML;
        return root;
    }

    function inputStyle() {
        return (
            "width:100%;padding:9px 11px;font-size:14px;border:1px solid #ccc;" +
            "border-radius:4px;box-sizing:border-box;"
        );
    }

    function labelStyle() {
        return "display:block;font-size:12px;color:#555;margin:10px 0 4px;font-weight:600;";
    }

    function sectionHeader(text) {
        return (
            '<div style="margin:18px 0 4px;padding-bottom:4px;border-bottom:1px solid #eee;' +
            'font-size:11px;text-transform:uppercase;letter-spacing:0.05em;color:#767676;font-weight:700;">' +
            text +
            "</div>"
        );
    }

    function field(id, label, opts) {
        opts = opts || {};
        const type = opts.type || "text";
        // `required` triggers HTML5 validation; `aria-required` is the
        // semantic flag screen readers consume. Both belong on every
        // required input — keep them in lockstep.
        const required = opts.required ? ' required aria-required="true"' : "";
        const minlength = opts.minlength ? ' minlength="' + opts.minlength + '"' : "";
        const placeholder = opts.placeholder
            ? ' placeholder="' + opts.placeholder + '"'
            : "";
        const autocomplete = opts.autocomplete
            ? ' autocomplete="' + opts.autocomplete + '"'
            : "";
        const value = opts.value ? ' value="' + escapeText(opts.value) + '"' : "";
        // Asterisk: bold + larger so a quick scan catches it. aria-hidden
        // because the same info is conveyed by aria-required on the input.
        const asterisk = opts.required
            ? ' <span aria-hidden="true" style="color:#a4242b;font-weight:700;font-size:15px;">*</span>'
            : "";
        return (
            '<label for="' +
            id +
            '" style="' +
            labelStyle() +
            '">' +
            label +
            asterisk +
            "</label>" +
            '<input id="' +
            id +
            '" name="' +
            id +
            '" type="' +
            type +
            '"' +
            required +
            minlength +
            placeholder +
            autocomplete +
            value +
            ' style="' +
            inputStyle() +
            '">'
        );
    }

    function escapeText(text) {
        return String(text)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function row(...cells) {
        return (
            '<div style="display:grid;grid-template-columns:repeat(' +
            cells.length +
            ',1fr);gap:10px;">' +
            cells.map((c) => "<div>" + c + "</div>").join("") +
            "</div>"
        );
    }

    function linkButtonStyle() {
        return (
            "display:inline;background:none;border:0;padding:0;color:#0066cc;" +
            "font-size:13px;cursor:pointer;text-decoration:underline;font-family:inherit;"
        );
    }

    function primaryButtonStyle() {
        return (
            "width:100%;padding:11px;font-size:15px;font-weight:600;" +
            "background:#0066cc;color:#fff;border:0;border-radius:4px;cursor:pointer;"
        );
    }

    function errorBoxStyle() {
        return "color:#c00;font-size:13px;margin-top:10px;min-height:18px;";
    }

    // ----- login view ------------------------------------------------------

    // Server Edition: set from /api/auth/status — when more than one user
    // exists, the login form gains a username field. Single-user installs
    // never see it.
    let multiUser = false;
    let usernames = [];
    // Several companies on one server (#358): the sign-in screen asks which.
    let companies = [];

    function rememberedUsername() {
        try { return sessionStorage.getItem(SWITCH_USERNAME_KEY) || ""; } catch (e) { return ""; }
    }

    function rememberUsername(name) {
        try {
            if (name) sessionStorage.setItem(SWITCH_USERNAME_KEY, name);
            else sessionStorage.removeItem(SWITCH_USERNAME_KEY);
        } catch (e) {}
    }

    function companySelectHTML() {
        if (companies.length < 2) return "";
        const opts = companies.map(function (c) {
            const e = document.createElement("option");
            e.value = c.file; e.textContent = c.name;
            if (c.current) e.setAttribute("selected", "");
            return e.outerHTML;
        }).join("");
        return (
            '<label for="auth-company" style="display:block;font-size:12px;font-weight:600;margin-bottom:4px;">Company</label>' +
            '<select id="auth-company" style="' + inputStyle() + '">' + opts + "</select>" +
            '<div style="height:10px"></div>'
        );
    }

    // Inside the native desktop window the launcher's bridge is present;
    // in a browser (Server Edition) it is not, and the picker does not exist.
    function isDesktopShell() {
        return typeof window.pywebview !== "undefined" && window.pywebview.api
            && typeof window.pywebview.api.show_picker === "function";
    }

    // The desktop app's own window, as /api/auth/status says (`desktop`).
    // The bridge is not always there yet when the sign-in screen is drawn:
    // on macOS pywebview injects it after the page has loaded, so the screen
    // the app starts on never offered "Choose a different company →", and
    // someone in the wrong company had no way back (2.18.0 gate, macbase1
    // NEW-8). The server's word draws the link; a click waits for the bridge.
    let desktopWindow = false;

    function offersPicker() {
        return desktopWindow || isDesktopShell();
    }

    function switchCompanyHTML() {
        return (
            '<br><button type="button" id="auth-switch-company" style="' +
            linkButtonStyle() +
            '">Choose a different company →</button>'
        );
    }

    let waitingForBridge = false;

    function openPicker(errBox) {
        if (isDesktopShell()) {
            window.pywebview.api.show_picker();
            return;
        }
        if (waitingForBridge) return; // a second click while waiting
        waitingForBridge = true;
        window.addEventListener("pywebviewready", function () {
            waitingForBridge = false;
            if (isDesktopShell()) window.pywebview.api.show_picker();
        }, { once: true });
        setTimeout(function () {
            if (waitingForBridge && errBox) {
                errBox.textContent = "The company list opens in the SlowBooks Pro window.";
            }
        }, 5000);
    }

    function wireSwitchCompany(overlay) {
        const switchCompany = overlay.querySelector("#auth-switch-company");
        if (!switchCompany) return;
        const errBox = overlay.querySelector("#auth-error");
        switchCompany.addEventListener("click", function () {
            openPicker(errBox);
        });
    }

    // A screen drawn before the bridge arrived, when the server did not say
    // this is the desktop app: add the link now that the bridge is here.
    window.addEventListener("pywebviewready", function () {
        const overlay = document.getElementById(OVERLAY_ID);
        if (!overlay || !isDesktopShell() || overlay.querySelector("#auth-switch-company")) return;
        const linkRow = overlay.querySelector("#auth-link-row");
        if (!linkRow) return;
        linkRow.insertAdjacentHTML("beforeend", switchCompanyHTML());
        wireSwitchCompany(overlay);
    });

    function userSelectHTML() {
        if (!usernames.length) {
            return field("auth-username", "Username", {
                required: true,
                autocomplete: "username",
            });
        }
        const chosen = rememberedUsername();
        const opts = usernames.map(function (u) {
            const e = document.createElement("option");
            e.value = u; e.textContent = u;
            if (u === chosen) e.setAttribute("selected", "");
            return e.outerHTML;
        }).join("");
        return (
            '<label for="auth-username" style="display:block;font-size:12px;font-weight:600;margin-bottom:4px;">Who is signing in?</label>' +
            '<select id="auth-username" required style="' + inputStyle() + '">' + opts + "</select>"
        );
    }

    function loginViewHTML() {
        // Name the books: with several companies on one machine, "Unlock
        // Slowbooks" did not say whose password it wanted (explore 2.17.3,
        // macbase1 F2).
        const name = existingCompany.name;
        return (
            '<form id="auth-form" ' +
            'style="background:#fff;color:#111;padding:calc(32px / var(--ui-scale, 1)) 28px;border-radius:8px;' +
            'min-width:340px;max-width:400px;width:100%;' +
            'box-shadow:0 20px 60px rgba(0,0,0,0.4);">' +
            '<h2 id="auth-title" style="margin:0 0 6px;font-size:20px;">' +
            (name ? "Unlock " + escapeText(name) : "Unlock Slowbooks") +
            "</h2>" +
            '<p style="margin:0 0 20px;color:#555;font-size:13px;line-height:1.5;">' +
            (multiUser
                ? "Sign in to continue."
                : name
                    ? "Enter the password for " + escapeText(name) + " to continue."
                    : "Enter your password to continue.") +
            "</p>" +
            recoveryNoticeHTML() +
            companySelectHTML() +
            (multiUser ? userSelectHTML() + '<div style="height:10px"></div>' : "") +
            field("auth-password", "Password", {
                type: "password",
                required: true,
                autocomplete: "current-password",
            }) +
            '<div style="height:14px"></div>' +
            '<button type="submit" id="auth-submit" style="' +
            primaryButtonStyle() +
            '">Unlock</button>' +
            '<div id="auth-error" style="' +
            errorBoxStyle() +
            '"></div>' +
            '<div style="margin-top:12px;text-align:center;">' +
            '<button type="button" id="auth-forgot" aria-expanded="false" aria-controls="auth-forgot-panel" style="' +
            linkButtonStyle() + '">Forgot your password?</button></div>' +
            '<div id="auth-forgot-panel" hidden style="' + noticeStyle() + 'margin-top:10px;"></div>' +
            '<div id="auth-link-row" style="margin-top:16px;text-align:center;">' +
            (offersPicker() ? switchCompanyHTML() : "") +
            "</div>" +
            "</form>"
        );
    }

    // ----- forgot your password? (#719) -----------------------------------

    // From /api/auth/status: {kind, message, elsewhere?}. kind is "ask_admin"
    // (Server Edition, another administrator), "recovery" (an encrypted
    // desktop file: open it with its recovery key), "reset_here" (a desktop
    // file that is not encrypted) or "recovery_reset" (opened with its
    // recovery key just now). The last two are what the app's own window can
    // do: the status cannot tell that window from a browser at its address
    // (W-125), so the reset is offered only where the launcher's bridge is,
    // and a page without it says `elsewhere` instead. The administrators'
    // names come through the bridge too, never in the status.
    let forgot = null;
    let windowBridge = null;   // a promise of the bridge's api, or of null
    let bridgeWaited = false;  // the one wait for a late bridge is over

    // The launcher's api when this page is the app's own window, else null.
    // The first call waits for a bridge that arrives after the page (macOS);
    // later calls answer at once, and still find one that came later yet.
    function windowCanReset() {
        if (bridgeWaited) return Promise.resolve(bridgeNow("reset_admin_password"));
        if (!windowBridge) {
            windowBridge = bridgeFor("reset_admin_password").then(function (api) {
                bridgeWaited = true;
                return api;
            });
        }
        return windowBridge;
    }

    function noticeStyle() {
        return (
            "padding:10px 12px;background:#eef4ff;border:1px solid #b9cdf2;border-radius:6px;" +
            "color:#1a2b3c;font-size:13px;line-height:1.5;"
        );
    }

    function forgotOffersReset() {
        return !!forgot && (forgot.kind === "reset_here" || forgot.kind === "recovery_reset");
    }

    function resetButtonHTML(id) {
        return (
            '<button type="button" id="' + id + '" style="' + linkButtonStyle() +
            'margin-top:6px;">Set a new administrator password</button>'
        );
    }

    // The notice a company opened with its recovery key shows at once: an
    // empty place in the page, filled when the window's bridge has answered
    // (a browser never sees it).
    function recoveryNoticeHTML() {
        if (!forgot || forgot.kind !== "recovery_reset") return "";
        return '<div id="auth-recovery-notice" hidden style="' + noticeStyle() + 'margin:0 0 14px;"></div>';
    }

    function wireForgot(overlay, onSuccess) {
        const link = overlay.querySelector("#auth-forgot");
        const panel = overlay.querySelector("#auth-forgot-panel");
        const notice = overlay.querySelector("#auth-recovery-notice");
        if (notice && forgot && forgot.kind === "recovery_reset") {
            windowCanReset().then(function (api) {
                if (!api || !notice.isConnected) return;
                notice.innerHTML = escapeText(forgot.message) + "<br>" + resetButtonHTML("auth-reset-open");
                notice.hidden = false;
                notice.querySelector("#auth-reset-open").addEventListener("click", function () {
                    renderReset(onSuccess);
                });
            });
        }
        if (!link || !panel) return;
        const ask = "Ask an administrator to reset it under Settings, Users.";
        const fill = function (offer) {
            let message = (forgot && forgot.message) || ask;
            if (forgotOffersReset() && !offer) message = forgot.elsewhere || ask;
            panel.innerHTML = '<span id="auth-forgot-text">' + escapeText(message) + "</span>" +
                (offer ? "<br>" + resetButtonHTML("auth-reset-here") : "");
            const here = panel.querySelector("#auth-reset-here");
            if (here) {
                here.addEventListener("click", function () { renderReset(onSuccess); });
                here.focus();
            }
        };
        link.addEventListener("click", function () {
            const showing = panel.hidden;
            panel.hidden = !showing;
            link.setAttribute("aria-expanded", showing ? "true" : "false");
            if (!showing) return;
            if (!forgotOffersReset()) { fill(false); return; }
            // only the app's own window has the bridge; a browser at the
            // same address is told where to go instead. That sentence shows
            // at once rather than after the wait for a bridge a browser
            // never gets (up to five seconds, #813); the reset is offered
            // in its place should the bridge arrive (the Mac app's comes
            // after the page).
            const api = bridgeNow("reset_admin_password");
            fill(!!api);
            if (api || bridgeWaited) return;
            windowCanReset().then(function (late) {
                if (late && !panel.hidden && panel.isConnected) fill(true);
            });
        });
    }

    // The launcher's bridge, waited for: on macOS it arrives after the page
    // has loaded. Resolves to the api, or null when there is none (a
    // browser, which can never set a password this way).
    function bridgeNow(method) {
        const there = typeof window.pywebview !== "undefined" && window.pywebview.api &&
            typeof window.pywebview.api[method] === "function";
        return there ? window.pywebview.api : null;
    }

    function bridgeFor(method) {
        if (bridgeNow(method)) return Promise.resolve(window.pywebview.api);
        return new Promise(function (resolve) {
            const timer = setTimeout(function () { resolve(null); }, 5000);
            window.addEventListener("pywebviewready", function () {
                clearTimeout(timer);
                resolve(bridgeNow(method));
            }, { once: true });
        });
    }

    function resetViewHTML(admins) {
        const name = existingCompany.name;
        let who = "";
        if (admins.length > 1) {
            who =
                '<label for="auth-reset-user" style="' + labelStyle() + '">Administrator</label>' +
                '<select id="auth-reset-user" required style="' + inputStyle() + '">' +
                admins.map(function (a) {
                    const e = document.createElement("option");
                    e.value = a; e.textContent = a;
                    return e.outerHTML;
                }).join("") + "</select>";
        } else if (admins.length === 1 && multiUser) {
            who = '<p style="margin:0 0 4px;color:#333;font-size:13px;">Administrator: <strong>' +
                escapeText(admins[0]) + "</strong></p>";
        }
        return (
            '<form id="auth-form" ' +
            'style="background:#fff;color:#111;padding:calc(32px / var(--ui-scale, 1)) 28px;border-radius:8px;' +
            'min-width:340px;max-width:400px;width:100%;' +
            'box-shadow:0 20px 60px rgba(0,0,0,0.4);">' +
            '<h2 id="auth-title" style="margin:0 0 6px;font-size:20px;">Set a new administrator password</h2>' +
            '<p style="margin:0 0 14px;color:#555;font-size:13px;line-height:1.5;">' +
            (name ? "For " + escapeText(name) + ". " : "") +
            "The administrator signs in with this password from now on, and is signed out everywhere else." +
            "</p>" +
            who +
            field("auth-reset-password", "New password", {
                type: "password",
                required: true,
                minlength: MIN_PASSWORD_LEN,
                autocomplete: "new-password",
                placeholder: MIN_PASSWORD_LEN + "+ characters",
            }) +
            field("auth-reset-confirm", "Confirm the new password", {
                type: "password",
                required: true,
                minlength: MIN_PASSWORD_LEN,
                autocomplete: "new-password",
            }) +
            '<div style="height:14px"></div>' +
            '<button type="submit" id="auth-submit" style="' + primaryButtonStyle() +
            '">Set the password and sign in</button>' +
            '<div id="auth-error" role="alert" style="' + errorBoxStyle() + '"></div>' +
            '<div style="margin-top:12px;text-align:center;">' +
            '<button type="button" id="auth-reset-cancel" style="' + linkButtonStyle() +
            '">Back to sign in</button></div>' +
            "</form>"
        );
    }

    async function renderReset(onSuccess) {
        // whose password: the names come from the launcher, which asks the
        // server with the window's secret (the status names nobody, W-125)
        let admins = [];
        const bridge = await windowCanReset();
        if (bridge && typeof bridge.reset_admins === "function") {
            try {
                const out = await bridge.reset_admins();
                if (out && out.success && Array.isArray(out.admins)) admins = out.admins;
            } catch (err) { /* the form still works for a lone administrator */ }
        }
        removeOverlay();
        const overlay = buildShell(resetViewHTML(admins));
        document.body.appendChild(overlay);
        const form = overlay.querySelector("#auth-form");
        const pw = overlay.querySelector("#auth-reset-password");
        const pw2 = overlay.querySelector("#auth-reset-confirm");
        const who = overlay.querySelector("#auth-reset-user");
        const errBox = overlay.querySelector("#auth-error");
        const btn = overlay.querySelector("#auth-submit");
        overlay.querySelector("#auth-reset-cancel").addEventListener("click", function () {
            renderView("login", onSuccess);
        });
        (who || pw).focus();
        form.addEventListener("submit", async function (e) {
            e.preventDefault();
            errBox.textContent = "";
            if (pw.value.length < MIN_PASSWORD_LEN) {
                errBox.textContent = "Password must be at least " + MIN_PASSWORD_LEN + " characters.";
                return;
            }
            if (pw.value !== pw2.value) {
                errBox.textContent = "Passwords do not match.";
                return;
            }
            btn.disabled = true;
            btn.textContent = "...";
            const done = function (message) {
                errBox.textContent = message;
                btn.disabled = false;
                btn.textContent = "Set the password and sign in";
            };
            const api = await windowCanReset();
            if (!api) {
                done("A new administrator password can be set only in the SlowBooks Pro window, on the computer that holds the company file.");
                return;
            }
            let result;
            try {
                result = await api.reset_admin_password(who ? who.value : "", pw.value);
            } catch (err) {
                result = null;
            }
            if (!result || !result.success) {
                done((result && result.error) || "The new password could not be set.");
                return;
            }
            try {
                const body = { password: pw.value };
                if (result.username) body.username = result.username;
                await postJSON(AUTH_LOGIN_URL, body);
            } catch (err) {
                // set, but the sign-in did not go through: sign in by hand
                renderView("login", onSuccess);
                const box = document.querySelector("#" + OVERLAY_ID + " #auth-error");
                if (box) box.textContent = "The new password is set. Sign in with it.";
                return;
            }
            if (forgot && forgot.kind === "recovery_reset") {
                showRecoveryStillWorks(overlay, onSuccess);
                return;
            }
            removeOverlay();
            if (onSuccess) onSuccess();
            else window.location.reload();
        });
    }

    // After a reset by the recovery key: the key is not used up. It still
    // opens the file; anyone else who may have seen it can too.
    function showRecoveryStillWorks(overlay, onSuccess) {
        const form = overlay.querySelector("#auth-form");
        form.innerHTML =
            '<h2 style="margin:0 0 6px;font-size:20px;">The new password is set</h2>' +
            '<p id="auth-recovery-still" style="margin:0 0 14px;color:#333;font-size:13px;line-height:1.5;">' +
            "Your recovery key still opens this company file, and still lets you set a new administrator password. " +
            "If anyone else may have seen it, make a new one under Settings, Company File Encryption, New recovery key. " +
            "The new key is shown once; keep it where the backups are not.</p>" +
            '<button type="button" id="auth-reset-continue" style="' + primaryButtonStyle() + '">Open the books</button>';
        const go = form.querySelector("#auth-reset-continue");
        go.addEventListener("click", function () {
            removeOverlay();
            if (onSuccess) onSuccess();
            else window.location.reload();
        });
        go.focus();
    }

    // The Company select on a served host's screens (#358): another
    // company's own people and its own password. The person stays chosen
    // when the other company has them. Shared by the sign-in and the setup
    // view, so a wrong choice is one more choice away from either.
    function wireCompanySelect(overlay, onSuccess) {
        const companyInput = overlay.querySelector("#auth-company");
        if (!companyInput) return;
        const errBox = overlay.querySelector("#auth-error");
        const userInput = overlay.querySelector("#auth-username");
        companyInput.addEventListener("change", async function () {
            errBox.textContent = "";
            if (userInput) rememberUsername(userInput.value);
            try {
                const r = await postJSON(AUTH_COMPANY_URL, { file: companyInput.value });
                if (r && r.authenticated) {
                    // This session's sign-in opens that company too, so it
                    // is signed in there now: start from its Home page, as
                    // the topbar's switch does. (It used to call promptAuth,
                    // which found the session signed in and drew nothing:
                    // the screen closed onto the page the old company's
                    // missing file had left, with the old company's name in
                    // the topbar — 2.25.0 gate, macbase1 NEW-169, #370.)
                    rememberUsername("");
                    removeOverlay();
                    window.location.hash = "#/";
                    window.location.reload();
                    return;
                }
                removeOverlay();
                promptAuth(onSuccess);
            } catch (err) {
                errBox.textContent = err.message;
            }
        });
    }

    function wireLogin(overlay, onSuccess) {
        const form = overlay.querySelector("#auth-form");
        wireSwitchCompany(overlay);
        const input = overlay.querySelector("#auth-password");
        const userInput = overlay.querySelector("#auth-username");
        const errBox = overlay.querySelector("#auth-error");
        const btn = overlay.querySelector("#auth-submit");
        wireCompanySelect(overlay, onSuccess);
        wireForgot(overlay, onSuccess);
        (rememberedUsername() && userInput ? input : (userInput || input)).focus();

        form.addEventListener("submit", async function (e) {
            e.preventDefault();
            errBox.textContent = "";
            btn.disabled = true;
            btn.textContent = "...";
            try {
                const body = { password: input.value };
                if (userInput) body.username = userInput.value.trim();
                await postJSON(AUTH_LOGIN_URL, body);
                rememberUsername("");
                removeOverlay();
                if (onSuccess) onSuccess();
                else window.location.reload();
            } catch (err) {
                // 409 means no password is set yet — bounce to setup so the
                // user isn't stuck staring at "setup required" with no path.
                if (err.status === 409) {
                    errBox.textContent =
                        "No password set yet. Switching to first-time setup.";
                    setTimeout(function () {
                        renderView("setup", onSuccess);
                    }, 800);
                    return;
                }
                errBox.textContent = err.message;
                btn.disabled = false;
                btn.textContent = "Unlock";
            }
        });
    }

    // ----- setup view ------------------------------------------------------

    // Filled from /api/auth/status before the setup view renders.
    let existingCompany = { name: "", hasData: false };

    function setupViewHTML() {
        const existingNotice = existingCompany.hasData
            ? '<div style="margin:0 0 12px;padding:10px 12px;background:#fff7e0;' +
              'border:1px solid #e8c56a;border-radius:6px;color:#5a4300;font-size:13px;line-height:1.5;">' +
              "This company file already contains books" +
              (existingCompany.name ? " for <strong>" + escapeText(existingCompany.name) + "</strong>" : "") +
              ". Setup only adds your operator password; keep the company name unless you mean to rename these books." +
              "</div>"
            : "";
        return (
            '<form id="auth-form" ' +
            'style="background:#fff;color:#111;padding:28px 28px 24px;border-radius:8px;' +
            'min-width:380px;max-width:440px;width:100%;' +
            'box-shadow:0 20px 60px rgba(0,0,0,0.4);">' +
            '<h2 style="margin:0 0 6px;font-size:22px;">Set up Slowbooks Pro 2026</h2>' +
            '<p style="margin:0 0 8px;color:#555;font-size:13px;line-height:1.5;">' +
            "Just enough to get you in. You can configure everything else later." +
            "</p>" +
            existingNotice +
            // On a served host the setup form is one choice among the
            // companies, like the sign-in: a company nobody has set up yet,
            // chosen by the wrong person, held the browser on this form
            // with no way back (2.25.0 gate, skytech W-28, #369). Empty on
            // a server with one company, and on the desktop.
            companySelectHTML() +
            field("operator_name", "Your name", { required: true }) +
            field("operator_email", "Your email", {
                type: "email",
                required: true,
                autocomplete: "email",
            }) +
            field("company_name", "Company name", {
                required: true,
                placeholder: "My Company",
                value: existingCompany.name,
            }) +
            field("company_email", "Company email", {
                type: "email",
                placeholder: "Leave blank if same as yours",
            }) +
            entityField() +

            // Account numbers on the chart (#567): on unless unticked; the
            // seeded chart keeps its numbers either way
            '<label style="display:flex;gap:8px;align-items:flex-start;margin:12px 0 0;font-size:13px;color:#333;line-height:1.5;">' +
            '<input type="checkbox" id="use_account_numbers" checked style="margin-top:3px;">' +
            '<span><strong>Use account numbers.</strong> Accounts read "6100 - Name" and list by number. ' +
            'Untick it if your business does not number its accounts; you can change this under Settings.</span></label>' +
            field("auth-password", "Password", {
                type: "password",
                required: true,
                minlength: MIN_PASSWORD_LEN,
                autocomplete: "new-password",
                placeholder: MIN_PASSWORD_LEN + "+ characters",
            }) +
            field("auth-password-confirm", "Confirm password", {
                type: "password",
                required: true,
                minlength: MIN_PASSWORD_LEN,
                autocomplete: "new-password",
            }) +
            // Encrypt the company file from the first day (#290): shown on
            // the desktop, where the key is the person's own; a server
            // keys the file from its operator secret, under Settings.
            (desktopWindow
                ? '<label style="display:flex;gap:8px;align-items:flex-start;margin:12px 0 0;font-size:13px;color:#333;line-height:1.5;">' +
                  '<input type="checkbox" id="auth-encrypt" style="margin-top:3px;">' +
                  '<span><strong>Encrypt this company file.</strong> Nothing readable is written to disk; your sign-in unlocks the books, ' +
                  'and a recovery key shown once opens them if every password is lost. Recommended where the customer list is private ' +
                  '(a clinic, a law office).</span></label>'
                : '') +
            '<div style="height:18px"></div>' +
            '<button type="submit" id="auth-submit" style="' +
            primaryButtonStyle() +
            '">Set up & continue</button>' +
            '<div id="auth-error" style="' +
            errorBoxStyle() +
            '"></div>' +
            '<p style="margin:14px 0 0;color:#767676;font-size:12px;line-height:1.5;text-align:center;">' +
            "You can add your address, phone, tax ID, payment defaults, " +
            "and integrations in Settings after you sign in." +
            "</p>" +
            "</form>"
        );
    }

    // Legal entity type (#488), one picker: the entity, with an LLC's tax
    // election folded in as "LLC taxed as …". Chosen here, before the first
    // posting, the equity accounts follow it; "Decide later" leaves the
    // chart as seeded and Settings → Company asks again.
    var ENTITY_CHOICES = [
        ["", "Decide later"],
        ["sole_proprietor", "Sole proprietor"],
        ["llc:disregarded", "LLC, single member (taxed as a sole proprietor)"],
        ["llc:partnership", "LLC taxed as a partnership"],
        ["llc:s_corp", "LLC taxed as an S corporation"],
        ["llc:c_corp", "LLC taxed as a C corporation"],
        ["general_partnership", "General partnership"],
        ["limited_partnership", "Limited partnership"],
        ["llp", "Limited liability partnership"],
        ["s_corp", "S corporation"],
        ["c_corp", "C corporation"],
    ];

    function entityField() {
        var options = ENTITY_CHOICES.map(function (pair) {
            return '<option value="' + pair[0] + '">' + pair[1] + "</option>";
        }).join("");
        return (
            '<label for="entity_choice" style="' + labelStyle() + '">Legal entity</label>' +
            '<select id="entity_choice" name="entity_choice" style="' + inputStyle() + '">' +
            options +
            "</select>" +
            '<div style="font-size:11px;color:#767676;margin-top:4px;line-height:1.4;">' +
            "Picks the tax return and the equity accounts (owner's equity and draw; partner capital; " +
            "common stock and shareholder distributions). You can change it in Settings." +
            "</div>"
        );
    }

    function entityPayload(choice) {
        if (!choice) return {};
        var parts = choice.split(":");
        var out = { entity_type: parts[0] };
        if (parts[1]) out.llc_tax_election = parts[1];
        return out;
    }

    function collectSetupPayload(overlay) {
        const ids = [
            "operator_name",
            "operator_email",
            "company_name",
            "company_email",
        ];
        const out = {
            password: overlay.querySelector("#auth-password").value,
        };
        const encrypt = overlay.querySelector("#auth-encrypt");
        if (encrypt && encrypt.checked) out.encrypt = true;
        const entity = overlay.querySelector("#entity_choice");
        if (entity) Object.assign(out, entityPayload(entity.value));

        const numbers = overlay.querySelector("#use_account_numbers");
        if (numbers) out.use_account_numbers = numbers.checked ? "true" : "false";
        ids.forEach(function (id) {
            const el = overlay.querySelector("#" + id);
            if (el && el.value.trim() !== "") {
                out[id] = el.value.trim();
            }
        });
        return out;
    }

    function wireSetup(overlay, onSuccess) {
        const form = overlay.querySelector("#auth-form");
        const pw = overlay.querySelector("#auth-password");
        const pw2 = overlay.querySelector("#auth-password-confirm");
        const errBox = overlay.querySelector("#auth-error");
        const btn = overlay.querySelector("#auth-submit");
        const firstField = overlay.querySelector("#operator_name");

        wireCompanySelect(overlay, onSuccess);
        if (firstField) firstField.focus();

        form.addEventListener("submit", async function (e) {
            e.preventDefault();
            errBox.textContent = "";

            if (pw.value.length < MIN_PASSWORD_LEN) {
                errBox.textContent =
                    "Password must be at least " + MIN_PASSWORD_LEN + " characters.";
                return;
            }
            if (pw.value !== pw2.value) {
                errBox.textContent = "Passwords do not match.";
                return;
            }

            btn.disabled = true;
            btn.textContent = "...";
            try {
                const done = await postJSON(AUTH_SETUP_URL, collectSetupPayload(overlay));
                if (done && done.recovery_key) {
                    // the wizard's last page: the recovery key, shown once
                    showRecoveryKey(overlay, done, onSuccess);
                    return;
                }
                if (done && done.encryption_note) window.alert(done.encryption_note);
                removeOverlay();
                if (onSuccess) onSuccess();
                else window.location.reload();
            } catch (err) {
                // 409 means the password was set between status check and
                // submit — guide the user to the login view rather than
                // leaving them stuck.
                // Two 409s: a password already set (switch to sign in), and
                // a company name another file already has, which stays here
                // with its sentence (2.24.0 round 2, W-24: it switched to an
                // Unlock form for a company with no password).
                if (err.status === 409 && !/already named/i.test(err.message || "")) {
                    errBox.textContent =
                        "Setup is already complete on this server. Switching to sign in.";
                    setTimeout(function () {
                        renderView("login", onSuccess);
                    }, 800);
                    return;
                }
                errBox.textContent = err.message;
                btn.disabled = false;
                btn.textContent = "Set up & continue";
            }
        });
    }

    // The recovery key, shown once after setup encrypted the file (#290):
    // copy it, or save it as a text file, then go on into the books.
    function showRecoveryKey(overlay, done, onSuccess) {
        const form = overlay.querySelector("#auth-form");
        if (!form) return;
        form.innerHTML =
            '<h2 style="margin:0 0 6px;font-size:22px;">Your recovery key</h2>' +
            '<p style="margin:0 0 10px;color:#333;font-size:13px;line-height:1.5;">The company file is encrypted. ' +
            "<strong>This recovery key is shown once.</strong> It opens the file, and every backup of it, if every " +
            "administrator's password is lost, and then lets you set a new administrator password. " +
            "Anyone holding it can read the books, so keep it where the backups are not: " +
            "a safe, a password manager, a sealed envelope.</p>" +
            '<p id="auth-recovery-key" style="margin:0 0 12px;font-family:monospace;font-size:16px;letter-spacing:0.04em;' +
            'text-align:center;padding:10px;border:1px solid #c6ccd4;border-radius:6px;user-select:all;">' +
            escapeText(done.recovery_key) + "</p>" +
            '<div style="display:flex;gap:8px;margin-bottom:12px;">' +
            '<button type="button" id="auth-copy-key" style="flex:1;padding:8px;border:1px solid #c6ccd4;border-radius:6px;background:#f4f6f8;cursor:pointer;">Copy</button>' +
            '<button type="button" id="auth-save-key" style="flex:1;padding:8px;border:1px solid #c6ccd4;border-radius:6px;background:#f4f6f8;cursor:pointer;">Save as a text file</button>' +
            "</div>" +
            '<button type="button" id="auth-kept-key" style="' + primaryButtonStyle() + '">I have kept it, open the books</button>';
        const text = done.recovery_file || done.recovery_key;
        form.querySelector("#auth-copy-key").addEventListener("click", async function () {
            try { await navigator.clipboard.writeText(done.recovery_key); this.textContent = "Copied"; }
            catch (e) { this.textContent = "Select the key and copy it"; }
        });
        form.querySelector("#auth-save-key").addEventListener("click", async function () {
            const name = ((existingCompany.name || "company").replace(/[^A-Za-z0-9 _-]+/g, "").trim() || "company") + " recovery key.txt";
            const api = window.pywebview && window.pywebview.api;
            if (api && api.save_secret_file) {
                // a Save panel on the desktop: never the synced Reports folder unasked
                const r = await api.save_secret_file(name, text);
                this.textContent = r && r.success ? "Saved" : (r && r.cancelled ? "Not saved" : "Could not save");
                return;
            }
            const blob = new Blob([text], { type: "text/plain" });
            if (window.SlowbooksDesktop && (await window.SlowbooksDesktop.saveFile(blob, name))) { this.textContent = "Saved"; return; }
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url; a.download = name;
            document.body.appendChild(a); a.click(); a.remove();
            setTimeout(function () { URL.revokeObjectURL(url); }, 30000);
            this.textContent = "Saved";
        });
        form.querySelector("#auth-kept-key").addEventListener("click", function () {
            removeOverlay();
            if (onSuccess) onSuccess();
            else window.location.reload();
        });
        form.querySelector("#auth-kept-key").focus();
    }

    // ----- view orchestration ---------------------------------------------

    function removeOverlay() {
        const existing = document.getElementById(OVERLAY_ID);
        if (existing) existing.remove();
    }

    // The status bar behind the overlay. The shell's own placeholder and the
    // failed first page load used to read "Error loading page · Company:
    // bookkeeper.sbk" behind a brand-new company's sign-in (explore 2.17.3,
    // skytech M18); nothing had failed.
    function paintStatusBar(mode) {
        const text = document.getElementById("status-text");
        if (text) {
            text.textContent = mode === "setup"
                ? "Set up this company to continue"
                : "Sign in to continue";
        }
        const company = document.getElementById("status-company");
        if (company) {
            company.textContent = existingCompany.name
                ? "Company: " + existingCompany.name
                : "";
        }
    }

    function renderView(mode, onSuccess) {
        removeOverlay();
        const html = mode === "setup" ? setupViewHTML() : loginViewHTML();
        const overlay = buildShell(html);
        document.body.appendChild(overlay);
        paintStatusBar(mode);
        if (mode === "setup") {
            wireSetup(overlay, onSuccess);
        } else {
            wireLogin(overlay, onSuccess);
        }
        // A first screen is up: boot_check.js stops waiting for one.
        if (window.SlowBooksBoot) window.SlowBooksBoot.drawn();
    }

    // Single entry point used by api.js (on 401) and the DOMContentLoaded
    // handler. The view follows /api/auth/status: a company nobody has set
    // up yet opens on first-run setup, every other one on sign-in. (It used
    // to open on "Unlock Slowbooks — enter your password" for a company that
    // had no password; the way on was a small link, or a wrong password —
    // explore 2.17.3, skytech M18 / macbase1 F1.) The cross-links still flip
    // between the two.
    //
    // Race-safe: a flurry of 401s from parallel API calls shares one status
    // check and can only ever paint one overlay. Resolves to true while an
    // overlay is up (the page reloads when the person is in), false when the
    // session turned out to be signed in after all.
    let authPromptPromise = null;
    function promptAuth(onSuccess) {
        if (document.getElementById(OVERLAY_ID)) return Promise.resolve(true);
        if (authPromptPromise) return authPromptPromise;
        authPromptPromise = (async function () {
            try {
                const status = await checkStatus();
                multiUser = status.multi_user === true;
                desktopWindow = status.desktop === true;
                usernames = Array.isArray(status.usernames) ? status.usernames : [];
                companies = Array.isArray(status.companies) ? status.companies : [];
                forgot = status.forgot && typeof status.forgot === "object" ? status.forgot : null;
                // start looking for the window's bridge now, so "Forgot your
                // password?" has its answer by the time it is pressed
                if (forgotOffersReset()) windowCanReset();
                existingCompany = {
                    name: status.company_name || "",
                    hasData: status.has_data === true,
                };
                if (status.authenticated) return false;
                renderView(status.setup_needed ? "setup" : "login", onSuccess);
                if (status.company_unavailable) {
                    // the session's company cannot be opened now (#358):
                    // the sentence, with the other companies still offered
                    const box = document.querySelector("#" + OVERLAY_ID + " #auth-error");
                    if (box) box.textContent = status.company_unavailable;
                }
                return true;
            } finally {
                authPromptPromise = null;
            }
        })();
        return authPromptPromise;
    }

    // Expose globals so api.js can prompt on 401
    window.SlowbooksAuth = {
        promptAuth: promptAuth,
        // the header's company switch (app.js) when the sign-in did not
        // open the other company: its sign-in screen has this person chosen
        rememberUsername: rememberUsername,
        // Back-compat: explicit view requests still work
        promptLogin: function () {
            renderView("login");
        },
        promptSetup: function () {
            renderView("setup");
        },
    };

    document.addEventListener("DOMContentLoaded", function () {
        promptAuth();
    });
})();
