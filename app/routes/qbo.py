# ============================================================================
# QBO API Routes — QuickBooks Online OAuth + Import/Export endpoints
#
# OAuth flow:
#   GET  /api/qbo/auth-url   -> returns Intuit authorization URL
#   GET  /api/qbo/callback   -> handles OAuth redirect, stores tokens
#   POST /api/qbo/disconnect -> clears tokens
#   GET  /api/qbo/status     -> connection status (never returns raw tokens)
#
# Data sync:
#   POST /api/qbo/import           -> import all entity types
#   POST /api/qbo/import/{entity}  -> import single entity type
#   POST /api/qbo/export           -> export all entity types
#   POST /api/qbo/export/{entity}  -> export single entity type
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.qbo import QBOImportResult, QBOExportResult, QBOConnectionStatus
from app.services import qbo_service
from app.services import qbo_import
from app.services import qbo_export

router = APIRouter(prefix="/api/qbo", tags=["qbo"])


# ============================================================================
# OAuth endpoints
# ============================================================================


@router.get("/auth-url")
def get_auth_url(db: Session = Depends(get_db)):
    """Generate the Intuit OAuth authorization URL."""
    try:
        url = qbo_service.get_auth_url(db)
        return {"url": url}
    except Exception as e:
        raise HTTPException(
            400,
            f"Failed to generate auth URL: {str(e)}. "
            "Check that Client ID and Client Secret are configured in Settings.",
        )


@router.get("/callback")
def oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    realmId: str = Query(...),
    db: Session = Depends(get_db),
):
    """Handle OAuth redirect from Intuit. Exchanges code for tokens."""
    try:
        qbo_service.handle_callback(db, code, state, realmId)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"OAuth callback failed: {str(e)}")

    # Redirect to QBO page in SPA
    return RedirectResponse(url="/#/qbo")


@router.post("/disconnect")
def disconnect(db: Session = Depends(get_db)):
    """Clear stored QBO tokens and disconnect."""
    qbo_service.disconnect(db)
    return {"status": "disconnected"}


@router.get("/status", response_model=QBOConnectionStatus)
def get_status(db: Session = Depends(get_db)):
    """Get QBO connection status. Never returns raw tokens."""
    connected = qbo_service.is_connected(db)
    company_name = ""
    realm_id = ""

    if connected:
        s = qbo_service.get_all_qbo_settings(db)
        realm_id = s.get("qbo_realm_id", "")
        try:
            company_name = qbo_service.get_company_name(db)
        except Exception:
            company_name = "(unable to fetch)"

    return QBOConnectionStatus(
        connected=connected,
        company_name=company_name,
        realm_id=realm_id,
    )


# ============================================================================
# Import endpoints
# ============================================================================

_IMPORT_ENTITY_MAP = {
    "accounts": qbo_import.import_accounts,
    "customers": qbo_import.import_customers,
    "vendors": qbo_import.import_vendors,
    "items": qbo_import.import_items,
    "invoices": qbo_import.import_invoices,
    "payments": qbo_import.import_payments,
}


@router.post("/import", response_model=QBOImportResult)
def import_all(db: Session = Depends(get_db)):
    """Import all entity types from QBO in dependency order."""
    if not qbo_service.is_connected(db):
        raise HTTPException(400, "Not connected to QuickBooks Online")
    try:
        result = qbo_import.import_all(db)
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Import failed: {str(e)}")
    return result


@router.post("/import/{entity}")
def import_entity(entity: str, db: Session = Depends(get_db)):
    """Import a single entity type from QBO."""
    if not qbo_service.is_connected(db):
        raise HTTPException(400, "Not connected to QuickBooks Online")

    if entity not in _IMPORT_ENTITY_MAP:
        raise HTTPException(
            400,
            f"Unknown entity type: {entity}. "
            f"Valid types: {', '.join(_IMPORT_ENTITY_MAP.keys())}",
        )

    try:
        result = _IMPORT_ENTITY_MAP[entity](db)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Import of {entity} failed: {str(e)}")

    return result


# ============================================================================
# Export endpoints
# ============================================================================

_EXPORT_ENTITY_MAP = {
    "accounts": qbo_export.export_accounts,
    "customers": qbo_export.export_customers,
    "vendors": qbo_export.export_vendors,
    "items": qbo_export.export_items,
    "invoices": qbo_export.export_invoices,
    "payments": qbo_export.export_payments,
}


@router.post("/export", response_model=QBOExportResult)
def export_all(db: Session = Depends(get_db)):
    """Export all entity types to QBO in dependency order."""
    if not qbo_service.is_connected(db):
        raise HTTPException(400, "Not connected to QuickBooks Online")
    try:
        result = qbo_export.export_all(db)
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Export failed: {str(e)}")
    return result


@router.post("/export/{entity}")
def export_entity(entity: str, db: Session = Depends(get_db)):
    """Export a single entity type to QBO."""
    if not qbo_service.is_connected(db):
        raise HTTPException(400, "Not connected to QuickBooks Online")

    if entity not in _EXPORT_ENTITY_MAP:
        raise HTTPException(
            400,
            f"Unknown entity type: {entity}. "
            f"Valid types: {', '.join(_EXPORT_ENTITY_MAP.keys())}",
        )

    try:
        result = _EXPORT_ENTITY_MAP[entity](db)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Export of {entity} failed: {str(e)}")

    return result
