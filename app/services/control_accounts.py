"""Control accounts — the accounts the posting code resolves BY NUMBER.

Issue #119 (wilsons043, against the v2.10.0 release): renumbering Accounts
Receivable away from 1100 made every later invoice post no journal entry.
The document was still accepted with a 201 and still appeared in the A/R
aging, so the sub-ledger and the general ledger drifted apart in silence.
Two independent faults produced that:

  1. `PUT /api/accounts/{id}` let the number of a structural account change.
  2. The resolvers returned None on a miss, and their callers read None as
     "skip the journal entry" rather than as an error.

The second is the dangerous one — the renumbering was only the easiest way
to reach it. A blank chart of accounts, reachable by bootstrapping from
source outside `manifest_create_company()`, gets there from a different
direction and posts nothing at all.

Note what the trial balance does while this happens: it BALANCES. Debits
equal credits to the cent, because the ledger is internally consistent — it
is simply missing an entry that should exist. No amount of checking the
ledger against itself can find this; only tying the control account to its
sub-ledger can. That is why the fix is to refuse, not to detect.

**This registry is the single authority** for which numbers are structural.
The route guard reads it to decide what may be renumbered, the resolvers
read it to raise a message naming the account, and the startup check reads
it to tell an operator what a company file is missing.

The real fix is a `role` column on Account so posting resolves by role and
a chart can be renumbered freely. That is a schema change and belongs in a
minor release, not a patch; until then the honest behaviour is to refuse
the edit and say why.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.accounts import Account


class MissingControlAccount(LookupError):
    """A required control account is not in this company's chart.

    Raised instead of returning None so a posting path cannot quietly skip
    its journal entry. `app/main.py` renders it as a 409 naming the account,
    which is actionable: the operator fixes the chart and retries.
    """

    def __init__(self, number: str, name: str, purpose: str = ""):
        self.number = number
        self.name = name
        self.purpose = purpose
        detail = (
            f"The account this posting needs is missing from the chart of "
            f"accounts: {number} {name}."
        )
        if purpose:
            detail += f" It is used for {purpose}."
        detail += (
            " Restore it with that exact number (Chart of Accounts → New "
            "Account), then try again. Nothing was posted."
        )
        super().__init__(detail)
        # This sentence is for the operator: safe_message passes it on
        # rather than answering "unexpected error" for a LookupError.
        self.user_text = detail


# number -> (name as seeded, what the posting code uses it for)
#
# Every entry here is resolved by literal number somewhere in app/. Renaming
# one is safe; renumbering it is not, which is what the route guard enforces.
CONTROL_ACCOUNTS: dict[str, tuple[str, str]] = {
    "1000": ("Checking", "the default bank account for deposits and payments"),
    "1100": ("Accounts Receivable", "what customers owe — every invoice and payment"),
    "1200": ("Undeposited Funds", "payments received but not yet deposited"),
    "1300": ("Inventory", "the inventory asset behind item purchases and sales"),
    "2000": ("Accounts Payable", "what you owe vendors — every bill and bill payment"),
    "2100": ("Credit Card", "credit-card charges and the card's balance"),
    "2200": ("Sales Tax Payable", "sales tax collected on invoices and receipts"),
    "3200": (
        "Retained Earnings",
        "the balancing account for imported opening balances",
    ),
    "4000": ("Service Income", "the default income account for invoice lines"),
    "4800": ("Late Fee Income", "finance charges added to overdue invoices"),
    "5000": ("Cost of Goods Sold", "the cost side of an inventory item's sale"),
    "5900": ("Inventory Adjustments", "write-offs and quantity corrections"),
    "6000": (
        "Advertising & Marketing",
        "payroll expense when 6110 or 6120 is missing from an older chart",
    ),
    "6120": ("Payroll Tax Expense", "employer taxes posted by job costing"),
    "6150": ("Employee Benefits Expense", "benefit costs posted by job costing"),
}


def is_control_number(number: str | None) -> bool:
    return bool(number) and str(number) in CONTROL_ACCOUNTS


def describe(number: str) -> tuple[str, str]:
    """(name, purpose) for a control number; ("", "") if it is not one."""
    return CONTROL_ACCOUNTS.get(str(number), ("", ""))


def resolve(db: Session, number: str) -> int:
    """The id of a control account, or raise.

    Never returns None. A caller that wants tolerance — a report, an export
    that only needs a display name — calls `find` instead and says so.
    """
    acct = db.query(Account).filter(Account.account_number == str(number)).first()
    if acct is None:
        name, purpose = describe(number)
        raise MissingControlAccount(str(number), name or "control account", purpose)
    return acct.id


def find(db: Session, number: str) -> int | None:
    """The id of an account by number, or None — for the paths that are
    genuinely tolerant (display names in exports, a report that shows a
    zero). Never use this where a journal entry depends on the result."""
    acct = db.query(Account).filter(Account.account_number == str(number)).first()
    return acct.id if acct else None


def seeded_numbers() -> set[str]:
    """The control accounts the standard chart actually creates.

    4800 Late Fee Income and 5900 Inventory Adjustments are created on
    demand by the code that needs them, so a healthy chart legitimately
    lacks them — the boot check must not cry wolf about those. They stay in
    the registry because they are still resolved by number, so renumbering
    one once it exists is still a trap the route guard should refuse.
    """
    from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS

    seeded = {entry["account_number"] for entry in CHART_OF_ACCOUNTS}
    return {n for n in CONTROL_ACCOUNTS if n in seeded}


def missing(db: Session) -> list[tuple[str, str]]:
    """[(number, name)] for every SEEDED control account absent from this
    chart — what a company file should have and does not.

    The boot check reports these once rather than waiting for the first
    document to fail, and it is what catches a chart that was never seeded
    at all (issue #119, reporter's note 1).
    """
    expected = seeded_numbers()
    present = {
        n
        for (n,) in db.query(Account.account_number)
        .filter(Account.account_number.in_(expected))
        .all()
    }
    return [
        (number, CONTROL_ACCOUNTS[number][0])
        for number in CONTROL_ACCOUNTS
        if number in expected and number not in present
    ]
