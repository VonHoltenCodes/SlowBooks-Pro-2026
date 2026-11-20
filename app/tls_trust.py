# ============================================================================
# Where TLS finds the certificate authorities, for the frozen macOS build
# (NEW-265).
#
# Every outbound HTTPS call that builds its context with
# ssl.create_default_context() (the relay's Switchboard client, the currency
# rate) asks OpenSSL for its default CA file and folder. In the signed macOS
# bundle those are compiled into Frameworks/libcrypto as the CI builder's
# python.org folder (/Library/Frameworks/Python.framework/.../etc/openssl),
# which a user's Mac does not have: every such call failed with
# CERTIFICATE_VERIFY_FAILED. httpx's callers were fine because httpx loads
# certifi's file itself.
#
# use_bundled_ca_if_needed() points OpenSSL's default at the certifi file
# the bundle carries (SSL_CERT_FILE, which OpenSSL reads on every
# create_default_context), at start-up before any thread connects, and only
# when: the build is frozen, on macOS, the person set neither SSL_CERT_FILE
# nor SSL_CERT_DIR, and the compiled-in defaults do not exist. Verification
# is never turned off. The frozen Windows build reads the Windows store
# (Python's ssl loads it into every default context) and the Linux one the
# system's folders, so neither is touched. The variable is inherited by the
# server child the launcher starts.
# ============================================================================

import os
import ssl
import sys
from pathlib import Path


def _default_paths_exist(paths) -> bool:
    cafile, capath = paths.cafile, paths.capath
    if cafile and Path(cafile).is_file():
        return True
    if capath and Path(capath).is_dir():
        try:
            return any(Path(capath).iterdir())
        except OSError:
            return False
    return False


def _bundled_ca_file() -> str | None:
    try:
        import certifi
    except ImportError:
        return None
    where = certifi.where()
    return where if where and Path(where).is_file() else None


def use_bundled_ca_if_needed(
    *,
    frozen: bool | None = None,
    platform: str | None = None,
    environ=None,
    default_paths=None,
    bundled: str | None = None,
) -> str | None:
    """Set SSL_CERT_FILE to the bundled certifi file when the frozen macOS
    build's compiled-in CA paths are missing; returns the file set, or None
    when nothing was changed. The keywords are for the tests."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    if not frozen or platform != "darwin":
        return None
    if environ.get("SSL_CERT_FILE") or environ.get("SSL_CERT_DIR"):
        return None  # the person's own choice stands
    paths = ssl.get_default_verify_paths() if default_paths is None else default_paths
    if _default_paths_exist(paths):
        return None
    bundled = _bundled_ca_file() if bundled is None else bundled
    if not bundled:
        return None
    environ["SSL_CERT_FILE"] = bundled
    return bundled


def default_context_has_cas() -> bool:
    """Whether a default context has certificate authorities to verify
    with (the smoke test's check, and the tests'). A bundle file is loaded
    when the context is made; a folder of hashed certificates (a Linux
    whose Python has no bundle file of its own) is read only as a
    handshake asks for one, so nothing shows as loaded until then and the
    folder itself is the evidence."""
    if ssl.create_default_context().get_ca_certs():
        return True
    return _default_paths_exist(ssl.get_default_verify_paths())
