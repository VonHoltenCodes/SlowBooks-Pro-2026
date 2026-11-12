# ============================================================================
# Print pages at a real address (#823)
#
# The desktop app opened every Print in a pywebview window handed the sheet's
# HTML (`html=`), a window with no address of its own: the print dialog's
# footer printed a long base64 data: string along the bottom of every page.
# A fresh window given the sheet's own URL loads without the main window's
# session cookie ("Not authenticated": desktop_shim.js tells that history),
# so the address it opens carries its own permission instead:
#
#   POST /api/print-tokens {path, query}
#       Made under the caller's sign-in or API token: the sheet is drawn
#       right then, through the whole app (the same gate, role checks and
#       404s as opening the print route directly), and kept for a minute.
#       Only a print or print-preview page of this app; anything else is
#       refused in a sentence.
#   GET /print/<token>
#       That sheet, once, with no cookie needed. A second open, or one after
#       the minute, is a 404 with a sentence.
#
# The window then shows http://127.0.0.1:<port>/print/<token>, and that short
# address and the sheet's title are what the footer and header print.
# ============================================================================

import re
import secrets
import threading
import time
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/print-tokens", tags=["print"])
page_router = APIRouter(tags=["print"])

PATH = "/api/print-tokens"
LIFETIME_SECONDS = 60
# A used or expired token is remembered this long, so a second open says
# which it was rather than that there never was one.
_REMEMBER_SECONDS = 15 * 60
_MAX_KEPT = 500

# A print sheet of this app: a GET under /api/ ending in /print (a report's,
# the Budgets sheets, a reconciliation) or /print-preview (an invoice, an
# estimate, a bill...). Plain path segments only: no dots, no query.
_SHEET_PATH_RE = re.compile(r"^/api(/[A-Za-z0-9_-]+)+/(print|print-preview)$")

NOT_A_SHEET = (
    "Only a print or print-preview page of SlowBooks Pro can be opened for "
    "printing this way."
)
ALREADY_OPENED = (
    "This print page was already opened once. Use Print in SlowBooks Pro again "
    "to open it afresh."
)
EXPIRED = (
    "This print page has expired: it opens within a minute of Print. Use Print "
    "in SlowBooks Pro again."
)
UNKNOWN = "There is no such print page. Use Print in SlowBooks Pro to open one."

# token -> {"html": str | None, "expires": float, "state": "ready"|"used"}
_tokens: dict = {}
_lock = threading.Lock()

# The caller's headers the sheet is drawn with: who it is (the session
# cookie, an API token, the company a token names) and where it came from.
# Never Accept-Encoding: the sheet is kept as text, not gzip.
_FORWARDED_HEADERS = {
    b"cookie",
    b"authorization",
    b"x-slowbooks-company",
    b"host",
    b"user-agent",
    b"accept-language",
}


class PrintTokenRequest(BaseModel):
    path: str
    query: str = ""


def _now() -> float:
    return time.monotonic()


def _purge(now: float) -> None:
    for key, entry in list(_tokens.items()):
        if now - entry["expires"] > _REMEMBER_SECONDS:
            del _tokens[key]
    if len(_tokens) > _MAX_KEPT:
        for key in sorted(_tokens, key=lambda k: _tokens[k]["expires"])[
            : len(_tokens) - _MAX_KEPT
        ]:
            del _tokens[key]


def _keep(html: str) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    with _lock:
        _purge(now)
        _tokens[token] = {
            "html": html,
            "expires": now + LIFETIME_SECONDS,
            "state": "ready",
        }
    return token


def _take(token: str) -> str:
    """The sheet once; a sentence (as a 404) every other time."""
    now = _now()
    with _lock:
        entry = _tokens.get(token)
        if entry is None:
            raise HTTPException(status_code=404, detail=UNKNOWN)
        if entry["state"] == "used":
            raise HTTPException(status_code=404, detail=ALREADY_OPENED)
        if now >= entry["expires"]:
            entry["html"] = None
            entry["state"] = "expired"
            raise HTTPException(status_code=404, detail=EXPIRED)
        html = entry["html"]
        entry["html"] = None
        entry["state"] = "used"
        return html


def _query_bytes(query: str) -> bytes:
    query = (query or "").lstrip("?")
    if "#" in query:
        query = query.split("#", 1)[0]
    # already percent-encoded from a URL: kept as it is; anything else typed
    # in is encoded, never refused
    return quote(query, safe="=&%+;,/:@!$'()*-._~").encode("ascii")


async def _draw(request: Request, path: str, query: bytes):
    """GET the sheet through the whole app, as the caller: (status,
    content type, body)."""
    scope = request.scope
    headers = [
        (name, value)
        for name, value in scope.get("headers") or []
        if name.lower() in _FORWARDED_HEADERS
    ]
    sub = {
        "type": "http",
        "asgi": scope.get("asgi", {"version": "3.0"}),
        "http_version": scope.get("http_version", "1.1"),
        "method": "GET",
        "scheme": scope.get("scheme", "http"),
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query,
        "root_path": scope.get("root_path", ""),
        "headers": headers,
        "client": scope.get("client"),
        "server": scope.get("server"),
    }
    if "state" in scope:
        sub["state"] = dict(scope["state"])
    sent = {"status": 500, "headers": [], "body": []}
    asked = False

    async def receive():
        nonlocal asked
        if not asked:
            asked = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
            sent["headers"] = message.get("headers") or []
        elif message["type"] == "http.response.body":
            sent["body"].append(message.get("body", b""))

    await request.app(sub, receive, send)
    content_type = ""
    for name, value in sent["headers"]:
        if name.lower() == b"content-type":
            content_type = value.decode("latin-1").lower()
    return sent["status"], content_type, b"".join(sent["body"])


def _detail_of(body: bytes, status: int) -> str:
    import json

    try:
        detail = json.loads(body.decode("utf-8")).get("detail")
    except Exception:
        detail = None
    if isinstance(detail, str) and detail:
        return detail
    return f"The print page could not be made (HTTP {status})."


@router.post("", status_code=201)
async def make_print_token(body: PrintTokenRequest, request: Request):
    """A one-time address for a print sheet (#823): the sheet at `path` with
    `query`, drawn now under the caller's sign-in or token, served once at
    the answered `url` within a minute, with no cookie needed. Any role that
    can read the sheet can make one; a refusal of the sheet is answered as
    the sheet's own route answers it."""
    path = body.path or ""
    if not _SHEET_PATH_RE.match(path):
        raise HTTPException(status_code=400, detail=NOT_A_SHEET)
    status, content_type, content = await _draw(request, path, _query_bytes(body.query))
    if status >= 400:
        raise HTTPException(status_code=status, detail=_detail_of(content, status))
    if status != 200 or not content_type.startswith("text/html"):
        raise HTTPException(status_code=400, detail=NOT_A_SHEET)
    token = _keep(content.decode("utf-8", errors="replace"))
    return {
        "token": token,
        "url": str(request.url_for("print_page", token=token)),
        "expires_in": LIFETIME_SECONDS,
    }


@page_router.get("/print/{token}", response_class=HTMLResponse, name="print_page")
def print_page(token: str):
    """The sheet a print token was made for, once (#823). No sign-in: the
    token is the permission, for a minute and one open."""
    html = _take(token)
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
