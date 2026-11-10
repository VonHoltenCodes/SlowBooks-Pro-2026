# ============================================================================
# API token management — admin sessions only. The middleware additionally
# blocks token principals from this whole prefix (no self-escalation), so
# only a human admin in a browser can mint or revoke tokens.
#
# The full secret appears exactly once, in the create response. No DELETE:
# tokens are revoked, for good (#434), keeping their label meaningful in
# the audit trail.
# ============================================================================

from typing import Optional

from datetime import timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from app.schemas.common import StrictModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.api_tokens import ApiToken
from app.models.users import ROLE_ADMIN, ROLE_BOOKKEEPER, VALID_ROLES
from app.services.api_token_service import generate_token, hash_token


def _utc_iso(stamp):
    """A stored timestamp as ISO text with its zone: SQLite's are naive UTC,
    and a browser reads a naive stamp as local, which put "Last login" and
    "Last used" on tomorrow's date from 7 pm Central (2.23.2 gate, NEW-110).
    The backups list sends "+00:00" and is right; so do these now."""
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.isoformat()


router = APIRouter(prefix="/api/tokens", tags=["api_tokens"])


def _require_admin(request: Request) -> None:
    if (request.session.get("role") or "admin") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")


def _out(t: ApiToken) -> dict:
    return {
        "id": t.id,
        "label": t.label,
        "token_hint": t.token_hint,
        "role": t.role,
        "is_active": t.is_active,
        "allow_backups": bool(t.allow_backups),
        # what the gate honours: on any role but bookkeeper a stored grant
        # gives nothing, so the list doesn't say it does (#755)
        "allow_payroll": t.role == ROLE_BOOKKEEPER and bool(t.allow_payroll),
        "allow_imports": t.role == ROLE_BOOKKEEPER and bool(t.allow_imports),
        "created_by": t.created_by,
        "created_at": _utc_iso(t.created_at),
        "last_used_at": _utc_iso(t.last_used_at),
        # An administrator's token from before 2.29.0 is refused on every
        # request (#659); the list says so, so it gets revoked and replaced.
        "refused": bool(t.is_active and t.role == ROLE_ADMIN),
    }


class TokenCreate(StrictModel):
    label: str = Field(..., min_length=1, max_length=100)
    role: str = Field(...)
    # Make, list and download this company's backups on top of the role
    # (#434): an import tool's safety net. Never restore.
    allow_backups: bool = False
    # Run payroll / run the migration imports on top of a bookkeeper's
    # role (#755). Bookkeeper tokens only.
    allow_payroll: bool = False
    allow_imports: bool = False


class TokenUpdate(StrictModel):
    is_active: Optional[bool] = None
    label: Optional[str] = Field(None, min_length=1, max_length=100)
    allow_backups: Optional[bool] = None
    allow_payroll: Optional[bool] = None
    allow_imports: Optional[bool] = None


def _check_grants(role: str, allow_payroll: bool, allow_imports: bool) -> None:
    """Payroll and imports are writes on top of the role (#755): a token
    that may not write the books may not be given them."""
    if (allow_payroll or allow_imports) and role != ROLE_BOOKKEEPER:
        raise HTTPException(
            status_code=400,
            detail=(
                "Only a bookkeeper token can run payroll or imports. Make a "
                "bookkeeper token for the program that needs them."
            ),
        )


@router.get("")
def list_tokens(request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    return [_out(t) for t in db.query(ApiToken).order_by(ApiToken.id).all()]


@router.post("", status_code=201)
def create_token(payload: TokenCreate, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    label = payload.label.strip()
    if payload.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    if payload.role == ROLE_ADMIN:
        # A token is a program, not a person. Roles are its only scope
        # (#434), and the one that reaches settings, closing dates and
        # company files stays with a signed-in administrator. Tokens
        # made as admin before 2.29.0 are refused at the door (#659) and
        # flagged under Settings, API Tokens until revoked.
        raise HTTPException(
            status_code=400,
            detail=(
                "An API token cannot be an administrator. Choose bookkeeper, "
                "accountant or read-only; settings, users and tokens stay with "
                "a signed-in administrator."
            ),
        )
    _check_grants(payload.role, payload.allow_payroll, payload.allow_imports)
    if db.query(ApiToken).filter(ApiToken.label == label).first():
        raise HTTPException(status_code=409, detail="Label already exists")
    secret = generate_token()
    row = ApiToken(
        label=label,
        token_hash=hash_token(secret),
        token_hint=secret[:10],
        role=payload.role,
        is_active=True,
        allow_backups=payload.allow_backups,
        allow_payroll=payload.allow_payroll,
        allow_imports=payload.allow_imports,
        created_by=request.session.get("username") or "operator",
    )
    db.add(row)
    db.commit()
    out = _out(row)
    # The one and only time the secret leaves the server.
    out["token"] = secret
    return out


@router.put("/{token_id}")
def update_token(
    token_id: int, payload: TokenUpdate, request: Request, db: Session = Depends(get_db)
):
    _require_admin(request)
    row = db.query(ApiToken).filter(ApiToken.id == token_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Token not found")
    if payload.is_active and not row.is_active:
        # Revoking is final (#434): the secret may sit in a config file, a
        # log or a leaked repo, and switching it back on would revive
        # whatever holds it. A program that still needs in gets a new token.
        raise HTTPException(
            status_code=409,
            detail=(
                f'The API token "{row.label}" was revoked, and a revoked token '
                "stays revoked. Create a new token for the program that needs one."
            ),
        )
    if payload.label is not None:
        label = payload.label.strip()
        clash = (
            db.query(ApiToken)
            .filter(ApiToken.label == label, ApiToken.id != token_id)
            .first()
        )
        if clash:
            raise HTTPException(status_code=409, detail="Label already exists")
        row.label = label
    if payload.is_active is False:
        row.is_active = False
    if payload.allow_backups is not None:
        row.allow_backups = payload.allow_backups
    _check_grants(row.role, bool(payload.allow_payroll), bool(payload.allow_imports))
    if payload.allow_payroll is not None:
        row.allow_payroll = payload.allow_payroll
    if payload.allow_imports is not None:
        row.allow_imports = payload.allow_imports
    db.commit()
    return _out(row)
