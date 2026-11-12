# ============================================================================
# OFX/QFX Import Service — parse bank feeds from OFX/QFX files
# Feature 18: Import, dedup by FITID, preview → confirm → auto-match
# ============================================================================

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO

from sqlalchemy.orm import Session

from app.models.banking import BankTransaction
from app.services.bank_rules_engine import apply_bank_rules
from app.services.safe_errors import DataProblem

logger = logging.getLogger(__name__)

UNREADABLE_TEXT = (
    "That file isn't a bank statement this can read: "
    "an OFX, QFX or CSV export from the bank."
)
EMPTY_TEXT = "The file is empty — there is nothing to import."

# What ofxparse raises on a file that isn't OFX, found by feeding it junk,
# truncations and corrupted statements (SlowBooks-Pro-Testing#101): its own
# OfxParserException, ValueError (a header line without a colon; also
# UnicodeDecodeError), IndexError / LookupError (a cut-off header, an
# unknown CHARSET), TypeError and AttributeError (missing aggregates), and
# UnboundLocalError (no ENCODING). The net is cast only around the library
# call, so a bug in our own code still surfaces as one.
_OFX_FAILURES = (
    ValueError,
    LookupError,
    TypeError,
    AttributeError,
    UnboundLocalError,
    ArithmeticError,  # decimal.InvalidOperation on a mangled amount
)

# A statement amount has to be an ordinary finite number we can store and
# turn into JSON for the preview. NaN, Infinity and an absurd magnitude like
# <TRNAMT>1e999999999 parse into a Decimal ofxparse hands back without
# complaint, but float() of them is inf/nan and the preview's json.dumps
# then 500s (Testing#101). A real bank line is well under this bound.
_MAX_AMOUNT = Decimal("1e12")


class UnreadableStatement(DataProblem):
    """The uploaded bank file can't be read as a statement."""

    def __init__(self, user_text: str = UNREADABLE_TEXT):
        super().__init__(user_text)


def _unreadable(exc: BaseException | None, reason: str = "") -> UnreadableStatement:
    # The exception's class only: its text can quote the file.
    logger.info(
        "bank file unreadable as OFX: %s",
        type(exc).__name__ if exc is not None else reason,
    )
    return UnreadableStatement()


def _clean_amount(raw) -> Decimal:
    """A statement amount as a Decimal, or UnreadableStatement for one that
    can't be stored or turned into JSON: a non-number, NaN, Infinity, or an
    absurd magnitude like <TRNAMT>1e999999999 (Testing#101)."""
    try:
        amount = Decimal(str(raw))
    except InvalidOperation as exc:
        raise _unreadable(exc, "amount is not a number") from None
    if not amount.is_finite() or abs(amount) > _MAX_AMOUNT:
        raise _unreadable(None, "amount is not an ordinary finite number")
    return amount


def parse_ofx(content: str) -> list[dict]:
    """Parse OFX/QFX file content into a list of transaction dicts.
    Uses ofxparse if available, falls back to simple regex parsing.

    Raises UnreadableStatement for a file that isn't a whole OFX statement:
    junk, a PDF, a CSV, or one cut off before any transaction and its
    closing </OFX>."""
    try:
        from ofxparse import OfxParser
        from ofxparse.ofxparse import OfxParserException
    except ImportError:
        return _parse_ofx_fallback(content)

    # bs4 (ofxparse's parser) raises ParserRejectedMarkup on markup it won't
    # touch, e.g. a bogus CDATA section <![bogus[x]]>; it is a bare Exception
    # subclass, so name it rather than widen the net to Exception.
    net = (OfxParserException, *_OFX_FAILURES)
    try:
        from bs4 import ParserRejectedMarkup

        net = (*net, ParserRejectedMarkup)
    except ImportError:
        pass

    transactions = []
    try:
        ofx = OfxParser.parse(BytesIO(_drop_empty_text_tags(content).encode("utf-8")))
        if not ofx.accounts:
            raise _unreadable(None, "no account in the file")
        for account in ofx.accounts:
            for txn in account.statement.transactions:
                transactions.append(
                    {
                        "fitid": txn.id,
                        "date": (
                            txn.date.date() if hasattr(txn.date, "date") else txn.date
                        ),
                        "amount": _clean_amount(txn.amount),
                        "payee": txn.payee or txn.memo or "",
                        "memo": txn.memo or "",
                        "type": txn.type or "",
                    }
                )
    except UnreadableStatement:
        raise
    except net as exc:
        raise _unreadable(exc) from None

    if not transactions and not re.search(r"</OFX>", content, re.IGNORECASE):
        # ofxparse reads a truncated SGML file without complaint and returns
        # nothing; every OFX file closes its root, so a file that parsed to
        # no transactions and never closed </OFX> was cut off. A complete
        # statement that happens to carry no transactions still closes, so
        # this only fires when nothing was read.
        raise _unreadable(None, "no transactions and no closing </OFX>")
    return transactions


# A transaction's optional words given empty, <NAME></NAME>: ofxparse
# refuses the whole file on one ("That file isn't a bank statement…"),
# where the same file without the tag previews and imports with no payee
# (2.29.1 gate, #813). The tag says nothing, so it is taken out first.
_EMPTY_TEXT_TAG = re.compile(r"<(NAME|MEMO)>\s*</\1>", re.IGNORECASE)


def _drop_empty_text_tags(content: str) -> str:
    return _EMPTY_TEXT_TAG.sub("", content)


def _parse_ofx_fallback(content: str) -> list[dict]:
    """Simple OFX text parser, for a build without ofxparse."""
    transactions = []
    txn_blocks = re.findall(r"<STMTTRN>(.*?)</STMTTRN>", content, re.DOTALL)
    for block in txn_blocks:
        fitid = _extract_tag(block, "FITID")
        dt = _extract_tag(block, "DTPOSTED")
        amt = _extract_tag(block, "TRNAMT")
        name = _extract_tag(block, "NAME")
        memo = _extract_tag(block, "MEMO")

        if dt and amt:
            try:
                txn_date = date(int(dt[:4]), int(dt[4:6]), int(dt[6:8]))
            except (ValueError, InvalidOperation) as exc:
                raise _unreadable(exc) from None
            amount = _clean_amount(amt)
            transactions.append(
                {
                    "fitid": fitid or "",
                    "date": txn_date,
                    "amount": amount,
                    "payee": name or "",
                    "memo": memo or "",
                    "type": "",
                }
            )
    return transactions


def _extract_tag(block: str, tag: str) -> str:
    """Extract value from OFX tag."""
    match = re.search(rf"<{tag}>([^<\n]+)", block)
    return match.group(1).strip() if match else ""


def import_transactions(
    db: Session,
    bank_account_id: int,
    transactions: list[dict],
    import_source: str = "ofx",
) -> dict:
    """Import parsed transactions, skipping any line the account already
    has: by FITID, or by date, amount and description when an earlier
    import of the same month came as a CSV or from a feed (#705).

    Shared by the OFX upload path and the SimpleFIN feed (which passes
    import_source="simplefin" and SimpleFIN transaction ids as fitids).
    """
    from app.services.bank_dedupe import drop_already_imported

    fresh, skipped = drop_already_imported(db, bank_account_id, transactions)
    new_rows = []
    for txn in fresh:
        bt = BankTransaction(
            bank_account_id=bank_account_id,
            date=txn["date"],
            amount=txn["amount"],
            payee=txn.get("payee", ""),
            description=txn.get("memo", ""),
            import_id=txn.get("fitid", ""),
            import_source=import_source,
            match_status="unmatched",
        )
        db.add(bt)
        new_rows.append(bt)

    db.commit()

    matched, in_books = finish_import(db, bank_account_id, new_rows)

    return {
        "imported": len(new_rows),
        "skipped": skipped,
        "matched": matched,
        "already_in_books": in_books,
        "total": len(transactions),
    }


def finish_import(db: Session, bank_account_id: int, new_rows) -> tuple[int, int]:
    """After an import: rules suggest a category; each new line looks for
    the one posting the ledger already has for it (issue #114); a new line
    whose posting an earlier statement line already holds is set aside, not
    left as "not in the books" (#705). Returns (matched, already in books)."""
    if not new_rows:
        return 0, 0
    from app.models.banking import BankAccount
    from app.services.bank_dedupe import set_aside_already_in_books

    apply_bank_rules(db, bank_account_id)
    matched = _auto_match_new(db, bank_account_id)
    ba = db.query(BankAccount).filter(BankAccount.id == bank_account_id).first()
    in_books = set_aside_already_in_books(db, ba, new_rows) if ba else 0
    if in_books:
        db.commit()
    return matched, in_books


def _auto_match_new(db: Session, bank_account_id: int) -> int:
    from app.models.banking import BankAccount
    from app.services.bank_matching import auto_match

    ba = db.query(BankAccount).filter(BankAccount.id == bank_account_id).first()
    if not ba:
        return 0
    rows = (
        db.query(BankTransaction)
        .filter(
            BankTransaction.bank_account_id == bank_account_id,
            BankTransaction.match_status == "unmatched",
        )
        .all()
    )
    n = auto_match(db, ba, rows)
    if n:
        db.commit()
    return n
