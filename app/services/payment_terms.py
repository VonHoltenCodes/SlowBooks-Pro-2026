# ============================================================================
# The one terms parser (#664). Every document that takes terms — invoices,
# bills, recurring invoices, memorized bills, an estimate's or a PO's
# conversion, a duplicate — asks here for its due date.
#
# A term is read from the terms list first (app/models/payment_terms.py),
# then from the text the app has always understood: "Net N" and the
# due-on-receipt words. Anything else keeps the old 30 days, and the
# document says so in a sentence (terms_note) instead of quietly.
# ============================================================================

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.payment_terms import DATE_DRIVEN, STANDARD, PaymentTerm

# What a document without a rule it understands has always been given.
FALLBACK_DAYS = 30

# The terms the forms offered before the list, seeded as its first entries
# so no book changes: each keeps the rule the old parser gave it.
SEED = (
    ("Due on Receipt", 0),
    ("Net 15", 15),
    ("Net 30", 30),
    ("Net 45", 45),
    ("Net 60", 60),
)

# The text the old parser read as due the same day.
RECEIPT_WORDS = ("due on receipt", "due upon receipt", "cod", "net 0")

# "Net 30", "net  30" or a bare "30", as the old parser took them; a signed
# count too, so "-0001" still makes a due date before the document's date,
# which the save refuses (docs/features.md). A term past ten years is not
# one: it is refused on the list and unread here.
_NET_N = re.compile(r"^(?:net\s+)?([+-]?\d{1,4})$")
MAX_DAYS = 3650


def _key(text: Optional[str]) -> str:
    """The text as terms are matched: trimmed, single-spaced, lower case."""
    return " ".join((text or "").split()).lower()


@dataclass(frozen=True)
class Rule:
    """A term's rule, from the list or read from the text."""

    name: str
    kind: str = STANDARD
    net_days: Optional[int] = None
    due_day: Optional[int] = None
    cutoff_days: int = 0
    discount_percent: Optional[Decimal] = None
    discount_days: Optional[int] = None
    discount_day: Optional[int] = None

    def due_date(self, on: date) -> date:
        if self.kind == DATE_DRIVEN:
            due = _day_in(on.year, on.month, self.due_day)
            # Before the date, or fewer than cutoff_days after it: the
            # next month's day. Never more than one month on.
            if (due - on).days < (self.cutoff_days or 0) or due < on:
                due = _day_in(*_next_month(on.year, on.month), self.due_day)
            return due
        return on + timedelta(days=self.net_days or 0)

    def discount_date(self, on: date) -> Optional[date]:
        """The last day the early-payment discount can be taken, or None
        when the term offers none."""
        if not self.discount_percent:
            return None
        if self.kind == DATE_DRIVEN:
            due = self.due_date(on)
            last = _day_in(due.year, due.month, self.discount_day)
            if last > due:
                last = _day_in(*_prev_month(due.year, due.month), self.discount_day)
            return last if last >= on else None
        return on + timedelta(days=self.discount_days or 0)


def _day_in(year: int, month: int, day: int) -> date:
    """Day `day` of the month, or its last day when the month is shorter."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def rule_of(term: PaymentTerm) -> Rule:
    return Rule(
        name=term.name,
        kind=term.kind,
        net_days=term.net_days,
        due_day=term.due_day,
        cutoff_days=term.cutoff_days or 0,
        discount_percent=(
            Decimal(str(term.discount_percent))
            if term.discount_percent is not None
            else None
        ),
        discount_days=term.discount_days,
        discount_day=term.discount_day,
    )


def find_term(db: Session, text: Optional[str]) -> Optional[PaymentTerm]:
    """The list entry these terms name, matched as _key matches: "net 30"
    is Net 30. Inactive entries still count, so a document keeps the rule
    it was made under."""
    key = _key(text)
    if not key:
        return None
    term = (
        db.query(PaymentTerm)
        .filter(func.lower(func.trim(PaymentTerm.name)) == key)
        .first()
    )
    if term is not None:
        return term
    # A name typed with doubled spaces matches too; the list is short.
    for term in db.query(PaymentTerm).all():
        if _key(term.name) == key:
            return term
    return None


def read_text(text: Optional[str]) -> Optional[Rule]:
    """The rule the old parser read from the text, or None when it read
    none: "Net N" (or a bare N), and the due-on-receipt words."""
    key = _key(text)
    if key in RECEIPT_WORDS:
        return Rule(name=text.strip(), net_days=0)
    match = _NET_N.match(key)
    if match and abs(int(match.group(1))) <= MAX_DAYS:
        return Rule(name=text.strip(), net_days=int(match.group(1)))
    return None


def rule_for(db: Session, text: Optional[str]) -> Optional[Rule]:
    """The list first, the text second; None when neither understands it."""
    term = find_term(db, text)
    if term is not None:
        return rule_of(term)
    return read_text(text)


def unrecognised_note(text: str, noun: str = "invoice") -> str:
    return (
        f'"{text.strip()}" isn\'t on the terms list, so the due date is '
        f"{FALLBACK_DAYS} days from the {noun} date. Add it under Settings > "
        "Payment Terms to give it its own rule."
    )


@dataclass(frozen=True)
class TermsDates:
    """What a document's terms and date make of it."""

    due_date: date
    discount_percent: Optional[Decimal] = None
    discount_date: Optional[date] = None
    # A sentence when the terms aren't understood (the due date is then the
    # old 30 days); None otherwise.
    note: Optional[str] = None


def terms_dates(
    db: Session, on: date, text: Optional[str], noun: str = "invoice"
) -> TermsDates:
    """The due date, early-payment discount and any note for a document
    dated `on` with these terms. Blank terms are the default Net 30, as
    they always were."""
    if not _key(text):
        return TermsDates(due_date=on + timedelta(days=FALLBACK_DAYS))
    rule = rule_for(db, text)
    if rule is None:
        return TermsDates(
            due_date=on + timedelta(days=FALLBACK_DAYS),
            note=unrecognised_note(text, noun),
        )
    discount_date = rule.discount_date(on)
    return TermsDates(
        due_date=rule.due_date(on),
        discount_percent=rule.discount_percent if discount_date else None,
        discount_date=discount_date,
    )


def due_date_for(db: Session, on: date, text: Optional[str]) -> date:
    return terms_dates(db, on, text).due_date


def terms_note(db: Session, text: Optional[str], noun: str = "invoice"):
    """The sentence a document shows when its terms aren't understood."""
    if not _key(text) or rule_for(db, text) is not None:
        return None
    return unrecognised_note(text, noun)


def describe(rule: Rule) -> str:
    """The rule in words, for the list and a tooltip."""
    if rule.kind == DATE_DRIVEN:
        words = f"Due on day {rule.due_day} of the month"
        if rule.cutoff_days:
            words += (
                f", the next month's when dated fewer than {rule.cutoff_days} "
                "days before it"
            )
    elif not rule.net_days:
        words = "Due on receipt"
    else:
        words = f"Due in {rule.net_days} days"
    if rule.discount_percent:
        pct = f"{rule.discount_percent.normalize():f}%"
        if rule.kind == DATE_DRIVEN:
            words += f"; {pct} off if paid by day {rule.discount_day}"
        else:
            words += f"; {pct} off if paid within {rule.discount_days} days"
    return words


def discount_line(percent, by: Optional[date]) -> Optional[str]:
    """The line a document and its PDF print under the terms."""
    if not percent or by is None:
        return None
    pct = f"{Decimal(str(percent)).normalize():f}%"
    return f"{pct} discount if paid by {by.strftime('%b')} {by.day}, {by.year}"


def ensure_seeded(db: Session) -> None:
    """Give a company with an empty list the terms the forms always
    offered. Flushes; the caller commits."""
    if db.query(PaymentTerm.id).first() is not None:
        return
    for name, days in SEED:
        db.add(PaymentTerm(name=name, kind=STANDARD, net_days=days))
    db.flush()


class DocumentTerms:
    """Fills a document response's discount and terms note, reading each
    terms text once per request (a list of invoices shares a few terms)."""

    def __init__(self, db: Session, noun: str = "invoice"):
        self.db = db
        self.noun = noun
        self._rules: dict = {}

    def _rule(self, text):
        key = _key(text)
        if key not in self._rules:
            self._rules[key] = rule_for(self.db, text) if key else None
        return self._rules[key]

    def fill(self, resp, doc) -> None:
        """discount_percent, discount_date, discount_line and terms_note on
        `resp`, from `doc`'s terms and date."""
        if not _key(doc.terms) or doc.date is None:
            return
        rule = self._rule(doc.terms)
        if rule is None:
            resp.terms_note = unrecognised_note(doc.terms, self.noun)
            return
        by = rule.discount_date(doc.date)
        if by is not None:
            resp.discount_percent = rule.discount_percent
            resp.discount_date = by
            resp.discount_line = discount_line(rule.discount_percent, by)


def document_discount_line(doc) -> Optional[str]:
    """The discount line a printed invoice or bill carries under its terms,
    read through the document's own session; None when there is none."""
    from sqlalchemy.orm import object_session

    db = object_session(doc)
    if db is None or doc.date is None or not _key(doc.terms):
        return None
    rule = rule_for(db, doc.terms)
    if rule is None:
        return None
    return discount_line(rule.discount_percent, rule.discount_date(doc.date))
