# ============================================================================
# Backup/Restore Service — accessible from settings
# Feature 11: Database backup and restore
#
# PostgreSQL (Docker / server installs): pg_dump/pg_restore subprocess.
# SQLite (native desktop installs): a snapshot copy of the company's .db
# file via sqlite3's online backup API (consistent even with the app's own
# connections open). An encrypted company file (#290) is copied under its
# key, so its backups are ciphertext too; a restore leaves the books in the
# state they are in, whichever state the backup was in.
# ============================================================================

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.models.backups import Backup
from app.services import company_file, storage

logger = logging.getLogger(__name__)

BACKUP_DIR = storage.backups_root()
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# Strict filename allow-list. Backup files we create are named
# "<company>_YYYYMMDD_HHMMSS.db" (SQLite) or ".sql" (Postgres) — before
# 2.17.4 every company's were "slowbooks_YYYYMMDD_HHMMSS", which still
# list and restore. We accept any safe basename matching this character
# class with a known backup extension. NO path separators, NO ".."
# components -- this is the trust boundary that CodeQL needs to see at the
# start of restore_backup() before BACKUP_DIR / filename is constructed.
# Matched with re.fullmatch (not .match + ^...$): in Python `$` also matches
# just before a trailing "\n", so "evil.db\n" would slip through .match.
# The extension is split off first and the stem matched on its own: one
# pattern holding both a "." in the class and a literal "\." before the
# extension backtracks polynomially on a long run of those characters.
_BACKUP_STEM_RE = re.compile(r"[A-Za-z0-9_.-]+")
_BACKUP_EXTENSIONS = frozenset({"sql", "dump", "backup", "db"})

# Every company's backups share one folder, and the old names said nothing
# about whose they were (explore 2.17.3, macbase1 F25: Riverbend x3,
# NEONpulse x2 and Harbor Light x1, all "slowbooks_<date>_<time>.db"). The
# name now starts with the company's own part: its file name on the
# desktop, its database name on a server.
_NAME_RE = re.compile(
    r"^(?P<slug>.+)_(?P<stamp>\d{8}_\d{6})(?:-(?P<tag>[a-z0-9-]+))?"
    r"\.(?P<ext>sql|dump|backup|db)$"
)
LEGACY_SLUG = "slowbooks"
# The copy taken automatically just before a restore replaces the books.
PRE_RESTORE_TAG = "before-restore"


def parse_backup_name(filename: str) -> dict | None:
    """{"slug", "stamp", "tag"} for a name this app makes, else None."""
    m = _NAME_RE.match(filename or "")
    return m.groupdict() if m else None


# A company's part of a backup name decides whose backups are whose, so two
# companies must never share one: a file stem that is already that part
# (lowercase letters, digits and single dashes, 60 characters at most) is
# kept as it is, and any other stem is shortened and given ten characters of
# a hash of the whole stem, after a "--" no kept stem has.
_PLAIN_KEY_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_KEY_MAX = 60


def _readable(stem: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")


def backup_key_for_stem(stem: str) -> str:
    """The backup-name part of a company whose file stem (or database name)
    is ``stem``. Two different stems never get the same one."""
    if stem == LEGACY_SLUG:
        return f"{LEGACY_SLUG}-books"
    if (
        len(stem) <= _KEY_MAX
        and _PLAIN_KEY_RE.fullmatch(stem)
        and stem != f"{LEGACY_SLUG}-books"
    ):
        return stem
    digest = hashlib.sha256(stem.encode("utf-8")).hexdigest()[:10]
    readable = _readable(stem)[: _KEY_MAX - 12].strip("-") or "company"
    return f"{readable}--{digest}"


def old_backup_key_for_stem(stem: str) -> str:
    """The part a backup name was given before 2.32.0-beta.2: the stem
    lowercased, every other character a dash, cut to 60 characters. Two
    companies could share it."""
    slug = _readable(stem)[:_KEY_MAX].strip("-")
    if not slug:
        slug = "company"
    return f"{slug}-books" if slug == LEGACY_SLUG else slug


def _company_stem() -> str:
    if _is_sqlite():
        path = _sqlite_db_path()
        return path.stem if path is not None else ""
    return _parse_db_url(DATABASE_URL)["dbname"]


def company_slug() -> str:
    """This company's part of a backup file name: "harbor-light-bakery" for
    harbor-light-bakery.db, the database name on a server. Never the old
    anonymous "slowbooks", so a new name can always be told from an old one,
    and never another company's."""
    return backup_key_for_stem(_company_stem())


def backup_key_clash(filename: str) -> str | None:
    """The listed company file whose backups a new company file named
    ``filename`` would be mixed up with, or None."""
    from app.services import served_companies

    stem = Path(filename).stem
    key = backup_key_for_stem(stem)
    try:
        rows = served_companies.listed()
    except Exception:
        return None
    for row in rows:
        other = Path(row["file"]).stem
        if other == stem:
            continue
        if key in (backup_key_for_stem(other), old_backup_key_for_stem(other)):
            return row["file"]
    return None


# Whose is a backup whose company part two listed companies have, or had?
# Before 2.32.0-beta.2 two companies could share one ("acme_co" and
# "acme--co" both made "acme-co_..."; so did two long names sharing their
# first 60 characters), and a plain part like "acme-co" is also what such a
# company's backups were named with. The name alone cannot say whose such a
# file is; a backups table can, but a restore replaces the table with the
# backup's, so the safety copy taken before it, and every backup made after
# the one restored, are in no table. What the install itself knows is kept
# here: a record in the backups folder, written by this app only (no company
# administrator can write it), of whose each backup is. Every new backup is
# recorded as it is made; one made before is settled once, the first time a
# company it could belong to looks, from the evidence the company files hold.
_OWNERS_FILE = ".owners.json"
_owners_lock = threading.Lock()


def _owners_path() -> Path:
    return BACKUP_DIR / _OWNERS_FILE


def _read_owners() -> dict:
    """{backup file name: the stem of the company it is, or None when it
    was settled and nothing said whose it is}."""
    try:
        data = json.loads(_owners_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    owners = data.get("owners") if isinstance(data, dict) else None
    if not isinstance(owners, dict):
        return {}
    return {
        name: stem
        for name, stem in owners.items()
        if _safe_backup_filename(name) is not None
        and (stem is None or isinstance(stem, str))
    }


def _record_owners(changes: dict) -> None:
    """Add ``changes`` to the record, dropping files no longer there. A
    record that cannot be written is logged: the backup itself is made."""
    with _owners_lock:
        owners = _read_owners()
        owners.update(changes)
        owners = {n: s for n, s in owners.items() if (BACKUP_DIR / n).is_file()}
        path = _owners_path()
        staged = path.with_name(path.name + ".writing")
        try:
            staged.write_text(
                json.dumps({"version": 1, "owners": owners}, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(staged, path)
        except OSError:
            logger.exception("Could not record whose the backups are")
            staged.unlink(missing_ok=True)


def _listed_rows() -> list:
    from app.services import served_companies

    try:
        return served_companies.listed()
    except Exception:
        return []


def _table_rows_of_file(path: Path, *, as_it_lies: bool = False) -> set | None:
    """The (filename, created_at) rows of the backups table in a company
    file or (``as_it_lies``: opened read-only, never changed) a SQLite
    backup, or None when it cannot be read here."""
    if path is None or not path.is_file():
        return None
    keys = [None]
    if company_file.is_encrypted_file(path):
        keys = []
        for find in (company_file.install_key, company_file.resolve_key):
            try:
                key = find()
            except Exception:
                key = None
            if key is not None and key not in keys:
                keys.append(key)
    for key in keys:
        try:
            with closing(
                company_file.connect_raw(path, key, readonly=as_it_lies)
            ) as conn:
                found = conn.execute(
                    "SELECT filename, created_at FROM backups"
                ).fetchall()
            return {(str(f), str(c)) for f, c in found}
        except company_file.errors() + (OSError,):
            continue
    return None


class _Ownership:
    """Whose each named backup is, as the open company sees it."""

    def __init__(self, db: Session):
        from app.services import served_companies

        self.db = db
        self.stem = _company_stem()
        self.slug = backup_key_for_stem(self.stem)
        self.several = served_companies.enabled()
        self.listed = _listed_rows()
        self.names = {Path(r["file"]).stem: r.get("name") or "" for r in self.listed}
        self.files = {Path(r["file"]).stem: r["file"] for r in self.listed}
        self.by_key: dict = {}
        for stem in set(self.names) | {self.stem}:
            for key in {backup_key_for_stem(stem), old_backup_key_for_stem(stem)}:
                self.by_key.setdefault(key, set()).add(stem)
        self.rows = {b.filename: b for b in db.query(Backup).all()}
        self._owners = None

    def candidates(self, slug: str) -> set:
        """The companies a backup named with ``slug`` may be, this one among
        them only when the name could be its."""
        return set(self.by_key.get(slug, ()))

    def owners(self) -> dict:
        """The record, after settling every file this company may own that
        the record does not have yet."""
        if self._owners is None:
            owners = _read_owners()
            unsettled = {}
            for path in BACKUP_DIR.iterdir():
                name = path.name
                if name in owners or _safe_backup_filename(name) is None:
                    continue
                parsed = parse_backup_name(name)
                if not parsed or not path.is_file():
                    continue
                found = self.candidates(parsed["slug"])
                if self.stem in found and len(found) > 1:
                    unsettled[name] = found
            if unsettled:
                settled = self._settle(unsettled)
                _record_owners(settled)
                owners.update(settled)
            self._owners = owners
        return self._owners

    def _live_rows(self, stem: str) -> set | None:
        if stem == self.stem:
            from sqlalchemy import text

            try:
                found = self.db.execute(
                    text("SELECT filename, created_at FROM backups")
                ).fetchall()
            except Exception:
                return None
            return {(str(f), str(c)) for f, c in found}
        from app.services import company_service

        listed = self.files.get(stem)
        if listed is None:
            return None
        return _table_rows_of_file(company_service.company_db_path(listed))

    def _settle(self, unsettled: dict) -> dict:
        """Whose each file is, from what cannot be forged by a company's
        administrator: the company whose backups table lists it, or whose
        table shares a row with the backups table the file itself holds (the
        books it is a copy of), or that owns a backup whose own table lists
        it. A file two companies have evidence for, or none, is settled as
        nobody's (None)."""
        stems = set().union(*unsettled.values())
        live = {stem: self._live_rows(stem) for stem in stems}
        held = {
            name: _table_rows_of_file(BACKUP_DIR / name, as_it_lies=True)
            for name in unsettled
            if name.endswith(".db")
        }
        evidence = {name: set() for name in unsettled}
        for name, found in unsettled.items():
            for stem in found:
                rows = live.get(stem)
                if not rows:
                    continue
                own = held.get(name)
                if name in {f for f, _ in rows} or (own and own & rows):
                    evidence[name].add(stem)
        changed = True
        while changed:
            changed = False
            for name, found in unsettled.items():
                if len(evidence[name]) == 1:
                    continue
                for other, rows in held.items():
                    if other == name or len(evidence[other]) != 1 or not rows:
                        continue
                    (owner,) = evidence[other]
                    if owner in found and owner not in evidence[name]:
                        if name in {f for f, _ in rows}:
                            evidence[name].add(owner)
                            changed = True
        return {
            name: (next(iter(stems)) if len(stems) == 1 else None)
            for name, stems in evidence.items()
        }

    def is_mine(self, filename: str, slug: str) -> bool:
        """Whether a backup named for ``slug`` is this company's."""
        found = self.candidates(slug)
        if self.stem not in found:
            return False  # named for another company
        if len(found) == 1:
            return True
        owner = self.owners().get(filename)
        if owner == self.stem or filename in self.rows:
            return True
        if owner is not None or self.several:
            # another company's, or, on a server serving several, a file
            # nothing the install holds says is this one's
            return False
        # The desktop: one person's companies. A file nothing settled is
        # this company's when it carries this company's name, or no other
        # company's it could be, so nobody loses sight of a backup they had.
        stored = read_backup_facts(filename).get("company_name")
        if stored is None:
            return True
        name = current_company_name(self.db)
        if _same_name(stored, name):
            return True
        return not any(
            _same_name(stored, self.names.get(other))
            for other in found
            if other != self.stem
        )


def _new_backup_filename(tag: str | None = None) -> str:
    ext = "db" if _is_sqlite() else "sql"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{company_slug()}_{stamp}" + (f"-{tag}" if tag else "")
    name = f"{base}.{ext}"
    n = 2
    while (BACKUP_DIR / name).exists():  # two in one second must not overwrite
        name = f"{base}-{n}.{ext}"
        n += 1
    return name


def _safe_backup_filename(filename: str) -> str | None:
    """Return a safe basename for a backup file, or None if invalid.

    Uses os.path.basename() to strip any path components — this is a
    sanitizer recognized by static analyzers (CodeQL py/path-injection)
    so the returned value is treated as path-safe at downstream sinks.
    Then enforces the strict regex / length / leading-dot rules.
    """
    if not filename or len(filename) > 255:
        return None
    # os.path.basename strips any directory component; if the input
    # contained separators, the result will differ from the input and we
    # reject it (keeps "files only" semantics rather than silently
    # accepting "evil/foo.sql" as "foo.sql").
    base = os.path.basename(filename)
    if base != filename:
        return None
    if base.startswith(".") or ".." in base:
        return None
    stem, dot, extension = base.rpartition(".")
    if not dot or extension not in _BACKUP_EXTENSIONS:
        return None
    if not _BACKUP_STEM_RE.fullmatch(stem):
        return None
    return base


def _is_sqlite() -> bool:
    return DATABASE_URL.startswith("sqlite")


def _sqlite_db_path() -> Path | None:
    """Filesystem path of the active SQLite database, or None when the URL
    isn't a file-backed SQLite database (e.g. sqlite:///:memory:). On a
    Server Edition server, the company this request works in (#358)."""
    from app.database import another_company_url

    url = another_company_url() or DATABASE_URL
    if not url.startswith("sqlite:///"):
        return None
    raw = url[len("sqlite:///") :]
    if not raw or raw == ":memory:":
        return None
    return Path(raw)


def _parse_db_url(url: str) -> dict:
    """Parse PostgreSQL connection URL into components."""
    # postgresql://user:pass@host:port/dbname
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "user": parsed.username or "bookkeeper",
        "password": parsed.password or "",
        "dbname": parsed.path.lstrip("/") or "bookkeeper",
    }


def _create_sqlite_backup(
    db: Session, notes: str, backup_type: str, tag: str | None = None
) -> dict:
    """Snapshot the active company's .db file into BACKUP_DIR."""
    src = _sqlite_db_path()
    if src is None or not src.exists():
        return {
            "success": False,
            "error": "Active database is not a file-backed SQLite database",
        }

    filename = _new_backup_filename(tag)
    filepath = BACKUP_DIR / filename

    try:
        # sqlite3's online backup API gives a consistent snapshot even if
        # the app holds open connections (unlike a raw file copy). An
        # encrypted company is copied under its key: the backup is
        # ciphertext, readable with the same key.
        key = (
            company_file.resolve_key() if company_file.is_encrypted_file(src) else None
        )
        with closing(company_file.connect_raw(src, key)) as source, closing(
            company_file.connect_raw(filepath, key)
        ) as dest:
            source.backup(dest)
    except company_file.errors():
        # Exception text can leak paths/internals into the HTTP response
        # (routes surface this error string) — log it, say something generic.
        logger.exception("SQLite backup failed")
        filepath.unlink(missing_ok=True)
        return {"success": False, "error": "SQLite backup failed. Check logs."}

    file_size = filepath.stat().st_size
    backup = Backup(
        filename=filename,
        file_size=file_size,
        backup_type=backup_type,
        notes=notes,
    )
    db.add(backup)
    db.commit()
    _record_owners({filename: _company_stem()})

    return {"success": True, "filename": filename, "file_size": file_size}


OTHER_KEY_MESSAGE = (
    "This backup was made under another key (another recovery key, or "
    "another install's secret). Enter the recovery key it was made under "
    "to restore it."
)


def is_encrypted_backup(path: Path | str | None) -> bool:
    """Whether a backup is an encrypted company file. Only a SQLite backup
    (.db) can be one: a Postgres backup is pg_dump's own format, which is
    not SQLite either but is not encrypted, and restoring it needs no key."""
    if path is None or Path(path).suffix != ".db":
        return False
    return company_file.is_encrypted_file(path)


def backup_key_problem(filename: str, recovery_key: str | None = None) -> dict | None:
    """Whether a backup can be opened here: None when it can (plain, or
    encrypted under this company's key, or under the recovery key given),
    else {"code", "message"}. Asked before anything is written."""
    from app.services import company_keyring

    path = backup_path(filename)
    if not is_encrypted_backup(path):
        return None
    key = company_file.resolve_key()
    if key is not None:
        try:
            company_file.verify_key(path, key)
            return None
        except company_file.CompanyFileLocked:
            pass
    if (recovery_key or "").strip():
        try:
            company_keyring.unlock_with_recovery(path, recovery_key)
            return None
        except company_file.CompanyFileLocked:
            return {
                "code": "wrong_recovery_key",
                "message": company_keyring.WRONG_RECOVERY_MESSAGE,
            }
    return {"code": "other_key", "message": OTHER_KEY_MESSAGE}


def _restore_sqlite_backup(
    filepath: Path, safe_name: str, recovery_key: str | None = None
) -> dict:
    """Copy a backup .db over the active company's database file. A backup
    encrypted under another key is opened with the recovery key it was made
    under (#290) and re-keyed, on a staged copy, to this company's key."""
    dest = _sqlite_db_path()
    if dest is None:
        return {
            "success": False,
            "error": "Active database is not a file-backed SQLite database",
        }

    # Close the app's pooled connections first so the live file can be
    # rewritten (SQLite locking; also matters for Windows file semantics).
    import app.database as db_module

    db_module.current_engine().dispose()

    live_encrypted = company_file.is_encrypted_file(dest)
    backup_encrypted = company_file.is_encrypted_file(filepath)
    key = None
    if live_encrypted or backup_encrypted:
        key = company_file.resolve_key()
        if key is None and live_encrypted:
            return {"success": False, "error": company_file.NO_KEY_MESSAGE}
    staged = None
    try:
        source_path = filepath
        if backup_encrypted:
            problem = backup_key_problem(filepath.name, recovery_key)
            if problem is not None:
                return {"success": False, "error": problem["message"], **problem}
            opens_here = key is not None
            if opens_here:
                try:
                    company_file.verify_key(filepath, key)
                except company_file.CompanyFileLocked:
                    opens_here = False
            if not opens_here:
                # another key: a staged copy is brought to this company's
                # key (or made plain, for a plain company); the backup file
                # itself is never changed
                from app.services import company_keyring

                other = company_keyring.key_from_recovery(recovery_key or "")
                staged = BACKUP_DIR / (filepath.name + ".restoring")
                shutil.copy2(filepath, staged)
                if live_encrypted:
                    company_file.rekey(staged, other, key)
                else:
                    company_file.convert(staged, other, encrypt=False)
                source_path = staged
                backup_encrypted = live_encrypted
        if backup_encrypted != live_encrypted:
            if key is None:
                return {"success": False, "error": company_file.NO_KEY_MESSAGE}
            # The books stay in the state they are in: a plain backup
            # restored into an encrypted company arrives encrypted, and
            # the other way around. The backup file itself is never
            # changed — a staged copy is converted and restored from.
            staged = BACKUP_DIR / (filepath.name + ".restoring")
            shutil.copy2(filepath, staged)
            company_file.convert(staged, key, encrypt=live_encrypted)
            source_path = staged
        live_key = key if live_encrypted else None
        with closing(
            company_file.connect_raw(source_path, live_key)
        ) as source, closing(company_file.connect_raw(dest, live_key)) as target:
            source.backup(target)
    except company_file.errors() + (OSError,):
        # Same shape as _create_sqlite_backup: no exception text in the
        # user-facing error (py/stack-trace-exposure) — details go to logs.
        logger.exception("SQLite restore failed")
        return {"success": False, "error": "SQLite restore failed. Check logs."}
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)

    return {"success": True, "message": f"Restored from {safe_name}"}


def create_backup(
    db: Session, notes: str = None, backup_type: str = "manual", tag: str = None
) -> dict:
    """Create a database backup (pg_dump on Postgres, file snapshot on SQLite),
    named for this company; ``tag`` marks it in the name (PRE_RESTORE_TAG)."""
    if _is_sqlite():
        return _create_sqlite_backup(db, notes, backup_type, tag)

    params = _parse_db_url(DATABASE_URL)
    filename = _new_backup_filename(tag)
    filepath = BACKUP_DIR / filename

    env = {"PGPASSWORD": params["password"]}

    try:
        result = subprocess.run(
            [
                "pg_dump",
                "-h",
                params["host"],
                "-p",
                params["port"],
                "-U",
                params["user"],
                "-F",
                "c",
                "-f",
                str(filepath),
                params["dbname"],
            ],
            env={**dict(__import__("os").environ), **env},
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr}

        file_size = filepath.stat().st_size

        backup = Backup(
            filename=filename,
            file_size=file_size,
            backup_type=backup_type,
            notes=notes,
        )
        db.add(backup)
        db.commit()
        _record_owners({filename: _company_stem()})

        return {"success": True, "filename": filename, "file_size": file_size}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Backup timed out"}
    except FileNotFoundError:
        return {
            "success": False,
            "error": "pg_dump not found. Is PostgreSQL client installed?",
        }


def restore_backup(db: Session, filename: str, recovery_key: str | None = None) -> dict:
    """Restore a database from a backup file."""
    # Trust boundary: validate filename as a safe basename BEFORE using it
    # to construct any filesystem path. is_relative_to is the
    # belt-and-suspenders check after.
    safe_name = _safe_backup_filename(filename)
    if safe_name is None:
        return {"success": False, "error": "Invalid filename"}

    # normpath + startswith is the containment guard CodeQL recognizes for
    # py/path-injection (is_relative_to on Path objects is not).
    backup_root = os.path.normpath(str(BACKUP_DIR))
    candidate = os.path.normpath(os.path.join(backup_root, safe_name))
    if not candidate.startswith(backup_root + os.sep):
        return {"success": False, "error": "Invalid filename"}
    filepath = Path(candidate)
    if not filepath.exists():
        return {"success": False, "error": f"Backup file not found: {safe_name}"}

    if _is_sqlite():
        return _restore_sqlite_backup(filepath, safe_name, recovery_key)

    params = _parse_db_url(DATABASE_URL)
    env = {"PGPASSWORD": params["password"]}

    try:
        result = subprocess.run(
            [
                "pg_restore",
                "-h",
                params["host"],
                "-p",
                params["port"],
                "-U",
                params["user"],
                "-d",
                params["dbname"],
                "--clean",
                "--if-exists",
                str(filepath),
            ],
            env={**dict(__import__("os").environ), **env},
            capture_output=True,
            text=True,
            timeout=300,
        )
        # pg_restore may return non-zero even on partial success
        if result.returncode != 0 and "error" in result.stderr.lower():
            return {"success": False, "error": result.stderr[:500]}

        return {"success": True, "message": f"Restored from {filename}"}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Restore timed out"}
    except FileNotFoundError:
        return {"success": False, "error": "pg_restore not found"}


def list_backup_files() -> list[dict]:
    """List every backup file in the (shared) backup directory, any company."""
    files = []
    candidates = [
        p
        for p in BACKUP_DIR.iterdir()
        if p.is_file() and _safe_backup_filename(p.name) is not None
    ]
    for f in sorted(candidates, key=lambda p: p.name, reverse=True):
        files.append(
            {
                "filename": f.name,
                "file_size": f.stat().st_size,
                "created": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                # a backup made before encryption was turned on is plain
                # SQLite (2.24.0 gate, NEW-158): the page says which
                "encrypted": is_encrypted_backup(f),
            }
        )
    return files


# ---------------------------------------------------------------------------
# Whose backup is it? (explore 2.17.3, macbase1 F25)
# ---------------------------------------------------------------------------


def backup_path(filename: str) -> Path | None:
    """The backup's path, validated the same way restore_backup() does."""
    safe_name = _safe_backup_filename(filename)
    if safe_name is None:
        return None
    backup_root = os.path.normpath(str(BACKUP_DIR))
    candidate = os.path.normpath(os.path.join(backup_root, safe_name))
    if not candidate.startswith(backup_root + os.sep):
        return None
    return Path(candidate)


def read_backup_facts(filename: str) -> dict:
    """What a SQLite backup says about itself: {"company_name", "revision"}
    (either may be None). Opened read-only and immutable, so reading never
    changes the file. Empty for a Postgres dump or anything unreadable."""
    path = backup_path(filename)
    if path is None or path.suffix != ".db" or not path.exists():
        return {}
    facts = {"company_name": None, "revision": None}
    key = None
    if company_file.is_encrypted_file(path):
        key = company_file.resolve_key()
        if key is None:
            return {}  # an encrypted backup with no key in hand: unreadable here
    try:
        with closing(company_file.connect_raw(path, key, readonly=True)) as conn:
            for fact, sql in (
                (
                    "company_name",
                    "SELECT value FROM settings WHERE key = 'company_name'",
                ),
                ("revision", "SELECT version_num FROM alembic_version"),
            ):
                try:
                    row = conn.execute(sql).fetchone()
                except company_file.errors():
                    row = None
                facts[fact] = (row[0] or None) if row else None
    except company_file.errors():
        logger.exception("Could not read backup %s", path.name)
        return {}
    return facts


def _same_name(a: str | None, b: str | None) -> bool:
    return (a or "").strip().casefold() == (b or "").strip().casefold()


def current_company_name(db: Session) -> str:
    from app.models.settings import DEFAULT_SETTINGS
    from app.services.settings_service import get_setting_raw

    return (get_setting_raw(db, "company_name") or "").strip() or DEFAULT_SETTINGS[
        "company_name"
    ]


def list_company_backups(db: Session) -> list[dict]:
    """The open company's backups, newest first.

    The folder holds every company's. This company's are the files named
    for it (when another company has, or had, that name too, the ones the
    install's record or this company's backups table says are its own; see
    _Ownership), plus older anonymous "slowbooks_*" files that are its own: ones
    its backups table lists, or (a SQLite copy) that carry its company name.
    Read from the folder, not only the table: restoring an older backup
    replaces the table too, and the safety copy taken just before it must
    still be listed afterwards."""
    whose = _Ownership(db)
    rows = whose.rows
    name = current_company_name(db)
    listed = []
    for path in BACKUP_DIR.iterdir():
        filename = path.name
        if not path.is_file() or _safe_backup_filename(filename) is None:
            continue
        parsed = parse_backup_name(filename)
        owner = parsed["slug"] if parsed else None
        if owner not in (None, LEGACY_SLUG):
            # named for a company: this one's when only this company has (or
            # had) that name, else when the install's record, this
            # company's backups table or, on the desktop, the file says so
            mine = whose.is_mine(filename, owner)
        elif filename in rows:
            mine = True
        else:
            mine = filename.endswith(".db") and _same_name(
                read_backup_facts(filename).get("company_name"), name
            )
        if not mine:
            continue
        row = rows.get(filename)
        stat = path.stat()
        pre_restore = bool(parsed and (parsed["tag"] or "").startswith(PRE_RESTORE_TAG))
        if row is not None and row.created_at is not None:
            stamped = row.created_at
            if stamped.tzinfo is None:  # SQLite's CURRENT_TIMESTAMP is UTC
                stamped = stamped.replace(tzinfo=timezone.utc)
            created = stamped.isoformat()
        else:
            created = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        listed.append(
            {
                "id": row.id if row is not None else None,
                # a backup made before encryption was turned on is plain
                # SQLite (2.24.0 gate, NEW-158 / W-25); the page says which
                "encrypted": is_encrypted_backup(path),
                "filename": filename,
                "file_size": stat.st_size,
                "backup_type": (
                    row.backup_type
                    if row is not None
                    else ("pre-restore" if pre_restore else "manual")
                ),
                "notes": (
                    row.notes
                    if row is not None
                    else (
                        "Taken automatically just before a restore"
                        if pre_restore
                        else None
                    )
                ),
                "created_at": created,
                "_sort": (parsed["stamp"] if parsed else "")
                or datetime.fromtimestamp(stat.st_mtime).strftime("%Y%m%d_%H%M%S"),
            }
        )
    listed.sort(key=lambda b: (b["_sort"], b["filename"]), reverse=True)
    for b in listed:
        del b["_sort"]
    return listed


def other_company(db: Session, filename: str) -> dict | None:
    """None when the backup is this company's (or nothing says otherwise);
    else {"backup_company", "current_company"} naming both. A backup named
    for another company, or carrying another company name in its settings,
    belongs to that company."""
    current = current_company_name(db)
    parsed = parse_backup_name(filename)
    stored = read_backup_facts(filename).get("company_name")
    if stored and not _same_name(stored, current):
        return {"backup_company": stored, "current_company": current}
    if parsed and parsed["slug"] != LEGACY_SLUG:
        if not _Ownership(db).is_mine(filename, parsed["slug"]):
            return {
                "backup_company": stored or parsed["slug"],
                "current_company": current,
            }
    return None


def _migration_script():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    return cfg, ScriptDirectory.from_config(cfg)


def backup_revision_problem(filename: str) -> str | None:
    """A sentence when this build cannot open the backup's books: a copy
    made by a newer SlowBooks Pro, whose schema this one does not know.
    Restoring it would leave a company file the app refuses to open."""
    revision = read_backup_facts(filename).get("revision")
    if not revision:
        return None
    try:
        _cfg, script = _migration_script()
        known = {rev.revision for rev in script.walk_revisions()}
    except Exception:
        logger.exception("Could not read the migration history")
        return None
    if revision in known:
        return None
    return (
        "This backup was made by a newer version of SlowBooks Pro than this "
        "one, which cannot open it. Update SlowBooks Pro, then restore it."
    )


def _postgres_dump_revisions(path: Path) -> list[str] | None:
    """The alembic_version rows a pg_dump custom-format backup holds, read
    with ``pg_restore --data-only --table=alembic_version -f -``: the dump
    is read as a script on stdout and no database is touched. None when it
    cannot be read here (no pg_restore, not a dump)."""
    try:
        result = subprocess.run(
            [
                "pg_restore",
                "--data-only",
                "--table=alembic_version",
                "-f",
                "-",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    revisions, copying = [], False
    for line in result.stdout.splitlines():
        if copying:
            if line == "\\.":
                copying = False
            elif line.strip():
                revisions.append(line.split("\t", 1)[0].strip())
        elif line.startswith("COPY ") and "alembic_version" in line:
            copying = True
        elif line.startswith("INSERT INTO ") and "alembic_version" in line:
            found = re.search(r"VALUES \('([^']+)'\)", line)
            if found:
                revisions.append(found.group(1))
    return revisions


def backup_floor_problem(filename: str, recovery_key: str | None = None) -> str | None:
    """The sentence when a backup holds books from before SlowBooks Pro
    2.29.0, which this build does not migrate (#769); asked before anything
    is written. A SQLite copy is read as it lies (with this company's key,
    or the recovery key given, when it is encrypted); a Postgres dump is
    read by pg_restore without touching a database. None for every other
    backup, and for one that cannot be read here."""
    from app.services import migration_floor

    path = backup_path(filename)
    if path is None or not path.exists():
        return None
    if path.suffix == ".db":
        key = None
        if company_file.is_encrypted_file(path):
            key = company_file.resolve_key()
            if key is not None:
                try:
                    company_file.verify_key(path, key)
                except company_file.CompanyFileLocked:
                    key = None
            if key is None and (recovery_key or "").strip():
                from app.services import company_keyring

                try:
                    key = company_keyring.unlock_with_recovery(path, recovery_key)
                except company_file.CompanyFileLocked:
                    key = None
            if key is None:
                return None
        return migration_floor.file_problem(path, key)
    revisions = _postgres_dump_revisions(path)
    if revisions is None:
        return None
    if migration_floor.is_below_floor(revisions):
        return migration_floor.BELOW_FLOOR_MESSAGE
    return None


def bring_restored_books_up_to_date() -> dict:
    """After a SQLite restore: an older backup is brought to this build's
    schema, the way the desktop app does when it opens a company, so the
    running app can use it straight away. {"success": bool, "error"}."""
    if not _is_sqlite():
        return {"success": True}
    path = _sqlite_db_path()
    if path is None:
        return {"success": True}
    key = company_file.resolve_key() if company_file.is_encrypted_file(path) else None
    try:
        with closing(company_file.connect_raw(path, key)) as conn:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        current = row[0] if row else None
    except company_file.errors():
        current = None  # no migration history: not a file alembic manages
    if not current:
        return {"success": True}
    try:
        cfg, script = _migration_script()
        if current == script.get_current_head():
            return {"success": True}
        from alembic import command

        cfg.attributes["database_url"] = "sqlite:///" + path.as_posix()
        cfg.attributes["company_file_key"] = key
        command.upgrade(cfg, "head")
    except Exception as exc:
        from app.services.migration_floor import CompanyFileRefused

        # from before 2.29.0 (#769), or a newer build's (W-186)
        if isinstance(exc, CompanyFileRefused):
            return {"success": False, "error": str(exc)}
        logger.exception("Could not upgrade the restored books")
        return {
            "success": False,
            "error": "The restored books could not be brought up to this version.",
        }
    import app.database as db_module

    db_module.current_engine().dispose()
    return {"success": True}
