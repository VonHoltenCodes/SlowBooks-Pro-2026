"""
Seed Slowbooks with mock data from IRS Publication 583 (Rev. December 2024):
Henry Brown's Auto Body Shop, in the database this checkout runs on.

The data and the logic live in app/seed/irs_sample.py, which Companies ->
Try a sample company uses to build the same books in a company file of
their own; this script is the checkout's way to run it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.seed.irs_sample import already_seeded, seed_sample  # noqa: E402


def seed():
    db = SessionLocal()
    try:
        if already_seeded(db):
            print("IRS mock data already seeded. Skipping.")
            return
        print("Seeding IRS Pub 583 mock data — Henry Brown's Auto Body Shop...")
        made = seed_sample(db)
        db.commit()
        for what in (
            "vendors",
            "customers",
            "items",
            "invoices",
            "estimates",
            "payments",
        ):
            print(f"  {made[what]} {what} created")
        invoiced, paid = made["total_invoiced"], made["total_paid"]
        print("\nIRS Pub 583 mock data seeded successfully.")
        print(f"  Total invoiced: ${invoiced:,.2f}")
        print(f"  Total paid:     ${paid:,.2f}")
        print(f"  Outstanding:    ${invoiced - paid:,.2f}")
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
