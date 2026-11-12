# ============================================================================
# Database layer — SQLAlchemy engine + session factory.
# PostgreSQL in server mode, per-company SQLite files in desktop mode.
# ============================================================================

import threading
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from starlette.requests import HTTPConnection

from app.config import DATABASE_URL

# Pool tuning rationale (Phase 9.6 perf pass):
#   pool_size=10        small base pool; most requests are short-lived
#   max_overflow=20     burst capacity when analytics + concurrent users hit
#   pool_recycle=1800   recycle every 30 min to avoid stale TCP idle kills
#   pool_pre_ping=True  cheap SELECT 1 before each checkout; catches dead conns
#   pool_use_lifo=True  reuse hottest conn first -> better CPU cache locality
# SQLite URLs skip pool_size/max_overflow since SQLite uses a different strategy.
_is_sqlite = DATABASE_URL.startswith("sqlite")
_engine_kwargs = dict(pool_pre_ping=True)
if not _is_sqlite:
    _engine_kwargs.update(
        pool_size=10,
        max_overflow=20,
        pool_recycle=1800,
        pool_use_lifo=True,
    )
if _is_sqlite:
    # A company file may be encrypted (SQLCipher, #290). The engine takes
    # the sqlcipher3 module in place of sqlite3 and keys each connection
    # before the PRAGMAs below — only when the file on disk is encrypted;
    # a plain file, and the suite's :memory: database, open as they always
    # did. The key is looked up once, and only if it is needed.
    from app.services import company_file

    engine = company_file.create_sqlite_engine(
        DATABASE_URL, company_file.resolve_key, **_engine_kwargs
    )
else:
    engine = create_engine(DATABASE_URL, **_engine_kwargs)


def enable_sqlite_tuning(target_engine) -> None:
    """Concurrency PRAGMAs for SQLite engines (Server Edition groundwork).

    WAL lets readers proceed while one writer commits — the difference
    between "works for an office" and "database is locked" the moment a
    second person opens a report mid-save. busy_timeout makes brief lock
    contention wait instead of erroring; NORMAL sync is the recommended
    pairing with WAL. Harmless no-ops on :memory: databases.

    secure_delete: a company keeps its files in its own database (W-4s and
    I-9s among them), and SQLite otherwise leaves a deleted row's bytes in
    the file's free pages, where a later copy or backup of the file still
    carries them. With it on, a deleted document is overwritten, not just
    unlinked from the table.
    """
    from sqlalchemy import event

    @event.listens_for(target_engine, "connect")
    def _tune(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA secure_delete=ON")
        cur.close()


if _is_sqlite:
    enable_sqlite_tuning(engine)


# ---------------------------------------------------------------------------
# Row locks on SQLite (#666). The routes lock a row for a read-check-write
# with with_for_update() so two payments on one invoice cannot both pass
# the balance check; SQLite has no row locks and SQLAlchemy's pysqlite
# driver emits no BEGIN until the first write, so on SQLite every such
# request read the same balance and all of them passed (four $98 payments
# on a $100 invoice, all 201). The hook below runs before every ORM
# statement: when it carries FOR UPDATE and the session is on SQLite, it
# begins the transaction IMMEDIATE on the DBAPI connection, which takes
# the database's write lock. The second request waits at that point (the
# connect PRAGMA's busy_timeout) until the first commits, then reads the
# balance the first one left and is refused the way PostgreSQL refuses it.
# Reads with no lock stay as they were: WAL readers never queue behind a
# writer. A transaction already begun (a write earlier in the request) is
# left alone; pysqlite then issues no second BEGIN.
# ---------------------------------------------------------------------------


def _is_sqlite_bind(bind) -> bool:
    return getattr(getattr(bind, "dialect", None), "name", "") == "sqlite"


def begin_write(session) -> None:
    """Take SQLite's write lock now (BEGIN IMMEDIATE) if this session is on
    SQLite and has not begun a transaction on its connection yet; a no-op
    on any other database."""
    bind = session.get_bind()
    if not _is_sqlite_bind(bind):
        return
    raw = session.connection().connection.dbapi_connection
    if getattr(raw, "in_transaction", False):
        return
    raw.execute("BEGIN IMMEDIATE")


def _lock_rows_on_sqlite(orm_execute_state) -> None:
    stmt = orm_execute_state.statement
    if getattr(stmt, "_for_update_arg", None) is None:
        return
    begin_write(orm_execute_state.session)


from sqlalchemy import event as _event  # noqa: E402

_event.listen(Session, "do_orm_execute", _lock_rows_on_sqlite)


# ---------------------------------------------------------------------------
# Several companies on one server (#358). Server Edition used to serve the
# one company file DATABASE_URL names, and reaching a second meant a second
# server or repointing this one for everybody. Now a signed-in person's
# session names the company file they chose, the request runs with that
# name in the contextvar below, and every Session opened during it binds to
# that company's own engine — get_db, the audit hooks and the code that
# calls SessionLocal() directly all follow without knowing. None is the
# company the server was started for, which is every request on a desktop.
# Which files may be named, and when, is app/services/served_companies.py.
# ---------------------------------------------------------------------------

_served_company: ContextVar[str | None] = ContextVar("served_company", default=None)
_company_engines: dict = {}
_company_engines_lock = threading.Lock()


def served_company() -> str | None:
    """The company file this request works in, by name; None for the one
    the server was started for."""
    return _served_company.get()


@contextmanager
def use_company(filename: str | None):
    """Run the block in another company: sessions opened inside it bind to
    that company's engine."""
    token = _served_company.set(filename)
    try:
        yield
    finally:
        _served_company.reset(token)


def started_company_file() -> str | None:
    """The basename of the file the server was started for, when it is one."""
    from app.services import company_file

    path = company_file.sqlite_path(DATABASE_URL)
    return path.name if path is not None else None


def company_engine(filename: str | None):
    """The engine for a company file, opened the first time it is asked
    for. The company the server was started for keeps the module's own
    engine (looked up at call time: the test harness repoints it)."""
    if filename is None or filename == started_company_file():
        return engine
    found = _company_engines.get(filename)
    if found is not None:
        return found
    with _company_engines_lock:
        found = _company_engines.get(filename)
        if found is None:
            from app.services import served_companies

            found = served_companies.open_engine(filename)
            _company_engines[filename] = found
        return found


def current_engine():
    """The engine of the company this request works in."""
    return company_engine(_served_company.get())


def another_company_url() -> str | None:
    """The database URL of the company this request works in when it is not
    the one the server was started for; None when it is. Code that reads
    DATABASE_URL for the company in hand keeps reading it for that one."""
    filename = _served_company.get()
    if filename is None or filename == started_company_file():
        return None
    return current_engine().url.render_as_string(hide_password=False)


def current_url() -> str:
    """The database URL of the company this request works in."""
    return another_company_url() or DATABASE_URL


def forget_company_engines() -> None:
    """Close and drop every other company's engine; the next request to one
    opens it again (tests start each case clean)."""
    with _company_engines_lock:
        for found in _company_engines.values():
            found.dispose()
        _company_engines.clear()


def drop_company_engines(filenames) -> None:
    """Close and drop the engines of companies no longer on the served list:
    a request naming one again is refused by the list, or opens it afresh if
    it comes back."""
    with _company_engines_lock:
        for name in list(filenames):
            found = _company_engines.pop(name, None)
            if found is not None:
                found.dispose()


class CompanySession(Session):
    """A Session bound to the company the request works in."""

    def __init__(self, bind=None, **kwargs):
        if _served_company.get() is not None:
            bind = current_engine()
        super().__init__(bind=bind, **kwargs)


SessionLocal = sessionmaker(
    class_=CompanySession, autocommit=False, autoflush=False, bind=engine
)
Base = declarative_base()


def get_db(request: HTTPConnection = None):
    """Request-scoped DB session (FastAPI dependency).

    When FastAPI injects the request, the acting username is stamped onto
    the Session's own info dict — the audit hooks receive this exact
    object at flush time, so attribution travels WITH the session instead
    of relying on contextvar propagation (which proved unreliable on the
    frozen Windows runtime; the contextvar remains as a fallback for
    sessions created outside the request cycle)."""
    db = SessionLocal()
    try:
        if request is not None:
            # an import's writes say which import made them (#894)
            from app.services.request_context import import_source

            source = import_source(
                request.scope.get("method") or "", request.scope.get("path") or ""
            )
            if source:
                db.info["audit_source"] = source
    except Exception:
        pass  # attribution must never break a request
    try:
        session = getattr(request, "session", None) if request is not None else None
        if isinstance(session, dict) and session.get("authenticated") is True:
            db.info["acting_username"] = session.get("username") or "operator"
            # The closing-date override password, when the page resent a
            # refused change with it. A signed-in person's request only —
            # the token branch below never carries it.
            from app.services.closing_date import (
                PASSWORD_HEADER,
                SESSION_INFO_KEY,
                password_from_header,
            )

            supplied = password_from_header(request.headers.get(PASSWORD_HEADER))
            if supplied:
                db.info[SESSION_INFO_KEY] = supplied
        elif request is not None:
            # Scoped API tokens: the middleware stashes the principal on
            # request.state — audit rows attribute to "token:<label>".
            tp = getattr(getattr(request, "state", None), "token_principal", None)
            if isinstance(tp, dict) and tp.get("username"):
                db.info["acting_username"] = tp["username"]
    except Exception:
        pass  # attribution must never break a request
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
