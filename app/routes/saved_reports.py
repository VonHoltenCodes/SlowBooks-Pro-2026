# ============================================================================
# Phase 11: Saved Reports.
#
# Lets the user save (report_type + parameters) tuples so they can rerun
# frequently-used reports without re-entering dates/accounts/etc. Doesn't
# cache the results — the SPA calls /api/reports/* with the stored params.
# ============================================================================

import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Cap serialized parameters at 64 KB. A saved report is just dates + a
# handful of IDs — 64K is already a thousand times what it needs.
# Prevents bloat/DoS via giant JSON payloads.
_MAX_PARAMS_BYTES = 64 * 1024

from app.database import get_db
from app.models.saved_reports import SavedReport
from app.routes._helpers import get_or_404

router = APIRouter(prefix="/api/saved-reports", tags=["saved-reports"])


_ALLOWED_TYPES = {
    "profit_loss",
    "balance_sheet",
    "ar_aging",
    "ap_aging",
    "sales_tax",
    "general_ledger",
    "income_by_customer",
    "account_transactions",
    "cash_flow",
    "analytics_dashboard",
}


class SavedReportCreate(BaseModel):
    name: str
    report_type: str
    parameters: dict[str, Any] = {}


class SavedReportUpdate(BaseModel):
    name: Optional[str] = None
    parameters: Optional[dict[str, Any]] = None


class SavedReportResponse(BaseModel):
    id: int
    name: str
    report_type: str
    parameters: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[SavedReportResponse])
def list_saved_reports(
    report_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(SavedReport)
    if report_type:
        q = q.filter(SavedReport.report_type == report_type)
    return q.order_by(SavedReport.name).all()


def _validate_params_size(params: dict) -> None:
    """Reject oversized parameter blobs before they bloat the DB."""
    try:
        size = len(json.dumps(params or {}))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="parameters must be JSON-serializable")
    if size > _MAX_PARAMS_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"parameters exceed {_MAX_PARAMS_BYTES} bytes ({size} provided)",
        )


@router.post("", response_model=SavedReportResponse, status_code=201)
def create_saved_report(data: SavedReportCreate, db: Session = Depends(get_db)):
    if data.report_type not in _ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown report_type '{data.report_type}'. Allowed: {sorted(_ALLOWED_TYPES)}",
        )
    if not data.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    _validate_params_size(data.parameters or {})

    report = SavedReport(
        name=data.name.strip(),
        report_type=data.report_type,
        parameters=data.parameters or {},
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


@router.get("/{report_id}", response_model=SavedReportResponse)
def get_saved_report(report_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, SavedReport, report_id)


@router.put("/{report_id}", response_model=SavedReportResponse)
def update_saved_report(
    report_id: int,
    data: SavedReportUpdate,
    db: Session = Depends(get_db),
):
    report = get_or_404(db, SavedReport, report_id)
    if data.name is not None:
        report.name = data.name.strip()
    if data.parameters is not None:
        _validate_params_size(data.parameters)
        report.parameters = data.parameters
    db.commit()
    db.refresh(report)
    return report


@router.delete("/{report_id}")
def delete_saved_report(report_id: int, db: Session = Depends(get_db)):
    report = get_or_404(db, SavedReport, report_id)
    db.delete(report)
    db.commit()
    return {"message": "deleted"}
