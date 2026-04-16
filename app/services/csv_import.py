# ============================================================================
# CSV Import Service — import entities from CSV files
# Feature 14: Resilient import with error collection (like iif_import.py)
# ============================================================================

import csv
import io
import logging

from sqlalchemy.orm import Session

from app.models.contacts import Customer, Vendor
from app.models.items import Item, ItemType

logger = logging.getLogger(__name__)


def import_customers(db: Session, csv_text: str) -> dict:
    reader = csv.DictReader(io.StringIO(csv_text))
    created = 0
    skipped = 0
    errors = []

    for i, row in enumerate(reader, start=2):
        try:
            name = row.get("Name", "").strip()
            if not name:
                errors.append(f"Row {i}: Missing name")
                continue

            existing = db.query(Customer).filter(Customer.name == name).first()
            if existing:
                skipped += 1
                continue

            db.add(Customer(
                name=name,
                company=row.get("Company", ""),
                email=row.get("Email", ""),
                phone=row.get("Phone", ""),
                bill_address1=row.get("Address", ""),
                bill_city=row.get("City", ""),
                bill_state=row.get("State", ""),
                bill_zip=row.get("ZIP", ""),
                terms=row.get("Terms", "Net 30"),
            ))
            created += 1
        except Exception:
            logger.exception("Failed to import customer row %d", i)
            errors.append(f"Row {i}: import failed")

    db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}


def import_vendors(db: Session, csv_text: str) -> dict:
    reader = csv.DictReader(io.StringIO(csv_text))
    created = 0
    skipped = 0
    errors = []

    for i, row in enumerate(reader, start=2):
        try:
            name = row.get("Name", "").strip()
            if not name:
                errors.append(f"Row {i}: Missing name")
                continue

            existing = db.query(Vendor).filter(Vendor.name == name).first()
            if existing:
                skipped += 1
                continue

            db.add(Vendor(
                name=name,
                company=row.get("Company", ""),
                email=row.get("Email", ""),
                phone=row.get("Phone", ""),
                address1=row.get("Address", ""),
                city=row.get("City", ""),
                state=row.get("State", ""),
                zip=row.get("ZIP", ""),
                terms=row.get("Terms", "Net 30"),
            ))
            created += 1
        except Exception:
            logger.exception("Failed to import vendor row %d", i)
            errors.append(f"Row {i}: import failed")

    db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}


def import_items(db: Session, csv_text: str) -> dict:
    reader = csv.DictReader(io.StringIO(csv_text))
    created = 0
    skipped = 0
    errors = []

    type_map = {"product": ItemType.PRODUCT, "service": ItemType.SERVICE,
                "material": ItemType.MATERIAL, "labor": ItemType.LABOR}

    for i, row in enumerate(reader, start=2):
        try:
            name = row.get("Name", "").strip()
            if not name:
                errors.append(f"Row {i}: Missing name")
                continue

            existing = db.query(Item).filter(Item.name == name).first()
            if existing:
                skipped += 1
                continue

            item_type = type_map.get(row.get("Type", "service").lower(), ItemType.SERVICE)
            db.add(Item(
                name=name,
                item_type=item_type,
                description=row.get("Description", ""),
                rate=float(row.get("Rate", 0)),
                cost=float(row.get("Cost", 0)),
            ))
            created += 1
        except Exception:
            logger.exception("Failed to import item row %d", i)
            errors.append(f"Row {i}: import failed")

    db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}
