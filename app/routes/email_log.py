# ============================================================================
# Recent email sends — a read-only view of the email_log table (#447).
#
# Every send writes an email_log row (app/services/email_service.py); until
# now nothing in the UI showed them, so three "see the email log" messages
# pointed at a table a user could not reach. Admin only: the rows carry
# recipient addresses and raw SMTP errors (hostnames, banners), the same
# reason the token and backup routes are an administrator's. require_admin
# refuses every other role, tokens included, the way api_tokens does.
# ============================================================================

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.email_log import EmailLog
from app.routes._roles import require_admin
from app.routes.api_tokens import _utc_iso

router = APIRouter(prefix="/api/email-log", tags=["email_log"])


@router.get("")
def list_email_log(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str = None,
    db: Session = Depends(get_db),
):
    """The newest sends first, paged and bounded. `status` filters to one
    value ("sent" / "failed"); anything else is ignored rather than erroring,
    so a stale value in the URL still returns the list."""
    require_admin(request)
    q = db.query(EmailLog)
    if status in ("sent", "failed"):
        q = q.filter(EmailLog.status == status)
    total = q.count()
    rows = (
        q.order_by(EmailLog.sent_at.desc(), EmailLog.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "rows": [
            {
                "id": r.id,
                "sent_at": _utc_iso(r.sent_at),
                "recipient": r.recipient,
                "subject": r.subject,
                "entity_type": r.entity_type,
                "status": r.status,
                "error_message": r.error_message,
            }
            for r in rows
        ],
    }
