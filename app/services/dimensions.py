"""A class or a job named on a posting must exist, and the class must not be
archived (2.31.4: the feed's add, the register entry and the transfer took
any integer and posted it)."""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db

from app.models.classes import TxnClass
from app.models.jobs import Job


def require_dimensions(
    db: Session,
    class_id: int | None = None,
    job_id: int | None = None,
    customer_id: int | None = None,
) -> None:
    if class_id is not None:
        cls = db.query(TxnClass).filter(TxnClass.id == class_id).first()
        if cls is None:
            raise HTTPException(
                status_code=404, detail=unknown_class_sentence(class_id)
            )
        if cls.is_archived:
            raise HTTPException(
                status_code=400,
                detail=f"Class {cls.name} is archived; restore it under Classes or choose another.",
            )
    if job_id is not None:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        if job.is_active is False:
            raise HTTPException(
                status_code=400,
                detail=f"The job {job.full_name} is inactive; pick an active one.",
            )
        if customer_id is not None and job.customer_id != customer_id:
            raise HTTPException(
                status_code=400,
                detail=f"The job {job.full_name} belongs to another customer.",
            )


# ---- An unknown class id on any write (W-199) -------------------------------
# A class id that is no class of this company was taken on an invoice's
# header and lines, a bill's line and more, and posted under an "Unknown"
# class on P&L by Class. Every request body whose schema declares a class
# (`class_id`, or a `…_class_id` such as an allocation's source) is checked
# here, once, before the route runs: each such id in it must be a class of
# this company. That a class is archived is left to each path, as before:
# the feed and the transfers refuse a new archived pick (require_dimensions
# above) and a document keeps the archived class it already has.

_WRITES = {"POST", "PUT", "PATCH"}
_DECLARES: dict = {}


def _is_class_key(name: str) -> bool:
    return name == "class_id" or name.endswith("_class_id")


def _declares_a_class(annotation, seen=None) -> bool:
    """True when a pydantic model (or a list or optional of one) declares a
    class field, at any depth."""
    import typing

    from pydantic import BaseModel

    seen = seen if seen is not None else set()
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return False
        seen.add(annotation)
        return any(
            _is_class_key(name) or _declares_a_class(f.annotation, seen)
            for name, f in annotation.model_fields.items()
        )
    return any(_declares_a_class(a, seen) for a in typing.get_args(annotation))


def _route_declares_a_class(route) -> bool:
    if route is None:
        return False
    key = id(route)
    if key not in _DECLARES:
        field = getattr(route, "body_field", None)
        annotation = None
        if field is not None:
            annotation = getattr(field, "type_", None) or getattr(
                getattr(field, "field_info", None), "annotation", None
            )
        _DECLARES[key] = (
            route,  # held, so its id is never another route's
            bool(annotation) and _declares_a_class(annotation),
        )
    return _DECLARES[key][1]


def _class_ids(value, out: set) -> set:
    """Every integer under a class key in a JSON body."""
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                isinstance(key, str)
                and _is_class_key(key)
                and isinstance(item, int)
                and not isinstance(item, bool)
            ):
                out.add(item)
            else:
                _class_ids(item, out)
    elif isinstance(value, list):
        for item in value:
            _class_ids(item, out)
    return out


def unknown_class_sentence(class_id: int) -> str:
    return (
        f"Class {class_id} is not a class of this company; pick one from the "
        "list, or leave it blank."
    )


def refuse_unknown_class_ids(db: Session, body) -> None:
    """Refuse (404, a sentence) a class id in `body` that is no class of
    this company."""
    ids = _class_ids(body, set())
    if not ids:
        return
    have = {
        row[0] for row in db.query(TxnClass.id).filter(TxnClass.id.in_(sorted(ids)))
    }
    missing = sorted(ids - have)
    if missing:
        raise HTTPException(status_code=404, detail=unknown_class_sentence(missing[0]))


async def class_ids_checked(request: Request, db: Session = Depends(get_db)) -> None:
    """The app-wide dependency (main.py): a write whose schema declares a
    class has every class id in its body checked before the route runs.
    A body that is not JSON is left to the route."""
    if request.method not in _WRITES or not _route_declares_a_class(
        request.scope.get("route")
    ):
        return
    try:
        body = await request.json()
    except Exception:
        return  # the route's own validation says what is wrong with it
    from starlette.concurrency import run_in_threadpool

    await run_in_threadpool(refuse_unknown_class_ids, db, body)
