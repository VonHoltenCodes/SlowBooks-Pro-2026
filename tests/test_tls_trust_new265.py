"""NEW-265: the frozen macOS build's outbound HTTPS finds its CAs.

The bundle's libcrypto names the CI builder's python.org folder as OpenSSL's
default CA location, absent on a user's Mac, so every call that builds its
context with ssl.create_default_context() (the relay's Switchboard client,
the currency rate) failed CERTIFICATE_VERIFY_FAILED. At start-up the
launcher now points SSL_CERT_FILE at the certifi file the bundle carries,
only when frozen on macOS, only when the person set nothing and the defaults
are missing. Verification is never loosened.
"""

import ssl
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import certifi
import pytest

from app import tls_trust

ROOT = Path(__file__).resolve().parent.parent
MISSING = SimpleNamespace(
    cafile=None, capath="/Library/Frameworks/Python.framework/nowhere/certs"
)


def _helper(**over):
    env = over.pop("environ", {})
    kw = dict(
        frozen=True,
        platform="darwin",
        environ=env,
        default_paths=MISSING,
        bundled=certifi.where(),
    )
    kw.update(over)
    return tls_trust.use_bundled_ca_if_needed(**kw), env


def test_frozen_mac_with_the_defaults_missing_uses_the_bundled_file():
    done, env = _helper()
    assert done == certifi.where()
    assert env == {"SSL_CERT_FILE": certifi.where()}


@pytest.mark.parametrize(
    "over",
    [
        {"frozen": False},
        {"platform": "win32"},
        {"platform": "linux"},
        {"environ": {"SSL_CERT_FILE": "/my/own.pem"}},
        {"environ": {"SSL_CERT_DIR": "/my/certs"}},
        {"default_paths": SimpleNamespace(cafile=certifi.where(), capath=None)},
        {"bundled": ""},
    ],
)
def test_otherwise_nothing_changes(over):
    before = dict(over.get("environ", {}))
    done, env = _helper(**over)
    assert done is None
    assert env == before  # a value the person set is never overridden


def test_a_default_folder_with_certificates_counts_as_present(tmp_path):
    (tmp_path / "ab12cd34.0").write_text("x")
    done, _ = _helper(default_paths=SimpleNamespace(cafile=None, capath=str(tmp_path)))
    assert done is None
    empty = tmp_path / "empty"
    empty.mkdir()
    done, _ = _helper(default_paths=SimpleNamespace(cafile=None, capath=str(empty)))
    assert done == certifi.where()


def test_on_this_machine_unfrozen_it_does_nothing(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    assert tls_trust.use_bundled_ca_if_needed() is None


# -- every outbound HTTPS helper ends up with CAs loaded --------------------------


def _contexts():
    """The TLS context of each outbound HTTPS caller, built as it builds it."""
    from app.services import ai_service, email_service
    from app.services.payments import _http
    from app.switchboard import account, client, ws

    out = {
        "switchboard account (link, code, forget)": next(
            h._context
            for h in account._opener(None).handlers
            if isinstance(h, urllib.request.HTTPSHandler)
        ),
        "switchboard client": client.tls_context(),
        "switchboard line (ws)": ws._verified(None),
        # fx_service's urlopen with no context: urllib's default
        "currency rate (urlopen)": ssl._create_default_https_context(),
        # STARTTLS to the mail server (send_email, the test email)
        "outgoing mail (SMTP)": email_service.smtp_tls_context(),
    }
    for name, made in (
        ("AI Insights", ai_service._hardened_client(5)),
        ("payments", _http.hardened_client()),
    ):
        out[name] = made._transport._pool._ssl_context
        made.close()
    import httpx

    # the update check, SimpleFIN and Mercury: httpx with verify on
    for trust in (True, False):
        with httpx.Client(trust_env=trust) as made:
            out[f"httpx trust_env={trust}"] = made._transport._pool._ssl_context
    return out


def test_every_outbound_https_helper_has_cas_and_verifies():
    """Each verifies, and has authorities to verify with: loaded already
    (a bundle file), or a folder of hashed certificates the default
    context reads at the handshake (a Linux with no bundle file, where
    nothing shows as loaded before a connection is made)."""
    for name, ctx in _contexts().items():
        assert ctx.verify_mode == ssl.CERT_REQUIRED, name
        assert ctx.check_hostname, name
        assert ctx.get_ca_certs() or tls_trust.default_context_has_cas(), name


def test_a_folder_of_certificates_counts_before_any_is_loaded(monkeypatch, tmp_path):
    """The Linux gate box: no bundle file, a populated hashed folder. A
    default context shows nothing loaded, and it still has its authorities."""
    folder = tmp_path / "certs"
    folder.mkdir()
    (folder / "0a1b2c3d.0").write_text("placeholder", encoding="utf-8")
    monkeypatch.setenv("SSL_CERT_DIR", str(folder))
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    if ssl.create_default_context().get_ca_certs():
        pytest.skip("this platform loads a system store whatever OpenSSL says")
    assert tls_trust.default_context_has_cas()
    (folder / "0a1b2c3d.0").unlink()
    assert not tls_trust.default_context_has_cas()


def test_with_the_mac_defaults_missing_the_helper_gives_each_one_its_cas(
    monkeypatch, tmp_path
):
    """What the Mac saw, made here: OpenSSL's defaults pointed at nothing
    (an empty folder, no file), then the helper's SSL_CERT_FILE."""
    empty = tmp_path / "certs"
    empty.mkdir()
    monkeypatch.setenv("SSL_CERT_DIR", str(empty))
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    if ssl.create_default_context().get_ca_certs():
        pytest.skip("this platform loads a system store whatever OpenSSL says")
    assert not tls_trust.default_context_has_cas()
    monkeypatch.delenv("SSL_CERT_DIR")
    monkeypatch.delenv("SSL_CERT_FILE")
    import os

    done = tls_trust.use_bundled_ca_if_needed(
        frozen=True,
        platform="darwin",
        environ=os.environ,
        default_paths=MISSING,
        bundled=certifi.where(),
    )
    assert done == certifi.where()
    for name, ctx in _contexts().items():
        assert ctx.get_ca_certs(), name


def test_the_launcher_calls_it_before_the_server_child_and_the_smoke_checks_it():
    src = (ROOT / "desktop_launcher.py").read_text(encoding="utf-8")
    main = src[src.index("def main() -> int:") :]
    assert main.index("use_bundled_ca_if_needed()") < main.index('"--_serve" in')
    assert "default_context_has_cas()" in src
    spec = (ROOT / "packaging/macos/SlowBooksPro-mac.spec").read_text(encoding="utf-8")
    assert 'collect_data_files("certifi")' in spec


def test_nothing_turns_verification_off():
    for path in [ROOT / "desktop_launcher.py", *sorted((ROOT / "app").rglob("*.py"))]:
        text = path.read_text(encoding="utf-8")
        assert "CERT_NONE" not in text or path.name == "test_tls_trust_new265.py", path
        assert "verify=False" not in text, path
