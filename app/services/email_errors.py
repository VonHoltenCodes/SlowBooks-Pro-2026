# ============================================================================
# Email send failures: what is stored, and who is told what.
#
# The sentence. Every failed send has one fixed sentence of ours, chosen by
# where the send failed and the reply code (and its enhanced status code).
# It may name our own data (the host from Settings, addresses from our own
# record of the send); it never holds text that came from the mail server.
# It is what everyone reads, everywhere: GET /api/email-log's `failure`,
# Recent sends, a document's Activity, the answer to a send and each batch
# result. Where the sentence does not say why, it ends with where the
# reason can be read (where_to_see_why).
#
# The server's reply. What the mail server answered is kept on the email
# log row as readable text and shown, as it was received, in two places,
# both an administrator's: the line under the sentence in Recent sends
# (GET /api/email-log's `error_message` for an administrator), and the
# answer to Test Email. Nowhere else, and to nobody else: no other role
# and no API token.
#
# Nothing in the reply is changed or hidden. If a mail server repeats the
# sign-in it was given, the administrator, who set it, sees it there.
#
# FailureReader is the one place the stored text leaves storage, and
# stored_text the one place it is made; tests hold the app to both.
# ============================================================================

import ast
import re
import smtplib
import ssl

from app.services.terminology import AsWritten

_CLOSED_BECAUSE = re.compile(r"^Connection unexpectedly closed: (.+)$", re.S)
_ERRNO = re.compile(r"^\[(?:Errno|WinError) -?\d+\] ")
_SYSTEM_ERROR = re.compile(r"^\[(?:(?:Errno|WinError) -?\d+|SSL: [A-Z0-9_]+)\] ")


def _plain_cause(words: str) -> str:
    """A socket failure as its plain cause: smtplib's "Connection
    unexpectedly closed: timed out" is "timed out" (nothing closed; the
    server never answered), and Python's "[Errno 104] " is dropped from
    "Connection reset by peer". A real close ("Connection unexpectedly
    closed") stays as it is."""
    words = (words or "").strip()
    found = _CLOSED_BECAUSE.match(words)
    if found:
        words = found.group(1).strip()
    return _ERRNO.sub("", words)


def _text(message) -> str:
    """A reply as text (bytes read as UTF-8, what cannot be read replaced)."""
    if isinstance(message, (bytes, bytearray)):
        message = bytes(message).decode("utf-8", "replace")
    return str(message or "")


def _decoded(message) -> str:
    """A reply as one line of text."""
    return " ".join(_text(message).split())


def _code_words(code, message) -> str:
    return f"{code} {_decoded(message)}".strip()


def _is_reply_code(code) -> bool:
    """Whether `code` is a reply code SMTP has (smtplib gives -1 for an
    answer whose first three characters are not a number)."""
    return isinstance(code, int) and not isinstance(code, bool) and 200 <= code <= 599


def _reply_words(reply) -> str:
    """ "550 5.1.1 ..." for a (code, message[, sender]) reply. An answer
    that was not an SMTP reply is said to be that, in words of ours, with
    what was received after them; smtplib's "-1" is never shown as a
    code."""
    if not (isinstance(reply, tuple) and reply):
        return _decoded(reply)
    said = reply[1] if len(reply) > 1 else ""
    if isinstance(reply[0], int) and not _is_reply_code(reply[0]):
        return _not_smtp_words(said)
    return _code_words(reply[0], said)


def _as_value(text: str):
    """A row stored as Python's str() of an smtplib exception (releases
    before 2.32), read back as the tuple or dict it was (never run), or
    None."""
    if not text or text[0] not in "({" or len(text) > 20000:
        return None
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None


def _code(value):
    """The reply code of a (code, message) pair, or None."""
    if isinstance(value, tuple) and value and isinstance(value[0], int):
        return value[0]
    return None


# an enhanced status code ("5.7.1") at the start of a reply's words
_ENHANCED = re.compile(r"[245]\.\d{1,3}\.\d{1,3}")
_ENHANCED_FIRST = re.compile(r"\s*([245]\.\d{1,3}\.\d{1,3})(?=\s|$)")


def _enhanced(message) -> str:
    """The enhanced status code a reply's words start with, or ""."""
    found = _ENHANCED_FIRST.match(_text(message))
    return found.group(1) if found else ""


# -- failures on our own side of the conversation --------------------------------


class MailTLSError(Exception):
    """The secure connection was asked for and could not be set up or
    verified; str() is the text stored on the email log row. When the
    server itself refused STARTTLS, `refusal` is (host, code, words) and
    the row keeps that reply instead (starttls_refused)."""

    refusal = None


class MailLocalError(Exception):
    """A send that failed for a reason of our own, before the server was
    asked anything it could refuse: str() is words of ours for the email
    log row, `stage` and `kind` choose the sentence."""

    def __init__(self, stage: str, kind: str, words: str):
        super().__init__(words)
        self.stage, self.kind = stage, kind


def sign_in_letters_error() -> MailLocalError:
    """The username or password holds a character SMTP sign-in (ASCII)
    cannot carry. Says nothing of which character or where."""
    return MailLocalError(
        "auth",
        "letters",
        "The username or password has a character the sign-in cannot carry",
    )


def host_name_error() -> MailLocalError:
    """The host in Settings is not a name a connection can be made to (an
    empty or over-long label)."""
    return MailLocalError("socket", "", "Host name is not valid")


def address_letters_error(addresses) -> MailLocalError:
    """An address of this send (To, Cc, Bcc or From) holds a character an
    SMTP envelope (ASCII) cannot carry. Found before anything is sent; the
    addresses are our own."""
    names = ", ".join(_fitting([_address(a) for a in addresses if a], _TAG_REFUSED))
    return MailLocalError(
        "address",
        names,
        "An address has a character that cannot be sent to the mail server: " + names,
    )


# a fault of ours that is not an answer from the mail server: the cause is
# in the server log (its kind and where it was raised)
_UNEXPECTED = (
    "Unexpected error in SlowBooks Pro, not an answer from the mail server "
    "(the server log has the cause)"
)


class RecipientsPartlyRefused(Exception):
    """The server took the message for some of its recipients and refused
    the others. `recipients` is smtplib's {address: (code, words)} of the
    refused; `delivered` the addresses of ours it took the message for."""

    def __init__(self, recipients: dict, delivered=()):
        super().__init__("some recipients were refused")
        self.recipients = dict(recipients or {})
        self.delivered = list(delivered or ())


# smtplib's own words (not a server's) for a server that offers no sign-in
# we can use
_NO_SIGN_IN_OFFERED = (
    "SMTP AUTH extension not supported by server.",
    "No suitable authentication method found.",
)
_LAST_RESPONSE = re.compile(r"Last response: (\(.*\))\s*$", re.S)


# -- what a row stores ---------------------------------------------------------
#
# "[data] 554 5.7.1 Message rejected as spam": our tag, then readable text.
# The tag is ours and never shown: the stage of the conversation the
# failure came at and what the sentence needs of our own record of the
# send. What follows it is the server's reply as it was received (the code
# and the decoded words, on one line), or the plain cause of a failure
# that was not a reply.
#   [recipients a@x 550 5.1.1, b@y 451; sent c@z] a@x: 550 5.1.1 User unknown
#       (the refused, each with its reply code; then who the message still
#        went to; the server's reply to each refused address on a line)
#   [sender books@x] 553 5.1.8 Sender rejected
#   [starttls mail.x] 454 4.7.0 TLS not available   (the host from Settings)
#   [socket] timed out                      [disconnected] Connection unexpectedly closed
#   [disconnected after-body] timed out    (lost once the whole message had
#       been handed over: the server may have taken it)
#   [disconnected at-mail] Connection unexpectedly closed
#   [disconnected at-auth 421 4.3.0] 421 4.3.0 Service shutting down
#   [socket at-rcpt] Connection reset by peer
#       (the server had greeted us and then ended the connection, at this
#        step of the conversation: helo, starttls, auth, mail, rcpt or
#        data. When the last thing it said in that step was a refusal, its
#        code stands in the tag and the reply is what the row keeps. A row
#        with no "at-" is a hang-up nothing more is known of, or a wait
#        that timed out.)
#   [local] words of our own (a fault before anything was sent)
#   [address josé@x] words of our own (an address SMTP cannot carry)
#   [sender books@x; signed-in] 530 5.7.0 Authentication Required
#       ("; signed-in" ends the tag of a 530 that came after the server had
#        accepted our sign-in: the sentence must not say none is set)
#   [sender not-smtp] The answer was not an SMTP reply: …
#   [data not-smtp after-body] …   [data after-body] 251 …
#       (an answer to the whole message that is neither a yes nor a
#        refusal: the message had been handed over when it came)
#   [recipients  550 5.1.1] : 550 5.1.1 …   (a refused address with nothing
#       in it to name keeps its reply code in the tag all the same)
# Two failures of our own keep the fixed text every release has stored for
# them, with no tag: "SMTP not configured", and the secure-connection
# texts below ("Certificate not verified (host): certificate is
# self-signed").
# Rows of releases before 2.32 hold str() of the exception: Python's tuple
# "(554, b'5.7.1 …')" or dict of refused recipients, or the system's
# "[Errno 111] Connection refused". They are read as well as that allows.
_TAGGED = re.compile(
    r"^\[(recipients|sender|connect|helo|starttls|auth|data|response"
    r"|disconnected|socket|local|address|other)"
    r"(?: ([^\]\n]*))?\] ?(.*)$",
    re.S,
)
_STORED_LIMIT = 2000
# what our tag may take of that, so a long list of addresses can never push
# its end (or all of the server's words) off the row
_ADDRESS_LIMIT = 320
_TAG_REFUSED = 900
_TAG_SENT = 400
# the stages whose stored words are a mail server's reply
_REPLY_STAGES = (
    "recipients",
    "sender",
    "connect",
    "helo",
    "starttls",
    "auth",
    "data",
    "response",
)
# ... but for these, which are words of our own (or smtplib's)
_OUR_AUTH = ("letters", "none-offered")


# What answered was not an SMTP server. smtplib keeps its line without the
# first four characters (where a reply code would stand), so the line is
# shown as the remainder it is, in words of ours.
_NOT_SMTP = "The answer was not an SMTP reply"


def _not_smtp_words(rest) -> str:
    rest = _decoded(rest)
    return f"{_NOT_SMTP}: …{rest}" if rest else _NOT_SMTP


_SIGNED_IN = "signed-in"
_AFTER_BODY = "after-body"
# "at-auth": the step of the conversation at which a server that had
# greeted us ended the connection
_LOST_AT = "at-"
# the steps a connection can be lost at, as the send path names them, and
# the stage a refusal read in each is classified as
_STEP_STAGES = {
    "helo": "helo",
    "starttls": "response",
    "auth": "auth",
    "mail": "sender",
    "rcpt": "response",
    "data": "data",
}


def _timed_out(error) -> bool:
    """Whether a lost connection was a wait that ran out (the server went
    silent; it did not hang up)."""
    return isinstance(error, TimeoutError) or isinstance(
        getattr(error, "__context__", None), TimeoutError
    )


def _lost_tag(lost_at: str, last_reply, error):
    """(tag, refusal) for a connection the server ended after it had
    greeted us: "at-<step>", with the code of the refusal that was the
    last thing read in that step when there was one (then `refusal` is its
    (code, words)). ("", None) when the step is not known, or the wait
    timed out."""
    if lost_at not in _STEP_STAGES or _timed_out(error):
        return "", None
    code = _code(last_reply)
    if _is_reply_code(code) and code >= 400:
        said = last_reply[1] if len(last_reply) > 1 else ""
        found = " ".join(p for p in (str(code), _enhanced(said)) if p)
        return f"{_LOST_AT}{lost_at} {found}", (code, said)
    return _LOST_AT + lost_at, None


def _lost_at(arg: str):
    """An "at-…" tag as (step, code, enhanced status code), or None."""
    tokens = (arg or "").split(" ")
    if not tokens[0].startswith(_LOST_AT):
        return None
    step = tokens[0][len(_LOST_AT) :]
    if step not in _STEP_STAGES:
        return None
    code, enhanced = None, ""
    if len(tokens) > 1 and re.fullmatch(r"\d{3}", tokens[1]):
        code = int(tokens[1])
        if len(tokens) > 2 and _ENHANCED.fullmatch(tokens[2]):
            enhanced = tokens[2]
    return step, code, enhanced


def _tag(text: str):
    """A stored row's (stage, arg, words, signed_in), or None when it
    carries no tag of ours. The one place a tag is taken apart."""
    tagged = _TAGGED.match((text or "").strip())
    if not tagged:
        return None
    stage, arg, words = tagged.groups()
    arg = arg or ""
    signed_in = False
    if arg == _SIGNED_IN:
        arg, signed_in = "", True
    elif arg.endswith("; " + _SIGNED_IN):
        arg, signed_in = arg[: -len("; " + _SIGNED_IN)], True
    return stage, arg, words, signed_in


def _is_not_smtp(arg: str) -> bool:
    """Whether a tag says the answer was not an SMTP reply."""
    return (arg or "").split(" ")[0] == "not-smtp"


def _address(value) -> str:
    """One of our own addresses, fit for the tag."""
    return re.sub(r"[\s\],;]", "", str(value or ""))[:_ADDRESS_LIMIT]


def _fitting(items, limit: int) -> list[str]:
    """The leading items that fit in `limit` characters when joined (the
    first always does)."""
    kept, used = [], 0
    for item in items:
        used += len(item) + (2 if kept else 0)
        if kept and used > limit:
            break
        kept.append(item)
    return kept


def _storable(text: str) -> str:
    """Text every database can keep: a NUL character (a 0x00 byte in a
    server's reply) is one PostgreSQL refuses in text, which would lose
    the row of the failed send. It is replaced as an unreadable byte is."""
    return text.replace("\x00", "\ufffd")


def stored_text(
    error,
    stage: str = "local",
    after_body: bool = False,
    signed_in: bool = False,
    lost_at: str = "",
    last_reply=None,
) -> str:
    """The text an email log row keeps for a failed send, and the only way
    one is made: our tag, then readable text (never str() of an exception,
    which is a Python tuple or dict, an "[Errno …]" or a codec's
    complaint). The server's reply is kept as it was received. `error` is
    the exception, or words of our own for a send that never reached a
    server (`stage` "setup" for "SMTP not configured"). `signed_in`: the
    server had accepted our sign-in before this failure. `lost_at`: the
    step of the conversation that was under way (after the server's
    greeting had been read), and `last_reply` the (code, words) last read
    in that step, for a connection that was then lost."""
    arg = ""
    asks_sign_in = False
    if isinstance(error, MailTLSError) and error.refusal:
        # the server answered STARTTLS with a refusal: its reply is kept
        host, code, said = error.refusal
        stage, arg = "starttls", _address(host)
        if _is_reply_code(code):
            words = _code_words(code, said)
        else:
            # not an SMTP reply: never "-1 ...", never called a reply
            words = _not_smtp_words(said)
    elif isinstance(error, MailTLSError) or (
        isinstance(error, str) and stage == "setup"
    ):
        # our own fixed text, as every release has stored it
        return _storable(_decoded(str(error))[:_STORED_LIMIT])
    elif isinstance(error, str):
        stage, words = "local", _decoded(error)
    elif isinstance(error, MailLocalError):
        stage, arg, words = error.stage, error.kind, _decoded(str(error))
    elif isinstance(error, (smtplib.SMTPRecipientsRefused, RecipientsPartlyRefused)):
        stage = "recipients"
        entries, lines = [], []
        for addr, reply in (error.recipients or {}).items():
            code = _code(reply)
            said = reply[1] if isinstance(reply, tuple) and len(reply) > 1 else ""
            name = _address(addr)
            answered = _reply_words(reply)
            lines.append(f"{name}: {answered}" if answered else name)
            asks_sign_in = asks_sign_in or code == 530
            found = []
            if _is_reply_code(code):
                # an answer that was not an SMTP reply has no code
                found = [p for p in (str(code), _enhanced(said)) if p]
            if name or found:
                # an address with nothing left of it to name still keeps
                # its reply code here (" 550 5.1.1"): the sentence is
                # chosen from our tag alone
                entries.append(" ".join([name] + found))
        arg = ", ".join(_fitting(entries, _TAG_REFUSED))
        delivered = [_address(a) for a in getattr(error, "delivered", ())]
        delivered = _fitting([a for a in delivered if a], _TAG_SENT)
        if arg and delivered:
            arg += "; sent " + ", ".join(delivered)
        words = "\n".join(lines)
    elif isinstance(error, smtplib.SMTPSenderRefused):
        stage = "sender"
        if _is_reply_code(error.smtp_code):
            arg = _address(error.sender)
            words = _code_words(error.smtp_code, error.smtp_error)
            asks_sign_in = error.smtp_code == 530
        else:
            arg, words = "not-smtp", _not_smtp_words(error.smtp_error)
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
        code = error.smtp_code
        if _is_reply_code(code):
            words = _code_words(code, error.smtp_error)
            asks_sign_in = code == 530 and stage in ("data", "response")
        else:
            # smtplib's -1: what answered did not speak SMTP (a web
            # server's port, say). Its line is kept, the "-1" is not.
            arg, words = "not-smtp", _not_smtp_words(error.smtp_error)
        if (
            after_body
            and stage == "data"
            and not (_is_reply_code(code) and code >= 400)
        ):
            # an answer to the whole message that neither took it nor
            # refused it: the message had been handed over when it came
            arg = f"{arg} {_AFTER_BODY}" if arg else _AFTER_BODY
    elif isinstance(error, smtplib.SMTPServerDisconnected):
        # "Connection unexpectedly closed: timed out" is a read that timed
        # out, and nothing closed: the plain cause
        stage = "disconnected"
        words = _plain_cause(_decoded(str(error))) or "Connection closed"
        if after_body:
            arg = _AFTER_BODY
        else:
            # a server that had greeted us and then hung up was reached:
            # the step it hung up at, and the refusal it gave there if it
            # gave one (a 421 and then a closed connection), which the row
            # keeps as the reply it is
            arg, refusal = _lost_tag(lost_at, last_reply, error)
            if refusal:
                words = _code_words(*refusal)
                asks_sign_in = refusal[0] == 530
    elif isinstance(error, OSError) and not isinstance(error, smtplib.SMTPException):
        # "Connection refused", "timed out", "Name or service not known":
        # the system's message, without Python's "[Errno 111]" in front
        said = getattr(error, "strerror", None) or str(error)
        if not said and isinstance(error, TimeoutError):
            said = "timed out"
        stage = "socket"
        words = _plain_cause(_decoded(said)) or "Connection failed"
        if after_body:
            arg = _AFTER_BODY
        elif isinstance(error, ConnectionError):
            # reset or cut by the server after it had greeted us
            arg = _lost_tag(lost_at, None, error)[0]
    elif isinstance(error, smtplib.SMTPException):
        said = str(error)
        last = _LAST_RESPONSE.search(said)
        value = _as_value(last.group(1)) if last else None
        if said in _NO_SIGN_IN_OFFERED:
            # smtplib's own sentence: the server offers no sign-in we can use
            stage, arg, words = "auth", "none-offered", said
        elif _code(value) is not None:
            # "Server AUTH mechanism infinite loop. Last response: (334,
            # b'…')": the reply it gave up on, as readable text
            stage, arg, words = "auth", "loop", _reply_words(value)
        else:
            stage, words = "other", _decoded(said)
    else:
        # anything else is a fault of ours, and not an answer from the
        # mail server. Python's text for it is not for a person (a codec
        # error names a character of what it could not encode), so the row
        # says what it is and where its cause was written.
        stage, words = "local", _UNEXPECTED
    if signed_in and asks_sign_in:
        arg = f"{arg}; {_SIGNED_IN}" if arg else _SIGNED_IN
    text = f"[{stage}{' ' + arg if arg else ''}] {words}"
    if len(text) > _STORED_LIMIT:
        text = text[: _STORED_LIMIT - 1] + "…"
    return _storable(text)


def _tag_recipients(arg: str):
    """A recipients tag as ([(address, code, enhanced)] of the refused,
    [addresses the message still went to])."""
    refused_part, _, sent_part = (arg or "").partition("; sent ")
    refused = []
    for entry in refused_part.split(", "):
        tokens = entry.split(" ")
        name = tokens[0]
        code, enhanced = None, ""
        if len(tokens) > 1 and re.fullmatch(r"\d{3}", tokens[1]):
            code = int(tokens[1])
            if len(tokens) > 2 and _ENHANCED.fullmatch(tokens[2]):
                enhanced = tokens[2]
        if name or code is not None:
            refused.append((name, code, enhanced))
    return refused, [a for a in sent_part.split(", ") if a]


def server_words(stored: str) -> str:
    """A stored row as the readable text an administrator is shown, with
    nothing changed in it: the server's reply ("530 5.7.0 Authentication
    Required", one "who@x: 550 ..." line per refused recipient) or the
    plain cause ("timed out"), our tag left off. A row of a release before
    2.32 is tidied where that is simple (Python's tuple, dict and b'' read
    back as the reply they held, "[Errno 111] " dropped), and is otherwise
    given as it is stored."""
    text = (stored or "").strip()
    if not text:
        return ""
    tagged = _tag(text)
    if tagged:
        stage, _arg, words, _ = tagged
        words = words.strip()
        if stage in ("disconnected", "socket"):
            words = _plain_cause(words)
        return words
    value = _as_value(text)
    if isinstance(value, dict) and value:
        lines = []
        for addr, reply in value.items():
            answered = _reply_words(reply)
            lines.append(f"{addr}: {answered}" if answered else str(addr))
        return "\n".join(lines)
    if _code(value) is not None:
        return _reply_words(value)
    return _plain_cause(text)


def _holds_reply(stored: str) -> bool:
    """Whether a stored row's words are a mail server's reply (not the
    cause of a connection that failed, nor words of our own)."""
    text = (stored or "").strip()
    tagged = _tag(text)
    if tagged:
        stage, arg, words, _ = tagged
        if _is_not_smtp(arg) or (stage == "auth" and arg in _OUR_AUTH):
            return False
        if stage == "recipients":
            # at least one refused address was answered with a reply (its
            # code stands in our tag; a row without one is not read for it)
            return any(c is not None for _, c, _ in _tag_recipients(arg)[0])
        if words.lstrip().startswith(_NOT_SMTP):
            # words of ours: what answered did not speak SMTP
            return False
        if stage == "disconnected":
            # the refusal a server gave before it hung up (its code stands
            # in our tag); any other lost connection keeps a plain cause
            lost = _lost_at(arg)
            return lost is not None and lost[1] is not None
        return stage in _REPLY_STAGES
    value = _as_value(text)
    if isinstance(value, dict):
        return any(_is_reply_code(_code(r)) for r in value.values())
    return _is_reply_code(_code(value))


def explain_send_failure(error_text: str, host: str = "") -> tuple[str, str]:
    """(reason, hint) for a failed send whose stored text carries no code:
    a row of a release before 2.32 that is not a reply.

    `reason` is the stored text made readable (server_words: an
    administrator's); `hint` is one sentence of ours for a common case, or
    "". The mapping is deliberately tiny: username/password (535), a server
    we couldn't reach (refused/timed out/reset/unknown host), and Gmail or
    Google Workspace, which reject an ordinary password and need an app
    password. The words only choose among our own sentences; none of them
    is repeated."""
    reason = server_words(error_text)
    text = (error_text or "").lower()
    hint = ""
    if "535" in text or ("534" in text and _is_google(host, text)):
        # only a 535 says the username or password is wrong; a 534 is
        # Google's way of asking for an app password
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
            "connection reset",
            "connection aborted",
            "forcibly closed",
            "broken pipe",
        )
    ) or _SYSTEM_ERROR.match((error_text or "").strip()):
        # the system's own error for a connection ("[Errno -3] Temporary
        # failure in name resolution", "[SSL: WRONG_VERSION_NUMBER] …")
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
# a refusal about permission (5.7.x: "Relay access denied"), which a server
# gives for a missing sign-in as often as for the address
_NOT_ALLOWED_SENTENCE = (
    "The mail server would not take mail for: {}. If the address is right, "
    "the server may want a sign-in (Settings → Email (SMTP)) or may not allow "
    "mail to it."
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
# the same 530 on a row that keeps no record of whether a sign-in was made
# (a release before 2.32): true whether or not one is set
_SIGN_IN_ASKED_SENTENCE = (
    "The mail server asked for a sign-in before it would take the email. "
    "Check the username and password under Settings → Email (SMTP)."
)
# the same 530 after the server had accepted our sign-in: a sign-in is set
# and was taken, so neither half of the sentence above would be true
_SIGN_IN_AGAIN_SENTENCE = (
    "The mail server accepted the sign-in and then asked for one again, so "
    "the email was not sent. Ask your mail provider why."
)
# the same when it asked only for some of the addresses and a copy went out
_SIGN_IN_AGAIN_FOR_SOME = (
    "The mail server accepted the sign-in and then asked for one again. Ask "
    "your mail provider why."
)
# an address of ours an SMTP envelope cannot carry (found before sending)
_ADDRESS_LETTERS_SENTENCE = (
    "This address has a character that cannot be sent to the mail server (a "
    "letter with an accent, for example), so nothing was sent: {}. Change "
    "the address and send again."
)
_ADDRESS_LETTERS_NO_NAME = (
    "An address has a character that cannot be sent to the mail server (a "
    "letter with an accent, for example), so nothing was sent. Change the "
    "address and send again."
)
# 530 asking for STARTTLS first, or 538: the server takes mail (or this
# sign-in) only over a secure connection
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
# a sign-in the server did not take for a reason other than a wrong
# username or password (only a 535 says that): a mechanism it finds too
# weak, a password it wants changed first, a reply we have no words for
_SIGN_IN_FAILED = "The mail server did not accept the sign-in."
_NO_SIGN_IN_OFFERED_SENTENCE = (
    "The mail server does not offer a sign-in by username and password. "
    "Clear the username under Settings → Email (SMTP) if the server needs "
    "none, or ask your mail provider."
)
_SIGN_IN_LETTERS_SENTENCE = (
    "The SMTP username or password has a character that cannot be sent to "
    "the mail server (a letter with an accent, for example), so nothing was "
    "sent. Change it under Settings → Email (SMTP), or use an app password "
    "from your mail provider."
)
# 552 at MAIL FROM, or 5.3.4 / 5.2.3 at any stage that takes mail: the
# message is over the server's size limit (not a refused From address)
_TOO_BIG_SENTENCE = (
    "The mail server refused the message because it is too large. Send a "
    "smaller attachment, or ask your mail provider about its size limit."
)
# A refusal we have no sentence of its own for: the stage it came at, and
# nothing after it. The server's own words are not part of any sentence.
_CONNECTION_REFUSED = "The mail server refused the connection."
_MESSAGE_REFUSED = "The mail server refused the message."
# the same from a row that does not say which stage (a release before 2.32)
_OLD_REFUSED_SENTENCE = "The mail server refused the email."
_UNREACHABLE_SENTENCE = (
    "The server could not be reached — check the host, port, and the "
    "Use TLS setting."
)
# the server had greeted us as a mail server and then ended the connection
# before the message was handed over: it was reached, so the settings that
# reached it are not what to change
_HUNG_UP_SENTENCE = (
    "The mail server ended the connection before the email was sent. Try "
    "again; if it keeps happening, ask your mail provider."
)
# the whole message was handed to a server we had reached, and the
# connection was lost (or the server went silent) before it answered: it
# may have taken the message, so "could not be reached" would be untrue
# and a second send might be a duplicate
_LOST_AFTER_BODY_SENTENCE = (
    "The connection to the mail server was lost after the message was handed "
    "to it, before it answered, so the email may or may not have gone out. "
    "Check with the recipient before sending it again."
)
# the same, when what came back after the whole message was not a reply
# that says either way
_ANSWER_UNREAD_SENTENCE = (
    "The mail server's answer after the message was handed to it could not "
    "be read, so the email may or may not have gone out. Check with the "
    "recipient before sending it again."
)
# sentences of a send that may have gone out: nothing that reports one
# says flatly that it was not sent
_UNSURE = (_LOST_AFTER_BODY_SENTENCE, _ANSWER_UNREAD_SENTENCE)
_GOOGLE_SENTENCE = (
    "Google will not accept a normal password here. Turn on "
    "2-Step Verification on the account and use a 16-character "
    "app password as the SMTP password."
)
# the row does not say which address
_REFUSED_NO_NAME = (
    "An address was refused by the mail server. Check the addresses and send again."
)
_SENDER_REFUSED_NO_NAME = (
    "The mail server refused the sender address. Check the From address "
    "under Settings → Email (SMTP)."
)
# the To address was refused and a Cc or Bcc was not: the send failed, and
# the copy is said, so nobody reads the log as "nothing went out"
_COPY_WENT_SENTENCE = " A copy still went to: {}."
# beside a refused address, one the server only put off (a 4xx): said too,
# so a deferred To is never left out because a copy was refused outright
_ALSO_LATER_SENTENCE = " The mail server asked to try again later for: {}."
# an address the message did not go to, where the sentence before it does
# not name it: one the server wanted a sign-in for, or gave no usable
# answer for, while a copy went out
_NOT_TAKEN_FOR_SENTENCE = " The mail server did not take the email for: {}."
# the To address took the message and a Cc or Bcc was refused: a sent row's
# note
_COPY_REFUSED_NOTE = "The mail server refused a copy to: {}."
# sentences that do not say why: they end with where the reason can be read
_UNEXPLAINED = (
    _FALLBACK_SENTENCE,
    _OLD_REFUSED_SENTENCE,
    _CONNECTION_REFUSED,
    _MESSAGE_REFUSED,
    _SIGN_IN_FAILED,
)


def _is_google(host: str, text: str) -> bool:
    return any(s in (host or "").lower() for s in ("gmail", "google")) or any(
        s in (text or "").lower() for s in ("gmail", "google")
    )


def _credentials_sentence(host: str, text: str) -> str:
    if _is_google(host, text):
        return _GOOGLE_SENTENCE
    return "The server rejected the username or password — check both."


# -- which sentence --------------------------------------------------------------
#
# A failure is classified by the stage it came at and its reply code (and
# enhanced status code). The server's prose is read in two places only, to
# choose between two fixed sentences: a 530 that asks for STARTTLS, and a
# Google server's refusal of an ordinary password. One function serves the
# exception of a send just made and every stored format, so the two cannot
# drift apart.

_TOO_BIG_CODES = ("5.3.4", "5.2.3")


def _sign_in_kind(words) -> str:
    """A 530: the server wants a sign-in, or STARTTLS first."""
    if "starttls" in _text(words).lower():
        return "starttls"
    return "sign-in"


def _asks_sign_in(words, signed_in: bool) -> str:
    """A 530 at a stage that takes mail."""
    kind = _sign_in_kind(words)
    if kind == "sign-in" and signed_in:
        # a sign-in is set and the server took it
        return "sign-in-again"
    return kind


def _stage_kind(
    stage: str, code, enhanced: str = "", words="", signed_in: bool = False
) -> str:
    """The kind of failure a reply of `code` at `stage` is."""
    if code is None:
        return "other"
    if not 200 <= code <= 599:
        # not an SMTP reply at all
        return "unreachable"
    later = 400 <= code < 500
    if stage == "auth":
        # only a 535 says the username or password is wrong
        if code == 535 or enhanced == "5.7.8":
            return "auth"
        if code == 432 or enhanced == "4.7.12":
            return "sign-in-failed"  # a password the server wants changed
        if later:
            return "later"
        if code == 538 or enhanced == "5.7.11":
            return "starttls"
        if code == 530 and _sign_in_kind(words) == "starttls":
            return "starttls"
        if code == 534:
            return "auth-534"
        return "sign-in-failed"
    if stage in ("connect", "helo"):
        return "later" if later else "connection"
    if later:
        return "later"
    if code == 530:
        return _asks_sign_in(words, signed_in)
    if code == 538 or enhanced == "5.7.11":
        return "starttls"
    if enhanced in _TOO_BIG_CODES:
        return "too-big"
    if code == 552 and stage == "sender":
        # the answer to the size given with MAIL FROM: the message is over
        # the limit. A 552 with another status code says something else,
        # which is still not about the From address.
        return "too-big" if not enhanced else "message"
    if stage == "sender":
        return "sender"
    if stage == "data":
        return "message"
    if code == 535 or enhanced == "5.7.8":
        return "auth"
    if code == 534:
        return "auth-534"
    return "message"


def _refused_kind(refused, words="", signed_in: bool = False):
    """(kind, refused addresses, put-off addresses, addresses with no
    usable answer) for the refused recipients [(address, code, enhanced)]:
    the 5xx addresses are refused, the 4xx ones put off; only 4xx ones
    mean try again later. An address whose answer was not an SMTP reply
    has no code; that, and a code that is no refusal (a 252, which smtplib
    does not take for a yes), is an address the message did not go to and
    nothing more is known of."""
    hard = [(n, c, e) for n, c, e in refused if c is not None and c >= 500]
    later = [str(n) for n, c, _ in refused if c is not None and 400 <= c < 500]
    odd = [str(n) for n, c, _ in refused if c is None or c < 400]
    if hard:
        names = [str(n) for n, _, _ in hard]
        if all(c == 530 for _, c, _ in hard):
            # the server wants a sign-in before it takes a recipient
            return _asks_sign_in(words, signed_in), names, later, odd
        if all(e in _TOO_BIG_CODES for _, _, e in hard):
            # over the size limit (some servers say so at RCPT, from the
            # SIZE the message was announced with): not a bad address
            return "too-big", names, later, odd
        if all(e.startswith("5.7.") for _, _, e in hard):
            return "not-allowed", names, later, odd
        return "refused", names, later, odd
    if later:
        return "later", [], later, odd
    # the server had answered every command before this one, so it was
    # reached: the general sentence, whatever these answers were
    return "other", [], [], odd


class _Read:
    """How a stored failure classifies: its kind, the addresses of ours it
    is about (the refused recipients, or the From address), and the
    addresses the message still went to."""

    __slots__ = ("kind", "names", "delivered", "later", "odd")

    def __init__(self, kind, names=(), delivered=(), later=(), odd=()):
        self.kind = kind
        self.names = [str(n) for n in names if n]
        self.delivered = list(delivered)
        # recipients the server put off (4xx) beside the refused ones
        self.later = [str(n) for n in later if n]
        # recipients whose answer was neither a yes nor a refusal
        self.odd = [str(n) for n in odd if n]


_CODE_FIRST = re.compile(r"(\d{3})(?:[ -]([245]\.\d{1,3}\.\d{1,3}))?(?=\s|$)")


def _untold(kind: str) -> str:
    """The kind of a failure read from a row of a release before 2.32,
    which keeps no record of whether a sign-in was made: a 530 there is a
    sign-in asked for, and no more is known."""
    return "sign-in-asked" if kind == "sign-in" else kind


def _classify_text(text: str) -> _Read:
    text = (text or "").strip()
    tagged = _tag(text)
    if tagged:
        return _classify_tagged(*tagged)
    # a release before 2.32: str() of the exception
    value = _as_value(text)
    if isinstance(value, dict) and value:
        refused = []
        said = []
        for addr, reply in value.items():
            words = reply[1] if isinstance(reply, tuple) and len(reply) > 1 else ""
            refused.append((str(addr), _code(reply), _enhanced(words)))
            said.append(_text(words))
        kind, names, later, odd = _refused_kind(refused, " ".join(said))
        return _Read(_untold(kind), names, later=later, odd=odd)
    code = _code(value)
    if code is None:
        if text in _NO_SIGN_IN_OFFERED:
            return _Read("none-offered")
        return _Read(None)
    message = value[1] if len(value) > 1 else ""
    if len(value) == 3 and isinstance(value[2], str):
        # Python's (code, words, sender) of a refused MAIL FROM
        kind = _untold(_stage_kind("sender", code, _enhanced(message), message))
        return _Read(kind, [value[2]] if kind == "sender" else [])
    kind = _untold(_stage_kind("response", code, _enhanced(message), message))
    if kind == "message":
        # Python's tuple was the same for a refused greeting and a refused
        # message: the stage is lost, so the sentence names neither
        kind = "old-refusal"
    return _Read(kind)


def _classify_tagged(
    stage: str, arg: str, words: str, signed_in: bool = False
) -> _Read:
    if stage == "recipients":
        # the addresses and codes stand in our tag. They are never read
        # from the lines under it, which hold what the server said: a row
        # whose tag names nobody is an address refused, and no more.
        refused, delivered = _tag_recipients(arg)
        if not refused:
            return _Read("refused")
        kind, names, later, odd = _refused_kind(refused, words, signed_in)
        return _Read(kind, names, delivered, later, odd)
    if stage in ("disconnected", "socket"):
        if arg == _AFTER_BODY:
            return _Read("lost-after-body")
        lost = _lost_at(arg)
        if lost is None:
            # nothing answered as a mail server, a wait that timed out, or
            # a row that does not say how far the conversation had got
            return _Read("unreachable")
        step, code, enhanced = lost
        if code is not None and code >= 400:
            # the server refused and then hung up: the refusal decides,
            # as it would have with the connection left open
            return _Read(
                _stage_kind(_STEP_STAGES[step], code, enhanced, words, signed_in)
            )
        return _Read("hung-up")
    if stage in ("local", "other"):
        return _Read("other")
    if stage == "address":
        # addresses of our own, as our tag keeps them
        return _Read("address", [a for a in arg.split(", ") if a])
    if stage == "starttls":
        # whatever the code: no secure connection, so nothing was sent. A
        # 4xx is the server's "not now", not a setting to change.
        found = _CODE_FIRST.match(words.strip())
        if found and found.group(1)[0] == "4":
            return _Read("starttls-later", [arg])
        return _Read("starttls-refused", [arg])
    if stage == "auth" and arg == "letters":
        return _Read("letters")
    if stage == "auth" and arg == "none-offered":
        return _Read("none-offered")
    after_body = arg.split(" ")[-1] == _AFTER_BODY
    if _is_not_smtp(arg):
        if stage == "connect":
            # nothing that spoke SMTP was ever on the line (a web server's
            # port, say): the greeting itself was not a reply
            return _Read("unreachable")
        # the server had been reached and had answered before this: the
        # message may have been taken when it had all been handed over
        return _Read("answer-unread" if after_body else "other")
    found = _CODE_FIRST.match(words.strip())
    code = int(found.group(1)) if found else None
    enhanced = (found.group(2) or "") if found else ""
    if code is not None and code < 400 and stage in ("sender", "data", "response"):
        # a reply that is no refusal, where smtplib wanted another one
        return _Read("answer-unread" if after_body else "other")
    kind = _stage_kind(stage, code, enhanced, words, signed_in)
    if stage == "sender" and kind == "sender":
        # the address in a sender tag is the From address we sent with
        return _Read(kind, [arg])
    return _Read(kind)


# -- the secure connection to the mail server ---------------------------------
#
# send_email() verifies the mail server's certificate on every TLS send (no
# setting turns it off). A failure there is stored on the log row as one of
# the short texts below — never OpenSSL's own words — and read back into
# the sentence a bookkeeper sees. Each says nothing was sent and the
# password was not given: the check runs before LOGIN, so both are true.


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
# the server answered STARTTLS with a 4xx: it cannot set one up just now
_TLS_LATER_SENTENCE = (
    "The mail server could not set up a secure connection just now, so "
    "nothing was sent and your password was not given to it. Try again "
    "later. ({host})"
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


def starttls_refused(error: smtplib.SMTPResponseException, host: str) -> MailTLSError:
    """The failure for a server that answered STARTTLS with a refusal: the
    same sentence as any secure connection that could not be set up, and
    the server's reply kept on the row."""
    failure = MailTLSError(tls_failure_text(error, host))
    failure.refusal = (host, error.smtp_code, error.smtp_error)
    return failure


def no_starttls_text(host: str) -> str:
    return _NO_STARTTLS_TEXT.format(host=host)


def sign_in_needs_tls_text(host: str) -> str:
    return _SIGN_IN_NEEDS_TLS_TEXT.format(host=host)


_HOST = re.compile(r"^[\w.:\[\]-]{1,255}$")
_KNOWN_REASONS = frozenset(_CERT_REASONS.values()) | {_CERT_UNVERIFIED}


def _shown_host(names) -> str:
    """The host a secure-connection sentence names: the one from Settings
    that we wrote on the row, when it reads as a host."""
    host = names[0] if names else ""
    return host if _HOST.match(host or "") else "the mail server"


def tls_sentence(text: str) -> str:
    """The sentence for a stored TLS failure, or "" when it is not one.
    The host is the one from Settings we wrote on the row, and the reason
    one of our own phrases (certificate_reason): nothing a certificate or
    a server supplied is repeated."""
    found = _TLS_STORED.match((text or "").strip())
    if not found:
        return ""
    kind, host, reason = found.groups()
    host = _shown_host([host])
    if kind == "Certificate not verified":
        if reason not in _KNOWN_REASONS:
            reason = _CERT_UNVERIFIED
        return _CERT_SENTENCE.format(host=host, reason=reason)
    if kind == "No secure connection offered":
        return _NO_STARTTLS_SENTENCE.format(host=host)
    if kind == "Sign-in offered only over a secure connection":
        return _SIGN_IN_NEEDS_TLS_SENTENCE.format(host=host)
    return _TLS_SENTENCE.format(host=host)


# -- the sentence ---------------------------------------------------------------


def _reading(error, host: str = ""):
    """(kind, sentence) for a failed send: the exception itself, or the
    text stored on its email log row. The sentence is ours alone: fixed
    words chosen by the stage and the reply code, naming at most the host
    from Settings and the addresses we sent to or from. Those stand in our
    own tag; on a row of a release before 2.32 they are the keys of the
    refused-recipients dict and the sender smtplib was given, read back as
    the values they were. Nothing the server said is ever part of it."""
    if isinstance(error, ssl.SSLCertVerificationError):
        # a certificate is only checked in the handshake, before any login
        error = MailTLSError(tls_failure_text(error, host))
    if isinstance(error, BaseException):
        # an exception reads as the row it would be stored as: one path
        error = stored_text(error)
    text = error or ""
    tls = tls_sentence(text)
    if tls:
        return "tls", tls
    read = _classify_text(text)
    kind = read.kind
    listed = kind in ("refused", "not-allowed")
    # what follows the sentence when some addresses fared differently: who
    # was put off, who the message did not go to though the sentence does
    # not name them, and who a copy still went to. Addresses of ours only.
    tail = ""
    if listed or read.delivered:
        if listed and read.later:
            tail += _ALSO_LATER_SENTENCE.format(", ".join(read.later))
        missed = ([] if listed else read.names) + read.odd
        if missed:
            tail += _NOT_TAKEN_FOR_SENTENCE.format(", ".join(missed))
        if read.delivered:
            tail += _COPY_WENT_SENTENCE.format(", ".join(read.delivered))
    if listed:
        if not read.names:
            return kind, _REFUSED_NO_NAME + tail
        form = _REFUSED_SENTENCE if kind == "refused" else _NOT_ALLOWED_SENTENCE
        return kind, form.format(", ".join(read.names)) + tail
    if kind == "later" and read.later and tail:
        # beside a copy that went: who was put off
        return kind, _ALSO_LATER_SENTENCE.format(", ".join(read.later)).strip() + tail
    if kind == "sender":
        if read.names:
            return kind, _SENDER_REFUSED_SENTENCE.format(read.names[0])
        return kind, _SENDER_REFUSED_NO_NAME
    if kind == "address":
        if read.names:
            return kind, _ADDRESS_LETTERS_SENTENCE.format(", ".join(read.names))
        return kind, _ADDRESS_LETTERS_NO_NAME
    if kind == "starttls-refused":
        return "tls", _TLS_SENTENCE.format(host=_shown_host(read.names))
    if kind == "starttls-later":
        return "tls", _TLS_LATER_SENTENCE.format(host=_shown_host(read.names))
    fixed = {
        "later": _TRY_LATER_SENTENCE,
        "sign-in": _SIGN_IN_SENTENCE,
        "sign-in-again": _SIGN_IN_AGAIN_SENTENCE,
        "sign-in-asked": _SIGN_IN_ASKED_SENTENCE,
        "hung-up": _HUNG_UP_SENTENCE,
        "starttls": _STARTTLS_FIRST_SENTENCE,
        "unreachable": _UNREACHABLE_SENTENCE,
        "old-refusal": _OLD_REFUSED_SENTENCE,
        "connection": _CONNECTION_REFUSED,
        "message": _MESSAGE_REFUSED,
        "setup": _NOT_CONFIGURED_SENTENCE,
        "other": _FALLBACK_SENTENCE,
        "sign-in-failed": _SIGN_IN_FAILED,
        "none-offered": _NO_SIGN_IN_OFFERED_SENTENCE,
        "letters": _SIGN_IN_LETTERS_SENTENCE,
        "too-big": _TOO_BIG_SENTENCE,
        "lost-after-body": _LOST_AFTER_BODY_SENTENCE,
        "answer-unread": _ANSWER_UNREAD_SENTENCE,
    }
    if kind in fixed:
        sentence = fixed[kind]
        if kind == "sign-in-again" and read.delivered:
            # a copy went out: "the email was not sent" would be untrue
            sentence = _SIGN_IN_AGAIN_FOR_SOME
        return kind, sentence + tail
    if kind == "auth":
        return kind, _credentials_sentence(host, text)
    if kind == "auth-534":
        # Google's 534 asks for an app password; anyone else's says only
        # that this sign-in will not do
        if _is_google(host, text):
            return kind, _GOOGLE_SENTENCE
        return "sign-in-failed", _SIGN_IN_FAILED
    # a row of a release before 2.32 with no reply code to go by: its
    # words pick one of our sentences
    _, hint = explain_send_failure(text, host)
    if hint:
        return "hint", hint
    if "not configured" in text.lower():
        return "setup", _NOT_CONFIGURED_SENTENCE
    return "other", _FALLBACK_SENTENCE


def failure_sentence(error, host: str = "") -> str:
    """One sentence a person can act on for a failed send, with nothing of
    the mail server's in it (see _reading). `error` is the exception, or
    stored text handed over by FailureReader (never read a row directly)."""
    return _reading(error, host)[1]


def _reached(stored: str) -> list:
    """The addresses of ours a failed send still went to (the copies the
    server took the message for when it turned the To address away), from
    our own tag. [] for a send that reached nobody."""
    tagged = _tag(stored)
    if not tagged or tagged[0] != "recipients":
        return []
    return _tag_recipients(tagged[1])[1]


def _copy_note(stored: str) -> str:
    """The note of a send that went out though the server refused a copy:
    who, from our own tag. "" for anything else."""
    tagged = _tag(stored)
    if not tagged or tagged[0] != "recipients":
        return ""
    refused, _ = _tag_recipients(tagged[1])
    names = [name for name, _, _ in refused if name]
    return _COPY_REFUSED_NOTE.format(", ".join(names)) if names else ""


# -- reading a stored failure ---------------------------------------------------
#
# FailureReader is the one place the stored text leaves storage, for every
# reader: Recent sends and GET /api/email-log, a document's Activity, Test
# Email, a document's Email and the batch results. Nothing else reads
# EmailLog.error_message (a test holds the app to that), so a new reader
# cannot hand the server's reply to someone it is not for.


class LoggedFailure:
    """A stored send failure as one reader may be told it.

    `sentence`: ours alone, the same for every reader (None for a send
    that went out). `note`: ours alone too, for a send that went out
    though a copy was refused (else None). `reply`: what the row keeps as
    readable text (the mail server's reply as it was received, or the
    plain cause of a failure that was not a reply), for an administrator;
    None for anyone else. `reached`: the addresses of ours a FAILED send
    still went to (a copy the server took when it turned the To address
    away), from our own record of the send; empty when it reached
    nobody."""

    __slots__ = ("sentence", "reply", "admin", "note", "_is_reply", "reached")

    def __init__(
        self, sentence, reply, admin: bool, note=None, is_reply=False, reached=()
    ):
        self.sentence = sentence
        self.reply = reply
        self.admin = admin
        self.note = note
        self._is_reply = is_reply
        self.reached = list(reached)

    @property
    def unexplained(self) -> bool:
        """The sentence does not say why the send failed."""
        return self.sentence in _UNEXPLAINED or self.general

    @property
    def unsure(self) -> bool:
        """The message was handed over and nothing says whether it was
        taken: it may have gone out."""
        return self.sentence in _UNSURE

    @property
    def general(self) -> bool:
        """The sentence is the general one ("The email could not be
        sent."), alone or with who a copy still went to."""
        return (self.sentence or "").startswith(_FALLBACK_SENTENCE)

    @property
    def beyond_general(self) -> str:
        """What the general sentence goes on to say (who a copy went to)."""
        return (self.sentence or "")[len(_FALLBACK_SENTENCE) :].strip()

    def reason(self, pointer: bool = True) -> str:
        """What a reader is told in a reply or a list: the sentence, ending
        with where the reason can be read when it does not say why. Never
        the server's reply, whoever reads."""
        if self.sentence is None:
            return ""
        if pointer and self.unexplained:
            return f"{self.sentence} {where_to_see_why(self.admin)}"
        return self.sentence

    def with_reply(self) -> str:
        """Test Email's answer and nothing else's: the sentence, then the
        mail server's reply as the row keeps it. The sentence alone when
        the reader is not an administrator, or the row holds no reply (a
        connection that failed, a failure of our own)."""
        if self.sentence is None:
            return ""
        if not (self.admin and self._is_reply and (self.reply or "").strip()):
            return self.sentence
        reply = "; ".join(ln.strip() for ln in self.reply.splitlines() if ln.strip())
        if reply[-1] not in ".!?…":
            reply += "."
        return f"{self.sentence} The mail server's reply: {reply}"


class FailureReader:
    """Reads email-log rows for showing. The sentence is the same for
    every reader and holds nothing of the server's. An administrator is
    also given what the row keeps, as it is stored (server_words)."""

    def __init__(self, db):
        from app.services.settings_service import get_setting_raw

        self.host = get_setting_raw(db, "smtp_host") or ""

    def read(self, row, admin: bool) -> LoggedFailure:
        stored = row.error_message or ""
        sentence = note = None
        reached = []
        if row.status == "failed":
            sentence = failure_sentence(stored, self.host)
            reached = _reached(stored)
        elif stored:
            note = _copy_note(stored) or None
        reply = None
        if admin:
            reply = server_words(stored) if stored else row.error_message
        return LoggedFailure(
            sentence, reply, admin, note, _holds_reply(stored), reached
        )


def where_to_see_why(admin: bool) -> str:
    """Where a failed send's reason can be read. Recent sends is an
    administrator's section (recipients and the mail server's replies), so
    anyone else is told who can read it rather than sent to a page they
    cannot open."""
    if admin:
        return "The reason is under Settings → Email (SMTP) → Recent sends."
    return "An administrator can see why under Settings → Recent sends."


def last_send_failure(db, entity_type: str, entity_id, admin: bool):
    """The failure of the send just made for this document as one reader
    may be told it, or None when the newest row is not a failure. Call it
    only after send_email() ran and returned False: every run writes a
    row, so the newest one for the document is that send's."""
    from app.models.email_log import EmailLog

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
        return None
    return FailureReader(db).read(row, admin)


def sends_that_reached_someone(db, rows) -> set:
    """The ids of the email-log `rows` that are failed sends which still
    reached someone: the To address was turned away and a copy took the
    message. Read from our own record of each send, by the one reader. A
    scheduled sender asks this before it sends a failed message again, so
    the copy's reader is not sent it on every run."""
    reader = FailureReader(db)
    return {row.id for row in rows if reader.read(row, admin=False).reached}


def note_for_last_send(db, entity_type: str, entity_id) -> str:
    """ "The mail server refused a copy to: …" when the send just made for
    this document went out though the server refused a Cc or Bcc, else "".
    Call it only after send_email() ran and returned True. Ours alone
    (addresses from our own record of the send), so it is for every
    reader: the answer to a send must not read as if a refused copy
    arrived."""
    from app.models.email_log import EmailLog

    row = (
        db.query(EmailLog)
        .filter(
            EmailLog.entity_type == (entity_type or ""),
            EmailLog.entity_id == (entity_id or 0),
        )
        .order_by(EmailLog.id.desc())
        .first()
    )
    if row is None or row.status != "sent":
        return ""
    return FailureReader(db).read(row, admin=False).note or ""


def sent_answer(db, entity_type: str, entity_id, **more) -> dict:
    """What a send that went out answers with: {"status": "sent", …}, and
    `note` when the server refused a copy."""
    answer = {"status": "sent", **more}
    note = note_for_last_send(db, entity_type, entity_id)
    if note:
        answer["note"] = note
    return answer


def with_notes(result: dict, notes: list) -> dict:
    """A batch result with `notes` added when any send went out though the
    server refused a copy (one "Name: The mail server refused a copy to:
    …" each). A run with none answers exactly as it always has."""
    if notes:
        result["notes"] = list(notes)
    return result


def sentence_for_last_send(db, entity_type: str, entity_id, admin: bool = False) -> str:
    """The reason a batch result gives for the send just made for this
    document, or "" when there is no failed row: the sentence, ending with
    where the reason can be read (for this reader) when it does not say."""
    failure = last_send_failure(db, entity_type, entity_id, admin)
    if failure is None:
        return ""
    if failure.general:
        # "could not be sent" is already said in front of this
        return f"{failure.beyond_general} {where_to_see_why(admin)}".strip()
    return failure.reason()


# what is said of a send before its sentence. A send that may have gone
# out is never opened with a flat "could not be sent".
_MAY_HAVE_BEEN_SENT = "may or may not have been sent."


def failed_send_line(
    db, entity_type: str, entity_id, admin: bool, what: str, ran: bool = True
) -> str:
    """One line of a batch result (or Remind's answer) for the send just
    made for this document: "<what> could not be sent. <the sentence>", or
    "<what> may or may not have been sent. <the sentence>" when the
    message was handed over and nothing says whether it was taken. `ran`
    False: send_email() never ran, so there is no row of this send."""
    failure = last_send_failure(db, entity_type, entity_id, admin) if ran else None
    if failure is not None and failure.unsure:
        return f"{what} {_MAY_HAVE_BEEN_SENT} {failure.sentence}"
    why = sentence_for_last_send(db, entity_type, entity_id, admin) if ran else ""
    return f"{what} could not be sent. {why}".rstrip()


def send_failed_detail(db, entity_type: str, entity_id, admin: bool) -> str:
    """The 502 a document's Email button answers with when the send failed:
    what went wrong in a sentence, else the advice to check the SMTP
    settings (an administrator's), then where the reason can be read.
    Given as written: it names addresses of ours, which are not prose for
    the company's vocabulary to reword."""
    failure = last_send_failure(db, entity_type, entity_id, admin)
    if failure is not None and failure.unsure:
        # it may have gone out, and the sentence is the whole reason:
        # there is nothing more to read elsewhere
        return AsWritten(f"Email {_MAY_HAVE_BEEN_SENT} {failure.sentence}")
    detail = "Email could not be sent. "
    if failure is not None and not failure.general:
        detail += failure.reason(pointer=False) + " "
    else:
        if failure is not None and failure.beyond_general:
            detail += failure.beyond_general + " "
        if admin:
            detail += "Check the SMTP settings under Settings → Email (SMTP). "
    return AsWritten(detail + where_to_see_why(admin))


def failed_test_email_detail(db, admin: bool, host: str = "") -> str:
    """What Test Email answers when the send failed: the sentence, then
    (Test Email is an administrator's) the mail server's reply as it was
    received, then where the list of sends is. Given as written: an
    address and the server's reply are shown as they are, never reworded
    by the company's vocabulary."""
    failure = last_send_failure(db, "settings_test", None, admin)
    if failure is None:
        said = failure_sentence("", host)
    else:
        said = failure.with_reply()
    if failure is not None and failure.unsure:
        detail = f"Test email {_MAY_HAVE_BEEN_SENT} {said} "
    else:
        detail = f"Test email failed to send. {said} "
    if admin:
        detail += "The full list is under Settings → Email (SMTP) → Recent sends."
    else:
        detail += where_to_see_why(False)
    return AsWritten(detail)
