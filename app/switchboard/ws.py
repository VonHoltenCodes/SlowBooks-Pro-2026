# Vendored from SlowBooks Switchboard: sbplive/ws.py
# (see app/switchboard/SOURCE.md). Do not edit here: change the source and run scripts/sync_switchboard.py.
# ============================================================================
# A WebSocket client (RFC 6455) on socket and ssl alone, for the line to the
# relay (line.py). Only what a client needs:
#
# - the opening handshake: GET with Upgrade, a random Sec-WebSocket-Key, and
#   the server's Sec-WebSocket-Accept checked against it; no subprotocol and
#   no extension is offered, so a server that names one is refused;
# - frames: text, binary, continuation (fragmented messages, with control
#   frames between the fragments), ping (answered with a pong at once), pong,
#   close (echoed, then the connection ends with ConnectionClosed); 7-, 16-
#   and 64-bit lengths; every frame this client sends is masked, and a
#   masked frame from the server is a protocol error, as the RFC says;
# - a cap on a message's size (MessageTooBig, close code 1009) checked from
#   the frame header, before the payload is read;
# - timeouts: one for connecting (TCP, TLS and the handshake together) and
#   one for reading. A read that times out ends the connection: the line
#   then reconnects, which is simpler and safer than resuming mid-frame.
# - one reading thread and any number of sending threads at once. A TLS
#   connection is not safe for a read in one thread and a write in another
#   at the same moment (OpenSSL keeps one state for both): on the live relay
#   that made a read report a false end of stream the instant an answer
#   went out, and the line dropped with 1006 (2026-11-19). So the socket is
#   non-blocking, every read and write on it holds one lock for that call
#   only, and the waiting for data or room is done outside the lock.
#
# TLS: wss:// is verified, always: the platform's trusted certificates and
# the host name. A context may be passed (the tests pass one that trusts a
# test CA), but one that does not verify is refused; there is no switch that
# turns verification off. Plain ws:// is accepted only for 127.0.0.1, which
# is where the tests run their fake relay.
# ============================================================================

import base64
import hashlib
import os
import select
import socket
import ssl
import struct
import threading
import time
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA
_CONTROL = (OP_CLOSE, OP_PING, OP_PONG)
_DATA = (OP_CONTINUATION, OP_TEXT, OP_BINARY)

CLOSE_NORMAL = 1000
CLOSE_GOING_AWAY = 1001
CLOSE_PROTOCOL_ERROR = 1002
CLOSE_NO_STATUS = 1005
CLOSE_ABNORMAL = 1006
CLOSE_INVALID_DATA = 1007
CLOSE_TOO_BIG = 1009

MAX_MESSAGE = 16 * 1024 * 1024  # the default cap on one received message
MAX_HANDSHAKE = 16 * 1024  # the server's handshake answer, headers only
_ERROR_BODY = 4096  # how much of a refused handshake's body is kept

# The only plain-text host: the tests' fake relay.
INSECURE_HOSTS = ("127.0.0.1",)


class WebSocketError(Exception):
    """Something went wrong on the WebSocket; the message is a sentence."""


class HandshakeError(WebSocketError):
    """The server answered the upgrade with something other than 101, or a
    101 that does not check out. status is the HTTP status (0 when the
    answer was not HTTP at all); body is the start of its body."""

    def __init__(self, message: str, status: int = 0, body: bytes = b""):
        super().__init__(message)
        self.status = status
        self.body = body


class ConnectionClosed(WebSocketError):
    """The connection ended: code and reason from the close frame, or 1006
    when it ended without one."""

    def __init__(self, code: int = CLOSE_ABNORMAL, reason: str = ""):
        super().__init__(
            f"the connection closed ({code}{': ' + reason if reason else ''})"
        )
        self.code = code
        self.reason = reason


class MessageTooBig(WebSocketError):
    """A message was larger than the cap; the connection is closed with 1009."""


class ProtocolError(WebSocketError):
    """The server broke RFC 6455; the connection is closed with 1002 or 1007."""


def check_url(url: str) -> tuple:
    """(secure, host, port, resource) for a ws:// or wss:// address."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise WebSocketError(f"{url!r} is not a WebSocket address.") from None
    if parts.scheme not in ("ws", "wss") or not parts.hostname:
        raise WebSocketError(f"{url!r} is not a WebSocket address (wss://host/path).")
    if parts.username is not None or parts.password is not None:
        raise WebSocketError("A WebSocket address may not carry a user name.")
    secure = parts.scheme == "wss"
    if not secure and parts.hostname not in INSECURE_HOSTS:
        raise WebSocketError(
            f"{url} is not encrypted. The relay is reached only over wss://."
        )
    resource = parts.path or "/"
    if parts.query:
        resource += "?" + parts.query
    return secure, parts.hostname, port or (443 if secure else 80), resource


# The oldest TLS the line speaks, set on every context rather than left to
# the platform's OpenSSL configuration.
TLS_MINIMUM = ssl.TLSVersion.TLSv1_2


def _verified(context) -> ssl.SSLContext:
    if context is None:
        context = ssl.create_default_context()
        context.minimum_version = TLS_MINIMUM
        return context
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise WebSocketError(
            "A TLS context that does not verify the certificate and the host "
            "name was given; there is no way to turn verification off."
        )
    # A context handed in keeps its certificates; its floor is raised.
    context.minimum_version = max(context.minimum_version, TLS_MINIMUM)
    return context


def _clean(value: str) -> str:
    if any(c in value for c in "\r\n\0"):
        raise WebSocketError("A handshake header may not hold a line break.")
    return value


def accept_for(key: str) -> str:
    """The Sec-WebSocket-Accept the server must answer for key."""
    digest = hashlib.sha1((key + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def mask(data: bytes, key: bytes) -> bytes:
    """XOR data with the four-byte key, repeated (masking and unmasking)."""
    n = len(data)
    if not n:
        return b""
    repeated = (key * (n // 4 + 1))[:n]
    value = int.from_bytes(data, "big") ^ int.from_bytes(repeated, "big")
    return value.to_bytes(n, "big")


def _parse_head(raw: bytes) -> tuple:
    """(status, {lowercased name: value}) from an HTTP response head."""
    try:
        text = raw.decode("iso-8859-1")
    except UnicodeDecodeError:  # pragma: no cover - latin-1 decodes anything
        raise HandshakeError("The relay's answer was not HTTP.") from None
    lines = text.split("\r\n")
    first = lines[0].split(" ", 2)
    if len(first) < 2 or not first[0].startswith("HTTP/1."):
        raise HandshakeError("The relay's answer was not HTTP.")
    try:
        status = int(first[1])
    except ValueError:
        raise HandshakeError("The relay's answer was not HTTP.") from None
    headers: dict = {}
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if not sep:
            raise HandshakeError("The relay's answer had a malformed header.")
        name, value = name.strip().lower(), value.strip()
        headers[name] = f"{headers[name]}, {value}" if name in headers else value
    return status, headers


def connect(
    url: str,
    headers=None,
    *,
    timeout: float = 15.0,
    read_timeout: float | None = None,
    ssl_context: ssl.SSLContext | None = None,
    max_message: int = MAX_MESSAGE,
) -> "WebSocket":
    """Open a WebSocket to url, sending headers (a dict) with the upgrade.

    timeout bounds connecting, TLS and the handshake; read_timeout bounds
    each read afterwards (None waits for ever)."""
    secure, host, port, resource = check_url(url)
    context = _verified(ssl_context) if secure else None
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        if context is not None:
            sock = context.wrap_socket(sock, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        default_port = 443 if secure else 80
        shown = f"[{host}]" if ":" in host else host
        host_header = shown if port == default_port else f"{shown}:{port}"
        lines = [
            f"GET {_clean(resource)} HTTP/1.1",
            f"Host: {host_header}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        for name, value in (headers or {}).items():
            lines.append(f"{_clean(str(name))}: {_clean(str(value))}")
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            if len(buf) > MAX_HANDSHAKE:
                raise HandshakeError("The relay's handshake answer was too long.")
            chunk = sock.recv(4096)
            if not chunk:
                raise HandshakeError(
                    "The relay closed the connection during the handshake."
                )
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        status, got = _parse_head(head)
        if status != 101:
            body = rest
            try:
                length = min(int(got.get("content-length", "0")), _ERROR_BODY)
            except ValueError:
                length = 0
            while len(body) < length:
                chunk = sock.recv(length - len(body))
                if not chunk:
                    break
                body += chunk
            raise HandshakeError(
                f"The relay answered {status} instead of opening the line.",
                status,
                body[:_ERROR_BODY],
            )
        if got.get("upgrade", "").lower() != "websocket":
            raise HandshakeError(
                "The relay's answer did not upgrade to a WebSocket.", 101
            )
        tokens = [t.strip().lower() for t in got.get("connection", "").split(",")]
        if "upgrade" not in tokens:
            raise HandshakeError(
                "The relay's answer did not upgrade the connection.", 101
            )
        if got.get("sec-websocket-accept") != accept_for(key):
            raise HandshakeError(
                "The relay's answer did not prove it read the handshake "
                "(Sec-WebSocket-Accept is wrong).",
                101,
            )
        if got.get("sec-websocket-extensions") or got.get("sec-websocket-protocol"):
            raise HandshakeError(
                "The relay chose an extension or subprotocol that was not offered.", 101
            )
        sock.settimeout(read_timeout)
        return WebSocket(sock, max_message=max_message, leftover=rest)
    except BaseException:
        sock.close()
        raise


class WebSocket:
    """An open client connection. recv() is for one reading thread; the send
    methods may be called from any thread."""

    def __init__(self, sock, *, max_message: int = MAX_MESSAGE, leftover: bytes = b""):
        self.sock = sock
        self.max_message = max_message
        self._buf = bytearray(leftover)
        self._send_lock = threading.Lock()  # one frame at a time
        self._message_lock = threading.Lock()  # one data message at a time
        # Held for each single read or write on the socket, never while
        # waiting: a TLS connection must not be read and written at once.
        self._io_lock = threading.RLock()
        self._timeout = sock.gettimeout()  # for each read, and each write
        sock.setblocking(False)
        self.close_sent = False
        self.closed = False
        self.on_pong = None  # called with the payload of each pong

    def settimeout(self, seconds: float | None) -> None:
        """How long a read (or a write) may wait; None waits for ever."""
        self._timeout = seconds

    # -- the socket, one call at a time ----------------------------------------

    def _adopt_timeout(self) -> None:
        # A caller that set the timeout on the socket itself (as line.py did
        # before settimeout() was here) made it blocking again: take the
        # value and make it non-blocking, so no call waits inside the lock.
        current = self.sock.gettimeout()
        if current != 0.0:
            self._timeout = current
            self.sock.setblocking(False)

    def _deadline(self):
        return None if self._timeout is None else time.monotonic() + self._timeout

    def _wait(self, want: str, deadline) -> None:
        """Wait, holding no lock, until the socket can be read ("r") or
        written ("w"); TimeoutError at the deadline."""
        left = None if deadline is None else deadline - time.monotonic()
        if left is not None and left <= 0:
            raise TimeoutError("timed out")
        if self.closed:
            return
        fd = self.sock.fileno()
        if fd < 0:
            return  # closed meanwhile: the next call says so
        if hasattr(select, "poll"):
            poller = select.poll()
            mask = select.POLLIN if want == "r" else select.POLLOUT
            poller.register(fd, mask | select.POLLERR | select.POLLHUP)
            ready = poller.poll(None if left is None else max(0, int(left * 1000)) + 1)
        else:  # pragma: no cover - Windows
            r, w, _ = select.select(
                [fd] if want == "r" else [], [fd] if want == "w" else [], [], left
            )
            ready = r or w
        if not ready and deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("timed out")

    def _recv_some(self, size: int) -> bytes:
        deadline = self._deadline()
        while True:
            with self._io_lock:
                if self.closed:
                    raise ConnectionClosed(CLOSE_ABNORMAL)
                self._adopt_timeout()
                try:
                    return self.sock.recv(size)
                except (ssl.SSLWantReadError, BlockingIOError, InterruptedError):
                    want = "r"
                except ssl.SSLWantWriteError:
                    want = "w"
            self._wait(want, deadline)

    def _write_all(self, data: bytes) -> None:
        view = memoryview(data)
        deadline = self._deadline()
        while view:
            with self._io_lock:
                if self.closed:
                    raise ConnectionClosed(CLOSE_ABNORMAL)
                self._adopt_timeout()
                try:
                    sent = self.sock.send(view)
                    want = ""
                except (ssl.SSLWantWriteError, BlockingIOError, InterruptedError):
                    sent, want = 0, "w"
                except ssl.SSLWantReadError:
                    sent, want = 0, "r"
            if sent:
                view = view[sent:]
            elif want:
                self._wait(want, deadline)

    # -- reading --------------------------------------------------------------

    def _exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            try:
                chunk = self._recv_some(max(65536, n - len(self._buf)))
            except TimeoutError:
                self.shutdown()
                raise
            except ConnectionClosed:
                self.shutdown()
                raise
            except OSError:
                self.shutdown()
                raise ConnectionClosed(CLOSE_ABNORMAL) from None
            if not chunk:
                self.shutdown()
                raise ConnectionClosed(CLOSE_ABNORMAL)
            self._buf += chunk
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def _fail(self, exc: WebSocketError, code: int):
        try:
            self.close(code)
        finally:
            self.shutdown()
        raise exc

    def _frame(self, budget: int) -> tuple:
        """(fin, opcode, payload) of the next frame; budget is how many more
        payload bytes the message in progress may take."""
        b0, b1 = self._exact(2)
        fin, opcode = bool(b0 & 0x80), b0 & 0x0F
        if b0 & 0x70:
            self._fail(
                ProtocolError("The relay used a WebSocket extension bit."),
                CLOSE_PROTOCOL_ERROR,
            )
        if b1 & 0x80:
            self._fail(
                ProtocolError("The relay masked a frame, which a server may not do."),
                CLOSE_PROTOCOL_ERROR,
            )
        if opcode not in _CONTROL and opcode not in _DATA:
            self._fail(
                ProtocolError(f"The relay sent an unknown frame type {opcode}."),
                CLOSE_PROTOCOL_ERROR,
            )
        length = b1 & 0x7F
        if opcode in _CONTROL and (length > 125 or not fin):
            self._fail(
                ProtocolError("The relay sent a malformed control frame."),
                CLOSE_PROTOCOL_ERROR,
            )
        if length == 126:
            (length,) = struct.unpack("!H", self._exact(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._exact(8))
            if length >> 63:
                self._fail(
                    ProtocolError("The relay sent a frame length out of range."),
                    CLOSE_PROTOCOL_ERROR,
                )
        if opcode in _DATA and length > budget:
            self._fail(
                MessageTooBig(
                    f"The relay sent a message larger than {self.max_message} bytes."
                ),
                CLOSE_TOO_BIG,
            )
        return fin, opcode, self._exact(length)

    def recv(self) -> tuple:
        """The next message: ("text", str) or ("binary", bytes). Pings are
        answered and pongs reported on the way; a close frame is echoed and
        raises ConnectionClosed. A read timeout raises TimeoutError and
        ends the connection."""
        if self.closed:
            raise ConnectionClosed(CLOSE_ABNORMAL)
        kind = None
        parts: list = []
        size = 0
        while True:
            fin, opcode, payload = self._frame(self.max_message - size)
            if opcode == OP_PING:
                self._send(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                if self.on_pong is not None:
                    self.on_pong(payload)
                continue
            if opcode == OP_CLOSE:
                self._closed_by_peer(payload)
            if opcode == OP_CONTINUATION:
                if kind is None:
                    self._fail(
                        ProtocolError("The relay continued a message it never began."),
                        CLOSE_PROTOCOL_ERROR,
                    )
            else:
                if kind is not None:
                    self._fail(
                        ProtocolError("The relay began a message inside another."),
                        CLOSE_PROTOCOL_ERROR,
                    )
                kind = "text" if opcode == OP_TEXT else "binary"
            parts.append(payload)
            size += len(payload)
            if fin:
                data = b"".join(parts)
                if kind == "binary":
                    return kind, data
                try:
                    return kind, data.decode("utf-8")
                except UnicodeDecodeError:
                    self._fail(
                        ProtocolError("The relay sent text that is not UTF-8."),
                        CLOSE_INVALID_DATA,
                    )

    def _closed_by_peer(self, payload: bytes):
        code, reason = CLOSE_NO_STATUS, ""
        if len(payload) >= 2:
            (code,) = struct.unpack("!H", payload[:2])
            reason = payload[2:].decode("utf-8", "replace")
        try:
            if not self.close_sent:
                self.close(code if code != CLOSE_NO_STATUS else CLOSE_NORMAL)
        finally:
            self.shutdown()
        raise ConnectionClosed(code, reason)

    # -- writing --------------------------------------------------------------

    def _send(self, opcode: int, payload: bytes, fin: bool = True) -> None:
        header = bytearray([(0x80 if fin else 0) | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack("!H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", n)
        key = os.urandom(4)
        frame = bytes(header) + key + mask(payload, key)
        with self._send_lock:
            if self.closed or (self.close_sent and opcode != OP_CLOSE):
                raise ConnectionClosed(CLOSE_ABNORMAL)
            try:
                self._write_all(frame)
            except OSError:  # TimeoutError too: a write that cannot go out
                raise ConnectionClosed(CLOSE_ABNORMAL) from None

    def send(self, data, *, fragment: int | None = None) -> None:
        """Send a text (str) or binary (bytes) message, split into frames of
        at most fragment bytes when fragment is given."""
        if isinstance(data, str):
            opcode, payload = OP_TEXT, data.encode("utf-8")
        else:
            opcode, payload = OP_BINARY, bytes(data)
        if not fragment or len(payload) <= fragment:
            with self._message_lock:
                self._send(opcode, payload)
            return
        # The fragments of one message go out together (no other message's
        # frames between them, as the RFC requires); control frames, which
        # take only the frame lock, may still come between them.
        pieces = [payload[i : i + fragment] for i in range(0, len(payload), fragment)]
        with self._message_lock:
            for i, piece in enumerate(pieces):
                last = i == len(pieces) - 1
                self._send(opcode if i == 0 else OP_CONTINUATION, piece, last)

    def send_text(self, text: str) -> None:
        self.send(text)

    def ping(self, payload: bytes = b"") -> None:
        self._send(OP_PING, payload[:125])

    def close(self, code: int = CLOSE_NORMAL, reason: str = "") -> None:
        """Send a close frame (once). The reading thread then receives the
        server's close and ends; shutdown() ends the socket at once."""
        if self.close_sent or self.closed:
            return
        payload = struct.pack("!H", code) + reason.encode("utf-8")[:123]
        try:
            self._send(OP_CLOSE, payload)
        except ConnectionClosed:
            pass
        self.close_sent = True

    def shutdown(self) -> None:
        with self._io_lock:  # never held while waiting, so this is quick
            if self.closed:
                return
            self.closed = True
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
