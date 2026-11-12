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
            " Restore it with that exact number and name (Chart of Accounts → "
            "New Account), then try again. Nothing was posted."
        )
        super().__init__(detail)
        # This sentence is for the operator: safe_message passes it on
        # rather than answering "unexpected error" for a LookupError.
        self.user_text = detail


# number -> (name as seeded, the account type the posting code expects, what
# the posting code uses it for)
#
# Every entry here is resolved by literal number somewhere in app/. Renaming
# one is safe; renumbering it is not, which is what the route guard enforces.
#
# The TYPE is load-bearing too. A number is only a control account when the
# account also carries the type the posting code expects: Opening Balance
# Equity is resolved as an EQUITY 3900, so an imported chart's expense "3900
# Miscellaneous Expense" is an ordinary account, not a control one — it must
# not be flagged, locked, or refused a renumber. The resolvers, the route
# guard and the chart importer all match on number AND type (see is_control).
CONTROL_ACCOUNTS: dict[str, tuple[str, str, str]] = {
    "1000": ("Checking", "asset", "the default bank account for deposits and payments"),
    "1100": (
        "Accounts Receivable",
        "asset",
        "what customers owe — every invoice and payment",
    ),
    "1200": ("Undeposited Funds", "asset", "payments received but not yet deposited"),
    "1300": (
        "Inventory",
        "asset",
        "the inventory asset behind item purchases and sales",
    ),
    "2000": (
        "Accounts Payable",
        "liability",
        "what you owe vendors — every bill and bill payment",
    ),
    "2010": (
        "Accrued Job Costs",
        "liability",
        "job costs accrued before the vendor's bill arrives; each accrual "
        "reverses itself on its Reverses on date",
    ),
    "2100": ("Credit Card", "liability", "credit-card charges and the card's balance"),
    "2200": (
        "Sales Tax Payable",
        "liability",
        "sales tax collected on invoices and receipts",
    ),
    "3200": (
        "Retained Earnings",
        "equity",
        "the balancing account for imported opening balances",
    ),
    "3900": (
        "Opening Balance Equity",
        "equity",
        "the offset for opening balances — bank and card accounts and an "
        "item's opening stock",
    ),
    "4000": (
        "Service Income",
        "income",
        "the default income account for invoice lines",
    ),
    "4800": ("Late Fee Income", "income", "finance charges added to overdue invoices"),
    "5000": ("Cost of Goods Sold", "cogs", "the cost side of an inventory item's sale"),
    "5900": ("Inventory Adjustments", "expense", "write-offs and quantity corrections"),
    "6000": (
        "Advertising & Marketing",
        "expense",
        "payroll expense when 6110 or 6120 is missing from an older chart",
    ),
    "6120": ("Payroll Tax Expense", "expense", "employer taxes posted by job costing"),
    "6150": (
        "Employee Benefits Expense",
        "expense",
        "benefit costs posted by job costing",
    ),
}


# The registry numbers the journal-style checks name (#897): what a job cost
# or journal entry may not post to, and the accrual account it may credit.
RECEIVABLE = "1100"
PAYABLE = "2000"
ACCRUED_JOB_COSTS = "2010"

# Seeded in a new chart but created on first use in an older one, so the boot
# check (`missing`) does not report its absence: a company that never accrues
# a job cost never needs it (#897). 4800 and 5900 are on demand by not being
# seeded at all.
ON_DEMAND = frozenset({ACCRUED_JOB_COSTS})


def _type_value(account_type) -> str | None:
    """The string value of an AccountType enum, a plain string, or None."""
    if account_type is None:
        return None
    return getattr(account_type, "value", account_type)


def is_control_number(number: str | None) -> bool:
    """True when `number` is a control-account number, type aside.

    Use this only where the account type is not available or not relevant —
    the seeded-gap boot check, the registry tripwire test. The user-facing
    paths (the route guard, the response flag, the chart importer) call
    `is_control`, which also matches the type, so an imported 3900 expense is
    not mistaken for Opening Balance Equity.
    """
    return bool(number) and str(number) in CONTROL_ACCOUNTS


def is_control(number: str | None, account_type, name: str | None = None) -> bool:
    """True when `number` is a control number AND `account_type` is the type
    the posting code expects for it.

    An on-demand number (ON_DEMAND: 2010 Accrued Job Costs) also needs its
    registry `name`: an older chart may already hold an account of its own on
    2010 (a loan, a payroll liability), and that one stays an ordinary
    account, free to renumber and to pick for payroll. A caller that does not
    pass the name reads such a number as ordinary.

    The registry is keyed on number, but the posting code finds, e.g.,
    Opening Balance Equity by an EQUITY 3900. An account numbered 3900 that
    is an expense is an ordinary account the registry does not govern: it is
    not flagged, not type-locked on import, and can be renumbered or deleted.
    """
    entry = CONTROL_ACCOUNTS.get(str(number)) if number else None
    if entry is None:
        return False
    if str(number) in ON_DEMAND and (
        name is None or name.strip().casefold() != entry[0].casefold()
    ):
        return False
    return _type_value(account_type) == entry[1]


def expected_type(number: str | None) -> str:
    """The account type the posting code expects for a control number, or ""."""
    entry = CONTROL_ACCOUNTS.get(str(number)) if number else None
    return entry[1] if entry else ""


def describe(number: str) -> tuple[str, str]:
    """(name, purpose) for a control number; ("", "") if it is not one."""
    entry = CONTROL_ACCOUNTS.get(str(number))
    return (entry[0], entry[2]) if entry else ("", "")


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def restore_conflict(number, name, account_type) -> str:
    """Why a new account of this number/name/type may not take a control-account
    slot — "" when it is allowed.

    Allowed means either the number is not a control number at all, or the row
    IS the control account the slot is reserved for (its registry name,
    case-insensitively, and its expected type): that restores an empty slot,
    which is what MissingControlAccount tells the operator to do. Everything
    else is a foreign account landing on a reserved number, which would
    silently become where that posting path resolves.

    Returns one of two reasons so a caller can pick a status code: a name
    mismatch ("reserved …") or a right-name/wrong-type mismatch ("… must be
    a(n) <type> account"). Shared by the accounts API and the chart importer so
    both refuse the same thing for the same reason.
    """
    entry = CONTROL_ACCOUNTS.get(str(number)) if number else None
    if entry is None:
        return ""
    control_name, control_type, purpose = entry
    name_ok = bool(name) and name.strip().casefold() == control_name.casefold()
    type_ok = _type_value(account_type) == control_type
    if name_ok and type_ok:
        return ""
    if name_ok and not type_ok:
        return (
            f"{number} {control_name} must be {_article(control_type)} "
            f"{control_type} account — the "
            f"software posts to it by number for {purpose}. Give it that type, "
            f"or use a different number."
        )
    return (
        f"Account number {number} is reserved for {control_name} — the software "
        f"posts to it by number for {purpose}, so another account cannot use "
        f"it. Choose a different number."
    )


def resolve(db: Session, number: str) -> int:
    """The id of a control account, or raise.

    Matched on number AND the type the posting code expects (an imported
    chart may reuse a number for something else), the same rule the route
    guard and the chart importer apply. Never returns None. A caller that
    wants tolerance — a report, an export that only needs a display name —
    calls `find` instead and says so.
    """
    want_type = expected_type(number)
    acct = db.query(Account).filter(Account.account_number == str(number)).first()
    if acct is None or (want_type and _type_value(acct.account_type) != want_type):
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


def ensure(db: Session, number: str) -> int:
    """The id of an on-demand control account (ON_DEMAND), created with its
    registry name and type when the chart has none (#897: a chart from before
    2.32 gets 2010 Accrued Job Costs on its first accrual).

    A different account already on the number is never taken over: it is
    refused, naming it, so an accrual cannot land on someone's loan. Raises
    MissingControlAccount (409) for that, as `resolve` does."""
    from app.models.accounts import AccountType

    name, want_type, purpose = CONTROL_ACCOUNTS[str(number)]
    acct = db.query(Account).filter(Account.account_number == str(number)).first()
    if acct is None:
        acct = Account(
            name=name,
            account_number=str(number),
            account_type=AccountType(want_type),
            is_system=True,
        )
        db.add(acct)
        db.flush()
        return acct.id
    if is_control(acct.account_number, acct.account_type, acct.name):
        return acct.id
    err = MissingControlAccount(str(number), name, purpose)
    detail = (
        f"Account number {number} is {acct.name} in this chart, so {number} "
        f"{name} can't be made for {purpose}. Renumber {acct.name} (Chart of "
        "Accounts, Edit), then try again. Nothing was posted."
    )
    err.args = (detail,)
    err.user_text = detail
    raise err


def is_receivable_or_payable(account) -> str | None:
    """RECEIVABLE or PAYABLE when `account` is the chart's Accounts
    Receivable or Accounts Payable control account (number and type), else
    None: the accounts a job cost or journal entry may not post to (#897)."""
    if account is None:
        return None
    number = str(account.account_number or "")
    if number in (RECEIVABLE, PAYABLE) and is_control(number, account.account_type):
        return number
    return None


def missing(db: Session) -> list[tuple[str, str]]:
    """[(number, name)] for every SEEDED control account absent from this
    chart — what a company file should have and does not.

    The boot check reports these once rather than waiting for the first
    document to fail, and it is what catches a chart that was never seeded
    at all (issue #119, reporter's note 1).
    """
    # 2010 Accrued Job Costs is seeded but optional (ON_DEMAND): an older
    # chart that never accrued a cost is not missing anything
    expected = seeded_numbers() - ON_DEMAND
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
