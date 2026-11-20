# ============================================================================
# Email send failures, in words the operator can act on (#447).
#
# send_email() stores the raw SMTP error on the EmailLog row. That text is
# accurate but unfriendly — a Gmail rejection reads
# "(535, b'5.7.8 Username and Password not accepted. For more information...')".
# One function turns a stored error into a plain reason and, for the handful
# of mistakes new users actually hit, a short hint. Kept small on purpose:
# three cases, matched on the error text (and the host, so Gmail is named).
#
# Since round 26 (NEW-275, NEW-276) the log row keeps the server's words
# as plain text with a tag for the stage it refused at (stored_text), and a
# refusal with no sentence of its own is said as that stage in the server's
# words ("The mail server refused the message: 554 5.7.1 …").
#
# The sentence (#811) reads the SMTP reply first: the exception's type and
# code, or the same thing parsed back out of the stored text (str() of an
# smtplib exception is its tuple or dict). A 550 for the recipient is "the
# address was refused", a 4xx is "try again later", only a socket error or a
# dropped connection is "could not be reached". The words of the error are
# the fallback for an error that carries no code.
# ============================================================================

import ast
import re
import smtplib
import ssl


def _clean_reason(error_text: str) -> str:
    """The SMTP error with Python's tuple/bytes wrapping stripped off.

    `str(SMTPAuthenticationError)` is "(535, b'5.7.8 ...')"; the bit a person
    reads is "535 5.7.8 ...". A refused sender is "(530, b'...', 'from@x')"
    and refused recipients a dict "{'to@x': (550, b'...')}"; each is read
    back as a value (W-216), so no quote, comma or b'' of Python's is left
    in the reply. A row stored since round 26 carries a stage tag in front
    of the plain words (see stored_text), which is ours and not shown.
    Leave anything we don't recognise untouched."""
    text = (error_text or "").strip()
    if not text:
        return ""
    tagged = _TAGGED.match(text)
    if tagged:
        return tagged.group(3).strip()
    if text[0] in "({" and len(text) <= 5000:
        try:
            value = ast.literal_eval(text)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            value = None
        if isinstance(value, dict) and value:
            parts = [
                f"{addr}: {_reply_words(reply)}".rstrip(": ")
                for addr, reply in value.items()
            ]
            # one refused address per line (NEW-275)
            return "\n".join(parts)
        if _code(value) is not None:
            return _reply_words(value)
    return text


def server_words(stored: str) -> str:
    """The mail server's reply as plain text, for Recent sends' second line
    (NEW-275): "530 5.7.0 Authentication Required", one "who@x: 550 ..."
    line per refused recipient, bytes read as UTF-8. Works on the plain
    words stored since round 26 and on the Python text rows stored before
    it (read back as a value, never run)."""
    return _clean_reason(stored)


def _reply_words(reply) -> str:
    """ "550 5.1.1 ..." for a (code, message[, sender]) reply."""
    if not (isinstance(reply, tuple) and reply):
        return _decoded(reply)
    return _code_words(reply[0], reply[1] if len(reply) > 1 else "")


def _decoded(message) -> str:
    if isinstance(message, (bytes, bytearray)):
        message = bytes(message).decode("utf-8", "replace")
    return " ".join(str(message or "").split())


def _code_words(code, message) -> str:
    return f"{code} {_decoded(message)}".strip()


# A row's stored text since round 26 (NEW-275, NEW-276): a short tag naming
# where in the conversation the server refused, then its own words as plain
# text. "[data] 554 5.7.1 Message rejected as spam",
# "[sender books@example.com] 530 5.7.0 Authentication Required",
# "[recipients] a@x: 550 5.1.1 User unknown" (one address per line). The
# tag is read by failure_sentence and never shown.
_TAGGED = re.compile(
    r"^\[(recipients|sender|connect|helo|auth|data|response|disconnected|socket)"
    r"(?: ([^\]\n]*))?\] ?(.*)$",
    re.S,
)
_STORED_LIMIT = 2000


def scrub_secrets(text: str, hidden) -> str:
    """`text` with each secret in `hidden` (the password, and its base64 in
    an AUTH line a server might echo) replaced, so no reply that repeats a
    credential is stored or shown."""
    import base64

    forms = set()
    for secret in hidden or ():
        if not secret:
            continue
        forms.add(secret)
        forms.add(base64.b64encode(secret.encode("utf-8", "replace")).decode("ascii"))
    # the longest first, so a form inside another is not left half-replaced
    for form in sorted(forms, key=len, reverse=True):
        if len(form) >= 3:
            text = text.replace(form, "••••")
    return text


def stored_text(error: BaseException, hidden=(), user: str = "") -> str:
    """The text send_email() keeps on the log row for a failed send: plain
    words, never str() of the exception (which is a Python tuple or dict).
    `hidden` are secrets scrubbed out of whatever the server said."""
    import base64

    secrets_ = [s for s in hidden if s]
    if user:
        for secret in list(secrets_):
            plain = f"\0{user}\0{secret}".encode("utf-8", "replace")
            secrets_.append(base64.b64encode(plain).decode("ascii"))
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        lines = [
            f"{addr}: {_reply_words(reply)}".rstrip(": ")
            for addr, reply in (error.recipients or {}).items()
        ]
        text = "[recipients] " + "\n".join(lines)
    elif isinstance(error, smtplib.SMTPSenderRefused):
        sender = " ".join(str(error.sender or "").split()).replace("]", "")
        text = f"[sender {sender}] " + _code_words(error.smtp_code, error.smtp_error)
    elif isinstance(error, smtplib.SMTPResponseException):
        stage = "response"
        if isinstance(error, smtplib.SMTPConnectError):
            stage = "connect"
        elif isinstance(error, smtplib.SMTPHeloError):
            stage = "helo"
        elif isinstance(error, smtplib.SMTPAuthenticationError):
            stage = "auth"
        elif isinstance(error, smtplib.SMTPDataError):
            stage = "data"
        text = f"[{stage}] " + _code_words(error.smtp_code, error.smtp_error)
    elif isinstance(error, smtplib.SMTPServerDisconnected):
        text = "[disconnected] " + (_decoded(str(error)) or "Connection closed")
    elif isinstance(error, OSError) and not isinstance(error, smtplib.SMTPException):
        # "Connection refused", "timed out", "Name or service not known":
        # the system's message, without Python's "[Errno 111]" in front
        words = getattr(error, "strerror", None) or str(error)
        if not words and isinstance(error, TimeoutError):
            words = "timed out"
        text = "[socket] " + (_decoded(words) or type(error).__name__)
    else:
        text = str(error)
    text = scrub_secrets(text, secrets_)
    if len(text) > _STORED_LIMIT:
        text = text[: _STORED_LIMIT - 1] + "…"
    return text


def explain_send_failure(error_text: str, host: str = "") -> tuple[str, str]:
    """(reason, hint) for a failed send.

    `reason` is the SMTP error cleaned up for reading; `hint` is one sentence
    of advice for a common case, or "" when we have nothing useful to add.
    The mapping is deliberately tiny: username/password (535/534), a server
    we couldn't reach (refused/timed out/unknown host), and Gmail or Google
    Workspace, which reject an ordinary password and need an app password."""
    reason = _clean_reason(error_text)
    text = (error_text or "").lower()
    hint = ""
    if "535" in text or "534" in text:
        hint = _credentials_sentence(host, text)
    elif any(
        s in text
        for s in (
            "refused",
            "timed out",
            "timeout",
            "getaddrinfo",
            "name or service not known",
            "no route to host",
            "network is unreachable",
            "connection unexpectedly closed",
        )
    ):
        hint = _UNREACHABLE_SENTENCE

    return reason, hint


# The sentence a failed send is shown with, wherever it is shown. The Recent
# sends list reads it from the API (GET /api/email-log's `failure`) rather than
# keeping a second copy of the mapping in settings.js, so the list and the
# test-email failure can never word the same error two ways.
_FALLBACK_SENTENCE = "The email could not be sent."
_NOT_CONFIGURED_SENTENCE = (
    "SMTP is not configured — fill in the fields under Settings → Email (SMTP)."
)


_REFUSED_SENTENCE = (
    "The address was refused by the mail server: {}. Check it and send again."
)
_SENDER_REFUSED_SENTENCE = (
    "The mail server refused the sender address: {}. Check the From address "
    "under Settings → Email (SMTP)."
)
_TRY_LATER_SENTENCE = "The mail server asked to try again later."
# 530, authentication required: the server wants a sign-in before it takes
# mail, and none was given (Gmail's "530 5.7.0 Authentication Required" to a
# send with no username), not a refused sender
_SIGN_IN_SENTENCE = (
    "The mail server wants a sign-in, and none is set. Enter the username and "
    "password under Settings → Email (SMTP)."
)
# 530 asking for STARTTLS first: the server takes mail only over a secure
# connection
_STARTTLS_FIRST_SENTENCE = (
    "The mail server takes mail only over a secure connection. Turn on Use "
    "TLS under Settings → Email (SMTP)."
)
# Use TLS off, a username set, and a server that offers AUTH only after
# STARTTLS (Gmail on 587): smtplib's "SMTP AUTH extension not supported by
# server." in words (W-217)
_SIGN_IN_NEEDS_TLS_SENTENCE = (
    "The mail server offers sign-in only over a secure connection, so nothing "
    "was sent and your password was not given to it. Turn on Use TLS under "
    "Settings → Email (SMTP). ({host})"
)
# A refusal we have no sentence of its own for (NEW-276): the stage it came
# at, then the server's own words as plain text
_CONNECTION_REFUSED_SENTENCE = "The mail server refused the connection: {}"
_MESSAGE_REFUSED_SENTENCE = "The mail server refused the message: {}"
_UNREACHABLE_SENTENCE = (
    "The server could not be reached — check the host, port, and the "
    "Use TLS setting."
)


def _credentials_sentence(host: str, text: str) -> str:
    is_google = any(s in (host or "").lower() for s in ("gmail", "google")) or any(
        s in (text or "").lower() for s in ("gmail", "google")
    )
    if is_google:
        return (
            "Google will not accept a normal password here. Turn on "
            "2-Step Verification on the account and use a 16-character "
            "app password as the SMTP password."
        )
    return "The server rejected the username or password — check both."


def _code(value):
    """The reply code of a (code, message) pair, or None."""
    if isinstance(value, tuple) and value and isinstance(value[0], int):
        return value[0]
    return None


def _refused(recipients: dict):
    """(kind, detail) for a recipients-refused mapping {address: (code, msg)}:
    the 5xx addresses are refused; only 4xx ones mean try again later."""
    codes = {addr: _code(reply) for addr, reply in recipients.items()}
    hard = [str(a) for a, c in codes.items() if c is not None and c >= 500]
    if hard:
        return "refused", ", ".join(hard)
    if any(c is not None and 400 <= c < 500 for c in codes.values()):
        return "later", ""
    return None, ""


def _by_code(code: int, message="") -> tuple:
    if code == 530:
        return _sign_in_kind(message)
    if code in (534, 535):
        return "auth", ""
    if 400 <= code < 500:
        return "later", ""
    # a refusal with no sentence of its own: say so, in the server's words
    # (NEW-276)
    return "message", ""


def _sign_in_kind(message) -> tuple:
    """A 530: the server wants a sign-in, or STARTTLS first."""
    if isinstance(message, bytes):
        message = message.decode("utf-8", "replace")
    if "starttls" in str(message or "").lower():
        return "starttls", ""
    return "sign-in", ""


def _classify_exception(error: BaseException):
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return _refused(error.recipients or {})
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return "auth", ""
    if isinstance(error, (smtplib.SMTPConnectError, smtplib.SMTPHeloError)):
        # the greeting or EHLO answered with a refusal (a 554 "no SMTP
        # service here"): the server was reached and said no (NEW-276)
        if 400 <= error.smtp_code < 500:
            return "later", ""
        return "connection", ""
    if isinstance(error, smtplib.SMTPSenderRefused):
        if 400 <= error.smtp_code < 500:
            return "later", ""
        if error.smtp_code == 530:
            return _sign_in_kind(error.smtp_error)
        return "sender", str(error.sender)
    if isinstance(error, smtplib.SMTPDataError):
        # DATA refused, or the message refused after its body was sent
        if 400 <= error.smtp_code < 500:
            return "later", ""
        return "message", ""
    if isinstance(error, smtplib.SMTPResponseException):
        return _by_code(error.smtp_code, error.smtp_error)
    if isinstance(error, smtplib.SMTPServerDisconnected):
        return "unreachable", ""
    if isinstance(error, OSError) and not isinstance(error, smtplib.SMTPException):
        # refused, timed out, unknown host, no route: the socket never got
        # (or lost) a conversation with the server
        return "unreachable", ""
    return None, ""


def _classify_text(text: str):
    """The same reading from a stored error: the tagged plain words stored
    since round 26 (stored_text), or, on an older row, str() of an smtplib
    exception: a tuple "(550, b'...')", "(553, b'...', 'from@x')" for a
    refused sender, or a dict "{'to@x': (550, b'...')}" for refused
    recipients."""
    text = (text or "").strip()
    tagged = _TAGGED.match(text)
    if tagged:
        return _classify_tagged(*tagged.groups())
    if not text or len(text) > 5000 or text[0] not in "({":
        return None, ""
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None, ""
    if isinstance(value, dict) and value:
        return _refused(value)
    code = _code(value)
    if code is None:
        return None, ""
    message = value[1] if len(value) > 1 else ""
    if code == 530:
        return _sign_in_kind(message)
    if len(value) == 3 and isinstance(value[2], str) and code >= 500:
        return "sender", value[2]
    return _by_code(code, message)


_LEADING_CODE = re.compile(r"^(\d{3})\b")
_RECIPIENT_LINE = re.compile(r"^(.*?): (\d{3})\b")


def _classify_tagged(stage: str, arg, words: str):
    if stage == "recipients":
        refused = {}
        for line in words.splitlines():
            found = _RECIPIENT_LINE.match(line.strip())
            if found:
                refused[found.group(1)] = (int(found.group(2)), "")
        return _refused(refused) if refused else ("other", "")
    if stage == "disconnected" or stage == "socket":
        return "unreachable", ""
    found = _LEADING_CODE.match(words.strip())
    code = int(found.group(1)) if found else None
    if stage == "sender":
        if code is not None and 400 <= code < 500:
            return "later", ""
        if code == 530:
            return _sign_in_kind(words)
        return "sender", arg or ""
    if stage == "auth":
        return "auth", ""
    if code is None:
        return "other", ""
    if stage in ("connect", "helo"):
        return ("later", "") if 400 <= code < 500 else ("connection", "")
    if stage == "data":
        return ("later", "") if 400 <= code < 500 else ("message", "")
    return _by_code(code, words)


# -- the secure connection to the mail server ---------------------------------
#
# send_email() verifies the mail server's certificate on every TLS send (no
# setting turns it off). A failure there is stored on the log row as one of
# the short texts below — never OpenSSL's own words — and read back into
# the sentence a bookkeeper sees. Each says nothing was sent and the
# password was not given: the check runs before LOGIN, so both are true.


class MailTLSError(Exception):
    """The secure connection was asked for and could not be set up or
    verified; str() is the text stored on the email log row."""


_CERT_TEXT = "Certificate not verified ({host}): {reason}"
_TLS_TEXT = "Secure connection failed ({host})"
_NO_STARTTLS_TEXT = "No secure connection offered ({host})"
_SIGN_IN_NEEDS_TLS_TEXT = "Sign-in offered only over a secure connection ({host})"
_TLS_STORED = re.compile(
    r"^(Certificate not verified|Secure connection failed|No secure connection "
    r"offered|Sign-in offered only over a secure connection) \((.*?)\)"
    r"(?:: (.+))?$",
    re.S,
)

# OpenSSL's X509_V_ERR_* codes for the reasons people actually meet
_ISSUER = "certificate is from an issuer this computer does not trust"
_CERT_REASONS = {
    62: "certificate is for another name",  # HOSTNAME_MISMATCH
    18: "certificate is self-signed",  # DEPTH_ZERO_SELF_SIGNED_CERT
    19: "certificate is self-signed",  # SELF_SIGNED_CERT_IN_CHAIN
    10: "certificate has expired",  # CERT_HAS_EXPIRED
    9: "certificate is not valid yet",  # CERT_NOT_YET_VALID
    23: "certificate has been revoked",  # CERT_REVOKED
    2: _ISSUER,  # UNABLE_TO_GET_ISSUER_CERT
    20: _ISSUER,  # UNABLE_TO_GET_ISSUER_CERT_LOCALLY
    21: _ISSUER,  # UNABLE_TO_VERIFY_LEAF_SIGNATURE
}
_CERT_UNVERIFIED = "certificate could not be verified"

_CERT_SENTENCE = (
    "SlowBooks Pro could not verify the mail server's security certificate, "
    "so nothing was sent and your password was not given to it. Check the "
    "server name under Settings → Email (SMTP), or ask your mail provider. "
    "({host}: {reason})"
)
_TLS_SENTENCE = (
    "SlowBooks Pro could not set up a secure connection to the mail server, "
    "so nothing was sent and your password was not given to it. Check the "
    "port and the Use TLS setting under Settings → Email (SMTP), or ask your "
    "mail provider. ({host})"
)
_NO_STARTTLS_SENTENCE = (
    "The mail server did not offer a secure connection, so nothing was sent "
    "and your password was not given to it. Check the port and the Use TLS "
    "setting under Settings → Email (SMTP), or ask your mail provider. ({host})"
)


def certificate_reason(error: ssl.SSLCertVerificationError) -> str:
    """The plain words for why a certificate was refused."""
    code = getattr(error, "verify_code", None)
    if code in _CERT_REASONS:
        return _CERT_REASONS[code]
    words = str(getattr(error, "verify_message", "") or error).lower()
    for needle, reason in (
        ("hostname mismatch", _CERT_REASONS[62]),
        ("ip address mismatch", _CERT_REASONS[62]),
        ("self-signed", _CERT_REASONS[18]),
        ("self signed", _CERT_REASONS[18]),
        ("expired", _CERT_REASONS[10]),
        ("not yet valid", _CERT_REASONS[9]),
        ("issuer", _ISSUER),
    ):
        if needle in words:
            return reason
    return _CERT_UNVERIFIED


def tls_failure_text(error: BaseException, host: str) -> str:
    """The text stored on the log row for a failure setting up TLS."""
    if isinstance(error, ssl.SSLCertVerificationError):
        return _CERT_TEXT.format(host=host, reason=certificate_reason(error))
    return _TLS_TEXT.format(host=host)


def no_starttls_text(host: str) -> str:
    return _NO_STARTTLS_TEXT.format(host=host)


def sign_in_needs_tls_text(host: str) -> str:
    return _SIGN_IN_NEEDS_TLS_TEXT.format(host=host)


def tls_sentence(text: str) -> str:
    """The sentence for a stored TLS failure, or "" when it is not one."""
    found = _TLS_STORED.match((text or "").strip())
    if not found:
        return ""
    kind, host, reason = found.groups()
    if kind == "Certificate not verified":
        return _CERT_SENTENCE.format(host=host, reason=reason or _CERT_UNVERIFIED)
    if kind == "No secure connection offered":
        return _NO_STARTTLS_SENTENCE.format(host=host)
    if kind == "Sign-in offered only over a secure connection":
        return _SIGN_IN_NEEDS_TLS_SENTENCE.format(host=host)
    return _TLS_SENTENCE.format(host=host)


def failure_sentence(error, host: str = "") -> str:
    """One sentence a person can act on for a failed send. `error` is the
    exception itself or the text stored on the email log row. The reply
    code decides first (#811); only an error with none falls back to the
    words: the hint for a common mistake, else "not configured", else a
    plain fallback."""
    if isinstance(error, ssl.SSLCertVerificationError):
        # a certificate is only checked in the handshake, before any login
        error = MailTLSError(tls_failure_text(error, host))
    tls = tls_sentence(str(error) if isinstance(error, BaseException) else error)
    if tls:
        return tls
    if isinstance(error, BaseException):
        kind, detail = _classify_exception(error)
        text = stored_text(error)
    else:
        text = error or ""
        kind, detail = _classify_text(text)
    if kind == "refused":
        return _REFUSED_SENTENCE.format(detail)
    if kind == "sender":
        return _SENDER_REFUSED_SENTENCE.format(detail)
    if kind == "later":
        return _TRY_LATER_SENTENCE
    if kind == "auth":
        return _credentials_sentence(host, text)
    if kind == "sign-in":
        return _SIGN_IN_SENTENCE
    if kind == "starttls":
        return _STARTTLS_FIRST_SENTENCE
    if kind == "unreachable":
        return _UNREACHABLE_SENTENCE
    if kind in ("connection", "message"):
        words = _sentence_words(text)
        if words:
            template = (
                _CONNECTION_REFUSED_SENTENCE
                if kind == "connection"
                else _MESSAGE_REFUSED_SENTENCE
            )
            return template.format(words)
        return _FALLBACK_SENTENCE
    if kind == "other":
        return _FALLBACK_SENTENCE
    _, hint = explain_send_failure(text, host)
    if hint:
        return hint
    if "not configured" in text.lower():
        return _NOT_CONFIGURED_SENTENCE
    return _FALLBACK_SENTENCE


def _sentence_words(text: str) -> str:
    """The server's words for a sentence: on one line, ending in a full
    stop, never so long the sentence is lost under them."""
    words = " ".join(server_words(text).split())
    if len(words) > 300:
        words = words[:299].rstrip() + "…"
    if words and words[-1] not in ".!?…":
        words += "."
    return words


def where_to_see_why(admin: bool) -> str:
    """Where a failed send's reason can be read. Recent sends is an
    administrator's section (recipients and raw SMTP errors), so anyone else
    is told who can read it rather than sent to a page they cannot open."""
    if admin:
        return "The reason is under Settings → Email (SMTP) → Recent sends."
    return "An administrator can see why under Settings → Recent sends."


def sentence_for_last_send(db, entity_type: str, entity_id) -> str:
    """The sentence for the send just made for this document, read from the
    log row send_email() wrote, or "" when there is nothing more to say than
    that it failed (#811). Call it only after send_email() ran and returned
    False: every run writes a row, so the newest one for the document is
    that send's. The raw SMTP text stays on the row, for an administrator."""
    from app.models.email_log import EmailLog
    from app.services.settings_service import get_setting_raw

    row = (
        db.query(EmailLog)
        .filter(
            EmailLog.entity_type == (entity_type or ""),
            EmailLog.entity_id == (entity_id or 0),
        )
        .order_by(EmailLog.id.desc())
        .first()
    )
    if row is None or row.status != "failed":
        return ""
    sentence = failure_sentence(
        row.error_message or "", get_setting_raw(db, "smtp_host") or ""
    )
    return "" if sentence == _FALLBACK_SENTENCE else sentence


def send_failed_detail(db, entity_type: str, entity_id, admin: bool) -> str:
    """The 502 a document's Email button answers with when the send failed:
    what went wrong in a sentence when the reply says (#811), else the
    advice to check the SMTP settings (an administrator's), then where the
    reason can be read."""
    sentence = sentence_for_last_send(db, entity_type, entity_id)
    detail = "Email could not be sent. "
    if sentence:
        detail += sentence + " "
    elif admin:
        detail += "Check the SMTP settings under Settings → Email (SMTP). "
    return detail + where_to_see_why(admin)
