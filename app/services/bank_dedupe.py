"""A statement line is the same line whatever file it came in (#705).

A month imported as CSV and then as OFX (or the other way round, or from
a bank feed) used to land twice: OFX lines were deduplicated by FITID and
CSV lines by a key made from the CSV's own row, so neither format saw the
other's lines. Here a line already on the account is recognised by its
date, amount and normalised description, as well as by its id (an OFX
FITID, a feed's transaction id, a CSV row key).

Two lines that are otherwise identical in one file (two same-day $5
charges at the same shop) are both real: each line already on the account
answers for one incoming line, never for two.
"""

import re
from collections import defaultdict
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.banking import BankAccount, BankTransaction

# An OFX <NAME> is cut at 32 characters, so the OFX line can be the start
# of the CSV's description. A prefix that long is the same line; a short
# one ("CHECK 1" against "CHECK 12") is not.
_PREFIX_MIN = 20

_NON_WORD = re.compile(r"[^0-9A-Z]+")


def normalise_text(text: str | None) -> str:
    """Capitals, letters and digits only, single spaces: "Hvsb loan pmt
    ...3318" and "HVSB LOAN PMT  ...3318" are the same description."""
    return _NON_WORD.sub(" ", (text or "").upper()).strip()


def _texts(*values) -> set[str]:
    return {t for t in (normalise_text(v) for v in values) if t}


def _same_text(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return not a and not b
    for x in a:
        for y in b:
            if x == y:
                return True
            short, long_ = (x, y) if len(x) <= len(y) else (y, x)
            if len(short) >= _PREFIX_MIN and long_.startswith(short):
                return True
    return False


def _amount_key(amount) -> Decimal:
    return Decimal(str(amount)).quantize(Decimal("0.01"))


def drop_already_imported(
    db: Session, bank_account_id: int, transactions: list[dict]
) -> tuple[list[dict], int]:
    """The incoming lines not already on the account, and how many were.

    Each dict carries date, amount, payee, and memo or description, and
    optionally its id under "fitid" or "import_id". A line is already
    there when a line on the account has its id, or failing that its date,
    amount and normalised description (the payee or the description of
    either side)."""
    if not transactions:
        return [], 0
    dates = {t["date"] for t in transactions}
    existing = (
        db.query(BankTransaction)
        .filter(
            BankTransaction.bank_account_id == bank_account_id,
            BankTransaction.date >= min(dates),
            BankTransaction.date <= max(dates),
        )
        .order_by(BankTransaction.id)
        .all()
    )
    pool: dict[tuple, list[BankTransaction]] = defaultdict(list)
    for bt in existing:
        if bt.date in dates:
            pool[(bt.date, _amount_key(bt.amount))].append(bt)

    def _id(txn):
        return txn.get("fitid") or txn.get("import_id") or ""

    # an id the account already holds answers first, so a line matched by
    # its id is never also taken by another line's description
    ids = {_id(t) for t in transactions if _id(t)}
    by_id = {}
    if ids:
        for bt in (
            db.query(BankTransaction)
            .filter(
                BankTransaction.bank_account_id == bank_account_id,
                BankTransaction.import_id.in_(ids),
            )
            .all()
        ):
            by_id.setdefault(bt.import_id, bt)

    duplicate = [False] * len(transactions)
    used: set[int] = set()
    seen: set[str] = set()
    for i, txn in enumerate(transactions):
        key = _id(txn)
        if not key:
            continue
        bt = by_id.get(key)
        if bt is not None:
            duplicate[i] = True
            used.add(bt.id)
        elif key in seen:
            duplicate[i] = True  # the same id twice in one file
        seen.add(key)

    for i, txn in enumerate(transactions):
        if duplicate[i]:
            continue
        mine = _texts(txn.get("payee"), txn.get("memo"), txn.get("description"))
        for bt in pool.get((txn["date"], _amount_key(txn["amount"])), []):
            if bt.id in used:
                continue
            if _same_text(mine, _texts(bt.payee, bt.description)):
                duplicate[i] = True
                used.add(bt.id)
                break

    fresh = [t for t, dup in zip(transactions, duplicate) if not dup]
    return fresh, len(transactions) - len(fresh)


def set_aside_already_in_books(db: Session, bank_account: BankAccount, rows) -> int:
    """Statement lines whose posting another statement line already holds.

    A line still unmatched, with no free posting of its amount within the
    auto-match window, while a line outside `rows` on the same account
    with the same date and amount is matched: the books have it already,
    through that line. It is set aside (excluded, so it can be restored)
    rather than left in To review as "not in the books". Returns how many."""
    from app.services.bank_matching import AUTO_WINDOW_DAYS, candidate_lines

    if not bank_account.account_id:
        return 0
    rows = [r for r in rows if r.match_status == "unmatched"]
    if not rows:
        return 0
    mine = {r.id for r in rows}
    used: set[int] = set()
    n = 0
    for bt in sorted(rows, key=lambda r: (r.date, r.id)):
        holders = (
            db.query(BankTransaction)
            .filter(
                BankTransaction.bank_account_id == bank_account.id,
                BankTransaction.date == bt.date,
                BankTransaction.amount == bt.amount,
                BankTransaction.transaction_line_id.isnot(None),
                ~BankTransaction.id.in_(mine),
            )
            .order_by(BankTransaction.id)
            .all()
        )
        holder = next((h for h in holders if h.id not in used), None)
        if holder is None:
            continue
        if candidate_lines(
            db, bank_account.account_id, bt.amount, bt.date, AUTO_WINDOW_DAYS
        ):
            continue
        used.add(holder.id)
        bt.match_status = "excluded"
        n += 1
    return n
