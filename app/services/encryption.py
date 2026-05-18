# ============================================================================
# Field-level encryption for payroll PII (employee bank routing / account #s).
# Fernet (AES-128-CBC + HMAC) with a key derived from PAYROLL_ENCRYPTION_SECRET.
# ============================================================================

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.config import PAYROLL_ENCRYPTION_SECRET

# Static salt — fine here because the secret itself is the protected material;
# rotating the secret rotates the derived key.
_SALT = b"slowbooks-payroll-v1"


def _build_fernet() -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_SALT,
        iterations=480000,
    )
    key = base64.urlsafe_b64encode(
        kdf.derive(PAYROLL_ENCRYPTION_SECRET.encode("utf-8"))
    )
    return Fernet(key)


_fernet = _build_fernet()


def encrypt(plaintext: str | None) -> str | None:
    """Encrypt a string for storage. Returns None for empty input."""
    if plaintext is None or plaintext == "":
        return None
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str | None) -> str | None:
    """Decrypt a stored token. Returns None for empty or undecryptable input."""
    if not token:
        return None
    try:
        return _fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
