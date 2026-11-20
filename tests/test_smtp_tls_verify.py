"""Outgoing email verifies the mail server's certificate.

send_email() used to call starttls() with no context, so smtplib took any
certificate. Now the connection goes EHLO → STARTTLS (a verifying default
context, host name checked, TLS 1.2 at least) → EHLO → LOGIN, and a
certificate that does not prove the server, or a server that offers no
STARTTLS when TLS is asked for, ends the send before LOGIN: no password, no
message. The failure reads as a sentence a bookkeeper can act on.

A real SMTP conversation with a local stub on 127.0.0.1 and throwaway
certificates made here with `cryptography` (already a dependency); the
throwaway CA is the trust root through SSL_CERT_FILE, which OpenSSL reads
on every ssl.create_default_context(). No real network, no stored password.
"""

import datetime
import secrets
import socket
import ssl
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.models.email_log import EmailLog
from app.services import email_errors
from app.services.email_service import send_email, smtp_tls_context
from app.services.settings_service import set_setting

HOST = "localhost"


# -- throwaway certificates --------------------------------------------------


def _key():
    return ec.generate_private_key(ec.SECP256R1())


def _cert(
    subject_cn,
    key,
    issuer_cert=None,
    issuer_key=None,
    *,
    ca=False,
    days=(-1, 30),
    san=None,
):
    now = datetime.datetime.now(datetime.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer_cert.subject if issuer_cert else name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + datetime.timedelta(days=days[0]))
        .not_valid_after(now + datetime.timedelta(days=days[1]))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if ca:
        builder = builder.add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        builder = builder.add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
    else:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(san or subject_cn)]),
            critical=False,
        )
    if issuer_cert is not None:
        builder = builder.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            critical=False,
        )
    return builder.sign(issuer_key or key, hashes.SHA256())


def _pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


def _key_pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    """{name: (certfile, keyfile)} for each server certificate, and the CA."""
    root = tmp_path_factory.mktemp("pki")
    ca_key = _key()
    ca = _cert("SlowBooks Pro test CA", ca_key, ca=True, days=(-1, 365))
    other_ca_key = _key()
    other_ca = _cert("Some other CA", other_ca_key, ca=True, days=(-1, 365))
    (root / "ca.pem").write_bytes(_pem(ca))
    made = {}
    for name, cert, key in (
        ("trusted", *_leaf(HOST, ca, ca_key)),
        ("wrong_host", *_leaf("mail.example.test", ca, ca_key)),
        ("expired", *_leaf(HOST, ca, ca_key, days=(-30, -1))),
        ("unknown_issuer", *_leaf(HOST, other_ca, other_ca_key)),
        ("self_signed", *_self_signed(HOST)),
    ):
        (root / f"{name}.pem").write_bytes(_pem(cert))
        (root / f"{name}.key").write_bytes(_key_pem(key))
        made[name] = (str(root / f"{name}.pem"), str(root / f"{name}.key"))
    made["ca"] = str(root / "ca.pem")
    return made


def _leaf(cn, ca, ca_key, days=(-1, 30)):
    key = _key()
    return _cert(cn, key, ca, ca_key, days=days), key


def _self_signed(cn):
    key = _key()
    return _cert(cn, key, days=(-1, 30), san=cn), key


# -- a stdlib SMTP stub that records everything it is told -------------------


class StubSMTP:
    """One-thread SMTP server: EHLO (offering STARTTLS when asked to), a
    real TLS handshake on STARTTLS, AUTH, MAIL/RCPT/DATA. `commands` is
    every line received; `raw` every byte."""

    def __init__(self, certfile=None, keyfile=None, offer_starttls=True):
        self.offer_starttls = offer_starttls
        self.context = None
        if certfile:
            self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.context.load_cert_chain(certfile, keyfile)
        self.commands = []
        self.raw = b""
        self.messages = []
        self.handshake_failed = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.sock.settimeout(10)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        conn.settimeout(10)
        try:
            self._talk(conn)
        except (OSError, ssl.SSLError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _talk(self, conn):
        tls = False
        f = conn.makefile("rb")

        def say(line):
            conn.sendall(line.encode() + b"\r\n")

        say("220 stub ESMTP")
        while True:
            line = f.readline()
            if not line:
                return
            self.raw += line
            text = line.decode(errors="replace").rstrip("\r\n")
            self.commands.append(text)
            verb = text.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                lines = ["250-stub", "250-AUTH PLAIN LOGIN"]
                if self.offer_starttls and not tls:
                    lines.append("250-STARTTLS")
                lines.append("250 OK")
                conn.sendall("".join(x + "\r\n" for x in lines).encode())
            elif verb == "STARTTLS":
                say("220 Go ahead")
                try:
                    conn = self.context.wrap_socket(conn, server_side=True)
                except (ssl.SSLError, OSError):
                    self.handshake_failed = True
                    # keep listening on the plain socket: anything the
                    # client says after a failed handshake is recorded
                    self._drain(conn)
                    return
                tls = True
                f = conn.makefile("rb")
            elif verb == "AUTH":
                say("235 2.7.0 Authentication successful")
            elif verb in ("MAIL", "RCPT", "RSET", "NOOP"):
                say("250 OK")
            elif verb == "DATA":
                say("354 End data with <CR><LF>.<CR><LF>")
                body = b""
                while True:
                    chunk = f.readline()
                    if not chunk or chunk == b".\r\n":
                        break
                    body += chunk
                self.raw += body
                self.messages.append(body)
                say("250 OK queued")
            elif verb == "QUIT":
                say("221 Bye")
                return
            else:
                say("502 Not implemented")

    def _drain(self, conn):
        try:
            conn.settimeout(2)
            while True:
                data = conn.recv(65536)
                if not data:
                    return
                self.raw += data
        except (OSError, ssl.SSLError):
            return

    def close(self):
        self.thread.join(timeout=10)
        self.sock.close()


@pytest.fixture
def trust_ca(pki, monkeypatch):
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", pki["ca"])


def _configure(db, port, password, use_tls="true"):
    for key, value in (
        ("smtp_host", HOST),
        ("smtp_port", str(port)),
        ("smtp_user", "books@example.test"),
        ("smtp_password", password),
        ("smtp_from_email", "books@example.test"),
        ("smtp_use_tls", use_tls),
    ):
        set_setting(db, key, value)
    db.commit()


def _send(db, stub, password):
    _configure(db, stub.port, password)
    ok = send_email(
        db,
        to_email="customer@example.test",
        subject="Invoice 1001",
        html_body="<p>Attached.</p>",
        entity_type="invoice",
        entity_id=1001,
    )
    stub.close()
    row = db.query(EmailLog).order_by(EmailLog.id.desc()).first()
    return ok, row


def _never_got_the_password_or_the_mail(stub, password):
    verbs = [c.split(" ", 1)[0].upper() for c in stub.commands]
    assert "AUTH" not in verbs and "MAIL" not in verbs and "DATA" not in verbs
    assert not stub.messages
    assert password.encode() not in stub.raw
    import base64

    assert base64.b64encode(password.encode())[:12] not in stub.raw


# -- the context ---------------------------------------------------------------


def test_the_mail_context_verifies_and_needs_tls_1_2():
    ctx = smtp_tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


# -- a trusted certificate: the mail goes ------------------------------------


def test_a_trusted_certificate_sends_in_order(db_session, pki, trust_ca):
    password = secrets.token_urlsafe(16)
    stub = StubSMTP(*pki["trusted"])
    ok, row = _send(db_session, stub, password)
    assert ok, row.error_message
    assert row.status == "sent"
    verbs = [c.split(" ", 1)[0].upper() for c in stub.commands]
    # EHLO → STARTTLS → EHLO → LOGIN → the mail
    assert verbs[:4] == ["EHLO", "STARTTLS", "EHLO", "AUTH"]
    assert verbs.index("MAIL") > verbs.index("AUTH")
    assert stub.messages and b"Invoice 1001" in stub.messages[0]


# -- certificates that do not prove the server: nothing is sent --------------


@pytest.mark.parametrize(
    "which, reason",
    [
        ("self_signed", "certificate is self-signed"),
        ("wrong_host", "certificate is for another name"),
        ("expired", "certificate has expired"),
        (
            "unknown_issuer",
            "certificate is from an issuer this computer does not trust",
        ),
    ],
)
def test_an_unproven_certificate_is_refused_before_login(
    db_session, pki, trust_ca, which, reason
):
    password = secrets.token_urlsafe(16)
    stub = StubSMTP(*pki[which])
    ok, row = _send(db_session, stub, password)
    assert not ok
    assert row.status == "failed"
    assert row.error_message == f"Certificate not verified ({HOST}): {reason}"
    _never_got_the_password_or_the_mail(stub, password)
    sentence = email_errors.failure_sentence(row.error_message)
    assert sentence == (
        "SlowBooks Pro could not verify the mail server's security certificate, "
        "so nothing was sent and your password was not given to it. Check the "
        "server name under Settings → Email (SMTP), or ask your mail provider. "
        f"({HOST}: {reason})"
    )


def test_a_server_offering_no_starttls_is_refused_before_login(
    db_session, pki, trust_ca
):
    password = secrets.token_urlsafe(16)
    stub = StubSMTP(offer_starttls=False)
    ok, row = _send(db_session, stub, password)
    assert not ok
    assert row.error_message == f"No secure connection offered ({HOST})"
    _never_got_the_password_or_the_mail(stub, password)
    assert email_errors.failure_sentence(row.error_message).startswith(
        "The mail server did not offer a secure connection, so nothing was sent "
        "and your password was not given to it."
    )


# -- where a bookkeeper reads it -----------------------------------------------


def test_the_test_email_button_says_it_in_plain_words(
    client, db_session, pki, trust_ca
):
    password = secrets.token_urlsafe(16)
    stub = StubSMTP(*pki["self_signed"])
    _configure(db_session, stub.port, password)
    r = client.post("/api/settings/test-email")
    stub.close()
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "could not verify the mail server's security certificate" in detail
    assert f"({HOST}: certificate is self-signed)" in detail
    for raw in ("SSL", "CERTIFICATE_VERIFY_FAILED", "_ssl.c", "Traceback"):
        assert raw not in detail
    # said once, not the stored text and then the sentence
    assert "Certificate not verified" not in detail
    _never_got_the_password_or_the_mail(stub, password)
    rows = client.get("/api/email-log?status=failed").json()["rows"]
    assert "CERTIFICATE_VERIFY_FAILED" not in (rows[0]["error_message"] or "")
    assert "security certificate" in rows[0]["failure"]


def test_a_document_send_names_it(db_session):
    db_session.add(
        EmailLog(
            entity_type="invoice",
            entity_id=77,
            recipient="customer@example.test",
            subject="Invoice",
            status="failed",
            error_message=f"Certificate not verified ({HOST}): certificate has expired",
        )
    )
    db_session.commit()
    detail = email_errors.send_failed_detail(db_session, "invoice", 77, admin=True)
    assert "could not verify the mail server's security certificate" in detail
    assert "certificate has expired" in detail


def test_a_reminder_run_records_the_same_sentence(db_session):
    from app.services.collections import _send_failed

    db_session.add(
        EmailLog(
            entity_type="reminder",
            entity_id=78,
            recipient="customer@example.test",
            subject="Reminder",
            status="failed",
            error_message=f"Certificate not verified ({HOST}): certificate is self-signed",
        )
    )
    db_session.commit()
    error = _send_failed("The email", "reminder", 78, db_session)
    assert error.startswith(
        "The email could not be sent. SlowBooks Pro could not verify"
    )
    assert "certificate is self-signed" in error


def test_an_exception_reads_the_same_as_its_stored_text():
    err = ssl.SSLCertVerificationError(1, "certificate verify failed")
    err.verify_code = 62
    err.verify_message = "Hostname mismatch, certificate is not valid for 'x'."
    assert "certificate is for another name" in email_errors.failure_sentence(err, HOST)
    unknown = ssl.SSLCertVerificationError(1, "certificate verify failed")
    unknown.verify_code = 999
    unknown.verify_message = "something new"
    assert "certificate could not be verified" in email_errors.failure_sentence(
        unknown, HOST
    )
