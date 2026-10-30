# ============================================================================
# Recurring Invoice Service — generates invoices from recurring templates
# Feature 2: Infrastructure C (background scheduler / cron)
# ============================================================================

import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal
from dateutil.relativedelta import relativedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, OperationalError

from app.models.recurring import RecurringInvoice
from app.models.invoices import Invoice, InvoiceLine
from app.models.items import Item
from app.services.jobs_service import own_job
from app.services.numbering import next_invoice_number
from app.services.sub_customers import invoice_addresses
from app.services.accounting import (
    taxed_copy_lines,
    _q,
    compute_line_totals,
    create_journal_entry,
    get_ar_account_id,
    get_default_income_account_id,
    get_sales_tax_account_id,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The schedule vocabulary recurring invoices and memorized transactions
# (#605) share: a frequency names the unit, an interval says every how many.
# Monthly with an interval of 3 and quarterly step the same; the word is
# kept so the list says what was chosen.
# ---------------------------------------------------------------------------
FREQUENCIES = ("daily", "weekly", "semimonthly", "monthly", "quarterly", "yearly")
# QuickBooks' names for a unit and a count.
FREQUENCY_ALIASES = {
    "biweekly": ("weekly", 2),
    "every_two_weeks": ("weekly", 2),
    "every_four_weeks": ("weekly", 4),
    "twice_a_month": ("semimonthly", 1),
    "semi_monthly": ("semimonthly", 1),
    "bimonthly": ("monthly", 2),
    "every_two_months": ("monthly", 2),
    "semiannually": ("monthly", 6),
    "twice_a_year": ("monthly", 6),
    "annually": ("yearly", 1),
}
# The stored words that carry their own count, as a plain word does not.
_COUNTED = ("semimonthly", "quarterly")
_MONTHS_PER = {"monthly": 1, "quarterly": 3, "yearly": 12}


_UNIT_WORDS = {
    "daily": "days",
    "weekly": "weeks",
    "monthly": "months",
    "yearly": "years",
}
# The plain word that counts in the same unit: twice a month and quarterly
# are counted in months.
_PLAIN_UNIT = {"semimonthly": "monthly", "quarterly": "monthly"}


def _takes_no_interval(word: str, unit: str) -> ValueError:
    plain = _PLAIN_UNIT.get(unit, unit)
    return ValueError(
        f"{word.replace('_', ' ')} takes no interval; for every N "
        f"{_UNIT_WORDS[plain]} use {plain} with an interval"
    )


def normalize_frequency(frequency: str, interval=None) -> tuple[str, int]:
    """The stored (frequency, interval) for what a request said, or
    ValueError naming the problem. A word that carries its own count
    ("every two weeks", "twice a month", "quarterly", "twice a year") takes
    no interval: sent with none, or 1, it is that count; sent with another
    it is refused, naming the word, rather than multiplied (2.31.1 gate
    W-65: "every two weeks" with an interval of 2 was stored as every four
    weeks; W-76: "twice a month" was not known at all, so even an interval
    of 1 was refused with the list of words). A plain word (daily, weekly,
    monthly, yearly) takes the interval: every N days, weeks, months or
    years."""
    word = (frequency or "").strip().lower().replace(" ", "_").replace("-", "_")
    try:
        count = int(interval) if interval is not None else 1
    except (TypeError, ValueError):
        raise ValueError("interval must be a whole number of 1 or more")
    if count < 1:
        raise ValueError("interval must be a whole number of 1 or more")
    if word in FREQUENCY_ALIASES:
        unit, base = FREQUENCY_ALIASES[word]
        # the word's own count is the interval: 1, or none, is that count
        if count != 1:
            raise _takes_no_interval(word, unit)
        return unit, base
    if word not in FREQUENCIES:
        raise ValueError(
            "frequency must be one of "
            + ", ".join(FREQUENCIES)
            + " (every N days, weeks or months is daily, weekly or monthly "
            "with an interval)"
        )
    if word in _COUNTED and count != 1:
        raise _takes_no_interval(word, word)
    return word, count


def frequency_label(frequency: str, interval=1) -> str:
    """ "Monthly", "Every two weeks", "Twice a month", "Every 5 days": the
    words the lists show for a schedule."""
    n = int(interval or 1)
    small = {2: "two", 3: "three", 4: "four", 6: "six"}
    if frequency == "semimonthly":
        return "Twice a month"
    if frequency == "quarterly":
        return "Quarterly"
    if frequency == "monthly" and n == 6:
        return "Twice a year"
    unit = {
        "daily": ("Daily", "days"),
        "weekly": ("Weekly", "weeks"),
        "monthly": ("Monthly", "months"),
        "yearly": ("Yearly", "years"),
    }.get(frequency)
    if unit is None:
        return (frequency or "").replace("_", " ").capitalize()
    if n == 1:
        return unit[0]
    return f"Every {small.get(n, n)} {unit[1]}"


def _add_months(current: date, months: int, anchor_day=None) -> date:
    target = current + relativedelta(months=months)
    if anchor_day:
        last = calendar.monthrange(target.year, target.month)[1]
        target = target.replace(day=min(int(anchor_day), last))
    return target


def _next_half_month(current: date, anchor_day=None) -> date:
    """Twice a month: the schedule's day of the month and fifteen days
    after it (the 1st and the 16th; the 15th and the 30th; begun on the
    31st, the 16th and the 31st), each cut back to the last day of a
    shorter month."""
    day = int(anchor_day or current.day)
    first = day - 15 if day > 15 else day
    year, month = current.year, current.month
    for _ in range(2):
        last = calendar.monthrange(year, month)[1]
        for candidate in (first, first + 15):
            d = date(year, month, min(candidate, last))
            if d > current:
                return d
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return current + timedelta(days=15)


def advance_date(
    current: date, frequency: str, interval: int = 1, anchor_day=None
) -> date:
    """The occurrence after `current`. With an anchor day (a memorized
    transaction's anchor_day, a recurring invoice's start day: anchor_day_of),
    a monthly, quarterly or yearly step, or every N months, holds to that
    day of the month: begun on the 31st it lands on the 28th of February and
    the 31st of March, and twice a month keeps that day and fifteen days
    after it. Without one, a step from the 28th of February stays on the
    28th. Daily and weekly steps take no anchor."""
    n = max(1, int(interval or 1))
    if frequency == "daily":
        return current + timedelta(days=n)
    if frequency == "weekly":
        return current + timedelta(weeks=n)
    if frequency == "semimonthly":
        return _next_half_month(current, anchor_day)
    return _add_months(current, _MONTHS_PER.get(frequency, 1) * n, anchor_day)


def anchor_day_of(rec: RecurringInvoice) -> int | None:
    """The day of the month a recurring invoice holds to: its start date's.
    The next due date used to be stepped from the date before it with no
    anchor, so a schedule begun on the 31st went 01-31, 02-28 and then the
    28th of every month for good, and a catch-up made one installment too
    many in the month it ran (2.31.1 gate W-85). Held to the start day, it
    lands on the last day of a shorter month and on its own day again
    after it, as a memorized transaction does with its anchor_day; one that
    already slipped returns to its day at its next step. The start date is
    set when the schedule is made and no edit changes it, so the day needs
    no column of its own."""
    return rec.start_date.day if rec.start_date else None


def _advance_next_due(current: date, frequency: str, anchor_day=None) -> date:
    """A recurring invoice's next due date after `current` (it has no
    interval); pass anchor_day_of(rec) so it holds to its start day."""
    return advance_date(current, frequency, 1, anchor_day)


def roll_next_due_forward(
    current: date, frequency: str, as_of: date, interval: int = 1, anchor_day=None
) -> date:
    """Advance next_due by whole periods to the first occurrence on or after
    as_of. Missed periods while a schedule was paused are skipped, not billed:
    re-activating a template resumed from a stale next_due and back-dated an
    invoice for every missed period (and a date inside the closing date made
    /api/recurring/generate refuse every schedule). A next_due already on or
    after as_of is left alone."""
    d = current
    guard = 0
    while d < as_of and guard < 10000:
        nxt = advance_date(d, frequency, interval, anchor_day)
        if nxt <= d:
            break
        d = nxt
        guard += 1
    return d


def _who(rec: RecurringInvoice) -> str:
    return rec.customer.name if rec.customer else f"schedule {rec.id}"


def _locked(exc: OperationalError) -> bool:
    """SQLite's "database is locked": another write held the file past the
    busy timeout."""
    return "locked" in str(getattr(exc, "orig", exc)).lower()


class InstallmentTaken(HTTPException):
    """The installment asked for is not the schedule's next one. Most often
    it was generated between the read and the claim (another window's Enter
    now, Generate, Enter All Due or the cron script), or the page that asked
    was showing a date the schedule has since moved on from. 409, nothing
    created; the sentence names the installment and the schedule as it now
    stands (2.31.1 gate W-71). A date after the schedule's next one (an API
    call that names a later installment) was never generated, so that
    sentence says it is not the next one, naming both dates, rather than
    "just generated" (2.31.1 gate, round 3 wording note)."""

    def __init__(self, rec: RecurringInvoice, occurrence: date):
        ended = bool(rec.end_date and rec.next_due > rec.end_date)
        # a date before the schedule starts was never one of its dates, so
        # it was not "just generated" either (2.31.1 gate, a round-4 note)
        before_start = rec.start_date is not None and occurrence < rec.start_date
        if occurrence > rec.next_due or before_start:
            now = (
                "it has ended"
                if ended
                else f"its next due date is {rec.next_due.isoformat()}"
            )
            detail = (
                f"The {occurrence.isoformat()} installment is before the schedule "
                f"for {_who(rec)} starts ({rec.start_date.isoformat()}), so "
                f"nothing was created: {now}."
                if before_start
                else f"The {occurrence.isoformat()} installment is not the next one "
                f"of the schedule for {_who(rec)}, so nothing was created: "
                f"{now}."
            )
        else:
            now = (
                "it has ended"
                if ended
                else f"its next due date is now {rec.next_due.isoformat()}"
            )
            detail = (
                "That installment was just generated (by another window, "
                "Generate or Enter All Due) or the schedule was changed, so "
                f"nothing was created: the schedule for {_who(rec)} was due "
                f"{occurrence.isoformat()} and {now}."
            )
        super().__init__(status_code=409, detail=detail)


class InstallmentBusy(HTTPException):
    """SQLite's "database is locked" while claiming an installment: a clean
    409 that says to try again, never a 500. Whatever holds the lock (any
    save in another window, not only another installment of this schedule,
    which the sentence used to say) is named only as another change (2.31.1
    gate, round 3 wording note)."""

    def __init__(self, rec: RecurringInvoice):
        super().__init__(
            status_code=409,
            detail=(
                "The company file is busy saving another change, so nothing "
                "was created. Wait a moment and try again."
            ),
        )


def claim_installment(db: Session, rec: RecurringInvoice, expected: date) -> date:
    """Move the schedule past `expected` in one statement that succeeds only
    while that date is still its next due date, as memorized_service's
    claim_occurrence does for a memorized schedule (2.31.1 gate W-71: two
    generations at once each read the same next due date and each invoiced
    the period). The database does the check: the second UPDATE waits for
    the first to commit and then matches no row, on SQLite and PostgreSQL
    alike. The count of invoices made moves with it. Raises InstallmentTaken
    when the row had already moved on and InstallmentBusy when SQLite would
    not give the write lock in time. It has to be the first statement of its
    transaction (or SAVEPOINT): after a read there, SQLite cannot wait for
    the lock and answers "database is locked" at once. The date it moves
    to holds to the schedule's start day (anchor_day_of, W-85); the start
    date was loaded with the row before the SAVEPOINT, so reading it here
    sends nothing to the database."""
    following = _advance_next_due(expected, rec.frequency, anchor_day_of(rec))
    try:
        moved = (
            db.query(RecurringInvoice)
            .filter(
                RecurringInvoice.id == rec.id,
                RecurringInvoice.next_due == expected,
            )
            .update(
                {
                    RecurringInvoice.next_due: following,
                    RecurringInvoice.invoices_created: (
                        func.coalesce(RecurringInvoice.invoices_created, 0) + 1
                    ),
                },
                synchronize_session=False,
            )
        )
    except OperationalError as e:
        if _locked(e):
            raise InstallmentBusy(rec) from e
        raise
    db.expire(rec, ["next_due", "invoices_created"])
    if moved != 1:
        db.refresh(rec)
        raise InstallmentTaken(rec, expected)
    return expected


def generate_due_invoices(
    db: Session,
    as_of: date = None,
    skipped: list | None = None,
    only_id: int | None = None,
    dated: date | None = None,
    occurrence: date | None = None,
    taken: list | None = None,
) -> list[int]:
    """Generate the next installment of every schedule due on or before
    as_of: one installment per schedule per call (a run that catches up
    calls again while a call generates something or loses a claim,
    scheduler.catch_up_recurring). Returns the ids of the invoices created.

    Each installment is claimed before anything is posted (claim_installment)
    and committed as it is made, so two generations at once never invoice the
    same period twice: the second finds the schedule moved on and leaves it,
    and a locked file skips the schedule with a sentence rather than failing
    the run (2.31.1 gate W-71). Pass a list as `taken` to hear which
    schedules (their ids) had their installment generated by someone else
    meanwhile: each has moved on, so a later installment of it may be due
    as well (W-84).

    A template whose lines add up to $0.00 (one saved before the routes
    refused them) generates nothing and keeps its date, so it catches up
    once a rate is entered; pass a list as `skipped` to hear which ones and
    why. Two such $0.00 invoices were generated on the 2.17.3 exploratory
    run and then showed on the dashboard as overdue.

    `only_id` generates that one template's next installment whether or
    not it is due yet (Enter now on the Memorized transactions page, #605),
    or `occurrence`, the installment the page showed, dated its scheduled
    date, or `dated` when given; the schedule advances from the scheduled
    date either way. For `only_id`, an installment taken meanwhile, a locked
    file or a refusal is raised (a 409 or the refusal) instead of skipped."""
    from app.services.donor_documents import document_label
    from app.services.terminology import terms_from_db

    words = terms_from_db(db)
    from app.services.settings_service import is_nonprofit

    # A nonprofit's recurring invoices are pledges (printed as such)
    nonprofit = is_nonprofit(db)
    if as_of is None:
        as_of = date.today()
    if skipped is None:
        skipped = []

    if only_id is not None:
        recurrings = (
            db.query(RecurringInvoice).filter(RecurringInvoice.id == only_id).all()
        )
    else:
        recurrings = (
            db.query(RecurringInvoice)
            .filter(
                RecurringInvoice.is_active,
                RecurringInvoice.next_due <= as_of,
            )
            .order_by(RecurringInvoice.next_due, RecurringInvoice.id)
            .all()
        )

    created_ids = []
    accounts = (
        get_ar_account_id(db),
        get_default_income_account_id(db),
        get_sales_tax_account_id(db),
    )

    for rec in recurrings:
        # Check end date
        if rec.end_date and rec.next_due > rec.end_date:
            rec.is_active = False
            try:
                db.commit()
            except OperationalError as e:
                # only the Active flag; the next run sets it
                if not _locked(e):
                    raise
                db.rollback()
            continue
        # The installment: the one the page showed, or the next due date
        expected = (
            occurrence
            if (occurrence is not None and only_id is not None)
            else rec.next_due
        )
        # The invoice's date: the scheduled one, or the day Enter now asked for
        on = dated if (dated is not None and only_id is not None) else expected

        # Compute totals via the shared helper: each line is rounded to 2dp
        # before summing so the stored subtotal matches the sum of stored
        # line amounts, and journal credits land on the same cents as the
        # A/R debit once SQL rounds.
        tax_rate = rec.tax_rate or Decimal("0")
        # the customer's CURRENT tax treatment, not the template's saved flags
        copied = taxed_copy_lines(rec.lines, rec.customer)
        subtotal, tax_amount, total = compute_line_totals(copied, tax_rate)
        if total <= 0:
            skipped.append(
                {
                    "recurring_id": rec.id,
                    "customer_name": rec.customer.name if rec.customer else None,
                    "message": (
                        f"The schedule for {_who(rec)} adds up to $0.00, so "
                        "nothing was created. Open it and enter a rate "
                        "on at least one line."
                    ),
                }
            )
            continue

        # One template whose installment lands in a closed period must not
        # sink the whole batch: check_closing_date raised a 403 that aborted
        # generation for every schedule (a healthy one made 0, and the daily
        # run failed the same way). Skip and report this one, keep the rest.
        # A valid override password on the request still passes through.
        from app.services.closing_date import check_closing_date

        try:
            check_closing_date(db, on)
        except HTTPException as e:
            if e.status_code != 403:
                raise
            skipped.append(
                {
                    "recurring_id": rec.id,
                    "customer_name": rec.customer.name if rec.customer else None,
                    "message": (
                        f"The schedule for {_who(rec)} is due {on}, which "
                        "is on or before the closing date, so nothing was "
                        "created. Move the closing date, or clear and re-tick "
                        "Active to roll the next due date forward past it."
                    ),
                }
            )
            continue

        # The claim first, as the SAVEPOINT's first statement (claim_installment
        # says why), then the posting; a refusal part way unwinds both, and the
        # installment is committed as it is made.
        nested = db.begin_nested()
        try:
            claim_installment(db, rec, expected)
            invoice = _post_installment(
                db,
                rec,
                on,
                copied,
                (subtotal, tax_amount, total),
                accounts,
                words,
                nonprofit,
                document_label,
            )
        except InstallmentTaken:
            nested.rollback()
            db.commit()
            if only_id is not None:
                raise
            # generated by another window or run meanwhile: left to it, and
            # named, since the schedule has moved on and may still be due
            if taken is not None:
                taken.append(rec.id)
            continue
        except (InstallmentBusy, OperationalError) as e:
            nested.rollback()
            if isinstance(e, OperationalError) and not _locked(e):
                raise
            db.commit()
            if only_id is not None:
                raise e if isinstance(e, InstallmentBusy) else InstallmentBusy(rec)
            skipped.append(
                {
                    "recurring_id": rec.id,
                    "customer_name": rec.customer.name if rec.customer else None,
                    "message": (
                        f"The schedule for {_who(rec)} (due {on}) was not "
                        "generated this time: the company file was busy "
                        "saving another change. Run it again in a moment."
                    ),
                }
            )
            continue
        except HTTPException as e:
            nested.rollback()
            db.commit()
            if only_id is not None:
                raise
            skipped.append(
                {
                    "recurring_id": rec.id,
                    "customer_name": rec.customer.name if rec.customer else None,
                    "message": (
                        f"The schedule for {_who(rec)} (due {on}) was not "
                        f"generated: {e.detail}"
                    ),
                }
            )
            continue
        except Exception:
            nested.rollback()
            raise
        if invoice is None:
            nested.rollback()
            db.commit()
            skipped.append(
                {
                    "recurring_id": rec.id,
                    "customer_name": rec.customer.name if rec.customer else None,
                    "message": (
                        f"The schedule for {_who(rec)} (due {on}) was not "
                        "generated: no invoice number was free after 10 tries, "
                        "because other invoices were being numbered at the same "
                        "time. Try again."
                    ),
                }
            )
            continue
        nested.commit()
        db.commit()
        created_ids.append(invoice.id)

    db.commit()
    return created_ids


def _post_installment(
    db: Session,
    rec: RecurringInvoice,
    on: date,
    copied,
    totals,
    accounts,
    words,
    nonprofit: bool,
    document_label,
):
    """The invoice for one claimed installment, dated `on`, with its lines,
    its journal entry and its stock; None when no invoice number could be
    had (the caller unwinds the claim). The schedule was already moved on
    by the claim; a schedule that has now passed its end date is marked
    inactive."""
    subtotal, tax_amount, total = totals
    ar_id, default_income_id, tax_account_id = accounts
    tax_rate = rec.tax_rate or Decimal("0")

    # The due date by the one terms parser (#664): its own copy read "Due
    # on Receipt" as 30 days.
    from app.services.payment_terms import due_date_for

    due_date = due_date_for(db, on, rec.terms)

    # MAX+1 numbering races against concurrent manual creates (same as
    # the invoices route). Retry under a SAVEPOINT so a collision rolls
    # back only this attempt, and a template that can't get a number is
    # left for the next run instead of aborting the whole batch.
    # The invoice carries the schedule's customer's job only: another
    # customer's job stored on the schedule (books from before the
    # rule) is left off rather than minted again every period, and the
    # invoice is still generated (NEW-36).
    left_off: list[str] = []
    job_id = own_job(db, rec.customer_id, rec.job_id, left_off)
    if left_off:
        logger.warning(
            "Recurring invoice %s for %s: job %s belongs to a different "
            "customer and was left off the generated invoice",
            rec.id,
            rec.customer.name if rec.customer else rec.customer_id,
            ", ".join(left_off),
        )

    # The payer's whole Bill To as of now (the property's street, name and
    # country for a tenant billed with it) and the customer's own Ship To,
    # through the helper every other new invoice uses. A generated invoice
    # used to carry no street at all, so it printed a name alone.
    addresses = invoice_addresses(db, rec.customer) if rec.customer else {}

    invoice = None
    invoice_number = None
    for _ in range(10):
        invoice_number = next_invoice_number(db)
        candidate = Invoice(
            invoice_number=invoice_number,
            customer_id=rec.customer_id,
            **addresses,
            date=on,
            due_date=due_date,
            terms=rec.terms,
            subtotal=subtotal,
            tax_rate=tax_rate,
            tax_amount=tax_amount,
            total=total,
            balance_due=total,
            notes=rec.notes,
            class_id=rec.class_id,
            job_id=job_id,
            recurring_invoice_id=rec.id,
            is_pledge=nonprofit,
        )
        nested = db.begin_nested()
        db.add(candidate)
        try:
            db.flush()
            nested.commit()
            invoice = candidate
            break
        except IntegrityError as e:
            nested.rollback()
            if "invoice_number" not in str(e.orig).lower():
                raise
    if invoice is None:
        return None

    for rline, cline in zip(rec.lines, copied):
        db.add(
            InvoiceLine(
                invoice_id=invoice.id,
                item_id=rline.item_id,
                description=rline.description,
                quantity=rline.quantity,
                rate=rline.rate,
                is_taxable=cline.is_taxable,
                amount=_q(Decimal(str(rline.quantity)) * Decimal(str(rline.rate))),
                line_order=rline.line_order,
            )
        )

    # Journal entry
    if ar_id and default_income_id:
        journal_lines = [
            {
                "account_id": ar_id,
                "debit": total,
                "credit": Decimal("0"),
                "description": f"Recurring {document_label(invoice, words)} #{invoice_number}",
            }
        ]
        for rline in rec.lines:
            # Must round per-line BEFORE summing to match compute_line_totals;
            # otherwise stored credits drift from the rounded A/R debit.
            line_amt = _q(Decimal(str(rline.quantity)) * Decimal(str(rline.rate)))
            if line_amt == 0:
                continue
            income_id = default_income_id
            if rline.item_id:
                item = db.query(Item).filter(Item.id == rline.item_id).first()
                if item and item.income_account_id:
                    income_id = item.income_account_id
            journal_lines.append(
                {
                    "account_id": income_id,
                    "debit": Decimal("0"),
                    "credit": line_amt,
                    "description": rline.description or "",
                }
            )
        if tax_amount > 0 and tax_account_id:
            journal_lines.append(
                {
                    "account_id": tax_account_id,
                    "debit": Decimal("0"),
                    "credit": tax_amount,
                    "description": "Sales tax",
                }
            )
        txn = create_journal_entry(
            db,
            on,
            f"Recurring {document_label(invoice, words)} #{invoice_number}",
            journal_lines,
            source_type="invoice",
            source_id=invoice.id,
            reference=invoice_number,
            class_id=invoice.class_id,
            job_id=invoice.job_id,
        )
        invoice.transaction_id = txn.id

    # Phase 11 (audit fix): recurring-generated invoices are real sales
    # and must hit the inventory ledger.
    db.flush()
    db.refresh(invoice)
    from app.services.inventory_hooks import post_sale_for_invoice

    post_sale_for_invoice(db, invoice, txn_date=on)

    # the claim moved the schedule on; past its end date, it stops
    if rec.end_date and rec.next_due > rec.end_date:
        rec.is_active = False
    db.flush()
    return invoice
