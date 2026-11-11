# ============================================================================
# Recent sends — a read-only view of the email_log table (#447).
#
# Every send writes an email_log row (app/services/email_service.py). Who
# reads what (#781): an administrator gets every row whole, the raw SMTP
# error included. Every other role that may read the books (bookkeeper,
# read-only, accountant; a session or an API token) gets the same rows with
# the raw error left out: `error_message` carries the bookkeeper's sentence
# for the failure (email_errors.failure_sentence) instead, because the raw
# text carries mail-server hostnames and banners. Recipients, copies,
# subjects, times and sent/failed are what an integration needs to confirm
# a send went out, and every one of those roles can already read the
# customer and invoice they came from. The Recent sends section in Settings
# stays an administrator's; this is the API.
# ============================================================================

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.email_log import EmailLog
from app.routes._roles import is_admin
from app.routes.api_tokens import _utc_iso
from app.services.email_errors import failure_sentence

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
    so a stale value in the URL still returns the list. A role other than
    administrator gets each row without the raw SMTP error (see the header)."""
    admin = is_admin(request)
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
        "rows": [_row(r, admin) for r in rows],
    }


def _row(r: EmailLog, admin: bool) -> dict:
    failure = failure_sentence(r.error_message or "") if r.status == "failed" else None
    return {
        "id": r.id,
        "sent_at": _utc_iso(r.sent_at),
        "recipient": r.recipient,
        "cc": r.cc,
        "bcc": r.bcc,
        "subject": r.subject,
        "entity_type": r.entity_type,
        "status": r.status,
        # the raw SMTP text is an administrator's (hostnames, banners);
        # anyone else reads the sentence in its place
        "error_message": r.error_message if admin else failure,
        # the one wording of a failure (email_errors), so the list and the
        # test-email message agree
        "failure": failure,
    }
