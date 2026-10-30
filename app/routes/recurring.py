# ============================================================================
# Recurring Invoices — CRUD + manual generate
# Feature 2: Schedule automatic invoice generation
# ============================================================================

from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes._helpers import reject_null_updates
from app.routes.invoices.helpers import refuse_zero_total, resolve_line_taxable
from app.models.invoices import Invoice
from app.models.recurring import RecurringInvoice, RecurringInvoiceLine
from app.models.contacts import Customer
from app.schemas.recurring import RecurringCreate, RecurringUpdate, RecurringResponse
from app.services.accounting import compute_line_totals
from app.services.jobs_service import (
    refuse_other_customers_jobs,
    refuse_other_customers_jobs_on_edit,
)
from app.services.recurring_service import (
    InstallmentTaken,
    anchor_day_of,
    generate_due_invoices,
    roll_next_due_forward,
)
from app.services.payment_terms import retie, term_id_for
from app.services.terminology import terms_from_db

router = APIRouter(prefix="/api/recurring", tags=["recurring"])


@router.get("", response_model=list[RecurringResponse])
def list_recurring(active_only: bool = False, db: Session = Depends(get_db)):
    q = db.query(RecurringInvoice)
    if active_only:
        q = q.filter(RecurringInvoice.is_active)
    recs = q.order_by(RecurringInvoice.next_due).all()
    results = []
    for r in recs:
        resp = RecurringResponse.model_validate(r)
        if r.customer:
            resp.customer_name = r.customer.name
        results.append(resp)
    return results


@router.get("/{rec_id}", response_model=RecurringResponse)
def get_recurring(rec_id: int, db: Session = Depends(get_db)):
    rec = db.query(RecurringInvoice).filter(RecurringInvoice.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recurring invoice not found")
    resp = RecurringResponse.model_validate(rec)
    if rec.customer:
        resp.customer_name = rec.customer.name
    return resp


def _refuse_end_before_start(start: date, end: date | None) -> None:
    """A schedule ending before it starts was accepted (2.17.3 exploratory
    W-L4); it could never bill anything."""
    if end is not None and end < start:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The end date ({end:%b} {end.day}, {end.year}) is before the start "
                f"date ({start:%b} {start.day}, {start.year}). Pick an end date on "
                "or after the start date, or leave it blank for no end."
            ),
        )


def _ended_on_resume(stored: date | None, sent: date, resumes: date) -> str:
    """Why ticking Active again is refused: the schedule would be past its
    end date on its first installment. The request is refused and rolled
    back, so the end date named is the one the schedule has, not the one
    the request sent (2.31.1 gate W-81(c) quoted the request's); an end date
    sent that is still too early is named as such, with the date the
    schedule would resume on."""
    tail = "; move or clear the end date first."
    if sent == stored:
        return f"This schedule has ended: its end date was {stored.isoformat()}" + tail
    sent_too_early = (
        f"the end date sent, {sent.isoformat()}, is before "
        f"{resumes.isoformat()}, the next due date it would resume on, so "
        "nothing was changed"
    )
    if stored is None:
        return "This schedule would end as soon as it resumed: " + sent_too_early + tail
    return (
        f"This schedule has ended: its end date was {stored.isoformat()}, and "
        + sent_too_early
        + tail
    )


def _schedule_noun(db: Session) -> str:
    """The schedule as the company's vocabulary names it: "recurring
    invoice", or "recurring pledge" for a nonprofit."""
    return "recurring " + terms_from_db(db)("invoice")


@router.post("", response_model=RecurringResponse, status_code=201)
def create_recurring(data: RecurringCreate, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    _refuse_end_before_start(data.start_date, data.end_date)
    # The job is this customer's (NEW-36); the schedule's lines carry none.
    refuse_other_customers_jobs(db, customer.id, data.job_id, (), _schedule_noun(db))

    # Refused here rather than skipped at every run: a template that adds up
    # to nothing generated $0.00 invoices that then showed as overdue.
    resolve_line_taxable(db, data.lines, customer)
    refuse_zero_total(
        compute_line_totals(data.lines, data.tax_rate)[2], _schedule_noun(db)
    )

    rec = RecurringInvoice(
        customer_id=data.customer_id,
        frequency=data.frequency,
        start_date=data.start_date,
        end_date=data.end_date,
        next_due=data.start_date,
        terms=data.terms,
        # its invoices follow the term these terms name now (#664)
        payment_term_id=term_id_for(db, data.terms),
        tax_rate=data.tax_rate,
        notes=data.notes,
        class_id=data.class_id,
        job_id=data.job_id,
    )
    db.add(rec)
    db.flush()

    for i, line_data in enumerate(data.lines):
        db.add(
            RecurringInvoiceLine(
                recurring_invoice_id=rec.id,
                item_id=line_data.item_id,
                description=line_data.description,
                quantity=line_data.quantity,
                rate=line_data.rate,
                is_taxable=(
                    line_data.is_taxable if line_data.is_taxable is not None else True
                ),
                line_order=i,
            )
        )

    db.commit()
    db.refresh(rec)
    resp = RecurringResponse.model_validate(rec)
    resp.customer_name = customer.name
    return resp


@router.put("/{rec_id}", response_model=RecurringResponse)
def update_recurring(rec_id: int, data: RecurringUpdate, db: Session = Depends(get_db)):
    rec = db.query(RecurringInvoice).filter(RecurringInvoice.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recurring invoice not found")
    if "end_date" in data.model_fields_set:
        _refuse_end_before_start(rec.start_date, data.end_date)
    changes = reject_null_updates(
        RecurringInvoice, data.model_dump(exclude_unset=True, exclude={"lines"})
    )
    # A changed job must be the schedule's customer's (NEW-36); a job sent
    # unchanged, or left alone, is not judged (the lines carry none).
    refuse_other_customers_jobs_on_edit(db, rec, changes, None, _schedule_noun(db))

    if data.lines is not None:
        resolve_line_taxable(db, data.lines, rec.customer)
        tax_rate = data.tax_rate if data.tax_rate is not None else rec.tax_rate
        refuse_zero_total(
            compute_line_totals(data.lines, tax_rate)[2], _schedule_noun(db)
        )

    was_active = rec.is_active
    stored_end = rec.end_date
    terms_before = rec.terms
    for key, val in changes.items():
        setattr(rec, key, val)
    # terms the person changed tie the schedule anew; the same terms sent
    # back leave it as it was saved (#664)
    retie(db, rec, terms_before)

    # Re-activating a paused template resumed from a stale next_due and
    # back-dated an invoice for every missed period (and a date inside the
    # closing date made generate refuse every schedule). When Active goes
    # from off to on, roll next_due forward by whole periods to the first
    # occurrence on or after today; missed periods are skipped, not billed.
    # The roll holds to the schedule's start day (2.31.1 gate W-85).
    if changes.get("is_active") and not was_active:
        rec.next_due = roll_next_due_forward(
            rec.next_due, rec.frequency, date.today(), 1, anchor_day_of(rec)
        )
        # Ticking Active on a schedule past its end date did nothing but
        # answer 200 (2.31.1 gate W-81): it can never bill again until the
        # end date moves, so say so. An end date cleared or moved past the
        # next due date in the same request is what makes it active again.
        if rec.end_date and rec.next_due > rec.end_date:
            detail = _ended_on_resume(stored_end, rec.end_date, rec.next_due)
            db.rollback()
            raise HTTPException(status_code=409, detail=detail)

    if data.lines is not None:
        db.query(RecurringInvoiceLine).filter(
            RecurringInvoiceLine.recurring_invoice_id == rec_id
        ).delete()
        for i, line_data in enumerate(data.lines):
            db.add(
                RecurringInvoiceLine(
                    recurring_invoice_id=rec_id,
                    item_id=line_data.item_id,
                    description=line_data.description,
                    quantity=line_data.quantity,
                    rate=line_data.rate,
                    is_taxable=(
                        line_data.is_taxable
                        if line_data.is_taxable is not None
                        else True
                    ),
                    line_order=i,
                )
            )

    db.commit()
    db.refresh(rec)
    resp = RecurringResponse.model_validate(rec)
    if rec.customer:
        resp.customer_name = rec.customer.name
    return resp


@router.delete("/{rec_id}")
def delete_recurring(rec_id: int, db: Session = Depends(get_db)):
    rec = db.query(RecurringInvoice).filter(RecurringInvoice.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recurring invoice not found")
    # A template that has made invoices cannot be deleted: deleting it left
    # those invoices pointing at a template that no longer existed
    # (SlowBooks-Pro-Testing#101). It keeps billing until the user acts.
    made = db.query(Invoice).filter(Invoice.recurring_invoice_id == rec.id).count()
    if made:
        t = terms_from_db(db)
        noun = t("invoice") if made == 1 else t("invoices")
        raise HTTPException(
            status_code=409,
            detail=(
                f"This {_schedule_noun(db)} has made {made} {noun}, so it "
                "cannot be deleted, and it keeps billing until you act. Set an "
                "End Date to stop it for good, or clear Active to pause it."
            ),
        )
    db.delete(rec)
    db.commit()
    return {"message": "Recurring invoice deleted"}


@router.post("/{rec_id}/generate")
def generate_one(
    rec_id: int,
    dated: date = Query(default=None),
    occurrence: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """Enter this one schedule's installment now (#605, the Memorized
    transactions page's Enter now), due or not: `occurrence`, the
    installment the page showed (the next due date when not sent), dated
    its scheduled date or `dated`; the schedule moves on from the scheduled
    date. The installment is claimed before anything posts, so one that is
    no longer the next (generated meanwhile by another window, Generate or
    Enter All Due, or the schedule edited) is a 409, "That installment was
    just generated", and nothing is created (2.31.1 gate W-71); one after
    the next due date is a 409 saying it is not the next one."""
    rec = db.query(RecurringInvoice).filter(RecurringInvoice.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recurring invoice not found")
    asked = occurrence or rec.next_due
    if asked != rec.next_due:
        raise InstallmentTaken(rec, asked)
    # Ended before paused (2.31.1 gate W-64): the generator marks a schedule
    # past its end date inactive, so the paused sentence fired for one that
    # had ended, and resuming it would not have helped.
    if rec.end_date and rec.next_due > rec.end_date:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This schedule has ended: its end date was "
                f"{rec.end_date.isoformat()}, so nothing was created. Move or "
                "clear the end date to bill it again."
            ),
        )
    if not rec.is_active:
        raise HTTPException(
            status_code=409,
            detail=(
                "This schedule is paused. Resume it to enter the invoice, "
                "or enter one by hand."
            ),
        )
    skipped: list = []
    created_ids = generate_due_invoices(
        db,
        rec.next_due,
        skipped=skipped,
        only_id=rec.id,
        dated=dated,
        occurrence=asked,
    )
    if skipped:
        raise HTTPException(status_code=409, detail=skipped[0]["message"])
    if not created_ids:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This {_schedule_noun(db)} is past its end date, so nothing "
                "was created. Move or clear the end date to bill it again."
            ),
        )
    db.refresh(rec)
    return {
        "invoices_created": len(created_ids),
        "invoice_ids": created_ids,
        "occurrence": asked,
        "next_due": rec.next_due,
        "skipped": skipped,
    }


@router.post("/generate")
def generate_now(as_of: date = Query(default=None), db: Session = Depends(get_db)):
    """Manually trigger generation of all due recurring invoices — one
    installment per template per call. `as_of` (default today) lets a
    catch-up or a test run generate a past installment deterministically.
    Each installment is claimed before it posts, so two at once never
    invoice one period twice; a schedule another generation holds is
    skipped and named."""
    skipped: list = []
    created_ids = generate_due_invoices(db, as_of, skipped=skipped)
    return {
        "invoices_created": len(created_ids),
        "invoice_ids": created_ids,
        "skipped": skipped,
    }
