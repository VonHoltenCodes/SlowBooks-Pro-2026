"""Connect a read-only Mercury feed to the company's bank review queue."""

import json
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.banking import BankAccount, BankTransaction
from app.schemas.common import StrictModel
from app.services import mercury_service as mercury
from app.services import mercury_balances as balances
from app.services.currency import home_currency
from app.services.ofx_import import import_transactions
from app.services.settings_service import get_setting_raw, set_setting

router = APIRouter(prefix="/api/mercury", tags=["mercury"])


class ConnectRequest(StrictModel):
    api_token: SecretStr


class MappingRequest(StrictModel):
    mapping: dict[UUID, int]


class SyncRequest(StrictModel):
    since: date


class WebhookSettingsRequest(StrictModel):
    signing_secret: SecretStr


def _setting(db, key, default):
    return get_setting_raw(db, f"mercury_{key}") or default


def _token(db):
    token = _setting(db, "api_token", "")
    if not token:
        raise HTTPException(400, "Mercury is not connected")
    return token


def _mapping(db):
    try:
        data = json.loads(_setting(db, "account_map", "{}"))
        return {str(UUID(k)): int(v) for k, v in data.items()}
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, "Save the Mercury account mapping again")


def _validate_mapping(db, mapping, accounts):
    if home_currency(db) != "USD":
        raise HTTPException(400, "The Mercury feed requires USD company books")
    known = {a["id"] for a in accounts}
    if len(set(mapping.values())) != len(mapping):
        raise HTTPException(400, "Map each Mercury account to a separate bank account")
    for mercury_id, bank_id in mapping.items():
        if mercury_id not in known:
            raise HTTPException(
                400,
                "A mapped Mercury account is unavailable. Refresh accounts and check the mapping.",
            )
        bank = db.get(BankAccount, bank_id)
        if (
            not bank
            or not bank.is_active
            or not bank.account
            or not bank.account.is_active
            or bank.account.bank_kind != "bank"
        ):
            raise HTTPException(
                400, "Choose an active bank feed linked to a bank ledger account"
            )
        other = (
            db.query(BankTransaction.id)
            .filter(
                BankTransaction.import_id.startswith(f"mercury:{mercury_id}:"),
                BankTransaction.bank_account_id != bank_id,
            )
            .first()
        )
        if other:
            raise HTTPException(
                400,
                "This Mercury account has imports in another bank feed. Keep its original mapping to avoid duplicate entries.",
            )


@router.get("/status")
def status(db: Session = Depends(get_db)):
    return {
        "connected": bool(_setting(db, "api_token", "")),
        "accounts": balances.public_accounts(
            json.loads(_setting(db, "accounts_cache", "[]"))
        ),
        "account_map": _mapping(db),
        "last_sync": _setting(db, "last_sync", ""),
        "since": _setting(db, "since", (date.today() - timedelta(days=90)).isoformat()),
        "webhook": {
            "configured": bool(_setting(db, "webhook_secret", "")),
            "path": balances.WEBHOOK_PATH,
            "event_types": balances.EVENT_TYPES,
            "last_received": _setting(db, "webhook_last_received", ""),
        },
    }


@router.post("/connect")
def connect(payload: ConnectRequest, db: Session = Depends(get_db)):
    token = payload.api_token.get_secret_value().strip()
    if (
        not token
        or len(token) > 4096
        or any(c.isspace() or not c.isascii() or ord(c) < 32 for c in token)
    ):
        raise HTTPException(400, "Enter a Mercury read-only API token")
    try:
        accounts = mercury.fetch_accounts(token)
    except mercury.MercuryError as exc:
        raise HTTPException(400, str(exc))
    if not accounts:
        raise HTTPException(
            400, "No active Mercury bank accounts are available to this token"
        )
    # Token rotation preserves mappings for accounts still visible to the token.
    ids = {a["id"] for a in accounts}
    mapping = {k: v for k, v in _mapping(db).items() if k in ids}
    set_setting(db, "mercury_api_token", token)
    accounts = balances.save_snapshots(db, accounts)
    set_setting(db, "mercury_account_map", json.dumps(mapping))
    db.commit()
    return {"connected": True, "accounts": balances.public_accounts(accounts)}


@router.post("/refresh")
def refresh(db: Session = Depends(get_db)):
    try:
        accounts = mercury.fetch_accounts(_token(db))
    except mercury.MercuryError as exc:
        raise HTTPException(502, str(exc))
    accounts = balances.save_snapshots(db, accounts)
    db.commit()
    return {"accounts": balances.public_accounts(accounts)}


@router.post("/map")
def save_mapping(payload: MappingRequest, db: Session = Depends(get_db)):
    _token(db)
    mapping = {str(k): v for k, v in payload.mapping.items() if v}
    _validate_mapping(db, mapping, json.loads(_setting(db, "accounts_cache", "[]")))
    set_setting(db, "mercury_account_map", json.dumps(mapping))
    db.commit()
    return {"account_map": mapping}


@router.post("/sync")
def sync(payload: SyncRequest, db: Session = Depends(get_db)):
    token = _token(db)
    mapping = _mapping(db)
    if not mapping:
        raise HTTPException(400, "Map at least one Mercury account first")
    if payload.since > date.today():
        raise HTTPException(400, "Choose a start date on or before today")
    prepared, warnings = {}, []
    try:
        accounts = mercury.fetch_accounts(token)
        _validate_mapping(db, mapping, accounts)
        # Finish every network read and validation before importing any rows.
        for mercury_id, bank_id in mapping.items():
            txns = mercury.fetch_transactions(token, mercury_id, payload.since)
            rows, messages = mercury.to_import_rows(txns, mercury_id)
            prepared[bank_id] = rows
            warnings.extend(messages)
    except mercury.MercuryError as exc:
        raise HTTPException(502, str(exc))
    result = {"imported": 0, "skipped": 0, "matched": 0}
    for bank_id, rows in prepared.items():
        # Serialize import dedup for this feed across PostgreSQL workers.
        db.query(BankAccount).filter(BankAccount.id == bank_id).with_for_update().one()
        imported = import_transactions(db, bank_id, rows, import_source="mercury")
        for key in result:
            result[key] += imported[key]
    balances.save_snapshots(db, accounts)
    set_setting(db, "mercury_since", payload.since.isoformat())
    set_setting(
        db,
        "mercury_last_sync",
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    db.commit()
    return {**result, "warnings": list(dict.fromkeys(warnings))}


@router.post("/disconnect")
def disconnect(db: Session = Depends(get_db)):
    balances.update_cache(db, lambda accounts: [])
    for key, value in {
        "api_token": "",
        "account_map": "{}",
        "webhook_secret": "",
        "webhook_last_received": "",
        "last_sync": "",
        "since": "",
    }.items():
        set_setting(db, f"mercury_{key}", value)
    db.commit()
    return {"connected": False}


@router.post("/webhook-settings")
def configure_webhook(payload: WebhookSettingsRequest, db: Session = Depends(get_db)):
    _token(db)
    secret = payload.signing_secret.get_secret_value().strip()
    if (
        not secret
        or len(secret) > 4096
        or any(c.isspace() or not c.isascii() or ord(c) < 32 for c in secret)
    ):
        raise HTTPException(400, "Enter the signing secret from your Mercury webhook")
    set_setting(db, "mercury_webhook_secret", secret)
    set_setting(db, "mercury_webhook_last_received", "")
    db.commit()
    return {"configured": True}


@router.delete("/webhook-settings")
def disable_webhook(db: Session = Depends(get_db)):
    set_setting(db, "mercury_webhook_secret", "")
    set_setting(db, "mercury_webhook_last_received", "")
    db.commit()
    return {"configured": False}


@router.post("/webhook")
async def receive_webhook(request: Request, db: Session = Depends(get_db)):
    """The raw-body signature authenticates this public endpoint."""
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 65536:
            raise HTTPException(413, "Mercury webhook body is too large")
    try:
        balances.verify_signature(
            bytes(body),
            request.headers.get("Mercury-Signature", ""),
            _setting(db, "webhook_secret", ""),
        )
    except mercury.MercuryError as exc:
        raise HTTPException(401, str(exc))
    try:
        event = balances.parse_event(bytes(body))
        result = "ignored"

        def apply(accounts):
            nonlocal result
            result = balances.apply_event(accounts, event)
            return accounts

        balances.update_cache(db, apply)
    except balances.BalanceConflict as exc:
        raise HTTPException(503, str(exc))
    except mercury.MercuryError as exc:
        raise HTTPException(400, str(exc))
    set_setting(
        db, "mercury_webhook_last_received", datetime.now(timezone.utc).isoformat()
    )
    db.commit()
    return {"status": result}
