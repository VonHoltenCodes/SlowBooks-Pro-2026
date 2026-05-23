# ============================================================================
# Field-level encryption for payroll PII (employee bank routing / account #s).
#
# Ciphertext is versioned with a "v{N}:" prefix so the active key can be
# rotated without a destructive re-encrypt:
#
#   PAYROLL_ENCRYPTION_SECRET       — current key, used to encrypt + try first
#                                     on decrypt
#   PAYROLL_ENCRYPTION_SECRET_PREV  — optional, last rotated-out key, used
#                                     only on decrypt
#
# Rotation procedure:
#   1. Move the live secret into PAYROLL_ENCRYPTION_SECRET_PREV.
#   2. Set the new secret as PAYROLL_ENCRYPTION_SECRET.
#   3. Bounce the app — new writes go out under the new key; old reads
#      transparently fall through to the previous key.
#   4. Run `python -m app.services.encryption rewrap` (offline) to re-encrypt
#      every stored ciphertext under the new key.
#   5. Drop PAYROLL_ENCRYPTION_SECRET_PREV.
# ============================================================================

import base64
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.config import PAYROLL_ENCRYPTION_SECRET

logger = logging.getLogger(__name__)

# Static salt — fine here because the secret itself is the protected material;
# rotating the secret rotates the derived key.
_SALT = b"slowbooks-payroll-v1"

# Version 1: current scheme. Bump if we ever change algorithm or salt.
_CURRENT_VERSION = 1
_VERSION_PREFIX = f"v{_CURRENT_VERSION}:"


def _derive_fernet(secret: str) -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_SALT,
        iterations=480000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret.encode("utf-8")))
    return Fernet(key)


def _active_fernets() -> list[Fernet]:
    """Build the decrypt-key chain: current first, then previous (if set)."""
    keys = [_derive_fernet(PAYROLL_ENCRYPTION_SECRET)]
    prev = os.environ.get("PAYROLL_ENCRYPTION_SECRET_PREV", "").strip()
    if prev and prev != PAYROLL_ENCRYPTION_SECRET:
        keys.append(_derive_fernet(prev))
    return keys


_fernets = _active_fernets()


def encrypt(plaintext: str | None) -> str | None:
    """Encrypt a string for storage. Returns None for empty input."""
    if plaintext is None or plaintext == "":
        return None
    token = _fernets[0].encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _VERSION_PREFIX + token


def decrypt(token: str | None) -> str | None:
    """Decrypt a stored value. Tries the current key first, then any
    rotated-out previous key. Returns None for empty or undecryptable input."""
    if not token:
        return None

    raw = token[len(_VERSION_PREFIX) :] if token.startswith(_VERSION_PREFIX) else token

    for fernet in _fernets:
        try:
            return fernet.decrypt(raw.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            continue

    logger.error("Failed to decrypt a stored secret with any configured key")
    return None


def _is_encrypted_with_current(token: str) -> bool:
    """True if the value decrypts under the CURRENT key. False if it
    decrypts only under the previous key, or doesn't decrypt at all."""
    if not token:
        return True  # nothing to rewrap
    raw = token[len(_VERSION_PREFIX) :] if token.startswith(_VERSION_PREFIX) else token
    try:
        _fernets[0].decrypt(raw.encode("ascii"))
        return True
    except (InvalidToken, ValueError):
        return False


def rewrap_all(db, dry_run: bool = False) -> dict:
    """Re-encrypt every stored ciphertext under the CURRENT key.

    Iterates every row in every model that stores a Fernet ciphertext —
    today that's EmployeeBankAccount.{routing_number_enc, account_number_enc}.
    For each value:
      - if it decrypts under the current key, skip (already rewrapped)
      - if it decrypts under PREV, re-encrypt with current and update
      - if it doesn't decrypt at all, log + count as a failure (don't wipe)

    Use this after rotating the secret. Returns a summary dict:
      {"checked": N, "rewrapped": N, "already_current": N, "failed": N}

    `dry_run=True` runs the same scan and reports what WOULD change
    without committing.
    """
    from app.models.bank_accounts import EmployeeBankAccount

    summary = {"checked": 0, "rewrapped": 0, "already_current": 0, "failed": 0}
    accounts = db.query(EmployeeBankAccount).all()

    for acct in accounts:
        for field in ("routing_number_enc", "account_number_enc"):
            blob = getattr(acct, field)
            if not blob:
                continue
            summary["checked"] += 1
            if _is_encrypted_with_current(blob):
                summary["already_current"] += 1
                continue
            plaintext = decrypt(blob)
            if plaintext is None:
                summary["failed"] += 1
                logger.error(
                    "rewrap: account #%s field %s did not decrypt under any key",
                    acct.id,
                    field,
                )
                continue
            if not dry_run:
                setattr(acct, field, encrypt(plaintext))
            summary["rewrapped"] += 1

    if not dry_run:
        db.commit()
    return summary


def _cli():
    """`python -m app.services.encryption rewrap` — offline key rotation helper.

    Requires the database to be reachable and PAYROLL_ENCRYPTION_SECRET_PREV
    to be set if any rows are currently encrypted under the old key. Exits
    non-zero if any row fails to decrypt under either key.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="python -m app.services.encryption")
    sub = parser.add_subparsers(dest="cmd")
    rewrap = sub.add_parser(
        "rewrap", help="Re-encrypt all stored ciphertext under the current key"
    )
    rewrap.add_argument(
        "--dry-run", action="store_true", help="Show what would change without writing"
    )
    args = parser.parse_args()

    if args.cmd != "rewrap":
        parser.print_help()
        sys.exit(2)

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        result = rewrap_all(db, dry_run=args.dry_run)
    finally:
        db.close()

    print(f"  checked         : {result['checked']}")
    print(f"  already current : {result['already_current']}")
    print(
        f"  rewrapped       : {result['rewrapped']}{' (dry-run, not committed)' if args.dry_run else ''}"
    )
    print(f"  failed          : {result['failed']}")
    sys.exit(1 if result["failed"] > 0 else 0)


if __name__ == "__main__":
    _cli()
