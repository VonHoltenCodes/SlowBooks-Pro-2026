import os
import sys
import types
import unittest
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

weasyprint_stub = types.ModuleType("weasyprint")
weasyprint_stub.HTML = object
sys.modules.setdefault("weasyprint", weasyprint_stub)
dateutil_stub = types.ModuleType("dateutil")
relativedelta_stub = types.ModuleType("dateutil.relativedelta")


class relativedelta:
    def __init__(self, months=0, years=0):
        self.days = (months * 31) + (years * 365)

    def __radd__(self, other):
        from datetime import timedelta

        return other + timedelta(days=self.days)


relativedelta_stub.relativedelta = relativedelta
sys.modules.setdefault("dateutil", dateutil_stub)
sys.modules.setdefault("dateutil.relativedelta", relativedelta_stub)

from app.database import Base
from app.models.bills import BillLine
from app.models.contacts import Customer, Vendor
from app.models.credit_memos import CreditMemoLine
from app.models.estimates import EstimateLine
from app.models.invoices import InvoiceLine
from app.models.purchase_orders import PurchaseOrderLine
from app.models.recurring import RecurringInvoiceLine


class LineGstStorageTests(unittest.TestCase):
    def setUp(self):
        from app.models.gst import GstCode  # noqa: F401

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def _seed_parties(self, db):
        customer = Customer(name="Aroha Ltd")
        vendor = Vendor(name="Harbour Supplies")
        db.add_all([customer, vendor])
        db.commit()
        return customer, vendor

    def test_line_models_default_to_gst15(self):
        for line_cls in (
            InvoiceLine,
            EstimateLine,
            BillLine,
            PurchaseOrderLine,
            CreditMemoLine,
            RecurringInvoiceLine,
        ):
            line = line_cls()
            self.assertEqual(line.gst_code, "GST15")
            self.assertEqual(line.gst_rate, Decimal("0.1500"))

    def test_line_schemas_default_to_gst15(self):
        from app.schemas.bills import BillLineCreate
        from app.schemas.credit_memos import CreditMemoLineCreate
        from app.schemas.estimates import EstimateLineCreate
        from app.schemas.invoices import InvoiceLineCreate
        from app.schemas.purchase_orders import POLineCreate
        from app.schemas.recurring import RecurringLineCreate

        for schema_cls in (
            InvoiceLineCreate,
            EstimateLineCreate,
            BillLineCreate,
            POLineCreate,
            CreditMemoLineCreate,
            RecurringLineCreate,
        ):
            line = schema_cls(description="Line")
            self.assertEqual(line.gst_code, "GST15")
            self.assertEqual(Decimal(str(line.gst_rate)), Decimal("0.1500"))

    def test_create_paths_persist_line_gst_metadata(self):
        from app.routes.bills import create_bill
        from app.routes.credit_memos import create_credit_memo
        from app.routes.estimates import create_estimate
        from app.routes.invoices import create_invoice
        from app.routes.purchase_orders import create_po
        from app.routes.recurring import create_recurring
        from app.schemas.bills import BillCreate, BillLineCreate
        from app.schemas.credit_memos import CreditMemoCreate, CreditMemoLineCreate
        from app.schemas.estimates import EstimateCreate, EstimateLineCreate
        from app.schemas.invoices import InvoiceCreate, InvoiceLineCreate
        from app.schemas.purchase_orders import POCreate, POLineCreate
        from app.schemas.recurring import RecurringCreate, RecurringLineCreate

        with self.Session() as db:
            customer, vendor = self._seed_parties(db)

            invoice = create_invoice(InvoiceCreate(
                customer_id=customer.id,
                date=date(2026, 4, 13),
                lines=[InvoiceLineCreate(description="Invoice", quantity=1, rate=10, gst_code="ZERO")],
            ), db=db)
            estimate = create_estimate(EstimateCreate(
                customer_id=customer.id,
                date=date(2026, 4, 13),
                lines=[EstimateLineCreate(description="Estimate", quantity=1, rate=10, gst_code="EXEMPT")],
            ), db=db)
            bill = create_bill(BillCreate(
                vendor_id=vendor.id,
                bill_number="B-1",
                date=date(2026, 4, 13),
                lines=[BillLineCreate(description="Bill", quantity=1, rate=10, gst_code="NO_GST")],
            ), db=db)
            po = create_po(POCreate(
                vendor_id=vendor.id,
                date=date(2026, 4, 13),
                lines=[POLineCreate(description="PO", quantity=1, rate=10, gst_code="ZERO")],
            ), db=db)
            credit = create_credit_memo(CreditMemoCreate(
                customer_id=customer.id,
                date=date(2026, 4, 13),
                lines=[CreditMemoLineCreate(description="Credit", quantity=1, rate=10, gst_code="EXEMPT")],
            ), db=db)
            recurring = create_recurring(RecurringCreate(
                customer_id=customer.id,
                frequency="monthly",
                start_date=date(2026, 4, 13),
                lines=[RecurringLineCreate(description="Recurring", quantity=1, rate=10, gst_code="NO_GST")],
            ), db=db)

        self.assertEqual(invoice.lines[0].gst_code, "ZERO")
        self.assertEqual(invoice.lines[0].gst_rate, Decimal("0.0000"))
        self.assertEqual(estimate.lines[0].gst_code, "EXEMPT")
        self.assertEqual(bill.lines[0].gst_code, "NO_GST")
        self.assertEqual(po.lines[0].gst_code, "ZERO")
        self.assertEqual(credit.lines[0].gst_code, "EXEMPT")
        self.assertEqual(recurring.lines[0].gst_code, "NO_GST")

    def test_invalid_gst_code_raises_400(self):
        from app.routes.invoices import create_invoice
        from app.schemas.invoices import InvoiceCreate, InvoiceLineCreate

        with self.Session() as db:
            customer, _vendor = self._seed_parties(db)
            with self.assertRaises(HTTPException) as ctx:
                create_invoice(InvoiceCreate(
                    customer_id=customer.id,
                    date=date(2026, 4, 13),
                    lines=[InvoiceLineCreate(description="Bad", quantity=1, rate=10, gst_code="BAD")],
                ), db=db)

        self.assertEqual(ctx.exception.status_code, 400)

    def test_copy_and_generation_paths_preserve_line_gst_metadata(self):
        from app.routes.estimates import convert_to_invoice, create_estimate
        from app.routes.invoices import duplicate_invoice
        from app.routes.purchase_orders import convert_to_bill, create_po
        from app.routes.recurring import create_recurring
        from app.schemas.estimates import EstimateCreate, EstimateLineCreate
        from app.schemas.purchase_orders import POCreate, POLineCreate
        from app.schemas.recurring import RecurringCreate, RecurringLineCreate
        from app.services.recurring_service import generate_due_invoices

        with self.Session() as db:
            customer, vendor = self._seed_parties(db)
            estimate = create_estimate(EstimateCreate(
                customer_id=customer.id,
                date=date(2026, 4, 13),
                lines=[EstimateLineCreate(description="Estimate", quantity=1, rate=10, gst_code="ZERO")],
            ), db=db)
            converted = convert_to_invoice(estimate.id, db=db)
            duplicated = duplicate_invoice(converted.id, db=db)

            po = create_po(POCreate(
                vendor_id=vendor.id,
                date=date(2026, 4, 13),
                lines=[POLineCreate(description="PO", quantity=1, rate=10, gst_code="EXEMPT")],
            ), db=db)
            bill_result = convert_to_bill(po.id, db=db)
            bill_line = db.query(BillLine).filter(BillLine.bill_id == bill_result["bill_id"]).one()

            create_recurring(RecurringCreate(
                customer_id=customer.id,
                frequency="monthly",
                start_date=date(2026, 4, 13),
                lines=[RecurringLineCreate(description="Recurring", quantity=1, rate=10, gst_code="NO_GST")],
            ), db=db)
            generated_ids = generate_due_invoices(db, as_of=date(2026, 4, 13))
            generated_line = db.query(InvoiceLine).filter(InvoiceLine.invoice_id == generated_ids[0]).one()
            bill_line_gst_code = bill_line.gst_code
            generated_line_gst_code = generated_line.gst_code

        self.assertEqual(converted.lines[0].gst_code, "ZERO")
        self.assertEqual(duplicated.lines[0].gst_code, "ZERO")
        self.assertEqual(bill_line_gst_code, "EXEMPT")
        self.assertEqual(generated_line_gst_code, "NO_GST")


if __name__ == "__main__":
    unittest.main()
