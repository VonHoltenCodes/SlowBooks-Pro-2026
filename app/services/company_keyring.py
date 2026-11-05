"""The desktop's own key for an encrypted company file (#290): a recovery
key the key is derived from, wrapped for each administrator's password.

Server Edition sets COMPANY_FILE_ENCRYPTION_SECRET where it starts. A
desktop has nobody at an environment variable, so its key is managed
here:

- The **recovery key** is 160 random bits shown once as eight groups of
  four letters and digits (``ABCD-EFGH-…``), and downloadable as a text
  file. The company file's key is derived from it (PBKDF2-HMAC-SHA256,
  480,000 rounds, under this module's own salt), so the recovery key alone
  opens the file, and every backup of it, on any machine: nothing else has
  to survive a lost laptop.
- The **keyring** is a small JSON file beside the company file
  (``<company>.db.keyring``) holding the key wrapped under each
  administrator's password (PBKDF2 under a salt of its own, then Fernet).
  It holds nothing secret: a wrapping opens only with the password it was
  made with. An administrator's sign-in at the picker unwraps the key for
  the process; the server child takes it from its environment, the way
  the launcher already hands a key on. Other users never see the key.
- An administrator who has no wrapping yet (added after encryption was
  turned on, or whose password changed) gets one at their next sign-in,
  from the running process, which has the key. Removing an
  administrator, or resetting their password, drops their wrapping. A
  forgotten password set again after the recovery key opened the file
  (#719, auth's reset-at-computer) is wrapped at once, for the new one.
- A **new recovery key** retires the old one: the file is re-keyed in
  place under the key the new one derives, the administrator who asked
  is wrapped for it at once, and every other administrator at their next
  sign-in (their wrapping names the key it holds, so a stale one is told
  from a current one).

Nothing here decides policy: who may turn encryption on or make a new
recovery key is the route's business (app/routes/company_file.py).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.services import company_file

logger = logging.getLogger(__name__)

KEYRING_SUFFIX = ".keyring"
VERSION = 1
_RECOVERY_SALT = b"slowbooks-company-recovery-v1"
_ROUNDS = 480_000
_RECOVERY_BYTES = 20  # 160 bits: 32 base32 characters, eight groups of four
_GROUP = 4

LOCKED_MESSAGE = (
    "This company file is encrypted. Unlock it with an administrator's "
    "sign-in, or with its recovery key."
)
STALE_WRAPPING_MESSAGE = (
    "This sign-in's key is out of date (a new recovery key was made since). "
    "Unlock with the recovery key, or have another administrator open the "
    "books first; your sign-in is brought up to date then."
)
NOT_AN_ADMIN_MESSAGE = (
    "Only an administrator's sign-in, or the recovery key, unlocks the " "company file."
)
WRONG_PASSWORD_MESSAGE = "Incorrect username or password."
WRONG_RECOVERY_MESSAGE = "That recovery key does not open this company file."
HEADLESS_MESSAGE = (
    "This company file is encrypted and unlocks from an administrator's "
    "sign-in at the SlowBooks Pro window. To run it without a window, set "
    "COMPANY_FILE_ENCRYPTION_SECRET where it starts instead, and encrypt "
    "the file under that secret."
)


# ---------------------------------------------------------------------------
# The recovery key
# ---------------------------------------------------------------------------


def new_recovery_key() -> str:
    raw = base64.b32encode(secrets.token_bytes(_RECOVERY_BYTES)).decode("ascii")
    return "-".join(raw[i : i + _GROUP] for i in range(0, len(raw), _GROUP))


# Base32 has no 0, 1 or 8: a key typed with them meant O, I and B
# (2.24.0 gate, NEW-160).
_LOOKALIKES = str.maketrans({"0": "O", "1": "I", "8": "B"})


def normalize_recovery_key(text: str) -> str:
    """The key as typed: case, spaces and dashes do not matter, and 0, 1
    and 8 read as the letters they look like."""
    return re.sub(r"[^A-Z2-7]", "", (text or "").upper().translate(_LOOKALIKES))


def is_recovery_key(text: str) -> bool:
    return len(normalize_recovery_key(text)) == _RECOVERY_BYTES * 8 // 5


def key_from_recovery(text: str) -> bytes:
    """The company file's key for a recovery key. Deterministic: the same
    recovery key opens the same file, and every backup of it, anywhere."""
    normalized = normalize_recovery_key(text)
    if not is_recovery_key(text):
        raise ValueError("not a recovery key")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=company_file.KEY_BYTES,
        salt=_RECOVERY_SALT,
        iterations=_ROUNDS,
    )
    return kdf.derive(normalized.encode("ascii"))


def recovery_file_text(company_name: str, recovery_key: str) -> str:
    """The text file the person downloads, with the words that matter."""
    return (
        f"SlowBooks Pro recovery key for {company_name or 'this company'}\n"
        f"\n"
        f"    {recovery_key}\n"
        f"\n"
        f"This key opens the encrypted company file, and every backup of it, if\n"
        f"every administrator's password is lost, and then lets you set a new\n"
        f"administrator password. Keep it where the books' backups are not: a\n"
        f"safe, a password manager, a sealed envelope.\n"
        f"Anyone holding it can read the books. SlowBooks Pro does not keep a\n"
        f"copy; an administrator can make a new one under Settings, which\n"
        f"retires this one.\n"
        f"\n"
        f"Made {datetime.now():%B %d, %Y}.\n"
    )


# ---------------------------------------------------------------------------
# Wrapping under a password
# ---------------------------------------------------------------------------


def _fernet_for(password: str, salt: bytes) -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=_ROUNDS
    )
    return Fernet(base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8"))))


def wrap(key: bytes, password: str) -> dict:
    salt = secrets.token_bytes(16)
    token = _fernet_for(password, salt).encrypt(key)
    return {
        "salt": base64.b64encode(salt).decode("ascii"),
        "wrapped": token.decode("ascii"),
    }


def unwrap(entry: dict, password: str) -> bytes | None:
    """The key, or None when the password is not the one it was wrapped
    with (or the entry is damaged)."""
    try:
        salt = base64.b64decode(entry["salt"])
        return _fernet_for(password, salt).decrypt(entry["wrapped"].encode("ascii"))
    except (InvalidToken, KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# The keyring file
# ---------------------------------------------------------------------------


def keyring_path(db_path: Path | str) -> Path:
    db_path = Path(db_path)
    return db_path.with_name(db_path.name + KEYRING_SUFFIX)


def active_keyring_path() -> Path | None:
    path = company_file.active_path()
    return keyring_path(path) if path is not None else None


def exists(db_path: Path | str | None) -> bool:
    return db_path is not None and keyring_path(db_path).is_file()


def load(db_path: Path | str) -> dict | None:
    path = keyring_path(db_path)
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("admins"), dict):
        return None
    return data


def save(db_path: Path | str, ring: dict) -> None:
    path = keyring_path(db_path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(ring, indent=2, sort_keys=True), "utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:  # Windows, or a filesystem without modes
        pass
    os.replace(tmp, path)


def remove(db_path: Path | str) -> None:
    keyring_path(db_path).unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_ring(key: bytes, *, company_name: str = "") -> dict:
    return {
        "version": VERSION,
        "company_name": company_name,
        "key_id": uuid.uuid4().hex,
        "recovery_made_at": _now(),
        "admins": {},
    }


def set_admin(ring: dict, username: str, password: str, key: bytes) -> dict:
    """Wrap the key for an administrator (replacing an older wrapping)."""
    entry = wrap(key, password)
    entry["key_id"] = ring["key_id"]
    entry["made_at"] = _now()
    ring["admins"][username.strip().lower()] = entry
    return ring


def drop_admin(ring: dict, username: str) -> bool:
    return ring["admins"].pop(username.strip().lower(), None) is not None


def admins(ring: dict | None) -> list[str]:
    return sorted((ring or {}).get("admins", {}).keys())


def wrapping_current(ring: dict, username: str) -> bool:
    entry = ring["admins"].get(username.strip().lower())
    return bool(entry) and entry.get("key_id") == ring.get("key_id")


# ---------------------------------------------------------------------------
# Unlocking
# ---------------------------------------------------------------------------


class Locked(company_file.CompanyFileLocked):
    """The keyring refused: ``str(exc)`` is the one sentence to show."""


def unlock_with_password(db_path: Path | str, username: str, password: str) -> bytes:
    """The key for an administrator's sign-in, proven against the file."""
    ring = load(db_path)
    if ring is None:
        raise Locked(LOCKED_MESSAGE)
    entry = ring["admins"].get((username or "").strip().lower())
    if entry is None:
        # the same sentence for a name that is not an administrator's and a
        # wrong password: the picker is not an oracle for who is who
        raise Locked(WRONG_PASSWORD_MESSAGE)
    key = unwrap(entry, password or "")
    if key is None:
        raise Locked(WRONG_PASSWORD_MESSAGE)
    if entry.get("key_id") != ring.get("key_id"):
        raise Locked(STALE_WRAPPING_MESSAGE)
    try:
        company_file.verify_key(db_path, key)
    except company_file.CompanyFileLocked as exc:
        raise Locked(STALE_WRAPPING_MESSAGE) from exc
    return key


def unlock_with_recovery(db_path: Path | str, recovery_key: str) -> bytes:
    """The key a recovery key derives, proven against the file."""
    if not is_recovery_key(recovery_key):
        raise Locked(WRONG_RECOVERY_MESSAGE)
    key = key_from_recovery(recovery_key)
    try:
        company_file.verify_key(db_path, key)
    except company_file.CompanyFileLocked as exc:
        raise Locked(WRONG_RECOVERY_MESSAGE) from exc
    return key


def unlock(
    db_path: Path | str,
    *,
    username: str = "",
    password: str = "",
    recovery_key: str = "",
) -> bytes:
    """Either way in: a recovery key when one is given, else a sign-in."""
    if (recovery_key or "").strip():
        return unlock_with_recovery(db_path, recovery_key)
    return unlock_with_password(db_path, username, password)


# ---------------------------------------------------------------------------
# Keeping the wrappings current, from the running (unlocked) process
# ---------------------------------------------------------------------------


def ensure_wrapped(
    db_path: Path | str, username: str, password: str, *, force: bool = False
) -> bool:
    """An administrator who signed in (the password just proven against
    the books): wrapped for the current key if not yet, or if their
    wrapping is stale; ``force`` re-wraps regardless (the password just
    changed). True when the keyring changed."""
    ring = load(db_path)
    if ring is None:
        return False
    if not force and wrapping_current(ring, username):
        return False
    key = company_file.resolve_key()
    if key is None:
        return False
    set_admin(ring, username, password, key)
    save(db_path, ring)
    logger.info("company file key wrapped for %s", username)
    return True


def forget(db_path: Path | str, username: str) -> bool:
    """An administrator removed, demoted or reset: their wrapping goes."""
    ring = load(db_path)
    if ring is None or not drop_admin(ring, username):
        return False
    save(db_path, ring)
    logger.info("company file key wrapping dropped for %s", username)
    return True


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def status(db_path: Path | str | None) -> dict | None:
    """What Settings shows: who can unlock, and when the recovery key was
    made; None when the file has no keyring."""
    if db_path is None:
        return None
    ring = load(db_path)
    if ring is None:
        return None
    return {
        "admins": admins(ring),
        "stale": sorted(
            name
            for name, entry in ring["admins"].items()
            if entry.get("key_id") != ring.get("key_id")
        ),
        "recovery_made_at": ring.get("recovery_made_at"),
    }
