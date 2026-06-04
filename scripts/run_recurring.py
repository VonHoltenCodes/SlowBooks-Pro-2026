#!/usr/bin/env python3
"""
Standalone cron script to generate due recurring invoices.
Add to crontab: 0 6 * * * cd /path/to/bookkeeper && python3 scripts/run_recurring.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.services.recurring_service import generate_due_invoices

if __name__ == "__main__":
    db = SessionLocal()
    try:
        ids = generate_due_invoices(db)
        if ids:
            print(f"Generated {len(ids)} invoice(s): {ids}")
        else:
            print("No recurring invoices due.")
    finally:
        db.close()
