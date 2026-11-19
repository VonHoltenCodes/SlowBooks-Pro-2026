# Vendored from SlowBooks Switchboard: sbplive/remote.py
# (see app/switchboard/SOURCE.md). Do not edit here: change the source and run scripts/sync_switchboard.py.
# ============================================================================
# The remote door: the same core (protocol.Session over the same tool
# registry) behind one HTTP endpoint, the MCP Streamable HTTP transport, for
# hosts that call in from outside this computer: Claude on the web, through
# the person's own Cloudflare Tunnel (docs/REMOTE.md). Standard library only:
# http.server's ThreadingHTTPServer.
#
#   sbplive serve-http [--host 127.0.0.1] [--port 8743]
#
# What it answers (docs/PROTOCOL.md, "The remote door", has every rule and
# its source):
#
# - One path, /mcp. Every other path is 404: no pages, no listing, nothing of
#   SlowBooks Pro's own; the books are reached only through the tools.
# - POST: one JSON-RPC message, answered with one application/json body.
#   Never an SSE stream: neither revision requires one for a request, and this
#   server sends no notifications or requests of its own.
#   * 2026-07-28 (a version in _meta): stateless; MCP-Protocol-Version,
#     Mcp-Method and, for tools/call, Mcp-Name must match the body (-32020);
#     the JSON-RPC error codes map to 400 and 404 as that revision says.
#   * 2025-11-25: initialize opens a session and its answer carries
#     Mcp-Session-Id; later requests carry it back; DELETE ends it.
# - GET is 405 (no stream is offered); DELETE without a session is 405.
#
# Sign-in, for this step: Authorization: Bearer <connector secret>. The
# connector secret is a random string this program makes and keeps beside
# the API token (mode 600). It is not the API token, which never leaves this
# computer. Wrong or missing: 401 with no detail; too many failures from one
# address: 429 for a while.
#
# With --public-url the door is also an OAuth authorization server (oauth.py,
# docs/AUTH.md): /mcp then answers a missing or wrong bearer token with the
# 401 that points Claude at the metadata, the well-known documents and the
# /oauth/ paths are served, and an access token from the sign-in is taken
# beside the connector secret. A 2025-11-25 session belongs to the sign-in
# that opened it.
#
# Logged: method, "the endpoint" or "another path", status, client address.
# Never a path, a query (the OAuth paths' carry codes), a header value or a
# body.
# ============================================================================

import base64
import binascii
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from app.switchboard import store
from app.switchboard.config import ConfigError
from app.switchboard.protocol import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    UNSUPPORTED_PROTOCOL_VERSION,
    Session,
)

log = logging.getLogger("sbplive")

DEFAULT_HOST = "127.0.0.1"
# Not 3000, 3001 (SlowBooks Pro itself), 8000, 8080, 8123 or 8765.
DEFAULT_PORT = 8743
ENDPOINT = "/mcp"

SECRET_PREFIX = "sbc_"
_SECRET_SHAPE = re.compile(r"sbc_[A-Za-z0-9_\-]{32,200}")

MAX_BODY = 4 * 1024 * 1024  # one request; a tool call is a few kilobytes
MAX_FAILURES = 10  # failed sign-ins from one address ...
FAILURE_WINDOW = 600.0  # ... within this many seconds, then 429
MAX_TRACKED = 4096  # addresses remembered by the failure count
MAX_SESSIONS = 256  # open 2025-11-25 sessions
SESSION_IDLE = 3600.0  # a session unused this long is forgotten (then 404)
SOCKET_TIMEOUT = 60  # seconds a connection may sit silent

# Origins a browser-borne request may carry. A request with no Origin (what
# a server-side client such as Claude's normally sends) is let through; the
# connector secret is what keeps strangers out. Anthropic's troubleshooting
# page warns that an over-strict Origin check rejects its requests without
# naming the value, so its two sites are allowed besides this computer.
ANTHROPIC_ORIGINS = ("https://claude.ai", "https://claude.com")
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

HEADER_MISMATCH = -32020  # 2026-07-28, Streamable HTTP, Server Validation
VERSION_HEADER = "MCP-Protocol-Version"
METHOD_HEADER = "Mcp-Method"
NAME_HEADER = "Mcp-Name"
SESSION_HEADER = "Mcp-Session-Id"
_ROUTING_HEADERS = (VERSION_HEADER, METHOD_HEADER, NAME_HEADER)
# Methods whose params[key] is mirrored in Mcp-Name (2026-07-28).
_NAME_BEARING = {"tools/call": "name", "resources/read": "uri", "prompts/get": "name"}
_B64 = re.compile(r"=\?base64\?(?P<payload>[A-Za-z0-9+/=]*)\?=")

# The HTTP status of a JSON-RPC error on the stateless (2026-07-28) path.
_MODERN_STATUS = {
    PARSE_ERROR: 400,
    INVALID_REQUEST: 400,
    INVALID_PARAMS: 400,
    HEADER_MISMATCH: 400,
    UNSUPPORTED_PROTOCOL_VERSION: 400,
    METHOD_NOT_FOUND: 404,
}
_ALLOW = "POST, DELETE"


# -- the connector secret ------------------------------------------------------


def new_secret() -> str:
    """256 random bits, URL-safe, with a prefix the log filter recognises."""
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def read_secret() -> str:
    """The stored connector secret, or ConfigError with the fix."""
    path = store.secret_path()
    if os.name != "nt" and os.stat(path).st_mode & 0o077:
        raise ConfigError(
            f"The connector secret {path} can be read by other users. "
            f"Run: chmod 600 {path}, or serve-http --new-secret to make a new one."
        )
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise ConfigError(f"The connector secret {path} could not be read.") from None
    if not _SECRET_SHAPE.fullmatch(text):
        raise ConfigError(
            f"The connector secret {path} is not one this program made. Run "
            "serve-http --new-secret to make a new one."
        )
    return text


def ensure_secret(replace: bool = False) -> tuple:
    """(the secret, whether it was made just now). Made on first use, or
    when replace is set; otherwise the stored one."""
    if store.has_secret() and not replace:
        return read_secret(), False
    secret = new_secret()
    store.save_secret(secret)
    return secret, True


# -- small rules -----------------------------------------------------------------


def check_origin_arg(raw: str) -> str:
    """An --allow-origin value, as scheme://host[:port], or ConfigError."""
    text = (raw or "").strip().lower().rstrip("/")
    try:
        parts = urlsplit(text)
        _ = parts.port  # raises ValueError on a bad port
    except ValueError:
        parts = None
    if (
        parts is None
        or parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.path
        or parts.query
        or parts.fragment
        or parts.username is not None
    ):
        raise ConfigError(
            f"{raw!r} is not an origin. An origin looks like https://example.com "
            "or http://localhost:5173, with nothing after the host and port."
        )
    return text


def origin_allowed(origin, extra=()) -> bool:
    if origin is None:
        return True  # not a browser
    text = origin.strip().lower().rstrip("/")
    if text in ANTHROPIC_ORIGINS or text in extra:
        return True
    try:
        parts = urlsplit(text)
        _ = parts.port  # raises ValueError on a bad port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or parts.path or parts.query:
        return False
    return (parts.hostname or "") in _LOOPBACK_HOSTS


def client_address(peer: str, headers) -> str:
    """Who is asking, for the failure count. Through cloudflared every request
    comes from this computer, so the visitor's address is read from
    CF-Connecting-IP, but only when the connection is from this computer: a
    computer elsewhere on the network could write any value there."""
    try:
        loopback = ipaddress.ip_address(peer.split("%", 1)[0]).is_loopback
    except ValueError:
        loopback = False
    if loopback:
        forwarded = (headers.get("CF-Connecting-IP") or "").strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return peer


def decode_header_value(value):
    """An Mcp-Name value, with the =?base64?...?= form decoded; None when it
    is malformed, so it can never match a body value by accident."""
    if value is None:
        return None
    m = _B64.fullmatch(value)
    if m is None:
        return value
    payload = m.group("payload")
    try:
        raw = base64.b64decode(payload, validate=True)
        if base64.b64encode(raw).decode("ascii") != payload:
            return None
        return raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None


def _accepts_json(accept: str) -> bool:
    for part in accept.split(","):
        media = part.split(";", 1)[0].strip().lower()
        if media in ("application/json", "application/*", "*/*"):
            return True
    return False


def _bearer(values) -> bytes | None:
    if not values or len(values) != 1:
        return None
    scheme, _, rest = values[0].strip().partition(" ")
    if scheme.lower() != "bearer" or not rest.strip():
        return None
    return rest.strip().encode("utf-8", "replace")


# -- the failure count -------------------------------------------------------------


class FailureLimit:
    """Failed sign-ins per client address in a sliding window. At the limit
    the address is refused (429) until its oldest failure leaves the window,
    the right secret included, so guessing cannot go on at speed."""

    def __init__(
        self,
        limit: int = MAX_FAILURES,
        window: float = FAILURE_WINDOW,
        max_tracked: int = MAX_TRACKED,
        clock=time.monotonic,
    ):
        self.limit, self.window, self.max_tracked = limit, window, max_tracked
        self.clock = clock
        self._seen: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def _recent(self, addr: str, now: float):
        times = self._seen.get(addr)
        if times is None:
            return None
        while times and now - times[0] >= self.window:
            times.popleft()
        if not times:
            del self._seen[addr]
            return None
        return times

    def wait(self, addr: str) -> float:
        """Seconds until addr may try again; 0 when it may now."""
        with self._lock:
            now = self.clock()
            times = self._recent(addr, now)
            if times is None or len(times) < self.limit:
                return 0.0
            return max(1.0, self.window - (now - times[0]))

    def failed(self, addr: str) -> None:
        with self._lock:
            now = self.clock()
            times = self._recent(addr, now)
            if times is None:
                times = self._seen[addr] = deque()
            times.append(now)
            self._seen.move_to_end(addr)
            while len(self._seen) > self.max_tracked:
                self._seen.popitem(last=False)

    def succeeded(self, addr: str) -> None:
        with self._lock:
            self._seen.pop(addr, None)


# -- the 2025-11-25 sessions -----------------------------------------------------


class Sessions:
    """Open handshake-era sessions by Mcp-Session-Id. A session unused for
    SESSION_IDLE is forgotten, and its id is then answered 404, which tells
    the client to initialize again."""

    def __init__(self, limit=MAX_SESSIONS, idle=SESSION_IDLE, clock=time.monotonic):
        self.limit, self.idle, self.clock = limit, idle, clock
        self._open: OrderedDict = OrderedDict()  # id -> [session, last used]
        self._lock = threading.Lock()

    def _expire(self, now: float) -> None:
        for sid in [
            s for s, (_, used) in self._open.items() if now - used >= self.idle
        ]:
            del self._open[sid]

    def add(self, session: Session) -> str | None:
        """The new session's id, or None when too many are open."""
        with self._lock:
            now = self.clock()
            self._expire(now)
            if len(self._open) >= self.limit:
                return None
            sid = secrets.token_urlsafe(24)  # visible ASCII, unguessable
            self._open[sid] = [session, now]
            return sid

    def get(self, sid: str) -> Session | None:
        with self._lock:
            now = self.clock()
            entry = self._open.get(sid)
            if entry is None:
                return None
            if now - entry[1] >= self.idle:
                del self._open[sid]
                return None
            entry[1] = now
            return entry[0]

    def close(self, sid: str) -> bool:
        with self._lock:
            return self._open.pop(sid, None) is not None

    def __len__(self) -> int:
        with self._lock:
            return len(self._open)


# -- the door ------------------------------------------------------------------------


class BodyError(Exception):
    def __init__(self, status: int):
        super().__init__(status)
        self.status = status


@dataclass
class Reply:
    status: int
    body: bytes = b""
    headers: dict = field(default_factory=dict)
    close: bool = False


def _rpc_error(id_, code: int, message: str, data=None) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": err}


def _json(status: int, message: dict, headers=None) -> Reply:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    out = {"Content-Type": "application/json"}
    out.update(headers or {})
    return Reply(status, body, out)


class Door:
    """Every HTTP request's answer, apart from the socket: handle() takes the
    method, path, headers (an email.message.Message, as http.server gives
    them), a body reader and the peer's address, and returns a Reply."""

    def __init__(
        self,
        registry,
        instructions: str,
        secret: str,
        *,
        allowed_origins=(),
        limiter: FailureLimit | None = None,
        sessions: Sessions | None = None,
        oauth=None,
    ):
        self.registry = registry
        self.instructions = instructions
        self._secret = secret.encode("utf-8")
        self.allowed_origins = tuple(allowed_origins)
        self.limiter = FailureLimit() if limiter is None else limiter
        self.sessions = Sessions() if sessions is None else sessions
        self.oauth = oauth  # an oauth.OAuthServer, or None: the secret only
        # The MCP core every HTTP-shaped door shares: server.Mcp.
        from app.switchboard.server import Mcp

        self.mcp = Mcp(registry, instructions, self.sessions)
        self._stateless = self.mcp.stateless

    # -- the HTTP layer ------------------------------------------------------

    def handle(self, method: str, raw_path: str, headers, read_body, peer: str):
        addr = client_address(peer, headers)
        path = raw_path.split("?", 1)[0].split("#", 1)[0]
        under = path == ENDPOINT or path.startswith(ENDPOINT + "/")
        signing_in = self.oauth is not None and self.oauth.owns(path)
        if signing_in:
            reply = self._oauth_path(method, raw_path, path, headers, read_body, addr)
        elif under:
            reply = self._handle(method, path, headers, read_body, addr)
        else:
            reply = Reply(404)
        # Never the path itself, only what kind it is. (The OAuth paths are
        # fixed words, but their queries carry codes.)
        where = (
            f"the sign-in ({path})"
            if signing_in
            else ("the endpoint" if under else "another path")
        )
        log.info("%s %s -> %s for %s", method, where, reply.status, addr)
        return reply

    def _handle(self, method, path, headers, read_body, addr) -> Reply:
        if not self._path_allowed(path):
            return Reply(404)
        if not origin_allowed(headers.get("Origin"), self.allowed_origins):
            log.warning("refused a request from %s: Origin not allowed", addr)
            return _json(
                403, _rpc_error(None, INVALID_REQUEST, "Forbidden: Origin not allowed.")
            )
        wait = self.limiter.wait(addr)
        if wait:
            return Reply(429, headers={"Retry-After": str(int(wait + 0.999))})
        owner, refusal = self._sign_in(path, headers, addr)
        if refusal is not None:
            return refusal
        self.limiter.succeeded(addr)
        if method == "POST":
            return self._post(headers, read_body, owner)
        if method == "DELETE":
            return self._delete(headers, owner)
        return Reply(405, headers={"Allow": _ALLOW})

    def _path_allowed(self, path: str) -> bool:
        return path in (ENDPOINT, ENDPOINT + "/")

    def _sign_in(self, path: str, headers, addr: str) -> tuple:
        """(who is signed in, None), or (None, the refusal to answer)."""
        given = _bearer(headers.get_all("Authorization"))
        owner = self._signed_in(given)
        if owner is not None:
            return owner, None
        if self.oauth is None or given is not None:
            # A missing token is how every OAuth sign-in starts, so with
            # OAuth on only a wrong one counts as a failure.
            self.limiter.failed(addr)
        log.warning("refused a request from %s: not signed in", addr)
        if self.oauth is not None:
            challenge = self.oauth.challenge(presented=given is not None)
            return None, Reply(401, headers=challenge.headers)
        return None, Reply(401, headers={"WWW-Authenticate": "Bearer"})

    def _signed_in(self, given: bytes | None) -> str | None:
        """Who the bearer token is: "secret" for the connector secret,
        "oauth:<family>" for a valid access token, None for nobody."""
        if given is None:
            return None
        if hmac.compare_digest(given, self._secret):
            return "secret"
        if self.oauth is not None:
            grant = self.oauth.authenticate(given.decode("utf-8", "replace"))
            if grant is not None:
                return f"oauth:{grant['family']}"
        return None

    def _oauth_path(self, method, raw_path, path, headers, read_body, addr) -> Reply:
        from app.switchboard import oauth

        query = raw_path.split("#", 1)[0].partition("?")[2]
        body = b""
        if method == "POST":
            try:
                body = read_body(oauth.MAX_FORM)
            except BodyError as exc:
                return Reply(exc.status, close=True)
        answer = self.oauth.handle(
            oauth.Request(
                method=method,
                path=path,
                query=query,
                headers=dict(headers.items()),
                body=body,
                addr=addr,
            )
        )
        if answer is None:
            return Reply(404)
        return Reply(answer.status, answer.body, dict(answer.headers))

    def _session_for(self, sid: str, owner: str) -> Session | None:
        """The open session sid, when it belongs to this sign-in."""
        session = self.sessions.get(sid)
        if session is None or getattr(session, "owner", owner) != owner:
            return None
        return session

    def _delete(self, headers, owner: str = "secret") -> Reply:
        sid = headers.get(SESSION_HEADER)
        if sid is None:
            return Reply(405, headers={"Allow": _ALLOW})
        if self._session_for(sid, owner) is None:
            return Reply(404)
        return Reply(200) if self.sessions.close(sid) else Reply(404)

    def _post(self, headers, read_body, owner: str = "secret") -> Reply:
        ctype = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return Reply(415)
        accept = headers.get("Accept")
        if accept is not None and not _accepts_json(accept):
            return Reply(406)
        try:
            raw = read_body(MAX_BODY)
        except BodyError as exc:
            return Reply(exc.status, close=True)
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return _json(400, _rpc_error(None, PARSE_ERROR, "Parse error."))
        from app.switchboard.server import serve_mcp

        status, extra, answer = serve_mcp(self.mcp, message, headers, owner)
        if answer is None:
            return Reply(status, headers=extra)
        return _json(status, answer, extra)


# -- the socket -------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "sbplive"
    sys_version = ""
    timeout = SOCKET_TIMEOUT
    # http.server's own error pages (a malformed request line) carry nothing.
    error_message_format = ""
    error_content_type = "text/plain"

    def _serve(self):
        door = self.server.door
        try:
            reply = door.handle(
                self.command,
                self.path,
                self.headers,
                self._read_body,
                self.client_address[0],
            )
        except Exception:  # a bug must not leave the client hanging
            log.exception("the remote door failed on a %s request", self.command)
            reply = Reply(500, close=True)
        self._send(reply)

    do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = do_HEAD = _serve
    do_OPTIONS = do_TRACE = do_CONNECT = _serve

    def _read_body(self, limit: int) -> bytes:
        coding = (self.headers.get("Transfer-Encoding") or "").strip().lower()
        if coding:
            if coding != "chunked":
                raise BodyError(400)
            return self._read_chunked(limit)
        length = self.headers.get("Content-Length")
        if length is None:
            raise BodyError(411)
        try:
            size = int(length)
        except ValueError:
            raise BodyError(400) from None
        if size < 0:
            raise BodyError(400)
        if size > limit:
            raise BodyError(413)
        return self.rfile.read(size)

    def _read_chunked(self, limit: int) -> bytes:
        data = bytearray()
        while True:
            line = self.rfile.readline(1024)
            try:
                size = int(line.split(b";", 1)[0].strip(), 16)
            except ValueError:
                raise BodyError(400) from None
            if size < 0:
                raise BodyError(400)
            if size == 0:
                while self.rfile.readline(8192) not in (b"\r\n", b"\n", b""):
                    pass  # trailers, ignored
                return bytes(data)
            if len(data) + size > limit:
                raise BodyError(413)
            data += self.rfile.read(size)
            self.rfile.readline(8)  # the CRLF after each chunk

    def _send(self, reply: Reply) -> None:
        self.send_response(reply.status)
        for name, value in reply.headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(reply.body)))
        if "Cache-Control" not in reply.headers:
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if reply.close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if self.command != "HEAD" and reply.body:
            self.wfile.write(reply.body)

    def log_message(self, format, *args):
        # http.server would print the request line, whose query can hold
        # an OAuth code; the door logs its own line instead.
        pass


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64


def make_server(door: Door, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    """The listening server, not yet serving; port 0 picks a free one."""
    cls = _Server
    if ":" in host:

        class _Server6(_Server):
            address_family = socket.AF_INET6

        cls = _Server6
    server = cls((host, port), _Handler)
    server.door = door
    return server


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def endpoint_url(host: str, port: int) -> str:
    shown = f"[{host}]" if ":" in host else host
    return f"http://{shown}:{port}{ENDPOINT}"
