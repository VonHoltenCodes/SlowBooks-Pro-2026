"""Where a pay run posts (#620).

One table of the payroll posting pieces: the settings key that names an
account for each, the kinds of account it takes, and the seeded account it
posts to when none is chosen (the numbers process_pay_run always used, so a
company that chooses nothing posts exactly as before). The settings save
checks a choice against this table, the Settings page draws its pickers from
it (GET /api/settings/payroll-accounts) and process_pay_run resolves every
piece through it.

Net pay is the piece the issue was about: it always credited 1000 Checking,
so a company that pays the wages later had no way to hold them in 2300
Payroll Liabilities. It now takes a bank account or a liability. The other
pieces take an expense (or cost of goods sold) account or a liability, as
the seeded one is.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.services.account_label import label_for
from app.services.control_accounts import is_control
from app.services.settings_service import get_setting_raw

# Where the pickers are, for a sentence that sends someone to fix a choice.
SETTINGS_PLACE = "Settings → Payroll Accounts"


@dataclass(frozen=True)
class PostingPiece:
    key: str  # the settings key holding the chosen account id ("" = default)
    label: str  # the piece, as the page and a refusal name it
    kinds: tuple[str, ...]  # the account kinds it takes: bank | liability | expense
    numbers: tuple[str, ...]  # the seeded defaults, first one found wins
    hint: str = ""  # one sentence under the picker


PIECES: tuple[PostingPiece, ...] = (
    PostingPiece(
        "payroll_net_pay_account_id",
        "Net pay",
        ("bank", "liability"),
        ("1000",),
        "The bank account the net pay leaves when the run is processed, or a "
        "liability account (2300 Payroll Liabilities) when the wages are paid "
        "later.",
    ),
    PostingPiece(
        "payroll_wages_account_id",
        "Gross wages",
        ("expense",),
        ("6110", "6000"),
    ),
    PostingPiece(
        "payroll_employer_tax_account_id",
        "Employer payroll taxes",
        ("expense",),
        ("6120", "6000"),
    ),
    PostingPiece(
        "payroll_federal_withholding_account_id",
        "Federal income tax withheld",
        ("liability",),
        ("2310", "2300"),
    ),
    PostingPiece(
        "payroll_state_withholding_account_id",
        "State income tax withheld",
        ("liability",),
        ("2320", "2300"),
    ),
    PostingPiece(
        "payroll_social_security_account_id",
        "Social Security payable",
        ("liability",),
        ("2330", "2300"),
        "The share withheld and the employer's share, together.",
    ),
    PostingPiece(
        "payroll_medicare_account_id",
        "Medicare payable",
        ("liability",),
        ("2340", "2300"),
        "Both shares, as for Social Security.",
    ),
    PostingPiece(
        "payroll_futa_account_id",
        "FUTA payable",
        ("liability",),
        ("2350", "2300"),
    ),
    PostingPiece(
        "payroll_suta_account_id",
        "SUTA payable",
        ("liability",),
        ("2360", "2300"),
    ),
    PostingPiece(
        "payroll_other_deductions_account_id",
        "Other payroll deductions payable",
        ("liability",),
        ("2370", "2300"),
        "Garnishments, state taxes other than income tax, and deductions typed "
        "on a stub with no benefit code.",
    ),
)
PIECE_BY_KEY = {piece.key: piece for piece in PIECES}
KINDS = ("bank", "liability", "expense")

_WANTED = {
    ("bank", "liability"): "a bank account or a liability account",
    ("expense",): "an expense or cost of goods sold account",
    ("liability",): "a liability account",
}


def wanted(piece: PostingPiece) -> str:
    """What the piece takes, for a refusal: "a liability account"."""
    return _WANTED[piece.kinds]


def kind_of(acct: Account) -> str | None:
    """The posting kind an account is: "bank" (an asset flagged as a bank),
    "liability" (a liability that is not a credit card), "expense" (an
    expense or cost of goods sold account); None for anything else."""
    if acct.account_type == AccountType.ASSET:
        return "bank" if acct.bank_kind == "bank" else None
    if acct.account_type == AccountType.LIABILITY:
        return None if acct.bank_kind else "liability"
    if acct.account_type in (AccountType.EXPENSE, AccountType.COGS):
        return "expense"
    return None


def account_problem(acct: Account | None, piece: PostingPiece, db=None) -> str:
    """The sentence that refuses `acct` for `piece`, or "" when it will do:
    it must exist, be active, be a kind the piece takes, and, for a payable,
    not be a credit card or a control liability (2000 Accounts Payable, 2200
    Sales Tax Payable), whose balance is kept by its own documents."""
    if acct is None:
        return f"The chosen {piece.label} account does not exist."
    if acct.is_active is False:
        return f"The chosen {piece.label} account is inactive."
    if "liability" in piece.kinds and acct.bank_kind == "credit_card":
        return f"The {piece.label} account cannot be a credit card."
    kind = kind_of(acct)
    if kind not in piece.kinds:
        return f"The {piece.label} account must be {wanted(piece)}."
    if kind == "liability" and is_control(
        acct.account_number, acct.account_type, acct.name
    ):
        return (
            f"The {piece.label} account cannot be {label_for(acct, db)}: that "
            "account's balance is kept by its own documents. Choose another "
            "liability account."
        )
    return ""


def parse_id(value) -> int | None:
    """The account id a setting or a form holds, or None when it is not one."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def default_account(db: Session, piece: PostingPiece) -> Account | None:
    """The seeded account the piece posts to when none is chosen: the first
    of its numbers that is in the chart (6110 Wages & Salaries, else 6000 on
    an un-migrated chart; a payable, else the umbrella 2300)."""
    for number in piece.numbers:
        acct = db.query(Account).filter(Account.account_number == number).first()
        if acct is not None:
            return acct
    return None


def eligible_accounts(db: Session, kind: str) -> list[Account]:
    """Every active account a piece of this kind may be pointed at, in chart
    order: the rule account_problem enforces, for the pickers."""
    rows = (
        db.query(Account)
        .filter(Account.is_active.isnot(False))
        .order_by(Account.account_number, Account.name)
        .all()
    )
    return [
        acct
        for acct in rows
        if kind_of(acct) == kind
        and not (
            kind == "liability"
            and is_control(acct.account_number, acct.account_type, acct.name)
        )
    ]


class PayrollAccountProblem(ValueError):
    """A chosen posting account that no longer works; str() says which and
    where to fix it."""


def posting_accounts(db: Session) -> dict[str, int | None]:
    """The account id each piece posts to: the chosen account when one is
    set, else the first seeded default in the chart (None when neither is
    there, which process_pay_run reports as it always has). A chosen account
    that no longer works (deleted, made inactive, retyped since it was chosen)
    raises PayrollAccountProblem rather than posting the piece somewhere
    else."""
    out: dict[str, int | None] = {}
    for piece in PIECES:
        raw = get_setting_raw(db, piece.key)
        if raw:
            acct_id = parse_id(raw)
            acct = db.get(Account, acct_id) if acct_id is not None else None
            problem = account_problem(acct, piece, db)
            if problem:
                raise PayrollAccountProblem(
                    f"{problem} Choose another under {SETTINGS_PLACE}, then "
                    "process the pay run again."
                )
            out[piece.key] = acct.id
        else:
            acct = default_account(db, piece)
            out[piece.key] = acct.id if acct is not None else None
    return out


def describe(db: Session) -> dict:
    """What the Settings page draws (#620): the pieces in posting order, each
    with its key, label, hint, the kinds it takes and what that is in words,
    the account chosen for it ("" for the default), the seeded default it
    posts to when none is chosen, and, when the chosen account no longer
    works, the sentence that says so; and, per kind, the active accounts a
    piece of that kind may be pointed at, labelled as the company labels
    accounts."""
    from app.services.settings_service import use_account_numbers
    from app.services.account_label import account_label

    numbered = use_account_numbers(db)

    def words(acct):
        return {
            "id": acct.id,
            "label": account_label(acct.account_number, acct.name, numbered, " "),
        }

    pieces = []
    for piece in PIECES:
        raw = get_setting_raw(db, piece.key) or ""
        default = default_account(db, piece)
        problem = ""
        if raw:
            acct_id = parse_id(raw)
            acct = db.get(Account, acct_id) if acct_id is not None else None
            problem = account_problem(acct, piece, db)
        pieces.append(
            {
                "key": piece.key,
                "label": piece.label,
                "hint": piece.hint,
                "kinds": list(piece.kinds),
                "wanted": wanted(piece),
                "chosen": raw,
                "problem": problem,
                "default": words(default) if default is not None else None,
            }
        )
    choices = {
        kind: [words(acct) for acct in eligible_accounts(db, kind)] for kind in KINDS
    }
    return {"pieces": pieces, "choices": choices}
