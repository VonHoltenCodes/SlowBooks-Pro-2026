# ============================================================================
# Document audit hashing — canonical SHA-256 over a document payload, plus a
# helper that writes the audit row.
# ============================================================================

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.document_audit import DocumentAudit


def _canonical(value: Any) -> Any:
    """Reshape a payload so json.dumps gives a stable byte string. Decimals
    serialize as strings (no float rounding); dates/datetimes as ISO-8601.

    Note: `datetime` is a subclass of `date`, so the `date` branch catches
    both. Ordering matters — Decimal first because Decimal isn't a Number
    subclass json.dumps recognizes.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def compute_doc_hash(payload: dict) -> str:
    """SHA-256 over a sorted, canonical JSON serialization of `payload`.

    The hash is content-only — no timestamps, no audit IDs — so re-rendering
    the same data on a different day yields the same hash. The audit row is
    where time-of-issue lives.
    """
    canonical = json.dumps(_canonical(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_doc_audit(
    db: Session, doc_type: str, doc_key: str, content_hash: str
) -> DocumentAudit:
    """Insert one DocumentAudit row and return it. The caller passes the
    same string into the PDF footer so the row and the printed page are
    bound together by id + hash."""
    audit = DocumentAudit(
        doc_type=doc_type,
        doc_key=doc_key,
        content_hash=content_hash,
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)
    return audit


def audit_footer_context(audit: DocumentAudit) -> dict:
    """Shape the audit dict the way the PDF templates expect."""
    return {
        "id": audit.id,
        "hash": audit.content_hash,
        "hash_short": audit.content_hash[:16],
        "timestamp": (
            audit.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            if audit.created_at
            else ""
        ),
    }
