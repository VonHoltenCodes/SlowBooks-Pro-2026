# ============================================================================
# Database layer — SQLAlchemy engine + session factory.
# PostgreSQL in server mode, per-company SQLite files in desktop mode.
# ============================================================================

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
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
engine = create_engine(DATABASE_URL, **_engine_kwargs)


def enable_sqlite_tuning(target_engine) -> None:
    """Concurrency PRAGMAs for SQLite engines (Server Edition groundwork).

    WAL lets readers proceed while one writer commits — the difference
    between "works for an office" and "database is locked" the moment a
    second person opens a report mid-save. busy_timeout makes brief lock
    contention wait instead of erroring; NORMAL sync is the recommended
    pairing with WAL. Harmless no-ops on :memory: databases.
    """
    from sqlalchemy import event

    @event.listens_for(target_engine, "connect")
    def _tune(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


if _is_sqlite:
    enable_sqlite_tuning(engine)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
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
