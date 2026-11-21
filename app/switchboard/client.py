# Vendored from SlowBooks Switchboard: sbplive/client.py
# (see app/switchboard/SOURCE.md). Do not edit here: change the source and run scripts/sync_switchboard.py.
# ============================================================================
# The HTTP client: every tool call is one request to the running app under
# the token. Standard library only, so this module needs no SDK. The
# transport is a plain callable, which is how the tests run the tools
# against the app in-process.
#
# What the program refuses on its own: nothing the API decides for the
# token's role. The rule is to match the software.
# Tokens, users, backups, the company file and system go to the API like
# everything else, and its answer is what the person hears. One thing stays
# refused here: paths outside /api/ (and /openapi.json). The pages, /health
# and the payment and portal pages are not the API.
#
# Making a backup (POST /api/backups) is the API's to decide like the rest:
# the app lets a token an administrator ticked "Can make backups" make one
# whatever its role, and this program does not second-guess that.
#
# The files the API returns (PDFs, CSV and IIF downloads, print pages) and
# uploads are refused in operations.py, since they cannot be carried here.
#
# HTTPS certificates are always verified, with no switch to turn that off,
# and redirects are never followed: the token is sent only to the address
# the person gave setup.
#
# One client works one company's books. A client made for a company sends
# X-SlowBooks-Company with that company's file on every request (no tool can
# set or change it), and with expect_served it takes an answer only when
# SlowBooks Pro says, in X-SlowBooks-Company-Served, that it answered from
# that same company. Every client of one connection shares one copy of the
# OpenAPI document (a SpecHolder): the document is the same for every company
# the app serves.
# ============================================================================

import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from app.switchboard.config import Config

log = logging.getLogger("sbplive")

# (method, path, params, body, headers) -> (status, parsed JSON or text), or
# (status, parsed JSON or text, the answer's headers): the headers are needed
# when the client checks which company answered.
Transport = Callable[[str, str, dict | None, Any, dict | None], tuple]

COMPANY_HEADER = "X-SlowBooks-Company"
SERVED_HEADER = "X-SlowBooks-Company-Served"
_COMPANY_LOWER = COMPANY_HEADER.lower()
_SERVED_LOWER = SERVED_HEADER.lower()
# What SlowBooks Pro answers before it reads a token (the OpenAPI document
# and the sign-in routes): those answers never say which company served them.
_UNSTAMPED_EXACT = {"/openapi.json"}
_UNSTAMPED_PREFIXES = ("/api/auth/",)


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(status, detail)
        self.status = status
        self.detail = detail
        # whether the answer said which company served it (a refusal made
        # before the token was read never does)
        self.stamped = False

    def __str__(self) -> str:
        return f"HTTP {self.status}: {self.detail}"


class Refused(ApiError):
    """A request this program does not send; the detail is the whole sentence."""


class Unconfirmed(Exception):
    """An answer that did not say it came from the expected company's books.

    It is deliberately not an ApiError, so no tool that reads past a refused
    request can go on to its write after it: the whole call stops."""

    def __init__(self, expected: str, served: str | None):
        super().__init__(expected, served)
        self.expected = expected
        self.served = served

    def __str__(self) -> str:
        return (
            f"SlowBooks Pro did not confirm it answered from {self.expected}, "
            "so this call stopped."
        )


class SpecHolder:
    """The OpenAPI document, read once and shared by every client of one
    connection."""

    def __init__(self):
        self.spec: dict | None = None
        self.lock = threading.Lock()


def reachable(path: str) -> bool:
    """Whether the program sends requests to this path at all: the API only."""
    if path == "/openapi.json":
        return True
    return path.startswith("/api/")


def unreachable_sentence(path: str) -> str:
    return (
        f"{path} is not part of the SlowBooks Pro API, so SlowBooks Switchboard "
        "does not reach it. API routes start with /api/."
    )


def refusal(method: str, path: str) -> str | None:
    """The sentence for a request this program keeps to SlowBooks Pro."""
    if not reachable(path):
        return unreachable_sentence(path)
    return None


def _check(method: str, path: str) -> None:
    sentence = refusal(method, path)
    if sentence:
        raise Refused(403, sentence)


def _parse(raw: bytes):
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the token would go along to the new address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def tls_context() -> ssl.SSLContext:
    """The platform's trusted certificates, hostname checked, TLS 1.2 at the
    least; there is deliberately no setting that loosens any of it."""
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _opener(base_url: str):
    handlers = [_NoRedirect()]
    if base_url.startswith("https://"):
        handlers.append(urllib.request.HTTPSHandler(context=tls_context()))
    return urllib.request.build_opener(*handlers)


def _reason(exc) -> str:
    reason = getattr(exc, "reason", None) or exc
    if isinstance(reason, ssl.SSLCertVerificationError) or (
        isinstance(reason, ssl.SSLError) and "CERTIFICATE" in str(reason).upper()
    ):
        return (
            "its certificate could not be verified "
            f"({getattr(reason, 'verify_message', None) or reason}). The server "
            "needs a certificate this computer trusts"
        )
    return str(reason)


def urllib_transport(config: Config) -> Transport:
    """The real transport: HTTP(S) with the token as a Bearer header."""
    opener = _opener(config.base_url)

    def send(method, path, params, body, headers):
        url = config.base_url + path
        if params:
            url += "?" + urlencode(params, doseq=True)
        hdrs = {
            "Authorization": f"Bearer {config.token}",
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        started = time.monotonic()
        try:
            with opener.open(req, timeout=config.timeout) as resp:
                status, payload = resp.status, _parse(resp.read())
                answered = _headers_of(resp.headers)
        except urllib.error.HTTPError as exc:
            status = exc.code
            answered = _headers_of(exc.headers)
            if 300 <= status < 400:
                where = exc.headers.get("Location") or "another address"
                payload = {
                    "detail": f"SlowBooks Pro at {config.base_url} sent the "
                    f"request on to {where}, which SlowBooks Switchboard does "
                    "not follow. Run setup again with that address"
                }
            else:
                payload = _parse(exc.read())
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            log.info("%s %s: no answer", method, path)
            raise ApiError(
                0,
                f"SlowBooks Pro did not answer at {config.base_url}: {_reason(exc)}",
            ) from None
        # The method, the path and the status only: never the token, the
        # query or a body.
        log.info(
            "%s %s -> %s in %.0f ms",
            method,
            path,
            status,
            (time.monotonic() - started) * 1000,
        )
        return status, payload, answered

    return send


def _headers_of(message) -> dict:
    """An answer's headers by lower-case name; a repeated header's values are
    joined, so a repeated company header never matches one company."""
    out: dict = {}
    if message is None:
        return out
    for name in set(message.keys()):
        values = message.get_all(name) or []
        out[name.lower()] = ", ".join(str(v) for v in values)
    return out


class ApiClient:
    """Requests to the app under one token.

    company: the company file to name in X-SlowBooks-Company on every
    request; none is sent when it is empty. expect_served: the company file
    every answer must say it came from (X-SlowBooks-Company-Served); a
    different one, or none on a success, raises Unconfirmed. spec_holder: the
    OpenAPI document shared with the connection's other clients."""

    def __init__(
        self,
        transport: Transport,
        *,
        company: str | None = None,
        expect_served: str | None = None,
        spec_holder: SpecHolder | None = None,
    ):
        self._transport = transport
        self.company = company or None
        self.expect_served = expect_served
        self.spec_holder = spec_holder or SpecHolder()

    @property
    def spec(self) -> dict | None:
        """openapi.json, cached by the tools (shared through spec_holder)."""
        return self.spec_holder.spec

    @spec.setter
    def spec(self, value: dict | None) -> None:
        self.spec_holder.spec = value

    def openapi(self) -> dict:
        """The OpenAPI document, fetched once for every client that shares
        the holder."""
        holder = self.spec_holder
        if holder.spec is None:
            with holder.lock:
                if holder.spec is None:
                    holder.spec = self.get("/openapi.json")
        return holder.spec

    def _headers(self, headers: dict | None) -> dict | None:
        # A caller's company header, in any spelling, never travels: only the
        # client's own does.
        out = {k: v for k, v in (headers or {}).items() if k.lower() != _COMPANY_LOWER}
        if self.company:
            out[COMPANY_HEADER] = self.company
        return out or None

    def _confirm(self, path: str, status: int, answered: dict | None) -> None:
        if self.expect_served is None:
            return
        if path in _UNSTAMPED_EXACT or path.startswith(_UNSTAMPED_PREFIXES):
            return
        served = (answered or {}).get(_SERVED_LOWER)
        if served is None:
            # The app's refusals before a token is read (401, 421, 503)
            # carry no served header; an answer with data always does.
            if 200 <= status < 300:
                raise Unconfirmed(self.expect_served, None)
            return
        if served != self.expect_served:
            raise Unconfirmed(self.expect_served, served)

    def _request(self, method, path, params=None, body=None, headers=None):
        _check(method, path)
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        result = self._transport(method, path, clean, body, self._headers(headers))
        status, payload = result[0], result[1]
        answered = None
        if len(result) > 2 and result[2] is not None:
            answered = {str(k).lower(): v for k, v in result[2].items()}
        self._confirm(path, status, answered)
        if 200 <= status < 300:
            return payload
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        if isinstance(detail, dict) and isinstance(detail.get("message"), str):
            detail = detail["message"]  # e.g. the possible-duplicate 409
        elif detail and not isinstance(detail, str):
            detail = json.dumps(detail)  # a validation error is a list
        exc = ApiError(status, detail or f"the request failed with status {status}")
        exc.stamped = answered is not None and _SERVED_LOWER in answered
        raise exc

    def request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        body: Any = None,
        headers: dict | None = None,
    ):
        """Any method: what the general tools send. refusal() applies."""
        return self._request(method.upper(), path, params, body, headers)

    def get(self, path: str, params: dict | None = None):
        return self._request("GET", path, params)

    def post(
        self,
        path: str,
        body: Any = None,
        headers: dict | None = None,
        params: dict | None = None,
    ):
        return self._request("POST", path, params, body, headers)
