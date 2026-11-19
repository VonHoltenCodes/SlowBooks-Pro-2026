# Vendored from SlowBooks Switchboard: sbplive/account.py
# (see app/switchboard/SOURCE.md). Do not edit here: change the source and run scripts/sync_switchboard.py.
# ============================================================================
# The link to the relay (docs/RELAY.md, "Accounts and keys"): `link` makes
# this computer a device of a relay account and keeps what the relay gave
# back; `unlink` forgets it. And the companion's two switches, which are
# this computer's own settings.
#
# The relay is account-less: an account is a random id and slug, its
# computers and its subscription; no email and no password. A person shows
# they hold a linked computer with a one-time code it asks for.
#
#   POST <relay>/v1/link    {"key": "<key>"} (a new account), or
#                           {"code": "<link code>"} (another computer of an
#                           account, the code from one already linked), or
#                           neither (a new free account, where the relay
#                           offers free sign-up); with "device_name" (the
#                           computer's name, optional) and "switchboard"
#     200 or 201            {"account": "<id>", "device": "<id>",
#                            "credential": "sbd_...", "slug": "<slug>",
#                            "terms": "https://...", "privacy": "https://..."
#                            (those two only when the relay has them)}
#     anything else         {"error": "<a sentence for the person>"}
#   POST <relay>/v1/code    the device's bearer, {"for": "sign-in" | "link"}
#     201                   {"code": "XXXX-XXXX-XXXX", "for": ..., "expires": ...}:
#                           a code for Claude's sign-in page, or for another
#                           computer's link; it works once, for ten minutes
#   POST <relay>/v1/key     the device's bearer, {"key": "<key>"}: a key added
#                           to this account (more time on the relay)
#     anything else         {"error": "<a sentence for the person>"}
#   POST <relay>/v1/unlink  Authorization: Bearer <device credential>, no body
#   POST <relay>/v1/access-phrase   the same bearer, {"phrase": "..."} to set
#                           the browser door's access phrase (8 to 256
#                           characters, kept there only as a hash), or
#                           {"phrase": null} to remove it and close the door
#     200                   {"remote_access": true | false}
#   GET  <relay>/v1/status  the same bearer; read here for "browser_url",
#                           the address people's browsers use
#
# Kept, for this user only (store.py's folder, mode 600; the paths are
# link_file(), credential_file() and switches_file() below):
#   link.json          the relay's address, the device and account ids, the
#                      account's slug, this computer's name
#   device-credential  the credential, one line; it is shown never
#   switches.json      {"mcp": false, "http": false}: both off until the
#                      person turns them on
#
# The relay is reached over https:// alone, its certificate verified; plain
# http:// only at 127.0.0.1, where the tests run their fake relay.
# ============================================================================

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.switchboard import __version__, store
from app.switchboard.config import ConfigError

# Where the relay is, once the owner has chosen its domain (docs/RELAY.md,
# "Open for the owner"). Until then `link` asks for it.
DEFAULT_RELAY = ""
LINE_PATH = "/v1/line"
LINK_PATH = "/v1/link"
UNLINK_PATH = "/v1/unlink"
PHRASE_PATH = "/v1/access-phrase"
CODE_PATH = "/v1/code"
KEY_PATH = "/v1/key"
CODE_PURPOSES = ("sign-in", "link")
_CODE = re.compile(r"[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}")
STATUS_PATH = "/v1/status"
PHRASE_MIN, PHRASE_MAX = 8, 256
NOT_LINKED = (
    "This computer is not linked to a relay account, so there is no access "
    "phrase to set. Link it first: sbplive link."
)
NOT_LINKED_CODE = (
    "This computer is not linked to a relay account, so it has no code to "
    "give. Link it first: sbplive link."
)
TIMEOUT = 30.0
# The relay's disclaimer (the owner, 2026-11-19), shown by `link` and the
# Link dialog; relay/src/common.py has the same words (a test keeps them so).
DISCLAIMER = (
    "The relay is free and comes with no guarantee: it may be slow, change, "
    "or stop, and it may not stay free without donations. Use it at your own risk."
)
_CREDENTIAL = re.compile(r"sbd_[A-Za-z0-9_\-]{32,200}")
_ID = re.compile(r"[A-Za-z0-9_.:\-]{1,128}")
_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?")
_INSECURE_HOSTS = ("127.0.0.1",)


def link_file():
    """The relay link: the relay, device, account, slug and name."""
    return store.config_dir() / "link.json"


def credential_file():
    """The device credential the relay gave at link: not the API token."""
    return store.config_dir() / "device-credential"


def switches_file():
    """The companion app's two switches and nothing else."""
    return store.config_dir() / "switches.json"


class LinkError(Exception):
    """Linking or unlinking failed; the message is the sentence to show."""


@dataclass(frozen=True)
class Link:
    relay: str
    device: str
    account: str
    slug: str
    name: str
    credential: str = ""
    # [(label, https URL)]: the relay's terms and privacy pages, from the
    # link's answer; not kept.
    links: tuple = ()

    def __repr__(self) -> str:  # keep the credential out of any log line
        return f"Link(relay={self.relay!r}, device={self.device!r}, slug={self.slug!r})"

    @property
    def line_url(self) -> str:
        parts = urlsplit(self.relay)
        scheme = "wss" if parts.scheme == "https" else "ws"
        return f"{scheme}://{parts.netloc}{LINE_PATH}"


def check_relay_url(raw: str) -> str:
    """The relay's address as https://host[:port], or ConfigError."""
    text = (raw or "").strip().rstrip("/")
    shape = (
        "The relay's address must look like https://relay.example.com: "
        "https, a host and maybe a port, with nothing after it."
    )
    try:
        parts = urlsplit(text)
        _ = parts.port
    except ValueError:
        raise ConfigError(shape) from None
    if (
        parts.scheme not in ("https", "http")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path
        or parts.query
        or parts.fragment
    ):
        raise ConfigError(shape)
    if parts.scheme == "http" and parts.hostname not in _INSECURE_HOSTS:
        raise ConfigError(
            f"{text} is not encrypted. The relay is reached only at an https:// "
            "address, and its certificate is always verified."
        )
    return f"{parts.scheme}://{parts.netloc}"


# -- the HTTP calls -------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # a redirect is answered as the error it is


def _opener(context=None):
    if context is None:
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    elif context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise ConfigError("The relay's certificate is always verified.")
    else:
        # A context handed in keeps its certificates; its floor is raised.
        context.minimum_version = max(context.minimum_version, ssl.TLSVersion.TLSv1_2)
    return urllib.request.build_opener(
        _NoRedirect(), urllib.request.HTTPSHandler(context=context)
    )


def _sentence(raw: bytes, status: int, relay: str) -> str:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        data = None
    if isinstance(data, dict):
        text = data.get("error") or data.get("message")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return f"The relay at {relay} answered {status}."


def _post(relay, path, body, headers, context=None):
    """(status, decoded JSON or None, raw body)."""
    return _call("POST", relay, path, body, headers, context)


def _call(method, relay, path, body, headers, context=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data is None and method == "POST":
        data = b""
    hdrs = {
        "Accept": "application/json",
        "User-Agent": f"sbplive/{__version__}",
    }
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers)
    req = urllib.request.Request(relay + path, data=data, headers=hdrs, method=method)
    try:
        with _opener(context).open(req, timeout=TIMEOUT) as resp:
            status, raw = resp.status, resp.read(1 << 20)
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read(1 << 20)
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise LinkError(f"The relay at {relay} did not answer: {reason}.") from None
    try:
        decoded = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, ValueError):
        decoded = None
    return status, decoded, raw


def link(
    relay: str,
    *,
    device_name: str = "",
    key: str | None = None,
    code: str | None = None,
    context=None,
) -> Link:
    """Link this computer to the relay, and keep the link: with a key (a new
    account), a link code from a computer already linked (that account), or
    neither (a new free account, where the relay offers free sign-up)."""
    relay = check_relay_url(relay)
    name = (device_name or "").strip()[:100]
    key, code = (key or "").strip(), (code or "").strip()
    if key and code:
        raise ConfigError("Give a key or a link code, not both.")
    body = {"device_name": name, "switchboard": __version__}
    if key:
        body["key"] = key
    if code:
        body["code"] = code
    status, data, raw = _post(relay, LINK_PATH, body, {}, context)
    if status not in (200, 201):
        raise LinkError(_sentence(raw, status, relay))
    if not isinstance(data, dict):
        raise LinkError(f"The relay at {relay} answered something that is not a link.")
    credential, device = data.get("credential"), data.get("device")
    account, slug = data.get("account"), data.get("slug")
    if not (
        isinstance(credential, str)
        and _CREDENTIAL.fullmatch(credential)
        and isinstance(device, str)
        and _ID.fullmatch(device)
        and isinstance(account, str)
        and _ID.fullmatch(account)
        and isinstance(slug, str)
        and _SLUG.fullmatch(slug)
    ):
        raise LinkError(f"The relay at {relay} answered something that is not a link.")
    links = tuple(
        (label, data[label.lower()])
        for label in ("Terms", "Privacy")
        if isinstance(data.get(label.lower()), str)
        and data[label.lower()].startswith("https://")
        and len(data[label.lower()]) <= 2000
        and not any(c.isspace() for c in data[label.lower()])
    )
    made = Link(relay, device, account, slug, name, credential, links)
    save_link(made)
    return made


def unlink(context=None) -> tuple:
    """Forget the link. (whether a link was kept, whether the relay heard
    it); the link is forgotten here even when the relay cannot be told."""
    kept = load_link()
    if kept is None:
        forget_link()
        return False, False
    told = False
    try:
        status, _, _ = _post(
            kept.relay,
            UNLINK_PATH,
            None,
            {"Authorization": f"Bearer {kept.credential}"},
            context,
        )
        told = 200 <= status < 300 or status in (401, 404)
    except (LinkError, ConfigError):
        told = False
    forget_link()
    return True, told


# -- one-time codes and keys --------------------------------------------------------


def request_code(purpose: str = "sign-in", context=None) -> tuple:
    """A fresh one-time code from the relay: (the code as shown, when it
    expires). "sign-in" is for Claude's sign-in page; "link" for another
    computer's `sbplive link --code`."""
    if purpose not in CODE_PURPOSES:
        raise ConfigError('A code is "sign-in" or "link".')
    kept = _kept_link(NOT_LINKED_CODE)
    status, data, raw = _post(
        kept.relay,
        CODE_PATH,
        {"for": purpose},
        {"Authorization": f"Bearer {kept.credential}"},
        context,
    )
    if status != 201:
        raise LinkError(_sentence(raw, status, kept.relay))
    code = data.get("code") if isinstance(data, dict) else None
    expires = data.get("expires") if isinstance(data, dict) else None
    if not (isinstance(code, str) and _CODE.fullmatch(code)) or not isinstance(
        expires, str
    ):
        raise LinkError(
            f"The relay at {kept.relay} answered something that is not a code."
        )
    return code, expires


def apply_key(key: str, context=None) -> dict:
    """Add a key to this computer's account; the relay's word on the
    subscription after it ({"status", "until", "open"})."""
    key = (key or "").strip()
    if not key:
        raise ConfigError("A key is needed.")
    kept = _kept_link(
        NOT_LINKED_CODE.replace(
            "it has no code to give", "a key has no account to go to"
        )
    )
    status, data, raw = _post(
        kept.relay,
        KEY_PATH,
        {"key": key},
        {"Authorization": f"Bearer {kept.credential}"},
        context,
    )
    if status != 200 or not isinstance(data, dict):
        raise LinkError(_sentence(raw, status, kept.relay))
    sub = data.get("subscription")
    return sub if isinstance(sub, dict) else {}


# -- the browser door's access phrase ---------------------------------------------


def _kept_link(sentence: str = NOT_LINKED) -> Link:
    kept = load_link()
    if kept is None:
        raise ConfigError(sentence)
    return kept


def check_phrase(phrase) -> str:
    """The phrase as typed (nothing trimmed: the browser sends it as typed),
    or ConfigError."""
    if not isinstance(phrase, str) or not phrase.strip():
        raise ConfigError("An access phrase is needed.")
    if not PHRASE_MIN <= len(phrase) <= PHRASE_MAX:
        raise ConfigError(
            f"The access phrase must be {PHRASE_MIN} to {PHRASE_MAX} characters."
        )
    if any(c in phrase for c in "\r\n\0"):
        raise ConfigError("The access phrase must be one line.")
    return phrase


def _send_phrase(phrase, context) -> bool:
    kept = _kept_link()
    status, data, raw = _post(
        kept.relay,
        PHRASE_PATH,
        {"phrase": phrase},
        {"Authorization": f"Bearer {kept.credential}"},
        context,
    )
    if status != 200:
        raise LinkError(_sentence(raw, status, kept.relay))
    if not isinstance(data, dict) or not isinstance(data.get("remote_access"), bool):
        raise LinkError(
            f"The relay at {kept.relay} answered something that is not about "
            "the access phrase."
        )
    return data["remote_access"]


def set_access_phrase(phrase, context=None) -> bool:
    """Set the browser door's access phrase; the relay keeps only its hash,
    and browsers that passed the old one must pass the new one. True when
    the relay says the door is set up."""
    return _send_phrase(check_phrase(phrase), context)


def clear_access_phrase(context=None) -> bool:
    """Remove the access phrase: the browser door closes to everyone, and
    browsers that passed it are forgotten. False when the relay agrees."""
    return _send_phrase(None, context)


def guessed_browser_address(kept: Link) -> str:
    """The browser door's address worked out from the relay's own
    (relay.<domain> -> <slug>.<domain>, docs/RELAY.md), or ""."""
    host = urlsplit(kept.relay).hostname or ""
    if host.startswith("relay.") and _SLUG.fullmatch(kept.slug or ""):
        return f"https://{kept.slug}.{host[len('relay.'):]}/"
    return ""


def browser_address(context=None) -> str:
    """The address people's browsers use: the relay's own word for it
    (/v1/status), else worked out from the relay's address and the slug,
    else ""."""
    kept = _kept_link()
    try:
        status, data, _ = _call(
            "GET",
            kept.relay,
            STATUS_PATH,
            None,
            {"Authorization": f"Bearer {kept.credential}"},
            context,
        )
    except (LinkError, ConfigError):
        status, data = 0, None
    url = data.get("browser_url") if isinstance(data, dict) else None
    if status == 200 and isinstance(url, str) and url.startswith("https://"):
        parts = urlsplit(url)
        if parts.hostname and not (parts.query or parts.fragment or parts.username):
            return url
    return guessed_browser_address(kept)


def phrase_sentence(is_set: bool, address: str, slug: str) -> str:
    """What to tell the person after setting or removing the phrase."""
    where = address or f"the books' address (its name is {slug})"
    if is_set:
        return (
            f"The access phrase is set. Your team opens {where} in a browser, "
            "enters the phrase once, and then signs in to SlowBooks Pro as "
            "usual. The relay keeps the phrase only as a hash and cannot show "
            "it to anyone, so give it to your people yourself; browsers that "
            "used an earlier phrase must enter this one."
        )
    return (
        f"The access phrase is removed, so {where} is closed to every browser "
        "until a phrase is set again; browsers that had entered it are signed out."
    )


# -- what is kept ---------------------------------------------------------------


def save_link(made: Link) -> None:
    store.write_private(credential_file(), made.credential + "\n")
    record = {
        "relay": made.relay,
        "device": made.device,
        "account": made.account,
        "slug": made.slug,
        "name": made.name,
    }
    store.write_private(link_file(), json.dumps(record, indent=2) + "\n")


def load_link() -> Link | None:
    """The kept link, or None when this computer is not linked (or what is
    kept cannot be used: then ConfigError says why)."""
    path, cred = link_file(), credential_file()
    if not path.is_file() or not cred.is_file():
        return None
    if os.name != "nt" and os.stat(cred).st_mode & 0o077:
        raise ConfigError(
            f"The device credential {cred} can be read by other users. "
            f"Run: chmod 600 {cred}, or unlink and link again."
        )
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        credential = cred.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError, ValueError):
        raise ConfigError(
            f"The relay link in {path} could not be read. Unlink and link again."
        ) from None
    # A link kept by an older companion also has the account's email: it is
    # not read, and goes when the link is next saved.
    fields = ("relay", "device", "account", "slug", "name")
    if not isinstance(record, dict) or not all(
        isinstance(record.get(f), str) for f in fields
    ):
        raise ConfigError(
            f"The relay link in {path} is damaged. Unlink and link again."
        )
    if not _CREDENTIAL.fullmatch(credential):
        raise ConfigError(
            f"The device credential {cred} is damaged. Unlink and link again."
        )
    return Link(*(record[f] for f in fields), credential=credential)


def forget_link() -> list:
    removed = []
    for path in (credential_file(), link_file()):
        try:
            path.unlink()
            removed.append(path)
        except FileNotFoundError:
            pass
    return removed


def load_switches() -> dict:
    """{"mcp": bool, "http": bool}; both off unless the person turned them on."""
    try:
        data = json.loads(switches_file().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"mcp": data.get("mcp") is True, "http": data.get("http") is True}


def save_switches(mcp: bool, http: bool) -> dict:
    switches = {"mcp": bool(mcp), "http": bool(http)}
    store.write_private(switches_file(), json.dumps(switches) + "\n")
    return switches
