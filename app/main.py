# ============================================================================
# Slowbooks Pro 2026 — "It's like QuickBooks, but we own the source code"
# A from-scratch homage to Intuit QuickBooks Pro 2003 (Build 12.0.3190).
# Spiritual ancestor: QBW32.EXE (14,823,424 bytes, PE32 MSVC++ 6.0 SP5)
# Imagined decompilation target: CQBMainApp (WinMain @ 0x00401000)
# ============================================================================
# LEGAL: This is an independent reimplementation. No Intuit source code or
# binaries were decompiled, disassembled, or used — the MFC class names and
# hex "offsets" sprinkled through this codebase are invented retro flavor,
# an affectionate fiction. All actual knowledge derived from:
#   1. Published Intuit SDK documentation (QBFC 5.0, qbXML 4.0)
#   2. 14 years of clicking every menu item as a paying customer
#   3. IIF files exported by our own licensed copy
#   4. Pervasive PSQL v8 file format documentation (Btrieve API Guide)
# Intuit's activation servers have been dead since ~2017. The hard drive
# that had our licensed copy died in 2024. We just want to print invoices.
# ============================================================================

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
from app.routes import bank_import, tax, backups

# Phase 6: Ambitious
from app.routes import companies, employees, payroll

# Phase 7: Online Payments
from app.routes import stripe_payments, public

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

from app.config import (
    CORS_ALLOW_ORIGINS,
    FORCE_HTTPS,
    HSTS_MAX_AGE,
    SESSION_IDLE_TIMEOUT_SECONDS,
)
from app.database import SessionLocal, Base, engine
from app.services.audit import register_audit_hooks


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
    yield


# FastAPI 0.121+ serializes return values to JSON bytes directly via Pydantic
# (fast, and the reason ORJSONResponse was deprecated in 0.136). We let it use
# its default response class rather than pinning the now-deprecated ORJSON one.
app = FastAPI(
    title="Slowbooks Pro 2026",
    version="2.0.0",
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
    "/analytics",  # redirect to SPA hash route
    "/favicon.ico",
    "/api/stripe/webhook",  # Stripe auth via signature, not session
}


@app.middleware("http")
async def require_session(request: Request, call_next):
    path = request.url.path
    if path in _AUTH_EXEMPT_EXACT or path.startswith(_AUTH_EXEMPT_PREFIXES):
        return await call_next(request)
    if request.session.get("authenticated") is not True:
        return JSONResponse(
            status_code=401,
            content={"detail": "Not authenticated"},
        )

    # Idle session cap. Sliding window — every authenticated hit refreshes
    # `last_activity`, so a session that's actively in use never trips this.
    # Disabled when SESSION_IDLE_TIMEOUT_SECONDS = 0 (test harness, dev).
    if SESSION_IDLE_TIMEOUT_SECONDS > 0:
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


# ---- Session cookie (Phase 9.7) ----
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
app.include_router(customers.router)
app.include_router(vendors.router)
app.include_router(items.router)
app.include_router(invoices.router)
app.include_router(estimates.router)
app.include_router(payments.router)
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
app.include_router(tax.router)
app.include_router(backups.router)
# Phase 6: Ambitious
app.include_router(companies.router)
app.include_router(employees.router)
app.include_router(payroll.router)
# Phase 7: Online Payments
app.include_router(stripe_payments.router)
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
