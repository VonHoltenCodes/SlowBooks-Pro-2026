# ============================================================================
# Served companies — which company files one Server Edition server opens
# (#358).
#
# A bookkeeper who keeps two companies' books used to need two servers, on
# two ports with two sets of sign-ins, or the host PC repointed for everyone
# at once. Now a server started with --serve-lan serves every company in
# the install's company list (companies.json): the sign-in screen asks which
# one, a session works in the company it chose, and two people work in two
# companies at once. Each company stays one file with its own users, roles,
# audit log, backups and bank feeds; nothing about a company is kept
# outside it.
#
# SQLite company files on a Server Edition host only. The desktop window
# still opens one company and switches by relaunching (the picker), and a
# PostgreSQL server still serves the one database DATABASE_URL names.
#
# app/database.py does the routing; this module says which names it may be
# given and opens each company's engine the first time.
# ============================================================================

import logging
import os
import threading

from app.config import DATABASE_URL

logger = logging.getLogger(__name__)

# The session keys: the company file a session works in, and the files its
# sign-in's password was found to open, so a switch to one takes a click.
SESSION_FILE_KEY = "company_file"
SESSION_OPENS_KEY = "company_files"
# what the session remembers of each other company's sign-in, so a switch
# can tell the password was changed there since (2.25.0 review)
SESSION_OPENS_FP_KEY = "company_files_fp"

ENCRYPTED_ON_ITS_OWN_KEY = (
    "This company file is encrypted under a key of its own, which only opens "
    "at the SlowBooks Pro window. To serve it with the others, set "
    "COMPANY_FILE_ENCRYPTION_SECRET on the host and encrypt the file under "
    "that secret."
)
NOT_SERVED = "That company is not one this server serves."
MISSING = "That company's file is missing from the company folder."
OTHER_VERSION = (
    "That company's file is from another version of SlowBooks Pro: restart "
    "the server on the host so it can bring the file up to date."
)


class CompanyUnavailable(Exception):
    """A company file the server cannot open now. ``exc.sentence`` is the one
    sentence to show, always one of this server's own (``sentence_of``)."""

    def __init__(self, sentence: str):
        super().__init__(sentence)
        self.sentence = sentence


def sentence_of(exc: CompanyUnavailable) -> str:
    """The sentence a response carries for the company it cannot open: one
    of the server's own, looked up, never the exception's text itself (a
    code scanner reads ``str(exc)`` on the way to a response as a possible
    traceback; here it is never one, and this keeps it so)."""
    from app.services import company_file

    known = (
        NOT_SERVED,
        MISSING,
        OTHER_VERSION,
        ENCRYPTED_ON_ITS_OWN_KEY,
        company_file.UNAVAILABLE_MESSAGE,
        company_file.NO_KEY_MESSAGE,
        company_file.WRONG_KEY_MESSAGE,
    )
    given = getattr(exc, "sentence", "")
    for sentence in known:
        if sentence == given:
            return sentence
    return NOT_SERVED


def enabled() -> bool:
    """True on a Server Edition host serving SQLite company files, with more
    than one company in its list."""
    if os.environ.get("SLOWBOOKS_SERVER_MODE") != "1":
        return False
    if not DATABASE_URL.startswith("sqlite:///"):
        return False
    return len(listed()) > 1


# The list is read on every request that names a company; the manifest is
# reread only when the file changes.
_manifest_cache: dict = {"stamp": None, "rows": []}
_manifest_lock = threading.Lock()


def listed() -> list[dict]:
    """The companies this host keeps, in the manifest's order:
    [{"file", "name"}], each file name already checked safe."""
    from app.services import company_service

    path = company_service.manifest_path()
    try:
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return []
    gone: set = set()
    with _manifest_lock:
        if _manifest_cache["stamp"] == stamp:
            return list(_manifest_cache["rows"])
        rows = []
        for entry in company_service._read_manifest()["companies"]:
            safe = company_service.safe_company_filename(entry.get("file") or "")
            if safe is None or any(r["file"] == safe for r in rows):
                continue
            rows.append({"file": safe, "name": (entry.get("name") or "").strip()})
        gone = {r["file"] for r in _manifest_cache["rows"]} - {r["file"] for r in rows}
        _manifest_cache.update(stamp=stamp, rows=rows)
    if gone:
        # a company taken off the list gives its engine up at once, rather
        # than at the next restart (2.25.0 review)
        from app import database

        database.drop_company_engines(gone)
    return list(rows)


def is_served(filename: str | None) -> bool:
    return bool(filename) and any(r["file"] == filename for r in listed())


def started_file() -> str | None:
    from app.database import started_company_file

    return started_company_file()


def chosen_file(session) -> str | None:
    """The company file a session works in, when it is another company this
    server serves; None for the company the server was started for (and
    for anything else a cookie might carry)."""
    if not isinstance(session, dict):
        return None
    filename = session.get(SESSION_FILE_KEY)
    if not filename or filename == started_file():
        return None
    if not enabled() or not is_served(filename):
        return None
    return filename


def current_file() -> str | None:
    """The company file this request works in."""
    from app.database import served_company

    return served_company() or started_file()


def open_engine(filename: str):
    """An engine for another company this server serves, after the checks
    the server's own start makes of the company it was started for: the
    file is there, its key opens it, its schema is this build's, and the
    tables the migrations do not make are made. Raises CompanyUnavailable."""
    from app.services import company_file, company_service

    if not is_served(filename):
        raise CompanyUnavailable(NOT_SERVED)
    path = company_service.company_db_path(filename)
    if path is None or not path.exists():
        raise CompanyUnavailable(MISSING)
    company_file.sweep_stale_conversions(path)
    if company_file.is_encrypted_file(path):
        from app.services import company_keyring

        if not company_file.available():
            raise CompanyUnavailable(company_file.UNAVAILABLE_MESSAGE)
        key = company_file.install_key()
        if key is None:
            raise CompanyUnavailable(
                ENCRYPTED_ON_ITS_OWN_KEY
                if company_keyring.exists(path)
                else company_file.NO_KEY_MESSAGE
            )
        try:
            company_file.verify_key(path, key)
        except company_file.CompanyFileLocked as exc:
            # one of the module's own sentences, never the exception's text
            given = str(exc)
            sentence = next(
                (
                    s
                    for s in (
                        company_file.NO_KEY_MESSAGE,
                        company_file.WRONG_KEY_MESSAGE,
                        company_file.UNAVAILABLE_MESSAGE,
                    )
                    if s == given
                ),
                company_file.WRONG_KEY_MESSAGE,
            )
            raise CompanyUnavailable(sentence) from exc

    from app import database

    url = "sqlite:///" + path.as_posix()
    engine = company_file.create_sqlite_engine(
        url, company_file.install_key, pool_pre_ping=True
    )
    database.enable_sqlite_tuning(engine)
    try:
        from app.main import _create_missing_tables, _refuse_a_database_behind_head

        _refuse_a_database_behind_head(engine)
        _create_missing_tables(engine)
    except RuntimeError as exc:
        engine.dispose()
        logger.error("not serving %s: %s", filename, exc)
        from app.services.migration_floor import BelowMigrationFloor

        if isinstance(exc, BelowMigrationFloor):
            raise CompanyUnavailable(str(exc)) from exc  # from before 2.29.0
        raise CompanyUnavailable(OTHER_VERSION) from exc
    return engine


def names_for_sign_in() -> list[dict]:
    """The companies the sign-in screen offers: [{"file", "name",
    "current"}]. Names only, the way the screen lists the usernames, and on
    a Server Edition host only (documented for trusted networks)."""
    current = current_file()
    return [
        {
            "file": r["file"],
            "name": r["name"] or r["file"].removesuffix(".db"),
            "current": r["file"] == current,
        }
        for r in listed()
    ]
