# ============================================================================
# SimpleFIN bank feed routes — connect (claim), map accounts, sync.
#
# The user signs up for a SimpleFIN bridge themselves and pastes a setup
# token; we never hold a developer credential. All endpoints are
# session-authenticated (nothing here is webhook/public). Error messages
# are fixed strings from SimpleFINError — raw exception text never
# reaches a response.
#
# Who may do what: connecting (claim), remapping accounts and disconnecting
# change the stored access URL/credentials and the books' wiring, so they
# are an administrator's — the same boundary as the connection settings
# under /api/settings and the AI provider key (require_admin). Claim also
# makes the server POST to a URL decoded from the token, so a non-admin
# must not be able to reach it at all. Syncing and reading status on an
# already-connected feed is daily books work and stays the bookkeeper's.
# ============================================================================

import json
from datetime import date, datetime, timedelta, timezone

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from app.schemas.common import StrictModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.banking import BankAccount
from app.routes._roles import require_admin
from app.services import simplefin_service
from app.services.bank_posting import feed_for_ledger_account
from app.services.settings_service import get_setting_raw, set_setting
from app.services.simplefin_service import SimpleFINError

router = APIRouter(prefix="/api/simplefin", tags=["simplefin"])


class ClaimRequest(StrictModel):
    setup_token: str


class MapRequest(StrictModel):
    mapping: dict[str, int] = {}
    # Feed id -> a bank or card account in the chart: its bank feed record
    # is used, or made when it has none (bank_posting.feed_for_ledger_account),
    # so the "Imports into" list can offer the chart's own accounts.
    ledger_mapping: Optional[dict[str, int]] = None
    # Feeds whose bank connection reports amounts the other way round
    # (deposits negative). Omitted leaves the stored choice as it is.
    reversed: Optional[list[str]] = None


class SyncRequest(StrictModel):
    # Reach back this many months rather than from the last sync: brings in
    # the older history a SimpleFIN server keeps. Transactions already
    # imported are skipped as on any sync.
    history_months: Optional[int] = Field(
        default=None, ge=1, le=simplefin_service.MAX_HISTORY_MONTHS
    )


def _connected(db: Session) -> str:
    access_url = get_setting_raw(db, "simplefin_access_url") or ""
    if not access_url:
        raise HTTPException(status_code=400, detail="SimpleFIN is not connected")
    return access_url


def _refuse_a_second_feed(db: Session, mapping: dict[str, int]) -> None:
    """Two SimpleFIN feeds into one account would bring every line of that
    register in twice (#819), so each feed needs an account of its own, as
    Mercury's mapping already insists. Two feed records on one chart account
    are one register, so they count as one. The map is replaced whole, so a
    feed saved again onto the account it already has is never a second feed."""
    seen: set[tuple[str, int]] = set()
    for bank_account_id in mapping.values():
        ba = db.get(BankAccount, bank_account_id)
        key = (
            ("acct", ba.account_id)
            if ba and ba.account_id
            else ("feed", bank_account_id)
        )
        if key in seen:
            raise HTTPException(
                status_code=400,
                detail="Map each SimpleFIN account to a separate bank account",
            )
        seen.add(key)


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
        "reversed": simplefin_service.parse_reversed(
            get_setting_raw(db, "simplefin_reversed") or "[]"
        ),
        "accounts": cache if isinstance(cache, list) else [],
    }


@router.post("/claim")
def claim(payload: ClaimRequest, request: Request, db: Session = Depends(get_db)):
    """Exchange a one-time setup token for the permanent access URL."""
    require_admin(request)  # connects the feed; also triggers an outbound POST
    try:
        access_url = simplefin_service.claim_access_url(payload.setup_token)
        data = simplefin_service.fetch_accounts(access_url)
    except SimpleFINError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    accounts = simplefin_service.account_summaries(data)
    set_setting(db, "simplefin_access_url", access_url)
    set_setting(db, "simplefin_accounts_cache", json.dumps(accounts))
    set_setting(db, "simplefin_account_map", "{}")
    set_setting(db, "simplefin_reversed", "[]")
    set_setting(db, "simplefin_last_sync", "")
    db.commit()
    return {"connected": True, "accounts": accounts}


@router.post("/map")
def save_mapping(payload: MapRequest, request: Request, db: Session = Depends(get_db)):
    """Store which bridge account feeds which SlowBooks bank account."""
    require_admin(request)  # the feed's wiring is a connection setting
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
    for sf_id, ledger_account_id in (payload.ledger_mapping or {}).items():
        if not ledger_account_id:
            clean.pop(str(sf_id), None)
            continue
        feed = feed_for_ledger_account(db, int(ledger_account_id))
        clean[str(sf_id)] = feed.id
    _refuse_a_second_feed(db, clean)
    set_setting(db, "simplefin_account_map", json.dumps(clean))
    if payload.reversed is not None:
        reversed_ids = sorted({str(sf_id) for sf_id in payload.reversed})
        set_setting(db, "simplefin_reversed", json.dumps(reversed_ids))
    else:
        reversed_ids = simplefin_service.parse_reversed(
            get_setting_raw(db, "simplefin_reversed") or "[]"
        )
    db.commit()
    return {"account_map": clean, "reversed": reversed_ids}


@router.post("/sync")
def sync(payload: Optional[SyncRequest] = None, db: Session = Depends(get_db)):
    """Pull transactions for every mapped account through dedup + rules.
    With history_months, from that many months back to today, fetched in
    slices the SimpleFIN Bridge accepts."""
    access_url = _connected(db)
    account_map = simplefin_service.parse_account_map(
        get_setting_raw(db, "simplefin_account_map") or "{}"
    )
    if not account_map:
        raise HTTPException(
            status_code=400,
            detail="Map at least one SimpleFIN account to a bank account first",
        )

    history = payload.history_months if payload else None
    last_sync_raw = get_setting_raw(db, "simplefin_last_sync") or ""
    if history:
        start = simplefin_service.months_before(date.today(), history)
    elif last_sync_raw:
        try:
            last = date.fromisoformat(last_sync_raw[:10])
            start = last - timedelta(days=simplefin_service.RESYNC_OVERLAP_DAYS)
        except ValueError:
            start = date.today() - timedelta(days=simplefin_service.FIRST_SYNC_DAYS)
    else:
        start = date.today() - timedelta(days=simplefin_service.FIRST_SYNC_DAYS)

    try:
        if history:
            # through today: the protocol's end-date is exclusive
            data = simplefin_service.fetch_history(
                access_url, start, date.today() + timedelta(days=1)
            )
        else:
            data = simplefin_service.fetch_accounts(access_url, start_date=start)
    except SimpleFINError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    reversed_ids = set(
        simplefin_service.parse_reversed(
            get_setting_raw(db, "simplefin_reversed") or "[]"
        )
    )
    result = simplefin_service.sync_accounts(db, data, account_map, reversed_ids)
    result["since"] = start.isoformat()
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
def disconnect(request: Request, db: Session = Depends(get_db)):
    """Forget the access URL, mapping, and cache. Imported rows stay."""
    require_admin(request)  # dropping the stored credential is an admin's
    set_setting(db, "simplefin_access_url", "")
    set_setting(db, "simplefin_account_map", "{}")
    set_setting(db, "simplefin_reversed", "[]")
    set_setting(db, "simplefin_accounts_cache", "[]")
    set_setting(db, "simplefin_last_sync", "")
    db.commit()
    return {"connected": False}
