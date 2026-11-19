# ============================================================================
# SlowBooks Pro 2026 — Secrets encryption helpers
#
# 2003-era accounting software famously "encrypted" secrets with trivial
# XOR obfuscation. This is the modern replacement: Fernet symmetric
# authenticated encryption.
#
# Fernet (from the `cryptography` library) gives us:
#   * AES-128 in CBC mode for confidentiality
#   * HMAC-SHA256 for integrity / tamper detection
#   * Version byte + timestamp built into the token
#   * URL-safe base64 encoding so tokens can live in TEXT columns
#
# Why not plaintext? Three reasons:
#   1. Anyone with pg_dump access gets every secret in the system.
#   2. Accidental logging of a Settings row dumps the raw key.
#   3. Backups are usually less-protected than live DBs.
#
# Master key priority order (first hit wins):
#   1. SETTINGS_ENCRYPTION_KEY environment variable  (ops-preferred; the
#      desktop app keeps one in its per-user .env)
#   2. .slowbooks-master.key file next to the repo    (an install that has
#      one keeps using it)
#   3. Derived from PAYROLL_ENCRYPTION_SECRET, when that is a real secret.
#      A Docker container has no lasting place for (2): the file was
#      written inside the container, so recreating the container for an
#      upgrade made a new key, every saved password and API key stopped
#      decrypting, and every page that read the settings failed. The
#      payroll secret is the one value a Docker operator must keep (compose
#      refuses to start without it), and a restore on a new server has it.
#      While PAYROLL_ENCRYPTION_SECRET_PREV is set during a rotation, the
#      key derived from it still decrypts, and the rewrap tool
#      (python -m app.services.encryption rewrap) re-encrypts these too.
#   4. Generate a new key, write it to (2) with 0600 perms, log a warning
#
# The master key is NEVER stored in the database — if it lived in the
# same place as the ciphertext, the whole exercise would be performative.
# ============================================================================

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

# Prefix that marks a value as ciphertext. Older plaintext settings rows
# don't carry this, so `decrypt_value()` can fall back gracefully while
# we migrate existing data.
CIPHERTEXT_PREFIX = "fernet:v1:"

# On-disk location of the master key when the env var isn't set.
_KEY_FILE = BASE_DIR / ".slowbooks-master.key"

# The source tree's placeholder payroll secret protects nothing: a key is
# never derived from it (app/config.py ships it as the default).
_PAYROLL_PLACEHOLDER = "slowbooks-dev-payroll-key-change-me"
# Its own salt, so the derived key is not the one the payroll secret
# derives for payroll fields (app/services/encryption.py).
_DERIVED_KEY_SALT = b"slowbooks-settings-key-v1"

_cached_fernet: Optional[MultiFernet] = None
_cached_primary: Optional[Fernet] = None
_key_source: Optional[str] = None


def _payroll_secret(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    return "" if value == _PAYROLL_PLACEHOLDER else value


def _derived_key(secret: str) -> bytes:
    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_DERIVED_KEY_SALT,
        info=b"slowbooks settings encryption",
    ).derive(secret.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def _master_keys() -> tuple[list[bytes], str]:
    """The keys that decrypt settings (the first one encrypts), and where
    they came from: "env", "file", "derived" or "generated"."""
    env_key = os.getenv("SETTINGS_ENCRYPTION_KEY")
    if env_key:
        return [env_key.encode("utf-8")], "env"

    if _KEY_FILE.exists():
        return [_KEY_FILE.read_bytes().strip()], "file"

    payroll = _payroll_secret("PAYROLL_ENCRYPTION_SECRET")
    if payroll:
        keys = [_derived_key(payroll)]
        previous = _payroll_secret("PAYROLL_ENCRYPTION_SECRET_PREV")
        if previous and previous != payroll:
            keys.append(_derived_key(previous))
        return keys, "derived"

    return [_load_or_create_master_key()], "generated"


def _load_or_create_master_key() -> bytes:
    """Resolve the master encryption key, creating one if needed.

    Priority: env var → on-disk file → fresh generation. Returns the
    raw 32-byte url-safe-base64 key as bytes (what Fernet expects).
    """
    env_key = os.getenv("SETTINGS_ENCRYPTION_KEY")
    if env_key:
        return env_key.encode("utf-8") if isinstance(env_key, str) else env_key

    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes().strip()

    # First-run: generate and persist. This path only runs when neither
    # the env var nor the file exists — so subsequent restarts will
    # pick up the same key from disk and decryption will be stable.
    key = Fernet.generate_key()
    try:
        import tempfile

        fd, tmp = tempfile.mkstemp(dir=str(_KEY_FILE.parent), prefix=".master-key-")
        os.write(fd, key)
        os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, str(_KEY_FILE))
    except OSError:
        try:
            _KEY_FILE.write_bytes(key)
            _KEY_FILE.chmod(0o600)
        except OSError:
            logger.warning(
                "Could not persist key to %s — check volume permissions", _KEY_FILE
            )
    logger.warning(
        "Generated new settings encryption key at %s. "
        "Back this file up — losing it means losing access to every "
        "encrypted settings value.",
        _KEY_FILE,
    )
    return key


def _fernet() -> MultiFernet:
    """Return the cached keys, instantiating them on first use. The first
    key encrypts; any of them decrypts."""
    global _cached_fernet, _cached_primary, _key_source
    if _cached_fernet is None:
        keys, _key_source = _master_keys()
        fernets = [Fernet(k) for k in keys]
        _cached_primary = fernets[0]
        _cached_fernet = MultiFernet(fernets)
    return _cached_fernet


def key_source() -> str:
    """Where the settings key came from: "env", "file", "derived" or
    "generated"."""
    _fernet()
    return _key_source or ""


def reset_cache_for_tests():
    """Clear the cached Fernet — only used by tests that override env vars."""
    global _cached_fernet, _cached_primary, _key_source
    _cached_fernet = None
    _cached_primary = None
    _key_source = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string value for at-rest storage.

    Returns `fernet:v1:<base64-token>`. Empty / None inputs are returned
    unchanged so an empty settings key stays empty (no point encrypting
    the empty string).
    """
    if plaintext is None or plaintext == "":
        return plaintext or ""
    token = _fernet().encrypt(plaintext.encode("utf-8"))
    return CIPHERTEXT_PREFIX + token.decode("ascii")


def decrypt_value(stored: str) -> str:
    """Decrypt a stored value, or return it unchanged if not encrypted.

    Supports the graceful-migration path: any value that doesn't carry
    the `fernet:v1:` prefix is assumed to be legacy plaintext and
    returned verbatim. This lets us ship the crypto layer without
    breaking every existing Settings row.
    """
    if not stored:
        return stored or ""
    if not stored.startswith(CIPHERTEXT_PREFIX):
        return stored
    token = stored[len(CIPHERTEXT_PREFIX) :].encode("ascii")
    try:
        return _fernet().decrypt(token).decode("utf-8")
    except InvalidToken:
        # Either the master key changed or the ciphertext was tampered
        # with. Loud error in logs, empty string to the caller so we
        # never silently fall through with corrupt data.
        logger.error("Failed to decrypt a stored secret — Fernet token is invalid")
        raise


def is_encrypted(stored: str) -> bool:
    """True if the given stored value carries the Fernet prefix."""
    return bool(stored) and stored.startswith(CIPHERTEXT_PREFIX)


def is_readable(stored: str) -> bool:
    """False for ciphertext no key here decrypts (saved under a key this
    install no longer has); True otherwise."""
    if not is_encrypted(stored):
        return True
    try:
        _fernet().decrypt(stored[len(CIPHERTEXT_PREFIX) :].encode("ascii"))
        return True
    except (InvalidToken, ValueError):
        return False


def rewrap_value(stored: str) -> Optional[str]:
    """The value re-encrypted under the current key; the same value when it
    is already current or not encrypted; None when no key decrypts it."""
    if not is_encrypted(stored):
        return stored
    token = stored[len(CIPHERTEXT_PREFIX) :].encode("ascii")
    fernet = _fernet()
    try:
        _cached_primary.decrypt(token)
        return stored
    except (InvalidToken, ValueError):
        pass
    try:
        return CIPHERTEXT_PREFIX + fernet.rotate(token).decode("ascii")
    except (InvalidToken, ValueError):
        return None


def mask_secret(secret: str, show_last: int = 4) -> str:
    """Return a display-safe mask like `••••••••••••abcd`.

    Used anywhere we want to show that a secret exists without revealing
    its content (e.g. the Settings UI that lets an admin confirm which
    API key slot is populated).
    """
    if not secret:
        return ""
    tail = secret[-show_last:] if len(secret) > show_last else ""
    return "•" * max(8, len(secret) - show_last) + tail
