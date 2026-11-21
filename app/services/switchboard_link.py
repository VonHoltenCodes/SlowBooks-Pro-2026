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
#   add_here()    puts the company this request works in on the connection:
#                 a bookkeeper token of its own, made in its own books, and a
#                 handle the assistant names it by
#   take_off()    takes one company off: its token is turned off at once
#   new_code()    a one-time sign-in code from the relay (XXXX-XXXX-XXXX,
#                 ten minutes, once), typed on the relay's sign-in page when
#                 the assistant asks
#   status()      what the panel says, for the person looking at it
#   disconnect()  closes the line, has the relay delete its record (or, out
#                 of reach, says the relay name to send to support), turns
#                 off every company's token and deletes the file
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
#     line, bounded (the panel escapes it), and with nothing secret in it:
#     the credential the call carried, anything shaped like one, and any
#     long unbroken run of letters and digits are taken out first, so a
#     relay (or something in front of it) that repeats what it was sent
#     shows nobody a credential.
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
#     connection, and the next Connect takes it away. Whatever lies under
#     the connection file's name counts: a folder, a link that leads
#     nowhere or anything else that is not a file is a damaged connection,
#     never "no connection", so status, Connect and Disconnect agree about
#     it and Disconnect clears it.
#  5. One record per install, one token per company, under the record
#     lock: a second press while one is being worked on is refused in a
#     sentence; every change to the kept record (Connect, Add, Take off,
#     Disconnect, a refused token's secret dropped, the leftover sweep) is
#     made under a lock the operating system holds on claude-relay.lock and
#     lets go of when the process ends; and the connection file is only
#     ever made new by Connect (never over another), so two presses, or two
#     server processes, make one relay record, and two Adds both persist.
#  6. (The panel, app/static/js/settings.js: a timed refresh never moves
#     keyboard focus or clears a selection; when the control that had the
#     keyboard goes, its neighbor in the panel gets it, never the top of
#     the page; a refresh never gives back a button whose press is still
#     being worked on; and no two buttons share a name.)
#
# One link per install, one relay device and one line, reaching every
# company an administrator of that company added, each through a token of
# its own made in its own books. Every tool call names its books by a
# handle (smith-plumbing~k3f9q2) that only this computer knows; a request
# carries that company's own token and names that company, and an answer
# is taken only when the app says it came from that company. Power over the
# whole connection (a sign-in code, Add, Disconnect) belongs to someone who
# is an administrator of every company on it; the company it was made in
# gives none. The line runs while any company on it is open here, in one
# process only (claude-relay.line); on the desktop only the open company
# answers. A token whose company is not open when it is ended is kept as a
# leftover (its hash and id, never its secret) and turned off when that
# company opens next.
# ============================================================================

import base64
import hashlib
import http.client
import json
import logging
import os
import re
import secrets
import socket
import threading
import time
import traceback
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.switchboard import __version__ as SWITCHBOARD_VERSION
from app.switchboard import account, line as line_client, logs as sb_logs
from app.switchboard.client import (
    COMPANY_HEADER,
    ApiClient,
    Refused,
    SpecHolder,
    urllib_transport,
)
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
# the record lock and the line owner's lock (both empty, never deleted), and
# the tokens still to be turned off in companies that were not open
LOCK_FILE = "claude-relay.lock"
LINE_FILE = "claude-relay.line"
LEFTOVER_FILE = "claude-relay-leftover.json"
# on the desktop, a window that has a company on the connection open holds
# a lock on this file (one per company), so the line's window can tell a
# company open in another window from one that is not open at all
OPEN_PREFIX = "claude-relay.open."
# a window that does not hold the line tries for it this often
LINE_RETRY = 30.0
# a connection reaches at most this many companies
MAX_COMPANIES = 200
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
ALREADY = (
    "An AI assistant connection already exists on this computer. Nothing was done. "
    "Use Add instead."
)
ALREADY_ON = "This company is already on the AI assistant connection. Nothing was done."
NOT_ON = "That company is not on the AI assistant connection. Nothing was done."
ONLY_ADMIN = "Only an administrator can connect an AI assistant."
NOT_ADMIN_THERE = (
    "Only an administrator of that company can take it off the AI assistant "
    "connection. Nothing was done."
)
NOT_A_MANAGER = (
    "Only someone who is an administrator of every company on the AI assistant "
    "connection can do this. On a server, sign in with the same username and "
    "password as an administrator of each of them. Nothing was done."
)
NOT_A_MANAGER_ADD = (
    "An AI assistant connection already exists on this server. Only someone who "
    "is an administrator of every company on it can add {name}."
)
ONE_ON_POSTGRES = (
    "On PostgreSQL this server serves one company, so the connection reaches only "
    "this one."
)
EDIT_BUSY = (
    "Another change to the AI assistant connection is under way, so nothing was "
    "done. Try again in a moment."
)
NOT_ADDED = (
    "SlowBooks Pro could not add this company to the AI assistant connection, so "
    "nothing was changed. Check that the data folder can be written to, then try "
    "again."
)
# Add when the connection already reaches as many companies as it can
FULL = (
    "The AI assistant connection already reaches {count} companies, the most it "
    "can. Nothing was done."
)
LATER = (
    " {name} is not open now; its token will be turned off the next time it is "
    "opened."
)
DAMAGED_OTHERS = (
    " If other companies were on the connection, an administrator of each turns "
    "off its AI assistant (relay) token in Settings, API Tokens."
)
DAMAGED_ELSEWHERE = (
    "The AI assistant connection kept in the data folder cannot be read, and this "
    "company is not on it. Nothing was done. An administrator of a company on it "
    "can disconnect it."
)
# what the assistant hears when the books it named cannot be reached now; each
# ends "Nothing was done." (the registry sends nothing)
NOTHING_DONE = "Nothing was done."
BOOKS_LOST = (
    "{name} no longer accepts this connection: its token was turned off there, or "
    "the person who added it is no longer an administrator there. To use it again, "
    "someone who is an administrator of {name}, and of every other company on this "
    "connection, adds it again in {name}'s Settings, under Connections. " + NOTHING_DONE
)
BOOKS_NOT_OPEN = (
    "{name} is not open in SlowBooks Pro on this computer. Ask the person to open "
    "it there, then try again. " + NOTHING_DONE
)
BOOKS_OTHER_WINDOW = (
    "{name} is open in another SlowBooks Pro window; this connection runs in the "
    "window that has {here} open. Ask the person to open {name} in that window, "
    "then try again. " + NOTHING_DONE
)
BOOKS_NOT_SERVED = (
    "{name} is not served by this SlowBooks Pro server right now. Ask its "
    "administrator. " + NOTHING_DONE
)
# who turns off a leftover token, in that company's audit log
SWEEP_ACTOR = "system: AI connection ended"
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
    """Keep a changed record over the one kept: only Add, Take off and a
    refused token's secret drop, under _record_lock (and the tests)."""
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


# -- the companies on the connection (format 2) -------------------------------------
#
# A format-2 record keeps a "companies" list, one entry per company:
#   {"books", "file", "name", "token", "token_sha256", "token_id",
#    "token_label", "added_at", "added_by", "off"}
# and mirrors companies[0] in the top-level token, token_id, token_label,
# company_file and company_name, so a build that knows only one company
# still reads it as that one. "off" is "" (live) or "refused" (the token
# answered 401 and its secret was deleted from here). A format-1 record (one
# company, no list) is read as one entry whose handle is derived from the
# device and the file, so it stays the same until the first write.

_ENTRY_FIELDS = (
    "books",
    "file",
    "name",
    "token",
    "token_sha256",
    "token_id",
    "token_label",
    "added_at",
    "added_by",
    "off",
)
_HANDLE = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}~[a-z2-7]{6}")
_BASE32 = "abcdefghijklmnopqrstuvwxyz234567"
_SHA256 = re.compile(r"[0-9a-f]{64}")
OFF_REFUSED = "refused"


def _handle_stem(company_file: str) -> str:
    stem = company_file
    if stem.lower().endswith(".db"):
        stem = stem[: -len(".db")]
    stem = re.sub(r"[^a-z0-9_-]+", "-", stem.lower()).strip("-_")
    return stem[:63].rstrip("-_") or "books"


def _new_handle(company_file: str, taken=()) -> str:
    """A handle for a company newly put on the connection: its file's stem
    and six random characters, new each time it is added."""
    stem = _handle_stem(company_file)
    while True:
        made = stem + "~" + "".join(secrets.choice(_BASE32) for _ in range(6))
        if made not in taken:
            return made


def _derived_handle(device: str, company_file: str) -> str:
    """The handle of the one company of a format-1 record: always the same
    for the same device and file."""
    digest = hashlib.sha256((device + company_file).encode("utf-8")).digest()
    return (
        _handle_stem(company_file)
        + "~"
        + base64.b32encode(digest).decode("ascii").lower()[:6]
    )


def _hash(secret: str) -> str:
    from app.services.api_token_service import hash_token

    return hash_token(secret)


def _named_now(entries: list) -> list:
    """The entries with each company's name as it is now: the name the
    company list gives its file (a rename in Settings changes it there), or
    the one kept when the list names it not."""
    try:
        from app.services import served_companies

        names = {r["file"]: r["name"] for r in served_companies.listed() if r["name"]}
    except Exception:
        names = {}
    for entry in entries:
        name = names.get(entry.get("file") or "")
        if name:
            entry["name"] = name
    return entries


def _entries(record: dict) -> list:
    """The companies on the connection, as entries (copies): format 2's own
    list, or format 1's one company, each named as it is now."""
    return _named_now(_kept_entries(record))


def _kept_entries(record: dict) -> list:
    if isinstance(record.get("companies"), list):
        return [dict(e) for e in record["companies"]]
    token = record.get("token") or ""
    return [
        {
            "books": _derived_handle(record["device"], record["company_file"]),
            "file": record["company_file"],
            "name": record["company_name"],
            "token": token,
            "token_sha256": _hash(token) if token else "",
            "token_id": record["token_id"],
            "token_label": record["token_label"],
            "added_at": record.get("linked_at") or "",
            "added_by": "",
            "off": "",
        }
    ]


def _rev(record: dict) -> int:
    rev = record.get("rev")
    return rev if isinstance(rev, int) and not isinstance(rev, bool) else 0


def _with_adders(entries: list) -> list:
    """A beta.1 record (format 1) names nobody as the person who added its
    company, and Disconnect, a sign-in code and Take off skip a company that
    cannot be opened now only for the person who added it. So the first
    time format 2 is written, an entry with no adder is given its token's
    maker, read in that company's books while it can be opened."""
    from app.models.api_tokens import ApiToken

    if not _several_served():
        return entries
    for entry in entries:
        digest = entry.get("token_sha256") or ""
        if (entry.get("added_by") or "").strip() or not digest:
            continue
        if not _reachable(entry["file"]):
            continue
        try:
            with _in_company(entry["file"]) as db:
                row = db.query(ApiToken).filter(ApiToken.token_hash == digest).first()
                maker = ((row.created_by if row is not None else "") or "").strip()
        except Exception as exc:
            _log_fault("who added a company to the AI connection was not read", exc)
            continue
        if maker:
            entry["added_by"] = maker
    return entries


def _format2(record: dict, entries: list) -> dict:
    """The record to keep for these entries: the link's own fields, the
    revision one up, and companies[0] mirrored at the top."""
    out = {"v": 1, "format": 2, "rev": _rev(record) + 1, "on": record.get("on", True)}
    for key in ("relay", "device", "account", "slug", "credential", "linked_at"):
        out[key] = record[key]
    first = entries[0]
    out.update(
        token=first["token"],
        token_id=first["token_id"],
        token_label=first["token_label"],
        company_file=first["file"],
        company_name=first["name"],
    )
    out["companies"] = [{k: e[k] for k in _ENTRY_FIELDS} for e in entries]
    return out


def _entry_whole(entry, alone: bool) -> bool:
    from app.services.company_service import safe_company_filename

    if not isinstance(entry, dict):
        return False
    if not all(isinstance(entry.get(f), str) for f in _ENTRY_FIELDS if f != "token_id"):
        return False
    token_id = entry.get("token_id")
    if not isinstance(token_id, int) or isinstance(token_id, bool):
        return False
    company_file = entry["file"]
    if company_file == "":
        if not alone:  # a database that is not a file serves one company
            return False
    elif safe_company_filename(company_file) != company_file:
        return False
    if not _HANDLE.fullmatch(entry["books"]):
        return False
    if entry["off"] not in ("", OFF_REFUSED):
        return False
    if (entry["off"] == "") != bool(entry["token"]):
        return False  # a live entry has its secret; a refused one has none
    if not _SHA256.fullmatch(entry["token_sha256"]):
        return False
    return not entry["token"] or _hash(entry["token"]) == entry["token_sha256"]


def _whole2(record) -> bool:
    """Whether what the file holds is a link as this build keeps one: a
    format-1 link as Connect kept one before, or a format-2 record whose
    every entry is whole, unique and mirrored."""
    if not _whole(record):
        return False
    if "companies" not in record and "format" not in record:
        return True
    companies = record.get("companies")
    if record.get("format") != 2 or not isinstance(companies, list):
        return False
    rev = record.get("rev")
    if not isinstance(rev, int) or isinstance(rev, bool) or rev < 0:
        return False
    if not 1 <= len(companies) <= MAX_COMPANIES:
        return False
    alone = len(companies) == 1
    if not all(_entry_whole(e, alone) for e in companies):
        return False
    for field in ("file", "books", "token_sha256"):
        values = [e[field] for e in companies]
        if len(set(values)) != len(values):
            return False
    tokens = [e["token"] for e in companies if e["token"]]
    if len(set(tokens)) != len(tokens):
        return False
    first = companies[0]
    return (
        record["token"] == first["token"]
        and record["token_id"] == first["token_id"]
        and record["token_label"] == first["token_label"]
        and record["company_file"] == first["file"]
        and record["company_name"] == first["name"]
    )


def _record_stamp():
    """What says the kept file changed: its inode, time and size."""
    try:
        st = os.stat(link_path())
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


_hashes_seen: dict = {"stamp": object(), "hashes": frozenset()}
_hashes_lock = threading.Lock()


def link_token_hashes() -> frozenset:
    """The SHA-256 of every token the kept record holds, live or turned
    off, whatever each is named now in its company (a token beta.1 let be
    renamed is still the connection's). Read again when the file changes;
    empty when there is no record or it cannot be read."""
    stamp = _record_stamp()
    with _hashes_lock:
        if stamp == _hashes_seen["stamp"]:
            return _hashes_seen["hashes"]
    found: set = set()
    if stamp is not None:
        try:
            record = _load()
        except (SwitchboardError, OSError, ValueError, KeyError, TypeError):
            record = None
        if record is not None:
            for entry in _entries(record):
                if entry.get("token_sha256"):
                    found.add(entry["token_sha256"])
    hashes = frozenset(found)
    with _hashes_lock:
        _hashes_seen.update(stamp=stamp, hashes=hashes)
    return hashes


# -- the locks ------------------------------------------------------------------------


def _lock_path(name: str) -> Path:
    return link_path().with_name(name)


def _open_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    return os.open(path, os.O_RDWR | os.O_CREAT, 0o600)


def _try_lock(fd: int) -> bool:
    """Take the kernel's lock on the file, without waiting. The operating
    system lets go of it when the process ends, however it ends."""
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


# One holder at a time in this process (the kernel's lock is the file's,
# and a second open of it in the same process would not wait on the first);
# the same thread may take it again inside.
_record_mutex = threading.RLock()
_record_held = {"fd": None, "depth": 0}


@contextmanager
def _record_lock(wait: float | None = None, pay: bool = True):
    """The record lock (rule 5): waits up to the press deadline, then
    refuses with EDIT_BUSY. A data folder where the lock file cannot be made
    cannot keep a record either: the change goes on and its write fails.
    Its first holder in this process makes the owed drops first, unless
    pay is False (a holder that makes them itself)."""
    wait = PRESS_TIMEOUT if wait is None else wait
    end = time.monotonic() + max(wait, 0.0)
    got = (
        _record_mutex.acquire(timeout=wait)
        if wait > 0
        else _record_mutex.acquire(blocking=False)
    )
    if not got:
        raise SwitchboardError(409, EDIT_BUSY, busy=True)
    try:
        if _record_held["depth"] == 0:
            try:
                fd = _open_lock(_lock_path(LOCK_FILE))
            except OSError as exc:
                logger.warning("the connection's lock file cannot be made: %s", exc)
                fd = None
            if fd is not None:
                while not _try_lock(fd):
                    if time.monotonic() >= end:
                        os.close(fd)
                        raise SwitchboardError(409, EDIT_BUSY, busy=True)
                    time.sleep(0.05)
            _record_held["fd"] = fd
        _record_held["depth"] += 1
        try:
            if pay and _record_held["depth"] == 1:
                _pay_owed_drops()
            yield
        finally:
            _record_held["depth"] -= 1
            if _record_held["depth"] == 0:
                fd, _record_held["fd"] = _record_held["fd"], None
                if fd is not None:
                    _unlock(fd)
    finally:
        _record_mutex.release()


# -- leftovers: tokens to turn off in a company that was not open ---------------------


def leftover_path() -> Path:
    return link_path().with_name(LEFTOVER_FILE)


def _load_leftovers() -> dict:
    try:
        data = _from_json(leftover_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, ValueError):
        logger.warning("the leftover AI connection tokens file cannot be read")
        return {}
    if not isinstance(data, dict):
        return {}
    from app.services.company_service import safe_company_filename

    out = {}
    for company_file, items in data.items():
        if not isinstance(items, list):
            continue
        if company_file and safe_company_filename(company_file) != company_file:
            continue
        kept = [
            i
            for i in items
            if isinstance(i, dict)
            and isinstance(i.get("token_id"), int)
            and not isinstance(i.get("token_id"), bool)
            and isinstance(i.get("token_sha256"), str)
            and _SHA256.fullmatch(i["token_sha256"])
        ]
        if kept:
            out[company_file] = kept
    return out


def _save_leftovers(found: dict) -> None:
    found = {f: items for f, items in found.items() if items}
    if not found:
        try:
            leftover_path().unlink()
        except FileNotFoundError:
            pass
        return
    _write_private(leftover_path(), json.dumps(found, indent=2) + "\n")


def _add_leftover(entry: dict, why: str) -> None:
    """Keep the token's hash and id to turn it off when its company opens
    next (never its secret)."""
    with _record_lock():
        found = _load_leftovers()
        items = found.setdefault(entry["file"], [])
        if not any(i["token_sha256"] == entry["token_sha256"] for i in items):
            items.append(
                {
                    "token_id": entry["token_id"],
                    "token_sha256": entry["token_sha256"],
                    "since": datetime.now(timezone.utc).isoformat(),
                    "why": why,
                }
            )
        _save_leftovers(found)


def _drop_leftover(entry: dict) -> None:
    """Forget the leftover kept for the entry's token: it is turned off
    already, or its company stays on the connection after all. One that
    cannot be forgotten is logged: for a token turned off already it changes
    nothing; for one still on the connection, its token is turned off when
    its company opens next, and the panel offers to add it again."""
    try:
        with _record_lock():
            found = _load_leftovers()
            items = found.get(entry["file"]) or []
            found[entry["file"]] = [
                i for i in items if i["token_sha256"] != entry["token_sha256"]
            ]
            _save_leftovers(found)
    except (OSError, SwitchboardError) as exc:
        logger.warning("a leftover AI connection token was not forgotten: %s", exc)


def sweep_leftovers() -> int:
    """Turn off the leftover tokens of every company open here now, each
    only where both its id and its hash match a row (an id alone may be
    another token's by now). Returns how many are still waiting. Run at the
    start and after a change to the connection, never while a company's
    engine is being opened."""
    from app.models.api_tokens import ApiToken

    with _record_lock():
        found = _load_leftovers()
        if not found:
            return 0
        for company_file in list(found):
            if not _company_open(company_file):
                continue
            try:
                with _in_company(company_file, acting=SWEEP_ACTOR) as db:
                    for item in found[company_file]:
                        row = (
                            db.query(ApiToken)
                            .filter(ApiToken.id == item["token_id"])
                            .first()
                        )
                        if (
                            row is not None
                            and row.token_hash == item["token_sha256"]
                            and row.is_active
                        ):
                            row.is_active = False
                    db.commit()
                found[company_file] = []
            except Exception as exc:
                _log_fault("a leftover AI connection token was not turned off", exc)
        try:
            _save_leftovers(found)
        except OSError as exc:
            logger.warning(
                "the leftover AI connection tokens file was not written: %s", exc
            )
        return sum(len(items) for items in found.values())


def _sweep_quietly() -> None:
    try:
        sweep_leftovers()
    except SwitchboardError:
        pass  # another change holds the lock; the next one sweeps
    except Exception as exc:
        _log_fault("the leftover AI connection tokens were not swept", exc)


# -- a session in another company's books ---------------------------------------------


class _NotOpen(Exception):
    """The company is not open here now: nothing of it is opened."""


@contextmanager
def _in_company(company_file: str, acting: str | None = None):
    """A fresh Session bound to that company's books, for a mint or a revoke
    in a company other than the request's own (the request's Session stays
    bound to its own). Refused unless the company is open here, so on the
    desktop a listed company that is not open is never opened."""
    from app import database

    if not _company_open(company_file):
        raise _NotOpen(company_file)
    name = company_file or None
    with database.use_company(name):
        db = database.SessionLocal()
        try:
            want = database.company_engine(name)
            bind = db.get_bind()
            if getattr(bind, "engine", bind) is not want:
                raise RuntimeError("the session is not bound to that company")
            if acting:
                db.info["acting_username"] = acting
            yield db
        finally:
            db.close()


def _load() -> dict | None:
    """The kept link, None when there is none; SwitchboardError when it
    cannot be used (another user can read it, or it is damaged)."""
    path = link_path()
    if not os.path.lexists(path):
        return None
    if not path.is_file():
        # a folder, a link that leads nowhere, anything that is not a file:
        # something is kept under the name and it is not a connection
        raise SwitchboardError(409, DAMAGED, unreadable=True)
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
    if not _whole2(record):
        raise SwitchboardError(409, DAMAGED, unreadable=True)
    return record


def _salvage() -> dict:
    """What can still be read of a kept file that _load refuses, for
    Disconnect (rule 4): the file as it is, whatever its mode, or {} when
    it is not a JSON object. Each value is checked where it is used."""
    path = link_path()
    if not path.is_file():
        return {}  # not a file: nothing of it is read
    try:
        record = _from_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def _forget() -> None:
    """Take away whatever lies under the connection file's name: the file,
    a link (the link itself, not what it leads to), an empty folder. A
    folder that holds something is not ours to delete: it is moved aside
    under a name that says so."""
    path = link_path()
    try:
        path.unlink()
        return
    except FileNotFoundError:
        return
    except OSError:
        if path.is_symlink() or not path.is_dir():
            raise
    try:
        path.rmdir()
    except OSError:
        os.rename(path, path.with_name(f"{path.name}.damaged-{time.time_ns()}"))


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
    path = link_path()
    if path.is_symlink() or not path.is_file():
        return False  # only a file of our own is written over
    try:
        fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
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
_line_fd = None  # the line owner's lock while this process holds it
_waiter = {"stop": None}  # the retry for the line lock, while one waits
_open_marks: dict = {}  # company file -> the lock this window holds on it


def _server_edition() -> bool:
    return os.environ.get("SLOWBOOKS_SERVER_MODE") == "1"


def _several_served() -> bool:
    """Server Edition on company files, which may serve several companies:
    the manager rule follows the mode, not how many companies are listed
    now (with the others unlisted, an administrator of the one left must not
    take power over a connection that still holds them)."""
    return _server_edition() and not _on_postgres()


def _on_postgres() -> bool:
    from app import database

    return not database.DATABASE_URL.startswith("sqlite")


def _books_transport(entry: dict):
    """The requests of one company's token to this app: that token only,
    naming that company's file in X-SlowBooks-Company (never another
    spelling of it, and none on PostgreSQL, where the company is not a
    file)."""
    config = Config(_app_base_url(), entry["token"], API_TIMEOUT)
    send = urllib_transport(config)
    company = entry.get("file") or ""
    lower = COMPANY_HEADER.lower()

    def with_company(method, path, params, body, headers):
        extra = {k: v for k, v in (headers or {}).items() if k.lower() != lower}
        if company:
            extra[COMPANY_HEADER] = company
        return send(method, path, params, body, extra)

    return with_company


def _entry_state(entry: dict, here: str | None = None) -> str:
    """ready, not_open_here, other_window, not_served, locked, refused or
    maker_gone (the token is not turned off, but the person who added it is
    no longer an administrator there, so the company's gate refuses it)."""
    if entry.get("off") or not entry.get("token"):
        return "refused"
    company_file = entry["file"]
    if not _company_open(company_file):
        if _server_edition():
            return "not_served"
        return "other_window" if _open_elsewhere(company_file) else "not_open_here"
    here = _company_here() if here is None else here
    if company_file == here and not _server_edition() and _line_elsewhere():
        return "other_window"
    if _locked(company_file):
        return "locked"
    return _refusal_there(entry) or "ready"


def _locked(company_file: str) -> bool:
    """Whether a company that is served here cannot be opened now (its file
    is locked, has its own key, or is behind this build's schema)."""
    from app import database
    from app.services.served_companies import CompanyUnavailable

    try:
        database.company_engine(company_file or None)
    except CompanyUnavailable:
        return True
    except Exception:
        return True
    return False


_NOTES = {
    "not_open_here": "not open on this computer",
    "other_window": "open in another window",
    "not_served": "not served by this server now",
    "locked": "locked",
    "refused": "turned off in that company",
    "maker_gone": "the person who added it is no longer an administrator there",
}


class LinkBooks:
    """The companies of the kept record, for the line's registry: one
    ApiClient per live company, each with its own token, naming its own
    company and taking only answers that say they came from it. The record
    is looked at again on every call, so an Add, a Take off or a refused
    secret counts at once."""

    def __init__(self, record: dict | None = None):
        self._mutex = threading.Lock()
        self._stamp = object()
        self._entries: list = []
        self._clients: dict = {}
        self._spec = SpecHolder()
        if record is not None:
            self._use(record, _record_stamp())

    def _use(self, record, stamp) -> None:
        live = record is not None and record.get("on") is not False
        entries = _entries(record) if live else []
        clients = {}
        for entry in entries:
            if entry["off"] or not entry["token"]:
                continue
            key = (entry["books"], entry["token_sha256"], entry["file"])
            made = self._clients.get(key)
            if made is None:
                made = ApiClient(
                    _books_transport(entry),
                    company=entry["file"] or None,
                    expect_served=entry["file"],
                    spec_holder=self._spec,
                )
            clients[key] = made
        self._entries, self._clients, self._stamp = entries, clients, stamp

    def _fresh(self) -> list:
        stamp = _record_stamp()
        with self._mutex:
            if stamp == self._stamp:
                return _named_now([dict(e) for e in self._entries])
        try:
            record = _load()
        except SwitchboardError:
            record = None
        with self._mutex:
            self._use(record, stamp)
            return _named_now([dict(e) for e in self._entries])

    def entries(self) -> list:
        here = _company_here()
        out = []
        for entry in self._fresh():
            state = _entry_state(entry, here)
            out.append(
                {
                    "books": entry["books"],
                    "name": entry["name"] or entry["file"],
                    "answers": state == "ready",
                    "why": _NOTES.get(state, ""),
                    "file": entry["file"],
                }
            )
        return out

    def count(self) -> int:
        return len(self._fresh())

    def pick(self, books, tool: str):
        entries = self._fresh()
        entry = next((e for e in entries if e["books"] == books), None)
        if entry is None and books is None and len(entries) == 1:
            entry = entries[0]
        if entry is None:
            raise Refused(
                403, f"This connection does not reach those books. {NOTHING_DONE}"
            )
        name = entry["name"] or entry["file"] or "These books"
        public = {
            "books": entry["books"],
            "name": name,
            "answers": True,
            "why": "",
            "file": entry["file"],
        }
        if _drop_owed(entry["books"]):
            # no 401 can come while the books answer as lost, so the call
            # refused here is the one to try the drop again
            _pay_owed_now(OWED_PAY_WAIT)
            raise Refused(403, BOOKS_LOST.format(name=name))
        if entry["off"] or not entry["token"]:
            raise Refused(403, BOOKS_LOST.format(name=name))
        if not _company_open(entry["file"]):
            if _server_edition():
                raise Refused(403, BOOKS_NOT_SERVED.format(name=name))
            if _open_elsewhere(entry["file"]):
                here_file = _company_here()
                here = (
                    next(
                        (
                            e["name"]
                            for e in entries
                            if e["file"] == here_file and e["name"]
                        ),
                        "",
                    )
                    or _here_name()
                )
                raise Refused(403, BOOKS_OTHER_WINDOW.format(name=name, here=here))
            raise Refused(403, BOOKS_NOT_OPEN.format(name=name))
        with self._mutex:
            client = self._clients.get(
                (entry["books"], entry["token_sha256"], entry["file"])
            )
        if client is None:
            raise Refused(403, BOOKS_LOST.format(name=name))
        return client, public

    def lost(self, books) -> None:
        drop_secret(books)

    def lost_sentence(self, entry: dict) -> str:
        """What the assistant hears when the company turned the token away."""
        return BOOKS_LOST.format(name=entry.get("name") or entry.get("file") or "")


# Books whose token was refused and whose secret is not deleted yet: from
# the moment drop_secret hears the 401 until the secret is really gone,
# those books answer as lost. A drop the record lock is not free for in
# time stays owed and is made by whichever comes first: the next holder of
# the record lock in this process (it makes the owed drops as it takes the
# lock), the next call to those books (refused as lost, it tries for the
# lock a moment first), or one retry a moment later on a thread of its own.
# The marks live in this process only: when the app ends with a drop still
# owed, the secret stays on disk and the company is not yet marked refused,
# as before owed drops existed; after the next start the first call to
# those books reaches the company, hears the 401 again, and that deletes it.
_owed_drops: set = set()
_owed_lock = threading.Lock()
OWED_PAY_WAIT = 0.25  # how long a call refused as owed tries for the lock
OWED_RETRY = 2.0  # how long after a drop becomes owed its retry runs
OWED_RETRY_WAIT = PRESS_TIMEOUT  # how long that retry waits for the lock
_owed_retry = {"thread": None, "stop": None}  # under _owed_lock


def _drop_owed(books: str) -> bool:
    with _owed_lock:
        return books in _owed_drops


def _drop_held(books: str) -> bool:
    """Under _record_lock: delete the secret of the books' token and mark
    the company refused. False when there is no record, or no token of
    those books with a secret left."""
    record = _load()
    if record is None:
        return False
    entries = _entries(record)
    for entry in entries:
        if entry["books"] == books and entry["token"]:
            entry["token"] = ""
            entry["off"] = OFF_REFUSED
            break
    else:
        return False
    _save(_format2(record, _with_adders(entries)))
    logger.warning(
        "an AI assistant connection token was refused by its company; its "
        "secret is deleted from this computer"
    )
    return True


def _pay_drop(books: str) -> bool:
    """Under _record_lock: make one owed drop. Its mark is cleared once no
    secret of those books is left (deleted now, or already gone); a drop
    that fails is logged and stays owed. Whether it was deleted now."""
    try:
        done = _drop_held(books)
    except Exception as exc:
        logger.warning(
            "a refused AI assistant connection token's secret is still "
            "not deleted: %s",
            exc,
        )
        return False
    with _owed_lock:
        _owed_drops.discard(books)
    return done


def _pay_owed_drops() -> None:
    """Under _record_lock: the drops owed. Nothing here fails the holder's
    own change."""
    with _owed_lock:
        owed = sorted(_owed_drops)
    for books in owed:
        _pay_drop(books)


def _pay_owed_now(wait: float) -> None:
    """Take the record lock for up to wait seconds, which makes the owed
    drops; when it stays busy, the retry is armed. Never raises."""
    try:
        with _record_lock(wait=wait):
            pass
    except SwitchboardError:
        pass
    except Exception:
        logger.exception("an owed AI assistant connection drop was not tried")
    _arm_owed_retry()


def _owed_retry_run(stop_event: threading.Event) -> None:
    """One retry: wait OWED_RETRY, then take the record lock for up to
    OWED_RETRY_WAIT to make the owed drops. It runs once and ends; a drop
    still owed after it is retried by the next call to those books."""
    try:
        if stop_event.wait(OWED_RETRY):
            return
        with _record_lock(wait=OWED_RETRY_WAIT, pay=False):
            if not stop_event.is_set():
                _pay_owed_drops()
    except SwitchboardError:
        pass
    except Exception:
        logger.exception("an owed AI assistant connection drop was not retried")
    finally:
        with _owed_lock:
            if _owed_retry["stop"] is stop_event:
                _owed_retry["thread"] = _owed_retry["stop"] = None


def _arm_owed_retry() -> None:
    """When a drop is owed and no retry is waiting, start one."""
    with _owed_lock:
        if not _owed_drops or _owed_retry["thread"] is not None:
            return
        stop_event = threading.Event()
        made = threading.Thread(
            target=_owed_retry_run,
            args=(stop_event,),
            name="claude-relay-owed-drop",
            daemon=True,
        )
        _owed_retry["thread"], _owed_retry["stop"] = made, stop_event
        # Started under the lock, so a stop never finds a thread that has
        # not begun. The thread takes this lock only as it ends.
        try:
            made.start()
        except RuntimeError:
            _owed_retry["thread"] = _owed_retry["stop"] = None
            logger.warning("the retry of an owed AI assistant drop did not start")


def stop_owed_retry(timeout: float = 0.0) -> None:
    """End the waiting retry of owed drops (the line's stop, the app's
    shutdown, the tests); waits up to timeout for its thread. The marks
    stay: a call to those books still answers as lost and tries again."""
    with _owed_lock:
        made, stop_event = _owed_retry["thread"], _owed_retry["stop"]
        _owed_retry["thread"] = _owed_retry["stop"] = None
    if stop_event is not None:
        stop_event.set()
    if made is not None and timeout > 0 and made.ident is not None:
        made.join(timeout)


def drop_secret(books: str | None) -> bool:
    """A company's token answered 401: its secret is deleted from the record
    and the company is marked refused (its hash and id stay, so a restore
    that revives the token row cannot revive access through this computer).
    The books answer as lost from the moment this is called. Another change,
    the start's sweep or another window may hold the record lock for a
    moment, so this waits for it up to the press deadline. When it is still
    busy the drop stays owed (see _owed_drops). Returns whether the secret
    was deleted now."""
    if not books:
        return False
    with _owed_lock:
        _owed_drops.add(books)
    try:
        with _record_lock(pay=False):
            done = _pay_drop(books)
            _pay_owed_drops()
            return done
    except SwitchboardError as exc:
        if exc.sentence == EDIT_BUSY:
            logger.warning(
                "a refused AI assistant connection token's secret is not "
                "deleted yet: the connection is busy; it is retried in a moment"
            )
        return False
    except OSError:
        return False
    finally:
        _arm_owed_retry()


def _make_line(record: dict):
    from app import __version__ as app_version

    made = account.Link(
        record["relay"], record["device"], record["account"], record["slug"], ""
    )
    mcp = Mcp(build_registry(LinkBooks(record)), INSTRUCTIONS)
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


def _take_line_lock() -> bool:
    """The line owner's lock (under _lock): one process on this computer
    runs the line. A data folder where the lock file cannot be made has
    nothing to share it with."""
    global _line_fd
    if _line_fd is not None:
        return True
    try:
        fd = _open_lock(_lock_path(LINE_FILE))
    except OSError as exc:
        logger.warning("the line's lock file cannot be made: %s", exc)
        return True
    if not _try_lock(fd):
        os.close(fd)
        return False
    _line_fd = fd
    return True


def _drop_line_lock() -> None:
    """Under _lock."""
    global _line_fd
    fd, _line_fd = _line_fd, None
    if fd is not None:
        _unlock(fd)


def _line_elsewhere() -> bool:
    """Whether another process on this computer holds the line."""
    with _lock:
        if _line is not None or _line_fd is not None:
            return False
    try:
        fd = os.open(_lock_path(LINE_FILE), os.O_RDWR)
    except OSError:
        return False
    if _try_lock(fd):
        _unlock(fd)
        return False
    os.close(fd)
    return True


def _mark_open(company_file: str) -> None:
    """On the desktop, hold this window's lock on the company it has open,
    so the line's window can tell it is open in another window."""
    if not company_file or _server_edition() or company_file in _open_marks:
        return
    try:
        fd = _open_lock(_lock_path(OPEN_PREFIX + company_file))
    except OSError:
        return
    if _try_lock(fd):
        _open_marks[company_file] = fd
    else:
        os.close(fd)


def _open_elsewhere(company_file: str) -> bool:
    """Whether another window on this computer has this company open."""
    if not company_file or company_file in _open_marks:
        return False
    try:
        fd = os.open(_lock_path(OPEN_PREFIX + company_file), os.O_RDWR)
    except OSError:
        return False
    if _try_lock(fd):
        _unlock(fd)
        return False
    os.close(fd)
    return True


def _wait_for_line() -> None:
    """Under _lock: try for the line lock every LINE_RETRY seconds in a
    thread of its own, until it is taken or the line is no longer wanted."""
    if _waiter["stop"] is not None:
        return
    stop_event = threading.Event()
    _waiter["stop"] = stop_event

    def wait():
        try:
            while not stop_event.wait(LINE_RETRY):
                try:
                    record = _load()
                except SwitchboardError:
                    return
                if record is None or record.get("on") is False:
                    return
                if not any(_company_open(e["file"]) for e in _entries(record)):
                    return
                with _lock:
                    if stop_event.is_set():
                        return
                    if not _take_line_lock():
                        continue
                    _waiter["stop"] = None
                _start(record)
                return
        except Exception:
            logger.exception("the wait for the AI assistant line failed")
        finally:
            with _lock:
                if _waiter["stop"] is stop_event:
                    _waiter["stop"] = None

    threading.Thread(target=wait, name="claude-relay-wait", daemon=True).start()


def _start(record: dict) -> bool:
    """Open the line for this record, unless another process holds it
    (then wait for it). Whether this process runs the line now."""
    global _line, _line_device
    _mark_open(_company_here())
    with _lock:
        if _line is not None and _line_device == record["device"]:
            return True
        if not _take_line_lock():
            logger.info("the AI assistant line runs in another window on this computer")
            _wait_for_line()
            return False
        old = _line
        _line = _make_line(record)
        _line_device = record["device"]
        made = _line
    if old is not None:
        old.stop()
    threading.Thread(
        target=_run, args=(made,), name="claude-relay", daemon=True
    ).start()
    return True


def stop() -> None:
    """Close the line, if one is open (the app's shutdown, Disconnect), and
    let another window have it."""
    global _line, _line_device
    with _lock:
        old, _line, _line_device = _line, None, ""
        _drop_line_lock()
        waiting, _waiter["stop"] = _waiter["stop"], None
    if waiting is not None:
        waiting.set()
    if old is not None:
        try:
            old.stop()
        except Exception:
            logger.exception("closing the Claude relay line failed")
    # an owed drop gets one last try, never waiting for the lock
    with _owed_lock:
        owed = bool(_owed_drops)
    if owed:
        try:
            with _record_lock(wait=0):
                pass
        except Exception:
            pass
    stop_owed_retry()


def start_in_background() -> None:
    """Called once the app has started: when a link is kept and a company on
    it is open here, open the line (or wait for it, when another window
    holds it), then turn off the leftover tokens of the companies open here.
    Returns at once; nothing here can fail the start."""

    def begin():
        new_start()
        try:
            if _load_pending():
                _start_retry()
        except Exception:
            logger.exception("pending relay deletions not retried")
        try:
            record = _load()
            if record is not None and record.get("on") is not False:
                if any(_company_open(e["file"]) for e in _entries(record)):
                    _start(record)
                else:
                    logger.info(
                        "Claude relay line not opened: no company on it is open here"
                    )
        except SwitchboardError as exc:
            logger.warning("Claude relay line not opened: %s", exc.sentence)
        except Exception:
            logger.exception("Claude relay line not opened")
        _sweep_quietly()

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


def _session_admin(session) -> bool:
    from app.models.users import ROLE_ADMIN

    return isinstance(session, dict) and (session.get("role") or "admin") == ROLE_ADMIN


def _admin_of(session, company_file: str) -> bool:
    """Whether the person behind this session is an administrator of that
    company: of the one the request works in, by the session's own role (the
    gate has checked it against the user row); of another company this
    server serves, the way the company switch checks it: the same username,
    active there, an administrator there, no one-time password to replace,
    and the same password as at the sign-in. On the desktop only the open
    company counts."""
    from app.models.users import ROLE_ADMIN
    from app.services import served_companies

    if not _session_admin(session):
        return False
    if company_file == _company_here():
        return True
    if not _several_served() or not _company_open(company_file):
        return False
    if session.get("authenticated") is not True or session.get("must_change_password"):
        return False
    username = session.get("username") or ""
    opens = session.get(served_companies.SESSION_OPENS_KEY) or []
    prints = session.get(served_companies.SESSION_OPENS_FP_KEY) or {}
    if not username or company_file not in opens or not isinstance(prints, dict):
        return False
    from app.routes.auth import _fingerprint, _user_who_may_open

    try:
        with _in_company(company_file) as db:
            user = _user_who_may_open(db, username)
            return (
                user is not None
                and user.role == ROLE_ADMIN
                and not getattr(user, "must_change_password", False)
                and _fingerprint(user.password_hash) == prints.get(company_file)
            )
    except Exception as exc:
        _log_fault("an administrator of another company could not be checked", exc)
        return False


def _may_manage(session, entries: list, *, reachable_only: bool, adding: bool = False):
    """(whether this person holds power over the whole connection, the
    companies that stop them). On a server serving several companies: an
    administrator of every company on it (with reachable_only, of every one
    that can be opened now, and of at least one, and the person who added
    each one that cannot be opened now). On the desktop: an
    administrator of the open company, which is on the connection (or is
    being added to it)."""
    if not _session_admin(session):
        return False, []
    here = _company_here()
    if not _several_served():
        return (adding or any(e["file"] == here for e in entries)), []
    checked, missing = 0, []
    username = (session.get("username") or "").strip().lower()
    for entry in entries:
        if reachable_only and not _reachable(entry["file"]):
            # a company that cannot be opened now cannot be asked; it is
            # skipped only for the person who added it to the connection
            # (no one else learns its name or ends its access this way)
            added_by = (entry.get("added_by") or "").strip().lower()
            if not username or added_by != username:
                missing.append(entry["name"] or entry["file"])
            continue
        checked += 1
        if not _admin_of(session, entry["file"]):
            missing.append(entry["name"] or entry["file"])
    if adding:
        checked += 1
    return (checked > 0 and not missing), missing


def _reachable(company_file: str) -> bool:
    """A company served here now whose books can be opened (not locked, not
    under a key of its own, not behind or past this build's schema)."""
    return _company_open(company_file) and not _locked(company_file)


def _here_name() -> str:
    from app.services import served_companies

    here = _company_here()
    for row in served_companies.listed():
        if row["file"] == here and row["name"]:
            return row["name"]
    return "this company"


def status(session=None) -> dict:
    """What the panel shows to the person behind this session: the names of
    the other companies on the connection only to someone who holds power
    over it."""
    out = {
        "state": "not_connected",
        "sentence": "",
        "linked": False,
        "on_link": False,
        "this_company": False,
        "company": "",
        "address": "",
        "slug": "",
        "token_label": "",
        "terms": TERMS_URL,
        "privacy": PRIVACY_URL,
        "can_add": False,
        "can_take_off": False,
        "can_code": False,
        "can_disconnect": False,
        # the line runs in another window on this computer
        "line_elsewhere": False,
        # a server serving companies, not one desktop window: the panel's
        # words for a company that cannot answer now
        "server_edition": _server_edition(),
        # the company this request works in, by name, for an administrator's
        # Add (its own name: nothing of any other company)
        "here_name": "",
        # its file, while it is on the connection (what Take off names)
        "here_file": "",
        # relay names a Disconnect could not delete yet (NEW-269)
        "pending": pending_notices(),
    }
    admin = _session_admin(session)
    try:
        record = _load()
    except SwitchboardError as exc:
        out.update(
            state="damaged", sentence=exc.sentence, linked=True, can_disconnect=admin
        )
        return out
    if record is None:
        return out
    entries = _entries(record)
    here = _company_here()
    mine = next((e for e in entries if e["file"] == here), None)
    manager, _ = _may_manage(session, entries, reachable_only=True)
    # this company is on it, but its token was turned off here: it can be
    # added again in place (by the same rule as an Add)
    dead_here = mine is not None and _dead(mine)
    others = [e for e in entries if e is not mine]
    out.update(linked=True, on_link=mine is not None, this_company=mine is not None)
    if mine is not None:
        out.update(
            company=mine["name"],
            token_label=mine["token_label"],
            here_file=mine["file"],
        )
        out["can_take_off"] = admin
    if manager:
        out["companies"] = [
            {
                "file": e["file"],
                "name": e["name"],
                "books": e["books"],
                "state": _entry_state(e, here),
                # the company this request works in
                "here": e["file"] == here,
            }
            for e in entries
        ]
        out["count_others"] = len(entries) - (1 if mine is not None else 0)
        can_code, needs = _may_manage(session, entries, reachable_only=False)
        out["can_code"] = can_code
        # the companies a code waits for: each must answer now, with this
        # person an administrator there
        out["code_needs"] = needs
        out["can_disconnect"] = True
    if mine is not None or manager:
        state, sentence = _line_state(record["device"])
        out.update(
            state=state,
            sentence=sentence,
            address=mcp_address(record["relay"]),
            slug=record["slug"],
            line_elsewhere=state == "not_connected" and _line_elsewhere(),
        )
    if mine is None:
        if not admin:
            out["sentence"] = ONLY_ADMIN
        elif _on_postgres():
            out["sentence"] = ONE_ON_POSTGRES
        elif _may_manage(session, entries, reachable_only=False, adding=True)[0]:
            out["can_add"] = len(entries) < MAX_COMPANIES
            if out["can_add"]:
                out["here_name"] = _here_name()
            else:
                out["sentence"] = FULL.format(count=MAX_COMPANIES)
        elif not manager:
            out["sentence"] = NOT_A_MANAGER_ADD.format(name=_here_name())
    elif dead_here and admin and not _on_postgres():
        if _may_manage(session, others, reachable_only=False, adding=True)[0]:
            out["can_add"] = True
            out["here_name"] = _here_name()
    if out["can_add"]:
        # the name the token Add makes will carry (" 2" when the plain one
        # is taken here), which every change it makes is recorded under
        out["next_label"] = _next_label_here()
    return out


def _dead(entry: dict) -> bool:
    """A company on the connection whose token was turned off there, or that
    the gate there turns away now (its token revoked or gone, or the person
    who made it no longer an administrator there), whether or not the
    assistant has been refused yet."""
    if entry.get("off") or not entry.get("token"):
        return True
    return _refused_there(entry)


def _refused_there(entry: dict) -> bool:
    """The company's gate would refuse the entry's token now. A company that
    cannot be opened now cannot say, and is not counted as refusing."""
    return _refusal_there(entry) is not None


# What a company's gate makes of an entry's token is asked for every entry
# on every assistant call and every panel refresh: up to MAX_COMPANIES
# companies, a session and two queries each. The answer is kept until that
# company's file changes (any write to it: a token turned off, a person
# demoted, a backup restored), something this process saves changes a user
# or a token, or a few seconds pass (an end date passing changes nothing on
# disk).
_REFUSAL_KEEP = 15.0
_refusal_kept: dict = {}
_refusal_mutex = threading.Lock()
_refusal_generation = [0]


def _company_marker(company_file: str):
    """What changes when a company's file is written to, or None when it is
    not a file this can watch (then nothing is kept)."""
    from app.services.company_service import company_db_path

    path = company_db_path(company_file) if company_file else None
    if path is None:
        return None
    marker = []
    for one in (path, path.with_name(path.name + "-wal")):
        try:
            st = one.stat()
            marker.append((st.st_mtime_ns, st.st_size))
        except OSError:
            marker.append(None)
    return tuple(marker)


def _users_or_tokens_changed(session, _context=None) -> None:
    from app.models.api_tokens import ApiToken
    from app.models.users import User

    for obj in (*session.new, *session.dirty, *session.deleted):
        if isinstance(obj, (ApiToken, User)):
            with _refusal_mutex:
                _refusal_generation[0] += 1
                _refusal_kept.clear()
            return


def _watch_users_and_tokens() -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    if not event.contains(Session, "after_flush", _users_or_tokens_changed):
        event.listen(Session, "after_flush", _users_or_tokens_changed)


_watch_users_and_tokens()


def _refusal_there(entry: dict) -> str | None:
    """Why the company's gate would refuse the entry's token now: "refused"
    (the token is turned off there, or gone), "maker_gone" (the person who
    made it is no longer an administrator there), or None when it lets it
    in or the company cannot be opened now to say."""
    company_file = entry.get("file") or ""
    digest = entry.get("token_sha256") or ""
    if not digest or not _reachable(company_file):
        return None
    marker = _company_marker(company_file)
    key = (company_file, digest)
    now = time.monotonic()
    with _refusal_mutex:
        generation = _refusal_generation[0]
        kept = _refusal_kept.get(key)
    if (
        kept is not None
        and marker is not None
        and kept[0] == marker
        and kept[1] == generation
        and now - kept[2] < _REFUSAL_KEEP
    ):
        return kept[3]
    answer = _ask_refusal(company_file, digest)
    if marker is not None:
        with _refusal_mutex:
            if _refusal_generation[0] == generation:
                if len(_refusal_kept) > 4 * MAX_COMPANIES:
                    _refusal_kept.clear()
                _refusal_kept[key] = (marker, generation, now, answer)
    return answer


def _ask_refusal(company_file: str, digest: str) -> str | None:
    from app.models.api_tokens import ApiToken
    from app.services.api_token_service import _maker_still_admin

    try:
        with _in_company(company_file) as db:
            row = db.query(ApiToken).filter(ApiToken.token_hash == digest).first()
            if row is None or not row.is_active:
                return "refused"
            # every token of the connection ends with its maker
            if not _maker_still_admin(db, row.created_by):
                return "maker_gone"
            return None
    except Exception as exc:
        _log_fault("a company's AI connection token could not be checked", exc)
        return None


def _next_label_here() -> str:
    try:
        with _in_company(_company_here()) as mine:
            return _free_label(mine)
    except Exception:
        return TOKEN_LABEL


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


HIDDEN = "(hidden)"
# What is never passed on from the relay's own sentence, whoever put it
# there: a value after "Bearer", anything that begins like a device
# credential, and any long unbroken run of letters and digits (a sentence
# has none; a credential, a token or a key is one).
_SECRET_SHAPES = re.compile(
    r"(?i)(?<=bearer )\S+|sbd_[A-Za-z0-9_\-]*|[A-Za-z0-9_\-+/=]{24,}"
)


def _said(data, sent=()) -> str:
    """The relay's own sentence for a refusal, as plain text: one line,
    printable, bounded, and with no secret in it (``sent`` is what the call
    carried: a relay that repeats its request shows nobody the credential).
    Never markup to trust: the panel escapes it."""
    text = (data.get("error") or data.get("message")) if isinstance(data, dict) else ""
    if not isinstance(text, str):
        return ""
    # what cannot be seen goes first, so nothing hides a secret from the
    # masks; the masks go before the cut, so no part of one is left
    text = "".join(c for c in text if c.isprintable() or c.isspace())
    text = " ".join(text.split())
    for secret in sent:
        if isinstance(secret, str) and secret:
            text = text.replace(secret, HIDDEN)
    text = _SECRET_SHAPES.sub(HIDDEN, text)
    if len(text) > SAID_MAX:
        text = text[: SAID_MAX - 1].rstrip() + "…"
    return text


class Answer:
    """One relay answer, classified (rule 2). ``data`` is the JSON object:
    the asked-for fields when OK, the relay's own body for a refusal.
    ``sent`` is what the call carried that is secret (its bearer), kept only
    to take it out of anything the relay says back."""

    __slots__ = ("kind", "status", "data", "sent")

    def __init__(self, kind: str, status: int = 0, data: dict | None = None):
        self.kind = kind
        self.status = status
        self.data = data
        self.sent = ()

    def error(self) -> SwitchboardError:
        """The press's failure, in the class's one sentence."""
        if self.kind == AWAY:
            return SwitchboardError(503, RELAY_AWAY, unreachable=True)
        if self.kind == BUSY:
            return SwitchboardError(503, RELAY_BUSY, busy=True)
        if self.kind == REFUSED:
            said = _said(self.data, self.sent)
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
    if bearer:
        answer.sent = (bearer,)
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


def _kept_already() -> SwitchboardError:
    """Why a new link cannot be kept under the name: what lies there, said
    as it is (a connection, or something damaged)."""
    try:
        _load()
    except SwitchboardError as exc:
        return exc
    return SwitchboardError(409, ALREADY)


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
def connect(db, username: str, session=None) -> dict:
    """Link, make the token, keep both, open the line. Linking comes first,
    so a relay that does not link leaves nothing behind in the books; and a
    failure after it (the token, the file) is undone on both sides before
    the press answers (rule 4). Only when no connection is kept: with one,
    another company is put on it with Add."""
    from app.models.users import ROLE_BOOKKEEPER
    from app.routes import api_tokens
    from app.services.backup_service import current_company_name

    with _record_lock():
        if _load() is not None:
            raise SwitchboardError(409, ALREADY)
        try:
            _clear_ended()
        except _AlreadyKept:
            raise _kept_already() from None
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
                    db,
                    label=label,
                    role=ROLE_BOOKKEEPER,
                    created_by=username or "operator",
                )
                token_id = row.id
                now = datetime.now(timezone.utc).isoformat()
                here = _company_here()
                entry = {
                    "books": _new_handle(here),
                    "file": here,
                    "name": current_company_name(db),
                    "token": secret,
                    "token_sha256": _hash(secret),
                    "token_id": row.id,
                    "token_label": row.label,
                    "added_at": now,
                    "added_by": username or "operator",
                    "off": "",
                }
                record = _format2(
                    {
                        "on": True,
                        "relay": relay,
                        "device": data["device"],
                        "account": data["account"],
                        "slug": data["slug"],
                        "credential": data["credential"],
                        "linked_at": now,
                    },
                    [entry],
                )
                _save_new(record)
            except Exception as exc:
                # nothing half-made stays: no token, no file (it was not made),
                # and the relay deletes the record it had just made
                _undo_token(db, token_id)
                more = _undo_link(relay, data, deadline)
                if isinstance(exc, _AlreadyKept):
                    # another Connect (another server process) kept its own
                    # meanwhile: that one is the connection
                    raise _kept_already() from None
                if isinstance(exc, OSError):
                    logger.warning("the connection could not be kept: %s", exc)
                else:
                    _log_fault("the connection could not be made", exc)
                raise SwitchboardError(503, NOT_KEPT + more) from None
    _start(record)
    return status(session)


def _take_back(company_file: str, token_id) -> None:
    """Take back a token Add made in that company and could not keep."""
    try:
        with _in_company(company_file) as other:
            _undo_token(other, token_id)
    except Exception as exc:
        _log_fault("the token of a company that was not added was not removed", exc)


@_one_press
def add_here(db, username: str, session) -> dict:
    """Put the company this request works in on the connection: a
    bookkeeper token made in its own books, and a handle of its own. Only an
    administrator here who is also an administrator of every company already
    on the connection; nothing is asked of the relay. A failure after the
    token is made takes it back, and the kept record stays as it was."""
    from app.models.users import ROLE_BOOKKEEPER
    from app.routes import api_tokens
    from app.services.backup_service import current_company_name

    here = _company_here()
    with _record_lock():
        record = _load()
        if record is None:
            raise SwitchboardError(409, NOT_LINKED)
        entries = _entries(record)
        old = next((e for e in entries if e["file"] == here), None)
        if old is not None and not _dead(old):
            raise SwitchboardError(409, ALREADY_ON)
        if _on_postgres():
            raise SwitchboardError(409, ONE_ON_POSTGRES + " " + NOTHING_DONE)
        if old is None and len(entries) >= MAX_COMPANIES:
            raise SwitchboardError(409, FULL.format(count=MAX_COMPANIES))
        # A company whose token was turned off here is added again in its
        # place: by the same rule as an Add, the others on the connection
        # counted, not its own dead entry.
        others = [e for e in entries if e is not old]
        if not _may_manage(session, others, reachable_only=False, adding=True)[0]:
            raise SwitchboardError(
                403, NOT_A_MANAGER_ADD.format(name=_here_name()) + " " + NOTHING_DONE
            )
        token_id = None
        try:
            with _in_company(here, acting=username or "operator") as mine:
                if old is not None:
                    # the old token's secret is gone from this computer;
                    # its row is turned off too (its maker may be an
                    # administrator again, which would revive it)
                    _revoke_by_hash(mine, old)
                label = _free_label(mine)
                row, secret = api_tokens.issue_token(
                    mine,
                    label=label,
                    role=ROLE_BOOKKEEPER,
                    created_by=username or "operator",
                )
                token_id, label = row.id, row.label
                name = current_company_name(mine)
            fresh = {
                "books": _new_handle(here, {e["books"] for e in entries}),
                "file": here,
                "name": name,
                "token": secret,
                "token_sha256": _hash(secret),
                "token_id": token_id,
                "token_label": label,
                "added_at": datetime.now(timezone.utc).isoformat(),
                "added_by": username or "operator",
                "off": "",
            }
            if old is None:
                entries.append(fresh)
            else:
                entries[entries.index(old)] = fresh
            changed = _format2(record, _with_adders(entries))
            _save(changed)
        except Exception as exc:
            if token_id is not None:
                _take_back(here, token_id)
            if isinstance(exc, OSError):
                logger.warning("the company could not be added: %s", exc)
            else:
                _log_fault("the company could not be added", exc)
            raise SwitchboardError(503, NOT_ADDED) from None
    _start(changed)
    _sweep_quietly()
    out = status(session)
    out["results"] = [{"file": here, "name": name, "result": "added"}]
    return out


def _as_record(entry: dict) -> dict:
    """An entry in the shape _revoke reads."""
    return {
        "token": entry.get("token") or "",
        "token_sha256": entry.get("token_sha256") or "",
        "token_id": entry.get("token_id"),
        "token_label": entry.get("token_label"),
    }


def _revoke_by_hash(db, entry: dict) -> bool:
    """Turn off the one token whose hash the entry keeps, in this Session's
    company: never one found by its id and label alone."""
    from app.models.api_tokens import ApiToken

    digest = entry.get("token_sha256") or ""
    if not digest:
        return False
    row = db.query(ApiToken).filter(ApiToken.token_hash == digest).first()
    if row is None:
        return False
    if row.is_active:
        row.is_active = False
        db.commit()
    return True


def _end_elsewhere(entry: dict, acting: str, kept: bool = False) -> str:
    """Turn off a token in a company other than the request's: "ended",
    or "later" when that company is not open now (or could not be reached),
    a leftover kept to turn it off when it opens next (``kept``: the caller
    kept it already)."""
    why = "not_open"
    if _company_open(entry["file"]):
        try:
            with _in_company(entry["file"], acting=acting) as other:
                _revoke_by_hash(other, entry)
            return "ended"
        except Exception as exc:
            _log_fault("a company's AI connection token was not turned off", exc)
            why = "failed"
    if not kept:
        _add_leftover(entry, why)
    return "later"


@_one_press
def take_off(db, company_file: str, session) -> dict:
    """Take one company off the connection: its token is turned off at once
    (or, when it is not open now, as soon as it opens), and the others stay
    connected. An administrator of that company, or someone who holds power
    over the connection when that company's token is refused there already.
    Taking off the last company is a Disconnect."""
    with _record_lock():
        record = _load()
        if record is None:
            raise SwitchboardError(409, NOT_LINKED)
        entries = _entries(record)
        entry = next((e for e in entries if e["file"] == company_file), None)
        # who may is asked first, and refused in one sentence whether that
        # company is on the connection or not: a client's administrator
        # never learns which other companies are on it
        allowed = _admin_of(session, company_file) or (
            entry is not None
            and _dead(entry)
            and _may_manage(session, entries, reachable_only=True)[0]
        )
        if not allowed:
            raise SwitchboardError(403, NOT_ADMIN_THERE)
        if entry is None:
            raise SwitchboardError(404, NOT_ON)
        if len(entries) == 1:
            return _disconnect_press(db, session)
        acting = (session or {}).get("username") or "operator"
        # The token is kept as a leftover first, and the record changed
        # next: when either cannot be written, nothing was changed (the token
        # still works, and the company is still on the connection). After
        # both, a token that cannot be turned off now is turned off when its
        # company opens next, and one that is turned off now drops its
        # leftover.
        rest = [e for e in entries if e is not entry]
        try:
            _add_leftover(entry, "taken_off")
        except OSError as exc:
            logger.warning("the connection could not be changed: %s", exc)
            raise SwitchboardError(503, NOT_WRITTEN) from None
        try:
            _save(_format2(record, _with_adders(rest)))
        except OSError as exc:
            logger.warning("the connection could not be changed: %s", exc)
            _drop_leftover(entry)
            raise SwitchboardError(503, NOT_WRITTEN) from None
        if company_file == _company_here():
            try:
                _revoke(db, _as_record(entry), True)
                result = "ended"
            except Exception as exc:
                _log_fault("this company's AI connection token was not turned off", exc)
                try:
                    db.rollback()
                except Exception:
                    pass
                result = "later"
        else:
            result = _end_elsewhere(entry, acting, kept=True)
        if result == "ended":
            _drop_leftover(entry)
    _sweep_quietly()
    out = status(session)
    out["results"] = [{"file": company_file, "name": entry["name"], "result": result}]
    return out


def new_code(session=None) -> dict:
    """A one-time sign-in code for the relay's sign-in page: it reaches every
    company on the connection, so only someone who is an administrator of
    every one of them gets one."""
    record = _load()
    if record is None:
        raise SwitchboardError(409, NOT_LINKED)
    if not _may_manage(session, _entries(record), reachable_only=False)[0]:
        raise SwitchboardError(403, NOT_A_MANAGER)
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


def _kept_for_disconnect(session) -> tuple:
    """(what is kept, whether it is a whole link _load accepts). A file
    _load refuses is read for what it still holds (rule 4)."""
    try:
        record = _load()
    except SwitchboardError as exc:
        if not exc.unreadable:
            raise
        record = None
    else:
        if record is None:
            raise SwitchboardError(409, NOT_LINKED)
        if not _may_manage(session, _entries(record), reachable_only=True)[0]:
            raise SwitchboardError(403, NOT_A_MANAGER)
        return record, True
    record = _salvage()
    here = _company_here()
    files = _salvaged_files(record)
    if files and here not in files:
        # the others' tokens are in their own books, so it is ended from there
        raise SwitchboardError(409, DAMAGED_ELSEWHERE)
    if not _session_admin(session):
        raise SwitchboardError(403, NOT_A_MANAGER)
    return record, False


def _salvaged_files(record: dict) -> list:
    """The company files a file _load refuses still names, where it can say."""
    found = []
    companies = record.get("companies")
    if isinstance(companies, list):
        found = [
            e.get("file")
            for e in companies
            if isinstance(e, dict) and isinstance(e.get("file"), str)
        ]
    whose, name = record.get("company_file"), record.get("company_name")
    if isinstance(whose, str) and isinstance(name, str) and name:
        found.append(whose)
    return found


def _salvaged_others(record: dict, here: str) -> list:
    """The other companies' entries a damaged file still holds whole enough
    to turn their tokens off later (a safe file, a hash and an id)."""
    from app.services.company_service import safe_company_filename

    out = []
    companies = record.get("companies")
    for e in companies if isinstance(companies, list) else []:
        if not isinstance(e, dict):
            continue
        company_file, digest = e.get("file"), e.get("token_sha256")
        token_id = e.get("token_id")
        if (
            isinstance(company_file, str)
            and company_file != here
            and safe_company_filename(company_file) == company_file
            and isinstance(digest, str)
            and _SHA256.fullmatch(digest)
            and isinstance(token_id, int)
            and not isinstance(token_id, bool)
        ):
            out.append(
                {
                    "file": company_file,
                    "name": e.get("name") if isinstance(e.get("name"), str) else "",
                    "token_sha256": digest,
                    "token_id": token_id,
                }
            )
    return out


def _revoke(db, record: dict, whole: bool) -> tuple:
    """Revoke the connection's token in the request's own company: (whether
    one was, when the newest was made). The token is the one whose secret
    (or hash) the record holds, whatever an administrator has renamed it to
    since (rule 4); where neither can be read, the one with the id and the
    label the file says; and with a file that cannot say, every active token
    carrying the connection's own label."""
    from app.models.api_tokens import ApiToken

    rows = []
    secret = record.get("token")
    digest = _hash(secret) if isinstance(secret, str) and secret else None
    if digest is None and isinstance(record.get("token_sha256"), str):
        digest = record["token_sha256"] or None
    if digest:
        row = db.query(ApiToken).filter(ApiToken.token_hash == digest).first()
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
def disconnect(db, session=None) -> dict:
    """Close the line, have the relay delete its record of these books (POST
    /v1/forget: the account and everything kept for it, when this is its
    last computer), turn off every company's token, forget the file.

    Only someone who is an administrator of every company on the connection
    that can be opened now (and of at least one): a company that is lost,
    locked or behind the schema does not keep the others connected. Its
    token, and the token of any company not open now, is kept as a leftover
    and turned off the next time it opens; its secret goes with the file.

    The tokens and the file always go (rule 4), with one exception: a relay
    that answers busy (a 5xx, a 429) undoes nothing, so Disconnect can
    simply be pressed again. What the relay did is said as the relay said it
    (rule 3): "deleted" only for a deletion; for anything else the relay
    name stays on the panel in a note, retried quietly where that can help.
    A file that can no longer be read is cleared all the same, and the
    answer says what could not be done at the relay and what to give
    support."""
    return _disconnect_press(db, session)


def _disconnect_press(db, session) -> dict:
    with _record_lock():
        record, whole = _kept_for_disconnect(session)
        stop()
        undoing = threading.Event()
        try:
            out = _disconnect(db, record, whole, undoing, session)
        except BaseException:
            # nothing was undone here (the relay busy, or a step of our own
            # failed before the token was touched): the link is as it was, so
            # its line opens again
            if whole and not undoing.is_set():
                _start(record)
            raise
    _sweep_quietly()
    return out


def _disconnect(db, record: dict, whole: bool, undoing, session=None) -> dict:
    # a file only its mode spoils is a whole link for everything but trust
    intact = whole or _whole2(record)
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
    here = _company_here()
    acting = (session or {}).get("username") or "operator"
    # each step says what it did; the answer is made of what was done
    token_kept = False
    results, later = [], []
    revoked, made = False, None
    if intact:
        entries = _entries(record)
        mine = [e for e in entries if e["file"] == here]
        others = [e for e in entries if e["file"] != here]
    else:
        mine, others = [record], _salvaged_others(record, here)
    for entry in mine:
        try:
            revoked, made = _revoke(db, _as_record(entry) if intact else entry, intact)
            results.append(_result(entry, "ended"))
        except Exception as exc:
            _log_fault("the connection's token could not be revoked", exc)
            token_kept = True
            try:
                db.rollback()
            except Exception:
                pass
    for entry in others:
        try:
            result = _end_elsewhere(entry, acting)
        except Exception as exc:
            _log_fault("a company's AI connection token was not noted", exc)
            result = "later"
        if result == "ended":
            revoked = True
        else:
            later.append(entry.get("name") or entry["file"])
        results.append(_result(entry, result))
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
        if not isinstance(record.get("companies"), list):
            # the file cannot say which other companies were on it
            sentence += DAMAGED_OTHERS
    for name in later:
        sentence += LATER.format(name=name)
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
        "say": not noted
        and (not told or fate.fate == UNLINKED or not intact or bool(later)),
        # what became of each company's token: ended, or later (when it opens)
        "results": results,
    }


def _text_of(entry: dict, *keys) -> str:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _result(entry: dict, result: str) -> dict:
    return {
        "file": _text_of(entry, "file", "company_file"),
        "name": _text_of(entry, "name", "company_name"),
        "result": result,
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
