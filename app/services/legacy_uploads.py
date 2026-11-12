# ============================================================================
# The folder earlier versions shared between companies — and clearing it.
#
# Before 2.18.0 every company on an install wrote its uploads into one
# folder, storage.uploads_root(). Each company now keeps its files in its own
# database (file_store.py); the upgrade (migration c5e1f7a9b3d2) copies a
# company's files in from the folder once, the first time that company is
# opened on 2.18. After that nothing reads the folder, but it still holds
# every old logo, attachment and employee document (W-4s, I-9s, vendor
# W-9s), including the old copy of anything deleted since.
#
# It can't be cleared automatically: a company not yet opened on 2.18 still
# has to copy its files from it (since #769 such books are below the
# migration floor, and the page sends them to 2.31.14 to be opened), and nothing records which company a file
# belongs to. So an administrator clears it from Settings, once no company
# on the install still needs it. The removal takes only regular files inside
# the folder and the folders they leave empty: never the folder itself (on
# Docker it is a volume's mount point), never anything reached through a
# link, never anything outside it.
# ============================================================================

import logging
import os
import sqlite3
import stat
from contextlib import closing
from functools import lru_cache
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.services import company_file, company_service, storage

logger = logging.getLogger(__name__)

# The upgrade step that copies a company's files in from the folder.
COPY_REVISION = "c5e1f7a9b3d2"

# How long a company file another process is writing may keep a read
# waiting, in seconds. A file still busy after that counts as needing the
# folder: it has not been read, so nothing says it is done with it.
BUSY_SECONDS = 2.0

# How deep the removal goes. The folder's own layout is four levels
# (uploads/attachments/<record type>/<id>/<file>); anything deeper is left.
MAX_DEPTH = 32

_ROOT = Path(__file__).resolve().parent.parent.parent

# Descriptor-relative calls, where the platform has them (Linux, macOS):
# each folder is opened without following a link and everything inside it is
# named relative to that open folder, so swapping a folder for a link while
# the removal runs can't send it anywhere else. Windows walks by path.
_FD_WALK = (
    {os.open, os.stat, os.unlink, os.rmdir} <= os.supports_dir_fd
    and os.scandir in os.supports_fd
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
)


class StillNeeded(Exception):
    """Companies on this install have not copied their files in yet."""

    def __init__(self, companies: list[str], unread: list[str] | tuple = ()):
        self.companies = list(companies) + list(unread)
        super().__init__(still_needed_message(companies, unread))


# A company whose books could not be read just now (another program is
# writing them, a server that does not answer): nothing says it has copied
# its files in, so it keeps the folder, but it is not known to be old.
UNREAD = "unread"


def _names(names: list[str]) -> str:
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def still_needed_message(companies: list[str], unread: list[str] | tuple = ()) -> str:
    """The sentences a person reads when the folder can't be cleared yet.

    ``companies`` are books from before 2.18.0, whose upgrade copies their
    files in. They are below the migration floor (#769): this version never
    opens them, so "open it once" here would be a dead end. 2.31.14 opens
    them, and the sentence says so. ``unread`` are books that could not be
    read just now."""
    from app.services.migration_floor import (
        BELOW_FLOOR_MESSAGE,
        FLOOR_RELEASE,
        FLOOR_RELEASE_URL,
    )

    parts = []
    if len(companies) == 1:
        parts.append(
            f"{companies[0]} still needs to copy its files from the shared "
            f"folder; remove them once it has. {BELOW_FLOOR_MESSAGE}"
        )
    elif companies:
        parts.append(
            f"{_names(list(companies))} still need to copy their files from the "
            "shared folder; remove them once they have. Their company files are "
            "from before SlowBooks Pro 2.29.0. Open each of them once with "
            f"{FLOOR_RELEASE}, then upgrade: {FLOOR_RELEASE_URL}"
        )
    if len(unread) == 1:
        parts.append(
            f"{unread[0]} could not be read just now, so nothing says it has "
            "copied its files from the shared folder. Try again in a moment."
        )
    elif unread:
        parts.append(
            f"{_names(list(unread))} could not be read just now, so nothing says "
            "they have copied their files from the shared folder. Try again in "
            "a moment."
        )
    return " ".join(parts)


# --- What the folder holds -------------------------------------------------


def _is_reparse_point(st: os.stat_result) -> bool:
    """A Windows junction or other reparse point: a folder that is really
    somewhere else. It is not a symbolic link, and lstat reports it as a
    folder, so this is how to tell."""
    return bool(
        getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _walk_fd(dir_fd: int, device: int, remove: bool, depth: int) -> tuple[int, int]:
    count = size = 0
    with os.scandir(dir_fd) as it:
        entries = list(it)
    for entry in entries:
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            if remove:
                try:
                    # Removes the entry itself, never what a link points at.
                    os.unlink(entry.name, dir_fd=dir_fd)
                except OSError:
                    logger.warning(
                        "Could not remove %s from the shared folder", entry.name
                    )
                    continue
            count += 1
            size += st.st_size
        elif stat.S_ISDIR(st.st_mode) and st.st_dev == device and depth < MAX_DEPTH:
            try:
                sub = os.open(
                    entry.name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=dir_fd,
                )
            except OSError:
                continue  # gone, or a link now: not followed
            try:
                # The folder opened must be the one looked at, not one swapped in.
                if not os.path.samestat(st, os.fstat(sub)):
                    continue
                n, b = _walk_fd(sub, device, remove, depth + 1)
            finally:
                os.close(sub)
            count += n
            size += b
            if remove:
                try:
                    os.rmdir(entry.name, dir_fd=dir_fd)
                except OSError:
                    pass  # not empty: something in it is not ours to remove
        # A link, a device or a pipe is left where it is, unfollowed.
    return count, size


def _walk_path(folder: str, remove: bool, depth: int) -> tuple[int, int]:
    count = size = 0
    with os.scandir(folder) as it:
        entries = list(it)
    for entry in entries:
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode) and not entry.is_symlink():
            if remove:
                try:
                    os.unlink(entry.path)
                except OSError:
                    logger.warning(
                        "Could not remove %s from the shared folder", entry.path
                    )
                    continue
            count += 1
            size += st.st_size
        elif (
            stat.S_ISDIR(st.st_mode)
            and not entry.is_symlink()
            and not _is_reparse_point(st)
            and depth < MAX_DEPTH
        ):
            n, b = _walk_path(entry.path, remove, depth + 1)
            count += n
            size += b
            if remove:
                try:
                    os.rmdir(entry.path)
                except OSError:
                    pass  # not empty: something in it is not ours to remove
    return count, size


def _walk(remove: bool) -> tuple[int, int]:
    """(files, bytes) of the regular files inside the folder, removing them
    and the folders they leave empty when ``remove`` is set. A folder that
    is not there, or is a link to somewhere else, holds nothing."""
    root = str(storage.uploads_root())
    try:
        root_stat = os.lstat(root)
    except OSError:
        return 0, 0
    if not stat.S_ISDIR(root_stat.st_mode) or _is_reparse_point(root_stat):
        return 0, 0
    try:
        if not _FD_WALK:
            return _walk_path(root, remove, 0)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if not os.path.samestat(root_stat, os.fstat(fd)):
                return 0, 0
            return _walk_fd(fd, root_stat.st_dev, remove, 0)
        finally:
            os.close(fd)
    except OSError:
        logger.exception("Could not read the shared uploads folder %s", root)
        return 0, 0


# --- Which companies still need it -----------------------------------------


@lru_cache(maxsize=1)
def _history() -> tuple[frozenset, frozenset] | None:
    """(revisions before the copy step, the copy step and every one after),
    from this build's migrations; None when they can't be read."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(_ROOT / "migrations"))
        script = ScriptDirectory.from_config(cfg)
        upto = {r.revision for r in script.iterate_revisions(COPY_REVISION, "base")}
        known = {r.revision for r in script.walk_revisions()}
    except Exception:
        logger.exception("Could not read the migration history")
        return None
    before = frozenset(upto - {COPY_REVISION})
    return before, frozenset(known - before)


def _needs_folder(tables: set[str], revisions: list[str]) -> bool:
    """Whether books with these tables, at these migration revisions, still
    have to copy their files in from the folder.

    The revision is the answer: the copy step runs as part of the upgrade
    past it. A table alone is not, because a server started against books
    behind the upgrade creates stored_files without copying anything (see
    schema_repair), and the repair then copies from the folder."""
    if revisions:
        history = _history()
        if history is None:
            return UNREAD  # nothing says the copy has run, so keep the folder
        before, after = history
        if any(r in after for r in revisions):
            return False
        if any(r in before for r in revisions):
            return True
        # a revision this build doesn't know (a newer version's): the table
        # says whether the copy step has run
    if "stored_files" in tables:
        return False
    # Books that have never had files have nothing to copy.
    return bool(tables & {"attachments", "settings"})


def _connection_needs_folder(conn) -> bool:
    tables = set(sa.inspect(conn).get_table_names())
    revisions = []
    if "alembic_version" in tables:
        revisions = [
            row[0]
            for row in conn.execute(sa.text("SELECT version_num FROM alembic_version"))
        ]
    return _needs_folder(tables, revisions)


def _wal_mode(path: Path) -> bool:
    """Whether a SQLite file is in WAL mode, from its header (bytes 18 and
    19 are 2 for WAL, 1 for a rollback journal)."""
    try:
        with open(path, "rb") as fh:
            header = fh.read(20)
    except OSError:
        return False
    return header.startswith(b"SQLite format 3\x00") and header[18:20] == b"\x02\x02"


def _company_file_needs_folder(path: Path) -> bool:
    """A desktop company file, opened read-only. A file that is missing or
    is not a database can't copy anything; one that can't be read right now
    (another program is writing it) may still need the folder."""
    if not path.is_file():
        return False
    # An encrypted company file (#290) cannot be read here, and the read
    # below would only write -wal and -shm files beside it for nothing. It
    # was encrypted on this version or a later one, so it keeps nothing in
    # the shared folder.
    if company_file.is_encrypted_file(path):
        return False
    # Reading a WAL-mode company read-only made SQLite create empty -wal and
    # -shm files beside it, next to companies nobody had opened (macbase1
    # NEW-17), so their absence no longer meant "never opened". A WAL-mode
    # file with no -wal beside it is one nobody has open: read it as it lies
    # (immutable), which leaves nothing beside it. Any other file is read the
    # ordinary way, with its locks: a rollback-journal file makes no
    # sidecars, and one in use waits, then counts as needing the folder.
    idle_wal = _wal_mode(path) and not path.with_name(path.name + "-wal").exists()
    try:
        uri = path.resolve().as_uri() + (
            "?mode=ro&immutable=1" if idle_wal else "?mode=ro"
        )
        # the engine's own SQLite library, never a second one on a company
        # file (NEW-156)
        from app.services.company_file import sqlite_module

        with closing(
            sqlite_module().connect(uri, uri=True, timeout=BUSY_SECONDS)
        ) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            revisions = []
            if "alembic_version" in tables:
                revisions = [
                    row[0]
                    for row in conn.execute("SELECT version_num FROM alembic_version")
                ]
    except (sqlite3.OperationalError, sqlite_module().OperationalError):
        logger.warning(
            "Could not read company file %s; keeping the shared folder", path
        )
        return UNREAD
    except (sqlite3.DatabaseError, sqlite_module().DatabaseError):
        return False  # not a database, or damaged beyond opening
    return _needs_folder(tables, revisions)


def _served_name(db: Session) -> str:
    from app.services.settings_service import get_setting_raw

    return (get_setting_raw(db, "company_name") or "").strip() or "This company"


def _sort(found: list[tuple[str, object]]) -> tuple[list[str], list[str]]:
    """(books from before 2.18.0, books that could not be read just now)."""
    old = [name for name, needs in found if needs and needs != UNREAD]
    unread = [name for name, needs in found if needs == UNREAD]
    return old, unread


def _sqlite_pending(db: Session) -> tuple[list[str], list[str]]:
    """The companies in the desktop manifest (the company picker's list),
    and the books this server has open, checked through its own session."""
    found = []
    served_listed = False
    for entry in company_service.manifest_list_companies():
        name = (entry.get("name") or "").strip() or entry.get("file") or "A company"
        if entry.get("is_current"):
            served_listed = True
            found.append((name, _connection_needs_folder(db.connection())))
            continue
        path = company_service.company_db_path(entry.get("file") or "")
        # A name the picker can't open is a company that can't copy anything.
        if path is not None:
            found.append((name, _company_file_needs_folder(path)))
    if not served_listed:
        found.insert(0, (_served_name(db), _connection_needs_folder(db.connection())))
    return _sort(found)


def _server_databases(db: Session) -> set[str]:
    """Every database on the PostgreSQL server this one is on."""
    return {row[0] for row in db.execute(sa.text("SELECT datname FROM pg_database"))}


def _company_database_url(database: str):
    """Another database on the same server, with the same credentials and
    connection options as the one this server is connected to."""
    return make_url(company_service.DATABASE_URL).set(database=database)


def _postgres_pending(db: Session) -> tuple[list[str], list[str]]:
    """The database this server is connected to, and every other one the
    companies table lists on the same server. A listed database the server
    no longer has can't copy anything; one it has but that can't be read
    from here may still need the folder."""
    from app.models.companies import Company

    found = [(_served_name(db), _connection_needs_folder(db.connection()))]
    served = make_url(company_service.DATABASE_URL).database
    others = [
        ((c.name or "").strip() or c.database_name, c.database_name)
        for c in db.query(Company).order_by(Company.name).all()
        if c.database_name and c.database_name != served
    ]
    if not others:
        return _sort(found)
    existing = _server_databases(db)
    for name, database in others:
        if database not in existing:
            continue
        url = _company_database_url(database)
        engine = sa.create_engine(
            url,
            poolclass=NullPool,
            # a server that doesn't answer doesn't hold the page up for long
            connect_args=(
                {"connect_timeout": 10}
                if url.get_driver_name() in ("psycopg2", "psycopg")
                else {}
            ),
        )
        try:
            with engine.connect() as conn:
                needs = _connection_needs_folder(conn)
        except SQLAlchemyError:
            logger.warning("Could not read company database %s", database)
            needs = UNREAD
        finally:
            engine.dispose()
        found.append((name, needs))
    return _sort(found)


def pending_state(db: Session) -> tuple[list[str], list[str]]:
    """(the companies on this install from before 2.18.0, which still have
    to copy their files in from the folder; the ones that could not be read
    just now, which may still have to)."""
    if company_service._is_sqlite():
        return _sqlite_pending(db)
    return _postgres_pending(db)


def pending_companies(db: Session) -> list[str]:
    """Every company that keeps the folder: both lists of pending_state."""
    old, unread = pending_state(db)
    return old + unread


# --- The routes' two answers -----------------------------------------------


def folder_state(db: Session) -> dict:
    """How much the folder still holds, and whether it can be cleared. An
    empty folder has nothing for any company to copy, so nothing is
    pending and no company file is opened to find out."""
    files, size = _walk(remove=False)
    old, unread = pending_state(db) if files else ([], [])
    pending = old + unread
    return {
        "files": files,
        "bytes": size,
        "pending_companies": pending,
        # what the page says about them (#769: books from before 2.18.0 are
        # opened with 2.31.14, which this version sends them to)
        "pending_message": still_needed_message(old, unread) if pending else "",
        "can_remove": files > 0 and not pending,
    }


def remove_files(db: Session) -> dict:
    """Delete every regular file in the folder and the folders left empty,
    keeping the folder itself. Raises StillNeeded, having removed nothing,
    while a company still has to copy its files from it."""
    files, _size = _walk(remove=False)
    old, unread = pending_state(db) if files else ([], [])
    if old or unread:
        raise StillNeeded(old, unread)
    removed, size = _walk(remove=True)
    return {"removed": removed, "bytes": size}
