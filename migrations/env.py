import os
from logging.config import fileConfig

# INSTALL.md has the user write DATABASE_URL into .env and THEN run
# `alembic upgrade head`. Alembic is its own entrypoint and never boots
# app.config, so without this the .env is ignored, sqlalchemy.url falls back
# to the placeholder in alembic.ini, and the user gets
# `FATAL: password authentication failed for user "user"` — a user they
# never created, with no hint that the .env they just edited was unread.
# override=False so a real exported DATABASE_URL (Docker) still wins.
try:
    from dotenv import load_dotenv

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
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
