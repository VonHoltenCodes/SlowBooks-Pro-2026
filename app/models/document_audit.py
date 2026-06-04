# ============================================================================
# Document audit trail — tamper-evident hashes for regulated PDFs.
#
# Every time a tax form (or other high-importance document) is rendered to
# PDF we compute a SHA-256 over its canonical content and store the result
# here alongside a timestamp. The PDF footer prints the audit id + the
# first 16 hex chars of the hash so an auditor can:
#
#   1. Pull the matching `document_audits` row by id.
#   2. Recompute the hash by re-rendering the document with the same inputs.
#   3. Compare — mismatch means the displayed PDF doesn't match the data.
#
# This is intentionally just a hash + audit trail, not a full digital
# signature. The trust anchor is the database row; the PDF footer is the
# verifiable link back to it.
# ============================================================================

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentAudit(Base):
    __tablename__ = "document_audits"

    id = Column(Integer, primary_key=True)
    # Short tag identifying the document family: "w2", "w3", "940", "941",
    # "new_hire", etc. Kept compact so it's easy to filter on.
    doc_type = Column(String(20), nullable=False, index=True)
    # Free-form key uniquely identifying this issuance within its type — e.g.
    # "emp42-yr2026" for a W-2 or "yr2026-q3" for a 941. Indexed for lookup.
    doc_key = Column(String(80), nullable=False, index=True)
    # Full 64-char hex SHA-256 of the canonical content payload.
    content_hash = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, index=True)
