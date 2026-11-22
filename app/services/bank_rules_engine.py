# ============================================================================
# Bank rule application — shared by every bank-feed importer (OFX, CSV,
# SimpleFIN) and the Bank Rules page.
#
# A rule SUGGESTS a category for a statement line that matches its payee
# pattern. It never changes the line's status and never posts: adding to
# the books is one explicit click ("Add" / "Add all categorised"), so
# nothing reaches the ledger silently (issue #114).
# ============================================================================

import logging
import re
from typing import Mapping, Optional

from sqlalchemy.orm import Session

from app.models.banking import BankTransaction

logger = logging.getLogger(__name__)


def payout_suggestion(
    db: Session,
    settings: Mapping[str, str],
    payee: Optional[str],
    line: Optional[BankTransaction] = None,
) -> Optional[dict]:
    """A deposit whose payee names Stripe, PayPal or Square (whole word,
    case-insensitive), on a company with that provider's clearing on, can
    be recorded as a payout of that provider (#584).

    Advisory only and a pure read: it creates no account, commits nothing,
    and posts nothing. Returns the provider, its display name and the
    clearing balance (shown as information; the modal pre-fills the gross
    with the deposit's net), or None.

    Clearing turned off still offers it while the clearing account holds a
    balance. When `line` is given and a ledger entry already matches it (a
    payout recorded by hand, whose feed line arrived late), nothing is
    suggested: that deposit wants Match, not a second posting. The
    caller gates on a deposit (amount > 0) and an unmatched line; this only
    decides provider and reads the clearing balance.

    No clearing account yet means nothing has cleared, so there is nothing
    to pay out and the suggestion is None — the chart is left untouched by
    merely listing the review queue."""
    from datetime import date as _date

    from app.services.bank_register import balance_as_of
    from app.services.payments.clearing import (
        DISPLAY_NAMES,
        PROVIDER_NAMES,
        clearing_enabled,
        existing_clearing_account,
    )

    text = (payee or "").lower()
    for provider in PROVIDER_NAMES:
        if not re.search(rf"\b{re.escape(provider)}\b", text):
            continue
        acct = existing_clearing_account(db, provider)
        if acct is None:
            return None
        balance, _ = balance_as_of(db, acct, _date.today())
        if not clearing_enabled(settings, provider) and balance <= 0:
            continue
        if line is not None and _has_ledger_match(db, line):
            return None
        return {
            "provider": provider,
            "display_name": DISPLAY_NAMES[provider],
            "clearing_balance": balance,
        }
    return None


def _has_ledger_match(db: Session, line: BankTransaction) -> bool:
    """An unlinked ledger entry on the feed's account already has this
    line's amount near its date, so the line is a Match, not a new posting."""
    from app.services import bank_matching as m

    return bool(
        m.candidate_lines(db, m._feed_account(db, line).id, line.amount, line.date)
    )


# A rule's payee pattern (#1064, NEW-259): `*` stands for any run of
# characters, as people type it ("T-MOBILE*"), and `\*` is a star itself,
# the one banks print ("SQ \*JOES"); `\\` is a backslash before a star.
# Every other character, a backslash before anything else included, is
# itself. A rule made before the wildcard had its stars escaped by the
# migration (c1064a5e7b2d), so it files exactly what it filed before.
def pattern_parts(pattern: str | None) -> list[str | None]:
    """The pattern as text runs and wildcards (None), in order."""
    parts: list[str | None] = []
    text: list[str] = []
    chars = (pattern or "").strip()
    i = 0
    while i < len(chars):
        c = chars[i]
        if c == "\\" and i + 1 < len(chars) and chars[i + 1] in "*\\":
            text.append(chars[i + 1])
            i += 2
            continue
        if c == "*":
            if text:
                parts.append("".join(text))
                text = []
            if not parts or parts[-1] is not None:
                parts.append(None)
        else:
            text.append(c)
        i += 1
    if text:
        parts.append("".join(text))
    return parts


def escape_pattern(text: str | None) -> str:
    """A pattern that matches `text` as it is written, every star in it a
    star itself: what a pattern made from a payee or a vendor's name is."""
    text = text or ""
    out = []
    for i, c in enumerate(text):
        if c == "*":
            out.append("\\*")
        elif c == "\\" and i + 1 < len(text) and text[i + 1] in "*\\":
            out.append("\\\\")
        else:
            out.append(c)
    return "".join(out)


PATTERN_EMPTY = (
    "A payee pattern needs some of the text the bank prints; a pattern of "
    "only * or spaces would match nothing. To match a star the bank prints, "
    "type \\*."
)


def pattern_problem(pattern: str | None) -> str | None:
    """The sentence when a pattern has no text to match, else None."""
    if any(p is not None and p.strip() for p in pattern_parts(pattern)):
        return None
    return PATTERN_EMPTY


def _matches(rule_type: str, pattern: str, payee: str, literal: bool = False) -> bool:
    """`payee` is lower-cased. A `*` in the pattern stands for any run of
    characters and `\\*` is a star itself (pattern_parts). `literal` reads
    the whole pattern as plain text: a vendor's own name, whose star is the
    one in the name."""
    if literal:
        pattern = escape_pattern((pattern or "").strip())
    if pattern_problem(pattern):
        return False
    parts = [p if p is None else p.lower() for p in pattern_parts(pattern)]
    if None in parts:
        rx = "".join(".*" if p is None else re.escape(p) for p in parts)
        if rule_type == "exact":
            return re.fullmatch(rx, payee, re.S) is not None
        if rule_type == "starts_with":
            return re.match(rx, payee, re.S) is not None
        if rule_type == "contains":
            return re.search(rx, payee, re.S) is not None
        return False
    text = "".join(parts)
    if rule_type == "contains":
        return text in payee
    if rule_type == "starts_with":
        return payee.startswith(text)
    if rule_type == "exact":
        return payee == text
    return False


def _hit(rule, payee: str, vendor_names: dict | None = None) -> bool:
    """The rule's pattern matches the payee (lower-cased) — or, for a rule
    that names a vendor (#493), the vendor's own name does, the same way:
    a vendor renamed after its rule was made is still found."""
    if _matches(rule.rule_type, rule.pattern, payee):
        return True
    if rule.vendor_id and vendor_names:
        return _matches(
            rule.rule_type, vendor_names.get(rule.vendor_id), payee, literal=True
        )
    return False


def _active_rules(db: Session):
    from app.models.bank_rules import BankRule

    return (
        db.query(BankRule)
        .filter(BankRule.is_active)
        .order_by(BankRule.priority.desc(), BankRule.id)
        .all()
    )


def _vendor_names(db: Session, rules) -> dict:
    from app.models.contacts import Vendor

    ids = {r.vendor_id for r in rules if r.vendor_id}
    if not ids:
        return {}
    return {v.id: v.name for v in db.query(Vendor).filter(Vendor.id.in_(ids))}


def rule_for(db: Session, payee: str | None):
    """The active rule a statement line with this payee follows (highest
    priority first, first hit wins), or None."""
    rules = _active_rules(db)
    names = _vendor_names(db, rules)
    text = (payee or "").lower()
    return next((r for r in rules if _hit(r, text, names)), None)


def rule_dimensions(db: Session, payee: str | None) -> tuple[int | None, int | None]:
    """(class id, job id) the rule a statement line with this payee follows
    names (#675), each None when it names none or when the class has since
    been archived or the job made inactive (the line then comes in without
    it, as a line no rule names does). Like the vendor, they are not kept on
    the statement line: Add asks the rule (bank_matching.add)."""
    rule = rule_for(db, payee)
    if rule is None:
        return None, None
    from app.models.classes import TxnClass
    from app.models.jobs import Job

    class_id = job_id = None
    if rule.class_id:
        cls = db.get(TxnClass, rule.class_id)
        if cls is not None and not cls.is_archived:
            class_id = cls.id
    if rule.job_id:
        job = db.get(Job, rule.job_id)
        if job is not None and job.is_active is not False:
            job_id = job.id
    return class_id, job_id


def rule_customer(db: Session, rule, amount):
    """The customer `rule` gives a statement line of `amount` (#1075): the
    rule's customer on money in, None on money out (the rule's vendor is
    its name there) and None when the customer is gone or inactive (the
    line then comes in without it, as a line whose rule names none)."""
    if rule is None or not rule.customer_id or not amount or amount <= 0:
        return None
    from app.models.contacts import Customer

    customer = db.get(Customer, rule.customer_id)
    if customer is None or customer.is_active is False:
        return None
    return customer


def rule_memo(rule) -> str | None:
    """The memo `rule` gives a line it matches (#1075), or None."""
    return ((rule.memo or "").strip() or None) if rule is not None else None


def rule_picks(db: Session, rows) -> dict[int, dict]:
    """What the rule each unmatched statement line follows gives it besides
    the category, by line id: {"class_id", "job_id", "vendor_id",
    "vendor_name", "customer_id", "customer_name", "memo"}, each None when
    the rule names none (or the class is archived, the job inactive). The
    customer is given to money in, and there the rule's vendor only when it
    names no customer (rule_customer, #1075). The review row is drawn with them, so it
    shows the class and the vendor Add will post with (rule_dimensions and
    _entry_vendor in bank_matching ask the same rule)."""
    rows = [r for r in rows if r.match_status == "unmatched"]
    if not rows:
        return {}
    rules = _active_rules(db)
    if not rules:
        return {}
    from app.models.classes import TxnClass
    from app.models.contacts import Vendor
    from app.models.jobs import Job

    names = _vendor_names(db, rules)
    out = {}
    for line in rows:
        text = (line.payee or "").lower()
        rule = next((r for r in rules if _hit(r, text, names)), None)
        if rule is None:
            continue
        cls = db.get(TxnClass, rule.class_id) if rule.class_id else None
        job = db.get(Job, rule.job_id) if rule.job_id else None
        customer = rule_customer(db, rule, line.amount)
        vendor = (
            db.get(Vendor, rule.vendor_id)
            if rule.vendor_id and customer is None
            else None
        )
        out[line.id] = {
            "class_id": cls.id if cls is not None and not cls.is_archived else None,
            "job_id": (
                job.id if job is not None and job.is_active is not False else None
            ),
            "vendor_id": vendor.id if vendor is not None else None,
            "vendor_name": vendor.name if vendor is not None else None,
            "customer_id": customer.id if customer is not None else None,
            "customer_name": customer.name if customer is not None else None,
            "memo": rule_memo(rule),
        }
    return out


def apply_bank_rules(db: Session, bank_account_id: int | None = None) -> int:
    """Categorise unmatched statement lines by the active rules (highest
    priority first, first hit wins). Returns how many lines got a category.
    Commits when anything changed. The vendor, class and job a rule names
    are not kept on the line: Add asks the same rule again
    (bank_matching.add, rule_dimensions)."""
    rules = _active_rules(db)
    if not rules:
        return 0
    names = _vendor_names(db, rules)
    q = db.query(BankTransaction).filter(BankTransaction.match_status == "unmatched")
    if bank_account_id:
        q = q.filter(BankTransaction.bank_account_id == bank_account_id)
    categorised = 0
    for txn in q.all():
        payee = (txn.payee or "").lower()
        for rule in rules:
            if _hit(rule, payee, names):
                if rule.account_id and txn.category_account_id != rule.account_id:
                    txn.category_account_id = rule.account_id
                    categorised += 1
                break
    if categorised:
        db.commit()
    return categorised
