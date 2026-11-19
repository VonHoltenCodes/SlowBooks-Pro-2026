# ============================================================================
# Connect Claude (#1087): Settings, Connections, Claude and agents.
#
#   GET  /api/switchboard/status      any signed-in person: the state
#   POST /api/switchboard/connect     an administrator: link, token, line
#   POST /api/switchboard/code        an administrator: a one-time sign-in code
#   POST /api/switchboard/disconnect  an administrator: line, link and token gone
#
# The gate (app.main) refuses every API token here, whatever its role: the
# assistant that holds the connection's token must not mint itself codes or
# end its own connection. The writes are also an administrator's in
# _ADMIN_WRITE_PREFIXES; each route checks again. Answers never carry the
# device credential or the token; the code is answered once, not stored.
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.users import ROLE_ADMIN
from app.services import switchboard_link as link

router = APIRouter(prefix="/api/switchboard", tags=["switchboard"])


def _is_admin(request: Request) -> bool:
    return (request.session.get("role") or "admin") == ROLE_ADMIN


def _require_admin(request: Request) -> None:
    if not _is_admin(request):
        raise HTTPException(
            status_code=403,
            detail="Connecting Claude is an administrator's.",
        )


def _answer(fn, *args):
    try:
        return fn(*args)
    except link.SwitchboardError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.sentence) from None


@router.get("/status")
def switchboard_status(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    out = link.status()
    out["can_manage"] = _is_admin(request)
    return out


@router.post("/connect")
def switchboard_connect(
    request: Request, response: Response, db: Session = Depends(get_db)
):
    _require_admin(request)
    response.headers["Cache-Control"] = "no-store"
    out = _answer(link.connect, db, request.session.get("username") or "operator")
    out["can_manage"] = True
    return out


@router.post("/code")
def switchboard_code(request: Request, response: Response):
    _require_admin(request)
    response.headers["Cache-Control"] = "no-store"
    return _answer(link.new_code)


@router.post("/disconnect")
def switchboard_disconnect(request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    return _answer(link.disconnect, db)
