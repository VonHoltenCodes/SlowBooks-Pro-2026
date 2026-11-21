# ============================================================================
# Connect to AI assistant from Settings (#1087; "Connect Claude" until
# 2.32.0's rename): SlowBooks Pro holds the line to the
# SlowBooks relay itself, with SlowBooks Switchboard's own line client
# (app/switchboard, vendored), so Claude on the web or a phone, and Grok,
# can work the books with no second program and no tunnel.
#
#   connect()     links this install to the relay (no account: no email, no
#                 password; the relay answers a random device credential),
#                 makes a bookkeeper API token "AI assistant (relay)" the way
#                 Settings, API Tokens makes one, keeps both in one file in
#                 the data folder readable by this user only, and opens the
#                 line in a background thread
#   new_code()    a one-time sign-in code from the relay (XXXX-XXXX-XXXX,
#                 ten minutes, once), typed on the relay's sign-in page when
#                 the assistant asks
#   status()      what the panel says
#   disconnect()  closes the line, has the relay delete its record (or, out
#                 of reach, says the relay name to send to support), revokes
#                 the token
#                 and deletes the file
#
# Off the books' critical path: the line is a daemon thread that dials out
# (nothing listens), started after the app has started; it reaches the
# books only through the app's own API with the token, like any agent. A
# relay that is down, slow or gone is caught, logged without secrets, and
# retried by the line client's own backoff (1 s doubling to 60 s); no
# request to the books waits on it.
#
# THE RULE for every call to the relay and everything kept here because of
# one. A change to this file keeps all six, as a design:
#
#  1. One deadline, PRESS_TIMEOUT (about ten seconds), covers a whole press,
#     from the first connection to the last byte read, however the relay
#     behaves: silence after the headers, an answer trickled a byte at a
#     time, an answer with no end (its size is capped at MAX_ANSWER). A
#     press that makes two calls shares the one deadline. The name lookup
#     alone is outside it (the stated limit).
#  2. Every relay answer is classified once, by _relay_post, into an Answer;
#     _relay_post never raises, whatever came back (an answer nested too
#     deep to decode, or anything else it trips on, is ODD).
#     The expected 2xx carrying the expected JSON fields is OK. Everything
#     else is one of four, each with one fixed sentence: AWAY (no answer at
#     all), REFUSED (a 4xx: said so, and not asked again by itself), BUSY
#     (a 5xx or a 429: busy, or just updated) and ODD (anything that is not
#     an answer to what was asked: malformed HTTP, a cut-off or oversized
#     body, an HTML page, missing fields, a redirect). The routes never
#     answer an HTTP 500, an address with a bare status code, or exception
#     text; the detail goes to the log, with no credential in it. The
#     relay's own sentence for a refusal is passed on as plain text, one
#     line, bounded (the panel escapes it).
#  3. "The relay deleted its record of these books" is said only when the
#     relay's answer says so ("deleted": true). Any other answer keeps the
#     pending-deletion note, and the retry where a retry can help.
#  4. Local state is never half-made. A Connect that fails at any step
#     (keeping the file included) leaves no token and no file here and, if
#     the relay had linked, has the relay delete that record or keeps a
#     pending-deletion note for its relay name (a link answer that is not
#     a whole one but carries a credential counts as linked). Disconnect
#     always revokes the token and clears the file, a damaged or unreadable
#     file included; the token is the one whose secret the file holds,
#     whatever it has been renamed to. When the relay name or the credential
#     can no longer be read it says plainly that the relay's own record
#     could not be removed and what to give support (never a secret).
#     Disconnect's steps each say what they did: when one cannot be done
#     (the file cannot be removed, the token cannot be revoked) the answer
#     says what was done and what was not, never "nothing was changed". A
#     file that cannot be removed is written over where it lies with
#     "ended": no credential and no token stay in it, it reads as no
#     connection, and the next Connect takes it away.
#  5. One Connect or Disconnect at a time: a second press while one is
#     being worked on is refused in a sentence, and the connection file is
#     only ever made new (never over another), so two presses, or two
#     server processes, make one relay record and one token.
#  6. (The panel, app/static/js/settings.js: a timed refresh never moves
#     keyboard focus or clears a selection, and no two buttons share a
#     name.)
#
# One link per install, tied to the company it was made in. The line is up
# only while that company is open here: on the desktop, the company the app
# was started for; on a Server Edition host serving several companies, any
# company it serves (the token's requests name the company). A sign-in to
# another company sees "not connected" and the company it belongs to, and
# cannot use or end it from there.
# ============================================================================

import http.client
import json
import logging
import os
import socket
import re
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from app.switchboard import __version__ as SWITCHBOARD_VERSION
from app.switchboard import account, line as line_client, logs as sb_logs
from app.switchboard.client import ApiClient, urllib_transport
from app.switchboard.config import Config, ConfigError
from app.switchboard.server import INSTRUCTIONS, Mcp, build_registry

logger = logging.getLogger(__name__)

# The relay, one constant; SLOWBOOKS_RELAY_URL points the tests at a fake.
RELAY_URL = "https://relay.sbplive.com"
RELAY_ENV = "SLOWBOOKS_RELAY_URL"
MCP_PATH = "/mcp"
TERMS_URL = "https://www.slowbookspro.com/terms/"
PRIVACY_URL = "https://www.slowbookspro.com/privacy/"
LINK_FILE = "claude-relay.json"
TOKEN_LABEL = "AI assistant (relay)"
DEVICE_NAME = "SlowBooks Pro"
# Under the relay's 110 seconds for a tool call, so the answer is the app's.
API_TIMEOUT = 100.0
# The line client's timings; the tests shorten them.
TIMINGS = line_client.DEFAULT_TIMINGS

NOT_LINKED = (
    "No AI assistant is connected. An administrator can press Connect to AI "
    "assistant."
)
OTHER_COMPANY = (
    "The AI assistant is connected to {name}, not to this company. Open {name} to "
    "show a sign-in code or to disconnect it."
)
ALREADY = "An AI assistant is already connected to this company."
UNREACHABLE = "SlowBooks Pro keeps trying on its own; the books work as before."
# What a press says when the relay does not give what was asked (rule 2):
# one sentence for each class of answer, never the OS's error text, an
# address or a status code; the detail goes to the log (no credential,
# token or code is in it).
# AWAY: no answer at all (W-208)
RELAY_AWAY = (
    "The relay could not be reached. SlowBooks Pro is working normally; try "
    "again in a few minutes."
)
# BUSY: a 5xx (the relay was just updated: it restarts for a moment) or a 429
RELAY_BUSY = "The relay was just updated or is busy. Try again in a couple of minutes."
# REFUSED: a 4xx; the relay's own sentence follows when it gave one
RELAY_REFUSED = (
    "The relay did not accept the request. SlowBooks Pro is working normally; "
    f"if this keeps happening, write to {account.SUPPORT}."
)
# ODD: what came back is not an answer to what was asked
RELAY_ODD = (
    "The relay's answer could not be read. SlowBooks Pro is working normally; "
    "try again in a few minutes, and if it keeps happening write to "
    f"{account.SUPPORT}."
)
# a second Connect or Disconnect while one is being worked on (rule 5)
PRESS_BUSY = (
    "The AI assistant connection is still being worked on from another press. "
    "Try again in a moment."
)
# Connect could not keep what it made (the token, the file): nothing is left
NOT_KEPT = (
    "SlowBooks Pro could not keep the connection in its data folder, so "
    "nothing was connected. Check that the data folder can be written to, "
    "then try again."
)
NOT_WRITTEN = (
    "SlowBooks Pro could not write to its data folder, so nothing was changed. "
    "Check that the data folder can be written to, then try again."
)
# Disconnect did what it could and one step could not be done (rule 4): said
# after what was done, never in place of it
FILE_KEPT = (
    " SlowBooks Pro could not remove the connection from its data folder. "
    "Check that the data folder can be written to, then press Disconnect again."
)
TOKEN_KEPT = (
    ' Its token could not be revoked: revoke "{label}" in Settings, API Tokens.'
)
# a fault of our own on one of these routes: a sentence, never an HTTP 500
UNFINISHED = (
    "SlowBooks Pro could not finish that. The books are not affected; try "
    f"again, and if it keeps happening write to {account.SUPPORT}."
)
# the most of an answer that is read; the relay's answers are a few hundred
# bytes, and more than this is not one of them
MAX_ANSWER = 64 * 1024
# How long a press waits on the relay, for the whole press: every address
# the relay's name has, the connection, TLS and the whole answer, to its
# last byte, of every call the press makes (rule 1). The relay answers
# these small requests in well under a second; ten seconds still allows a
# slow or busy connection, where the vendored client's 30 seconds, or
# Windows' own ~21-second connect timeout when a firewall drops the packets,
# left "Connecting…" up for half a minute, and a limit for each address in
# turn waited 40 s on a name with four (NEW-270). The background line is not
# on this path: it keeps its own backoff (1 s doubling to 60 s).
PRESS_TIMEOUT = 10.0
DAMAGED = (
    "The AI assistant connection kept in the data folder cannot be read. "
    "Disconnect, then connect again."
)

# The vendored client logs to "sbplive"; its own filter takes credentials,
# tokens and bearer values out of every line it logs.
logging.getLogger(sb_logs.LOGGER).addFilter(sb_logs._MaskSecrets())


class SwitchboardError(Exception):
    """A refusal or failure; .sentence is for the person, .status for HTTP."""

    def __init__(
        self,
        status: int,
        sentence: str,
        *,
        unreadable: bool = False,
        unreachable: bool = False,
        busy: bool = False,
    ):
        super().__init__(sentence)
        self.status = status
        self.sentence = sentence
        # the kept file itself cannot be used (Disconnect forgets it)
        self.unreadable = unreadable
        # the relay did not answer at all (Disconnect's fallback)
        self.unreachable = unreachable
        # the relay answered a server error (just updated, or busy)
        self.busy = busy


# -- where things are -------------------------------------------------------------


def relay_url() -> str:
    return account.check_relay_url(os.environ.get(RELAY_ENV) or RELAY_URL)


def mcp_address(relay: str | None = None) -> str:
    return (relay or relay_url()) + MCP_PATH


def link_path() -> Path:
    from app.services.company_service import data_dir

    return data_dir() / LINK_FILE


def _app_base_url() -> str:
    """This app's own address, for the token's requests: loopback unless it
    listens on one address only."""
    host = (os.environ.get("APP_HOST") or "127.0.0.1").strip()
    port = (os.environ.get("APP_PORT") or "3001").strip()
    if host in ("", "0.0.0.0"):
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}"


def _company_here() -> str:
    """The company file this request works in ("" for a database that is
    not a file, as on PostgreSQL)."""
    from app.services import served_companies

    return served_companies.current_file() or ""


def _company_open(company_file: str) -> bool:
    """Whether the company a link was made in is open in this app now."""
    from app.services import served_companies

    if company_file == (served_companies.started_file() or ""):
        return True
    return served_companies.enabled() and served_companies.is_served(company_file)


# -- the file ---------------------------------------------------------------------


def _from_json(text: str):
    """json.loads whose every failure is a ValueError: text nested too deep
    to decode raises RecursionError, which is not one, and must not get
    past the places that read a file or an answer (rule 2)."""
    try:
        return json.loads(text)
    except RecursionError:
        raise ValueError("nested too deep") from None


_FIELDS = (
    "relay",
    "device",
    "account",
    "slug",
    "credential",
    "token",
    "token_id",
    "token_label",
    "company_file",
    "company_name",
    "linked_at",
)


def _write_tmp(path: Path, text: str) -> Path:
    """The text in a file beside ``path``, readable and writable by this
    user only from the first byte (0600 on macOS and Linux; on Windows the
    data folder sits in the user's own profile, whose access list is the
    protection)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        _unlink_quietly(tmp)
        raise
    return tmp


def _unlink_quietly(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _write_private(path: Path, text: str) -> None:
    """The file written whole and private, then moved over any earlier
    one."""
    tmp = _write_tmp(path, text)
    try:
        os.replace(tmp, path)
    except BaseException:
        _unlink_quietly(tmp)
        raise
    if os.name != "nt":
        os.chmod(path, 0o600)


class _AlreadyKept(Exception):
    """A link is kept already: a new one is not written over it."""


def _write_private_new(path: Path, text: str) -> None:
    """The file written whole and private, only if there is none yet:
    _AlreadyKept when there is (rule 5: two Connects, in one process or
    two, make one file). Never a half-written file under the name."""
    tmp = _write_tmp(path, text)
    try:
        try:
            # a second name for the finished file: all of it or none of it
            os.link(tmp, path)
            return
        except FileExistsError:
            raise _AlreadyKept() from None
        except OSError:
            pass  # a folder that cannot hold a second name: made in place
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise _AlreadyKept() from None
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            _unlink_quietly(path)
            raise
    finally:
        _unlink_quietly(tmp)


def _record_text(record: dict) -> str:
    return json.dumps(record, indent=2) + "\n"


def _save(record: dict) -> None:
    """Keep a changed record over the one kept (the tests; nothing in the
    product changes a kept link)."""
    _write_private(link_path(), _record_text(record))


def _save_new(record: dict) -> None:
    """Keep a new link: _AlreadyKept when one is kept already."""
    _write_private_new(link_path(), _record_text(record))


def _usable_relay(value) -> bool:
    """Whether a kept relay address is one a call can be made to: what
    Connect itself accepts, no more."""
    if not isinstance(value, str):
        return False
    try:
        return account.check_relay_url(value) == value
    except ConfigError:
        return False


def _whole(record) -> bool:
    """Whether what the file holds is a link as Connect keeps one."""
    return (
        isinstance(record, dict)
        and all(isinstance(record.get(f), str) for f in _FIELDS if f != "token_id")
        and isinstance(record.get("token_id"), int)
        and not isinstance(record.get("token_id"), bool)
        and _usable_relay(record["relay"])
    )


def _load() -> dict | None:
    """The kept link, None when there is none; SwitchboardError when it
    cannot be used (another user can read it, or it is damaged)."""
    path = link_path()
    if not path.is_file():
        return None
    try:
        open_to_others = os.name != "nt" and os.stat(path).st_mode & 0o077
    except OSError:
        raise SwitchboardError(409, DAMAGED, unreadable=True) from None
    if open_to_others:
        raise SwitchboardError(
            409,
            "The AI assistant connection kept in the data folder can be read by "
            "other "
            "users of this computer. Disconnect, then connect again.",
            unreadable=True,
        )
    try:
        record = _from_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise SwitchboardError(409, DAMAGED, unreadable=True) from None
    if _ended(record):
        return None  # what a Disconnect left when it could not remove the file
    if not _whole(record):
        raise SwitchboardError(409, DAMAGED, unreadable=True)
    return record


def _salvage() -> dict:
    """What can still be read of a kept file that _load refuses, for
    Disconnect (rule 4): the file as it is, whatever its mode, or {} when
    it is not a JSON object. Each value is checked where it is used."""
    try:
        record = _from_json(link_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def _forget() -> None:
    try:
        link_path().unlink()
    except FileNotFoundError:
        pass


# What is left in a connection file that Disconnect could not remove: no
# link, no credential, no token. _load reads it as no connection, and the
# next Connect takes it away.
_ENDED_TEXT = '{"v": 1, "ended": true}\n'


def _ended(record) -> bool:
    return (
        isinstance(record, dict)
        and record.get("ended") is True
        and "credential" not in record
        and "token" not in record
    )


def _end_in_place() -> bool:
    """Write "ended" over a connection file that cannot be removed (a data
    folder that cannot be written to still lets this user's own file be
    written): the credential and the token no longer lie in it, and it no
    longer reads as a connection. Whether that was done."""
    try:
        fd = os.open(link_path(), os.O_WRONLY | os.O_TRUNC)
    except OSError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_ENDED_TEXT)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        return False
    return True


def _clear_ended() -> None:
    """Take away the "ended" file an earlier Disconnect left, before a new
    link is kept: OSError when the folder still cannot be written to. Moved
    aside first and looked at there, so a link another server process kept
    in the same moment is never the file removed (rule 5): it is put back,
    and _AlreadyKept says so."""
    path = link_path()
    if not path.is_file():
        return
    aside = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.ended")
    try:
        os.rename(path, aside)
    except FileNotFoundError:
        return
    try:
        record = _from_json(aside.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        record = None
    if _ended(record):
        _unlink_quietly(aside)
        return
    try:
        os.link(aside, path)
    except FileExistsError:
        pass
    except OSError:
        os.rename(aside, path)
        raise _AlreadyKept() from None
    _unlink_quietly(aside)
    raise _AlreadyKept()


# -- a deletion the relay has not heard yet (NEW-269) --------------------------------
#
# Disconnect with the relay out of reach undoes everything here, but the
# relay still holds its record. What deleting it needs is kept in a small
# file of its own, user-only like the link file: the relay, the relay name
# (for the panel's sentence and for support) and the device credential (the
# forget call's bearer; it can do nothing else once the token is revoked and
# the link file is gone). A quiet retry with a bounded backoff, off the
# books' path, tells the relay when it is back, then deletes the entry. The
# panel says the name until then (Dismiss hides it; the retry goes on). A
# new Connect is not held up by it.
#
# Each entry says why the relay still holds the record (rule 3: any answer
# but a deletion keeps the note), and the notice says the same:
#   away         the relay could not be reached
#   unconfirmed  it answered, but not that it deleted the record
#   refused      it answered a refusal (a 4xx): asking again cannot help, so
#                it is not retried on the timer; one try at each start of
#                the app, in case support has removed the record since
# and whether a Connect that could not be finished left it ("connect"),
# where there was never a link here to unlink.

PENDING_FILE = "claude-relay-pending.json"
# The retry's backoff: first try after RETRY_FIRST seconds, doubling to
# RETRY_MAX; the tests shorten both.
RETRY_FIRST = 30.0
RETRY_MAX = 30 * 60.0
_PENDING_FIELDS = ("relay", "slug", "credential")
WHY_AWAY, WHY_UNCONFIRMED, WHY_REFUSED = "away", "unconfirmed", "refused"

_pending_lock = threading.Lock()
_retry_thread = None
_retry_wake = threading.Event()
_retry_stop = threading.Event()
# relay names whose refusal has been heard since the app started: not asked
# again until the next start
_refused_heard: set = set()


def pending_path() -> Path:
    return link_path().with_name(PENDING_FILE)


def _load_pending() -> list:
    path = pending_path()
    try:
        data = _from_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError, ValueError):
        logger.warning("the pending relay deletions file cannot be read")
        return []
    items = data.get("pending") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [
        item
        for item in items
        if isinstance(item, dict)
        and all(isinstance(item.get(f), str) and item[f] for f in _PENDING_FIELDS)
    ]


def _save_pending(items: list) -> None:
    if not items:
        try:
            pending_path().unlink()
        except FileNotFoundError:
            pass
        return
    _write_private(
        pending_path(), json.dumps({"v": 1, "pending": items}, indent=2) + "\n"
    )


def _why(item: dict) -> str:
    why = item.get("why")
    return why if why in (WHY_UNCONFIRMED, WHY_REFUSED) else WHY_AWAY


def _add_pending(record: dict, why: str = WHY_AWAY, connect: bool = False) -> None:
    with _pending_lock:
        items = [i for i in _load_pending() if i["slug"] != record["slug"]]
        items.append(
            {
                "relay": record["relay"],
                "slug": record["slug"],
                "credential": record["credential"],
                "since": datetime.now(timezone.utc).isoformat(),
                "dismissed": False,
                "why": why,
                "connect": connect,
            }
        )
        _save_pending(items)
        if why == WHY_REFUSED:
            _refused_heard.add(record["slug"])
    _start_retry()


def pending_sentence(
    slug: str, why: str = WHY_AWAY, *, connect: bool = False, kept: bool = True
) -> str:
    """The notice for a record the relay still holds: why it does, the
    relay name, and what happens next. SlowBooks Pro keeps trying by itself
    (NEW-269), so the name is there in case support is ever needed, not as a
    step for the person to take. The exceptions say to send the name: a
    refusal (asking again cannot help), and a note that could not be kept
    (``kept`` False: nothing will retry)."""
    name = slug or "(unknown)"
    retried = kept and why != WHY_REFUSED
    then = (
        "SlowBooks Pro will keep trying to remove that record itself; the name "
        f"is here in case you ever need to ask {account.SUPPORT} about it."
        if retried
        else f"Send that name to {account.SUPPORT} to have it removed."
    )
    if connect:
        said = (
            " The relay did not accept the request to delete it."
            if why == WHY_REFUSED
            else ""
        )
        return (
            "A connection that could not be finished left a record on the relay "
            f"under relay name {name}.{said} {then}"
        )
    if why == WHY_REFUSED:
        return (
            "This computer is unlinked here, but the relay did not accept the "
            "request to delete its record, so it still holds one under relay "
            f"name {name}. {then}"
        )
    if why == WHY_UNCONFIRMED:
        return (
            "This computer is unlinked here, but the relay did not confirm that "
            "it deleted its record, so it may still hold one under relay name "
            f"{name}. {then}"
        )
    return (
        "This computer is unlinked here, but the relay could not be reached, "
        f"so it still holds a record under relay name {name}. {then}"
    )


def pending_notices() -> list:
    """The relay names still held on the relay, for the panel (never the
    credential)."""
    with _pending_lock:
        items = _load_pending()
    return [
        {
            "slug": i["slug"],
            "sentence": pending_sentence(
                i["slug"], _why(i), connect=i.get("connect") is True
            ),
            "dismissed": bool(i.get("dismissed")),
        }
        for i in items
    ]


def dismiss_pending(slug: str) -> dict:
    """Hide the panel's notice for this relay name; the retry goes on."""
    with _pending_lock:
        items = _load_pending()
        for i in items:
            if i["slug"] == slug:
                i["dismissed"] = True
        try:
            _save_pending(items)
        except OSError as exc:
            logger.warning("the pending relay deletions file was not written: %s", exc)
            raise SwitchboardError(503, NOT_WRITTEN) from None
    return {"pending": pending_notices()}


def _tell_relay_forget(item: dict) -> bool:
    """One try at the deletion: True when the relay no longer holds the
    record (or never will delete it: a relay from before /v1/forget), False
    when it still does."""
    return _relay_forget(item["relay"], item["credential"]).fate in _TOLD


def _waits(item: dict) -> bool:
    """Whether the timer tries this entry again: not a refusal already
    heard since the app started."""
    return not (_why(item) == WHY_REFUSED and item["slug"] in _refused_heard)


def retry_pending_once() -> int:
    """Try every pending deletion once; returns how many are left to try
    (an entry the relay refused stays kept, and is not counted)."""
    with _pending_lock:
        items = [i for i in _load_pending() if _waits(i)]
    fates = {i["slug"]: _relay_forget(i["relay"], i["credential"]).fate for i in items}
    deleted = sorted(s for s, f in fates.items() if f == DELETED)
    gone = sorted(s for s, f in fates.items() if f in _TOLD and f != DELETED)
    refused = sorted(s for s, f in fates.items() if f == REFUSED)
    with _pending_lock:
        left = [i for i in _load_pending() if fates.get(i["slug"]) not in _TOLD]
        for i in left:
            if i["slug"] in refused:
                i["why"] = WHY_REFUSED
        _refused_heard.update(refused)
        try:
            _save_pending(left)
        except OSError as exc:
            logger.warning("the pending relay deletions file was not written: %s", exc)
        waiting = sum(1 for i in left if _waits(i))
    # WARNING, not INFO: the served app sets no handler of its own, so only
    # WARNING and up reach launcher.log, and support reads these lines there
    # (W-212). The relay names only: no credential, no token.
    if deleted:
        logger.warning(
            "the relay deleted %d record(s) left by a disconnect (relay name %s)",
            len(deleted),
            ", ".join(deleted),
        )
    if gone:
        logger.warning(
            "the relay no longer holds %d link(s) left by a disconnect (relay "
            "name %s)",
            len(gone),
            ", ".join(gone),
        )
    if refused:
        logger.warning(
            "the relay refused to delete %d record(s) left by a disconnect "
            "(relay name %s); not asked again until SlowBooks Pro starts again",
            len(refused),
            ", ".join(refused),
        )
    return waiting


def _retry_loop() -> None:
    global _retry_thread
    delay = RETRY_FIRST
    try:
        while True:
            _retry_wake.wait(delay)
            _retry_wake.clear()
            if _retry_stop.is_set():
                return
            try:
                left = retry_pending_once()
            except Exception as exc:
                _log_fault("a pending relay deletion could not be tried", exc)
                left = 1
            if not left:
                return
            delay = min(delay * 2, RETRY_MAX)
    finally:
        with _pending_lock:
            _retry_thread = None
            more = not _retry_stop.is_set() and any(_waits(i) for i in _load_pending())
        # an entry added while this one was ending gets its own loop
        if more:
            _start_retry()


def stop_retry(timeout: float = 5.0) -> None:
    """End the quiet retry (the app's shutdown, the tests); the entries stay
    kept for the next start."""
    _retry_stop.set()
    _retry_wake.set()
    with _pending_lock:
        made = _retry_thread
    if made is not None:
        made.join(timeout)
    _retry_stop.clear()


def _start_retry() -> None:
    global _retry_thread
    with _pending_lock:
        if _retry_thread is not None:
            return
        _retry_thread = threading.Thread(
            target=_retry_loop, name="claude-relay-forget", daemon=True
        )
        _retry_thread.start()


def new_start() -> None:
    """The app has started: a refusal heard in an earlier run is asked once
    more in this one."""
    with _pending_lock:
        _refused_heard.clear()


# -- the line -----------------------------------------------------------------------

_lock = threading.Lock()
_line = None  # the Line while one runs
_line_device = ""  # whose line it is


def _transport(record: dict):
    """The token's requests to this app, naming the company it was made in."""
    config = Config(_app_base_url(), record["token"], API_TIMEOUT)
    send = urllib_transport(config)
    company = record.get("company_file") or ""

    def with_company(method, path, params, body, headers):
        extra = dict(headers or {})
        if company:
            extra["X-SlowBooks-Company"] = company
        return send(method, path, params, body, extra)

    return with_company


def _make_line(record: dict):
    from app import __version__ as app_version

    made = account.Link(
        record["relay"], record["device"], record["account"], record["slug"], ""
    )
    mcp = Mcp(build_registry(ApiClient(_transport(record))), INSTRUCTIONS)
    return line_client.Line(
        made.line_url,
        record["device"],
        record["credential"],
        app_url=_app_base_url(),
        mcp=mcp,
        app_version=app_version,
        # Claude's door on; the browser door (people's browsers through
        # the relay) stays off: Connect to AI assistant is for the assistant.
        switches={"mcp": True, "http": False},
        timings=TIMINGS,
    )


def _run(made) -> None:
    try:
        end = made.run()
        logger.info("Claude relay line ended: %s", end)
    except Exception:  # never let the line take anything else down
        logger.exception("Claude relay line failed")


def _start(record: dict) -> None:
    global _line, _line_device
    with _lock:
        if _line is not None and _line_device == record["device"]:
            return
        old = _line
        _line = _make_line(record)
        _line_device = record["device"]
        made = _line
    if old is not None:
        old.stop()
    threading.Thread(
        target=_run, args=(made,), name="claude-relay", daemon=True
    ).start()


def stop() -> None:
    """Close the line, if one is open (the app's shutdown, Disconnect)."""
    global _line, _line_device
    with _lock:
        old, _line, _line_device = _line, None, ""
    if old is not None:
        try:
            old.stop()
        except Exception:
            logger.exception("closing the Claude relay line failed")


def start_in_background() -> None:
    """Called once the app has started: when a link is kept and its company
    is open here, open the line. Returns at once; nothing here can fail the
    start."""

    def begin():
        new_start()
        try:
            if _load_pending():
                _start_retry()
        except Exception:
            logger.exception("pending relay deletions not retried")
        try:
            record = _load()
            if record is None or record.get("on") is False:
                return
            if not _company_open(record["company_file"]):
                logger.info(
                    "Claude relay line not opened: its company is not open here"
                )
                return
            _start(record)
        except SwitchboardError as exc:
            logger.warning("Claude relay line not opened: %s", exc.sentence)
        except Exception:
            logger.exception("Claude relay line not opened")

    threading.Thread(target=begin, name="claude-relay-start", daemon=True).start()


# -- what the panel shows -----------------------------------------------------------


def _line_state(device: str) -> tuple:
    """(state, sentence) of the line for this device."""
    with _lock:
        made = _line if _line_device == device else None
    if made is None:
        return "not_connected", ""
    st = made.status
    if st.state == "connected":
        return "connected", ""
    if st.state == "waiting" or (st.state == "connecting" and made.waits):
        return "unreachable", UNREACHABLE
    if st.state == "connecting":
        return "connecting", ""
    if st.state == "refused":
        return (
            "refused",
            "The relay no longer accepts this link. Disconnect, then connect again.",
        )
    if st.state == "replaced":
        return (
            "refused",
            "Another computer took this link's line. Disconnect, then connect again.",
        )
    return "not_connected", ""


def status() -> dict:
    out = {
        "state": "not_connected",
        "sentence": "",
        "linked": False,
        "this_company": False,
        "company": "",
        "address": "",
        "slug": "",
        "token_label": "",
        "terms": TERMS_URL,
        "privacy": PRIVACY_URL,
        # relay names a Disconnect could not delete yet (NEW-269)
        "pending": pending_notices(),
    }
    try:
        record = _load()
    except SwitchboardError as exc:
        out.update(state="damaged", sentence=exc.sentence, linked=True)
        return out
    if record is None:
        return out
    here = record["company_file"] == _company_here()
    out.update(
        linked=True,
        this_company=here,
        company=record["company_name"],
        token_label=record["token_label"],
    )
    if not here:
        out["sentence"] = OTHER_COMPANY.format(name=record["company_name"])
        return out
    state, sentence = _line_state(record["device"])
    out.update(
        state=state,
        sentence=sentence,
        address=mcp_address(record["relay"]),
        slug=record["slug"],
    )
    return out


# -- a call to the relay: one deadline, one classification ---------------------------

_SHUT_RDWR = socket.SHUT_RDWR


def _log_fault(what: str, exc: BaseException) -> None:
    """A fault of our own in the log: what it was and where, without the
    exception's own text (which can quote a value) and so with no secret."""
    where = "".join(traceback.format_tb(exc.__traceback__))
    logger.error("%s: %s\n%s", what, type(exc).__name__, where)


class _Deadline:
    """One moment by which a whole press is over (rule 1), whatever the
    relay is doing: at that moment every connection the press holds is shut,
    so a read that the relay keeps feeding a byte at a time ends too (a
    socket's own timeout is for each read, and never fires on a trickle)."""

    def __init__(self, seconds: float):
        self.at = time.monotonic() + seconds
        self._fired = False
        self._lock = threading.Lock()
        self._held = []
        self._timer = threading.Timer(max(seconds, 0.0), self._end)
        self._timer.daemon = True
        self._timer.start()

    def left(self) -> float:
        return self.at - time.monotonic()

    def over(self) -> bool:
        return self._fired or self.left() <= 0

    def hold(self, sock) -> None:
        """Have this connection shut at the deadline. A second handle on
        the same connection is kept for that: the first is wrapped (TLS)
        and handed on, out of reach from here."""
        try:
            twin = sock.dup()
        except (AttributeError, OSError):
            return  # its own timeout is what is left
        with self._lock:
            if not self._fired:
                self._held.append(twin)
                return
        _shut(twin)

    def _end(self) -> None:
        with self._lock:
            self._fired = True
            held, self._held = self._held, []
        for twin in held:
            _shut(twin)

    def release(self) -> None:
        """A call is over: its connection is let go (not shut)."""
        with self._lock:
            held, self._held = self._held, []
        for twin in held:
            try:
                twin.close()
            except OSError:
                pass

    def close(self) -> None:
        self._timer.cancel()
        self.release()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def _shut(sock) -> None:
    try:
        sock.shutdown(_SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


class _Unreadable(Exception):
    """The relay began an answer that cannot be read as one: not HTTP, cut
    off, too large, or not finished by the deadline."""


def _connect_by(deadline: _Deadline):
    """socket.create_connection with one deadline for every address the
    name has, in place of its limit for each address in turn (NEW-270):
    each address gets what is left, and the connection is shut at the
    deadline whatever is being read from it by then."""

    def create(address, timeout=None, source_address=None, *_args, **_kw):
        host, port = address
        last = None
        for family, kind, proto, _name, where in socket.getaddrinfo(
            host, port, 0, socket.SOCK_STREAM
        ):
            left = deadline.left()
            if left <= 0:
                break
            sock = socket.socket(family, kind, proto)
            try:
                sock.settimeout(left)
                if source_address:
                    sock.bind(source_address)
                sock.connect(where)
            except OSError as exc:
                sock.close()
                last = exc
                continue
            sock.settimeout(max(deadline.left(), 0.01))
            deadline.hold(sock)
            return sock
        if last is not None and deadline.left() > 0:
            raise last
        raise TimeoutError("timed out")

    return create


def _by(connection_class, deadline: _Deadline):
    def make(host, **kw):
        made = connection_class(host, **kw)
        made._create_connection = _connect_by(deadline)
        return made

    return make


class _HttpBy(urllib.request.HTTPHandler):
    def __init__(self, deadline: _Deadline):
        super().__init__()
        self._deadline = deadline

    def http_open(self, req):
        return self.do_open(_by(http.client.HTTPConnection, self._deadline), req)


class _HttpsBy(urllib.request.HTTPSHandler):
    def __init__(self, deadline: _Deadline, context):
        super().__init__(context=context)
        self._deadline = deadline

    def https_open(self, req):
        return self.do_open(
            _by(http.client.HTTPSConnection, self._deadline),
            req,
            context=self._context,
        )


def _opener_by(deadline: _Deadline):
    """The vendored client's opener (its verified TLS context, no
    redirects), its connections bound by ``deadline``."""
    made = account._opener(None)
    context = next(
        h._context for h in made.handlers if isinstance(h, urllib.request.HTTPSHandler)
    )
    keep = [
        h
        for h in made.handlers
        if not isinstance(h, (urllib.request.HTTPHandler, urllib.request.HTTPSHandler))
    ]
    out = urllib.request.OpenerDirector()
    for h in [*keep, _HttpBy(deadline), _HttpsBy(deadline, context)]:
        out.add_handler(h)
    return out


def _relay_call(relay: str, path: str, body, headers: dict, deadline=None):
    """One POST to the relay within the press's deadline, to the last byte
    of the answer (rule 1), in place of the vendored client's 30 seconds
    for each read (app/switchboard is vendored and kept as shipped):
    (status, decoded JSON or None, raw body).

    No answer at all raises OSError (urllib's URLError is one); an answer
    that began and cannot be read (not HTTP, cut off, over MAX_ANSWER, not
    finished by the deadline) raises _Unreadable."""
    own = deadline is None
    if own:
        deadline = _Deadline(PRESS_TIMEOUT)
    began = False
    try:
        if deadline.left() <= 0:
            raise TimeoutError("timed out")
        data = json.dumps(body).encode("utf-8") if body is not None else b""
        hdrs = {
            "Accept": "application/json",
            "User-Agent": f"sbplive/{SWITCHBOARD_VERSION}",
        }
        if body is not None:
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers)
        req = urllib.request.Request(
            relay + path, data=data, headers=hdrs, method="POST"
        )
        try:
            resp = _opener_by(deadline).open(req, timeout=max(deadline.left(), 0.01))
        except urllib.error.HTTPError as exc:
            resp = exc  # a 3xx, 4xx or 5xx: an answer all the same
        began = True
        with resp:
            status = (
                resp.code if isinstance(resp, urllib.error.HTTPError) else resp.status
            )
            raw = resp.read(MAX_ANSWER + 1)
        if deadline.over():
            # shut at the deadline: what was read is not the whole answer
            raise _Unreadable("the answer did not finish in time")
        if len(raw) > MAX_ANSWER:
            raise _Unreadable("the answer is too large")
    except (OSError, http.client.HTTPException, ValueError) as exc:
        if began:
            raise _Unreadable(type(exc).__name__) from None
        if isinstance(exc, OSError):
            # no answer: refused, reset, timed out, closed without a word
            # (http.client's RemoteDisconnected is an OSError too)
            raise
        if deadline.over():
            raise TimeoutError("timed out") from None
        raise _Unreadable(type(exc).__name__) from None
    finally:
        if own:
            deadline.close()
        else:
            deadline.release()
    try:
        decoded = _from_json(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, ValueError):
        decoded = None
    return status, decoded, raw


# the five classes of answer (rule 2)
OK, AWAY, REFUSED, BUSY, ODD = "ok", "away", "refused", "busy", "odd"
SAID_MAX = 200


def _said(data) -> str:
    """The relay's own sentence for a refusal, as plain text: one line,
    printable, bounded. Never markup to trust: the panel escapes it."""
    text = (data.get("error") or data.get("message")) if isinstance(data, dict) else ""
    if not isinstance(text, str):
        return ""
    text = "".join(c for c in " ".join(text.split()) if c.isprintable())
    if len(text) > SAID_MAX:
        text = text[: SAID_MAX - 1].rstrip() + "…"
    return text


class Answer:
    """One relay answer, classified (rule 2). ``data`` is the JSON object:
    the asked-for fields when OK, the relay's own body for a refusal."""

    __slots__ = ("kind", "status", "data")

    def __init__(self, kind: str, status: int = 0, data: dict | None = None):
        self.kind = kind
        self.status = status
        self.data = data

    def error(self) -> SwitchboardError:
        """The press's failure, in the class's one sentence."""
        if self.kind == AWAY:
            return SwitchboardError(503, RELAY_AWAY, unreachable=True)
        if self.kind == BUSY:
            return SwitchboardError(503, RELAY_BUSY, busy=True)
        if self.kind == REFUSED:
            said = _said(self.data)
            return SwitchboardError(
                502, RELAY_REFUSED + (f" The relay said: {said}" if said else "")
            )
        return SwitchboardError(502, RELAY_ODD)


def _classify(status: int, data, want) -> Answer:
    """What a whole, well-formed answer is: OK only for a 2xx whose JSON
    object carries what was asked for (``want``)."""
    obj = data if isinstance(data, dict) else None
    if 200 <= status < 300:
        # an answer that is not what was asked keeps what it did carry: a
        # link answer with a credential is a record the relay made (rule 4)
        return Answer(OK if obj is not None and want(obj) else ODD, status, obj)
    if status == 429 or status >= 500:
        return Answer(BUSY, status)
    if 400 <= status < 500:
        return Answer(REFUSED, status, obj)
    return Answer(ODD, status)  # a redirect, or a 1xx that is no answer


def _relay_post(
    relay: str, path: str, body, *, want, bearer: str | None = None, deadline=None
) -> Answer:
    """The one way the product asks the relay anything: within the press's
    deadline (rule 1), and answered as one of the five classes (rule 2).
    Never raises; what went wrong goes to the log, where the relay's address
    and the status belong, with no credential."""
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    try:
        status, data, _raw = _relay_call(relay, path, body, headers, deadline)
    except _Unreadable as exc:
        logger.warning(
            "the relay at %s answered %s with what cannot be read: %s",
            relay,
            path,
            exc,
        )
        return Answer(ODD)
    except OSError as exc:
        # what the OS said goes to the log, not to the person (W-208)
        reason = getattr(exc, "reason", None) or exc
        logger.warning("the relay at %s did not answer %s: %s", relay, path, reason)
        return Answer(AWAY)
    except Exception as exc:
        # nothing gets past the classifier (rule 2): whatever tripped on
        # this answer, it was not one that could be read
        _log_fault(f"the relay's answer to {path} could not be worked on", exc)
        return Answer(ODD)
    try:
        answer = _classify(status, data, want)
    except Exception as exc:
        _log_fault(f"the relay's answer to {path} could not be classified", exc)
        return Answer(ODD)
    if answer.kind != OK:
        logger.warning(
            "the relay at %s answered %s to %s (%s)", relay, status, path, answer.kind
        )
    return answer


def _is_link(data: dict) -> bool:
    return bool(
        isinstance(data.get("credential"), str)
        and account._CREDENTIAL.fullmatch(data["credential"])
        and isinstance(data.get("device"), str)
        and account._ID.fullmatch(data["device"])
        and isinstance(data.get("account"), str)
        and account._ID.fullmatch(data["account"])
        and isinstance(data.get("slug"), str)
        and account._SLUG.fullmatch(data["slug"])
    )


def _is_code(data: dict) -> bool:
    code = data.get("code")
    return isinstance(code, str) and bool(account._CODE.fullmatch(code))


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _is_forgotten(data: dict) -> bool:
    return isinstance(data.get("deleted"), bool) or _count(data.get("devices_left")) > 0


# What became of the relay's record when it was asked to forget a computer
# (rule 3). Only DELETED is "the relay deleted its record".
DELETED = "deleted"  # the relay said so: "deleted": true
SHARED = "shared"  # unlinked; the relay keeps the account for other computers
GONE = "gone"  # the relay no longer knows this computer (its own 401)
UNLINKED = "unlinked"  # a relay from before /v1/forget: unlinked, not deleted
UNCONFIRMED = "unconfirmed"  # it answered, but not one of the above
# the relay will not be asked again: the record is gone, or asking for its
# deletion is not something this relay does
_TOLD = (DELETED, SHARED, GONE, UNLINKED)


class _Forgot:
    __slots__ = ("fate", "left")

    def __init__(self, fate: str, left: int = 0):
        self.fate = fate
        self.left = left


def _relay_forget(relay: str, credential: str, deadline=None) -> _Forgot:
    """Have the relay delete its record of this computer (POST /v1/forget;
    on a relay from before it, /v1/unlink), and say what its answer means:
    one of _TOLD, or UNCONFIRMED, or the class of an answer that was not
    one (AWAY, REFUSED, BUSY). Both calls share the press's deadline."""
    own = deadline is None
    if own:
        deadline = _Deadline(PRESS_TIMEOUT)
    try:
        answer = _relay_post(
            relay,
            account.FORGET_PATH,
            None,
            want=_is_forgotten,
            bearer=credential,
            deadline=deadline,
        )
        if answer.kind == OK:
            if answer.data.get("deleted") is True:
                return _Forgot(DELETED)
            left = _count(answer.data.get("devices_left"))
            return _Forgot(SHARED, left) if left > 0 else _Forgot(UNCONFIRMED)
        if answer.kind == REFUSED and answer.status == 404:
            # a relay from before /v1/forget: unlink, as then
            answer = _relay_post(
                relay,
                account.UNLINK_PATH,
                None,
                want=lambda data: True,
                bearer=credential,
                deadline=deadline,
            )
            if answer.kind == OK:
                return _Forgot(UNLINKED)
        if answer.kind == REFUSED and answer.status == 401 and answer.data is not None:
            return _Forgot(GONE)  # the relay's own "not linked"
        return _Forgot(UNCONFIRMED if answer.kind == ODD else answer.kind)
    finally:
        if own:
            deadline.close()


# -- the administrator's three buttons ----------------------------------------------

# one Connect or Disconnect at a time (rule 5)
_press_lock = threading.Lock()


def _one_press(fn):
    def press(*args, **kw):
        if not _press_lock.acquire(blocking=False):
            raise SwitchboardError(409, PRESS_BUSY)
        try:
            return fn(*args, **kw)
        finally:
            _press_lock.release()

    press.__name__ = fn.__name__
    press.__doc__ = fn.__doc__
    press.__wrapped__ = fn
    return press


_OWN_LABEL = re.compile(re.escape(TOKEN_LABEL) + r"(?: \d+)?")


def _free_label(db) -> str:
    from app.models.api_tokens import ApiToken

    taken = {
        label
        for (label,) in db.query(ApiToken.label)
        .filter(ApiToken.label.like(TOKEN_LABEL + "%"))
        .all()
    }
    if TOKEN_LABEL not in taken:
        return TOKEN_LABEL
    n = 2
    while f"{TOKEN_LABEL} {n}" in taken:
        n += 1
    return f"{TOKEN_LABEL} {n}"


def _undo_token(db, token_id) -> None:
    """Take back the token a Connect made and could not keep: the row goes
    (its secret never left this function, so nothing was ever done under
    it); if it cannot be deleted it is at least revoked."""
    from app.models.api_tokens import ApiToken

    try:
        db.rollback()
        if token_id is None:
            return
        row = db.query(ApiToken).filter(ApiToken.id == token_id).first()
        if row is None:
            return
        try:
            db.delete(row)
            db.commit()
        except Exception:
            db.rollback()
            row = db.query(ApiToken).filter(ApiToken.id == token_id).first()
            if row is not None:
                row.is_active = False
                db.commit()
    except Exception as exc:
        _log_fault("the token of a connection that was not kept was not removed", exc)
        try:
            db.rollback()
        except Exception:
            pass


def _undo_link(relay: str, data: dict, deadline) -> str:
    """Have the relay delete the record of a link that was not kept, in
    what is left of the press; if it cannot be told now, the record is
    noted and retried (rule 4). Returns a sentence to add when not even the
    note could be kept, "" otherwise."""
    fate = _relay_forget(relay, data["credential"], deadline).fate
    if fate in _TOLD:
        return ""
    why = {AWAY: WHY_AWAY, REFUSED: WHY_REFUSED}.get(fate, WHY_UNCONFIRMED)
    slug = data.get("slug")
    if not (isinstance(slug, str) and account._SLUG.fullmatch(slug)):
        # the relay gave a credential and no name a note could be kept
        # under: there is nothing to give support but the time
        logger.warning(
            "the relay keeps a record of a link that was not kept here, and "
            "gave no relay name for it"
        )
        return (
            " The relay may still hold a record of this try; if you write to "
            f"{account.SUPPORT} about it, say when you pressed Connect."
        )
    try:
        _add_pending(
            {"relay": relay, "slug": slug, "credential": data["credential"]},
            why,
            connect=True,
        )
        return ""
    except OSError as exc:
        logger.warning(
            "the relay keeps a record of a link that was not kept here (relay "
            "name %s), and no note of it could be written: %s",
            slug,
            exc,
        )
        return (
            f" The relay may still hold a record under relay name {slug}; "
            f"send that name to {account.SUPPORT} to have it removed."
        )


def _made_a_record(answer: Answer) -> bool:
    """Whether a link answer that is not a whole link still shows the relay
    made a record: a 2xx carrying a device credential (rule 4)."""
    credential = (answer.data or {}).get("credential")
    return (
        answer.kind == ODD
        and 200 <= answer.status < 300
        and isinstance(credential, str)
        and bool(account._CREDENTIAL.fullmatch(credential))
    )


@_one_press
def connect(db, username: str) -> dict:
    """Link, make the token, keep both, open the line. Linking comes first,
    so a relay that does not link leaves nothing behind in the books; and a
    failure after it (the token, the file) is undone on both sides before
    the press answers (rule 4)."""
    from app.models.users import ROLE_BOOKKEEPER
    from app.routes import api_tokens
    from app.services.backup_service import current_company_name

    record = _load()
    if record is not None:
        if record["company_file"] != _company_here():
            raise SwitchboardError(
                409, OTHER_COMPANY.format(name=record["company_name"])
            )
        raise SwitchboardError(409, ALREADY)
    try:
        _clear_ended()
    except _AlreadyKept:
        raise SwitchboardError(409, ALREADY) from None
    except OSError as exc:
        # before the relay is asked for anything: nothing is made anywhere
        logger.warning("the data folder cannot be written to: %s", exc)
        raise SwitchboardError(503, NOT_KEPT) from None
    try:
        relay = relay_url()
    except ConfigError as exc:
        raise SwitchboardError(502, str(exc)) from None
    with _Deadline(PRESS_TIMEOUT) as deadline:
        answer = _relay_post(
            relay,
            account.LINK_PATH,
            {"device_name": DEVICE_NAME, "switchboard": SWITCHBOARD_VERSION},
            want=_is_link,
            deadline=deadline,
        )
        if answer.kind != OK:
            failed = answer.error()
            if _made_a_record(answer):
                # not a link that can be kept, but the relay made a record
                # and said what deleting it needs: it is deleted, or noted
                more = _undo_link(relay, answer.data, deadline)
                failed = SwitchboardError(failed.status, failed.sentence + more)
            raise failed
        data = answer.data
        token_id = None
        try:
            label = _free_label(db)
            row, secret = api_tokens.issue_token(
                db, label=label, role=ROLE_BOOKKEEPER, created_by=username or "operator"
            )
            token_id = row.id
            record = {
                "v": 1,
                "on": True,
                "relay": relay,
                "device": data["device"],
                "account": data["account"],
                "slug": data["slug"],
                "credential": data["credential"],
                "token": secret,
                "token_id": row.id,
                "token_label": row.label,
                "company_file": _company_here(),
                "company_name": current_company_name(db),
                "linked_at": datetime.now(timezone.utc).isoformat(),
            }
            _save_new(record)
        except Exception as exc:
            # nothing half-made stays: no token, no file (it was not made),
            # and the relay deletes the record it had just made
            _undo_token(db, token_id)
            more = _undo_link(relay, data, deadline)
            if isinstance(exc, _AlreadyKept):
                # another Connect (another server process) kept its own
                # meanwhile: that one is the connection
                raise SwitchboardError(409, ALREADY) from None
            if isinstance(exc, OSError):
                logger.warning("the connection could not be kept: %s", exc)
            else:
                _log_fault("the connection could not be made", exc)
            raise SwitchboardError(503, NOT_KEPT + more) from None
    _start(record)
    return status()


def _record_here() -> dict:
    record = _load()
    if record is None:
        raise SwitchboardError(409, NOT_LINKED)
    if record["company_file"] != _company_here():
        raise SwitchboardError(409, OTHER_COMPANY.format(name=record["company_name"]))
    return record


def new_code() -> dict:
    """A one-time sign-in code for the relay's sign-in page."""
    record = _record_here()
    answer = _relay_post(
        record["relay"],
        account.CODE_PATH,
        {"for": "sign-in"},
        want=_is_code,
        bearer=record["credential"],
    )
    if answer.kind != OK:
        raise answer.error()
    expires = answer.data.get("expires")
    return {
        "code": answer.data["code"],
        "expires": expires if isinstance(expires, str) else None,
    }


def _kept_for_disconnect() -> tuple:
    """(what is kept, whether it is a whole link _load accepts). A file
    _load refuses is read for what it still holds (rule 4)."""
    try:
        return _record_here(), True
    except SwitchboardError as exc:
        if not exc.unreadable:
            raise
    record = _salvage()
    whose, name = record.get("company_file"), record.get("company_name")
    if (
        isinstance(whose, str)
        and isinstance(name, str)
        and name
        and whose != _company_here()
    ):
        # another company's: its token is in that company's books, so it is
        # ended from there, like a link that can be read
        raise SwitchboardError(409, OTHER_COMPANY.format(name=name))
    return record, False


def _revoke(db, record: dict, whole: bool) -> tuple:
    """Revoke the connection's token: (whether one was, when the newest was
    made). The token is the one whose secret the file holds, whatever an
    administrator has renamed it to since (rule 4); where the secret can no
    longer be read, the one with the id and the label the file says; and
    with a file that cannot say, every active token carrying the
    connection's own label."""
    from app.models.api_tokens import ApiToken
    from app.services.api_token_service import hash_token

    rows = []
    secret = record.get("token")
    if isinstance(secret, str) and secret:
        row = (
            db.query(ApiToken).filter(ApiToken.token_hash == hash_token(secret)).first()
        )
        if row is not None:
            rows.append(row)
    token_id, label = record.get("token_id"), record.get("token_label")
    if not rows and isinstance(token_id, int) and not isinstance(token_id, bool):
        row = db.query(ApiToken).filter(ApiToken.id == token_id).first()
        if row is not None and row.label == label:
            rows.append(row)
    if not whole:
        rows += [
            row
            for row in db.query(ApiToken)
            .filter(ApiToken.label.like(TOKEN_LABEL + "%"), ApiToken.is_active)
            .all()
            if _OWN_LABEL.fullmatch(row.label) and row not in rows
        ]
    made = max((row.created_at for row in rows if row.created_at), default=None)
    live = [row for row in rows if row.is_active]
    for row in live:
        row.is_active = False
    if live:
        db.commit()
    return bool(rows), made


@_one_press
def disconnect(db) -> dict:
    """Close the line, have the relay delete its record of these books (POST
    /v1/forget: the account and everything kept for it, when this is its
    last computer), revoke the token, forget the file.

    The token and the file always go (rule 4), with one exception: a relay
    that answers busy (a 5xx, a 429) undoes nothing, so Disconnect can
    simply be pressed again. What the relay did is said as the relay said it
    (rule 3): "deleted" only for a deletion; for anything else the relay
    name stays on the panel in a note, retried quietly where that can help.
    A file that can no longer be read is cleared all the same, and the
    answer says what could not be done at the relay and what to give
    support."""
    record, whole = _kept_for_disconnect()
    stop()
    undoing = threading.Event()
    try:
        return _disconnect(db, record, whole, undoing)
    except BaseException:
        # nothing was undone here (the relay busy, or a step of our own
        # failed before the token was touched): the link is as it was, so
        # its line opens again
        if whole and not undoing.is_set():
            _start(record)
        raise


def _disconnect(db, record: dict, whole: bool, undoing) -> dict:
    # a file only its mode spoils is a whole link for everything but trust
    intact = whole or _whole(record)
    slug = record.get("slug")
    slug = slug if isinstance(slug, str) and account._SLUG.fullmatch(slug) else ""
    credential = record.get("credential")
    can_tell = (
        _usable_relay(record.get("relay"))
        and isinstance(credential, str)
        and bool(account._CREDENTIAL.fullmatch(credential))
    )
    fate = _relay_forget(record["relay"], credential) if can_tell else None
    if fate is not None and fate.fate == BUSY:
        # the relay is there but was just updated or is busy: nothing is
        # undone here, so Disconnect can simply be pressed again (W-208's
        # note)
        raise Answer(BUSY).error()
    told = fate is not None and fate.fate in _TOLD
    why, noted = "", False
    if fate is not None and not told:
        # kept until the relay hears it, and retried quietly (NEW-269)
        why = {AWAY: WHY_AWAY, REFUSED: WHY_REFUSED}.get(fate.fate, WHY_UNCONFIRMED)
        if slug:
            try:
                _add_pending(
                    {"relay": record["relay"], "slug": slug, "credential": credential},
                    why,
                )
                noted = True
            except OSError as exc:
                logger.warning(
                    "the relay name %s of a record the relay still holds could "
                    "not be noted: %s",
                    slug,
                    exc,
                )
    undoing.set()
    # each step says what it did; the answer is made of what was done
    token_kept = False
    try:
        revoked, made = _revoke(db, record, intact)
    except Exception as exc:
        _log_fault("the connection's token could not be revoked", exc)
        revoked, made, token_kept = False, None, True
        try:
            db.rollback()
        except Exception:
            pass
    file_kept = False
    try:
        _forget()
    except OSError as exc:
        logger.warning("the connection file could not be removed: %s", exc)
        # emptied where it lies, it is no connection any more: Disconnect
        # is done all the same
        file_kept = not _end_in_place()
    if told:
        sentence = _told_sentence(fate, slug)
    elif fate is not None and slug:
        sentence = pending_sentence(slug, why, kept=noted)
    else:
        sentence = _lost_sentence(slug, made)
    if not intact:
        sentence = (
            "The connection kept in the data folder could not be read, so it was "
            "removed here"
            + (" and its token revoked. " if revoked else ". ")
            + sentence
        )
    if file_kept or token_kept:
        # not finished: what was done at the relay (the sentence so far) and
        # to the token, then what was not, and what to do about it
        if token_kept:
            label = record.get("token_label")
            label = label if isinstance(label, str) and label else TOKEN_LABEL
            sentence += TOKEN_KEPT.format(label=" ".join(label.split())[:100])
        elif revoked and intact:
            sentence += " Its token is revoked."
        if file_kept:
            sentence += FILE_KEPT
        raise SwitchboardError(503, sentence)
    return {
        "unlinked": told,
        "revoked": revoked,
        "deleted": told and fate.fate == DELETED,
        "devices_left": fate.left if told else 0,
        "slug": slug,
        "sentence": sentence,
        # why the relay still holds a record ("" when it does not, or when
        # it could not be asked), and whether a note of it is kept
        "why": why,
        "pending": noted,
        # the sentence carries something the person has to read: it stays on
        # the panel instead of a passing message
        "say": not noted and (not told or fate.fate == UNLINKED or not intact),
    }


def _told_sentence(fate: _Forgot, slug: str) -> str:
    """What the relay did, as it said it (rule 3)."""
    if fate.fate in (DELETED, SHARED):
        return account.Forgotten(
            True, True, fate.fate == DELETED, fate.left, slug
        ).sentence()
    if fate.fate == GONE:
        return (
            "This computer is unlinked. The relay no longer has a link for it "
            f"(relay name {slug or '(unknown)'})."
        )
    return (
        f"This computer is unlinked (relay name {slug or '(unknown)'}). This relay "
        "cannot delete its record of these books by itself; to have it removed, "
        f"send that name to {account.SUPPORT}."
    )


def _lost_sentence(slug: str, made) -> str:
    """The relay could not be asked: what asking needs was lost with the
    file. What to give support, and never a secret."""
    if slug:
        return (
            "The relay's own record of these books could not be removed, because "
            "what removing it needs was lost with the file. Send relay name "
            f"{slug} to {account.SUPPORT} to have it removed."
        )
    when = f" ({made:%Y-%m-%d %H:%M} UTC)" if made is not None else ""
    return (
        "The relay's own record of these books could not be removed, because "
        "the relay name was lost with the file. To have it removed, write to "
        f"{account.SUPPORT} and say when this connection was made{when}."
    )
