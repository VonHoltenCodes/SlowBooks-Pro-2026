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


class NzDemoBusinessSeedTests(unittest.TestCase):
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

    def test_demo_seed_uses_nz_relevant_items_and_transactions(self):
        import scripts.seed_database as seed_database
        import scripts.seed_irs_mock_data as seed_demo
        from app.models.items import Item
        from app.models.invoices import Invoice
        from app.models.estimates import Estimate
        from app.models.payments import Payment

        seed_database.SessionLocal = self.Session
        seed_demo.SessionLocal = self.Session

        seed_database.seed()
        seed_demo.seed()

        with self.Session() as db:
            item_names = {row.name for row in db.query(Item).all()}
            invoice_numbers = [row.invoice_number for row in db.query(Invoice).all()]
            estimate_numbers = [row.estimate_number for row in db.query(Estimate).all()]
            payment_refs = [row.reference for row in db.query(Payment).all()]

        self.assertIn("Strategy Workshop", item_names)
        self.assertIn("Monthly Advisory Retainer", item_names)
        self.assertIn("Website Refresh", item_names)
        self.assertIn("Payroll Filing Setup", item_names)
        self.assertNotIn("Body Repair", item_names)
        self.assertNotIn("Paint & Finish", item_names)
        self.assertEqual(len(invoice_numbers), 10)
        self.assertEqual(len(estimate_numbers), 3)
        self.assertEqual(len(payment_refs), 5)

    def test_demo_seed_uses_nz_gst_rate_not_old_sales_tax_rate(self):
        import scripts.seed_database as seed_database
        import scripts.seed_irs_mock_data as seed_demo
        from app.models.invoices import Invoice

        seed_database.SessionLocal = self.Session
        seed_demo.SessionLocal = self.Session

        seed_database.seed()
        seed_demo.seed()

        with self.Session() as db:
            invoice = db.query(Invoice).order_by(Invoice.id).first()

        self.assertEqual(str(invoice.tax_rate), "0.1500")


if __name__ == "__main__":
    unittest.main()
