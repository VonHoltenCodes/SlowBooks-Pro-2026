"""Seed the database with Chart of Accounts."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models.accounts import Account, AccountType
from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS
from app.seed.fixed_assets import ensure_default_asset_type
from app.services.payment_terms import ensure_seeded as ensure_payment_terms


def seed():
    db = SessionLocal()
    try:
        # the terms list the forms offer (#664), whatever the chart holds
        ensure_payment_terms(db)
        db.commit()
        existing = db.query(Account).count()
        if existing > 0:
            print(f"Database already has {existing} accounts. Skipping seed.")
            return

        for entry in CHART_OF_ACCOUNTS:
            account = Account(
                name=entry["name"],
                account_number=entry["account_number"],
                account_type=AccountType(entry["account_type"]),
                bank_kind=entry.get("bank_kind"),
                detail_type=entry.get("detail_type"),
                is_system=True,
            )
            db.add(account)

        db.flush()
        ensure_default_asset_type(db)
        db.commit()
        print(f"Seeded {len(CHART_OF_ACCOUNTS)} accounts.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
