"""
Wiring audit as a unit test.

Greps every JS file for `API.get/post/put/del('/path')` calls, normalizes
the path templates (template-literal interpolation becomes `{x}` path
params), and asserts every one resolves to a registered FastAPI route
with a matching method.

The same audit was historically done by hand (see docs/wiring-audit.md).
This test catches regressions automatically: rename a route, forget to
update the SPA, and CI fails.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parents[1] / "app" / "static" / "js"
INDEX_HTML = Path(__file__).resolve().parents[1] / "index.html"

# API.get('/foo') | API.post(`/foo/${id}`, ...) | API.put('/foo', data) | API.del(`/foo/${id}`)
# Captures the method and the FIRST argument string (single, double, or backtick quoted).
_API_CALL = re.compile(
    r"""API\.(get|post|put|del)\(\s*['"`]([^'"`,]+)['"`]""",
    re.MULTILINE,
)

# Raw fetch() calls: fetch('/api/foo') | fetch(`/api/foo/${x}`, { method: 'POST' })
# File-upload paths and blob-returning routes use raw fetch because they
# need FormData / response.blob() — not JSON. We still want them in the
# audit. The method defaults to GET when no `method:` clause appears in
# the same fetch() options object.
_RAW_FETCH = re.compile(
    r"""fetch\(\s*['"`](/api[^'"`,)]+)['"`](?:\s*,\s*\{([^}]*)\})?""",
    re.MULTILINE,
)
_METHOD_IN_OPTS = re.compile(
    r"""method\s*:\s*['"`](GET|POST|PUT|DELETE|PATCH|HEAD)['"`]""",
    re.IGNORECASE,
)


def _normalize_js_path(raw: str) -> str:
    """JS path string -> /api-prefixed, query-stripped path with `${x}` -> `*`.

    `*` marks "an interpolation went here" — distinct from literal segments
    so we know which positions were dynamic on the call side.
    """
    path = raw.split("?", 1)[0]
    path = re.sub(r"\$\{[^}]+\}", "*", path)
    if not path.startswith("/api"):
        path = "/api" + (path if path.startswith("/") else "/" + path)
    return path


@pytest.fixture(scope="module")
def app_routes():
    """List of (METHOD, [path_segments]) for every registered FastAPI route."""
    from app.main import app

    out = []
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        segs = route.path.split("/")
        for m in methods:
            out.append((m.upper(), segs))
    return out


def _route_matches(call_segs: list[str], route_segs: list[str]) -> bool:
    """Segment-by-segment match between a JS call path and a FastAPI route.

    Three matching rules per segment, any of which makes the segment OK:
      - literal equality
      - the ROUTE side is a `{...}` path param   (FastAPI accepts anything there)
      - the CALL side contains `*` from template
        literal substitution; we treat `*` as a
        regex `.*` and full-match the route side  (handles whole-segment
                                                   `${id}` AND in-segment
                                                   `export.${kind}` cases)
    """
    if len(call_segs) != len(route_segs):
        return False
    for call, route in zip(call_segs, route_segs):
        if route.startswith("{") and route.endswith("}"):
            continue
        if "*" in call:
            pattern = re.escape(call).replace(r"\*", ".*")
            if re.fullmatch(pattern, route):
                continue
            return False
        if call == route:
            continue
        return False
    return True


# Any string literal starting with /api/ that we couldn't classify above.
# Catches `_openPDF(\`/api/foo/${x}\`, ...)`, `window.open('/api/...', ...)`,
# `href="/api/..."` in JS-rendered HTML, etc. — we don't know the method,
# so we treat these as "any-method" hints for the reverse-direction audit.
# Source is pre-processed to replace `${...}` blocks with `*` (the JS
# template-literal expressions otherwise contain parens/quotes that
# would terminate the path mid-string).
_ANY_API_STRING = re.compile(r"""['"`](/api/[A-Za-z0-9/_\-.?=&*]+)['"`]""")

# Catch-all for paths assigned to variables before being passed to API.*:
#   const url = `/analytics/ai-actions/${k}?period=${p}`;
#   API.post(url, body);
# We can't tie the literal back to API.post here, but for the reverse-
# direction audit we only need to know that the route name appears
# *somewhere* in the JS. Match any single-, double-, or backtick-quoted
# string that starts with `/` and looks path-like. The consumer prepends
# `/api` if the captured path doesn't already start with it, so paths
# like `/analytics/foo` resolve to `/api/analytics/foo`. False positives
# (e.g. `/static/css/foo.css`) simply don't match any registered route.
_ANY_PATH_STRING = re.compile(r"""['"`](/[a-zA-Z][a-zA-Z0-9/_\-.?=&*]*)['"`]""")

# href="/api/..." or action="/api/..." inside template-literal HTML strings.
# JS-rendered modals routinely contain `<a href="/api/foo">Download</a>`,
# `<form action="/api/bar">` — these are browser-driven calls we'd
# otherwise miss because they're inside string templates, not function args.
_API_HREF_IN_HTML = re.compile(
    r"""(?:href|action)\s*=\s*['"`]?(/api/[A-Za-z0-9/_\-.?=&*]+)""",
)

# Strip `${...}` template-literal expressions (which can nest parens/quotes
# that confuse downstream regexes) and replace with `*`. Must be greedy
# enough to handle 1-level-deep nesting like `${encodeURIComponent(x)}`,
# but our paths in this codebase never use nested `${ ${} }` so a single
# pass is sufficient.
_TEMPLATE_EXPR = re.compile(r"\$\{[^{}]*\}")


def _collect_api_calls():
    """Walk app/static/js/*.js, yield (file, line, method, normalized_path).

    Picks up three call styles:
      - `API.get/post/put/del('/path')`     (JSON helper, auto-prefixes /api)
      - `fetch('/api/path', {method: ...})` (raw, used for uploads + blobs)
      - any `'/api/...'` string literal      (catch-all hint with method="*")
    """
    for js in sorted(JS_DIR.glob("*.js")):
        original = js.read_text(encoding="utf-8")
        if js.name == "api.js":
            continue
        # Pre-substitute `${...}` blocks with `*` so paths inside template
        # literals stay continuous strings — otherwise nested parens /
        # quotes inside the expression break the catch-all regex.
        text = _TEMPLATE_EXPR.sub("*", original)

        # 1) API.* calls — JSON helper, method known
        for match in _API_CALL.finditer(text):
            method, raw = match.group(1), match.group(2)
            method = "DELETE" if method == "del" else method.upper()
            line = text.count("\n", 0, match.start()) + 1
            yield js.name, line, method, _normalize_js_path(raw)

        # 2) Raw fetch() — method comes from the options object
        for match in _RAW_FETCH.finditer(text):
            raw_path = match.group(1)
            opts_body = match.group(2) or ""
            mopt = _METHOD_IN_OPTS.search(opts_body)
            method = (mopt.group(1) if mopt else "GET").upper()
            line = text.count("\n", 0, match.start()) + 1
            path = raw_path.split("?", 1)[0]
            yield js.name, line, method, path

        # 3) Catch-all string literals starting with /api/. Useful for the
        #    reverse-direction audit (is this route called from ANYWHERE?).
        #    Method is "*" — every method counts as a reference for these.
        for match in _ANY_API_STRING.finditer(text):
            raw_path = match.group(1)
            line = text.count("\n", 0, match.start()) + 1
            path = raw_path.split("?", 1)[0]
            yield js.name, line, "*", path

        # 3b) Broader catch-all for leading-slash path literals (variable
        #     assignments before API.post(url, …) calls). Prepended with
        #     /api when missing so /analytics/foo → /api/analytics/foo.
        for match in _ANY_PATH_STRING.finditer(text):
            raw_path = match.group(1)
            if raw_path.startswith("/api/"):
                continue  # already handled by step 3
            line = text.count("\n", 0, match.start()) + 1
            path = _normalize_js_path(raw_path)
            yield js.name, line, "*", path

        # 4) href="/api/..." and action="/api/..." in template-literal HTML.
        #    Browser-driven downloads + form submissions to admin endpoints
        #    show up here.
        for match in _API_HREF_IN_HTML.finditer(text):
            raw_path = match.group(1)
            line = text.count("\n", 0, match.start()) + 1
            path = raw_path.split("?", 1)[0]
            yield js.name, line, "*", path

    # 5) index.html — static nav links, splash button. Same as JS-rendered
    #    href= scan, just against the SPA shell page.
    if INDEX_HTML.exists():
        html_text = _TEMPLATE_EXPR.sub("*", INDEX_HTML.read_text(encoding="utf-8"))
        for match in _API_HREF_IN_HTML.finditer(html_text):
            raw_path = match.group(1)
            line = html_text.count("\n", 0, match.start()) + 1
            path = raw_path.split("?", 1)[0]
            yield "index.html", line, "*", path


def test_every_js_api_call_resolves_to_a_route(app_routes):
    """Every API.get/post/put/del in the SPA must hit a real handler."""
    orphans = []
    for file, line, method, path in _collect_api_calls():
        if method == "*":
            # Catch-all string literals are hints for the reverse-direction
            # audit only — skip in the forward test because we don't know
            # what method they'd be called with.
            continue
        call_segs = path.split("/")
        matched = any(
            rm == method and _route_matches(call_segs, rsegs)
            for rm, rsegs in app_routes
        )
        if not matched:
            orphans.append(f"  {file}:{line}  {method} {path}")
    assert not orphans, "JS API calls without matching FastAPI handlers:\n" + "\n".join(
        orphans
    )


def test_collector_finds_something():
    """Smoke test for the collector itself — if this asserts 0 calls,
    the regex broke."""
    calls = list(_collect_api_calls())
    assert len(calls) > 20, (
        f"_collect_api_calls() only found {len(calls)} entries — the regex "
        "is probably broken. Spot-check app/static/js/payroll.js."
    )


# Routes the SPA intentionally never calls. They exist for OTHER reasons:
# - The Stripe webhook fires from Stripe's servers, not our SPA
# - OAuth callbacks land here from the OAuth provider
# - Background utilities (gross-up, classify) are invoked from server code
# - One-shot seeds run once during setup, not from the running SPA
# - Legacy paths superseded by newer endpoints (kept for backwards compat)
_INTENTIONAL_BACKEND_ONLY: set[tuple[str, str]] = {
    ("POST", "/api/stripe/webhook"),
    ("GET", "/api/qbo/callback"),
    ("POST", "/api/deductions/types/seed-standard"),
    ("POST", "/api/payroll/gross-up"),
    ("POST", "/api/payroll/{run_id}/nacha"),
    ("POST", "/api/time-entries/classify"),
    # Legacy: superseded by /api/payroll/forms/* — kept until next major release.
    # Migration tracker in docs/todo.md.
    ("GET", "/api/tax-forms/w2"),
    ("GET", "/api/tax-forms/w2/{employee_id}"),
    ("GET", "/api/tax-forms/w2/{employee_id}/pdf"),
    ("GET", "/api/tax-forms/940"),
    ("GET", "/api/tax-forms/940/pdf"),
    ("GET", "/api/tax-forms/941"),
    ("GET", "/api/tax-forms/941/pdf"),
    ("GET", "/api/tax-forms/1099"),
    ("GET", "/api/tax-forms/1099/{vendor_id}/pdf"),
    ("GET", "/api/tax-forms/1096/pdf"),
    ("GET", "/api/tax-forms/sui"),
    ("GET", "/api/tax-forms/liability"),
    # Legacy JSON-mode tax form endpoints — superseded by /pdf variants.
    # Kept for machine readers / future e-file integration.
    ("POST", "/api/payroll/forms/w2/{emp_id}"),
    ("POST", "/api/payroll/forms/w3/{year}"),
    ("POST", "/api/payroll/forms/940/{year}"),
    ("POST", "/api/payroll/forms/941/{year}/{quarter}"),
    # /decision is the canonical endpoint; /approve and /reject are aliases
    # the SPA actually calls. Decision route stays for scripts / API users.
    ("POST", "/api/pto/requests/{request_id}/decision"),
    # Admin / scripting utilities the SPA doesn't surface.
    ("POST", "/api/tax/mappings"),
    ("GET", "/api/tax/mappings"),
    ("POST", "/api/analytics/ai-query"),
    # Drill-down analytics endpoints. SPA uses /api/analytics/dashboard
    # which returns the bundled response. The per-card endpoints stay
    # for future "refresh this card" UI + API consumers.
    ("GET", "/api/analytics/revenue"),
    ("GET", "/api/analytics/expenses"),
    ("GET", "/api/analytics/cash-flow"),
    ("GET", "/api/analytics/profitability"),
    # Singular paystub fetch — SPA renders paystubs via the bulk list +
    # PDF endpoints. This route exists for direct linking / API consumers.
    ("GET", "/api/payroll/{run_id}/paystub/{stub_id}"),
    # Backup restore — dangerous; deliberately not exposed in the SPA.
    # Run via CLI: `python -m app.services.backup restore <file>`.
    ("POST", "/api/backups/restore"),
    # Stripe checkout — billing/upgrade flow not surfaced yet (product is
    # currently a single-tier release). Webhook handler already on this list.
    ("POST", "/api/stripe/create-checkout-session"),
    # Employee self-service "submit timecard" — meant to be called from
    # the employee portal, not the admin TimeEntriesPage (which uses
    # /approve and /reject). Portal time-entry UI is future work.
    ("POST", "/api/time-entries/{entry_id}/submit"),
    # Year-end PTO carryover — annual admin batch job, scheduled via
    # cron or run manually by the operator at fiscal year-end.
    ("POST", "/api/pto/accruals/year-end-carryover"),
    # Garnishment order delete — court-ordered garnishments must retain
    # an audit trail. This endpoint exists for data-correction overrides
    # only and is deliberately not surfaced in the deductions UI.
    ("DELETE", "/api/deductions/garnishments/{order_id}"),
    # DocumentAudit hash-chain viewer/verifier — endpoints ready, the
    # admin UI ("Compliance" tab) is future work (docs/todo.md).
    ("GET", "/api/document-audits"),
    ("GET", "/api/document-audits/{audit_id}"),
    ("GET", "/api/document-audits/verify/{content_hash}"),
}


def test_no_orphan_backend_routes(app_routes):
    """Every backend `/api/*` route either has a JS caller, is browser-
    accessed via an `<a href>`, or is on the intentional-backend-only list.

    Acts as a tripwire: a route that loses its caller (e.g. UI is refactored
    but the route stays) shows up here so we can either wire it back or
    explicitly mark it intentional.
    """
    calls = list(_collect_api_calls())
    orphans = []
    for method, rsegs in app_routes:
        # rsegs is already a segment list — reconstruct the path for the
        # human-readable orphan report and the exclusion check.
        rpath = "/".join(rsegs)
        if not rpath.startswith("/api/"):
            continue
        if (method, rpath) in _INTENTIONAL_BACKEND_ONLY:
            continue
        if "/portal/" in rpath:
            continue
        matched = any(
            (jm == method or jm == "*") and _route_matches(jpath.split("/"), rsegs)
            for _, _, jm, jpath in calls
        )
        if not matched:
            orphans.append(f"  {method:6} {rpath}")

    assert not orphans, (
        "Backend routes with no JS caller (add a caller, mark them as "
        "intentional in _INTENTIONAL_BACKEND_ONLY, or delete them):\n"
        + "\n".join(orphans)
    )
