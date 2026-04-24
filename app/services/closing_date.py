# ============================================================================
# Closing Date Enforcement — prevent modifications before closing date
# Feature 10: Configurable closing date with optional password override
# ============================================================================

import hmac
from datetime import date

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.settings import Settings


def get_closing_date(db: Session) -> date | None:
    """Get the configured closing date, or None if not set."""
    row = db.query(Settings).filter(Settings.key == "closing_date").first()
    if row and row.value:
        try:
            return date.fromisoformat(row.value)
        except ValueError:
            return None
    return None


def check_closing_date(db: Session, txn_date: date, password: str = None):
    """Raise HTTPException if txn_date is on or before the closing date.
    If a closing_date_password is set and the caller provides it, allow override."""
    closing = get_closing_date(db)
    if closing is None:
        return  # No closing date configured

    if txn_date <= closing:
        # Check if password override is available
        pw_row = db.query(Settings).filter(Settings.key == "closing_date_password").first()
        if pw_row and pw_row.value and password and hmac.compare_digest(password, pw_row.value):
            return  # Password override accepted
        raise HTTPException(
            status_code=403,
            detail=f"Transaction date {txn_date} is on or before the closing date ({closing}). "
                   f"Modifications to closed periods are not allowed."
        )
