# Vendored from SlowBooks Switchboard: sbplive/store.py
# (see app/switchboard/SOURCE.md). Do not edit here: change the source and run scripts/sync_switchboard.py.
# ============================================================================
# Where SlowBooks Switchboard keeps its own things, for this user only: the
# API token, the app's address, the remote door's connector secret and its
# OAuth records, and the log. One folder in the user's own configuration
# directory:
#
#   Windows  %APPDATA%\sbplive
#   macOS    ~/Library/Application Support/sbplive
#   Linux    $XDG_CONFIG_HOME/sbplive, else ~/.config/...
#
# The token file is mode 600 and the folder 700 on macOS and Linux. On
# Windows there are no POSIX modes: the folder sits in the user's own
# profile, whose access list already lets only that user (and
# administrators) read it, and that protection is what the token relies on.
#
# Several companies (add-books): each company's token is its own file,
# books/<handle>.token (mode 600, the folder 700), and settings.json lists
# the companies under "books" (handle, file, name and the token's SHA-256,
# never the token). The single `token` file is setup's one company, as it
# always was; the first add-books moves it into books/, and from then on the
# list is what counts. The two are never used together.
#
# The platform is read through platform() and the folders from the
# environment each time, so the tests can lay out any platform's home.
# ============================================================================

import contextlib
import json
import os
import shutil
import sys
from pathlib import Path

TOKEN_NAME = "token"
SECRET_NAME = "connector-secret"
SETTINGS_NAME = "settings.json"
LOG_NAME = "sbplive.log"
BOOKS_NAME = "books"
BOOKS_LOCK_NAME = "books.lock"


def platform() -> str:
    """sys.platform, read through one function the tests can replace."""
    return sys.platform


def _home() -> Path:
    # HOME first, also on Windows when set (the tests set it); then the
    # platform's own idea of the user's home.
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    return Path(home) if home else Path.home()


def config_dir() -> Path:
    """This user's configuration directory for SlowBooks Switchboard."""
    if platform() == "win32":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else _home() / "AppData" / "Roaming"
        return root / "sbplive"
    if platform() == "darwin":
        return _home() / "Library" / "Application Support" / "sbplive"
    xdg = os.environ.get("XDG_CONFIG_HOME") or ""
    # The XDG specification ignores a relative value, and so does this.
    root = Path(xdg) if xdg and os.path.isabs(xdg) else _home() / ".config"
    return root / "sbplive"


def token_path() -> Path:
    return config_dir() / TOKEN_NAME


def secret_path() -> Path:
    """The remote door's connector secret (remote.py): not the API token."""
    return config_dir() / SECRET_NAME


def settings_path() -> Path:
    return config_dir() / SETTINGS_NAME


def log_path() -> Path:
    return config_dir() / LOG_NAME


def books_dir(folder: Path | None = None) -> Path:
    """The folder of the companies' tokens (one file per handle)."""
    return (folder or config_dir()) / BOOKS_NAME


def book_token_path(handle: str, folder: Path | None = None) -> Path:
    return books_dir(folder) / f"{handle}.token"


def ensure_dir() -> Path:
    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(folder, 0o700)
    return folder


def _ensure_parent(path: Path) -> None:
    ensure_dir()
    if path.parent != config_dir():
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(path.parent, 0o700)


def _write_private(path: Path, text: str) -> None:
    """Write a file only this user can read, replacing any earlier one whole.

    The file is created with mode 600 from the start (never written first
    and narrowed after), in the same folder, then moved over the old one."""
    _ensure_parent(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    if os.name != "nt":
        os.chmod(path, 0o600)


def write_private(path: Path, text: str) -> None:
    """Write one of this program's files, mode 600 (see _write_private)."""
    _write_private(path, text)


def save(url: str, token: str) -> Path:
    """Store the address and the token; returns the token file's path."""
    _write_private(token_path(), token.strip() + "\n")
    _write_private(settings_path(), json.dumps({"url": url}, indent=2) + "\n")
    return token_path()


def save_book_token(handle: str, token: str) -> Path:
    """Store one company's token as books/<handle>.token, mode 600."""
    path = book_token_path(handle)
    _write_private(path, token.strip() + "\n")
    return path


def save_settings(settings: dict) -> Path:
    """Replace settings.json whole with settings (the address and, with
    several companies, the list of them)."""
    _write_private(settings_path(), json.dumps(settings, indent=2) + "\n")
    return settings_path()


def remove_book_token(handle: str) -> bool:
    try:
        book_token_path(handle).unlink()
        return True
    except FileNotFoundError:
        return False


def remove_token() -> bool:
    """Delete setup's single token file (when remove-books takes off the
    company it reaches)."""
    try:
        token_path().unlink()
        return True
    except FileNotFoundError:
        return False


@contextlib.contextmanager
def books_lock():
    """Held while add-books or remove-books changes the list, so two at once
    cannot lose one another's company. The operating system lets go of it
    when the process ends, however it ends."""
    ensure_dir()
    path = config_dir() / BOOKS_LOCK_NAME
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing the file lets go of the lock


def save_secret(secret: str) -> Path:
    """Store the connector secret, mode 600, replacing any earlier one."""
    _write_private(secret_path(), secret.strip() + "\n")
    return secret_path()


def has_secret() -> bool:
    return secret_path().is_file()


def load_settings() -> dict:
    """The stored settings, or {} when there are none or they cannot be read."""
    try:
        with open(settings_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def stored_url() -> str | None:
    url = load_settings().get("url")
    return url if isinstance(url, str) and url.strip() else None


def has_token() -> bool:
    return token_path().is_file()


def forget() -> list:
    """Delete the stored token, the companies' tokens, settings and connector
    secret; returns the paths removed."""
    removed = []
    folder = books_dir()
    if folder.is_dir():
        shutil.rmtree(folder)
        removed.append(folder)
    # oauth.json: the OAuth door's records (oauth_store_file.py); deleting
    # it signs every Claude connection out.
    for path in (
        token_path(),
        settings_path(),
        secret_path(),
        config_dir() / "oauth.json",
    ):
        try:
            path.unlink()
            removed.append(path)
        except FileNotFoundError:
            pass
    with contextlib.suppress(FileNotFoundError):
        (config_dir() / BOOKS_LOCK_NAME).unlink()
    return removed
