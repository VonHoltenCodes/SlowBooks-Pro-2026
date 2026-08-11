# ============================================================================
# SimpleFIN bank feeds — pull account balances + transactions from a
# SimpleFIN Bridge (bridge.simplefin.org) the *user* signs up for.
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
import json
import logging
from datetime import date, datetime, time, timezone
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


class SimpleFINError(Exception):
    """Raised with a user-safe message — never wraps raw exception text."""


def send(request: dict, timeout: float = DEFAULT_TIMEOUT) -> httpx.Response:
    """Execute a request dict from a build_* function (hardened defaults)."""
    with httpx.Client(
        verify=True,
        follow_redirects=False,
        timeout=timeout,
        headers={"User-Agent": "slowbooks-bankfeed"},
        trust_env=False,
    ) as client:
        return client.request(
            request["method"],
            request["url"],
            params=request.get("params"),
            auth=request.get("auth"),
            content=request.get("content"),
        )


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


def build_accounts_request(access_url: str, start_date: date | None = None) -> dict:
    bare, auth = _split_credentials(access_url)
    params = {}
    if start_date is not None:
        epoch = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        params["start-date"] = int(epoch.timestamp())
    return {
        "method": "GET",
        "url": bare.rstrip("/") + "/accounts",
        "params": params,
        "auth": auth,
    }


def fetch_accounts(access_url: str, start_date: date | None = None) -> dict:
    resp = send(build_accounts_request(access_url, start_date))
    if resp.status_code == 403:
        raise SimpleFINError(
            "The SimpleFIN bridge refused the stored credentials — "
            "disconnect and connect again with a fresh token"
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


def to_import_rows(sf_account: dict) -> tuple[list[dict], list[str]]:
    """SimpleFIN transactions → the dict shape import_transactions expects.

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


def sync_accounts(
    db: Session,
    data: dict,
    account_map: dict[str, int],
) -> dict:
    """Import every mapped bridge account's transactions. Returns totals."""
    imported = skipped = 0
    warnings: list[str] = []
    by_id = {str(a.get("id", "")): a for a in data.get("accounts", [])}
    for sf_id, bank_account_id in account_map.items():
        sf_account = by_id.get(sf_id)
        if sf_account is None:
            warnings.append(
                "A mapped account was missing from the bridge response "
                "(bank connection may need attention on the SimpleFIN side)"
            )
            continue
        rows, row_errors = to_import_rows(sf_account)
        warnings.extend(row_errors)
        result = import_transactions(
            db, bank_account_id, rows, import_source="simplefin"
        )
        imported += result["imported"]
        skipped += result["skipped"]
    for sf_error in data.get("errors", []):
        warnings.append(str(sf_error))
    return {"imported": imported, "skipped": skipped, "warnings": warnings}
