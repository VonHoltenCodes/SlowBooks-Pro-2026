# ============================================================================
# Per-user preferences: GET/PUT /api/preferences/{key}.
#
# The user is the session's user_id when the company has users; the
# single-password operator session (no user_id) shares one row per key.
# Values are opaque JSON objects the page owns; the server only checks
# they are objects and not absurdly large.
# ============================================================================

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.preferences import UserPreference

router = APIRouter(prefix="/api/preferences", tags=["preferences"])

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
_MAX_BYTES = 16_000


def _user_id(request: Request):
    try:
        return request.session.get("user_id")
    except AssertionError:  # no SessionMiddleware (tests without the app)
        return None


def _check_key(key: str):
    if not _KEY_RE.match(key):
        raise HTTPException(
            status_code=422, detail="Preference key must be a short snake_case name"
        )


@router.get("/{key}")
def get_preference(key: str, request: Request, db: Session = Depends(get_db)):
    _check_key(key)
    row = (
        db.query(UserPreference)
        .filter(UserPreference.user_id == _user_id(request), UserPreference.key == key)
        .first()
    )
    if not row:
        return {"key": key, "value": None}
    try:
        value = json.loads(row.value)
    except json.JSONDecodeError:
        value = None
    return {"key": key, "value": value}


@router.put("/{key}")
def put_preference(
    key: str, payload: dict, request: Request, db: Session = Depends(get_db)
):
    _check_key(key)
    value = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail='Send {"value": {...}}')
    raw = json.dumps(value)
    if len(raw) > _MAX_BYTES:
        raise HTTPException(status_code=422, detail="Preference is too large")
    uid = _user_id(request)
    row = (
        db.query(UserPreference)
        .filter(UserPreference.user_id == uid, UserPreference.key == key)
        .first()
    )
    if row:
        row.value = raw
    else:
        db.add(UserPreference(user_id=uid, key=key, value=raw))
    db.commit()
    return {"key": key, "value": value}


@router.delete("/{key}")
def delete_preference(key: str, request: Request, db: Session = Depends(get_db)):
    """Back to the default for this user."""
    _check_key(key)
    db.query(UserPreference).filter(
        UserPreference.user_id == _user_id(request), UserPreference.key == key
    ).delete()
    db.commit()
    return {"key": key, "value": None}
