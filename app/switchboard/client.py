# Vendored from SlowBooks Switchboard: sbplive/client.py
# (see app/switchboard/SOURCE.md). Do not edit here: change the source and run scripts/sync_switchboard.py.
# ============================================================================
# The HTTP client: every tool call is one request to the running app under
# the token. Standard library only, so this module needs no SDK. The
# transport is a plain callable, which is how the tests run the tools
# against the app in-process.
#
# What the program refuses on its own ("match the software", the owner's
# answer on admin operations): nothing the API decides for the token's role.
# Tokens, users, backups, the company file and system go to the API like
# everything else, and its answer is what the person hears. One thing stays
# refused here: paths outside /api/ (and /openapi.json). The pages, /health
# and the payment and portal pages are not the API.
#
# Making a backup (POST /api/backups) is the API's to decide like the rest
# (the owner, 2026-11-06): the app lets a token an administrator ticked
# "Can make backups" make one whatever its role, and this program does not
# second-guess that.
#
# The files the API returns (PDFs, CSV and IIF downloads, print pages) and
# uploads are refused in operations.py, since they cannot be carried here.
#
# HTTPS certificates are always verified, with no switch to turn that off,
# and redirects are never followed: the token is sent only to the address
# the person gave setup.
# ============================================================================

import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from app.switchboard.config import Config

log = logging.getLogger("sbplive")

# (method, path, params, body, headers) -> (status, parsed JSON or text)
Transport = Callable[[str, str, dict | None, Any, dict | None], tuple]


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(status, detail)
        self.status = status
        self.detail = detail

    def __str__(self) -> str:
        return f"HTTP {self.status}: {self.detail}"


class Refused(ApiError):
    """A request this program does not send; the detail is the whole sentence."""


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


def _opener(base_url: str):
    handlers = [_NoRedirect()]
    if base_url.startswith("https://"):
        # The platform's trusted certificates, hostname checked; there is
        # deliberately no setting that turns verification off.
        handlers.append(
            urllib.request.HTTPSHandler(context=ssl.create_default_context())
        )
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
        except urllib.error.HTTPError as exc:
            status = exc.code
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
        return status, payload

    return send


class ApiClient:
    def __init__(self, transport: Transport):
        self._transport = transport
        self.spec: dict | None = None  # openapi.json, cached by the tools

    def _request(self, method, path, params=None, body=None, headers=None):
        _check(method, path)
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        status, payload = self._transport(method, path, clean, body, headers)
        if 200 <= status < 300:
            return payload
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        if isinstance(detail, dict) and isinstance(detail.get("message"), str):
            detail = detail["message"]  # e.g. the possible-duplicate 409
        elif detail and not isinstance(detail, str):
            detail = json.dumps(detail)  # a validation error is a list
        raise ApiError(status, detail or f"the request failed with status {status}")

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
