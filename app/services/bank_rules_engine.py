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


def _matches(rule_type: str, pattern: str, payee: str) -> bool:
    pattern = (pattern or "").strip().lower()
    if not pattern:
        return False
    if rule_type == "contains":
        return pattern in payee
    if rule_type == "starts_with":
        return payee.startswith(pattern)
    if rule_type == "exact":
        return payee == pattern
    return False


def _hit(rule, payee: str, vendor_names: dict | None = None) -> bool:
    """The rule's pattern matches the payee (lower-cased) — or, for a rule
    that names a vendor (#493), the vendor's own name does, the same way:
    a vendor renamed after its rule was made is still found."""
    if _matches(rule.rule_type, rule.pattern, payee):
        return True
    if rule.vendor_id and vendor_names:
        return _matches(rule.rule_type, vendor_names.get(rule.vendor_id), payee)
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
