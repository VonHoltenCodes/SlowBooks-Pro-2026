/**
 * Slowbooks Pro 2026 — Auth overlay (Phase 9.7)
 *
 * Injects a full-screen login/setup overlay when the API returns 401, or
 * when /api/auth/status reports first-time setup is needed.
 *
 * Two views:
 *   - login: password only, with a "First time? Set up Slowbooks →" link
 *   - setup: full first-run wizard (company info, operator name/email,
 *            defaults, password) with a "Already set up? Sign in →" link
 *
 * The initial view is decided by /api/auth/status; the user can flip
 * between them via the cross-links. Race-safe: a flurry of 401s from
 * parallel API calls can only ever paint one overlay.
 */
(function () {
    "use strict";

    const AUTH_STATUS_URL = "/api/auth/status";
    const AUTH_SETUP_URL = "/api/auth/setup";
    const AUTH_LOGIN_URL = "/api/auth/login";

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
                "padding:48px 16px;overflow-y:auto;" +
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
            'font-size:11px;text-transform:uppercase;letter-spacing:0.05em;color:#888;font-weight:700;">' +
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

    // Inside the native desktop window the launcher's bridge is present;
    // in a browser (Server Edition) it is not, and the picker does not exist.
    function isDesktopShell() {
        return typeof window.pywebview !== "undefined" && window.pywebview.api
            && typeof window.pywebview.api.show_picker === "function";
    }

    function userSelectHTML() {
        if (!usernames.length) {
            return field("auth-username", "Username", {
                required: true,
                autocomplete: "username",
            });
        }
        const opts = usernames.map(function (u) {
            const e = document.createElement("option");
            e.value = u; e.textContent = u;
            return e.outerHTML;
        }).join("");
        return (
            '<label for="auth-username" style="display:block;font-size:12px;font-weight:600;margin-bottom:4px;">Who is signing in?</label>' +
            '<select id="auth-username" required style="' + inputStyle() + '">' + opts + "</select>"
        );
    }

    function loginViewHTML() {
        return (
            '<form id="auth-form" ' +
            'style="background:#fff;color:#111;padding:32px 28px;border-radius:8px;' +
            'min-width:340px;max-width:400px;width:100%;' +
            'box-shadow:0 20px 60px rgba(0,0,0,0.4);">' +
            '<h2 style="margin:0 0 6px;font-size:20px;">Unlock Slowbooks</h2>' +
            '<p style="margin:0 0 20px;color:#555;font-size:13px;line-height:1.5;">' +
            (multiUser
                ? "Sign in to continue."
                : "Enter your password to continue.") +
            "</p>" +
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
            '<div style="margin-top:16px;text-align:center;">' +
            '<button type="button" id="auth-switch-setup" style="' +
            linkButtonStyle() +
            '">First time? Set up Slowbooks →</button>' +
            (isDesktopShell()
                ? '<br><button type="button" id="auth-switch-company" style="' +
                  linkButtonStyle() +
                  '">Choose a different company →</button>'
                : "") +
            "</div>" +
            "</form>"
        );
    }

    function wireLogin(overlay, onSuccess) {
        const form = overlay.querySelector("#auth-form");
        const switchCompany = overlay.querySelector("#auth-switch-company");
        if (switchCompany) {
            switchCompany.addEventListener("click", function () {
                window.pywebview.api.show_picker();
            });
        }
        const input = overlay.querySelector("#auth-password");
        const userInput = overlay.querySelector("#auth-username");
        const errBox = overlay.querySelector("#auth-error");
        const btn = overlay.querySelector("#auth-submit");
        const switchBtn = overlay.querySelector("#auth-switch-setup");

        (userInput || input).focus();

        switchBtn.addEventListener("click", function () {
            renderView("setup", onSuccess);
        });

        form.addEventListener("submit", async function (e) {
            e.preventDefault();
            errBox.textContent = "";
            btn.disabled = true;
            btn.textContent = "...";
            try {
                const body = { password: input.value };
                if (userInput) body.username = userInput.value.trim();
                await postJSON(AUTH_LOGIN_URL, body);
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
            '<div style="height:18px"></div>' +
            '<button type="submit" id="auth-submit" style="' +
            primaryButtonStyle() +
            '">Set up & continue</button>' +
            '<div id="auth-error" style="' +
            errorBoxStyle() +
            '"></div>' +
            '<p style="margin:14px 0 0;color:#777;font-size:12px;line-height:1.5;text-align:center;">' +
            "You can add your address, phone, tax ID, payment defaults, " +
            "and integrations in Settings after you sign in." +
            "</p>" +
            '<div style="margin-top:12px;text-align:center;">' +
            '<button type="button" id="auth-switch-login" style="' +
            linkButtonStyle() +
            '">Already set up? Sign in →</button>' +
            "</div>" +
            "</form>"
        );
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
        const switchBtn = overlay.querySelector("#auth-switch-login");
        const firstField = overlay.querySelector("#operator_name");

        if (firstField) firstField.focus();

        switchBtn.addEventListener("click", function () {
            renderView("login", onSuccess);
        });

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
                await postJSON(AUTH_SETUP_URL, collectSetupPayload(overlay));
                removeOverlay();
                if (onSuccess) onSuccess();
                else window.location.reload();
            } catch (err) {
                // 409 means the password was set between status check and
                // submit — guide the user to the login view rather than
                // leaving them stuck.
                if (err.status === 409) {
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

    // ----- view orchestration ---------------------------------------------

    function removeOverlay() {
        const existing = document.getElementById(OVERLAY_ID);
        if (existing) existing.remove();
    }

    function renderView(mode, onSuccess) {
        removeOverlay();
        const html = mode === "setup" ? setupViewHTML() : loginViewHTML();
        const overlay = buildShell(html);
        document.body.appendChild(overlay);
        if (mode === "setup") {
            wireSetup(overlay, onSuccess);
        } else {
            wireLogin(overlay, onSuccess);
        }
    }

    // Single entry point used by api.js (on 401) and the DOMContentLoaded
    // handler. Login is the canonical entry view — setup is reachable via the
    // hyperlink on the login form. Race-safe: a flurry of 401s from parallel
    // API calls can only ever paint one overlay.
    let authPromptInFlight = false;
    async function promptAuth(onSuccess) {
        if (authPromptInFlight) return;
        if (document.getElementById(OVERLAY_ID)) return;
        authPromptInFlight = true;
        try {
            const status = await checkStatus();
            multiUser = status.multi_user === true;
            usernames = Array.isArray(status.usernames) ? status.usernames : [];
            existingCompany = {
                name: status.company_name || "",
                hasData: status.has_data === true,
            };
            if (status.authenticated) return;
            renderView("login", onSuccess);
        } finally {
            authPromptInFlight = false;
        }
    }

    // Expose globals so api.js can prompt on 401
    window.SlowbooksAuth = {
        promptAuth: promptAuth,
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
