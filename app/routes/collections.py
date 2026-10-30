# ============================================================================
# Collections (#653) — payment reminder rules, the open-invoice list with each
# invoice's reminder status, sending reminders, and monthly statements.
# The logic lives in app/services/collections.py; this is the HTTP shell.
# ============================================================================

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.collections import InvoiceReminder, InvoiceReminderRule
from app.routes._helpers import get_or_404
from app.routes._roles import require_admin
from app.schemas.collections import (
    RuleCreate,
    RuleResponse,
    RuleUpdate,
    RunRequest,
    StatementRunRequest,
)
from app.services import collections as svc

router = APIRouter(prefix="/api/collections", tags=["collections"])


def _bad_request(e) -> HTTPException:
    # The service's errors carry the sentence for the screen in .message;
    # nothing of the exception itself reaches the response.
    return HTTPException(status_code=400, detail=e.message)


def _check_day_free(db: Session, days: int, rule_id: Optional[int] = None) -> None:
    q = db.query(InvoiceReminderRule.id).filter(
        InvoiceReminderRule.days_from_due == days
    )
    if rule_id is not None:
        q = q.filter(InvoiceReminderRule.id != rule_id)
    if q.first():
        raise HTTPException(400, "A rule for that day already exists")


def _check_text(subject: str, body: str) -> None:
    try:
        svc.validate_template(subject, body)
    except svc.TemplateInvalid as e:
        raise _bad_request(e)


# ---- Rules ----


@router.get("/rules", response_model=list[RuleResponse])
def list_rules(db: Session = Depends(get_db)):
    """The rules as they are. A read writes nothing: the four starter rules
    come from migration c8e1a4d7f305, once, and a company that deleted them
    keeps an empty list (review of #654)."""
    return [svc.rule_dict(r) for r in svc.list_rules(db)]


@router.post("/rules", response_model=RuleResponse, status_code=201)
def create_rule(data: RuleCreate, request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    _check_day_free(db, data.days_from_due)
    _check_text(data.subject, data.body)
    rule = InvoiceReminderRule(**data.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return svc.rule_dict(rule)


@router.put("/rules/{rule_id}", response_model=RuleResponse)
def update_rule(
    rule_id: int, data: RuleUpdate, request: Request, db: Session = Depends(get_db)
):
    require_admin(request)
    rule = get_or_404(db, InvoiceReminderRule, rule_id, "Rule")
    changes = data.model_dump(exclude_unset=True)
    for required in ("days_from_due", "enabled", "subject", "body"):
        if required in changes and changes[required] is None:
            raise HTTPException(400, f"{required} can't be empty")
    if "days_from_due" in changes:
        _check_day_free(db, changes["days_from_due"], rule.id)
    _check_text(changes.get("subject", rule.subject), changes.get("body", rule.body))
    for key, val in changes.items():
        setattr(rule, key, val)
    db.commit()
    db.refresh(rule)
    return svc.rule_dict(rule)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    rule = get_or_404(db, InvoiceReminderRule, rule_id, "Rule")
    # The audit keeps the rule it came from, so a rule that has sent can only
    # be switched off.
    if db.query(InvoiceReminder.id).filter(InvoiceReminder.rule_id == rule.id).first():
        raise HTTPException(
            409, "This rule has already sent reminders; disable it instead"
        )
    db.delete(rule)
    db.commit()
    return {"deleted": True}


# ---- Open invoices and reminders ----


@router.get("/invoices")
def collections_invoices(
    customer_id: Optional[int] = None,
    overdue_only: bool = False,
    as_of_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
):
    return svc.invoice_rows(db, as_of_date or date.today(), customer_id, overdue_only)


# Sends, and the previews of them, are always as of today (review of #654):
# the list's As of date is for looking, and looking ahead must not send the
# final reminder early or a second statement.


@router.get("/reminders/due")
def reminders_due(db: Session = Depends(get_db)):
    return svc.preview_reminders(db)


@router.post("/reminders/run")
def reminders_run(data: Optional[RunRequest] = None, db: Session = Depends(get_db)):
    data = data or RunRequest()
    try:
        return svc.run_reminders(db, "run", data.invoice_ids)
    except svc.EmailNotSetUp as e:
        raise _bad_request(e)


@router.post("/reminders/run-if-due")
def reminders_run_if_due(db: Session = Depends(get_db)):
    return svc.run_if_due(db, date.today())


@router.post("/invoices/{invoice_id}/remind")
def remind_invoice(invoice_id: int, db: Session = Depends(get_db)):
    try:
        return svc.remind_invoice(db, invoice_id)
    except LookupError:
        raise HTTPException(404, "Invoice not found")
    except (svc.RemindError, svc.EmailNotSetUp) as e:
        raise _bad_request(e)


# ---- Monthly statements ----


@router.get("/statements/due")
def statements_due(db: Session = Depends(get_db)):
    return svc.statements_due(db)


@router.post("/statements/run")
def statements_run(
    data: Optional[StatementRunRequest] = None, db: Session = Depends(get_db)
):
    data = data or StatementRunRequest()
    try:
        return svc.run_statements(db, data.force)
    except svc.EmailNotSetUp as e:
        raise _bad_request(e)
