"""The oldest company file this build opens (#769).

A company file is migrated and opened only when its ``alembic_version`` is
at or past ``MIGRATION_FLOOR``: 2.29.0's migration head, the 59th of the
chain. Every release since 2.29.0 (October 2026) was offered to every
install on its next start, so a file below the floor is one nobody has
opened since before 2.29.0. Such a file is not migrated here: the path from
it runs through 59 old migrations the suite no longer walks. 2.31.14 still
walks them, so the way forward is to open the file once with 2.31.14 and
then upgrade.

The migrations before the floor stay in the repo; a fresh database (no
``alembic_version`` at all) is still built through them. Where a revision
sits is decided by walking the chain with Alembic's ScriptDirectory, never
by comparing revision strings.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# 2.29.0's migration head (items.code, #445): the oldest revision this build
# migrates a company file from.
MIGRATION_FLOOR = "c4e5d6f7a8b9"

# The last release that still walks the migrations below the floor.
FLOOR_RELEASE = "2.31.14"
FLOOR_RELEASE_URL = (
    "https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/releases/tag/v2.31.14"
)

BELOW_FLOOR_MESSAGE = (
    "This company file is from before SlowBooks Pro 2.29.0. Open it once "
    f"with {FLOOR_RELEASE}, then upgrade: {FLOOR_RELEASE_URL}"
)

ROOT = Path(__file__).resolve().parents[2]


class BelowMigrationFloor(RuntimeError):
    """A company file from before 2.29.0. ``str(exc)`` is the sentence."""

    def __init__(self, message: str = BELOW_FLOOR_MESSAGE):
        super().__init__(message)


@lru_cache(maxsize=1)
def _at_or_past_floor() -> frozenset[str]:
    """Every revision from the floor up to the head, walked by Alembic."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    return frozenset(
        rev.revision
        for rev in script.walk_revisions(base=MIGRATION_FLOOR, head="heads")
    )


@lru_cache(maxsize=1)
def _known() -> frozenset[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return frozenset(
        rev.revision for rev in ScriptDirectory.from_config(cfg).walk_revisions()
    )


def is_at_or_past_floor(revision) -> bool:
    """True when a file at ``revision`` (one revision, or the rows of its
    ``alembic_version``) may be migrated and opened.

    No revision is a new database, which the migrations build from nothing:
    allowed. A revision this build does not know is a file from a NEWER
    build; that is the start-up guard's and the restore's own refusal, not
    this one, so it passes here."""
    if revision is None:
        return True
    revisions = [revision] if isinstance(revision, str) else list(revision)
    revisions = [r for r in revisions if r]
    if not revisions:
        return True
    floor = _at_or_past_floor()
    known = _known()
    return all(r in floor or r not in known for r in revisions)


def is_below_floor(revision) -> bool:
    return not is_at_or_past_floor(revision)


def revisions_of(connection) -> list[str]:
    """The rows of ``alembic_version`` on a SQLAlchemy connection or engine
    ([] when there is no such table: a new database)."""
    from sqlalchemy import inspect, text
    from sqlalchemy.engine import Engine

    if isinstance(connection, Engine):
        with connection.connect() as conn:
            return revisions_of(conn)
    if not inspect(connection).has_table("alembic_version"):
        return []
    return [
        row[0]
        for row in connection.execute(text("SELECT version_num FROM alembic_version"))
        if row[0]
    ]


def refuse_below_floor(connection) -> None:
    """Raise BelowMigrationFloor when the database is from before 2.29.0."""
    if is_below_floor(revisions_of(connection)):
        raise BelowMigrationFloor()


def file_revisions(
    path, key: bytes | None = None, *, resolve_key: bool = True
) -> list[str] | None:
    """The rows of a SQLite company file's ``alembic_version``, read-only
    and immutable, so reading never changes the file. None when it cannot
    be read here (an encrypted file with no key in hand, not a database).
    ``resolve_key=False`` reads an encrypted file only with ``key``: a list
    of companies does not derive a key for each one."""
    from contextlib import closing

    from app.services import company_file

    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        if key is None and company_file.is_encrypted_file(path):
            key = company_file.resolve_key() if resolve_key else None
            if key is None:
                return None
        with closing(company_file.connect_raw(path, key, readonly=True)) as conn:
            has = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'alembic_version'"
            ).fetchone()
            if not has:
                return []
            return [
                row[0]
                for row in conn.execute("SELECT version_num FROM alembic_version")
                if row[0]
            ]
    except (OSError, *company_file.errors()):
        return None


def file_problem(
    path, key: bytes | None = None, *, resolve_key: bool = True
) -> str | None:
    """The sentence when a SQLite company file is from before 2.29.0, else
    None (also when the file cannot be read here)."""
    revisions = file_revisions(path, key, resolve_key=resolve_key)
    if revisions is None:
        return None
    return BELOW_FLOOR_MESSAGE if is_below_floor(revisions) else None
