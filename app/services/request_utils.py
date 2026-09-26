# ============================================================================
# Shared request helpers used across route modules.
# ============================================================================

import unicodedata
from urllib.parse import quote

from fastapi import Request


def client_ip(request: Request) -> str:
    """Best-effort client IP. Honors X-Forwarded-For ONLY when the deployment
    declares it runs behind a trusted proxy (TRUST_PROXY_HEADERS) — otherwise
    XFF is client-spoofable and would let an attacker forge the audited IP.
    Direct deploys fall back to the socket peer."""
    from app.config import TRUST_PROXY_HEADERS

    fwd = request.headers.get("x-forwarded-for", "") if TRUST_PROXY_HEADERS else ""
    if fwd:
        # Take the first hop — that's the client (proxies append to the right).
        return fwd.split(",")[0].strip()[:45]
    client = request.client
    return (client.host if client else "")[:45]


# Letters NFKD leaves whole (no accent to take off), spelled the way a person
# types them on a plain keyboard.
_ASCII_PLAIN = {
    "Ł": "L",
    "ł": "l",
    "Đ": "D",
    "đ": "d",
    "Ð": "D",
    "ð": "d",
    "Ø": "O",
    "ø": "o",
    "Æ": "AE",
    "æ": "ae",
    "Œ": "OE",
    "œ": "oe",
    "ß": "ss",
    "Þ": "Th",
    "þ": "th",
    "ı": "i",
    "Ħ": "H",
    "ħ": "h",
}
# Characters the quoted ASCII name can't carry: a quote or backslash ends or
# escapes it, ";" starts the next parameter, and "%" makes a reader that
# percent-decodes the name (the desktop shell does) fail.
_FALLBACK_UNSAFE = frozenset('"\\;%')


def _file_name_text(name: str) -> str:
    """The name without what no file name can hold: a path separator
    becomes "-", a control character goes."""
    name = (name or "").replace("/", "-").replace("\\", "-")
    return "".join(ch for ch in name if unicodedata.category(ch) != "Cc")


def _ascii_file_name(name: str) -> str:
    """Plain ASCII for a reader without RFC 6266: accents taken off
    ("Café" -> "Cafe", "Łódź" -> "Lodz"), anything else "_"."""
    out = []
    for ch in name:
        for part in unicodedata.normalize("NFKD", _ASCII_PLAIN.get(ch, ch)):
            if unicodedata.combining(part):
                continue
            ok = " " <= part <= "~" and part not in _FALLBACK_UNSAFE
            out.append(part if ok else "_")
    return "".join(out)


def content_disposition(filename: str, disposition: str = "inline") -> str:
    """A Content-Disposition header value for a file named after something a
    person typed — a customer's name, an invoice or bill number.

    The raw name used to go into the header as it was. A header is sent as
    Latin-1, so an accented name reached the browser as bytes it read the
    wrong way, and a name with a letter Latin-1 lacks ("Łódź Signs") made
    the whole request fail with a 500 (2.17.3 exploratory). RFC 6266 /
    RFC 5987: ``filename*=UTF-8''…`` carries the exact name, percent-
    encoded, and every current browser uses it; ``filename="…"`` is a
    plain-ASCII stand-in for any reader that doesn't (and it is what the
    desktop shell reads)."""
    name = _file_name_text(filename)
    return (
        f'{disposition}; filename="{_ascii_file_name(name)}"; '
        f"filename*=UTF-8''{quote(name, safe='')}"
    )
