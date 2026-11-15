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
# Refreshing the account list, moving a mapping to another SimpleFIN account
# and updating the token (#905) are the same connection work: an
# administrator's. Each writes settings rows, which the audit hooks log as
# they log claim and disconnect.
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


class MoveRequest(StrictModel):
    # the mapped SimpleFIN account, and the one its mapping moves to
    from_id: str = Field(min_length=1, max_length=200)
    to_id: str = Field(min_length=1, max_length=200)


def _account_map(db: Session) -> dict[str, int]:
    return simplefin_service.parse_account_map(
        get_setting_raw(db, "simplefin_account_map") or "{}"
    )


def _reversed(db: Session) -> list[str]:
    return simplefin_service.parse_reversed(
        get_setting_raw(db, "simplefin_reversed") or "[]"
    )


def _cache(db: Session) -> list:
    try:
        cache = json.loads(get_setting_raw(db, "simplefin_accounts_cache") or "[]")
    except ValueError:
        return []
    return cache if isinstance(cache, list) else []


def _feed_dates(db: Session, account_map: dict[str, int]) -> dict[str, str]:
    """Each feed's own last sync (#905). A connection from before these were
    kept has only the one shared date: every feed mapped then has synced to
    it, so each is seeded with it. A feed mapped after that has none, and
    its first sync takes the first-sync window."""
    raw = get_setting_raw(db, "simplefin_feed_last_sync")
    if raw:
        return simplefin_service.parse_feed_last_sync(raw)
    shared = get_setting_raw(db, "simplefin_last_sync") or ""
    return {sf_id: shared for sf_id in account_map} if shared else {}


def _save_feed_dates(db: Session, dates: dict[str, str], keep) -> dict[str, str]:
    keep = set(keep)
    kept = {sf_id: when for sf_id, when in dates.items() if sf_id in keep}
    set_setting(db, "simplefin_feed_last_sync", json.dumps(kept, sort_keys=True))
    return kept


def _present_cache(cache: list) -> list[dict]:
    """The accounts the bridge reported last, as account_summaries gave them."""
    return [
        a for a in cache if isinstance(a, dict) and a.get("id") and not a.get("gone")
    ]


def _status_body(db: Session) -> dict:
    account_map = _account_map(db)
    accounts = simplefin_service.merge_accounts_cache(
        _cache(db), _present_cache(_cache(db)), account_map.keys()
    )
    for a in accounts:
        # what the account table says of each row (#905)
        a["state"] = (
            "gone" if a["gone"] else "mapped" if a["id"] in account_map else "new"
        )
    return {
        "connected": bool(get_setting_raw(db, "simplefin_access_url") or ""),
        "last_sync": get_setting_raw(db, "simplefin_last_sync") or "",
        "feed_last_sync": _feed_dates(db, account_map),
        "account_map": account_map,
        "reversed": _reversed(db),
        "accounts": accounts,
    }


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
    return _status_body(db)


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
    set_setting(db, "simplefin_feed_last_sync", "{}")
    db.commit()
    return {"connected": True, "accounts": accounts}


@router.post("/update-token")
def update_token(
    payload: ClaimRequest, request: Request, db: Session = Depends(get_db)
):
    """Claim a fresh setup token for a feed already connected (#905): the
    new access URL replaces the stored one and nothing else is cleared. Every
    mapping, Reverse signs choice and feed's last sync stays; a mapped
    account the new connection does not report is named in "missing" and
    shown as gone, to be moved to its new account or unmapped."""
    require_admin(request)  # replaces the stored credential; outbound POST
    _connected(db)
    try:
        access_url = simplefin_service.claim_access_url(payload.setup_token)
        data = simplefin_service.fetch_accounts(access_url, balances_only=True)
    except SimpleFINError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    account_map = _account_map(db)
    dates = _feed_dates(db, account_map)
    old_cache = _cache(db)
    summaries = simplefin_service.account_summaries(data)
    present = {a["id"] for a in summaries}
    names = {
        str(a.get("id")): str(a.get("name") or a.get("id"))
        for a in old_cache
        if isinstance(a, dict)
    }
    missing = [
        {"id": sf_id, "name": names.get(sf_id, sf_id)}
        for sf_id in account_map
        if sf_id not in present
    ]
    keep = present | set(account_map)
    set_setting(db, "simplefin_access_url", access_url)
    set_setting(
        db,
        "simplefin_accounts_cache",
        json.dumps(
            simplefin_service.merge_accounts_cache(
                old_cache, summaries, account_map.keys()
            )
        ),
    )
    set_setting(
        db,
        "simplefin_reversed",
        json.dumps(sorted(i for i in _reversed(db) if i in keep)),
    )
    _save_feed_dates(db, dates, keep)
    db.commit()
    body = _status_body(db)
    body["missing"] = missing
    return body


@router.post("/refresh")
def refresh(request: Request, db: Session = Depends(get_db)):
    """Read the bridge's account list again, balances only (#905): accounts
    added at the SimpleFIN provider since appear unmapped, and a mapped one
    it no longer reports is kept and shown as gone. Needs no mapping."""
    require_admin(request)  # the account list is the connection's wiring
    access_url = _connected(db)
    try:
        data = simplefin_service.fetch_accounts(access_url, balances_only=True)
    except SimpleFINError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    old_cache = _cache(db)
    before = {str(a.get("id")) for a in _present_cache(old_cache)}
    summaries = simplefin_service.account_summaries(data)
    set_setting(
        db,
        "simplefin_accounts_cache",
        json.dumps(
            simplefin_service.merge_accounts_cache(
                old_cache, summaries, _account_map(db).keys()
            )
        ),
    )
    db.commit()
    body = _status_body(db)
    body["added"] = [a["id"] for a in summaries if a["id"] not in before]
    body["gone"] = [a["id"] for a in body["accounts"] if a["gone"]]
    body["warnings"] = [str(e) for e in data.get("errors", []) or []]
    return body


@router.post("/move")
def move_mapping(payload: MoveRequest, request: Request, db: Session = Depends(get_db)):
    """Move one mapping to another SimpleFIN account (#905) — a reissued
    card that came through under a new id: the old id is unmapped and the
    new one imports into the same SlowBooks account, with the old one's
    Reverse signs choice. The new id has synced nothing, so its first sync
    takes the first-sync window."""
    require_admin(request)  # the feed's wiring is a connection setting
    _connected(db)
    from_id, to_id = payload.from_id, payload.to_id
    account_map = _account_map(db)
    if from_id not in account_map:
        raise HTTPException(
            status_code=400, detail="That SimpleFIN account is not mapped"
        )
    if to_id == from_id:
        raise HTTPException(
            status_code=400, detail="Choose a different SimpleFIN account"
        )
    present = {str(a.get("id")) for a in _present_cache(_cache(db))}
    if to_id not in present:
        raise HTTPException(
            status_code=400,
            detail="That SimpleFIN account is not in the account list "
            "(use Refresh accounts first)",
        )
    if to_id in account_map:
        raise HTTPException(
            status_code=400,
            detail="That SimpleFIN account already imports into an account "
            "(set it to not imported first)",
        )
    dates = _feed_dates(db, account_map)
    moved = {k: v for k, v in account_map.items() if k != from_id}
    moved[to_id] = account_map[from_id]
    _refuse_a_second_feed(db, moved)
    reversed_ids = set(_reversed(db))
    if from_id in reversed_ids:
        reversed_ids.add(to_id)
    else:
        reversed_ids.discard(to_id)
    reversed_ids.discard(from_id)
    set_setting(db, "simplefin_account_map", json.dumps(moved))
    set_setting(db, "simplefin_reversed", json.dumps(sorted(reversed_ids)))
    _save_feed_dates(db, dates, moved.keys())
    set_setting(
        db,
        "simplefin_accounts_cache",
        json.dumps(
            simplefin_service.merge_accounts_cache(
                _cache(db), _present_cache(_cache(db)), moved.keys()
            )
        ),
    )
    db.commit()
    return _status_body(db)


@router.post("/map")
def save_mapping(payload: MapRequest, request: Request, db: Session = Depends(get_db)):
    """Store which bridge account feeds which SlowBooks bank account."""
    require_admin(request)  # the feed's wiring is a connection setting
    _connected(db)
    # seeded from the map as it was, before this save changes it (#905)
    dates = _feed_dates(db, _account_map(db))
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
    # a feed unmapped forgets its last sync: mapped again, it starts afresh
    # (anything it brought in before is a duplicate and skipped)
    _save_feed_dates(db, dates, clean.keys())
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
    today = date.today()
    dates = _feed_dates(db, account_map)
    since_by_id: Optional[dict[str, date]] = None
    if history:
        start = simplefin_service.months_before(today, history)
    else:
        # each feed from its own last sync, or the first-sync window for a
        # feed that has never synced (#905); one request reaches back as far
        # as the feed that needs the most
        since_by_id = {
            sf_id: simplefin_service.feed_start(today, dates.get(sf_id, ""))
            for sf_id in account_map
        }
        start = min(since_by_id.values())
        # only a feed whose window starts later than the request's is
        # trimmed: what the bridge returns for the request is otherwise
        # taken as it comes, as before
        since_by_id = {k: v for k, v in since_by_id.items() if v > start}

    try:
        if history:
            # through today: the protocol's end-date is exclusive
            data = simplefin_service.fetch_history(
                access_url, start, today + timedelta(days=1)
            )
        else:
            data = simplefin_service.fetch_accounts(access_url, start_date=start)
    except SimpleFINError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    reversed_ids = set(_reversed(db))
    result = simplefin_service.sync_accounts(
        db, data, account_map, reversed_ids, since_by_id
    )
    synced = result.pop("synced", [])
    result["since"] = start.isoformat()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    set_setting(
        db,
        "simplefin_accounts_cache",
        json.dumps(
            simplefin_service.merge_accounts_cache(
                _cache(db),
                simplefin_service.account_summaries(data),
                account_map.keys(),
            )
        ),
    )
    set_setting(db, "simplefin_last_sync", now)
    # a feed the bridge did not answer for keeps its date, so the next sync
    # reaches back to it
    dates.update({sf_id: now for sf_id in synced})
    _save_feed_dates(db, dates, account_map.keys())
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
    set_setting(db, "simplefin_feed_last_sync", "{}")
    db.commit()
    return {"connected": False}
