# ============================================================================
# Document audit endpoints — admin-only lookup of audit rows for verification.
#
# These power the "is this PDF authentic?" workflow:
#   1. Operator opens a tax-form PDF, reads "Audit ID #42" from the footer.
#   2. Operator hits GET /api/document-audits/42 to pull the canonical row.
#   3. Compares the hash printed in the PDF to the hash returned by the API.
#      Match -> the PDF data matches what was generated. Mismatch -> tamper
#      or version drift.
# ============================================================================

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.document_audit import DocumentAudit

router = APIRouter(prefix="/api/document-audits", tags=["document-audit"])


class DocumentAuditResponse(BaseModel):
    id: int
    doc_type: str
    doc_key: str
    content_hash: str
    created_at: Optional[datetime]
    model_config = {"from_attributes": True}


@router.get("", response_model=list[DocumentAuditResponse])
def list_audits(
    doc_type: Optional[str] = Query(default=None),
    doc_key: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List recent document audit rows, newest first. Optional filters
    narrow by doc family (`w2`, `941`, ...) or by the type-specific key."""
    q = db.query(DocumentAudit)
    if doc_type:
        q = q.filter(DocumentAudit.doc_type == doc_type)
    if doc_key:
        q = q.filter(DocumentAudit.doc_key == doc_key)
    return q.order_by(DocumentAudit.id.desc()).limit(limit).all()


@router.get("/{audit_id}", response_model=DocumentAuditResponse)
def get_audit(audit_id: int, db: Session = Depends(get_db)):
    """Look up one audit row by its ID — the ID printed in the PDF footer."""
    row = db.query(DocumentAudit).filter(DocumentAudit.id == audit_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Audit row not found")
    return row


@router.get("/verify/{content_hash}", response_model=list[DocumentAuditResponse])
def verify_hash(content_hash: str, db: Session = Depends(get_db)):
    """Find every audit row matching this full SHA-256 hash. Used when an
    auditor has the PDF's hash but not the ID — e.g. they recomputed it
    independently and want to confirm it's known to the system."""
    if len(content_hash) != 64 or not all(
        c in "0123456789abcdef" for c in content_hash
    ):
        raise HTTPException(status_code=400, detail="content_hash must be 64 hex chars")
    return (
        db.query(DocumentAudit)
        .filter(DocumentAudit.content_hash == content_hash)
        .order_by(DocumentAudit.id.desc())
        .all()
    )
