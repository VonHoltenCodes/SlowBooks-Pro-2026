from typing import Iterable, Type, TypeVar

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.database import Base

T = TypeVar("T", bound=Base)


def clamp_pagination(skip: int, limit: int, max_limit: int = 1000) -> tuple[int, int]:
    """Normalize list-endpoint paging params: skip >= 0, 1 <= limit <= max_limit."""
    return max(0, skip or 0), max(1, min(limit or max_limit, max_limit))


def get_or_404(db: Session, model: Type[T], obj_id: int, name: str = None) -> T:
    obj = db.query(model).filter(model.id == obj_id).first()
    if not obj:
        label = name or model.__name__
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return obj


def reject_null_updates(
    model: Type[T], update_data: dict, allow_null: Iterable[str] = ()
) -> dict:
    """Refuse a PUT that explicitly sets a NOT NULL column to null (422).

    The Update schemas are all-optional and the routes dump them with
    exclude_unset=True, so a field the client sent as ``null`` (but a field
    they omitted does not) survives into ``update_data`` as None. Assigning
    that to a NOT NULL column raised an opaque IntegrityError at commit — an
    HTTP 500 (``PUT /api/customers/{id}`` or ``/vendors/{id}`` with
    ``{"name": null}``). This names the field and answers 422 instead. An
    omitted field never reaches ``update_data``, so an empty ``{}`` PUT is
    still a no-op 200. Generic on the model's columns rather than per-schema,
    so it covers every Update schema the same way. Returns ``update_data`` so
    callers can inline it.

    ``allow_null`` names NOT NULL columns the route fills in itself when the
    client sends null — a bill's ``bill_number``, which the edit form sends
    as null when cleared and the route then generates. Those skip the check
    and fall through to the route's own handling.
    """
    allowed = set(allow_null)
    offenders = sorted(
        c.name
        for c in model.__table__.columns
        if not c.nullable
        and not c.primary_key
        and c.name not in allowed
        and c.name in update_data
        and update_data[c.name] is None
    )
    if offenders:
        # The API's one 422 shape (2.31.1 gate): the list the request's own
        # validation answers, an error per field, each with a plain
        # `message` the page shows ("Mode may not be set to null; ..."). A
        # bare sentence here was the one refusal a client had to read
        # another way.
        from fastapi.exceptions import RequestValidationError

        from app.services.validation_messages import field_label

        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("body", name),
                    "msg": f"Value error, {field_label(name)} may not be set "
                    "to null; leave it out to keep what it is",
                    "input": None,
                }
                for name in offenders
            ]
        )
    return update_data


def _declared_query_names(dependant) -> set:
    """Every query parameter a route (and the dependencies it pulls in) reads."""
    names = {p.alias for p in dependant.query_params}
    for sub in dependant.dependencies:
        names |= _declared_query_names(sub)
    return names


def refuse_unknown_filters(request: Request) -> None:
    """A route dependency: answer 422 with the accepted names when the request
    carries a query parameter the route does not read, so a misspelled filter
    (`jobid=3`, `asof=...`) is not dropped silently and the whole list
    returned. Add it with `dependencies=[Depends(refuse_unknown_filters)]`."""
    route = request.scope.get("route")
    if route is None:
        return
    accepted = _declared_query_names(route.dependant)
    unknown = sorted(set(request.query_params) - accepted)
    if unknown:
        listed = ", ".join(sorted(accepted)) or "none"
        if len(unknown) == 1:
            named = f"{unknown[0]} is not a filter here."
        else:
            named = f"{', '.join(unknown[:-1])} and {unknown[-1]} are not filters here."
        raise HTTPException(
            status_code=422, detail=f"{named} This request accepts: {listed}."
        )
