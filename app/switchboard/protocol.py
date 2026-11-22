# Vendored from SlowBooks Switchboard: sbplive/protocol.py
# (see app/switchboard/SOURCE.md). Do not edit here: change the source and run scripts/sync_switchboard.py.
# ============================================================================
# The protocol: JSON-RPC 2.0 messages in both eras of MCP, one process per
# client. Transport-free: a Session takes one decoded line and gives back the
# reply to write, or None. The stdio loop is serve_stdio at the bottom; the
# remote door (remote.py, serve-http) reuses Session.
#
# Two eras (docs/PROTOCOL.md says which rule comes from where):
# - 2025-11-25, the handshake era: initialize, then requests that rely on it.
# - 2026-07-28, the modern era: no handshake; every request carries its
#   version and the client's capabilities in _meta and is served on its own.
# A request is modern when its _meta names a protocol version; initialize
# opens the handshake era for the process. Both are served side by side.
#
# The revisions are the constants below and nowhere else. Adding or
# dropping one is a deliberate commit with docs/PROTOCOL.md updated and the
# contract test rerun.
# ============================================================================

import json
import logging
import sys
import typing

from app.switchboard import __version__
from app.switchboard.registry import Registry

# The answer to initialize: the newest handshake-era revision.
PROTOCOL_VERSION = "2025-11-25"
# Revisions served per request, with no handshake.
MODERN_VERSIONS = ("2026-07-28",)
# Revisions served through initialize.
HANDSHAKE_VERSIONS = (PROTOCOL_VERSION,)
# Every revision this server speaks, newest first: server/discover's
# supportedVersions and the "supported" list of an unsupported-version error.
SUPPORTED_VERSIONS = MODERN_VERSIONS + HANDSHAKE_VERSIONS

# The reserved _meta keys of a modern request and result.
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

# How long a modern client may keep server/discover's and tools/list's
# answers: the tools are fixed for the life of the process and the same for
# every caller, whatever the token's role.
CACHE_TTL_MS = 3_600_000
CACHE_SCOPE = "public"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_PROTOCOL_VERSION = -32022  # 2026-07-28

# Who the server is (Implementation, in both eras). The name is what
# clients key on and never changes; the title is what a person sees.
SERVER_NAME = "sbplive"
SERVER_TITLE = "SlowBooks Pro"
WEBSITE_URL = "https://www.slowbookspro.com/connect/"
SERVER_INFO = {
    "name": SERVER_NAME,
    "title": SERVER_TITLE,
    "version": __version__,
    "websiteUrl": WEBSITE_URL,
}

# The icon files a server with a web address of its own serves at its root:
# path, MIME type, sizes. PNG first, which every client that shows icons
# must take; SVG, which it should, last.
ICON_FILES = (
    ("/icon-48.png", "image/png", "48x48"),
    ("/icon-96.png", "image/png", "96x96"),
    ("/icon-192.png", "image/png", "192x192"),
    ("/icon-512.png", "image/png", "512x512"),
    ("/icon.svg", "image/svg+xml", "any"),
)

# -- ICON: the block below is written by tools/make_icons.py --
# The icon as data: URIs (PNG, 64 colors), for a server with no web
# address of its own: stdio and a self-hosted door. A server that has one
# points at its own files instead (icons_at).
_ICON_PNG_48 = (
    "iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAMAAABg3Am1AAAAwFBMVEX////+/v79/v38/fz6/Pr3"
    "+fXz9/Pw9vDs8uvp8ejq7+Xk7uTi7OHg6+De6t3d59jY5tfU5NTQ4c/N38zJ3cjbzqLD2MK81Lu1"
    "0LSzzK2ryamox6bdunWzwpefwZ3QokXMmTOXvJWRuI+LtYmIsYODr4GFqnR+rHx8q3p3qHRxpG5r"
    "oGhmnWNfmFxZlFZWklNRkE5PjkxLi0dDhkA/hDw9gTg3fzM1fTIxey0ueCkrdycpdiUodSQndCMm"
    "dCIlcyGoio+GAAAB9UlEQVR42u2V/bZyQBTGFSVvKlII6SD5zmek0tz/XR0zdM46L9bqAnr+MWN+"
    "e2bvxwaGffQR0kq1bHX1Nr7wAdTTId/j2Rzi9zQKtbd4PAXlab/dKlDbNwI21emw/XppMsiNyWkz"
    "kC6nyFIQrRy+Bipnj/ENgDLUKAwTYAW7Q81vPVP514fPTfDSTScW8Foe98o+A+DcgxNmlYjuo6Ye"
    "WZIU3uiGQh/wDrh0+ekZeigxrsZSDDMncLxsDvMM6wyc7v4BWk0nCzNDG8vrV3r1GWHXJB2thdTx"
    "0VD5zHkFFKGMd7sAJZxQ5xYqOb5Cg6tA9PqJuubOei2fc0zRjNR+/5dVbEkcJba8S/ItnxEDbWA4"
    "QRStwqYQnrSqNnTXz48StMrA5F1uIueven2i/4TGwRufOeJkrhc/zzuYuinVX3IRORqPjRktekLn"
    "XVnTL8AmDACigT6dLAXNb/cuBedZP/QNhkzQOwX4lyy9gl9dJdSEcW3eHYZ3kiIEM2gC0rNnyhxO"
    "3VFcvWT2HlGLRL4E42ZGy1DQUQommfUEqLCDXRrDZmtRNexGhiqy09XOTujua5kGMj0TjSAH/+mZ"
    "WOKsxyJyY8RVGXuWLgvrJT0nSZyc0QzL7zTbT+IjN/rbqrrvqPx8NPhdWIoSO/6dMpvp56fw0V99"
    "A5DOcbjl1vD+AAAAAElFTkSuQmCC"
)
_ICON_PNG_96 = (
    "iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAMAAADVRocKAAAAwFBMVEX////+/v79/v38/fz6/Pr3"
    "+vf2+PPw9vDu9O3q8urq8Obl7uTi7OHo6dfc6NvZ5tjX5dbV5NTT49Pj38PM3svF2sTA17++07i0"
    "z7PfwIG3yaaqyKinxqWkxKKewZyUupKQuI6MtYqHs4XRokbMmTTMmTOSqm9/rX16qXh1p3Nvomxr"
    "oGhkm2FfmFxak1NVklJSkE9Rj01MjElIikVHiEJChj87gTc3fzM1fTEyey4veisteCkrdiYodSQm"
    "dCIlcyEaYSv7AAAEc0lEQVR42u1YWZuiOhCNioiYVlvZHGS3L7sbrijy///VIDYSwZ4Od+yX+ThP"
    "EsmppFJ1qgIANWrUqFGjRo1/BS04YvrkT7H31eU5TnDZ6MMfoGf8OMfF7b2YnjAv8QMO7Ev5yXlc"
    "xGn0Qv4m4p5wM3cdy9SUV25BvnHvrClDkfTb2/gT9Iv424cr/cZoTAxj9oFi8hoDQkK/NT5m5EcR"
    "s9fkhBWfZzNjMiGNkoXxSwx48X+TX6Zj9yYlA//HRwQFIfUwMo+j9JDdMUo9HhuVDRCM6m9TstPG"
    "FmE27GQhyuRbmPR19c2oZmBkHR9zaS4Q6R9aNhDQmYUJf4rj5dh4w6dnF3EZW/76l5g/s+OJ8WFM"
    "xmq6z72Ar5Si/MxAHHtJMvWR55UqCFPrkG0JV+aTBR0kqB+eWNiPQOMYf4EtHj99kzKfJpU7Vbjy"
    "Xcdxl7vYBWCNkh7D/PcCi3+4zxbLgU4SMZelwvYad51oFwxclOniegLX4qNiVZLcMZEIACd2k8Hu"
    "SJiKPPueJkSz4CKfpUYMlyjssYtzvOjsy1XkSd7a5Q7xSTAs+T7cXN84cxj81Bbl15qAUh6PetvK"
    "86BQ1HD4G97jjKYcFmh40HkWXHFkY1WDKTJlNwC9Uml0wJMNhIEvd7ECqIesNxyA99Jal20wOD2M"
    "rAW6ia8PNjJTAMMS/6YLyOBhZNGuIm9DpBdxAdyX1k8Bwn30fL+SPCOTD91WSYxcErScxyG7Wi+Y"
    "buASOLrEQyAVGx85SQnvcexcraHT4stC4zqfklBwkJfUGxgUjJrVCpgqwQEnKqZlW2xhA0FSCBpS"
    "MSfCbiV+Sg+ibKoIlii9kERi3y/FvwwqeigH2831wuMSesqMSvxLohJ/A1UhKGR5pFzPkVKe1Jgj"
    "BBSs0vCjKtdWU9coaZi/W6cn4hOygFyfJXwDJipDwNq5Uro8SnxenPcMIJJTuWBbGJzzJPOz0+tK"
    "PuL6k6/Jqnd7L4CgkQrLhcPfQLRbu7rE0VnXJc/Ra0yodHqyZcnQSkbtpMtV70eB10l0OsgNhlX8"
    "gt/XsGNHtyohLXi0O1pWUFNA0Iyoe9uyywOKzkaP6Rbb+QKm37I6y2C1Wvjz4MuGJxoRq/uDVgiK"
    "3XeS3dBO8Z+xm6LScet+qFysvo+krrKIvuA+zE1x0ALtXal/y1vUNY73O4yko2qzdm1dFhgauTYV"
    "DDRybcW9wiLVZFXwq77KkVUZsapsM+ih4kT2PQsPLcy7VyFQ7mkB+yMEQzqTUWIkmqsI10eDz7zd"
    "u9Lwljs9VlQsLwifKOlmbsk8JG5Zqa0kXLUL3Wm/kX4I4hVvF3+HKLCn/eticJK5fTg64vUiTfHm"
    "8hTjY++IWLUfcsmGW6y+zhXutJm7liaLAseMIOx8ogfhO8MLkqI799zfmCzGObd55/b+ceVqU25A"
    "YX246HOS7m2SO5fF/bGENjk7TPNWYihQHcS7qPuB9qV008r2NNeFv/0w1uO5p59DBootMwT4MRAk"
    "qFGjRo0aNWr8u/gNTcXIDcapHjUAAAAASUVORK5CYII="
)
# -- ICON: end of the generated block --


def data_icons() -> list:
    """The icons as data: URIs: for stdio and a self-hosted door, which
    have no web address of their own to point at (docs/PROTOCOL.md, "Who
    the server is")."""
    return [
        {
            "src": "data:image/png;base64," + png,
            "mimeType": "image/png",
            "sizes": [f"{size}x{size}"],
        }
        for size, png in ((48, _ICON_PNG_48), (96, _ICON_PNG_96))
    ]


def icons_at(origin: str) -> list:
    """The icons as the files served at origin's root (the server's own
    address: the specification asks a client to take icons from the
    server's origin only)."""
    origin = origin.rstrip("/")
    return [
        {"src": origin + path, "mimeType": mime, "sizes": [sizes]}
        for path, mime, sizes in ICON_FILES
    ]


def make_server_info(icons: list | None = None) -> dict:
    """serverInfo with icons: the given ones, or the data: URIs."""
    info = dict(SERVER_INFO)
    info["icons"] = data_icons() if icons is None else [dict(i) for i in icons]
    return info


log = logging.getLogger("sbplive")


def _capabilities() -> dict:
    # Tools only, in both eras; no list-changed notifications.
    return {"tools": {"listChanged": False}}


def _error(id_, code: int, message: str, data=None) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": err}


def _result(id_, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _valid_id(value) -> bool:
    # MCP: a string or an integer, never null (unlike base JSON-RPC).
    return isinstance(value, str) or (
        isinstance(value, int) and not isinstance(value, bool)
    )


class Session:
    """One process's client: the handshake state, if the client opened with
    initialize, and the request handlers of both eras."""

    def __init__(self, registry: Registry, instructions: str = "", server_info=None):
        self.registry = registry
        self.instructions = instructions
        # initialize's serverInfo and server/discover's; every other modern
        # result names the server without the icons (SERVER_INFO).
        self.server_info = make_server_info() if server_info is None else server_info
        self.initialized = False  # initialize answered
        self.ready = False  # notifications/initialized received
        self.client_info: dict = {}
        self.client_version: str | None = None

    # -- framing -----------------------------------------------------------

    def handle_line(self, line) -> dict | None:
        """One line off the wire; the reply to write, or None."""
        if isinstance(line, bytes):
            try:
                line = line.decode("utf-8")
            except UnicodeDecodeError:
                return _error(
                    None, PARSE_ERROR, "Parse error: the message is not UTF-8."
                )
        if not line.strip():
            return None
        try:
            message = json.loads(line)
        except ValueError:
            return _error(None, PARSE_ERROR, "Parse error: the line is not JSON.")
        return self.handle_message(message)

    def handle_message(self, message) -> dict | None:
        if isinstance(message, list):
            # JSON-RPC batching was removed from MCP in 2025-06-18.
            return _error(
                None,
                INVALID_REQUEST,
                "Invalid Request: MCP does not allow batches (removed in "
                "2025-06-18); send one message per line.",
            )
        if not isinstance(message, dict):
            return _error(
                None, INVALID_REQUEST, "Invalid Request: a message is a JSON object."
            )
        has_id = "id" in message
        id_ = message.get("id") if _valid_id(message.get("id")) else None
        if message.get("jsonrpc") != "2.0":
            return _error(
                id_, INVALID_REQUEST, 'Invalid Request: jsonrpc must be "2.0".'
            )
        method = message.get("method")
        if method is None:
            if has_id and ("result" in message or "error" in message):
                # A response: this server sends no requests, so none is
                # awaited. Responses are never answered.
                log.debug("ignored a response to id %r", message.get("id"))
                return None
            return _error(
                id_, INVALID_REQUEST, "Invalid Request: the message has no method."
            )
        if not isinstance(method, str):
            return _error(
                id_, INVALID_REQUEST, "Invalid Request: method must be a string."
            )
        params = message.get("params")
        if not has_id:
            self._notify(method, params)
            return None
        if id_ is None:
            return _error(
                None,
                INVALID_REQUEST,
                "Invalid Request: id must be a string or an integer, not null.",
            )
        if params is not None and not isinstance(params, dict):
            return _error(id_, INVALID_PARAMS, "Invalid params: params is an object.")
        try:
            return self._request(id_, method, params or {})
        except Exception:  # a bug must not end the session
            log.exception("request %s failed", method)
            return _error(id_, INTERNAL_ERROR, "Internal error: see the server's log.")

    # -- notifications -----------------------------------------------------

    def _notify(self, method: str, params) -> None:
        if method == "notifications/initialized":
            if not self.initialized:
                log.warning("initialized notification before initialize")
                return
            self.ready = True
            return
        # notifications/cancelled and anything else: requests are answered
        # in order and quickly, so there is nothing to cancel or act on.
        log.debug("ignored notification %s", method)

    # -- requests ----------------------------------------------------------

    # Handshake era: the methods of 2025-11-25.
    _METHODS: typing.ClassVar[dict] = {
        "initialize": "_initialize",
        "ping": "_ping",
        "tools/list": "_tools_list",
        "tools/call": "_tools_call",
    }
    # Modern era: the methods of 2026-07-28 this server implements (ping and
    # initialize are gone from it).
    _MODERN_METHODS: typing.ClassVar[dict] = {
        "server/discover": "_discover",
        "tools/list": "_tools_list",
        "tools/call": "_tools_call",
    }

    def _request(self, id_, method: str, params: dict) -> dict:
        meta = params.get("_meta")
        if meta is not None and not isinstance(meta, dict):
            return _error(id_, INVALID_PARAMS, "Invalid params: _meta is an object.")
        if method != "initialize" and meta is not None and META_VERSION in meta:
            return self._modern(id_, method, params, meta)
        return self._handshake_era(id_, method, params)

    # -- the handshake era (2025-11-25) --------------------------------------

    def _handshake_era(self, id_, method: str, params: dict) -> dict:
        handler = self._METHODS.get(method)
        if handler is None and method not in self._MODERN_METHODS:
            return _error(id_, METHOD_NOT_FOUND, f"Method not found: {method}")
        if method in ("initialize", "ping"):
            return getattr(self, handler)(id_, params)
        if handler is None:
            # server/discover is modern only, and it came without a version.
            return _error(
                id_,
                INVALID_PARAMS,
                f"Invalid params: {method} needs {META_VERSION} "
                f"({', '.join(MODERN_VERSIONS)}) and {META_CAPABILITIES} in _meta.",
            )
        if not self.initialized:
            # No handshake and no version: the modern rule applies, which
            # makes a request without its required _meta fields -32602. A
            # dual-era client falls back to initialize on it.
            return _error(
                id_,
                INVALID_PARAMS,
                f"Invalid params: {method} carries no {META_VERSION} in _meta "
                "and the session is not initialized; send initialize first, "
                f"or give the version ({', '.join(MODERN_VERSIONS)}) and "
                f"{META_CAPABILITIES} in _meta.",
            )
        return getattr(self, handler)(id_, params)

    # -- the modern era (2026-07-28) -----------------------------------------

    def _modern(self, id_, method: str, params: dict, meta: dict) -> dict:
        """One stateless request: everything it needs is in its own _meta;
        nothing from earlier requests, the handshake included, is used."""
        requested = meta.get(META_VERSION)
        if not isinstance(requested, str) or not requested:
            return _error(
                id_,
                INVALID_PARAMS,
                f"Invalid params: {META_VERSION} is a version string.",
            )
        if requested not in MODERN_VERSIONS:
            how = (
                " it is served through initialize, not per request"
                if requested in HANDSHAKE_VERSIONS
                else " this server does not speak it"
            )
            return _error(
                id_,
                UNSUPPORTED_PROTOCOL_VERSION,
                f"Unsupported protocol version: {requested};{how}. "
                f"Per request: {', '.join(MODERN_VERSIONS)}; through "
                f"initialize: {', '.join(HANDSHAKE_VERSIONS)}.",
                {"supported": list(SUPPORTED_VERSIONS), "requested": requested},
            )
        if not isinstance(meta.get(META_CAPABILITIES), dict):
            return _error(
                id_,
                INVALID_PARAMS,
                f"Invalid params: a {requested} request needs "
                f"{META_CAPABILITIES} in _meta, an object.",
            )
        if META_CLIENT_INFO in meta and not isinstance(meta[META_CLIENT_INFO], dict):
            return _error(
                id_, INVALID_PARAMS, f"Invalid params: {META_CLIENT_INFO} is an object."
            )
        handler = self._MODERN_METHODS.get(method)
        if handler is None:
            return _error(id_, METHOD_NOT_FOUND, f"Method not found: {method}")
        log.debug("%s at %s", method, requested)
        reply = getattr(self, handler)(id_, params)
        if "result" in reply:
            result = dict(reply["result"])
            result["resultType"] = "complete"
            result_meta = dict(result.get("_meta") or {})
            result_meta[META_SERVER_INFO] = dict(
                self.server_info if method == "server/discover" else SERVER_INFO
            )
            result["_meta"] = result_meta
            if method in ("server/discover", "tools/list"):
                result["ttlMs"] = CACHE_TTL_MS
                result["cacheScope"] = CACHE_SCOPE
            reply = _result(id_, result)
        return reply

    def _discover(self, id_, params: dict) -> dict:
        result: dict = {
            "supportedVersions": list(SUPPORTED_VERSIONS),
            "capabilities": _capabilities(),
        }
        if self.instructions:
            result["instructions"] = self.instructions
        return _result(id_, result)

    # -- the handshake itself --------------------------------------------------

    def _initialize(self, id_, params: dict) -> dict:
        if self.initialized:
            return _error(
                id_,
                INVALID_REQUEST,
                "Invalid Request: the session is already initialized.",
            )
        requested = params.get("protocolVersion")
        if not isinstance(requested, str) or not requested:
            return _error(
                id_,
                INVALID_PARAMS,
                "Invalid params: initialize needs protocolVersion as a string.",
            )
        for name in ("capabilities", "clientInfo"):
            if name in params and not isinstance(params[name], dict):
                return _error(
                    id_, INVALID_PARAMS, f"Invalid params: {name} is an object."
                )
        self.client_info = params.get("clientInfo") or {}
        self.client_version = requested
        if requested != PROTOCOL_VERSION:
            # The revision's rule: answer with a version the server supports;
            # the client disconnects if it cannot speak it.
            log.info("client asked for %s; offering %s", requested, PROTOCOL_VERSION)
        self.initialized = True
        result: dict = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": _capabilities(),
            "serverInfo": dict(self.server_info),
        }
        if self.instructions:
            result["instructions"] = self.instructions
        return _result(id_, result)

    def _ping(self, id_, params: dict) -> dict:
        return _result(id_, {})

    # -- both eras ------------------------------------------------------------

    def _tools_list(self, id_, params: dict) -> dict:
        if params.get("cursor") is not None:
            # Every tool comes in the first page, so no cursor was ever given.
            return _error(
                id_,
                INVALID_PARAMS,
                "Invalid params: unknown cursor; the first page lists every tool.",
            )
        return _result(id_, {"tools": self.registry.definitions()})

    def _tools_call(self, id_, params: dict) -> dict:
        name = params.get("name")
        if not isinstance(name, str):
            return _error(
                id_, INVALID_PARAMS, "Invalid params: tools/call needs a tool name."
            )
        if name not in self.registry:
            return _error(id_, INVALID_PARAMS, self.registry.unknown_sentence(name))
        args = params.get("arguments")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return _error(
                id_, INVALID_PARAMS, "Invalid params: arguments is an object."
            )
        return _result(id_, self.registry.call(name, args))


def encode(reply: dict) -> bytes:
    """One message on one line: JSON escapes every newline inside strings."""
    return (json.dumps(reply, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def serve_stdio(session: Session, stdin=None, stdout=None) -> int:
    """Read requests line by line until stdin closes; write replies to stdout."""
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    for line in stdin:
        reply = session.handle_line(line)
        if reply is not None:
            stdout.write(encode(reply))
            stdout.flush()
    return 0
