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
#   POST /api/qbo/import-runs      -> start a background import (HTTP 202)
#   GET  /api/qbo/import-runs/latest -> latest status and incremental events
#   POST /api/qbo/import           -> import all entity types
#   POST /api/qbo/import/{entity}  -> import single entity type
#   POST /api/qbo/export           -> export all entity types
#   POST /api/qbo/export/{entity}  -> export single entity type
# ============================================================================

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from intuitlib.exceptions import AuthClientError
from pydantic import BaseModel
from requests.exceptions import RequestException
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes._roles import require_admin
from app.schemas.qbo import (
    QBOImportResult,
    QBOExportResult,
    QBOConnectionStatus,
    QBOImportRunRequest,
)
from app.services import qbo_service
from app.services import qbo_import
from app.services import qbo_ledger_import
from app.services import qbo_export
from app.services import qbo_import_runs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/qbo", tags=["qbo"])


# ============================================================================
# OAuth endpoints
# ============================================================================


@router.get("/auth-url")
def get_auth_url(request: Request, db: Session = Depends(get_db)):
    """Generate the Intuit OAuth authorization URL."""
    require_admin(request)
    try:
        url = qbo_service.get_auth_url(db)
        return {"url": url}
    except Exception:
        logger.exception("QBO auth URL failed")
        raise HTTPException(
            400,
            "Failed to generate the auth URL — check that Client ID and Client "
            "Secret are configured in Settings; the server log has the details.",
        )


class ManualConnection(BaseModel):
    authorization_code: str
    realm_id: str


@router.post("/connect-manual")
def connect_manual(
    credentials: ManualConnection,
    request: Request,
    db: Session = Depends(get_db),
):
    """Redeem an authorization code supplied by an authenticated admin."""
    require_admin(request)
    code = credentials.authorization_code.strip()
    realm_id = credentials.realm_id.strip()
    if not code or not realm_id or len(code) > 4096 or len(realm_id) > 128:
        raise HTTPException(400, "Enter an Authorization Code and Realm ID")

    try:
        qbo_service.exchange_authorization_code(db, code, realm_id)
    except AuthClientError as exc:
        db.rollback()
        logger.warning(
            "Intuit rejected manual QBO connection (HTTP %s)", exc.status_code
        )
        if exc.status_code == 401:
            detail = "Intuit rejected the QBO Client ID or Secret. Check Settings and the selected environment."
        elif exc.status_code == 400:
            detail = "Intuit rejected the authorization code. Get a fresh code and check that the Redirect URI matches the one used to obtain it."
        else:
            detail = "Intuit could not complete the connection. Try again with a fresh authorization code."
        raise HTTPException(400, detail)
    except RequestException:
        db.rollback()
        logger.warning("Manual QBO connection could not reach Intuit")
        raise HTTPException(
            502, "Could not reach Intuit. Check the server connection and try again."
        )
    except Exception as exc:
        db.rollback()
        logger.warning("Manual QBO connection failed (%s)", type(exc).__name__)
        raise HTTPException(
            400,
            "QuickBooks connection failed. Check the code, Realm ID, and QBO Settings, then try again.",
        )

    return {"connected": True}


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
    except Exception:
        # Exception text can carry provider responses or paths; log it,
        # tell the browser only that it failed (CodeQL stack-trace exposure).
        logger.exception("QBO OAuth callback failed")
        raise HTTPException(
            500, "OAuth callback failed — the server log has the details"
        )

    # Redirect to QBO page in SPA
    return RedirectResponse(url="/#/qbo")


@router.post("/disconnect")
def disconnect(db: Session = Depends(get_db)):
    """Clear stored QBO tokens and disconnect."""
    qbo_service.disconnect(db)
    return {"status": "disconnected"}


@router.get("/status", response_model=QBOConnectionStatus)
def get_status(include_company_name: bool = True, db: Session = Depends(get_db)):
    """Get QBO connection status. Never returns raw tokens."""
    connected = qbo_service.is_connected(db)
    company_name = ""
    realm_id = ""

    if connected:
        s = qbo_service.get_all_qbo_settings(db)
        realm_id = s.get("qbo_realm_id", "")
        if include_company_name:
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
    "sales_receipts": qbo_import.import_sales_receipts,
    "journal_entries": qbo_import.import_journal_entries,
    "ledger": qbo_ledger_import.import_ledger,
}


@router.post("/import-runs", status_code=202)
def start_import_run(
    payload: QBOImportRunRequest, request: Request, db: Session = Depends(get_db)
):
    require_admin(request)
    if not qbo_service.is_connected(db):
        raise HTTPException(400, "Not connected to QuickBooks Online")
    entities = [
        entity
        for entity in qbo_import_runs.ENTITY_ORDER
        if payload.entities is None or entity in payload.entities
    ]
    actor = db.info.get("acting_username", "operator")
    return qbo_import_runs.start_run(
        db, entities, actor, import_all=payload.entities is None
    )


@router.get("/import-runs/latest")
def latest_import_run(
    after: int = Query(default=0, ge=0), db: Session = Depends(get_db)
):
    return qbo_import_runs.store_for(db).latest(after)


@router.post("/import", response_model=QBOImportResult)
def import_all(request: Request, db: Session = Depends(get_db)):
    """Import all entity types from QBO in dependency order."""
    require_admin(request)
    if not qbo_service.is_connected(db):
        raise HTTPException(400, "Not connected to QuickBooks Online")
    try:
        with qbo_import_runs.synchronous_run(
            db, qbo_import_runs.ENTITY_ORDER, db.info.get("acting_username", "operator")
        ):
            result = qbo_import.import_all(db)
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        logger.exception("QBO import failed")
        raise HTTPException(500, "Import failed — the server log has the details")
    return result


@router.post("/import/{entity}")
def import_entity(entity: str, request: Request, db: Session = Depends(get_db)):
    """Import a single entity type from QBO."""
    require_admin(request)
    if not qbo_service.is_connected(db):
        raise HTTPException(400, "Not connected to QuickBooks Online")

    if entity not in _IMPORT_ENTITY_MAP:
        raise HTTPException(
            400,
            f"Unknown entity type: {entity}. "
            f"Valid types: {', '.join(_IMPORT_ENTITY_MAP.keys())}",
        )

    try:
        with qbo_import_runs.synchronous_run(
            db, [entity], db.info.get("acting_username", "operator")
        ):
            result = _IMPORT_ENTITY_MAP[entity](db)
            db.commit()
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        logger.exception("QBO import of %s failed", entity)
        raise HTTPException(
            500, f"Import of {entity} failed — the server log has the details"
        )

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
    except Exception:
        db.rollback()
        logger.exception("QBO export failed")
        raise HTTPException(500, "Export failed — the server log has the details")
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
    except Exception:
        db.rollback()
        logger.exception("QBO export of %s failed", entity)
        raise HTTPException(
            500, f"Export of {entity} failed — the server log has the details"
        )

    return result
