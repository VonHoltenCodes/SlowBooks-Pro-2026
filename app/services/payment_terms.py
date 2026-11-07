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
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

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

# Past this many days a date leaves the calendar (main's parser raised a
# 500 there); such text reads as nothing.
MAX_TEXT_DAYS = 99999
# The longest a term on the list may run.
MAX_DAYS = 3650


# The joiners some scripts and emoji need (ZWNJ in Persian and Indic text,
# ZWJ in "👨‍👩‍👧"): kept in a stored name, ignored when names are compared.
JOINERS = {"\u200c", "\u200d"}


def _strip_format(text: str, keep=frozenset()) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf" or ch in keep)


def clean_name(text: Optional[str]) -> str:
    """A term name as stored: NFKC-normalized (a full-width "Ｎｅｔ" is
    "Net"), invisible format characters (a zero-width space, a BOM, a
    direction mark) taken out but for the joiners, and every run of
    whitespace (a tab, CR, LF, NBSP) one space, trimmed (#665: "Net<TAB>81"
    passed the name guard, which read it as main did, then matched plain
    "Net 81" through the list's key). Stored so, a name is read by the
    guard, matched by the list and parsed by main_days alike."""
    text = _strip_format(unicodedata.normalize("NFKC", text or ""), JOINERS)
    return " ".join(text.split())


# A small table of the Cyrillic and Greek letters that look exactly like a
# Latin letter (#665: "Nеt 30" with a Cyrillic е was a second "Net 30").
# Folded only for matching, never in the stored name. The letter O is NOT
# here: folding it to the digit 0 would make "1OO" and "100" one name.
_CONFUSABLES = {
    # Cyrillic
    "а": "a",
    "е": "e",
    "о": "o",
    "р": "p",
    "с": "c",
    "х": "x",
    "у": "y",
    "і": "i",
    "ј": "j",
    "ѕ": "s",
    "А": "a",
    "Е": "e",
    "О": "o",
    "Р": "p",
    "С": "c",
    "Х": "x",
    "У": "y",
    "І": "i",
    "Ј": "j",
    "Ѕ": "s",
    # Greek
    "ο": "o",
    "α": "a",
    "ν": "v",
    "ρ": "p",
    "τ": "t",
    "ι": "i",
    "κ": "k",
    "Ο": "o",
    "Α": "a",
    "Ν": "v",
    "Ρ": "p",
    "Τ": "t",
    "Ι": "i",
    "Κ": "k",
}


def _key(text: Optional[str]) -> str:
    """The text as a list name is matched: NFKC, every format character out
    (the joiners too), the common confusables folded to their Latin
    look-alike, EVERY whitespace ignored, lower case (#665: "Net30",
    "Nеt 30" and "Net 30" are one name). Matching to the list only; text is
    READ as the old parser read it (read_text), which is stricter."""
    text = _strip_format(unicodedata.normalize("NFKC", text or ""))
    text = "".join(_CONFUSABLES.get(ch, ch) for ch in text)
    return "".join(text.split()).lower()


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
            # the first due day on or after the date (a short month's last
            # day standing in for a day it lacks); dated fewer than
            # cutoff_days before it, the month after's (#665: the cutoff was
            # only checked against the date's own month, so a document dated
            # Jan 27 under "5th, 10-day cutoff" was due Feb 5, not Mar 5)
            due = _day_in(on.year, on.month, self.due_day)
            if due < on:
                due = _day_in(*_next_month(on.year, on.month), self.due_day)
            if (due - on).days < (self.cutoff_days or 0):
                due = _day_in(*_next_month(due.year, due.month), self.due_day)
            return due
        return on + timedelta(days=self.net_days or 0)

    def discount_date(self, on: date) -> Optional[date]:
        """The last day the early-payment discount can be taken, or None
        when the term offers none."""
        if not self.discount_percent:
            return None
        if self.kind == DATE_DRIVEN:
            due = self.due_date(on)
            if self.discount_day > self.due_day:
                # "paid by day D of the month BEFORE it's due" (#665): a
                # short month's clamp used to land day D on the due day
                # itself (_day_in(due.month, 31) is the 28th in February,
                # equal to a due_day of 30), so the discount ran right
                # through the due date. Compute it in the month before the
                # due month, clamped to that month's last day; it is before
                # the due date whatever the month's length.
                last = _day_in(*_prev_month(due.year, due.month), self.discount_day)
                # a document dated after that day offers none: a Feb 1
                # invoice cannot be paid by Jan 31
                return last if on <= last < due else None
            # the same month, an earlier day: no discount once that day has
            # already passed by the document's own date. The order is the
            # configured days' order, decided before the clamp (#664): "by
            # the 28th, net the 30th" clamps both to Feb 28 in February, and
            # the discount then runs to that same day rather than vanishing.
            # A discount day equal to the due day is refused at save.
            if self.discount_day == self.due_day:
                return None
            last = _day_in(due.year, due.month, self.discount_day)
            return last if on <= last <= due else None
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
    """The list entry these terms name, matched in Python as _key matches:
    "net 30" is Net 30. The list is short, so it is loaded and compared
    here rather than in SQL — Postgres's lower() folds İSTANBUL/istanbul
    and ΟΔΟΣ/οδοσ together where _key does not, which tied a document to
    the wrong row (#665, B4/B5). An exact-key tie goes to the lowest id, so
    the answer is the same every time. Inactive entries still count, so a
    document keeps the rule it was made under."""
    key = _key(text)
    if not key:
        return None
    matches = [t for t in db.query(PaymentTerm).all() if _key(t.name) == key]
    return min(matches, key=lambda t: t.id) if matches else None


def main_days(text: Optional[str]) -> Optional[int]:
    """The days main's parser gave the text, exactly as it read it, or None
    where it fell back to 30: trimmed and lower-cased, the four receipt
    words are 0, else int() of the text with "net " (one space) taken out,
    so "net\t45" and "net\u00a045" were 30 days, "Net 1_0" was 10 and a
    bare "30" was 30."""
    if not text:
        return None
    t = text.strip().lower()
    if t in RECEIPT_WORDS:
        return 0
    try:
        days = int(t.replace("net ", "").strip())
    except ValueError:
        return None
    return days if abs(days) <= MAX_TEXT_DAYS else None


def read_text(text: Optional[str]) -> Optional[Rule]:
    """The rule the old parser read from the text (main_days), or None when
    it read none. Every reader of a document not tied to a term uses this,
    never the list, so a term added later can't reach it."""
    days = main_days(text)
    if days is None:
        return None
    return Rule(name=text.strip(), net_days=days)


def rule_for(db: Session, text: Optional[str]) -> Optional[Rule]:
    """The list first, the text second; None when neither understands it."""
    term = find_term(db, text)
    if term is not None:
        return rule_of(term)
    return read_text(text)


# Who can act on a note: the list is an administrator's (Settings).
ADD_IT = (
    "An administrator can add it under Settings > Payment Terms to give it "
    "its own rule."
)


def company_noun(db: Session, noun: str) -> str:
    """A note's document word in the company's own vocabulary (#664): a
    nonprofit reads "pledge" where a business reads "invoice", and "bill"
    stays "bill" everywhere. Translated once at each note's entry point, so
    every note a document or the /dates form shows uses the right word."""
    from app.services.terminology import terms_from_db

    return terms_from_db(db)(noun)


def unrecognised_note(text: str, noun: str = "invoice") -> str:
    """The note for terms a new document is about to be saved with."""
    return (
        f'"{text.strip()}" isn\'t on the terms list, so the due date is '
        f"{FALLBACK_DAYS} days from the {noun} date. {ADD_IT}"
    )


def _day(d: date) -> str:
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def stored_note(db: Session, doc, noun: str, listed=None) -> str:
    """The note for a saved document whose terms gave it no rule: it says
    what its due date is, never what it would have been. `listed` is
    whether the list has an entry by that name now (found when None)."""
    text = doc.terms.strip()
    if listed is None:
        listed = find_term(db, text) is not None
    if listed:
        # true of a document saved before the term and of a copy of one (a
        # duplicate, a schedule's next document), which keeps its source's
        # reading (#665)
        head = (
            f'This {noun}\'s terms were first saved before "{text}" was on the '
            "terms list, so it keeps their old reading, not that term's rule"
        )
        tail = ""
    else:
        head = f'"{text}" isn\'t on the terms list'
        tail = f" {ADD_IT}"
    due, on = doc.due_date, doc.date
    if due is None:
        said = "it has no due date"
    elif on is not None and due == on + timedelta(days=FALLBACK_DAYS):
        said = f"its due date was set {FALLBACK_DAYS} days from the {noun} date"
    else:
        said = f"its due date ({_day(due)}) is the one saved with it"
    return f"{head}; {said}.{tail}"


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
    noun = company_noun(db, noun)
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


def describe(rule: Rule) -> str:
    """The rule in words, for the list and a tooltip."""
    if rule.kind == DATE_DRIVEN:
        words = f"Due on day {rule.due_day} of the month"
        if rule.cutoff_days:
            words += (
                ", the next month's when dated fewer than "
                f"{_days(rule.cutoff_days)} before it"
            )
    elif not rule.net_days:
        words = "Due on receipt"
    else:
        words = f"Due in {_days(rule.net_days)}"
    if rule.discount_percent:
        pct = f"{rule.discount_percent.normalize():f}%"
        if rule.kind == DATE_DRIVEN:
            words += f"; {pct} off if paid by day {rule.discount_day}"
            if rule.discount_day > rule.due_day:
                # day 25 for a term due on the 10th is the month before
                words += " of the month before it's due"
        else:
            words += f"; {pct} off if paid within {_days(rule.discount_days)}"
    return words


def _days(n: int) -> str:
    return f"{n} day" if abs(n) == 1 else f"{n} days"


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


def rule_from(db: Session, term_id: Optional[int], text: Optional[str]):
    """The rule of something tied to a term (term_id), else the old
    parser's reading of its text, never the list: an invoice, a bill, or
    a recurring or memorized template."""
    if term_id is not None:
        term = db.get(PaymentTerm, term_id)
        if term is not None:
            return rule_of(term)
    return read_text(text)


def due_for(db: Session, on: date, term_id: Optional[int], text) -> date:
    """The due date that rule gives a document dated `on`; blank or unread
    text is the old 30 days."""
    rule = rule_from(db, term_id, text) if _key(text) else None
    if rule is None:
        return on + timedelta(days=FALLBACK_DAYS)
    return rule.due_date(on)


def document_rule(db: Session, doc) -> Optional[Rule]:
    """The rule a saved invoice or bill was made under: the list entry it
    was tied to when saved (payment_term_id), else what the old parser read
    from its text. A term added to the list later never reaches it."""
    return rule_from(db, getattr(doc, "payment_term_id", None), doc.terms)


def document_due_date(db: Session, doc) -> date:
    """A saved document's due date from its own rule (an edit that moves
    its date, or clears its due date)."""
    return due_for(db, doc.date, getattr(doc, "payment_term_id", None), doc.terms)


def link_terms(db: Session, doc) -> None:
    """Tie the document (or template) to the list entry its terms name now,
    or to none: when the person picks them, on a new one or an edit that
    changes them (retie)."""
    with db.no_autoflush:
        term = find_term(db, doc.terms)
    # a new tie to an inactive term is refused (W-102); one the document
    # already has (its terms retyped) stands
    if term is not None and term.id != doc.payment_term_id:
        refuse_inactive(term)
    doc.payment_term_id = term.id if term is not None else None


def retie(db: Session, doc, before: Optional[str]) -> bool:
    """After an edit: tie anew whenever the terms text changed, by so much
    as a space or a letter's case, to the entry the new text names (#665:
    "Net 45" picked over "net<TAB>45" was taken for the same terms and left
    30 days out). Only the identical text sent back, as the forms send it,
    leaves the document as it was saved. True when it re-tied."""
    if (before or "") == (doc.terms or ""):
        return False
    link_terms(db, doc)
    return True


def _void(doc) -> bool:
    status = getattr(doc, "status", None)
    return getattr(status, "value", status) == "void"


def _paid(doc) -> bool:
    status = getattr(doc, "status", None)
    return getattr(status, "value", status) == "paid"


def document_discount_taken(db: Session, doc) -> Optional[dict]:
    """The early-payment discount already taken on a document (#664): the
    non-void credit memo (an invoice) or vendor credit (a bill) tied to the
    payment that took it, as {"amount", "number"}, or None. Shown in place
    of the offer once the discount is spent."""
    from app.models.bills import Bill

    if isinstance(doc, Bill):
        from app.models.vendor_credits import (
            VendorCredit,
            VendorCreditApplication,
            VendorCreditStatus,
        )

        rows = (
            db.query(VendorCredit)
            .join(
                VendorCreditApplication,
                VendorCreditApplication.vendor_credit_id == VendorCredit.id,
            )
            .filter(
                VendorCreditApplication.bill_id == doc.id,
                VendorCredit.discount_bill_payment_id.isnot(None),
                VendorCredit.status != VendorCreditStatus.VOID,
            )
            .all()
        )
    else:
        from app.models.credit_memos import (
            CreditApplication,
            CreditMemo,
            CreditMemoStatus,
        )

        rows = (
            db.query(CreditMemo)
            .join(CreditApplication, CreditApplication.credit_memo_id == CreditMemo.id)
            .filter(
                CreditApplication.invoice_id == doc.id,
                CreditMemo.discount_payment_id.isnot(None),
                CreditMemo.status != CreditMemoStatus.VOID,
            )
            .all()
        )
    if not rows:
        return None
    amount = sum((Decimal(str(r.total or 0)) for r in rows), Decimal("0"))
    number = getattr(rows[0], "credit_number", None) or getattr(
        rows[0], "memo_number", None
    )
    return {"amount": amount, "number": number}


def _foreign(db: Session, doc) -> bool:
    """A discount is taken in the home currency only, so a foreign-currency
    document offers none."""
    if not getattr(doc, "currency", None):
        return False
    from app.services.currency import home_currency

    return doc.currency.upper() != home_currency(db).upper()


class DocumentTerms:
    """Fills a document response's discount and terms note, reading each
    term once per request (a list of invoices shares a few terms)."""

    def __init__(self, db: Session, noun: str = "invoice"):
        self.db = db
        self.noun = company_noun(db, noun)
        self._terms: dict = {}
        self._listed: dict = {}

    def _rule(self, doc):
        if doc.payment_term_id is not None:
            if doc.payment_term_id not in self._terms:
                term = self.db.get(PaymentTerm, doc.payment_term_id)
                self._terms[doc.payment_term_id] = rule_of(term) if term else None
            rule = self._terms[doc.payment_term_id]
            if rule is not None:
                return rule
        return read_text(doc.terms)

    def fill(self, resp, doc) -> None:
        """discount_percent, discount_date, discount_line, discount_taken
        and terms_note on `resp`, from the rule `doc` was saved under and
        its date. A void document offers no discount (#665); once the
        discount has been taken, or the document is paid, the offer is spent
        and what was taken is shown instead (#664)."""
        if not _key(doc.terms) or doc.date is None or _void(doc):
            return
        from app.schemas.invoices import DiscountTaken

        taken = document_discount_taken(self.db, doc)
        if taken is not None:
            resp.discount_taken = DiscountTaken(**taken)
        rule = self._rule(doc)
        if rule is None:
            key = _key(doc.terms)
            if key not in self._listed:
                self._listed[key] = find_term(self.db, doc.terms) is not None
            resp.terms_note = stored_note(self.db, doc, self.noun, self._listed[key])
            return
        by = rule.discount_date(doc.date)
        if (
            taken is None
            and not _paid(doc)
            and by is not None
            and not _foreign(self.db, doc)
        ):
            resp.discount_percent = rule.discount_percent
            resp.discount_date = by
            resp.discount_line = discount_line(rule.discount_percent, by)


def document_discount_line(doc) -> Optional[str]:
    """The discount line a printed invoice or bill carries under its terms,
    read through the document's own session; None when there is none."""
    from sqlalchemy.orm import object_session

    db = object_session(doc)
    if db is None or doc.date is None or not _key(doc.terms) or _void(doc):
        return None
    # Once it is paid or the discount has been taken, the offer is spent:
    # the printed document shows what was taken, not what could be (#664).
    if _paid(doc) or _foreign(db, doc) or document_discount_taken(db, doc) is not None:
        return None
    rule = document_rule(db, doc)
    if rule is None:
        return None
    return discount_line(rule.discount_percent, rule.discount_date(doc.date))


def term_id_for(db: Session, text: Optional[str]) -> Optional[int]:
    """The list entry the terms of a document made now name, picked by the
    person (a form, the API) or given by its customer or vendor: what a new
    invoice or bill is tied to. A copy (duplicate, a template's next
    document) takes its source's tie instead; an import takes none. A company
    whose list is still empty gets the standard terms first, so a new document
    links to its term instead of none."""
    ensure_seeded(db)
    with db.no_autoflush:
        term = find_term(db, text)
    return term.id if term is not None else None


def inactive_sentence(term: PaymentTerm) -> str:
    return (
        f'"{term.name}" is inactive on the terms list, so nothing new can be '
        "saved under it. Choose other terms, or an administrator can make it "
        "active again under Settings > Payment Terms."
    )


def refuse_inactive(term: PaymentTerm) -> None:
    """The terms of a new document, or terms a person changes on one, may
    name only an active entry (W-102): the forms offer no inactive term,
    and the API now refuses what they don't offer, 422 naming it. A
    document saved under it before keeps its tie; find_term still matches
    inactive entries for that."""
    if term.is_active:
        return
    from fastapi import HTTPException

    raise HTTPException(status_code=422, detail=inactive_sentence(term))


def picked_term_id(
    db: Session, text: Optional[str], payment_term_id: Optional[int]
) -> Optional[int]:
    """The entry a document ties to (#664). With no explicit pick, the entry
    the terms text names now (term_id_for), exactly as before. With one —
    the form's "I picked the list entry" even when the text is the entry's
    own name, so a schedule saved as "Net 10th" before the term existed can
    finally follow it — the picked entry, when it is active and its _key is
    the key of the terms text; otherwise 409 naming both. A pick names a
    deliberate tie, so the identical-text rule that leaves a copy untied
    does not apply to it."""
    if payment_term_id is None:
        with db.no_autoflush:
            term = find_term(db, text)
        if term is None:
            return None
        refuse_inactive(term)
        return term.id
    from fastapi import HTTPException

    term = db.get(PaymentTerm, payment_term_id)
    if term is None or not term.is_active:
        raise HTTPException(
            status_code=409,
            detail=f"Payment term {payment_term_id} is not on the list.",
        )
    if _key(term.name) != _key(text):
        raise HTTPException(
            status_code=409,
            detail=(
                f'The terms "{(text or "").strip()}" are not "{term.name}", so '
                "they can't be tied to it. Change the terms or the term picked."
            ),
        )
    return term.id


def document_terms_dates(db: Session, doc, on: date, noun: str) -> TermsDates:
    """What a saved document's own terms give it dated `on`: its own rule
    (the term it was saved under, or the old reading of its text), as the
    save keeps it while its terms are sent back unchanged (#665: the bill
    form promised a discount from a term an old untied bill doesn't
    follow). A void or foreign-currency one offers no discount."""
    noun = company_noun(db, noun)
    if not _key(doc.terms):
        return TermsDates(due_date=on + timedelta(days=FALLBACK_DAYS))
    rule = document_rule(db, doc)
    if rule is None:
        text = doc.terms.strip()
        note = (
            f'This {noun}\'s terms were first saved before "{text}" was on the '
            "terms list, so it keeps their old reading: the due date is "
            f"{FALLBACK_DAYS} days from the {noun} date."
            if find_term(db, text) is not None
            else unrecognised_note(doc.terms, noun)
        )
        return TermsDates(due_date=on + timedelta(days=FALLBACK_DAYS), note=note)
    by = rule.discount_date(on)
    if by is None or _void(doc) or _foreign(db, doc):
        return TermsDates(due_date=rule.due_date(on))
    return TermsDates(
        due_date=rule.due_date(on),
        discount_percent=rule.discount_percent,
        discount_date=by,
    )
