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

import logging
import math
import os
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
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import DataError

from app.services.control_accounts import MissingControlAccount
from app.services.rate_limit import limiter

from app.routes import (
    dashboard,
    accounts,
    customers,
    customers_past,
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
from app.routes import email_log as email_log_routes

# Phase 2: Accounts Payable
from app.routes import purchase_orders, bills, bill_payments, credit_memos
from app.routes import vendor_credits

# Phase 3: Productivity
from app.routes import recurring, batch_payments, memorized

# Phase 4: Communication & Export
from app.routes import csv as csv_routes
from app.routes import uploads

# Phase 5: Advanced Integration
from app.routes import bank_import, simplefin, mercury, tax, backups
from app.routes import company_file as company_file_routes
from app.routes import accountants_copy as accountants_copy_routes
from app.routes import reclassify as reclassify_routes
from app.routes import users as users_routes
from app.routes import api_tokens as api_tokens_routes
from app.routes import classes as classes_routes
from app.routes import jobs as jobs_routes
from app.routes import preferences as preferences_routes
from app.routes import cost_codes as cost_codes_routes
from app.routes import job_costing as job_costing_routes
from app.routes import nonprofit as nonprofit_routes
from app.routes import construction as construction_routes
from app.routes import trade_packages as trade_packages_routes
from app.routes import change_orders as change_orders_routes
from app.routes import pay_applications as pay_applications_routes
from app.routes import lien_waivers as lien_waivers_routes
from app.routes import donors as donors_routes
from app.routes import in_kind as in_kind_routes
from app.routes import fx as fx_routes
from app.routes import fixed_assets as fixed_assets_routes
from app.routes import migration as migration_routes
from app.routes import opening_balances as opening_balances_routes

# Phase 6: Ambitious
from app.routes import companies, employees, payroll

# Phase 7: Online Payments
from app.routes import provider_payments, public

# Phase 9: Forum Bug Fixes & Missing Features
from app.routes import journal, deposits, cc_charges, checks, expenses, transfers

# Phase 10: Quick Wins + Medium Effort Features
from app.routes import bank_rules, budgets, attachments, email_templates
from app.routes import invoice_messages as invoice_messages_routes
from app.routes import payment_terms as payment_terms_routes

# Phase 9: Analytics (real-time business intelligence)
from app.routes import analytics

# Phase 9.7: Single-user authentication
from app.routes import auth as auth_routes

# System info + update check (desktop installs)
from app.routes import system as system_routes

# In-app "Contact support": read-only diagnostics for the mailto form
from app.routes import support as support_routes

# Phase 11: Inventory tracking + drill-down reports + saved reports
from app.routes import saved_reports

# Tier 1: Full payroll / HR system (onboarding, time entries, PTO)
from app.routes import time_entries, pto, tax_forms

# Tier 2: garnishments + the benefits engine
from app.routes import deductions, benefits

# Tier 3: HR admin + employee self-service portal
from app.routes import onboarding, portal
from app.routes import document_audit as document_audit_routes
from app.routes import reseller_permits as reseller_permits_routes

# Tier 2: Receipt / document intake — local OCR (docs/design/receipt-intake.md)
from app.routes import ocr as ocr_routes
from app.services.auth import get_session_secret
from app.services.auth import (
    signed_in_before_this_start as _signed_in_before_this_start,
    signed_in_to_another_company as _signed_in_to_another_company,
    check_session_user as _check_session_user,
    SESSION_VERSION_KEY as _SESSION_VERSION_KEY,
)

from app import __version__
from app.config import (
    CORS_ALLOW_ORIGINS,
    FORCE_HTTPS,
    HSTS_MAX_AGE,
    SESSION_IDLE_TIMEOUT_SECONDS,
)
from app.database import SessionLocal, Base, engine
from app.database import _served_company
from app.services.audit import register_audit_hooks
from app.services.request_context import acting_username as _acting_username
from app.services.request_context import (
    closing_date_password as _closing_date_password,
)
from app.services.closing_date import (
    PASSWORD_HEADER as _CLOSING_PASSWORD_HEADER,
    password_from_header as _closing_password_from_header,
)
from app.services.api_token_service import resolve as _resolve_api_token
from app.services.served_companies import CompanyUnavailable, sentence_of

# An API token's request names the company it works in with this header
# (#358); a signed-in person's session carries its company instead.
_COMPANY_HEADER = "X-SlowBooks-Company"


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

    # A configured rate-limit storage that cannot be reached would otherwise
    # surface as a 500 on every rate-limited route (login first of all).
    _check_rate_limit_storage()

    if not APP_DEBUG:
        if PAYROLL_ENCRYPTION_SECRET == _DEV_KEY:
            raise RuntimeError(
                "FATAL: PAYROLL_ENCRYPTION_SECRET has not been set in production. "
                "All employee bank account data would be decryptable by anyone with the source code. "
                "Set a unique, strong PAYROLL_ENCRYPTION_SECRET env var before deploying."
            )

        # The single-host install: `docker compose up` puts Postgres on the
        # compose-internal bridge and serves the app on http://localhost.
        # Both transport guards below are about traffic leaving the host,
        # which that traffic never does — but the guards fired anyway, and
        # the documented Docker path has refused to start since they landed
        # (2.9.0 Linux gate). docker-compose.yml sets this flag and says so;
        # anyone exposing the stack beyond the host puts a TLS proxy in
        # front (docs/tls-proxy-setup.md), sets FORCE_HTTPS=true and drops
        # the flag. The encryption-key guards above are never relaxed.
        if os.environ.get("SLOWBOOKS_PRIVATE_NETWORK") == "1":
            logging.getLogger("app.main").warning(
                "SLOWBOOKS_PRIVATE_NETWORK=1: serving plain HTTP and a "
                "non-TLS database connection on the assumption that neither "
                "leaves this host. Do not expose this instance beyond the "
                "host without a TLS proxy (docs/tls-proxy-setup.md)."
            )
            _create_missing_tables()
            return

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

        # Non-fatal: two proxy misconfigurations that silently weaken a
        # security control without any visible symptom. Only reached in
        # production (APP_DEBUG=false) and not on the SLOWBOOKS_PRIVATE_NETWORK
        # path above, which knowingly serves plain HTTP on a trusted host — so
        # the desktop app and local dev never see these warnings.
        _warn_on_proxy_misconfiguration()

    # Only after the cheap checks pass do we open a DB connection — and an
    # encrypted company file is not opened at all without its key (#290):
    # one sentence, before the migration guard or create_all can touch it.
    from app.services.company_file import refuse_a_locked_file

    refuse_a_locked_file()
    _refuse_a_database_behind_head()
    _create_missing_tables()


def _warn_on_proxy_misconfiguration() -> None:
    """Warn (never refuse) on two silent weakenings behind a reverse proxy.

    Both degrade a security control without any visible symptom — exactly the
    kind of thing that goes unnoticed for a year — but the app serves correctly
    either way, and failing hard would break existing deploys on upgrade.

    1. Client IP. uvicorn parses X-Forwarded-For but trusts it only from the
       addresses in --forwarded-allow-ips / FORWARDED_ALLOW_IPS (default
       127.0.0.1). A proxy in another container or pod has a different address,
       so the header is ignored and every request reports the PROXY's IP. That
       collapses the login rate limiter into one shared bucket for all users and
       writes the proxy's address into login_attempts and portal_accesses,
       making the audit trail useless for forensics. FORCE_HTTPS=true is the
       reliable signal here: it means TLS terminates somewhere (almost always a
       proxy, since the app serves plain HTTP), so an empty trust list is wrong.

    2. Rate-limit storage. In-process counters are per-worker and per-replica,
       so with APP_WORKERS>1 the effective limit is N times the configured one.
       Point RATE_LIMIT_STORAGE_URI at shared storage (e.g. redis://...).
    """
    from app.services.rate_limit import RATE_LIMIT_STORAGE_URI, _enabled

    log = logging.getLogger("uvicorn.error")

    if FORCE_HTTPS and not os.environ.get("FORWARDED_ALLOW_IPS", "").strip():
        log.warning(
            "FORWARDED_ALLOW_IPS is unset while FORCE_HTTPS is on, which implies "
            "TLS terminates at a proxy in front of the app. uvicorn trusts "
            "X-Forwarded-For only from 127.0.0.1, so a proxy in another "
            "container or pod makes every client IP read as the proxy's address: "
            "the login rate limiter degrades to one shared bucket for all users "
            "and login_attempts / portal_accesses lose forensic value. Set "
            "FORWARDED_ALLOW_IPS to your proxy's address or CIDR (never 0.0.0.0/0 "
            "or *, which lets any client forge its source IP)."
        )

    workers = int(os.environ.get("APP_WORKERS", "1") or "1")
    if _enabled and not RATE_LIMIT_STORAGE_URI and workers > 1:
        log.warning(
            "Rate limiting is on with APP_WORKERS=%d but RATE_LIMIT_STORAGE_URI "
            "is unset, so each worker keeps its own counters — every configured "
            "limit is effectively %dx looser (more still across replicas). Point "
            "RATE_LIMIT_STORAGE_URI at shared storage, e.g. redis://redis:6379/0.",
            workers,
            workers,
        )


def _check_rate_limit_storage() -> None:
    """Refuse to start when RATE_LIMIT_STORAGE_URI names storage that fails
    its health check. Unset (in-process memory) is never checked.

    slowapi builds the storage from the URI at import; a Redis that is down
    or mis-addressed only shows up when the first rate-limited request runs
    its counter, which turns every login into a 500. Fail once, loudly, here
    instead. The message names the scheme and host only — the URI may carry
    a password."""
    from urllib.parse import urlsplit

    from app.services.rate_limit import RATE_LIMIT_STORAGE_URI, limiter

    if not RATE_LIMIT_STORAGE_URI:
        return
    parts = urlsplit(RATE_LIMIT_STORAGE_URI)
    where = f"{parts.scheme}://{parts.hostname or ''}" + (
        f":{parts.port}" if parts.port else ""
    )
    try:
        healthy = limiter._storage.check()
    except Exception as exc:  # the limits backends wrap their own errors
        raise RuntimeError(
            f"FATAL: RATE_LIMIT_STORAGE_URI storage at {where} failed its "
            f"health check ({type(exc).__name__}: {exc}). Start the storage "
            "service, fix the URI, or unset RATE_LIMIT_STORAGE_URI for "
            "in-process counters (single worker only)."
        ) from exc
    if not healthy:
        raise RuntimeError(
            f"FATAL: RATE_LIMIT_STORAGE_URI storage at {where} is unreachable. "
            "Start the storage service, fix the URI, or unset "
            "RATE_LIMIT_STORAGE_URI for in-process counters (single worker only)."
        )


def _refuse_a_database_behind_head(target=None) -> None:
    """Refuse to start against a database the migrations have not reached.

    Issue #132, found by the macOS QA agent while gating 2.11.0.
    `_create_missing_tables()` runs `create_all()` on EVERY start. Point that
    at a database behind the migration head and it half-upgrades it: tables
    the new revision ADDS are created, tables it ALTERS are untouched, and
    `alembic_version` does not move. Alembic can then never run on that
    database again — the upgrade tries to create tables that already exist.

    The damage is silent at the moment it happens and loud much later, in a
    different session, as a start failure with no obvious cause.

    Neither shipped path reaches it: `desktop_launcher.py` runs
    `alembic upgrade head` before serving, and `docker-entrypoint.sh` runs it
    at line 21. A self-managed deployment that sets DATABASE_URL, runs
    uvicorn directly and treats migrations as a separate step is on exactly
    that path — and so is `--_serve`, which is how the agent met it.

    A database with no `alembic_version` at all is a NEW one and is allowed
    through: that is how a fresh company file and a fresh Docker volume both
    start.
    """
    from sqlalchemy import inspect as _inspect, text as _text

    # ``target``: another company a Server Edition server serves (#358),
    # checked the first time a session opens it
    checked = engine if target is None else target
    try:
        insp = _inspect(checked)
        if not insp.has_table("alembic_version"):
            return  # fresh database — create_all is how it gets built
        with checked.connect() as conn:
            row = conn.execute(_text("SELECT version_num FROM alembic_version")).first()
        current = row[0] if row else None
    except Exception:
        # A guard that cannot read the thing it guards must not pass it
        # silently. The agent's own version of this check had `except:
        # return`, which let a file through BY FAILING TO READ IT while it
        # was mid-copy. Say so and let the start proceed: refusing here
        # would turn any transient DB blip into a failure to boot.
        logging.getLogger(__name__).exception(
            "could not read alembic_version; skipping the migration-head check"
        )
        return

    if current is None:
        return

    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        root = Path(__file__).resolve().parent.parent
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "migrations"))
        head = ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:
        logging.getLogger(__name__).exception(
            "could not determine the migration head; skipping the check"
        )
        return

    if head and current != head:
        # Two different situations, and only one of them is fixed by the
        # obvious command. An ORDINARY old file upgrades cleanly. A file a
        # server has already half-upgraded does NOT: the tables the pending
        # revisions create are already there, so the upgrade dies on
        # "already exists". Printing `alembic upgrade head` at someone in
        # that state is advice that fails — the same shape as #139's delete
        # error saying "deactivate it instead" when nothing could deactivate.
        from app.services.schema_repair import looks_half_upgraded

        if looks_half_upgraded(checked):
            # Name the path that exists on THIS install. Frozen bundles put
            # it under _internal/scripts; a checkout has it at scripts/.
            # Printing the repo path at a Server Edition operator who only
            # has the bundle is the defect #144 was about.
            from app.services.schema_repair import repair_command

            remedy = (
                "A server has already been started against this database "
                "while it was behind, so `alembic upgrade head` will fail on "
                "a table that already exists. Repair it with:\n"
                f"    {repair_command()} --database-url <url>\n"
                "which drops only the empty tables left behind and then "
                "upgrades. Take a copy first."
            )
        else:
            remedy = (
                "Run `alembic upgrade head` against it first. (The desktop "
                "app and the Docker entrypoint both do this for you; a "
                "self-managed deployment must run it as its own step.)"
            )
        raise RuntimeError(
            f"FATAL: this database is at migration '{current}' and this build "
            f"expects '{head}'. Starting anyway would create the new tables "
            f"without altering the existing ones and without moving the "
            f"revision, after which migrations could never run on it again. "
            f"{remedy}"
        )


def _create_missing_tables(target=None) -> None:
    """create_all for whatever the migrations do not cover — serialized.

    Under Docker the entrypoint starts uvicorn with two workers, and each
    worker runs this lifespan. Two concurrent create_all() calls on a
    fresh Postgres race on CREATE TYPE for the enums (`duplicate key
    value violates unique constraint "pg_type_typname_nsp_index"`): one
    worker dies, uvicorn stops the parent, the container restarts, and
    any client mid-request sees the connection dropped (2.9.0 Linux
    gate). A transaction-scoped advisory lock makes the second worker
    wait for the first; checkfirst then finds everything present.
    SQLite has one process and no enum types, so it takes the plain path.
    ``target`` is another company a Server Edition server serves (#358).
    """
    checked = engine if target is None else target
    if checked.dialect.name == "postgresql":
        from sqlalchemy import text

        with checked.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(7264013)"))
            Base.metadata.create_all(bind=conn)
        return
    Base.metadata.create_all(bind=checked)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan. Replaces the deprecated @app.on_event("startup") hook
    (removed in the Starlette 1.x line we pin). The security checks are the
    fail-hard production guard — keep them on the startup side of the yield
    so a misconfigured deploy never serves a single request."""
    _run_startup_security_checks()
    try:
        from app.services.company_service import warn_if_manifest_missing

        warn_if_manifest_missing()
    except Exception:
        pass  # a diagnostic, never a reason not to boot
    # Issue #119: say once, at boot, if this company's chart is missing an
    # account the posting code resolves by number. Before 2.10.1 the first
    # symptom was a document that looked saved and never reached the ledger;
    # now the posting refuses, and this is the warning that gets ahead of it.
    # A diagnostic, never fatal — refusing to boot would lock an operator out
    # of the very chart they need to repair.
    try:
        from app.services.control_accounts import missing as _missing_controls

        _db = SessionLocal()
        try:
            gaps = _missing_controls(_db)
        finally:
            _db.close()
        if gaps:
            logging.getLogger(__name__).warning(
                "chart of accounts is missing %d control account(s): %s — "
                "documents that need them will be refused (409) until they are "
                "restored with these exact numbers",
                len(gaps),
                ", ".join(f"{n} {name}" for n, name in gaps),
            )
    except Exception:
        pass  # a diagnostic, never a reason not to boot
    # At-rest upgrade: encrypt any legacy plaintext credential rows (SMTP,
    # payment, SimpleFIN secrets) on first boot after upgrading, and drop
    # the rows of a retired integration's settings.
    try:
        from app.services.settings_service import (
            remove_retired_settings,
            upgrade_plaintext_secrets,
        )

        _db = SessionLocal()
        try:
            # Log a constant message only: interpolating anything derived
            # from the secret-touching upgrader trips CodeQL's clear-text-
            # logging taint (alert #38), and the count isn't worth arguing.
            if upgrade_plaintext_secrets(_db):
                print("Encrypted legacy plaintext secret settings at rest")
            if remove_retired_settings(_db):
                print("Removed the settings of a retired integration")
            # #317: invoices the retired QuickBooks Online import counted as
            # paid twice get their paid amount, balance and status back
            from app.services.qbo_documents import repair_double_counted_payments

            if repair_double_counted_payments(_db):
                print(
                    "Repaired invoices the QuickBooks Online import counted as paid twice"
                )
            # #490: before 2.29.0 a reclassify moved the line and left the
            # stored balance on the old account. The totals are recomputed
            # from the ledger once here; books that agree have nothing to do.
            from app.services.accounting import rebuild_stored_balances

            fixed = rebuild_stored_balances(_db)
            if fixed:
                print(
                    f"Recomputed {len(fixed)} stored account balance(s) from the ledger"
                )
            # #483: a file from before ledger lines carried a basis gets its
            # accrual tags and the cash-basis lines of its payments once.
            from app.services.cash_basis import backfill as cash_basis_backfill

            done = cash_basis_backfill(_db)
            if done["tagged"] or done["payments"] or done["bill_payments"]:
                print(
                    "Cash basis: tagged %d accrual line(s), wrote cash lines for "
                    "%d payment(s) and %d bill payment(s)"
                    % (done["tagged"], done["payments"], done["bill_payments"])
                )
            if done["failed"]:
                # each failure is logged by payment id; it is retried next start
                logging.getLogger("uvicorn.error").warning(
                    "Cash basis: %d payment(s) could not be given cash lines "
                    "and were skipped; see the log above for each",
                    done["failed"],
                )
        finally:
            _db.close()
    except Exception:
        pass  # never block boot on the upgrader
    # #605 (2.31.1 gate W-66): enter what is due when the app opens, and
    # once a day while it runs. A background task; the first request is
    # not kept waiting for a backlog, and the Memorized transactions page
    # says what was entered. Every way the app is served runs this
    # lifespan: the desktop launcher's --_serve child (desktop_launcher.py
    # _serve passes app.main.app to uvicorn), Server Edition and Docker.
    from app.services import scheduler as _scheduler

    _auto_enter = _scheduler.start()
    try:
        yield
    finally:
        await _scheduler.stop(_auto_enter)


# FastAPI 0.121+ serializes return values to JSON bytes directly via Pydantic
# (fast, and the reason ORJSONResponse was deprecated in 0.136). We let it use
# its default response class rather than pinning the now-deprecated ORJSON one.
app = FastAPI(
    title="Slowbooks Pro 2026",
    version=__version__,
    lifespan=lifespan,
    description=(
        "Local bookkeeping API. Conventions an agent needs before writing:\n\n"
        "- **Unknown fields are rejected** (422 naming the field); nothing is "
        "silently dropped.\n"
        "- **Posted documents are voided, not deleted**: `POST /api/<resource>/"
        "{id}/void` (invoices, bills, payments, bill payments, credit memos, "
        "expenses, journal entries, in-kind gifts, job costs). `DELETE` on one "
        "answers 405 and names the void route. A pledge that will not be paid "
        "is written off (`POST /api/invoices/{id}/write-off`), not voided.\n"
        "- **`tax_rate` on a document is a fraction** (0.089 = 8.9%), kept "
        "to six places (0.08875 = 8.875%); `default_tax_rate` in settings is "
        'a percent string ("8.9"). Divide by 100.\n'
        "- **`terms` names an entry on `/api/payment-terms`** (its rule gives "
        "the due date and any early-payment discount; the document keeps the "
        "term it was saved under); text that is neither on the list nor "
        "`Net N` is due in 30 days and the document's `terms_note` says so. "
        "A payment's `allocations[].discount` must be one the document's "
        "terms offer, else an administrator sends `allow_discount_override`, "
        "which the payment records.\n"
        "- Enumerated fields are enums in this spec; read the allowed values "
        "here rather than guessing."
    ),
)


# ---- Rate limiting (Phase 9.7) ----
# limiter is defined in app.services.rate_limit so routes can import it
# without circular-importing the app module. Toggle via RATE_LIMIT_ENABLED
# env var (tests use 0 to avoid per-process counter bleed).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def _company_terms():
    """The vocabulary the company sees, read fresh: one settings row. Never
    raises — an error response must not fail because of its wording."""
    from app.services.terminology import Terms, terms_from_db

    # Looked up on the module, not bound at import: the test harness and
    # the desktop launcher both repoint app.database.SessionLocal.
    import app.database as database

    try:
        db = database.SessionLocal()
        try:
            return terms_from_db(db)
        finally:
            db.close()
    except Exception:
        return Terms("business")


async def _method_not_allowed_handler(request: Request, exc: StarletteHTTPException):
    """Two things every HTTP error passes through.

    A bare 405 on DELETE /api/<doc>/<id> names nothing. Posted documents
    are never deleted — they are voided, which keeps the audit trail and
    reverses the ledger — and the void route exists one segment further
    down. Say so in the body (2.9.0 gate: an agent rebuilt a whole fixture
    to work around a 405 that could have pointed at POST .../void).

    And the words. Every page label goes through the terminology
    dictionary, and the sentences the server sends back never did — a
    nonprofit's Pledge screen said "Invoice not found" under it. Forty-two
    such sentences across the routes, three files wrapping any of them.
    Rather than forty-two edits, the swap happens here, once, at the
    boundary every HTTPException crosses: whole-word, case-preserving,
    and the protected words ("Sales Tax") stay by the dictionary's own
    rule. The vocabulary audit's walk of a running server is what found
    it (scripts/audit/vocab_walk.py)."""
    if isinstance(exc.detail, str) and exc.detail and exc.status_code != 405:
        terms = _company_terms()
        if terms.has_words:
            worded = terms.text(exc.detail)
            if worded != exc.detail:
                return JSONResponse(
                    status_code=exc.status_code,
                    headers=exc.headers,
                    content={"detail": worded},
                )
    if exc.status_code == 405 and request.method == "DELETE":
        candidate = request.url.path.rstrip("/") + "/void"
        # The spec is the flat, cached view of every mounted router.
        for template, ops in request.app.openapi().get("paths", {}).items():
            if "post" not in ops or not template.endswith("/void"):
                continue
            pattern = "^" + _re.sub(r"\{[^}]+\}", r"[^/]+", template) + "$"
            if _re.match(pattern, candidate):
                return JSONResponse(
                    status_code=405,
                    headers=exc.headers,
                    content={
                        "detail": (
                            "Posted documents are voided, not deleted: "
                            f"use POST {candidate}"
                        )
                    },
                )
    return await http_exception_handler(request, exc)


app.add_exception_handler(StarletteHTTPException, _method_not_allowed_handler)


# ---- Missing control account (issue #119) ----
# A posting path that cannot resolve the account it must debit or credit
# now raises instead of silently skipping its journal entry. Answer 409 —
# the request was valid, the company's chart is not — and name the account
# so the operator can restore it. Nothing was written: the raise happens
# before any journal line is built.
async def _missing_control_account_handler(request: Request, exc: Exception):
    logging.getLogger(__name__).warning(
        "posting refused: control account %s (%s) missing from the chart",
        getattr(exc, "number", "?"),
        getattr(exc, "name", "?"),
    )
    # "what customers owe — every invoice and payment" is written in the
    # business words; the company may not use them (see the handler above).
    return JSONResponse(
        status_code=409, content={"detail": _company_terms().text(str(exc))}
    )


app.add_exception_handler(MissingControlAccount, _missing_control_account_handler)


# ---- Another company this server cannot open now (#358) ----
# A session chose a company the server serves, and its file is missing,
# locked, or from another version. 503 with the one sentence, never a
# traceback; the person can sign in to another company meanwhile.
async def _company_unavailable_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=503, content={"detail": sentence_of(exc)})


app.add_exception_handler(CompanyUnavailable, _company_unavailable_handler)


# ---- A value the database refuses (422, not 500) ----
# PostgreSQL enforces column widths and types that SQLite does not: a
# 301-character split memo, a number out of range. That is a problem with
# the data, not a server fault, and nothing was written (the request's
# session rolls back). Answer 422 with a fixed sentence; the driver's text
# carries the statement and its parameters, so it goes to the log only.
async def _data_error_handler(request: Request, exc: Exception):
    logging.getLogger(__name__).warning(
        "database refused a value: %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=422,
        content={
            "detail": "A value in this request is too long or not valid for "
            "the database, so nothing was saved."
        },
    )


app.add_exception_handler(DataError, _data_error_handler)


# ---- Validation errors (422), in sentences ----
# FastAPI's own body, unchanged, with a plain "message" added to each entry
# ("Name is required.") for the page to show instead of validator text
# ("name: String should have at least 1 character" — explore 2.17.3, L5).
def _json_safe(value):
    """A 422 echoes what was sent, and JSON has no NaN or Infinity: a body
    carrying one (Python's JSON reader accepts them) turned the refusal
    into a 500. Name them as text instead."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


async def _request_validation_handler(request: Request, exc: RequestValidationError):
    from app.services.validation_messages import with_messages

    terms = _company_terms()
    wording = terms.text if terms.has_words else None
    errors = _json_safe(jsonable_encoder(exc.errors()))
    return JSONResponse(
        status_code=422,
        content={"detail": with_messages(errors, wording)},
    )


app.add_exception_handler(RequestValidationError, _request_validation_handler)

# ---- Middleware registration ----
# All middleware is registered as one ordered block near the end of this
# module (the "Middleware stack" section), after require_session and
# ActingUserContextMiddleware are defined. Starlette makes the LAST-added
# middleware the OUTERMOST layer, and CORS plus the security-header
# middleware MUST sit OUTSIDE the auth gate (require_session): a CORS
# preflight is unauthenticated and has to be answered before auth, and the
# gate's own 401/403/503 responses have to carry the security headers.
# security_headers and require_session are only DEFINED below; the single
# registration block at the end wires the whole stack in the right order.


# Content-Security-Policy: defense in depth against XSS even if autoescape
# misses a sink. 'self' for scripts/styles + 'unsafe-inline' for the inline
# bootstrap script in index.html. Tighten to a nonce-based CSP once the SPA
# is migrated off inline scripts.
# 'unsafe-eval' is added ONLY under the desktop launcher. pywebview builds
# every window.pywebview.api method with `new Function(...)` (its js/api.js),
# and WebKit enforces the page's CSP on that call even though pywebview
# injects the script itself: under the strict policy WKWebView threw
# "Refused to evaluate a string as JavaScript because 'unsafe-eval' ... is
# not an allowed source" and the bridge stayed permanently empty — Save PDF,
# print preview, Save backup, Show in folder, the company picker: all dead
# on macOS, silently (2.9.0 gate, round 3; the policy dates from v2.1.0, so
# every macOS build since then). Chromium lets injected scripts bypass CSP,
# which is why Windows never showed it. Measured on macbase1 with a
# three-way probe: strict CSP → EvalError; + 'unsafe-eval' → api populated;
# no CSP → api populated. A browser install (Server Edition / Docker) has
# no bridge and keeps the strict policy.
def _build_csp(desktop: bool) -> str:
    script_src = "script-src 'self' 'unsafe-inline'"
    if desktop:
        script_src += " 'unsafe-eval'"
    script_src += " https://js.stripe.com; "
    return (
        "default-src 'self'; " + script_src + "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self' https://api.stripe.com; "
        "frame-src https://js.stripe.com https://hooks.stripe.com; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "base-uri 'self'; "
        "object-src 'none'"
    )


_CSP_STRICT = _build_csp(desktop=False)
_CSP_DESKTOP = _build_csp(desktop=True)
# The launcher sets this for every server it starts — the windowed app AND
# Server Edition's headless --serve-lan (system.py reads it for the update
# check), so the flag alone over-reaches: it served 'unsafe-eval' to LAN
# browsers (Keith, #98). The relaxation is for the native web view, which
# only ever connects from loopback, so require both.
_DESKTOP_FLAG = os.environ.get("SLOWBOOKS_DESKTOP") == "1"


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host in ("localhost", "::1"):
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _csp_for(request: Request) -> str:
    """The desktop policy only for the desktop shell: launcher flag set AND
    the request came over loopback. A LAN browser talking to --serve-lan
    gets the strict policy exactly like a Docker install."""
    client = request.client.host if request.client else None
    if _DESKTOP_FLAG and _is_loopback(client):
        return _CSP_DESKTOP
    return _CSP_STRICT


# Backwards-compatible name: the policy this process serves to its own shell.
_CSP = _CSP_DESKTOP if _DESKTOP_FLAG else _CSP_STRICT


def _set_if_unset(headers, name: str, value: str) -> None:
    """Only write a header the route handler did not already set. Lets
    sensitive routes (portal, public pay page) opt into stricter values
    like Referrer-Policy: no-referrer."""
    if name not in headers:
        headers[name] = value


async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # The desktop shell fetches documents from page JS and saves them
    # through its native bridge. Both WebView2 and WKWebView intercept a
    # Content-Disposition: attachment response at the network layer as a
    # download — the fetch() promise never resolves ("Failed to fetch" /
    # "Load failed"). app/routes/csv.py already served inline for these
    # requests; every other CSV and PDF producer had to remember to, and
    # the nonprofit report CSVs did not (2.9.0 gate: Save CSV → "Could not
    # load the document: Load failed" on macOS). Done once here instead.
    if request.headers.get("X-Slowbooks-Desktop"):
        disposition = response.headers.get("Content-Disposition", "")
        if disposition.lower().startswith("attachment"):
            response.headers["Content-Disposition"] = "inline" + disposition[10:]
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
    _set_if_unset(response.headers, "Content-Security-Policy", _csp_for(request))
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


# ---- Auth gate (Phase 9.7) ----
# Single middleware that lets through static assets, the SPA shell, the
# auth routes themselves, /health, and the public customer pay page.
# Everything else demands an authenticated session.
#
# IMPORTANT: require_session must run INSIDE SessionMiddleware so that
# request.session is populated by the time the gate dispatches, and INSIDE
# CORS and the security-header middleware so a preflight is answered before
# auth and the gate's own refusals still carry the headers. The registration
# that enforces this lives in the "Middleware stack" section at the end of
# this module; see it for the full outer-to-inner order.
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
    "/api/mercury/webhook",  # Mercury raw-body HMAC authenticates deliveries
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
    "/api/mercury/connect",
    # which Mercury account feeds which bank account wires the books, as
    # SimpleFIN's mapping is admin-only since 2.26.0 (#384)
    "/api/mercury/map",
    "/api/mercury/disconnect",
    "/api/mercury/webhook-settings",
    "/api/backups",
    # Turning company-file encryption on or off is an administrator's
    # decision (#290); the route asks for the company's name as well.
    "/api/company-file",
    # Sending the books to an accountant, opening a copy and applying the
    # changes back are an administrator's (#346); the copy's own Send
    # changes back is a read
    "/api/accountants-copy",
    "/api/companies",
    "/api/migration",
    # Staff records carry SSN, pay rate and W-4 elections: creating or
    # editing one is HR, not daily books (GHSA-rh75-6834-f66j).
    "/api/employees",
    # The saved invoice messages are company settings by another name
    # (#392): kept under Settings, written by an administrator.
    "/api/invoice-messages",
    # The terms list is company setup too (#664): kept under Settings.
    "/api/payment-terms",
)
# HR and payroll are admin functions, reads included: pay stubs, W-2s and
# 941s, NACHA files, benefit elections, garnishments and onboarding
# paperwork are the payroll clerk's, not the read-only reviewer's
# (docs/server-edition.md; GHSA-pwj7-6qq3-h4fj). Every method.
_ADMIN_ONLY_PREFIXES = (
    "/api/payroll",
    "/api/tax-forms",
    "/api/document-audits",  # the forms' history and hash checks (#263)
    "/api/benefits",
    "/api/deductions",
    "/api/onboarding",
)
# The parts of an employee record that are a credential or a bank account:
# the self-service portal token (a full login as that employee —
# GHSA-rh68-48w8-pj8r), direct-deposit accounts, I-9 and other documents,
# E-Verify, year-to-date pay and its opening balance (#433). The record
# itself stays listable by every role (time entries and job costing need
# the names) but is redacted for non-admins in the employees router.
_ADMIN_ONLY_EMPLOYEE_RE = _re.compile(
    r"^/api/employees/[^/]+/(portal-token|portal-access|everify|bank-accounts|documents|ytd|opening-ytd)(/|$)"
)
_READ_METHODS = ("GET", "HEAD", "OPTIONS")
# An outside accountant's sign-in (#340) writes journal entries only: it
# posts them, voids its own (the journal router checks whose), and attaches
# the support behind one. Everything else it reads, the audit log included:
# seeing what moved is the job.
# ... and moves lines between accounts with the reclassify tool (#337),
# the accountant's own job: nothing new is posted, and the audit log
# names every line.
_ACCOUNTANT_WRITE_RE = _re.compile(
    r"^/api/(journal(/\d+/void)?|attachments/journal_entry/\d+|reclassify)$"
)
ACCOUNTANT_REFUSAL = (
    "An accountant's sign-in posts journal entries and reads everything "
    "else; it cannot make this change. An administrator can change the "
    "role under Settings → Users."
)


def _is_hr_sensitive(path: str) -> bool:
    return path.startswith(_ADMIN_ONLY_PREFIXES) or bool(
        _ADMIN_ONLY_EMPLOYEE_RE.match(path)
    )


def _role_allows(role: str, method: str, path: str) -> bool:
    if role == "admin":
        return True
    if _is_hr_sensitive(path):
        return False
    is_read = method in _READ_METHODS
    if role == "readonly":
        # Field finding: audit payloads snapshot full record contents
        # (tax ids, addresses) — "read the books" shouldn't mean "read
        # every historical value of every field".
        return is_read and not path.startswith("/api/audit")
    if role == "accountant":
        # an outside role: the employee directory (pay rates, filing status,
        # the last four of a Social Security number) is HR's, not the
        # books' (2.24.0 gate, macbase1's question on round 1)
        if path.startswith("/api/employees"):
            return False
        return is_read or (method == "POST" and bool(_ACCOUNTANT_WRITE_RE.match(path)))
    # bookkeeper: full read, all daily-books writes, no admin writes
    if is_read:
        return True
    return not path.startswith(_ADMIN_WRITE_PREFIXES)


# What a token's "Can make backups" permission reaches (#434): make one,
# list them, download one. Restore is not here, and is refused to every
# token before the role is asked.
_TOKEN_BACKUP_DOWNLOAD_RE = _re.compile(r"^/api/backups/download/[^/]+$")
TOKEN_RESTORE_REFUSAL = (
    "An API token cannot restore a backup: restoring replaces the whole "
    "company, so it stays with an administrator signed in to SlowBooks Pro."
)


def _is_token_backup_route(method: str, path: str) -> bool:
    if path == "/api/backups":
        return method in ("GET", "HEAD", "POST")
    return method in ("GET", "HEAD") and bool(_TOKEN_BACKUP_DOWNLOAD_RE.match(path))


def _role_refusal(role: str) -> str:
    """The sentence a refused write gets: the accountant's says what the
    role is for (#340); the others keep the one the pages pin."""
    if role == "accountant":
        return ACCOUNTANT_REFUSAL
    return "Your role doesn't allow this action"


async def require_session(request: Request, call_next):
    try:
        return await _require_session(request, call_next)
    except CompanyUnavailable as exc:
        # The session's company cannot be opened now (#358): the gate itself
        # opens it (the company id, the copy's mode) before any route runs,
        # so the sentence is answered here too, as a 503, never a traceback.
        return JSONResponse(status_code=503, content={"detail": sentence_of(exc)})


async def _require_session(request: Request, call_next):
    path = request.url.path
    if (
        path in _AUTH_EXEMPT_EXACT
        or path.startswith(_AUTH_EXEMPT_PREFIXES)
        or _AUTH_EXEMPT_RE.match(path)
    ):
        return await call_next(request)
    token_principal = None
    backup_grant = False
    if request.session.get("authenticated") is True:
        if _signed_in_before_this_start(request.session):
            # Settings -> "Ask for the password each time SlowBooks Pro
            # starts": this session dates from before the app last started.
            request.session.clear()
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "SlowBooks Pro was restarted. Enter the password "
                    "to continue."
                },
            )
        if _signed_in_to_another_company(request.session):
            # A sign-in belongs to the company it was made in (R6-1).
            request.session.clear()
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "That sign-in was for another company. Enter this "
                    "company's password to continue."
                },
            )
        # Revocable sessions: the signed cookie is no longer trusted
        # on its own. Re-read the user this session names (one cheap lookup
        # on this company's DB) and sign the session out when it has been
        # deactivated, its role or password changed since, or the row is
        # gone. The role comes from the row too, so a demotion takes effect
        # on the next request instead of living in the cookie.
        state, db_role, db_version = _check_session_user(request.session)
        if state == "revoked":
            request.session.clear()
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "Your sign-in has ended. Sign in again to continue."
                },
            )
        if state == "ok":
            role = db_role
            # Keep the cookie's own copy honest: routes that gate on
            # request.session["role"] (user management, company switch) then
            # see the demotion too, not the stale role the cookie carried.
            if request.session.get("role") != db_role:
                request.session["role"] = db_role
            request.session[_SESSION_VERSION_KEY] = db_version
        else:  # "skip": a legacy / desktop session that names no user row
            role = request.session.get("role") or "admin"
    else:
        # Scoped API tokens: non-human principals (agents, integrations)
        # authenticate with `Authorization: Bearer sbp_...` and wear a role
        # exactly like a user. Session auth always wins when present.
        auth_header = request.headers.get("authorization") or ""
        if auth_header.startswith("Bearer "):
            token_principal = _resolve_api_token(auth_header[7:].strip())
        if token_principal is not None and token_principal.get("revoked"):
            # A revoked token says so (#434): the client that holds it is
            # usually unattended, and "Not authenticated" sent its operator
            # to the sign-in page. A secret that matches nothing stays vague.
            return JSONResponse(
                status_code=401,
                content={
                    "detail": (
                        f"The API token \"{token_principal['label']}\" was revoked. "
                        "Ask an administrator for a new one under Settings, API Tokens."
                    )
                },
            )
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
        # Restoring a backup replaces the whole company: a signed-in
        # administrator's, never a token's, whatever its role (#434).
        if path.startswith("/api/backups/restore"):
            return JSONResponse(
                status_code=403,
                content={"detail": TOKEN_RESTORE_REFUSAL},
            )
        role = token_principal["role"]
        allow_backups = bool(token_principal.get("allow_backups"))
        # get_db reads this to stamp audit attribution ("token:<label>")
        request.state.token_principal = {
            "username": "token:" + token_principal["label"],
            "role": role,
            "allow_backups": allow_backups,
        }
        # "Can make backups" (#434), set by an administrator on the token:
        # make, list and download this company's backups on top of the role.
        backup_grant = allow_backups and _is_token_backup_route(request.method, path)

    # A signed-in person's own preferences (#480: Enter moves between
    # fields, the decimal point; the dashboard's order) are theirs to set
    # whatever the role: they change nothing in the books, and the row is
    # the person's own (app.routes.preferences). Not a token's: a token has
    # no screen and no row of its own.
    own_preference = token_principal is None and path.startswith("/api/preferences/")
    # ...and a token may not write one: its row would be user_id NULL,
    # which on a password-only book is the operator's own preferences.
    if (
        token_principal is not None
        and path.startswith("/api/preferences/")
        and request.method not in _READ_METHODS
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "An API token has no preferences of its own to set."},
        )
    if (
        not backup_grant
        and not own_preference
        and not _role_allows(role, request.method, path)
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": _role_refusal(role)},
        )
    # An accountant's copy (#346) takes journal entries and nothing else,
    # whoever is signed in: the one permission the copy has.
    if not path.startswith("/api/auth/"):
        from app.services import accountants_copy as _ac

        copy = _ac.mode()
        if copy and not _ac.copy_gate(request.method, path):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": _ac.COPY_MODE_MESSAGE.format(day=copy["dividing_date"])
                },
            )
    if (
        token_principal is None
        and request.session.get("must_change_password")
        # the shell's own reads, which the page that sets the password is
        # drawn from: the version and the company's words (2.24.0 gate,
        # W-18 / NEW-149: refused, the shell stopped before the form)
        and not (
            request.method in _READ_METHODS and path in ("/api/system", "/api/settings")
        )
    ):
        # an invited sign-in with its one-time password (#345): the only
        # thing it can do is set a password of its own (the auth routes
        # are exempt above)
        return JSONResponse(
            status_code=403,
            content={
                "detail": "Set a password of your own first: the one you signed "
                "in with was for one use."
            },
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

    # A person is signed in to this server's own company: the app's own run
    # of the schedules, which waits for one when the company has a password,
    # may go (2.31.1 gate W-70). A sign-in the app remembers counts as much
    # as one typed; an API token, and a one-time password not yet replaced,
    # do not.
    if (
        token_principal is None
        and not request.session.get("must_change_password")
        and _served_company.get() is None
    ):
        from app.services import scheduler as _scheduler

        _scheduler.note_sign_in(
            request.session.get("username") or "operator",
            request.session.get("role"),
        )

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
        override = None
        if session.get("authenticated") is True:
            acting = session.get("username") or "operator"
            # The closing-date override password a signed-in person resent a
            # refused change with (get_db also stamps it on the Session).
            wanted = _CLOSING_PASSWORD_HEADER.lower().encode("latin-1")
            for name, value in scope.get("headers") or []:
                if name == wanted:
                    override = _closing_password_from_header(value.decode("latin-1"))
        token = _acting_username.set(acting)
        override_token = _closing_date_password.set(override)
        # The company this request works in (#358), here for the same reason
        # as the username: every Session the request opens binds to it.
        company_token = _served_company.set(_request_company(scope, session))
        try:
            await self.app(scope, receive, send)
        finally:
            _served_company.reset(company_token)
            _closing_date_password.reset(override_token)
            _acting_username.reset(token)


def _request_company(scope, session) -> str | None:
    """The company file a request works in: the one its session chose, or,
    for an API token's request (no sign-in), the one its
    X-SlowBooks-Company header names, where the token is then looked up.
    None is the company the server was started for."""
    from app.services import served_companies

    chosen = served_companies.chosen_file(session)
    if chosen is not None or session.get("authenticated") is True:
        return chosen
    wanted = _COMPANY_HEADER.lower().encode("latin-1")
    for name, value in scope.get("headers") or []:
        if name == wanted:
            return served_companies.chosen_file(
                {served_companies.SESSION_FILE_KEY: value.decode("latin-1").strip()}
            )
    return None


# ---- Middleware stack ----
# Registered here, as one ordered block, because every constraint below is
# about ORDER and Starlette resolves order by registration sequence: the
# LAST add_middleware call is the OUTERMOST layer (runs first on the way in,
# last on the way out). Registering innermost-first, the resulting stack is,
# outermost to innermost:
#
#   HTTPS redirect -> CORS -> security headers -> session cookie
#     -> acting-user context -> auth gate -> gzip -> routes
#
# Why this order:
#   - HTTPS redirect outermost: a plain-HTTP request is promoted before any
#     cookie is parsed or the app is touched. A no-op behind a TLS proxy.
#   - CORS outside the auth gate: a browser preflight (OPTIONS) is sent
#     WITHOUT credentials, so if it reached require_session it would be
#     answered 401 with no Access-Control-Allow-* headers — breaking every
#     cross-origin call after a successful login. It must be answered by
#     CORSMiddleware before auth. The allow-list is unchanged; credentials
#     still never combine with a wildcard origin.
#   - Security headers outside the auth gate: so the gate's own 401/403/503
#     refusals (unauthenticated, role refusal, CompanyUnavailable) still
#     carry CSP, X-Frame-Options, nosniff, Referrer-Policy and HSTS.
#     _set_if_unset means a route that already chose a stricter value (the
#     portal's Referrer-Policy: no-referrer) still wins, since routes run
#     inside this layer.
#   - SessionMiddleware outside the gate and ActingUserContextMiddleware:
#     both read the session (request.session / scope["session"]), which it
#     populates.
#   - ActingUserContextMiddleware outside the gate: it sets the acting-user
#     and served-company contextvars before require_session opens the
#     company and before any route runs.
#   - gzip innermost: it only needs to compress route payloads; the gate's
#     short refusals are tiny and need no compression.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
app.add_middleware(BaseHTTPMiddleware, dispatch=require_session)
app.add_middleware(ActingUserContextMiddleware)
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
app.add_middleware(BaseHTTPMiddleware, dispatch=security_headers)
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
# Outermost: promote plain HTTP to HTTPS before anything else sees the
# request. Behind a TLS-terminating proxy this is a no-op.
if FORCE_HTTPS:
    app.add_middleware(HTTPSRedirectMiddleware)

# Phase 9.7: Auth routes MUST be included (they're exempt from the session gate)
app.include_router(auth_routes.router)

# Original API routes
app.include_router(dashboard.router)
app.include_router(accounts.router)
app.include_router(classes_routes.router)
app.include_router(jobs_routes.router)
app.include_router(preferences_routes.router)
app.include_router(cost_codes_routes.router)
app.include_router(job_costing_routes.cost_types_router)
app.include_router(job_costing_routes.equipment_router)
app.include_router(job_costing_routes.job_costs_router)
app.include_router(nonprofit_routes.router)
app.include_router(construction_routes.router)
app.include_router(trade_packages_routes.router)
app.include_router(change_orders_routes.router)
app.include_router(pay_applications_routes.router)
app.include_router(lien_waivers_routes.router)
app.include_router(donors_routes.router)
app.include_router(in_kind_routes.router)
app.include_router(fx_routes.router)
app.include_router(fixed_assets_routes.router)
app.include_router(migration_routes.router)
app.include_router(opening_balances_routes.router)
# before customers.router so /past* is not swallowed by /{customer_id} (#436)
app.include_router(customers_past.router)
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
app.include_router(email_log_routes.router)
app.include_router(search.router)
# Phase 2: Accounts Payable
app.include_router(purchase_orders.router)
app.include_router(bills.router)
app.include_router(bill_payments.router)
app.include_router(credit_memos.router)
app.include_router(vendor_credits.router)
# Phase 3: Productivity
app.include_router(recurring.router)
app.include_router(memorized.router)
app.include_router(batch_payments.router)
# Phase 4: Communication & Export
app.include_router(csv_routes.router)
app.include_router(uploads.router)
# Phase 5: Advanced Integration
app.include_router(bank_import.router)
app.include_router(simplefin.router)
app.include_router(mercury.router)
app.include_router(users_routes.router)
app.include_router(api_tokens_routes.router)
app.include_router(tax.router)
app.include_router(backups.router)
app.include_router(company_file_routes.router)
app.include_router(accountants_copy_routes.router)
app.include_router(reclassify_routes.router)
app.include_router(system_routes.router)
app.include_router(support_routes.router)
# Phase 6: Ambitious
app.include_router(companies.router)
app.include_router(employees.router)
app.include_router(payroll.router)
app.include_router(payroll.opening_employee_router)  # opening YTD (#433)
# Phase 7: Online Payments
app.include_router(provider_payments.router)
app.include_router(provider_payments.legacy_stripe_router)
app.include_router(public.router)
# Phase 8: QuickBooks Online
# Phase 9: Analytics (real-time business intelligence)
app.include_router(analytics.router)
# Phase 9: Forum Bug Fixes & Missing Features
app.include_router(journal.router)
app.include_router(deposits.router)
app.include_router(transfers.router)
app.include_router(cc_charges.router)
app.include_router(expenses.router)
app.include_router(checks.router)
# Phase 10: Quick Wins + Medium Effort Features
app.include_router(bank_rules.router)
app.include_router(budgets.router)
app.include_router(attachments.router)
app.include_router(email_templates.router)
# the saved invoice messages, chosen on the invoice form (#392)
app.include_router(invoice_messages_routes.router)
app.include_router(payment_terms_routes.router)
# Phase 11: Saved Reports (inventory endpoints live on the items router)
app.include_router(saved_reports.router)

# Tier 1: Onboarding, time entries, PTO management
app.include_router(time_entries.router)
app.include_router(pto.router)

# Tier 2: Advanced deductions, garnishments, and tax forms UI
app.include_router(deductions.router)
app.include_router(benefits.router)
app.include_router(tax_forms.router)

# Tier 3: Employee onboarding workflows + self-service portal
app.include_router(onboarding.router)
app.include_router(portal.router)
app.include_router(document_audit_routes.router)
app.include_router(reseller_permits_routes.router)
app.include_router(ocr_routes.router)

# Register audit log hooks
register_audit_hooks(SessionLocal)

# Static files.
static_dir = Path(__file__).parent / "static"


# The uploads folder is not served. Before 2.18.0 every company's logo,
# attachments and employee documents were files there, one folder for every
# company, published at /static/uploads/ like the app's own scripts — and
# /static/ needs no sign-in, so a W-4 was one guessable URL away from anyone
# who could reach the server. Each company now keeps its files in its own
# database and serves them through signed-in routes; the folder is left on
# disk (the upgrade copies from it, and other companies may still need it)
# but nothing reads it over HTTP.
#
# The route below answers 404 for the literal /static/uploads/ prefix, but a
# route only matches that one spelling — StaticFiles resolves the request path
# itself, so /static//uploads/<file> and /static/css/%2e%2e/uploads/<file>
# slipped past the route and the mount served the file with 200. The real
# defence is the mount: resolve the request against the static directory
# and refuse it if it lands inside <static>/uploads, whatever spelling got it
# there. The route stays as belt-and-braces for the common path.
class _UploadsHiddenStaticFiles(StaticFiles):
    """A /static mount that never serves anything under the uploads folder."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The one folder that must stay unreachable, as its resolved real path
        # with the filesystem's case folding — normcase so a request for
        # UPLOADS matches uploads on macOS/Windows; realpath so symlinks and
        # the parent's real location are accounted for. Computed once; it is
        # the same folder for the life of the mount even if it does not exist
        # yet (realpath still normalises a missing path).
        self._uploads_prefix = os.path.normcase(
            os.path.realpath(os.path.join(str(self.directory), "uploads"))
        )

    def lookup_path(self, path):
        try:
            full_path, stat_result = super().lookup_path(path)
        except (OSError, ValueError):
            # A name the filesystem can't hold (on Windows ':' or one of
            # < > ? * " |) is not a file here: a 404, not a 500 (2.26.0 gate,
            # W-31). Starlette catches only FileNotFoundError and
            # NotADirectoryError.
            return "", None
        if full_path and self._inside_uploads(
            os.path.normcase(os.path.realpath(full_path))
        ):
            return "", None
        return full_path, stat_result

    def _inside_uploads(self, resolved: str) -> bool:
        """Whether a resolved real path is the uploads folder or inside it."""
        if resolved == self._uploads_prefix or resolved.startswith(
            self._uploads_prefix + os.sep
        ):
            return True
        # A case-insensitive filesystem (macOS, Windows) opens UPLOADS as
        # uploads, but normcase folds case on Windows only and realpath keeps
        # the spelling asked for, so the string test above misses it on a
        # Mac. Ask the filesystem instead: walk from the file up to the
        # static root and compare each folder's identity with the uploads
        # folder's. A few stats per asset, and none when the folder is gone.
        try:
            uploads_stat = os.stat(self._uploads_prefix)
        except OSError:
            return False
        root = os.path.normcase(os.path.realpath(str(self.directory)))
        here = resolved
        while here.startswith(root):
            try:
                if os.path.samestat(os.stat(here), uploads_stat):
                    return True
            except OSError:
                return False
            parent = os.path.dirname(here)
            if parent == here:
                return False
            here = parent
        return False


@app.api_route(
    "/static/uploads/{rest:path}", methods=["GET", "HEAD"], include_in_schema=False
)
async def _uploads_folder_is_not_served(rest: str):
    return JSONResponse(status_code=404, content={"detail": "Not Found"})


app.mount(
    "/static", _UploadsHiddenStaticFiles(directory=str(static_dir)), name="static"
)

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
        description=app.description,
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
