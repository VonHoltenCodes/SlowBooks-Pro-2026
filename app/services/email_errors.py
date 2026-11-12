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
# The sentence (#811) reads the SMTP reply first: the exception's type and
# code, or the same thing parsed back out of the stored text (str() of an
# smtplib exception is its tuple or dict). A 550 for the recipient is "the
# address was refused", a 4xx is "try again later", only a socket error or a
# dropped connection is "could not be reached". The words of the error are
# the fallback for an error that carries no code.
# ============================================================================

import ast
import smtplib


def _clean_reason(error_text: str) -> str:
    """The SMTP error with Python's tuple/bytes wrapping stripped off.

    `str(SMTPAuthenticationError)` is "(535, b'5.7.8 ...')"; the bit a person
    reads is "535 5.7.8 ...". Leave anything we don't recognise untouched."""
    text = (error_text or "").strip()
    if not text:
        return ""
    if text.startswith("(") and text.endswith(")") and "," in text:
        code, _, rest = text[1:-1].partition(",")
        rest = rest.strip()
        # b'...' or b"..." -> the inner text
        if (rest.startswith("b'") and rest.endswith("'")) or (
            rest.startswith('b"') and rest.endswith('"')
        ):
            rest = rest[2:-1]
        rest = rest.replace("\\n", " ").replace("\\r", " ").strip()
        code = code.strip()
        if code.isdigit() and rest:
            return f"{code} {rest}"
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


def _by_code(code: int):
    if code in (530, 534, 535):
        return "auth", ""
    if 400 <= code < 500:
        return "later", ""
    return "other", ""


def _classify_exception(error: BaseException):
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return _refused(error.recipients or {})
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return "auth", ""
    if isinstance(error, smtplib.SMTPConnectError):
        return "unreachable", ""
    if isinstance(error, smtplib.SMTPSenderRefused):
        if 400 <= error.smtp_code < 500:
            return "later", ""
        return "sender", str(error.sender)
    if isinstance(error, smtplib.SMTPResponseException):
        return _by_code(error.smtp_code)
    if isinstance(error, smtplib.SMTPServerDisconnected):
        return "unreachable", ""
    if isinstance(error, OSError) and not isinstance(error, smtplib.SMTPException):
        # refused, timed out, unknown host, no route: the socket never got
        # (or lost) a conversation with the server
        return "unreachable", ""
    return None, ""


def _classify_text(text: str):
    """The same reading from a stored error: str() of an smtplib exception
    is a tuple "(550, b'...')", "(553, b'...', 'from@x')" for a refused
    sender, or a dict "{'to@x': (550, b'...')}" for refused recipients."""
    text = (text or "").strip()
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
    if len(value) == 3 and isinstance(value[2], str) and code >= 500:
        return "sender", value[2]
    return _by_code(code)


def failure_sentence(error, host: str = "") -> str:
    """One sentence a person can act on for a failed send. `error` is the
    exception itself or the text stored on the email log row. The reply
    code decides first (#811); only an error with none falls back to the
    words: the hint for a common mistake, else "not configured", else a
    plain fallback."""
    if isinstance(error, BaseException):
        kind, detail = _classify_exception(error)
        text = str(error)
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
    if kind == "unreachable":
        return _UNREACHABLE_SENTENCE
    if kind == "other":
        return _FALLBACK_SENTENCE
    _, hint = explain_send_failure(text, host)
    if hint:
        return hint
    if "not configured" in text.lower():
        return _NOT_CONFIGURED_SENTENCE
    return _FALLBACK_SENTENCE


def where_to_see_why(admin: bool) -> str:
    """Where a failed send's reason can be read. Recent sends is an
    administrator's section (recipients and raw SMTP errors), so anyone else
    is told who can read it rather than sent to a page they cannot open."""
    if admin:
        return "The reason is under Settings → Email (SMTP) → Recent sends."
    return "An administrator can see why under Settings → Recent sends."
