# ============================================================================
# Recent sends — a read-only view of the email_log table (#447).
#
# Every send writes an email_log row (app/services/email_service.py). Who
# reads what (#781, and the rule in app/services/email_errors.py): `failure`
# is our own sentence for a failed send, the same for every reader, with
# nothing of the mail server's in it. An administrator signed in also gets
# the server's reply, as it was received, in `error_message`. Every other
# role that may read the books (bookkeeper, read-only, accountant) and
# every API token gets the same rows with the sentence in `error_message`
# in place of the reply. Recipients, copies, subjects, times, sent/failed
# and the document a row was for (entity_type, entity_id) are what an
# integration needs to confirm a send went out, and every one of those
# roles can already read the customer and invoice they came from. The
# Recent sends section in Settings stays an administrator's; this is the
# API.
# ============================================================================

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.email_log import EmailLog
from app.routes._roles import reads_mail_server_words
from app.routes.api_tokens import _utc_iso
from app.services.email_errors import FailureReader

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
    so a stale value in the URL still returns the list. Only an administrator
    signed in gets the mail server's reply on a row (see the header)."""
    admin = reads_mail_server_words(request)
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
    # the one reader of the stored text: the server's reply is an
    # administrator's only
    reader = FailureReader(db)
    return {
        "total": total,
        "rows": [_row(r, admin, reader) for r in rows],
    }


def _row(r: EmailLog, admin: bool, reader: FailureReader) -> dict:
    failure = reader.read(r, admin)
    return {
        "id": r.id,
        "sent_at": _utc_iso(r.sent_at),
        "recipient": r.recipient,
        "cc": r.cc,
        "bcc": r.bcc,
        "subject": r.subject,
        "entity_type": r.entity_type,
        # the invoice, estimate, statement's customer, ... it was sent for,
        # so a client can tie the row to its document (#781)
        "entity_id": r.entity_id,
        "status": r.status,
        # the mail server's reply, as it was received, is an
        # administrator's; anyone else reads the sentence in its place,
        # ending with who can read the reason when it does not say why
        "error_message": failure.reply if admin else failure.reason() or None,
        # the sentence, the same for every reader, with nothing of the
        # server's in it
        "failure": failure.sentence,
        # a send that went out though the server refused a copy: who was
        # refused, in our own words (null otherwise)
        "note": failure.note,
    }
