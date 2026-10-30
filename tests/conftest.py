# ============================================================================
# Slowbooks Pro 2026 — pytest configuration
#
# One in-memory SQLite database for the whole run, built once; each test
# runs inside a transaction on it that is rolled back at the end (issue
# #128). The `client` fixture wires the app's get_db dependency to that same
# connection so API calls and direct db_session queries hit the same tables.
# Rate limiting is disabled by default so per-test counters don't collide.
# ============================================================================

import atexit
import os
import shutil
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet

# ---- The machine's own SlowBooks files stay out of the suite ----
# Without SLOWBOOKS_DATA_DIR, company_service.data_dir() is the real per-user
# folder (~/Library/Application Support/SlowBooksPro/data on a Mac), so the
# suite saw the tester's own companies: a test that renames its company to
# "Harbor Light Bakery" got a 409 from a real company of that name (2.18.0
# gate, macbase1). And storage.files_root() is app/static, so attachments,
# receipt intake and backups were written into the source tree, where a
# local build could pick them up. One folder per run, set before anything
# imports the app (the upload roots are read at import) and set always: a
# SLOWBOOKS_DATA_DIR in the shell would point the suite at real books.
SUITE_DATA_DIR = Path(tempfile.mkdtemp(prefix="slowbooks-suite-")).resolve()
atexit.register(shutil.rmtree, SUITE_DATA_DIR, True)
os.environ["SLOWBOOKS_DATA_DIR"] = str(SUITE_DATA_DIR)
# The checkout's own .env (a developer's DATABASE_URL, company name and
# keys) is not read either; CI has none, so a run here is the run CI does.
os.environ["SLOWBOOKS_ENV_FILE"] = str(SUITE_DATA_DIR / "suite.env")
# Settings encryption with a key for this run only: without one, crypto
# writes .slowbooks-master.key next to the code.
os.environ["SETTINGS_ENCRYPTION_KEY"] = Fernet.generate_key().decode("ascii")

# ---- Environment overrides (must run BEFORE any app imports) ----
os.environ["APP_DEBUG"] = "true"  # Disable production security checks in test
os.environ["SESSION_SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["ALLOWED_ORIGINS"] = "http://testserver,http://localhost:3001"
os.environ["CORS_ALLOW_ORIGINS"] = "http://testserver,http://localhost:3001"
os.environ["RATE_LIMIT_ENABLED"] = "0"
os.environ["SESSION_IDLE_TIMEOUT_SECONDS"] = "0"  # Disable idle expiry in tests
# The suite is written against tesseract; on a Mac/Windows dev box with the
# native OCR bridge installed, auto-selection would pick Vision/WinRT and
# bypass the ocr_service monkeypatches. Selection tests override per-test.
os.environ.setdefault("SLOWBOOKS_OCR_ENGINE", "tesseract")
# Point the app at an in-memory DB by default; fixtures override per-test.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

# ---- No fsync on the suite's SQLite files ----
# The Windows CI job spent most of its time waiting on fsync: every test that
# walks the migration chain against a real SQLite file commits once per
# migration, 15 to 80 s a test there against 1 to 3 s on Linux, and the
# slowest of them outgrew the job's per-test timeout. synchronous=OFF on every
# SQLite connection this test process opens (the migration runner's engine and
# the company files' alike, through a class-level listener) writes the same
# files without waiting for the disk. Test setup only: nothing in app/ reads
# it. SLOWBOOKS_SQLITE_FSYNC=1 in the shell keeps fsync on, for a timing.
import sqlite3 as _sqlite3  # noqa: E402

from sqlalchemy import event as _sa_event  # noqa: E402
from sqlalchemy.engine import Engine as _Engine  # noqa: E402

if os.environ.get("SLOWBOOKS_SQLITE_FSYNC") != "1":

    @_sa_event.listens_for(_Engine, "connect")
    def _suite_sqlite_without_fsync(dbapi_conn, _record):
        module = type(dbapi_conn).__module__ or ""
        if (
            not isinstance(dbapi_conn, _sqlite3.Connection)
            and "sqlcipher" not in module
        ):
            return
        try:
            dbapi_conn.execute("PRAGMA synchronous=OFF")
        except Exception:
            # an encrypted file before its key (company_file keys it next)
            pass


# The in-app scheduler (#605) is a background task the lifespan starts; it
# would run against a lifespan test's single shared connection from its own
# thread. Off for the suite; the tests of the task call it directly.
os.environ["SLOWBOOKS_AUTO_ENTER"] = "0"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---- Each migration walk runs once per test run ----
# 33 test files, and every test that makes a company file, run the migration
# chain (86 migrations) from an empty SQLite file; most of them to the same
# few targets (head, or the revision before the one a test checks). The file
# such a walk leaves is the same every time, so the first walk to a target is
# run for real and kept, and every later one gets a copy of it. Only a walk
# from an empty (or missing) plain SQLite file is kept; a walk on a file that
# already holds anything, an encrypted file, offline SQL, PostgreSQL and a
# relative target all run for real, as do downgrades. Test setup only: the
# migrations themselves, and app code that calls command.upgrade, are
# unchanged. SLOWBOOKS_MIGRATION_CACHE=0 in the shell runs every walk.
import sqlite3 as _cache_sqlite3  # noqa: E402

import alembic.command as _alembic_command  # noqa: E402

_real_upgrade = _alembic_command.upgrade
_MIGRATION_CACHE: dict = {}
_MIGRATION_CACHE_DIR = SUITE_DATA_DIR / "migrated"


def _sqlite_file(url: str):
    if not url.startswith("sqlite:///") or ":memory:" in url:
        return None
    raw = url[len("sqlite:///") :].split("?", 1)[0]
    return Path(raw) if raw else None


def _cached_upgrade(config, revision, sql=False, tag=None, **kw):
    url = (
        config.attributes.get("database_url")
        or config.get_main_option("sqlalchemy.url")
        or os.environ.get("DATABASE_URL", "")
    )
    path = _sqlite_file(url)
    if (
        sql
        or tag
        or kw
        or path is None
        or (path.exists() and path.stat().st_size > 0)
        or config.attributes.get("company_file_key")
        or os.environ.get("SLOWBOOKS_COMPANY_FILE_KEY")
    ):
        return _real_upgrade(config, revision, sql=sql, tag=tag, **kw)
    from alembic.script import ScriptDirectory

    try:
        script = ScriptDirectory.from_config(config)
        targets = script.get_revisions(revision)
    except Exception:
        return _real_upgrade(config, revision, sql=sql, tag=tag)
    if len(targets) != 1 or targets[0] is None:
        return _real_upgrade(config, revision, sql=sql, tag=tag)
    key = (str(Path(script.dir).resolve()), targets[0].revision)
    kept = _MIGRATION_CACHE.get(key)
    if kept is not None and kept.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        src = _cache_sqlite3.connect(kept)
        dst = _cache_sqlite3.connect(path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return None
    result = _real_upgrade(config, revision, sql=sql, tag=tag)
    if path.exists():
        _MIGRATION_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        kept = (
            _MIGRATION_CACHE_DIR / f"{len(_MIGRATION_CACHE)}-{targets[0].revision}.db"
        )
        src = _cache_sqlite3.connect(path)
        dst = _cache_sqlite3.connect(kept)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        _MIGRATION_CACHE[key] = kept
    return result


if os.environ.get("SLOWBOOKS_MIGRATION_CACHE") != "0":
    _alembic_command.upgrade = _cached_upgrade


import pytest  # noqa: E402

# ---------------------------------------------------------------------------
# WeasyPrint's native stack (issue #121)
#
# Rendering a PDF needs pango/cairo/gobject. Importing the app no longer does
# (app/services/pdf_service.py imports WeasyPrint lazily), so the suite runs
# on a machine without them — which matters because CI runs pytest on Linux
# only, and the Windows box that would have caught @wilsons043's encoding bug
# could not import the app at all.
#
# A test that actually renders still cannot pass without the stack. Rather
# than guess which tests those are with a marker anyone can forget, we let
# the test run and turn the library's own ImportError into a SKIP — so a test
# skips exactly when it needed the missing library, and never otherwise.
# ---------------------------------------------------------------------------

try:  # noqa: SIM105
    import weasyprint as _weasyprint  # noqa: F401

    WEASYPRINT_AVAILABLE = True
except Exception:
    WEASYPRINT_AVAILABLE = False


def _is_missing_native_stack(exc: BaseException) -> bool:
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (ImportError, OSError)):
            text = str(exc).lower()
            if (
                "weasyprint" in text
                or "gobject" in text
                or "pango" in text
                or "cairo" in text
            ):
                return True
        exc = exc.__cause__ or exc.__context__
    return False


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    outcome = yield
    if WEASYPRINT_AVAILABLE:
        return
    exc = outcome.excinfo[1] if getattr(outcome, "excinfo", None) else None
    if exc is not None and _is_missing_native_stack(exc):
        outcome.force_exception(
            pytest.skip.Exception(
                "needs WeasyPrint's native stack (pango/cairo/gobject), which "
                "is not installed on this machine — the rest of the suite runs"
            )
        )


from starlette.requests import HTTPConnection  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import close_all_sessions, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

# Import all model modules so Base.metadata sees every table before create_all.
# Without these imports, tables defined in unimported modules wouldn't be created.
from app.models import (  # noqa: F401,E402
    accounts,
    attachments,
    request_keys,
    audit,
    auth as auth_model,
    backups,
    banking,
    bank_accounts,
    bank_rules,
    bills,
    budgets,
    companies,
    contacts,
    credit_memos,
    vendor_credits,
    deductions,
    document_audit as document_audit_model,
    email_log,
    email_templates,
    portal_access as portal_access_model,
    reseller_permit as reseller_permit_model,
    estimates,
    hr,
    invoices,
    in_kind as in_kind_model,
    items,
    job_deposits,
    lien_waivers,
    pay_applications,
    nonprofit as nonprofit_model,
    payments,
    payroll,
    payroll_opening,
    pto,
    purchase_orders,
    qbo_mapping,
    recurring,
    settings as settings_model,
    tax,
    time_entries,
    transactions,
)
import app.database as db_module  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS  # noqa: E402

from app.main import app  # noqa: E402

# ---------------------------------------------------------------------------
# ONE session factory for the whole suite.
#
# Issue #124: the suite used to build a fresh sessionmaker per test — two of
# them, in fact — and call register_audit_hooks() on each. That is ~4,060
# event registrations over a run, and SQLAlchemy keeps per-target listener
# bookkeeping for every one. Measured growth was ~1.6 MB retained per test,
# reaching 1.2 GB by the end and never plateauing, which is what terminated
# the suite at a random point on a memory-constrained Windows box.
#
# A sessionmaker can be re-pointed with .configure(bind=...), so one factory
# serves every test and the listener is attached exactly once.
#
# The old comment here warned that id-reuse across short-lived factories made
# `event.contains` unreliable. With one long-lived factory that hazard is gone
# by construction rather than worked around.
# ---------------------------------------------------------------------------
_SUITE_SESSION_FACTORY = sessionmaker(autocommit=False, autoflush=False)


def _shared_factory(bind):
    from app.services.audit import register_audit_hooks

    # create_savepoint: every Session on this connection — the test's, the
    # client's, one the app opens itself — runs in its own SAVEPOINT, so
    # session.commit() releases the savepoint and never touches the outer
    # transaction that the db_engine fixture rolls back.
    _SUITE_SESSION_FACTORY.configure(
        bind=bind, join_transaction_mode="create_savepoint"
    )
    register_audit_hooks(_SUITE_SESSION_FACTORY)  # idempotent via its sentinel
    return _SUITE_SESSION_FACTORY


def _test_engine(url="sqlite:///:memory:"):
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # pysqlite's legacy transaction handling never emits BEGIN for a
    # SAVEPOINT, so a savepoint released before any BEGIN "functions on its
    # own" and a rollback of the enclosing transaction leaves it in place —
    # the exact failure that would make a test pass while leaking rows into
    # the next one. SQLAlchemy's documented fix: take BEGIN away from the
    # driver and emit it ourselves.
    @event.listens_for(engine, "connect")
    def _no_implicit_begin(dbapi_connection, _record):
        dbapi_connection.isolation_level = None
        # A file-backed database at memory speed: nothing here needs to
        # survive a crash, and the file exists only so the database does
        # (see _suite_engine).
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=MEMORY")
        cur.execute("PRAGMA synchronous=OFF")
        cur.execute("PRAGMA temp_store=MEMORY")
        cur.close()

    @event.listens_for(engine, "begin")
    def _explicit_begin(conn):
        conn.exec_driver_sql("BEGIN")

    Base.metadata.create_all(bind=engine)
    return engine


# ---------------------------------------------------------------------------
# Issue #128: one engine and one schema for the whole run.
#
# Every test used to build its own engine and create_all() into it. Live
# objects grew all run long (795k -> 1.36M, mostly DDL event dispatch and
# column types that never freed), and the cost was linear in the size of the
# suite. Now the schema exists once; a test gets a connection with an open
# transaction, everything it does nests inside that as savepoints, and the
# transaction is rolled back at the end. Isolation is by rollback, not by
# rebuilding the world — and rowids restart with it, so `id == 1` still holds.
#
# The proof that no test sees another's data is a shuffled-order run, not
# this comment: see the 2.13.0 gate record.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _suite_engine(tmp_path_factory):
    # A file rather than :memory:, for one reason: restoring a backup calls
    # db_module.engine.dispose() so the app re-reads the overwritten file.
    # On a :memory: database, closing the connection IS deleting the
    # database — one restore test and every test after it would find an
    # empty schema. On a file, dispose just reconnects.
    path = tmp_path_factory.mktemp("suite") / "suite.db"
    engine = _test_engine("sqlite:///" + path.as_posix())
    yield engine
    engine.dispose()


# Tables that are empty at the start of every test. A test that commits
# outside its transaction (after a dispose, say) shows up here at the NEXT
# test's setup, named, rather than as a mystery failure three files later.
_SENTINEL_TABLES = ("accounts", "customers", "vendors", "transactions", "users")
_previous_test = {"nodeid": "(none)"}


def _assert_pristine(connection, nodeid):
    from sqlalchemy import text

    for table in _SENTINEL_TABLES:
        n = connection.execute(text(f"SELECT count(*) FROM {table}")).scalar()
        if n:
            pytest.fail(
                f"{table} has {n} row(s) at the start of {nodeid}: a previous "
                f"test ({_previous_test['nodeid']}) committed outside its "
                "transaction, so its rows leaked into this one"
            )


@pytest.fixture
def db_engine(_suite_engine, request):
    """The suite's engine, with this test's transaction open on it. Rolled
    back — every row the test wrote, whether it committed or not — at exit."""
    connection = _suite_engine.connect()
    outer = connection.begin()
    _assert_pristine(connection, request.node.nodeid)
    # Point the app module at this engine so SessionLocal-based code (the
    # startup helpers, api_token_service, encryption) also lands in the
    # same transaction.
    db_module.engine = _suite_engine
    db_module.SessionLocal = _shared_factory(connection)
    try:
        yield _suite_engine
    finally:
        _previous_test["nodeid"] = request.node.nodeid
        close_all_sessions()
        # A test that disposed the engine already lost this connection;
        # what it wrote after that is the sentinel's job to catch.
        try:
            outer.rollback()
        except Exception:
            pass
        try:
            connection.close()
        except Exception:
            pass


@pytest.fixture(scope="session")
def suite_data_dir():
    """The run's own data folder (see SUITE_DATA_DIR at the top): what
    data_dir() and every upload root resolve to during the suite."""
    return SUITE_DATA_DIR


@pytest.fixture(autouse=True)
def _forget_closing_date_override_attempts():
    """Wrong closing-date passwords are counted per company file in the
    process; every test's in-memory file has the same URL, so start each test
    with no count and no lock."""
    import app.services.closing_date as closing_date

    reset = getattr(closing_date, "reset_override_attempts", None)
    if reset is not None:
        reset()
    yield


@pytest.fixture(autouse=True)
def _release_closed_event_loops():
    """anyio 4.12 keeps a registry of per-run variables keyed weakly by
    event loop — and one of the values is the run's root Task, which holds
    the loop strongly. A value that references its own weak key can never
    be collected, so every event loop the test client spins up (one per
    request, without a context manager) stayed alive after it was closed:
    loop, task, thread limiter, worker sets, contexts. Measured at ~2.3
    loops per test and about a third of the suite's remaining growth.
    Drop the entries for closed loops after each test. Private API, so a
    version that changes it simply leaves the leak in place."""
    yield
    try:
        from anyio.lowlevel import _run_vars
    except Exception:
        return
    for loop in [
        k for k in list(_run_vars) if getattr(k, "is_closed", lambda: False)()
    ]:
        _run_vars.pop(loop, None)


@pytest.fixture
def TestSession(db_engine):
    """The suite's session factory, bound to this test's transaction. The
    `client` fixture wires get_db to this."""
    return _SUITE_SESSION_FACTORY


@pytest.fixture
def db_session(TestSession):
    """Isolated session backed by the per-test in-memory engine."""
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def legacy_admin_token(db_session):
    """make(label) → the secret of an admin-role API token, written to the
    database the way one made before 2.29.0 is. None are made now (#434),
    but those that exist keep working until they are revoked, so the rules
    an admin token still meets are tested on one of these."""
    from app.models.api_tokens import ApiToken
    from app.services.api_token_service import generate_token, hash_token

    def make(label="legacy-admin"):
        secret = generate_token()
        db_session.add(
            ApiToken(
                label=label,
                token_hash=hash_token(secret),
                token_hint=secret[:10],
                role="admin",
                is_active=True,
                created_by="test",
            )
        )
        db_session.commit()
        return secret

    return make


@pytest.fixture
def seed_accounts(db_session):
    """Seed the standard chart of accounts; returns dict keyed by account_number."""
    from app.models.accounts import Account, AccountType

    accounts_by_number = {}
    for data in CHART_OF_ACCOUNTS:
        acct = Account(
            account_number=data["account_number"],
            name=data["name"],
            account_type=AccountType(data["account_type"]),
            bank_kind=data.get("bank_kind"),
            detail_type=data.get("detail_type"),
            is_system=True,
            balance=Decimal("0"),
        )
        db_session.add(acct)
        accounts_by_number[data["account_number"]] = acct
    db_session.commit()
    return accounts_by_number


@pytest.fixture
def seed_customer(db_session):
    """Seed a single active Customer and return it."""
    from app.models.contacts import Customer

    customer = Customer(name="Test Customer", is_active=True)
    db_session.add(customer)
    db_session.commit()
    return customer


# ---------------------------------------------------------------------------
# Client fixtures — both wire get_db to the per-test in-memory engine
# ---------------------------------------------------------------------------


def _wire_app(TestSession):
    """Override app's get_db dependency to use the per-test session factory."""

    def override_get_db(request: HTTPConnection = None):
        session = TestSession()
        # Mirror production's attribution stamping so tests exercise the
        # session.info path (the mechanism that works on frozen Windows),
        # not just the contextvar fallback.
        http_session = getattr(request, "session", None) if request else None
        if isinstance(http_session, dict) and http_session.get("authenticated") is True:
            session.info["acting_username"] = http_session.get("username") or "operator"
        elif request is not None:
            tp = getattr(getattr(request, "state", None), "token_principal", None)
            if isinstance(tp, dict) and tp.get("username"):
                session.info["acting_username"] = tp["username"]
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db


@pytest.fixture
def unauthed_client(db_engine, TestSession):
    """Unauthenticated TestClient backed by the per-test in-memory DB.

    Use this fixture in tests that exercise the auth flow itself (setup, login,
    logout) where you need to start from an unauthenticated state.
    """
    _wire_app(TestSession)
    c = TestClient(app)  # no lifespan — see the note on `client` below
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client(db_engine, TestSession):
    """Authenticated TestClient backed by the per-test in-memory DB.

    Auth setup is performed during fixture setup so every API call
    made through this client is already authenticated.
    """
    _wire_app(TestSession)
    # No `with`, so the app's lifespan does NOT run (issue #124). Startup does
    # security checks, a manifest warning, the control-account check and the
    # at-rest secret upgrader — none of which a route reads, and all of which
    # the three tests that care call directly rather than through a client.
    # Running it 2,030 times cost roughly 56 KB of retained memory per test
    # and bought nothing. `lifespan_client` below is there for anything that
    # genuinely needs startup.
    c = TestClient(app)
    try:
        r = c.post("/api/auth/setup", json={"password": "test-password-123"})
        assert r.status_code == 200, f"Auth setup failed: {r.text}"
        yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def lifespan_client():
    """An authenticated client with the app's startup events actually run.

    Use this only for a test that asserts on startup behaviour; `client` skips
    the lifespan on purpose (issue #124). Startup opens engine-level
    transactions (the migration guard, create_all), which cannot nest inside
    a test's open transaction — so this one gets a private engine of its own,
    the way every test used to."""
    engine = _test_engine()
    db_module.engine = engine
    db_module.SessionLocal = _shared_factory(engine)
    _wire_app(_SUITE_SESSION_FACTORY)
    try:
        with TestClient(app) as c:
            r = c.post("/api/auth/setup", json={"password": "test-password-123"})
            assert r.status_code == 200, f"Auth setup failed: {r.text}"
            yield c
    finally:
        app.dependency_overrides.clear()
        close_all_sessions()
        engine.dispose()


@pytest.fixture
def authed_client(client):
    """Alias for client (already authenticated). Kept for backwards compatibility."""
    return client


@pytest.fixture
def db():
    """Legacy fixture: fresh session against the module-level engine.

    Tests that import this fixture directly (not via client) get a session
    backed by whatever engine db_module.SessionLocal points at.
    """
    session = db_module.SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(name="books")
def books_fixture(client, seed_accounts):
    """The bakery's books (tests/bakery_books.py), entered through the API:
    the company the browser modules and the report-link tests run on. Here
    rather than in tests/test_theme_contrast.py, which skips as a module
    without Playwright and took every importer of the fixture with it
    (2.22.0 Windows gate, W-2)."""
    from tests.bakery_books import seed_books

    return seed_books(client, seed_accounts)


@pytest.fixture
def api(client, db_session):
    """An ApiClient that reaches the app with a bookkeeper token."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.mcp.client import ApiClient
    from app.models.api_tokens import ApiToken
    from app.services.api_token_service import generate_token, hash_token

    client.put("/api/settings", json={"company_name": "Acme Fence Test Co"})
    secret = generate_token()
    db_session.add(
        ApiToken(
            label="mcp-test",
            token_hash=hash_token(secret),
            token_hint=secret[:10],
            role="bookkeeper",
            is_active=True,
            created_by="test",
        )
    )
    db_session.commit()
    http = TestClient(app)
    http.headers["Authorization"] = f"Bearer {secret}"

    def transport(method, path, params, body, headers):
        r = http.request(method, path, params=params, json=body, headers=headers)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text

    return ApiClient(transport)
