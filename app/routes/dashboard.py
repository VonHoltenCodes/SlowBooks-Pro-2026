# ============================================================================
# Dashboard — the customizable overview.
#
#   GET /api/dashboard/widgets          catalog + the default layout
#   GET /api/dashboard/data?ids=a,b,c   data for those cards, keyed by id
#
# Which cards a user sees and in what order is a per-user preference
# (/api/preferences/dashboard); the builders live in
# app/services/dashboard_widgets.py.
# ============================================================================

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.dashboard_widgets import DEFAULT_LAYOUT, WIDGETS, build, catalog

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/widgets")
def list_widgets():
    return {"widgets": catalog(), "default_order": list(DEFAULT_LAYOUT)}


@router.get("/data")
def widget_data(ids: str = Query(default=""), db: Session = Depends(get_db)):
    """Data for the requested widget ids (comma-separated). Unknown ids are
    ignored; no ids means the default layout."""
    wanted = [w.strip() for w in ids.split(",") if w.strip()] or list(DEFAULT_LAYOUT)
    wanted = [w for w in wanted if w in WIDGETS]
    return build(db, wanted)
