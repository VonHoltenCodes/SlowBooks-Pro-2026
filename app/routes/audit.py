# ============================================================================
# Audit Log Routes — read-only viewer for audit trail
# ============================================================================

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.database import get_db
from app.models.audit import AuditLog
from app.schemas.audit import AuditLogResponse

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", response_model=list[AuditLogResponse])
def list_audit_logs(
    table_name: str = None,
    action: str = None,
    record_id: int = None,
    start_date: date = None,
    end_date: date = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(AuditLog)
    if table_name:
        q = q.filter(AuditLog.table_name == table_name)
    if action:
        q = q.filter(AuditLog.action == action)
    if record_id:
        q = q.filter(AuditLog.record_id == record_id)
    if start_date:
        q = q.filter(sqlfunc.date(AuditLog.timestamp) >= start_date)
    if end_date:
        q = q.filter(sqlfunc.date(AuditLog.timestamp) <= end_date)
    return q.order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit).all()


@router.get("/tables")
def list_audited_tables(db: Session = Depends(get_db)):
    """Get list of tables that have audit entries."""
    tables = (
        db.query(AuditLog.table_name).distinct().order_by(AuditLog.table_name).all()
    )
    return [t[0] for t in tables]
