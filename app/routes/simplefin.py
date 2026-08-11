# ============================================================================
# SimpleFIN bank feed routes — connect (claim), map accounts, sync.
#
# The user signs up for a SimpleFIN bridge themselves and pastes a setup
# token; we never hold a developer credential. All endpoints are
# session-authenticated (nothing here is webhook/public). Error messages
# are fixed strings from SimpleFINError — raw exception text never
# reaches a response.
# ============================================================================

import json
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.banking import BankAccount
from app.services import simplefin_service
from app.services.settings_service import get_setting_raw, set_setting
from app.services.simplefin_service import SimpleFINError

router = APIRouter(prefix="/api/simplefin", tags=["simplefin"])


class ClaimRequest(BaseModel):
    setup_token: str


class MapRequest(BaseModel):
    mapping: dict[str, int]


def _connected(db: Session) -> str:
    access_url = get_setting_raw(db, "simplefin_access_url") or ""
    if not access_url:
        raise HTTPException(status_code=400, detail="SimpleFIN is not connected")
    return access_url


@router.get("/status")
def status(db: Session = Depends(get_db)):
    access_url = get_setting_raw(db, "simplefin_access_url") or ""
    try:
        cache = json.loads(get_setting_raw(db, "simplefin_accounts_cache") or "[]")
    except ValueError:
        cache = []
    return {
        "connected": bool(access_url),
        "last_sync": get_setting_raw(db, "simplefin_last_sync") or "",
        "account_map": simplefin_service.parse_account_map(
            get_setting_raw(db, "simplefin_account_map") or "{}"
        ),
        "accounts": cache if isinstance(cache, list) else [],
    }


@router.post("/claim")
def claim(payload: ClaimRequest, db: Session = Depends(get_db)):
    """Exchange a one-time setup token for the permanent access URL."""
    try:
        access_url = simplefin_service.claim_access_url(payload.setup_token)
        data = simplefin_service.fetch_accounts(access_url)
    except SimpleFINError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    accounts = simplefin_service.account_summaries(data)
    set_setting(db, "simplefin_access_url", access_url)
    set_setting(db, "simplefin_accounts_cache", json.dumps(accounts))
    set_setting(db, "simplefin_account_map", "{}")
    set_setting(db, "simplefin_last_sync", "")
    db.commit()
    return {"connected": True, "accounts": accounts}


@router.post("/map")
def save_mapping(payload: MapRequest, db: Session = Depends(get_db)):
    """Store which bridge account feeds which SlowBooks bank account."""
    _connected(db)
    clean: dict[str, int] = {}
    for sf_id, bank_account_id in payload.mapping.items():
        if not bank_account_id:
            continue  # unmapped rows are simply omitted
        ba = db.query(BankAccount).filter(BankAccount.id == bank_account_id).first()
        if not ba:
            raise HTTPException(
                status_code=404, detail=f"Bank account {bank_account_id} not found"
            )
        clean[str(sf_id)] = int(bank_account_id)
    set_setting(db, "simplefin_account_map", json.dumps(clean))
    db.commit()
    return {"account_map": clean}


@router.post("/sync")
def sync(db: Session = Depends(get_db)):
    """Pull transactions for every mapped account through dedup + rules."""
    access_url = _connected(db)
    account_map = simplefin_service.parse_account_map(
        get_setting_raw(db, "simplefin_account_map") or "{}"
    )
    if not account_map:
        raise HTTPException(
            status_code=400,
            detail="Map at least one SimpleFIN account to a bank account first",
        )

    last_sync_raw = get_setting_raw(db, "simplefin_last_sync") or ""
    if last_sync_raw:
        try:
            last = date.fromisoformat(last_sync_raw[:10])
            start = last - timedelta(days=simplefin_service.RESYNC_OVERLAP_DAYS)
        except ValueError:
            start = date.today() - timedelta(days=simplefin_service.FIRST_SYNC_DAYS)
    else:
        start = date.today() - timedelta(days=simplefin_service.FIRST_SYNC_DAYS)

    try:
        data = simplefin_service.fetch_accounts(access_url, start_date=start)
    except SimpleFINError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    result = simplefin_service.sync_accounts(db, data, account_map)
    set_setting(
        db,
        "simplefin_accounts_cache",
        json.dumps(simplefin_service.account_summaries(data)),
    )
    set_setting(
        db,
        "simplefin_last_sync",
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    db.commit()
    return result


@router.post("/disconnect")
def disconnect(db: Session = Depends(get_db)):
    """Forget the access URL, mapping, and cache. Imported rows stay."""
    set_setting(db, "simplefin_access_url", "")
    set_setting(db, "simplefin_account_map", "{}")
    set_setting(db, "simplefin_accounts_cache", "[]")
    set_setting(db, "simplefin_last_sync", "")
    db.commit()
    return {"connected": False}
