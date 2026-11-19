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

SERVER_INFO = {
    "name": "sbplive",
    "title": "SlowBooks Switchboard",
    "version": __version__,
}

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

    def __init__(self, registry: Registry, instructions: str = ""):
        self.registry = registry
        self.instructions = instructions
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
            result_meta[META_SERVER_INFO] = dict(SERVER_INFO)
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
            "serverInfo": dict(SERVER_INFO),
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
