# ============================================================================
# Connect to AI assistant (#1087): Settings, Connections, AI assistants.
#
#   GET  /api/switchboard/status      any signed-in person: the state, as
#                                     that person may see it
#   POST /api/switchboard/connect     an administrator: link, token, line
#   POST /api/switchboard/add         an administrator of this company and of
#                                     every company on the connection: this
#                                     company on it, with a token of its own
#   POST /api/switchboard/take-off    an administrator of that company: its
#                                     token off, the others stay connected
#   POST /api/switchboard/code        an administrator of every company on the
#                                     connection: a one-time sign-in code
#   POST /api/switchboard/disconnect  an administrator of every company on it
#                                     that can be opened now: line, link and
#                                     every token gone
#   POST /api/switchboard/dismiss     an administrator: hide a relay name the
#                                     relay has not deleted yet (NEW-269; the
#                                     quiet retry goes on)
#
# The gate (app.main) refuses every API token here, whatever its role: the
# assistant that holds the connection's token must not mint itself codes or
# end its own connection. The writes are also an administrator's in
# _ADMIN_WRITE_PREFIXES; each route checks again. Answers never carry the
# device credential or the token; the code is answered once, not stored.
#
# Every route answers through _answer: a refusal or a failure is the
# service's own sentence, and a fault of ours is a sentence too, never an
# HTTP 500 or exception text (the service's rule, point 2).
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.users import ROLE_ADMIN
from app.schemas.common import StrictModel
from app.services import switchboard_link as link

router = APIRouter(prefix="/api/switchboard", tags=["switchboard"])


def _is_admin(request: Request) -> bool:
    return (request.session.get("role") or "admin") == ROLE_ADMIN


def _require_admin(request: Request) -> None:
    if not _is_admin(request):
        raise HTTPException(
            status_code=403,
            detail="Connecting an AI assistant is an administrator's.",
        )


def _answer(fn, *args):
    try:
        return fn(*args)
    except link.SwitchboardError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.sentence) from None
    except Exception as exc:
        # where it failed goes to the log, without the exception's own text
        # (it can quote a value); the person gets a sentence
        link._log_fault("the AI assistant connection could not be worked on", exc)
        raise HTTPException(status_code=503, detail=link.UNFINISHED) from None


@router.get("/status")
def switchboard_status(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    out = _answer(link.status, request.session)
    out["can_manage"] = _is_admin(request)
    return out


@router.post("/connect")
def switchboard_connect(
    request: Request, response: Response, db: Session = Depends(get_db)
):
    _require_admin(request)
    response.headers["Cache-Control"] = "no-store"
    out = _answer(
        link.connect,
        db,
        request.session.get("username") or "operator",
        request.session,
    )
    out["can_manage"] = True
    return out


@router.post("/add")
def switchboard_add(
    request: Request, response: Response, db: Session = Depends(get_db)
):
    _require_admin(request)
    response.headers["Cache-Control"] = "no-store"
    out = _answer(
        link.add_here,
        db,
        request.session.get("username") or "operator",
        request.session,
    )
    out["can_manage"] = True
    return out


class TakeOff(StrictModel):
    file: str = Field(..., max_length=255)


@router.post("/take-off")
def switchboard_take_off(
    request: Request,
    response: Response,
    data: TakeOff,
    db: Session = Depends(get_db),
):
    _require_admin(request)
    response.headers["Cache-Control"] = "no-store"
    out = _answer(link.take_off, db, data.file, request.session)
    out["can_manage"] = True
    return out


@router.post("/code")
def switchboard_code(request: Request, response: Response):
    _require_admin(request)
    response.headers["Cache-Control"] = "no-store"
    return _answer(link.new_code, request.session)


@router.post("/disconnect")
def switchboard_disconnect(request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    return _answer(link.disconnect, db, request.session)


class DismissPending(StrictModel):
    slug: str = Field(..., min_length=1, max_length=64)


@router.post("/dismiss")
def switchboard_dismiss(request: Request, data: DismissPending):
    _require_admin(request)
    return _answer(link.dismiss_pending, data.slug)
