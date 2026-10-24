"""Signed Mercury balance updates and ordered bank balance snapshots."""

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from decimal import Decimal

from app.models.settings import Settings
from app.services.mercury_service import MercuryError, _id, _money

WEBHOOK_PATH = "/api/mercury/webhook"
EVENT_TYPES = ["checkingAccount.balance.updated", "savingsAccount.balance.updated"]
BALANCE_FIELDS = {
    "currentBalance": "balance",
    "availableBalance": "available_balance",
    "inFlightBalance": "in_flight_balance",
}
CACHE_KEY = "mercury_accounts_cache"


class BalanceConflict(MercuryError):
    """Another worker updated the snapshot; retry this request."""


def verify_signature(body: bytes, header: str, secret: str) -> None:
    """Verify Mercury's raw-body HMAC and its five-minute delivery window."""
    try:
        parts = [part.strip().split("=", 1) for part in header.split(",")]
        if len(parts) != 2 or {p[0] for p in parts} != {"t", "v1"}:
            raise ValueError
        fields = dict(parts)
        stamp = fields["t"]
        if not stamp.isascii() or not stamp.isdecimal() or len(stamp) > 12:
            raise ValueError
        if not secret or abs(time.time() - int(stamp)) > 300:
            raise ValueError
        signature = bytes.fromhex(fields["v1"])
        expected = hmac.new(
            secret.encode(), stamp.encode() + b"." + body, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise MercuryError("Invalid or expired Mercury signature") from None


def parse_event(body: bytes) -> dict:
    try:
        event = json.loads(body, parse_float=Decimal)
        if not isinstance(event, dict):
            raise ValueError
        event["id"] = _id(event.get("id"))
        event["resourceId"] = _id(event.get("resourceId"))
        version = event["resourceVersion"]
        if type(version) is not int or not 1 <= version < 2**63:
            raise ValueError
        occurred = datetime.fromisoformat(event["occurredAt"])
        if occurred.tzinfo is None or occurred.timestamp() > time.time() + 300:
            raise ValueError
        event["occurredAt"] = occurred.astimezone(timezone.utc).isoformat()
        if not isinstance(event["mergePatch"], dict):
            raise ValueError
        return event
    except (ValueError, KeyError, TypeError, OverflowError):
        raise MercuryError("Invalid Mercury event") from None


def public_accounts(accounts: list[dict]) -> list[dict]:
    return [
        {k: v for k, v in account.items() if not k.startswith("_")}
        for account in accounts
    ]


def _stamp(account):
    stamps = [v["observed_at"] for v in account.get("_balance_state", {}).values()]
    if stamps:
        account["balance_updated_at"] = max(stamps)


def merge_snapshots(existing: list[dict], snapshots: list[dict]) -> list[dict]:
    """A poll cannot overwrite a field observed after that poll started."""
    by_id = {a["id"]: a for a in existing}
    merged = []
    for snapshot in snapshots:
        old = by_id.get(snapshot["id"], {})
        account = {**old, **snapshot}
        state = dict(old.get("_balance_state", {}))
        stamp = snapshot["_snapshot_at"]
        for field in BALANCE_FIELDS.values():
            if field not in snapshot:
                continue
            previous = state.get(field, {})
            if previous.get("observed_at", "") > stamp:
                account[field] = old[field]
            else:
                state[field] = {**previous, "observed_at": stamp, "snapshot_at": stamp}
        account["_balance_state"] = state
        _stamp(account)
        merged.append(account)
    return merged


def apply_event(accounts: list[dict], event: dict) -> str:
    """Apply each changed balance once, ordered by its resource version."""
    if (
        event.get("resourceType") not in ("checkingAccount", "savingsAccount")
        or event.get("operationType") != "update"
    ):
        return "ignored"
    account = next((a for a in accounts if a["id"] == event["resourceId"]), None)
    if account is None:
        return "ignored"
    patch = event["mergePatch"]
    # Validate the whole patch before changing any balance.
    values = {
        field: str(_money(patch[key]))
        for key, field in BALANCE_FIELDS.items()
        if key in patch
    }
    state = account.setdefault("_balance_state", {})
    changed = False
    for field, value in values.items():
        previous = state.get(field, {})
        if (
            event["resourceVersion"] <= previous.get("version", 0)
            or event["id"] == previous.get("event_id")
            or event["occurredAt"] <= previous.get("snapshot_at", "")
        ):
            continue
        account[field] = value
        state[field] = {
            **previous,
            "version": event["resourceVersion"],
            "event_id": event["id"],
            "observed_at": event["occurredAt"],
        }
        changed = True
    _stamp(account)
    return "updated" if changed else "ignored"


def update_cache(db, transform):
    """Update the cache in the caller's transaction, across server workers."""
    row = (
        db.query(Settings)
        .filter_by(key=CACHE_KEY)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if row is None:
        row = Settings(key=CACHE_KEY, value="[]")
        db.add(row)
        db.flush()
    previous = row.value
    accounts = transform(json.loads(previous))
    count = (
        db.query(Settings)
        .filter(Settings.id == row.id, Settings.value == previous)
        .update(
            {Settings.value: json.dumps(accounts)},
            synchronize_session=False,
        )
    )
    if count != 1:
        raise BalanceConflict("A Mercury balance update is in progress. Try again.")
    db.expire(row)
    return accounts


def save_snapshots(db, snapshots):
    return update_cache(db, lambda existing: merge_snapshots(existing, snapshots))
