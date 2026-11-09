"""A bank account's check details (#739), for checks printed on blank stock:
read for the Settings page, saved from it, and handed to the check face."""

import logging

from sqlalchemy.orm import Session

from app.models.bank_check_details import BankCheckDetails
from app.services import crypto

logger = logging.getLogger(__name__)


def _row(db: Session, account_id: int):
    return (
        db.query(BankCheckDetails)
        .filter(BankCheckDetails.account_id == account_id)
        .first()
    )


def _account_number(row) -> str:
    """The account number in the clear, or "" when there is none or it was
    saved under a key this install no longer has."""
    if row is None or not row.account_number_enc:
        return ""
    try:
        return crypto.decrypt_value(row.account_number_enc) or ""
    except Exception:
        logger.error("A bank account's check account number could not be read")
        return ""


def describe(db: Session, account_id: int) -> dict:
    """What the Settings page shows: everything but the account number,
    which reads as its last four."""
    row = _row(db, account_id)
    return {
        "account_id": account_id,
        "bank_name": row.bank_name if row else "",
        "bank_city_state": row.bank_city_state if row else "",
        "fractional_routing": row.fractional_routing if row else "",
        "routing_number": row.routing_number if row else "",
        "account_number_last_four": (row.account_last_four or "") if row else "",
        "has_account_number": bool(row and row.account_number_enc),
    }


def save(db: Session, account_id: int, fields: dict) -> dict:
    """Save the fields given; `account_number` absent keeps the stored one
    and "" clears it."""
    row = _row(db, account_id)
    if row is None:
        row = BankCheckDetails(account_id=account_id)
        db.add(row)
    for key in ("bank_name", "bank_city_state", "fractional_routing", "routing_number"):
        if key in fields:
            setattr(row, key, fields[key])
    if "account_number" in fields:
        number = fields["account_number"]
        row.account_number_enc = crypto.encrypt_value(number) if number else None
        row.account_last_four = number[-4:] if number else None
    db.commit()
    return describe(db, account_id)


def for_check(db: Session, account_id) -> dict | None:
    """What the check face prints for a bank account, or None when it has
    no details saved."""
    row = _row(db, account_id) if account_id else None
    if row is None:
        return None
    return {
        "bank_name": row.bank_name or "",
        "bank_city_state": row.bank_city_state or "",
        "fractional_routing": row.fractional_routing or "",
        "routing_number": row.routing_number or "",
        "account_number": _account_number(row),
    }
