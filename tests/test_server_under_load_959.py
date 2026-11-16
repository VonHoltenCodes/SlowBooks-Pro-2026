"""The server under load (#959, 2.32.0 round 10: W-167 / NEW-242).

Twenty-four requests in flight at once froze the server for minutes: the
sign-in middleware re-read the user on the event loop's thread, waiting up
to 30 s for a pooled connection while the handlers holding the pool's
fifteen connections could not hand them back. The lookup runs in the thread
pool now, and a file-backed engine's pool is sized to that thread pool."""

import re
from pathlib import Path

from app.services.company_file import create_sqlite_engine

ROOT = Path(__file__).resolve().parents[1]


def test_the_sign_in_check_runs_off_the_event_loop():
    src = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "from starlette.concurrency import run_in_threadpool" in src
    body = src.split("async def _require_session(")[1]
    assert re.search(
        r"await run_in_threadpool\(\s*_check_session_user,\s*request\.session\s*\)",
        body,
    )
    # and never called on the loop's thread any more
    assert not re.search(
        r"(?<!await run_in_threadpool\(\n            )_check_session_user\(request\.session\)",
        body,
    )


def _engine_kwargs_for(url: str, monkeypatch, pool_env=None):
    import importlib

    import app.database as database

    monkeypatch.setenv("DATABASE_URL", url)
    if pool_env is None:
        monkeypatch.delenv("SLOWBOOKS_DB_POOL_SIZE", raising=False)
    else:
        monkeypatch.setenv("SLOWBOOKS_DB_POOL_SIZE", pool_env)
    import app.config as config

    importlib.reload(config)
    try:
        importlib.reload(database)
        return dict(database._engine_kwargs), database.engine
    finally:
        monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
        monkeypatch.delenv("SLOWBOOKS_DB_POOL_SIZE", raising=False)
        importlib.reload(config)
        importlib.reload(database)


def test_a_company_file_gets_a_pool_the_size_of_the_thread_pool(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'books.db'}"
    kwargs, engine = _engine_kwargs_for(url, monkeypatch)
    assert kwargs["pool_size"] == 40 and kwargs["max_overflow"] == 10
    assert engine.pool.size() == 40
    kwargs, engine = _engine_kwargs_for(url, monkeypatch, pool_env="80")
    assert kwargs["pool_size"] == 80 and engine.pool.size() == 80
    kwargs, _ = _engine_kwargs_for(url, monkeypatch, pool_env="not a number")
    assert kwargs["pool_size"] == 40
    kwargs, _ = _engine_kwargs_for(url, monkeypatch, pool_env="1")
    assert kwargs["pool_size"] == 5


def test_the_in_memory_database_takes_no_pool_size(monkeypatch):
    kwargs, _ = _engine_kwargs_for("sqlite:///:memory:", monkeypatch)
    assert "pool_size" not in kwargs and "max_overflow" not in kwargs


def test_forty_connections_open_on_one_file_at_once(tmp_path):
    engine = create_sqlite_engine(
        f"sqlite:///{tmp_path / 'many.db'}", None, pool_size=40, max_overflow=10
    )
    conns = [engine.connect() for _ in range(40)]
    try:
        assert all(c.exec_driver_sql("select 1").scalar() == 1 for c in conns)
        assert engine.pool.checkedout() == 40
    finally:
        for c in conns:
            c.close()
        engine.dispose()
