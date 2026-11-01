import os
from logging.config import fileConfig

# INSTALL.md has the user write DATABASE_URL into .env and THEN run
# `alembic upgrade head`. Alembic is its own entrypoint and never boots
# app.config, so without this the .env is ignored, sqlalchemy.url falls back
# to the placeholder in alembic.ini, and the user gets
# `FATAL: password authentication failed for user "user"` — a user they
# never created, with no hint that the .env they just edited was unread.
# override=False so a real exported DATABASE_URL (Docker) still wins.
# The desktop launcher (and a restore, the test suite, the QA harness) names
# the .env the app reads in SLOWBOOKS_ENV_FILE: read that one, as
# app/config.py does, so a migration sees the secrets the server sees. The
# checkout's own .env named a different PAYROLL_ENCRYPTION_SECRET, and a
# migration that encrypts (e2b7c4d9a1f3) used a key the app never does.
try:
    from dotenv import load_dotenv

    if os.getenv("SLOWBOOKS_ENV_FILE"):
        load_dotenv(os.environ["SLOWBOOKS_ENV_FILE"], override=False)
    else:
        load_dotenv(override=False)
except ImportError:  # pragma: no cover - dotenv is in requirements.txt
    pass

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Programmatic callers (company_service creating a NEW company database)
# pass an explicit URL via config.attributes — that wins, so a per-company
# migration never gets clobbered by the process-wide DATABASE_URL.
# Otherwise, override sqlalchemy.url from DATABASE_URL env var (Docker).
_url_override = config.attributes.get("database_url")
if _url_override:
    config.set_main_option("sqlalchemy.url", _url_override)
elif os.getenv("DATABASE_URL"):
    config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])

# Interpret the config file for Python logging.
# disable_existing_loggers=False: alembic also runs in-process (desktop
# company creation, the frozen launcher) — the default True silently
# kills every already-created app logger, eating the very tracebacks
# that explain a failed migration.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

from app.database import Base
from app.models import *  # noqa: F401,F403 — import all models for autogenerate

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    url = config.get_main_option("sqlalchemy.url") or ""
    if url.startswith("sqlite"):
        # An encrypted company file (#290): the key has to be in hand before
        # a migration opens the file. The launcher hands it over in
        # config.attributes (in-process) or SLOWBOOKS_COMPANY_FILE_KEY (a
        # child process); otherwise it derives from the operator's secret.
        # A plain file is opened as before.
        from app.services import company_file

        connectable = company_file.create_sqlite_engine(
            url,
            config.attributes.get("company_file_key") or company_file.resolve_key,
            poolclass=pool.NullPool,
        )
    else:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )

    if url.startswith("sqlite") and os.environ.get("SLOWBOOKS_SQLITE_FSYNC") == "0":
        # the test suite walks the whole chain in alembic subprocesses (the
        # migration tests), where its in-process no-fsync hook does not
        # reach; on the Windows runner that walk went past the per-test
        # timeout once the chain grew. The suite sets the flag; nothing
        # else does, and a real upgrade keeps SQLite's default durability.
        from sqlalchemy import event

        @event.listens_for(connectable, "connect")
        def _no_fsync(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA synchronous=OFF")
            cur.close()

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
