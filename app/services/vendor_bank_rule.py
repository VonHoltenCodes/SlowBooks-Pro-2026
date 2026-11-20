"""A vendor's bank rule, set from the vendor form (#493).

A bookkeeper setting up a vendor usually knows the account every payment
to it takes. The vendor form's "Bank feed: lines from this payee post to
<the default expense account>" says so once: it makes the vendor's bank
rule (pattern = the vendor's name, Contains, any case; vendor_id = the
vendor; account = the default expense account) or updates the one it has,
and turns it off when unticked. The rule is an ordinary bank rule: the
Bank Rules page lists and edits it, and the engine treats it like any
other, naming the vendor on the lines it matches."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.bank_rules import BankRule
from app.models.contacts import Vendor
from app.services.bank_rules_engine import escape_pattern


def vendor_rules(db: Session, vendor_ids) -> dict[int, BankRule]:
    """Each vendor's active bank rule (the first made, when several)."""
    ids = [i for i in vendor_ids if i]
    if not ids:
        return {}
    out: dict[int, BankRule] = {}
    for rule in (
        db.query(BankRule)
        .filter(BankRule.vendor_id.in_(ids), BankRule.is_active)
        .order_by(BankRule.id)
    ):
        out.setdefault(rule.vendor_id, rule)
    return out


def set_vendor_rule(db: Session, vendor: Vendor, wanted: bool) -> BankRule | None:
    """Make, update or turn off the vendor's bank rule. On: the vendor's
    rule (an inactive one is turned back on) posts to its default expense
    account; a pattern edited on the Bank Rules page is kept. Off: every
    active rule naming the vendor is turned off, not deleted."""
    rules = (
        db.query(BankRule)
        .filter(BankRule.vendor_id == vendor.id)
        .order_by(BankRule.is_active.desc(), BankRule.id)
        .all()
    )
    if not wanted:
        for rule in rules:
            rule.is_active = False
        return None
    if not vendor.default_expense_account_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Choose the default expense or COGS account first: a bank-feed "
                "rule posts this vendor's lines there."
            ),
        )
    if rules:
        rule = rules[0]
        rule.is_active = True
    else:
        rule = BankRule(
            name=vendor.name,
            # the name as it is written: a star in it is a star (NEW-259)
            pattern=escape_pattern(vendor.name),
            rule_type="contains",
            priority=0,
            vendor_id=vendor.id,
        )
        db.add(rule)
    rule.account_id = vendor.default_expense_account_id
    db.flush()
    return rule
