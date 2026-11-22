# Vendored from SlowBooks Switchboard: sbplive/line.py
# (see app/switchboard/SOURCE.md). Do not edit here: change the source and run scripts/sync_switchboard.py.
# ============================================================================
# The line: the companion's one WebSocket to the relay (docs/RELAY.md, "The
# line"). The companion dials out to wss://<relay>/v1/line with its device
# credential as a bearer and keeps the line open; the relay sends requests
# down it and the companion answers on it. Nothing listens on this computer.
#
# Frames, text, one JSON object each, version 1 (field names exactly):
#
#   -> hello      {"t":"hello","v":1,"device","switchboard","app","caps":
#                  ["mcp","http","replay"],"mcp":bool,"http":bool}
#   <- welcome    {"t":"welcome","account","slug","subscription":{"status",
#                  "until"}}
#   -> switch     {"t":"switch","mcp":bool,"http":bool} when one changes
#   -> ping       {"t":"ping"} every 15 s; <- pong {"t":"pong"}
#   <- mcp        {"t":"mcp","id","era","headers","body"[,"method"]}
#   -> mcp.result {"t":"mcp.result","id","status","headers","body"}
#   <- http       {"t":"http","id","method","path","headers","body","more"}
#   <- http.more  {"t":"http.more","id","body","more"} until more is false
#   -> http.result{"t":"http.result","id","status","headers","body","more"}
#   -> http.more  the same, streamed as the app answers
#   <> error      {"t":"error","id","message"}
#
# Headers on the line are an object of lowercased names; a value is a string,
# or a list of strings when the header is repeated (Set-Cookie). Bodies of
# http frames are base64, at most 256 KiB of it per frame, at most 64 MiB in
# all.
#
# An mcp frame is answered by server.serve_mcp, the same core the remote door
# uses, over the same registry and the API token, which never leaves this
# computer; the era is read from the message itself, as the remote door does
# it, and the frame's "era" is not needed for that. An http frame is passed
# to SlowBooks Pro at the address `setup` stored, as it came: hop-by-hop
# headers dropped, Host set to that address, nothing added (never the API
# token); cookies and everything else pass both ways untouched. An error
# frame from the relay with the id of a request in flight cancels it.
#
# Beyond RELAY.md's table, as built (the relay follows these):
# - mcp may carry "method": "DELETE" (ending a 2025-11-25 session named by
#   its mcp-session-id header; 200, 404, or 405 without one); absent means
#   POST. A notification answers status 202 and body null. mcp.result's
#   headers are lowercased: content-type, and mcp-session-id after an
#   initialize.
# - http: the request body is sent to the app with Content-Length when the
#   first frame has more=false (none for an empty GET, HEAD, DELETE,
#   OPTIONS or TRACE), and with chunked transfer coding while more frames
#   follow. The answer is one http.result with more=false when the app
#   gives no body or a Content-Length of at most one chunk; otherwise an
#   http.result with an empty body and more=true at once, then http.more
#   frames as the app writes, the last one empty with more=false.
# - A welcome after the first updates the subscription shown.
# - Close codes 4401 and 4403 from the relay mean the link was refused while
#   the line was open: no reconnect, as for 401 and 403 at the handshake.
#
# Switches: each door has one, off by default. A frame for a door whose
# switch is off is answered with an error frame and the spec's sentence.
#
# The line reconnects when it drops, waiting 1 s, doubling to 60 s, with
# jitter; a welcome resets the wait. Two ends do not reconnect: the relay
# refusing the credential (401 or 403: link again) and close code 4001,
# "replaced", which means another computer linked to the account took the
# line (reconnecting would take it back, and the two would fight).
# ============================================================================

import base64
import binascii
import http.client
import json
import logging
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field, replace
from email.message import Message
from urllib.parse import urlsplit

from app.switchboard import __version__, account, store, ws
from app.switchboard.client import (
    ApiClient,
    ApiError,
    Unconfirmed,
    tls_context,
    urllib_transport,
)
from app.switchboard.config import (
    DEFAULT_URL,
    ConfigError,
    check_url,
    load_config,
)
from app.switchboard.server import INSTRUCTIONS, Mcp, build_registry, serve_mcp

log = logging.getLogger("sbplive")

VERSION = 1
# The line can go silent without closing: Cloudflare stops acknowledging
# it, and neither end hears a close (docs/RELAY.md, "As built: the idle
# line"). A ping every 15 s and a line given up after 35 s with no frame (two
# pongs missed) keep that gap short; the relay keeps the requests caught in
# it and sends them again down the next line ("replay" below).
PING_EVERY = 15.0
DEAD_AFTER = 35.0  # no frame at all for this long: the line is gone
# An mcp request's answer is kept this long by its id, so a request the
# relay sends again after the line came back is answered once, never run
# twice (the relay's MCP wait is 110 s).
REPLAY_KEEP = 150.0
REPLAY_MAX = 256
WELCOME_WAIT = 30.0
BACKOFF_FIRST = 1.0
BACKOFF_MAX = 60.0
MCP_TIMEOUT = 110.0
HTTP_TIMEOUT = 300.0
CHUNK = 256 * 1024  # base64 characters per frame
RAW_CHUNK = CHUNK // 4 * 3  # the bytes those carry
MAX_BODY = 64 * 1024 * 1024
MAX_FRAME = 8 * 1024 * 1024  # one frame from the relay
MAX_IN_FLIGHT = 32
REPLACED = 4001

CLAUDE_OFF = "The owner has turned Claude's access off in SlowBooks Switchboard."
TEAM_OFF = "The owner has turned remote access off in SlowBooks Switchboard."
NO_TOKEN = (
    "SlowBooks Switchboard on the computer that holds these books has no API "
    "token for SlowBooks Pro yet. Run setup there."
)
BUSY = "SlowBooks Switchboard is answering too many requests at once; try again."
MCP_SLOW = (
    "SlowBooks Pro took longer than 110 seconds to answer, so the request was "
    "given up. It may still finish in the app; check before trying again."
)
HTTP_SLOW = (
    "SlowBooks Pro took longer than 300 seconds to answer, so the request was given up."
)
TOO_BIG = (
    "The request or its answer is larger than 64 MiB, which the relay does not carry."
)

# RFC 9110 section 7.6.1, and the old Keep-Alive and Proxy-Connection.
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-connection",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)
_TOKEN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_ID = re.compile(r"[\x21-\x7e]{1,200}")
_NO_BODY_METHODS = ("GET", "HEAD", "DELETE", "OPTIONS", "TRACE")


# -- headers on the line ---------------------------------------------------------


def headers_in(raw) -> Message:
    """A line headers object as get()/get_all(), case-insensitive."""
    msg = Message()
    if isinstance(raw, dict):
        for name, value in raw.items():
            if not isinstance(name, str) or not _TOKEN.fullmatch(name):
                continue
            values = value if isinstance(value, list) else [value]
            for v in values:
                if isinstance(v, str) and not any(c in v for c in "\r\n\0"):
                    msg[name] = v
    return msg


def headers_out(pairs) -> dict:
    """(name, value) pairs as the line's object: lowercased names, a list
    for a repeated header."""
    out: dict = {}
    for name, value in pairs:
        key = name.lower()
        if key in out:
            prev = out[key]
            out[key] = (prev if isinstance(prev, list) else [prev]) + [value]
        else:
            out[key] = value
    return out


def _connection_tokens(pairs) -> set:
    named = set()
    for name, value in pairs:
        if name.lower() == "connection":
            named.update(t.strip().lower() for t in value.split(",") if t.strip())
    return named


def pass_through_headers(pairs, *, request: bool) -> list:
    """Drop the hop-by-hop headers (and those Connection names); a request
    also loses Host, which is set to the app's address, and the framing
    headers, which the companion sets for the body it sends."""
    named = _connection_tokens(pairs)
    out = []
    for name, value in pairs:
        key = name.lower()
        if key in HOP_BY_HOP or key in named:
            continue
        if request and key in ("host", "content-length"):
            continue
        out.append((name, value))
    return out


def _pairs(raw) -> list:
    msg = headers_in(raw)
    return list(msg.items())


# -- the status the window and the relay command show -------------------------


@dataclass
class Status:
    state: str = "stopped"  # stopped connecting connected waiting replaced refused
    sentence: str = "The line to the relay is not open."
    account: str = ""
    slug: str = ""
    subscription: dict = field(default_factory=dict)
    retry_in: float = 0.0


def subscription_sentence(sub: dict) -> str:
    status = (sub or {}).get("status")
    until = (sub or {}).get("until")
    when = f" until {until}" if isinstance(until, str) and until else ""
    if status == "active":
        return f"The subscription is active{when}."
    if status == "past_due":
        return f"The subscription's payment is past due; the line stays open{when}."
    if status == "cancelled":
        return f"The subscription is cancelled; it ends{when or ' soon'}."
    if status == "paused":
        return "The subscription is paused."
    if status:
        return f"The subscription is {status}{when}."
    return "The relay has not said how the subscription stands."


@dataclass(frozen=True)
class Timings:
    ping_every: float = PING_EVERY
    dead_after: float = DEAD_AFTER
    welcome_wait: float = WELCOME_WAIT
    backoff_first: float = BACKOFF_FIRST
    backoff_max: float = BACKOFF_MAX
    mcp_timeout: float = MCP_TIMEOUT
    http_timeout: float = HTTP_TIMEOUT
    connect_timeout: float = 15.0


DEFAULT_TIMINGS = Timings()


def backoff(
    attempt: int, timings: Timings = DEFAULT_TIMINGS, rand=random.random
) -> float:
    """The wait before reconnect number attempt (0 first): 1 s doubling to
    60 s, each between three quarters and the whole of it."""
    full = min(timings.backoff_max, timings.backoff_first * (2 ** min(attempt, 30)))
    return full * (0.75 + 0.25 * rand())


class _Replaced(Exception):
    pass


class _Refused(Exception):
    pass


class _Request:
    """One request in flight: answered once, cancellable."""

    def __init__(self, id_: str, kind: str = ""):
        self.id = id_
        self.kind = kind
        self.lock = threading.Lock()
        self.done = False
        self.cancelled = False
        self.conn = None  # the app connection, for an http request
        self.body: list = []  # incoming http body pieces, (bytes, more)
        self.body_ready = threading.Condition(self.lock)
        self.received = 0

    def finish(self) -> bool:
        """True the first time; the caller then sends the answer."""
        with self.lock:
            if self.done:
                return False
            self.done = True
            return True

    def cancel(self) -> None:
        with self.lock:
            self.cancelled = True
            self.body_ready.notify_all()
            conn = self.conn
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass


def relay_origin(line_url: str) -> str:
    """The relay's web address from its line's: wss://relay.example/v1/line
    is https://relay.example (ws:// is http://, a relay on this computer)."""
    parts = urlsplit(line_url)
    scheme = "http" if parts.scheme == "ws" else "https"
    return f"{scheme}://{parts.netloc}"


class Line:
    """The line client. run() keeps the line until stop(); everything else
    is safe from any thread."""

    def __init__(
        self,
        url: str,
        device: str,
        credential: str,
        *,
        app_url: str,
        mcp=None,
        mcp_problem: str | None = None,
        app_version: str | None = None,
        switches: dict | None = None,
        timings: Timings = DEFAULT_TIMINGS,
        ssl_context=None,
        on_change=None,
        rand=random.random,
    ):
        self.url = url
        self.device = device
        self._credential = credential
        parts = urlsplit(app_url)
        self.app_scheme = parts.scheme
        self.app_host = parts.hostname or "127.0.0.1"
        self.app_port = parts.port or (443 if parts.scheme == "https" else 80)
        self.app_netloc = parts.netloc
        self.mcp = mcp
        if mcp is not None and hasattr(mcp, "use_origin"):
            # serverInfo's icons are the relay's own files (relay/src/icons.py).
            mcp.use_origin(relay_origin(url))
        self.mcp_problem = mcp_problem or (NO_TOKEN if mcp is None else None)
        self.app_version = app_version
        sw = switches or {}
        self._switches = {"mcp": bool(sw.get("mcp")), "http": bool(sw.get("http"))}
        self.timings = timings
        self.ssl_context = ssl_context
        self.on_change = on_change
        self.rand = rand
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ws = None
        self._inflight: dict = {}
        # mcp requests by id across reconnects: those still running, and
        # the answers given (when, frame), for the relay's replay
        self._mcp_running: dict = {}
        self._answers: dict = {}
        self.status = Status()
        self._said = ""  # the relay's last sentence before a welcome
        self._welcomed = False  # this connection reached a welcome
        self.waits: list = []  # every backoff wait taken, for the tests

    def __repr__(self) -> str:
        return f"Line(url={self.url!r}, device={self.device!r})"

    # -- the status ---------------------------------------------------------

    def _set(self, **changes) -> None:
        with self._lock:
            self.status = replace(self.status, **changes)
            status = self.status
        if self.on_change is not None:
            try:
                self.on_change(status)
            except Exception:  # a display must not stop the line
                log.exception("the line's status display failed")

    def switches(self) -> dict:
        with self._lock:
            return dict(self._switches)

    def set_switches(self, *, mcp: bool | None = None, http: bool | None = None):
        """Change a switch; the relay hears it at once when the line is open."""
        with self._lock:
            if mcp is not None:
                self._switches["mcp"] = bool(mcp)
            if http is not None:
                self._switches["http"] = bool(http)
            frame = {"t": "switch", **self._switches}
            sock = self._ws
            connected = self.status.state == "connected"
        if sock is not None and connected:
            self._send(sock, frame)

    # -- running ------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            sock = self._ws
        if sock is not None:
            sock.close(ws.CLOSE_GOING_AWAY, "stopped")
            sock.shutdown()

    def run(self) -> str:
        """Keep the line until stop(), replaced or refused; returns the final
        state."""
        attempt = 0
        self._stop.clear()
        while not self._stop.is_set():
            host = urlsplit(self.url).hostname
            self._set(
                state="connecting",
                sentence=f"Connecting to the relay at {host}.",
                retry_in=0.0,
            )
            reason = ""
            self._said = ""
            self._welcomed = False
            try:
                self._connect_once()
                reason = "the relay closed it"
            except _Replaced:
                self._set(
                    state="replaced",
                    sentence=(
                        "SlowBooks Switchboard on another computer linked to this "
                        "account took over the line, so this one let go. Connect "
                        "again here to take it back."
                    ),
                )
                return "replaced"
            except _Refused as exc:
                self._set(state="refused", sentence=str(exc))
                return "refused"
            except ws.HandshakeError as exc:
                reason = _handshake_reason(exc)
            except ws.ConnectionClosed as exc:
                reason = self._said or (
                    f"it closed ({exc.code}{': ' + exc.reason if exc.reason else ''})"
                )
            except TimeoutError:
                reason = "the relay stopped answering"
            except (ws.WebSocketError, OSError) as exc:
                reason = str(exc) or exc.__class__.__name__
            if self._welcomed:
                attempt = 0  # it was up: start again from the first wait
            if self._stop.is_set():
                break
            wait = backoff(attempt, self.timings, self.rand)
            attempt += 1
            self.waits.append(wait)
            self._set(
                state="waiting",
                sentence=(
                    f"The line to the relay is down ({reason}); trying again in "
                    f"{max(1, round(wait))} second{'s' if round(wait) != 1 else ''}."
                ),
                retry_in=wait,
            )
            log.info("line down (%s); retrying in %.1f s", reason, wait)
            if self._stop.wait(wait):
                break
        self._set(state="stopped", sentence="The line to the relay is closed.")
        return "stopped"

    def _connect_once(self) -> bool:
        t = self.timings
        try:
            sock = ws.connect(
                self.url,
                {
                    "Authorization": f"Bearer {self._credential}",
                    "User-Agent": f"sbplive/{__version__}",
                },
                timeout=t.connect_timeout,
                read_timeout=t.welcome_wait,
                ssl_context=self.ssl_context,
                max_message=MAX_FRAME,
            )
        except ws.HandshakeError as exc:
            if exc.status in (401, 403):
                raise _Refused(
                    "The relay did not accept this computer's link "
                    f"({_body_sentence(exc.body) or exc.status}). Link it again."
                ) from None
            raise
        with self._lock:
            self._ws = sock
        welcomed = False
        try:
            if self._stop.is_set():
                return False
            sw = self.switches()
            self._send(
                sock,
                {
                    "t": "hello",
                    "v": VERSION,
                    "device": self.device,
                    "switchboard": __version__,
                    "app": self.app_version,
                    "caps": ["mcp", "http", "replay"],
                    "mcp": sw["mcp"],
                    "http": sw["http"],
                },
            )
            deadline = time.monotonic() + t.welcome_wait
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError("no welcome")
                frame = self._read(sock)
                if frame is None:
                    continue
                kind = frame.get("t")
                if kind == "welcome":
                    break
                if kind == "error" and not frame.get("id"):
                    said = frame.get("message")
                    if isinstance(said, str) and said.strip():
                        self._said = said.strip().rstrip(".")
                    log.warning("the relay refused the line: %s", said)
                    continue
            sub = (
                frame.get("subscription")
                if isinstance(frame.get("subscription"), dict)
                else {}
            )
            welcomed = self._welcomed = True
            sock.settimeout(t.dead_after)
            slug = str(frame.get("slug") or "")
            self._set(
                state="connected",
                sentence="The line to the relay is open.",
                account=str(frame.get("account") or ""),
                slug=slug,
                subscription=dict(sub),
                retry_in=0.0,
            )
            log.info("line open")
            # The switches as they are now, in case one changed meanwhile.
            now = self.switches()
            if now != sw:
                self._send(sock, {"t": "switch", **now})
            threading.Thread(target=self._pinger, args=(sock,), daemon=True).start()
            while not self._stop.is_set():
                frame = self._read(sock)
                if frame is not None:
                    self._dispatch(sock, frame)
            return welcomed
        except ws.ConnectionClosed as exc:
            if exc.code == REPLACED:
                raise _Replaced() from None
            if exc.code in (4401, 4403):
                raise _Refused(
                    f"The relay closed the line: {exc.reason or 'the link was refused'}. "
                    "Link this computer again."
                ) from None
            if self._stop.is_set():
                return welcomed
            raise
        except (OSError, TimeoutError):
            if self._stop.is_set():
                return welcomed
            raise
        finally:
            with self._lock:
                self._ws = None
                flights = list(self._inflight.values())
                self._inflight.clear()
            for req in flights:
                if req.kind == "mcp":
                    # it runs to its end; its answer is kept and goes down
                    # the next line if the relay sends the request again
                    continue
                req.finish()
                req.cancel()
            sock.shutdown()

    def _read(self, sock):
        kind, data = sock.recv()
        if kind != "text":
            log.info("ignored a binary frame from the relay")
            return None
        try:
            frame = json.loads(data)
        except ValueError:
            log.warning("ignored a frame from the relay that is not JSON")
            return None
        if not isinstance(frame, dict) or not isinstance(frame.get("t"), str):
            log.warning("ignored a frame from the relay without a type")
            return None
        return frame

    def _pinger(self, sock) -> None:
        while not self._stop.wait(self.timings.ping_every):
            if sock.closed:
                return
            if not self._send(sock, {"t": "ping"}):
                return

    def _send(self, sock, frame: dict) -> bool:
        try:
            sock.send(json.dumps(frame, ensure_ascii=False, separators=(",", ":")))
            return True
        except ws.WebSocketError:
            return False

    def _error(self, sock, id_, message: str) -> None:
        self._send(sock, {"t": "error", "id": id_, "message": message})

    # -- the frames ---------------------------------------------------------

    def _dispatch(self, sock, frame: dict) -> None:
        kind = frame["t"]
        if kind == "pong":
            return
        if kind == "welcome":
            sub = frame.get("subscription")
            if isinstance(sub, dict):
                self._set(subscription=dict(sub))
            return
        id_ = frame.get("id")
        if kind == "error":
            if isinstance(id_, str):
                with self._lock:
                    req = self._inflight.pop(id_, None)
                if req is not None and req.finish():
                    req.cancel()  # the relay gave up on it: answer nothing
            else:
                log.warning("the relay said: %s", frame.get("message"))
            return
        if kind == "http.more":
            self._http_more(sock, frame)
            return
        if kind not in ("mcp", "http"):
            log.info("ignored a frame of type %r", kind)
            return
        if not isinstance(id_, str) or not _ID.fullmatch(id_):
            log.warning("ignored a %s frame without a usable id", kind)
            return
        switch = self.switches()
        if kind == "mcp" and not switch["mcp"]:
            self._error(sock, id_, CLAUDE_OFF)
            return
        if kind == "http" and not switch["http"]:
            self._error(sock, id_, TEAM_OFF)
            return
        if kind == "mcp":
            with self._lock:
                kept = self._answers.get(id_)
                running = id_ in self._mcp_running
            if kept is not None:
                self._send(sock, kept[1])  # sent again: answered as before
                log.info("mcp -> %s again (sent again by the relay)", kept[1]["status"])
                return
            if running:
                return  # its answer goes down whichever line is open then
        req = _Request(id_, kind)
        with self._lock:
            if id_ in self._inflight:
                busy = "A request with this id is already in flight."
            elif len(self._inflight) >= MAX_IN_FLIGHT:
                busy = BUSY
            else:
                busy = None
                self._inflight[id_] = req
                if kind == "mcp":
                    self._mcp_running[id_] = req
        if busy:
            self._error(sock, id_, busy)
            return
        if kind == "mcp":
            target, limit, slow = self._serve_mcp, self.timings.mcp_timeout, MCP_SLOW
        else:
            if not self._take_body(req, frame):
                self._forget(req)
                self._error(
                    sock, id_, "The request's body is not base64, or too large."
                )
                return
            target, limit, slow = self._serve_http, self.timings.http_timeout, HTTP_SLOW

        def too_slow():
            if req.finish():
                req.cancel()
                self._forget(req)
                self._error(sock, id_, slow)

        timer = threading.Timer(limit, too_slow)
        timer.daemon = True
        timer.start()

        def work():
            try:
                target(sock, req, frame)
            except Exception:  # one request must not stop the line
                log.exception("a %s request failed", kind)
                if req.finish():
                    self._error(
                        sock, id_, "SlowBooks Switchboard failed on this request."
                    )
            finally:
                timer.cancel()
                self._forget(req)

        threading.Thread(target=work, daemon=True).start()

    def _forget(self, req) -> None:
        with self._lock:
            if self._inflight.get(req.id) is req:
                del self._inflight[req.id]
            if self._mcp_running.get(req.id) is req:
                del self._mcp_running[req.id]

    def _keep_answer(self, id_: str, frame: dict) -> None:
        now = time.monotonic()
        with self._lock:
            for old in [
                k for k, (at, _) in self._answers.items() if now - at > REPLAY_KEEP
            ]:
                del self._answers[old]
            while len(self._answers) >= REPLAY_MAX:
                del self._answers[next(iter(self._answers))]
            self._answers[id_] = (now, frame)

    # -- mcp ------------------------------------------------------------------

    def _serve_mcp(self, sock, req, frame) -> None:
        started = time.monotonic()
        if self.mcp is None:
            if req.finish():
                self._error(sock, req.id, self.mcp_problem)
            return
        headers = headers_in(frame.get("headers"))
        method = str(frame.get("method") or "POST").upper()
        if method == "DELETE":
            sid = headers.get("Mcp-Session-Id")
            if sid is None:
                status = 405
            else:
                status = 200 if self.mcp.sessions.close(sid) else 404
            extra, answer = {}, None
        elif method != "POST":
            status, extra, answer = 405, {}, None
        else:
            status, extra, answer = serve_mcp(self.mcp, frame.get("body"), headers)
        out = headers_out(extra.items())
        if answer is not None:
            out["content-type"] = "application/json"
        result = {
            "t": "mcp.result",
            "id": req.id,
            "status": status,
            "headers": out,
            "body": answer,
        }
        self._keep_answer(req.id, result)
        if req.finish():
            # down the line that is open now: the one it came on may be gone
            with self._lock:
                now_open = self._ws
            self._send(now_open or sock, result)
        log.info("mcp -> %s in %.0f ms", status, (time.monotonic() - started) * 1000)

    # -- http -----------------------------------------------------------------

    def _take_body(self, req, frame) -> bool:
        """Add a frame's body to the request; False when it cannot be."""
        raw = frame.get("body") or ""
        if not isinstance(raw, str) or len(raw) > CHUNK:
            return False
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            return False
        with req.lock:
            req.received += len(data)
            if req.received > MAX_BODY:
                return False
            req.body.append((data, frame.get("more") is True))
            req.body_ready.notify_all()
        return True

    def _http_more(self, sock, frame) -> None:
        id_ = frame.get("id")
        with self._lock:
            req = self._inflight.get(id_) if isinstance(id_, str) else None
        if req is None:
            return  # a request already answered or given up
        if not self._take_body(req, frame) and req.finish():
            req.cancel()
            self._forget(req)
            self._error(sock, req.id, TOO_BIG)

    def _body_pieces(self, req):
        """The request body as it arrives, piece by piece."""
        deadline = time.monotonic() + self.timings.http_timeout
        while True:
            with req.lock:
                while not req.body and not req.cancelled:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        req.cancelled = True
                        break
                    req.body_ready.wait(left)
                if req.cancelled:
                    raise ConnectionAbortedError("the request was given up")
                data, more = req.body.pop(0)
            yield data
            if not more:
                return

    def _connection(self):
        timeout = self.timings.http_timeout
        if self.app_scheme == "https":
            return http.client.HTTPSConnection(
                self.app_host,
                self.app_port,
                timeout=timeout,
                context=tls_context(),
            )
        return http.client.HTTPConnection(self.app_host, self.app_port, timeout=timeout)

    def _serve_http(self, sock, req, frame) -> None:
        started = time.monotonic()
        method = frame.get("method")
        path = frame.get("path")
        if not isinstance(method, str) or not _TOKEN.fullmatch(method):
            if req.finish():
                self._error(sock, req.id, "The request's method is not valid.")
            return
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in path)
        ):
            if req.finish():
                self._error(sock, req.id, "The request's path is not valid.")
            return
        pairs = pass_through_headers(_pairs(frame.get("headers")), request=True)
        first_more = frame.get("more") is True
        conn = self._connection()
        with req.lock:
            req.conn = conn
            if req.cancelled:
                return
        pieces = self._body_pieces(req)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", self.app_netloc)
            for name, value in pairs:
                conn.putheader(name, value)
            if first_more:
                # The body is still arriving: send it as it comes.
                conn.putheader("Transfer-Encoding", "chunked")
                conn.endheaders()
                for data in pieces:
                    if data:
                        conn.send(b"%x\r\n%s\r\n" % (len(data), data))
                conn.send(b"0\r\n\r\n")
            else:
                data = b"".join(pieces)
                if data or method.upper() not in _NO_BODY_METHODS:
                    conn.putheader("Content-Length", str(len(data)))
                conn.endheaders(data or None)
            resp = conn.getresponse()
            self._answer(sock, req, method, resp)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            if req.finish():
                self._error(
                    sock,
                    req.id,
                    f"SlowBooks Pro did not answer at {self.app_scheme}://"
                    f"{self.app_netloc}: {exc.__class__.__name__}.",
                )
            return
        finally:
            conn.close()
        log.info(
            "http %s -> %s in %.0f ms",
            method,
            resp.status,
            (time.monotonic() - started) * 1000,
        )

    def _answer(self, sock, req, method: str, resp) -> None:
        pairs = pass_through_headers(resp.getheaders(), request=False)
        head = {
            "t": "http.result",
            "id": req.id,
            "status": resp.status,
            "headers": headers_out(pairs),
        }
        no_body = (
            method.upper() == "HEAD" or resp.status in (204, 304) or resp.status < 200
        )
        length = resp.getheader("Content-Length")
        known = int(length) if length and length.isdigit() else None
        if no_body or (known is not None and known <= RAW_CHUNK):
            data = b"" if no_body else resp.read()
            if not req.finish():
                return
            self._send(
                sock,
                {**head, "body": base64.b64encode(data).decode("ascii"), "more": False},
            )
            return
        if known is not None and known > MAX_BODY:
            if req.finish():
                self._error(sock, req.id, TOO_BIG)
            return
        # Streamed: the status and headers at once, then the body as the app
        # gives it, in frames of at most CHUNK base64 characters.
        with req.lock:
            if req.done:
                return
        if not self._send(sock, {**head, "body": "", "more": True}):
            req.finish()
            return
        sent = 0
        while True:
            data = resp.read1(RAW_CHUNK)
            with req.lock:
                if req.done:
                    return
            sent += len(data)
            if sent > MAX_BODY:
                if req.finish():
                    self._error(sock, req.id, TOO_BIG)
                return
            if not data:
                if req.finish():
                    self._send(
                        sock,
                        {"t": "http.more", "id": req.id, "body": "", "more": False},
                    )
                return
            frame = {
                "t": "http.more",
                "id": req.id,
                "body": base64.b64encode(data).decode("ascii"),
                "more": True,
            }
            if not self._send(sock, frame):
                req.finish()
                return


def _body_sentence(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ""
    if isinstance(data, dict):
        text = data.get("error") or data.get("message")
        if isinstance(text, str):
            return text.strip().rstrip(".")
    return ""


def _handshake_reason(exc: ws.HandshakeError) -> str:
    said = _body_sentence(exc.body)
    if said:
        return said
    if exc.status:
        return f"the relay answered {exc.status}"
    return str(exc).rstrip(".")


# -- built from what is kept --------------------------------------------------------


def from_store(
    *, on_change=None, timings: Timings = DEFAULT_TIMINGS, ssl_context=None
) -> Line:
    """The line for this user's link, switches and SlowBooks Pro, or
    ConfigError saying what is missing."""
    made = account.load_link()
    if made is None:
        raise ConfigError(NOT_LINKED)
    mcp = problem = version = None
    try:
        config = load_config()
    except ConfigError as exc:
        problem = NO_TOKEN
        log.info("the line serves no tools: %s", exc)
        raw = os.environ.get("SBPLIVE_URL") or store.stored_url() or DEFAULT_URL
        app_url, _ = check_url(raw, must_resolve=False)
    else:
        # The companies this user added (sbplive add-books), or setup's one
        # token. Imported here: the product carries this module without it.
        from app.switchboard.books import provider

        app_url = config.base_url
        # Under the relay's 110 seconds, so the answer is the app's own.
        config = replace(config, timeout=min(config.timeout, MCP_TIMEOUT - 10))
        mcp = Mcp(build_registry(provider(config)), INSTRUCTIONS)
        try:
            company = config.company or None
            quick = ApiClient(
                urllib_transport(replace(config, timeout=5)),
                company=company,
                expect_served=company,
            )
            info = quick.get("/api/system")
            if isinstance(info, dict) and info.get("version"):
                version = str(info["version"])
        except (ApiError, Unconfirmed):
            pass
    return Line(
        made.line_url,
        made.device,
        made.credential,
        app_url=app_url,
        mcp=mcp,
        mcp_problem=problem,
        app_version=version,
        switches=account.load_switches(),
        timings=timings,
        ssl_context=ssl_context,
        on_change=on_change,
    )


NOT_LINKED = (
    "This computer is not linked to a SlowBooks Switchboard relay account. "
    "Run: sbplive link"
)
