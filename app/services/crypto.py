# ============================================================================
# Slowbooks Pro 2026 — Secrets encryption helpers
#
# Not "decompiled" from QB2003 — QuickBooks 2003 "encrypted" its secrets
# with a single-byte XOR 0x1F cipher (CFileHeader::Obfuscate @ 0x00241520).
# That was a joke in 2003 and it's a felony in 2026. This is the modern
# replacement: Fernet symmetric authenticated encryption.
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
#   1. SETTINGS_ENCRYPTION_KEY environment variable  (ops-preferred)
#   2. .slowbooks-master.key file next to the repo    (zero-config default)
#   3. Generate a new key, write it to (2) with 0600 perms, log a warning
#
# The master key is NEVER stored in the database — if it lived in the
# same place as the ciphertext, the whole exercise would be performative.
# ============================================================================

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

# Prefix that marks a value as ciphertext. Older plaintext settings rows
# don't carry this, so `decrypt_value()` can fall back gracefully while
# we migrate existing data.
CIPHERTEXT_PREFIX = "fernet:v1:"

# On-disk location of the master key when the env var isn't set.
_KEY_FILE = BASE_DIR / ".slowbooks-master.key"

_cached_fernet: Optional[Fernet] = None


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
    _KEY_FILE.write_bytes(key)
    try:
        _KEY_FILE.chmod(0o600)
    except OSError:
        # Not fatal on filesystems that don't support chmod (e.g. some
        # Docker-mounted volumes), but warn loudly so ops knows.
        logger.warning("Could not set 0600 perms on %s — check volume permissions", _KEY_FILE)
    logger.warning(
        "Generated new settings encryption key at %s. "
        "Back this file up — losing it means losing access to every "
        "encrypted settings value.",
        _KEY_FILE,
    )
    return key


def _fernet() -> Fernet:
    """Return a cached Fernet instance, instantiating it on first use."""
    global _cached_fernet
    if _cached_fernet is None:
        _cached_fernet = Fernet(_load_or_create_master_key())
    return _cached_fernet


def reset_cache_for_tests():
    """Clear the cached Fernet — only used by tests that override env vars."""
    global _cached_fernet
    _cached_fernet = None


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
    token = stored[len(CIPHERTEXT_PREFIX):].encode("ascii")
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
