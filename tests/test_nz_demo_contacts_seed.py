import os
import sys
import types
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

weasyprint_stub = types.ModuleType("weasyprint")
weasyprint_stub.HTML = object
sys.modules.setdefault("weasyprint", weasyprint_stub)

from app.database import Base


class NzDemoContactsSeedTests(unittest.TestCase):
    def setUp(self):
        from app.models import (  # noqa: F401
            Account, Customer, Vendor, Item, Invoice, InvoiceLine,
            Payment, PaymentAllocation, Estimate, EstimateLine, Transaction,
            TransactionLine, Settings, GstCode, BankAccount, Reconciliation,
            BankTransaction, Company, PayRun, PayStub, Employee,
        )

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def test_demo_seed_uses_nz_xero_contact_names_not_henry_brown_contacts(self):
        import scripts.seed_database as seed_database
        import scripts.seed_irs_mock_data as seed_demo
        from app.models.contacts import Customer, Vendor

        seed_database.SessionLocal = self.Session
        seed_demo.SessionLocal = self.Session

        seed_database.seed()
        seed_demo.seed()

        with self.Session() as db:
            customer_names = {row.name for row in db.query(Customer).all()}
            vendor_names = {row.name for row in db.query(Vendor).all()}

        self.assertIn("Basket Case", customer_names)
        self.assertIn("Bayside Club", customer_names)
        self.assertIn("ABC Furniture", vendor_names)
        self.assertIn("Capital Cab Co", vendor_names)
        self.assertNotIn("John E. Marks", customer_names)
        self.assertNotIn("Patricia Davis", customer_names)
        self.assertNotIn("Dale Advertising", vendor_names)

    def test_demo_seed_remains_idempotent_after_contact_replacement(self):
        import scripts.seed_database as seed_database
        import scripts.seed_irs_mock_data as seed_demo
        from app.models.contacts import Customer, Vendor

        seed_database.SessionLocal = self.Session
        seed_demo.SessionLocal = self.Session

        seed_database.seed()
        seed_demo.seed()
        seed_demo.seed()

        with self.Session() as db:
            customer_count = db.query(Customer).count()
            vendor_count = db.query(Vendor).count()

        self.assertEqual(customer_count, 8)
        self.assertEqual(vendor_count, 13)


if __name__ == "__main__":
    unittest.main()
