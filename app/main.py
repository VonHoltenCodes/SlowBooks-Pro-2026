# ============================================================================
# Slowbooks Pro 2026 — "It's like QuickBooks, but we own the source code"
# An independent, from-scratch replacement for Intuit QuickBooks Pro 2003.
# No Intuit source code or binaries were decompiled, disassembled, or used.
# Everything derives from published SDK documentation (QBFC 5.0, qbXML 4.0),
# IIF files exported by our own licensed copy, and 14 years of using the
# product as a paying customer. Intuit's activation servers have been dead
# since ~2017; the hard drive with our licensed copy died in 2024. We just
# want to print invoices.
# ============================================================================

import re as _re
import time as _time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.services import storage
from app.services.rate_limit import limiter

from app.routes import (
    dashboard,
    accounts,
    customers,
    vendors,
    items,
    invoices,
    estimates,
    payments,
    sales_receipts,
    banking,
    reports,
    settings,
    iif,
)

# Phase 1: Foundation
from app.routes import audit, search

# Phase 2: Accounts Payable
from app.routes import purchase_orders, bills, bill_payments, credit_memos

# Phase 3: Productivity
from app.routes import recurring, batch_payments

# Phase 4: Communication & Export
from app.routes import csv as csv_routes
from app.routes import uploads

# Phase 5: Advanced Integration
from app.routes import bank_import, simplefin, tax, backups
from app.routes import users as users_routes
from app.routes import api_tokens as api_tokens_routes
from app.routes import classes as classes_routes
from app.routes import fx as fx_routes
from app.routes import fixed_assets as fixed_assets_routes
from app.routes import migration as migration_routes
from app.routes import opening_balances as opening_balances_routes

# Phase 6: Ambitious
from app.routes import companies, employees, payroll

# Phase 7: Online Payments
from app.routes import provider_payments, public

# Phase 8: QuickBooks Online
from app.routes import qbo

# Phase 9: Forum Bug Fixes & Missing Features
from app.routes import journal, deposits, cc_charges, checks

# Phase 10: Quick Wins + Medium Effort Features
from app.routes import bank_rules, budgets, attachments, email_templates

# Phase 9: Analytics (real-time business intelligence)
from app.routes import analytics

# Phase 9.7: Single-user authentication
from app.routes import auth as auth_routes

# System info + update check (desktop installs)
from app.routes import system as system_routes

# Phase 11: Inventory tracking + drill-down reports + saved reports
from app.routes import saved_reports

# Tier 1: Full payroll / HR system (onboarding, time entries, PTO)
from app.routes import time_entries, pto, tax_forms

# Tier 2: Advanced payroll (deductions and garnishments)
from app.routes import deductions

# Tier 3: HR admin + employee self-service portal
from app.routes import onboarding, portal
from app.routes import document_audit as document_audit_routes
from app.routes import reseller_permits as reseller_permits_routes
from app.services.auth import get_session_secret

from app import __version__
from app.config import (
    CORS_ALLOW_ORIGINS,
    FORCE_HTTPS,
    HSTS_MAX_AGE,
    SESSION_IDLE_TIMEOUT_SECONDS,
)
from app.database import SessionLocal, Base, engine
from app.services.audit import register_audit_hooks
from app.services.request_context import acting_username as _acting_username
from app.services.api_token_service import resolve as _resolve_api_token


def _run_startup_security_checks():
    """Fail hard on critical misconfigurations BEFORE touching the DB.

    Order matters: the env-var checks are cheap and don't need network
    I/O, so they run first. A misconfigured production deploy gets a
    clean error message instead of a Postgres connection traceback.
    """
    from app.config import APP_DEBUG, DATABASE_URL, PAYROLL_ENCRYPTION_SECRET

    _DEV_KEY = "slowbooks-dev-payroll-key-change-me"
    _is_real_db = not DATABASE_URL.startswith("sqlite")

    # UNCONDITIONAL guard (fires even under APP_DEBUG): the public dev
    # encryption key must never protect data in a real database. Without
    # this, setting APP_DEBUG=true in production "to debug an issue" would
    # silently leave every employee's bank PII decryptable with the key
    # that ships in the source tree. SQLite (dev/test) is exempt, so this
    # never trips local development or the test suite.
    if _is_real_db and PAYROLL_ENCRYPTION_SECRET == _DEV_KEY:
        raise RuntimeError(
            "FATAL: PAYROLL_ENCRYPTION_SECRET is the public dev default while "
            "connected to a non-SQLite database. All employee bank PII would be "
            "decryptable by anyone with the source code — even with APP_DEBUG=true. "
            "Set a unique, strong PAYROLL_ENCRYPTION_SECRET before deploying."
        )

    if not APP_DEBUG:
        if PAYROLL_ENCRYPTION_SECRET == _DEV_KEY:
            raise RuntimeError(
                "FATAL: PAYROLL_ENCRYPTION_SECRET has not been set in production. "
                "All employee bank account data would be decryptable by anyone with the source code. "
                "Set a unique, strong PAYROLL_ENCRYPTION_SECRET env var before deploying."
            )

        if not DATABASE_URL.startswith("sqlite"):
            if "sslmode" not in DATABASE_URL and "ssl" not in DATABASE_URL.lower():
                raise RuntimeError(
                    "FATAL: DATABASE_URL does not specify TLS mode in production. "
                    "Unencrypted database connections leak sensitive financial and payroll data. "
                    "Add sslmode=require (or sslmode=verify-full for cert validation) to DATABASE_URL. "
                    "Example: postgresql://user:pass@host:5432/db?sslmode=require"
                )

        if not FORCE_HTTPS:
            raise RuntimeError(
                "FATAL: FORCE_HTTPS=false in production. Plain-HTTP traffic leaks "
                "session cookies, portal tokens, and bank PII over the wire. Set "
                "FORCE_HTTPS=true (default in production) so the app redirects plain "
                "HTTP to HTTPS and emits HSTS. If terminating TLS at a proxy, the "
                "redirect becomes a no-op."
            )

    # Only after the cheap checks pass do we open a DB connection.
    Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan. Replaces the deprecated @app.on_event("startup") hook
    (removed in the Starlette 1.x line we pin). The security checks are the
    fail-hard production guard — keep them on the startup side of the yield
    so a misconfigured deploy never serves a single request."""
    _run_startup_security_checks()
    # At-rest upgrade: encrypt any legacy plaintext credential rows (SMTP,
    # payment, QBO, SimpleFIN secrets) on first boot after upgrading.
    try:
        from app.services.settings_service import upgrade_plaintext_secrets

        _db = SessionLocal()
        try:
            # Log a constant message only: interpolating anything derived
            # from the secret-touching upgrader trips CodeQL's clear-text-
            # logging taint (alert #38), and the count isn't worth arguing.
            if upgrade_plaintext_secrets(_db):
                print("Encrypted legacy plaintext secret settings at rest")
        finally:
            _db.close()
    except Exception:
        pass  # never block boot on the upgrader
    yield


# FastAPI 0.121+ serializes return values to JSON bytes directly via Pydantic
# (fast, and the reason ORJSONResponse was deprecated in 0.136). We let it use
# its default response class rather than pinning the now-deprecated ORJSON one.
app = FastAPI(
    title="Slowbooks Pro 2026",
    version=__version__,
    lifespan=lifespan,
)


# ---- Rate limiting (Phase 9.7) ----
# limiter is defined in app.services.rate_limit so routes can import it
# without circular-importing the app module. Toggle via RATE_LIMIT_ENABLED
# env var (tests use 0 to avoid per-process counter bleed).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---- CORS (Phase 9.7: locked down) ----
# Wildcard origins with credentials is a CSRF amplifier. Default to just
# localhost; override with ALLOWED_ORIGINS env var (comma-separated) for
# a custom LAN hostname like http://slowbooks.local:3001.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# gzip responses larger than 1 KB. Analytics JSON payloads compress ~70%,
# which is a big win over LAN for /api/analytics/dashboard and friends.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)


# Content-Security-Policy: defense in depth against XSS even if autoescape
# misses a sink. 'self' for scripts/styles + 'unsafe-inline' for the inline
# bootstrap script in index.html. Tighten to a nonce-based CSP once the SPA
# is migrated off inline scripts.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://js.stripe.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' https://api.stripe.com; "
    "frame-src https://js.stripe.com https://hooks.stripe.com; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "base-uri 'self'; "
    "object-src 'none'"
)


def _set_if_unset(headers, name: str, value: str) -> None:
    """Only write a header the route handler did not already set. Lets
    sensitive routes (portal, public pay page) opt into stricter values
    like Referrer-Policy: no-referrer."""
    if name not in headers:
        headers[name] = value


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    _set_if_unset(response.headers, "X-Content-Type-Options", "nosniff")
    _set_if_unset(response.headers, "X-Frame-Options", "DENY")
    _set_if_unset(
        response.headers, "Referrer-Policy", "strict-origin-when-cross-origin"
    )
    _set_if_unset(
        response.headers,
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )
    # WebView2's persistent desktop profile heuristically caches responses
    # that carry validators (ETag/Last-Modified) but no Cache-Control — after
    # an app update it kept rendering the previous version's cached HTML/JS.
    # no-cache still allows ETag/304 revalidation (free on localhost) but
    # forbids serving from cache without asking.
    _set_if_unset(response.headers, "Cache-Control", "no-cache")
    _set_if_unset(response.headers, "Content-Security-Policy", _CSP)
    # HSTS instructs browsers to refuse plain HTTP for HSTS_MAX_AGE seconds.
    # Only emit when HTTPS is actually enforced; sending it under plain HTTP
    # would lock users out if they later visit via http://.
    if FORCE_HTTPS:
        _set_if_unset(
            response.headers,
            "Strict-Transport-Security",
            f"max-age={HSTS_MAX_AGE}; includeSubDomains; preload",
        )
    return response


# Promote any plain-HTTP request to HTTPS before it touches the app. Added
# BEFORE other middleware so it runs LAST in the response chain — i.e. the
# OUTERMOST request gate. Behind a TLS-terminating proxy this is a no-op
# because the proxy already speaks HTTPS to the app.
if FORCE_HTTPS:
    app.add_middleware(HTTPSRedirectMiddleware)


# ---- Auth gate (Phase 9.7) ----
# Single middleware that lets through static assets, the SPA shell, the
# auth routes themselves, /health, and the public customer pay page.
# Everything else demands an authenticated session.
#
# IMPORTANT: This decorator MUST come before the SessionMiddleware
# add_middleware() call. Starlette's middleware stack wraps the
# most-recently-added layer on the outside, so the LAST add_middleware
# call is the outermost / runs first. SessionMiddleware needs to run
# BEFORE require_session so that request.session is populated.
_AUTH_EXEMPT_PREFIXES = (
    "/static/",
    "/api/auth/",
    "/pay/",  # public Stripe customer-facing pay page
    "/portal/",  # employee self-service portal — token-based auth, no session
)
_AUTH_EXEMPT_EXACT = {
    "/",
    "/health",
    # The published AI docs (llms.txt, ai/agents-template.md) tell agents to
    # fetch the spec FIRST and "discover endpoints from the spec; do not
    # guess paths" — so gating it behind auth made the documented flow
    # impossible: the first call an agent is told to make returned 401.
    # The spec is a description of the interface, not business data; every
    # operation behind it still enforces auth and role scoping.
    "/openapi.json",
    "/analytics",  # redirect to SPA hash route
    "/favicon.ico",
    "/api/stripe/webhook",  # legacy alias — Stripe auth via signature
}
# Provider payment routes that are public by design:
#   - webhook: the provider's signature is the authentication
#   - create-checkout-session: called from the unauthenticated /pay/{token}
#     page; the payment_token is the capability (and the route is
#     rate-limited). check-status is NOT here — it stays session-gated.
_AUTH_EXEMPT_RE = _re.compile(
    r"^/api/payments/[a-z0-9_]+/(webhook|create-checkout-session)$"
)


# ---------------------------------------------------------------------------
# Server Edition RBAC — coarse route-group policy, enforced centrally.
#
#   admin       everything
#   bookkeeper  everything except administrative writes (users, settings,
#               backups, companies, migration imports)
#   readonly    GET/HEAD/OPTIONS only
#
# Legacy sessions (issued before the principal model) carry no role and
# are treated as admin — they belong to the operator by definition.
# ---------------------------------------------------------------------------
_ADMIN_WRITE_PREFIXES = (
    "/api/users",
    "/api/tokens",
    "/api/settings",
    "/api/backups",
    "/api/companies",
    "/api/migration",
)
_READ_METHODS = ("GET", "HEAD", "OPTIONS")


def _role_allows(role: str, method: str, path: str) -> bool:
    if role == "admin":
        return True
    is_read = method in _READ_METHODS
    if role == "readonly":
        # Field finding: audit payloads snapshot full record contents
        # (tax ids, addresses) — "read the books" shouldn't mean "read
        # every historical value of every field".
        return is_read and not path.startswith("/api/audit")
    # bookkeeper: full read, all daily-books writes, no admin writes
    if is_read:
        return True
    return not path.startswith(_ADMIN_WRITE_PREFIXES)


@app.middleware("http")
async def require_session(request: Request, call_next):
    path = request.url.path
    if (
        path in _AUTH_EXEMPT_EXACT
        or path.startswith(_AUTH_EXEMPT_PREFIXES)
        or _AUTH_EXEMPT_RE.match(path)
    ):
        return await call_next(request)
    token_principal = None
    if request.session.get("authenticated") is True:
        role = request.session.get("role") or "admin"
    else:
        # Scoped API tokens: non-human principals (agents, integrations)
        # authenticate with `Authorization: Bearer sbp_...` and wear a role
        # exactly like a user. Session auth always wins when present.
        auth_header = request.headers.get("authorization") or ""
        if auth_header.startswith("Bearer "):
            token_principal = _resolve_api_token(auth_header[7:].strip())
        if token_principal is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Not authenticated"},
            )
        # Tokens cannot manage identities — a leaked bookkeeper token must
        # not be able to mint itself an admin token or a user account.
        if path.startswith(("/api/users", "/api/tokens")):
            return JSONResponse(
                status_code=403,
                content={"detail": "API tokens cannot manage users or tokens"},
            )
        role = token_principal["role"]
        # get_db reads this to stamp audit attribution ("token:<label>")
        request.state.token_principal = {
            "username": "token:" + token_principal["label"],
            "role": role,
        }

    if not _role_allows(role, request.method, path):
        return JSONResponse(
            status_code=403,
            content={"detail": "Your role doesn't allow this action"},
        )

    # Idle session cap. Sliding window — every authenticated hit refreshes
    # `last_activity`, so a session that's actively in use never trips this.
    # Disabled when SESSION_IDLE_TIMEOUT_SECONDS = 0 (test harness, dev).
    if SESSION_IDLE_TIMEOUT_SECONDS > 0 and token_principal is None:
        now = int(_time.time())
        last = request.session.get("last_activity")
        if isinstance(last, int) and (now - last) > SESSION_IDLE_TIMEOUT_SECONDS:
            request.session.clear()
            return JSONResponse(
                status_code=401,
                content={"detail": "Session expired (idle timeout)"},
            )
        request.session["last_activity"] = now

    return await call_next(request)


class ActingUserContextMiddleware:
    """Pure-ASGI middleware: stamp the acting username into a contextvar
    for the audit hooks (which live at the SQLAlchemy layer and can't see
    the request).

    Deliberately NOT a BaseHTTPMiddleware — those run the downstream app
    in a separate task, and contextvar propagation across that hop proved
    unreliable on the frozen Windows build (field report: every audit row
    showed no user despite the middleware setting the var). Pure ASGI
    runs in the same task; the value is visible to everything downstream
    unconditionally.

    Must be registered BEFORE SessionMiddleware's add_middleware call so
    SessionMiddleware wraps outside us and scope["session"] is populated.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        session = scope.get("session") or {}
        acting = None
        if session.get("authenticated") is True:
            acting = session.get("username") or "operator"
        token = _acting_username.set(acting)
        try:
            await self.app(scope, receive, send)
        finally:
            _acting_username.reset(token)


# ---- Session cookie (Phase 9.7) ----
app.add_middleware(ActingUserContextMiddleware)

# Added AFTER require_session so SessionMiddleware becomes the outer
# layer (Starlette: last added = outermost). That way request.session
# is populated by the time require_session dispatches.
app.add_middleware(
    SessionMiddleware,
    secret_key=get_session_secret(),
    session_cookie="slowbooks_session",
    max_age=60 * 60 * 24 * 30,
    same_site="strict",
    # Cookie carries the Secure flag whenever HTTPS is enforced. Tied to the
    # same env var as the redirect middleware so the two stay in lockstep:
    # if the app insists on HTTPS, the session cookie must too.
    https_only=FORCE_HTTPS,
)

# Phase 9.7: Auth routes MUST be included (they're exempt from the session gate)
app.include_router(auth_routes.router)

# Original API routes
app.include_router(dashboard.router)
app.include_router(accounts.router)
app.include_router(classes_routes.router)
app.include_router(fx_routes.router)
app.include_router(fixed_assets_routes.router)
app.include_router(migration_routes.router)
app.include_router(opening_balances_routes.router)
app.include_router(customers.router)
app.include_router(vendors.router)
app.include_router(items.router)
app.include_router(invoices.router)
app.include_router(estimates.router)
app.include_router(payments.router)
app.include_router(sales_receipts.router)
app.include_router(banking.router)
app.include_router(reports.router)
app.include_router(settings.router)
app.include_router(iif.router)

# Phase 1: Foundation
app.include_router(audit.router)
app.include_router(search.router)
# Phase 2: Accounts Payable
app.include_router(purchase_orders.router)
app.include_router(bills.router)
app.include_router(bill_payments.router)
app.include_router(credit_memos.router)
# Phase 3: Productivity
app.include_router(recurring.router)
app.include_router(batch_payments.router)
# Phase 4: Communication & Export
app.include_router(csv_routes.router)
app.include_router(uploads.router)
# Phase 5: Advanced Integration
app.include_router(bank_import.router)
app.include_router(simplefin.router)
app.include_router(users_routes.router)
app.include_router(api_tokens_routes.router)
app.include_router(tax.router)
app.include_router(backups.router)
app.include_router(system_routes.router)
# Phase 6: Ambitious
app.include_router(companies.router)
app.include_router(employees.router)
app.include_router(payroll.router)
# Phase 7: Online Payments
app.include_router(provider_payments.router)
app.include_router(provider_payments.legacy_stripe_router)
app.include_router(public.router)
# Phase 8: QuickBooks Online
app.include_router(qbo.router)
# Phase 9: Analytics (real-time business intelligence)
app.include_router(analytics.router)
# Phase 9: Forum Bug Fixes & Missing Features
app.include_router(journal.router)
app.include_router(deposits.router)
app.include_router(cc_charges.router)
app.include_router(checks.router)
# Phase 10: Quick Wins + Medium Effort Features
app.include_router(bank_rules.router)
app.include_router(budgets.router)
app.include_router(attachments.router)
app.include_router(email_templates.router)
# Phase 11: Saved Reports (inventory endpoints live on the items router)
app.include_router(saved_reports.router)

# Tier 1: Onboarding, time entries, PTO management
app.include_router(time_entries.router)
app.include_router(pto.router)

# Tier 2: Advanced deductions, garnishments, and tax forms UI
app.include_router(deductions.router)
app.include_router(tax_forms.router)

# Tier 3: Employee onboarding workflows + self-service portal
app.include_router(onboarding.router)
app.include_router(portal.router)
app.include_router(document_audit_routes.router)
app.include_router(reseller_permits_routes.router)

# Register audit log hooks
register_audit_hooks(SessionLocal)

# Static files. Uploads live outside the bundle on desktop installs
# (SLOWBOOKS_DATA_DIR) but keep their /static/uploads URLs — the more
# specific mount must be registered first so it wins over /static.
static_dir = Path(__file__).parent / "static"
uploads_dir = storage.uploads_root()
uploads_dir.mkdir(parents=True, exist_ok=True)
if uploads_dir != static_dir / "uploads":
    app.mount(
        "/static/uploads",
        StaticFiles(directory=str(uploads_dir)),
        name="static-uploads",
    )
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# SPA entry point
index_path = Path(__file__).parent.parent / "index.html"


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(static_dir / "favicon.ico")


@app.get("/health")
async def health_check():
    """Liveness probe. Always on, no auth. Used by load balancers,
    k8s probes, and uptime monitors."""
    return {"status": "ok", "version": app.version}


@app.get("/")
async def serve_index():
    return FileResponse(str(index_path))


@app.get("/analytics")
async def serve_analytics_redirect():
    """Backwards-compat: old /analytics bookmarks land on the SPA hash route.

    The analytics UI is now integrated inline as #/analytics inside the
    main SPA shell (see app/static/js/analytics.js). Anyone hitting the
    bare path gets redirected to the same feature.
    """
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/#/analytics", status_code=307)


# ---------------------------------------------------------------------------
# OpenAPI: declare the auth the API actually enforces.
#
# components.securitySchemes was empty and there was no top-level `security`,
# so the spec described an API that needs no credentials — while every
# operation behind it returns 401 without a bearer token. That matters
# specifically because llms.txt and ai/agents-template.md tell agents to
# "discover endpoints from the spec; do not guess paths": a client generated
# from the spec emitted no Authorization header and 401'd on every call.
#
# Applied globally, with the genuinely public routes exempted so the document
# stays truthful in both directions.
# ---------------------------------------------------------------------------
_PUBLIC_FOR_SPEC = {"/", "/health", "/openapi.json", "/analytics", "/favicon.ico"}
_PUBLIC_PREFIXES_FOR_SPEC = ("/api/auth/", "/pay/", "/portal/", "/static/")


def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    from fastapi.openapi.utils import get_openapi

    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    schema.setdefault("components", {})["securitySchemes"] = {
        "BearerToken": {
            "type": "http",
            "scheme": "bearer",
            "description": (
                "Scoped API token from Settings -> API Tokens, sent as "
                "`Authorization: Bearer sbp_...`. Session cookies from "
                "POST /api/auth/login are accepted equivalently."
            ),
        }
    }
    schema["security"] = [{"BearerToken": []}]

    for path, item in schema.get("paths", {}).items():
        if path in _PUBLIC_FOR_SPEC or path.startswith(_PUBLIC_PREFIXES_FOR_SPEC):
            for operation in item.values():
                if isinstance(operation, dict):
                    operation["security"] = []

    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi
