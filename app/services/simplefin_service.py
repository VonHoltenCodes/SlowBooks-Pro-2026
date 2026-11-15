# ============================================================================
# SimpleFIN bank feeds — pull account balances + transactions from a
# SimpleFIN server the *user* signs up for: the reference Bridge
# (bridge.simplefin.org) or any other provider that issues setup tokens.
# Nothing here is tied to one host; the claim URL inside the token decides.
#
# Protocol (https://www.simplefin.org/protocol.html):
#   1. User pastes a one-time SETUP TOKEN (base64 of a claim URL).
#   2. We POST the claim URL once and receive a permanent ACCESS URL with
#      embedded basic-auth credentials — stored as a secret setting.
#   3. Sync = GET {access_url}/accounts?start-date=... — plain JSON, no
#      OAuth, no webhooks. Desktop-friendly: outbound HTTPS only.
#
# Transactions are funneled through the same dedup + bank-rules path as
# OFX (import_transactions), keyed on SimpleFIN's stable transaction id.
# Request builders are pure functions returning httpx-ready dicts so unit
# tests can assert the exact URL/params without touching the network
# (same pattern as app/services/payments and ai_service).
# ============================================================================

import base64
import binascii
import ipaddress
import json
import logging
import socket
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy.orm import Session

from app.services.ofx_import import import_transactions

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0

# First sync reaches back this far; later syncs re-request a small overlap
# window before the last sync (dedup makes the overlap harmless). The
# reference bridge caps requests at 90 days and returns a warning when
# exceeded — 85 stays cleanly inside regardless of timezone arithmetic.
FIRST_SYNC_DAYS = 85
RESYNC_OVERLAP_DAYS = 7

# Older history, on request: a SimpleFIN server keeps as much as its bank
# connection gives it (the reference Bridge about 90 days, BankSync up to a
# year). A request may not span more than the Bridge's 90 days, so a longer
# reach is asked for in slices of this many days, oldest first.
HISTORY_WINDOW_DAYS = 85
MAX_HISTORY_MONTHS = 24


class SimpleFINError(Exception):
    """Raised with a user-safe message — never wraps raw exception text."""


def _assert_public_https(url: str) -> str:
    """SSRF guard: bridge URLs are user-supplied by design (self-hosted
    bridges are a feature), so before any request we require https and
    refuse hosts that resolve to non-public addresses — loopback, RFC1918,
    link-local/metadata, and friends. A hostile setup token must not be
    able to point SlowBooks at localhost services or the LAN.

    Returns the address that passed, so the caller connects to THAT address
    and not to whatever a second lookup returns (DNS rebinding)."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise SimpleFINError("Bridge URLs must use https")
    host = parts.hostname or ""
    if not host:
        raise SimpleFINError("Bridge URL has no hostname")
    try:
        infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise SimpleFINError("Could not resolve the bridge hostname")
    if not infos:
        raise SimpleFINError("Could not resolve the bridge hostname")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise SimpleFINError(
                "Bridge host resolves to a private or local address — refusing"
            )
    return infos[0][4][0]


def _pin(url: str, address: str) -> tuple[str, dict]:
    """Rewrite `url` to connect to `address` while keeping the hostname for
    the Host header and TLS (SNI + certificate check), so the socket cannot
    be steered elsewhere by a second DNS answer."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    ip = ipaddress.ip_address(address)
    netloc = f"[{address}]" if ip.version == 6 else address
    if parts.port:
        netloc += f":{parts.port}"
    pinned = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return pinned, {"host": host, "sni": host}


def send(request: dict, timeout: float = DEFAULT_TIMEOUT) -> httpx.Response:
    """Execute a request dict from a build_* function (hardened defaults).

    Every outbound URL passes the SSRF guard first — this is the single
    network chokepoint for the SimpleFIN feature — and the connection is
    made to the address the guard approved (host header and SNI keep the
    hostname), then the peer address is checked once more after connect."""
    address = _assert_public_https(request["url"])
    pinned_url, names = _pin(request["url"], address)
    try:
        # follow_redirects=False is load-bearing: a redirect would resolve a
        # new hostname and step straight around the pin. Keep it off.
        with httpx.Client(
            verify=True,
            follow_redirects=False,
            timeout=timeout,
            headers={"User-Agent": "slowbooks-bankfeed", "Host": names["host"]},
            trust_env=False,
        ) as client:
            response = client.request(
                request["method"],
                pinned_url,
                params=request.get("params"),
                auth=request.get("auth"),
                content=request.get("content"),
                extensions={"sni_hostname": names["sni"]},
            )
            # Peer check while the socket is still open (after the client
            # closes, getpeername() is EBADF on Linux / WSAENOTSOCK on
            # Windows — the 2.9.3 gate found exactly that). A stream that
            # cannot report its peer is not evidence either way.
            peer = _peer_address(response)
    except httpx.RequestError:
        logger.warning("SimpleFIN bridge request failed", exc_info=True)
        raise SimpleFINError("Could not reach the SimpleFIN bridge right now")
    if peer and not ipaddress.ip_address(peer).is_global:
        raise SimpleFINError(
            "Bridge host resolves to a private or local address — refusing"
        )
    return response


def _peer_address(response: httpx.Response) -> str | None:
    stream = response.extensions.get("network_stream")
    if stream is None:
        return None
    try:
        info = stream.get_extra_info("server_addr")
    except (OSError, ValueError, AttributeError):
        return None
    if not info:
        return None
    try:
        ipaddress.ip_address(info[0])
    except (ValueError, IndexError, TypeError):
        return None
    return info[0]


def decode_setup_token(setup_token: str) -> str:
    """Base64 setup token → claim URL. HTTPS is enforced."""
    token = (setup_token or "").strip()
    if not token:
        raise SimpleFINError("Setup token is empty")
    try:
        claim_url = base64.b64decode(token, validate=True).decode("utf-8").strip()
    except (binascii.Error, UnicodeDecodeError):
        raise SimpleFINError("That doesn't look like a SimpleFIN setup token")
    if not claim_url.startswith("https://"):
        raise SimpleFINError("Setup token must contain an https claim URL")
    return claim_url


def build_claim_request(claim_url: str) -> dict:
    # Claiming is a bare POST; the response body is the access URL.
    return {"method": "POST", "url": claim_url, "content": b""}


def claim_access_url(setup_token: str) -> str:
    """Exchange a setup token for the permanent access URL (one-time)."""
    claim_url = decode_setup_token(setup_token)
    resp = send(build_claim_request(claim_url))
    if resp.status_code != 200:
        logger.warning("SimpleFIN claim failed: HTTP %s", resp.status_code)
        raise SimpleFINError(
            "The SimpleFIN bridge rejected the token (setup tokens are "
            "single-use — generate a fresh one and try again)"
        )
    access_url = resp.text.strip()
    parts = urlsplit(access_url)
    if parts.scheme != "https" or not parts.username:
        raise SimpleFINError("The bridge returned an unusable access URL")
    return access_url


def _split_credentials(access_url: str) -> tuple[str, tuple[str, str]]:
    """https://user:pass@host/path → (bare URL, (user, pass))."""
    parts = urlsplit(access_url)
    if parts.scheme != "https" or not parts.username:
        raise SimpleFINError("Stored SimpleFIN access URL is invalid — reconnect")
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    bare = urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
    return bare, (parts.username, parts.password or "")


def _epoch(day: date) -> int:
    return int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp())


def build_accounts_request(
    access_url: str,
    start_date: date | None = None,
    end_date: date | None = None,
    balances_only: bool = False,
) -> dict:
    """GET {access_url}/accounts, for transactions on or after start_date
    and before end_date (the protocol's end-date is exclusive). With
    balances_only the server sends the accounts and their balances and no
    transactions (the protocol's balances-only=1): Refresh accounts (#905)."""
    bare, auth = _split_credentials(access_url)
    params = {}
    if start_date is not None:
        params["start-date"] = _epoch(start_date)
    if end_date is not None:
        params["end-date"] = _epoch(end_date)
    if balances_only:
        params["balances-only"] = 1
    return {
        "method": "GET",
        "url": bare.rstrip("/") + "/accounts",
        "params": params,
        "auth": auth,
    }


def fetch_accounts(
    access_url: str,
    start_date: date | None = None,
    end_date: date | None = None,
    balances_only: bool = False,
) -> dict:
    resp = send(build_accounts_request(access_url, start_date, end_date, balances_only))
    if resp.status_code == 403:
        # Update token keeps every mapping (#905); disconnecting loses them
        raise SimpleFINError(
            "The SimpleFIN bridge refused the stored credentials — get a "
            "fresh setup token from your SimpleFIN provider and use "
            "Update token on the Banking page"
        )
    if resp.status_code != 200:
        logger.warning("SimpleFIN accounts fetch failed: HTTP %s", resp.status_code)
        raise SimpleFINError("The SimpleFIN bridge is unavailable right now")
    try:
        data = resp.json()
    except ValueError:
        raise SimpleFINError("The SimpleFIN bridge returned an unreadable response")
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
        raise SimpleFINError("The SimpleFIN bridge returned an unreadable response")
    return data


def months_before(day: date, months: int) -> date:
    """The same day `months` calendar months earlier (the last day of that
    month when it is shorter: Mar 31 less 1 month is Feb 28)."""
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    month += 1
    for last in (31, 30, 29, 28):
        try:
            return date(year, month, min(day.day, last))
        except ValueError:
            continue
    raise ValueError(day)


def history_windows(
    since: date, until: date, days: int = HISTORY_WINDOW_DAYS
) -> list[tuple[date, date]]:
    """[since, until) cut into slices of at most `days` days, oldest first,
    each a (start-date, end-date) pair for one request."""
    windows = []
    start = since
    while start < until:
        end = min(start + timedelta(days=days), until)
        windows.append((start, end))
        start = end
    return windows


def fetch_history(access_url: str, since: date, until: date) -> dict:
    """Everything from `since` up to (not including) `until`, one request per
    slice, merged into one answer shaped like fetch_accounts': each account
    once, with its details from the latest slice and the transactions of
    every slice (a transaction two slices both return counted once), and
    each error the server gave once."""
    accounts: dict[str, dict] = {}
    order: list[str] = []
    seen: dict[str, set] = {}
    errors: list[str] = []
    windows = history_windows(since, until)
    for start, end in windows:
        data = fetch_accounts(access_url, start_date=start, end_date=end)
        for acct in data.get("accounts", []):
            key = str(acct.get("id", ""))
            txns = acct.get("transactions") or []
            if key not in accounts:
                order.append(key)
                seen[key] = set()
                merged = {**acct, "transactions": []}
            else:
                merged = {**acct, "transactions": accounts[key]["transactions"]}
            for txn in txns:
                txn_id = str(txn.get("id", ""))
                if txn_id and txn_id in seen[key]:
                    continue
                if txn_id:
                    seen[key].add(txn_id)
                merged["transactions"].append(txn)
            accounts[key] = merged
        for err in data.get("errors", []) or []:
            if str(err) not in errors:
                errors.append(str(err))
    return {
        "errors": errors,
        "accounts": [accounts[k] for k in order],
        "requests": len(windows),
    }


def account_summaries(data: dict) -> list[dict]:
    """UI-facing snapshot of the bridge's accounts (no transactions)."""
    out = []
    for acct in data.get("accounts", []):
        org = acct.get("org") or {}
        out.append(
            {
                "id": str(acct.get("id", "")),
                "name": str(acct.get("name", "")),
                "org": str(org.get("name") or org.get("domain") or ""),
                "currency": str(acct.get("currency", "")),
                "balance": str(acct.get("balance", "")),
            }
        )
    return out


def merge_accounts_cache(
    old_cache: list, summaries: list[dict], mapped_ids
) -> list[dict]:
    """The account list the page shows (#905): the bridge's accounts as it
    reports them now, then each mapped account it no longer reports, kept
    from the earlier list with gone=True, so the row can be moved to another
    account or unmapped rather than vanishing with its mapping still saved.
    A gone account nothing is mapped from any more is dropped."""
    out = [dict(a, gone=False) for a in summaries]
    present = {a["id"] for a in out}
    mapped = {str(i) for i in mapped_ids}
    kept = set()
    for old in old_cache if isinstance(old_cache, list) else []:
        if not isinstance(old, dict):
            continue
        sf_id = str(old.get("id", ""))
        if sf_id in present or sf_id not in mapped or sf_id in kept:
            continue
        kept.add(sf_id)
        out.append(
            {
                "id": sf_id,
                "name": str(old.get("name", "")),
                "org": str(old.get("org", "")),
                "currency": str(old.get("currency", "")),
                "balance": str(old.get("balance", "")),
                "gone": True,
            }
        )
    for sf_id in sorted(mapped - present - kept):
        # mapped, and in no list this connection has seen
        out.append(
            {
                "id": sf_id,
                "name": sf_id,
                "org": "",
                "currency": "",
                "balance": "",
                "gone": True,
            }
        )
    return out


def parse_feed_last_sync(raw: str) -> dict[str, str]:
    """Settings JSON → {simplefin_account_id: ISO time of its last sync}."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v}


def feed_start(today: date, last_sync: str) -> date:
    """Where one feed's sync begins: RESYNC_OVERLAP_DAYS before its own last
    sync, or FIRST_SYNC_DAYS back for a feed that has never synced."""
    if last_sync:
        try:
            last = date.fromisoformat(last_sync[:10])
            return last - timedelta(days=RESYNC_OVERLAP_DAYS)
        except ValueError:
            pass
    return today - timedelta(days=FIRST_SYNC_DAYS)


def to_import_rows(
    sf_account: dict, reverse_signs: bool = False
) -> tuple[list[dict], list[str]]:
    """SimpleFIN transactions → the dict shape import_transactions expects.

    The protocol says a positive amount is money deposited into the
    account, and that is how amounts are read. Some bank connections
    report an account the other way round (a checking account presented
    as a liability: negative balance, deposits negative); the account
    object has no type field to detect it by, so the person says so per
    feed and reverse_signs flips every amount.

    Skips pending transactions (their ids are not stable until posted).
    """
    rows, errors = [], []
    name = str(sf_account.get("name", "")) or str(sf_account.get("id", ""))
    for txn in sf_account.get("transactions", []):
        if txn.get("pending"):
            continue
        txn_id = str(txn.get("id", "")).strip()
        posted = txn.get("posted") or txn.get("transacted_at")
        try:
            amount = Decimal(str(txn.get("amount", "")))
            txn_date = datetime.fromtimestamp(int(posted), tz=timezone.utc).date()
        except (InvalidOperation, TypeError, ValueError, OSError, OverflowError):
            errors.append(f"{name}: skipped a transaction with malformed data")
            continue
        if not txn_id:
            errors.append(f"{name}: skipped a transaction without a stable id")
            continue
        if reverse_signs:
            amount = -amount
        rows.append(
            {
                "fitid": txn_id,
                "date": txn_date,
                "amount": amount,
                "payee": str(txn.get("payee") or txn.get("description") or ""),
                "memo": str(txn.get("memo") or txn.get("description") or ""),
                "type": "",
            }
        )
    return rows, errors


def parse_account_map(raw: str) -> dict[str, int]:
    """Settings JSON → {simplefin_account_id: bank_account_id}."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, value in data.items():
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def parse_reversed(raw: str) -> list[str]:
    """Settings JSON → SimpleFIN account ids whose amounts are reversed."""
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if isinstance(item, (str, int))]


def sync_accounts(
    db: Session,
    data: dict,
    account_map: dict[str, int],
    reversed_ids: frozenset[str] | set[str] = frozenset(),
    since_by_id: dict[str, date] | None = None,
) -> dict:
    """Import every mapped bridge account's transactions. Returns totals,
    and under "synced" the ids the bridge answered for. Accounts in
    reversed_ids have their amounts' signs flipped. With since_by_id, an
    account's transactions from before its own start are left out: one
    request reaches back as far as the feed that needs the most, and each
    feed takes its own window of it (#905)."""
    imported = skipped = 0
    warnings: list[str] = []
    synced: list[str] = []
    by_id = {str(a.get("id", "")): a for a in data.get("accounts", [])}
    for sf_id, bank_account_id in account_map.items():
        sf_account = by_id.get(sf_id)
        if sf_account is None:
            warnings.append(
                "A mapped account was missing from the bridge response "
                "(bank connection may need attention on the SimpleFIN side)"
            )
            continue
        rows, row_errors = to_import_rows(
            sf_account, reverse_signs=sf_id in reversed_ids
        )
        since = (since_by_id or {}).get(sf_id)
        if since is not None:
            rows = [row for row in rows if row["date"] >= since]
        warnings.extend(row_errors)
        synced.append(sf_id)
        result = import_transactions(
            db, bank_account_id, rows, import_source="simplefin"
        )
        imported += result["imported"]
        skipped += result["skipped"]
    for sf_error in data.get("errors", []):
        warnings.append(str(sf_error))
    return {
        "imported": imported,
        "skipped": skipped,
        "warnings": warnings,
        "synced": synced,
    }
