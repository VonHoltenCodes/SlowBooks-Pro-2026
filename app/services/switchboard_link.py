# ============================================================================
# Connect Claude from Settings (#1087): SlowBooks Pro holds the line to the
# SlowBooks relay itself, with SlowBooks Switchboard's own line client
# (app/switchboard, vendored), so Claude on the web or a phone, and Grok,
# can work the books with no second program and no tunnel.
#
#   connect()     links this install to the relay (no account: no email, no
#                 password; the relay answers a random device credential),
#                 makes a bookkeeper API token "Claude (relay)" the way
#                 Settings, API Tokens makes one, keeps both in one file in
#                 the data folder readable by this user only, and opens the
#                 line in a background thread
#   new_code()    a one-time sign-in code from the relay (XXXX-XXXX-XXXX,
#                 ten minutes, once), typed on the relay's sign-in page when
#                 the assistant asks
#   status()      what the panel says
#   disconnect()  closes the line, unlinks at the relay, revokes the token
#                 and deletes the file
#
# Off the books' critical path: the line is a daemon thread that dials out
# (nothing listens), started after the app has started; it reaches the
# books only through the app's own API with the token, like any agent. A
# relay that is down, slow or gone is caught, logged without secrets, and
# retried by the line client's own backoff (1 s doubling to 60 s); no
# request to the books waits on it.
#
# One link per install, tied to the company it was made in. The line is up
# only while that company is open here: on the desktop, the company the app
# was started for; on a Server Edition host serving several companies, any
# company it serves (the token's requests name the company). A sign-in to
# another company sees "not connected" and the company it belongs to, and
# cannot use or end it from there.
# ============================================================================

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.switchboard import __version__ as SWITCHBOARD_VERSION
from app.switchboard import account, line as line_client, logs as sb_logs
from app.switchboard.client import ApiClient, urllib_transport
from app.switchboard.config import Config, ConfigError
from app.switchboard.server import INSTRUCTIONS, Mcp, build_registry

logger = logging.getLogger(__name__)

# The relay, one constant; SLOWBOOKS_RELAY_URL points the tests at a fake.
RELAY_URL = "https://relay.sbplive.com"
RELAY_ENV = "SLOWBOOKS_RELAY_URL"
MCP_PATH = "/mcp"
TERMS_URL = "https://www.slowbookspro.com/terms/"
PRIVACY_URL = "https://www.slowbookspro.com/privacy/"
LINK_FILE = "claude-relay.json"
TOKEN_LABEL = "Claude (relay)"
DEVICE_NAME = "SlowBooks Pro"
# Under the relay's 110 seconds for a tool call, so the answer is the app's.
API_TIMEOUT = 100.0
# The line client's timings; the tests shorten them.
TIMINGS = line_client.DEFAULT_TIMINGS

NOT_LINKED = "Claude is not connected. An administrator can press Connect Claude."
OTHER_COMPANY = (
    "Claude is connected to {name}, not to this company. Open {name} to "
    "show a sign-in code or to disconnect it."
)
ALREADY = "Claude is already connected to this company."
UNREACHABLE = "SlowBooks Pro keeps trying on its own; the books work as before."
DAMAGED = (
    "The Claude connection kept in the data folder cannot be read. "
    "Disconnect, then connect again."
)

# The vendored client logs to "sbplive"; its own filter takes credentials,
# tokens and bearer values out of every line it logs.
logging.getLogger(sb_logs.LOGGER).addFilter(sb_logs._MaskSecrets())


class SwitchboardError(Exception):
    """A refusal or failure; .sentence is for the person, .status for HTTP."""

    def __init__(self, status: int, sentence: str, *, unreadable: bool = False):
        super().__init__(sentence)
        self.status = status
        self.sentence = sentence
        # the kept file itself cannot be used (Disconnect forgets it)
        self.unreadable = unreadable


# -- where things are -------------------------------------------------------------


def relay_url() -> str:
    return account.check_relay_url(os.environ.get(RELAY_ENV) or RELAY_URL)


def mcp_address(relay: str | None = None) -> str:
    return (relay or relay_url()) + MCP_PATH


def link_path() -> Path:
    from app.services.company_service import data_dir

    return data_dir() / LINK_FILE


def _app_base_url() -> str:
    """This app's own address, for the token's requests: loopback unless it
    listens on one address only."""
    host = (os.environ.get("APP_HOST") or "127.0.0.1").strip()
    port = (os.environ.get("APP_PORT") or "3001").strip()
    if host in ("", "0.0.0.0"):
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}"


def _company_here() -> str:
    """The company file this request works in ("" for a database that is
    not a file, as on PostgreSQL)."""
    from app.services import served_companies

    return served_companies.current_file() or ""


def _company_open(company_file: str) -> bool:
    """Whether the company a link was made in is open in this app now."""
    from app.services import served_companies

    if company_file == (served_companies.started_file() or ""):
        return True
    return served_companies.enabled() and served_companies.is_served(company_file)


# -- the file ---------------------------------------------------------------------

_FIELDS = (
    "relay",
    "device",
    "account",
    "slug",
    "credential",
    "token",
    "token_id",
    "token_label",
    "company_file",
    "company_name",
    "linked_at",
)


def _write_private(path: Path, text: str) -> None:
    """The file made readable and writable by this user only from the first
    byte (0600 on macOS and Linux; on Windows the data folder sits in the
    user's own profile, whose access list is the protection), then moved
    over any earlier one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    if os.name != "nt":
        os.chmod(path, 0o600)


def _save(record: dict) -> None:
    _write_private(link_path(), json.dumps(record, indent=2) + "\n")


def _load() -> dict | None:
    """The kept link, None when there is none; SwitchboardError when it
    cannot be used (another user can read it, or it is damaged)."""
    path = link_path()
    if not path.is_file():
        return None
    if os.name != "nt" and os.stat(path).st_mode & 0o077:
        raise SwitchboardError(
            409,
            "The Claude connection kept in the data folder can be read by other "
            "users of this computer. Disconnect, then connect again.",
            unreadable=True,
        )
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise SwitchboardError(409, DAMAGED, unreadable=True) from None
    if not isinstance(record, dict) or not all(
        isinstance(record.get(f), str) for f in _FIELDS if f != "token_id"
    ):
        raise SwitchboardError(409, DAMAGED, unreadable=True)
    return record


def _forget() -> None:
    try:
        link_path().unlink()
    except FileNotFoundError:
        pass


# -- the line -----------------------------------------------------------------------

_lock = threading.Lock()
_line = None  # the Line while one runs
_line_device = ""  # whose line it is


def _transport(record: dict):
    """The token's requests to this app, naming the company it was made in."""
    config = Config(_app_base_url(), record["token"], API_TIMEOUT)
    send = urllib_transport(config)
    company = record.get("company_file") or ""

    def with_company(method, path, params, body, headers):
        extra = dict(headers or {})
        if company:
            extra["X-SlowBooks-Company"] = company
        return send(method, path, params, body, extra)

    return with_company


def _make_line(record: dict):
    from app import __version__ as app_version

    made = account.Link(
        record["relay"], record["device"], record["account"], record["slug"], ""
    )
    mcp = Mcp(build_registry(ApiClient(_transport(record))), INSTRUCTIONS)
    return line_client.Line(
        made.line_url,
        record["device"],
        record["credential"],
        app_url=_app_base_url(),
        mcp=mcp,
        app_version=app_version,
        # Claude's door on; the browser door (people's browsers through
        # the relay) stays off: Connect Claude is for the assistant.
        switches={"mcp": True, "http": False},
        timings=TIMINGS,
    )


def _run(made) -> None:
    try:
        end = made.run()
        logger.info("Claude relay line ended: %s", end)
    except Exception:  # never let the line take anything else down
        logger.exception("Claude relay line failed")


def _start(record: dict) -> None:
    global _line, _line_device
    with _lock:
        if _line is not None and _line_device == record["device"]:
            return
        old = _line
        _line = _make_line(record)
        _line_device = record["device"]
        made = _line
    if old is not None:
        old.stop()
    threading.Thread(
        target=_run, args=(made,), name="claude-relay", daemon=True
    ).start()


def stop() -> None:
    """Close the line, if one is open (the app's shutdown, Disconnect)."""
    global _line, _line_device
    with _lock:
        old, _line, _line_device = _line, None, ""
    if old is not None:
        try:
            old.stop()
        except Exception:
            logger.exception("closing the Claude relay line failed")


def start_in_background() -> None:
    """Called once the app has started: when a link is kept and its company
    is open here, open the line. Returns at once; nothing here can fail the
    start."""

    def begin():
        try:
            record = _load()
            if record is None or record.get("on") is False:
                return
            if not _company_open(record["company_file"]):
                logger.info(
                    "Claude relay line not opened: its company is not open here"
                )
                return
            _start(record)
        except SwitchboardError as exc:
            logger.warning("Claude relay line not opened: %s", exc.sentence)
        except Exception:
            logger.exception("Claude relay line not opened")

    threading.Thread(target=begin, name="claude-relay-start", daemon=True).start()


# -- what the panel shows -----------------------------------------------------------


def _line_state(device: str) -> tuple:
    """(state, sentence) of the line for this device."""
    with _lock:
        made = _line if _line_device == device else None
    if made is None:
        return "not_connected", ""
    st = made.status
    if st.state == "connected":
        return "connected", ""
    if st.state == "waiting" or (st.state == "connecting" and made.waits):
        return "unreachable", UNREACHABLE
    if st.state == "connecting":
        return "connecting", ""
    if st.state == "refused":
        return (
            "refused",
            "The relay no longer accepts this link. Disconnect, then connect again.",
        )
    if st.state == "replaced":
        return (
            "refused",
            "Another computer took this link's line. Disconnect, then connect again.",
        )
    return "not_connected", ""


def status() -> dict:
    out = {
        "state": "not_connected",
        "sentence": "",
        "linked": False,
        "this_company": False,
        "company": "",
        "address": "",
        "token_label": "",
        "terms": TERMS_URL,
        "privacy": PRIVACY_URL,
    }
    try:
        record = _load()
    except SwitchboardError as exc:
        out.update(state="damaged", sentence=exc.sentence, linked=True)
        return out
    if record is None:
        return out
    here = record["company_file"] == _company_here()
    out.update(
        linked=True,
        this_company=here,
        company=record["company_name"],
        token_label=record["token_label"],
    )
    if not here:
        out["sentence"] = OTHER_COMPANY.format(name=record["company_name"])
        return out
    state, sentence = _line_state(record["device"])
    out.update(state=state, sentence=sentence, address=mcp_address(record["relay"]))
    return out


# -- the administrator's three buttons ----------------------------------------------


def _relay_post(record_or_relay, path: str, body, *, bearer: str | None = None):
    relay = record_or_relay
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    try:
        return account._post(relay, path, body, headers)
    except account.LinkError as exc:
        raise SwitchboardError(502, str(exc)) from None
    except ConfigError as exc:
        raise SwitchboardError(502, str(exc)) from None


def _free_label(db) -> str:
    from app.models.api_tokens import ApiToken

    taken = {
        label
        for (label,) in db.query(ApiToken.label)
        .filter(ApiToken.label.like(TOKEN_LABEL + "%"))
        .all()
    }
    if TOKEN_LABEL not in taken:
        return TOKEN_LABEL
    n = 2
    while f"{TOKEN_LABEL} {n}" in taken:
        n += 1
    return f"{TOKEN_LABEL} {n}"


def connect(db, username: str) -> dict:
    """Link, make the token, keep both, open the line. Linking comes first,
    so a relay that cannot be reached leaves nothing behind in the books."""
    from app.models.users import ROLE_BOOKKEEPER
    from app.routes.api_tokens import issue_token
    from app.services.backup_service import current_company_name

    record = _load()
    if record is not None:
        if record["company_file"] != _company_here():
            raise SwitchboardError(
                409, OTHER_COMPANY.format(name=record["company_name"])
            )
        raise SwitchboardError(409, ALREADY)
    relay = relay_url()
    status_code, data, raw = _relay_post(
        relay,
        account.LINK_PATH,
        {"device_name": DEVICE_NAME, "switchboard": SWITCHBOARD_VERSION},
    )
    if status_code not in (200, 201):
        raise SwitchboardError(502, account._sentence(raw, status_code, relay))
    if not (
        isinstance(data, dict)
        and isinstance(data.get("credential"), str)
        and account._CREDENTIAL.fullmatch(data["credential"])
        and isinstance(data.get("device"), str)
        and account._ID.fullmatch(data["device"])
        and isinstance(data.get("account"), str)
        and account._ID.fullmatch(data["account"])
        and isinstance(data.get("slug"), str)
        and account._SLUG.fullmatch(data["slug"])
    ):
        raise SwitchboardError(
            502, f"The relay at {relay} answered something that is not a link."
        )
    try:
        label = _free_label(db)
        row, secret = issue_token(
            db, label=label, role=ROLE_BOOKKEEPER, created_by=username or "operator"
        )
        record = {
            "v": 1,
            "on": True,
            "relay": relay,
            "device": data["device"],
            "account": data["account"],
            "slug": data["slug"],
            "credential": data["credential"],
            "token": secret,
            "token_id": row.id,
            "token_label": row.label,
            "company_file": _company_here(),
            "company_name": current_company_name(db),
            "linked_at": datetime.now(timezone.utc).isoformat(),
        }
        _save(record)
    except Exception:
        # nothing half-made stays: the relay forgets the device
        _relay_post(relay, account.UNLINK_PATH, None, bearer=data["credential"])
        raise
    _start(record)
    return status()


def _record_here() -> dict:
    record = _load()
    if record is None:
        raise SwitchboardError(409, NOT_LINKED)
    if record["company_file"] != _company_here():
        raise SwitchboardError(409, OTHER_COMPANY.format(name=record["company_name"]))
    return record


def new_code() -> dict:
    """A one-time sign-in code for the relay's sign-in page."""
    record = _record_here()
    status_code, data, raw = _relay_post(
        record["relay"],
        account.CODE_PATH,
        {"for": "sign-in"},
        bearer=record["credential"],
    )
    if status_code != 201:
        raise SwitchboardError(
            502, account._sentence(raw, status_code, record["relay"])
        )
    code = data.get("code") if isinstance(data, dict) else None
    expires = data.get("expires") if isinstance(data, dict) else None
    if not (isinstance(code, str) and account._CODE.fullmatch(code)):
        raise SwitchboardError(
            502,
            f"The relay at {record['relay']} answered something that is not a code.",
        )
    return {"code": code, "expires": expires if isinstance(expires, str) else None}


def disconnect(db) -> dict:
    """Close the line, unlink at the relay, revoke the token, forget the
    file. The file and the token go even when the relay cannot be told."""
    from app.models.api_tokens import ApiToken

    try:
        record = _record_here()
    except SwitchboardError as exc:
        if not exc.unreadable:
            raise
        # what cannot be read cannot be unlinked: forget it here
        stop()
        _forget()
        return {"unlinked": False, "revoked": False}
    stop()
    told = False
    try:
        status_code, _, _ = _relay_post(
            record["relay"], account.UNLINK_PATH, None, bearer=record["credential"]
        )
        told = 200 <= status_code < 300 or status_code in (401, 404)
    except SwitchboardError as exc:
        logger.warning("the relay was not told of the disconnect: %s", exc.sentence)
    revoked = False
    row = db.query(ApiToken).filter(ApiToken.id == record["token_id"]).first()
    if row is not None and row.label == record["token_label"]:
        row.is_active = False
        db.commit()
        revoked = True
    _forget()
    return {"unlinked": told, "revoked": revoked}
