import io
import os
import sys
import types
import unittest
from datetime import date
from decimal import Decimal

from pypdf import PdfReader
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

weasyprint_stub = types.ModuleType("weasyprint")
weasyprint_stub.HTML = object
sys.modules.setdefault("weasyprint", weasyprint_stub)

from app.database import Base
from app.models.accounts import Account, AccountType
from app.models.contacts import Customer, Vendor
from app.models.settings import Settings


class GstReturnReportTests(unittest.TestCase):
    def setUp(self):
        from app.models.gst import GstCode  # noqa: F401

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def _seed(self, db, gst_basis="invoice"):
        customer = Customer(name="Aroha Ltd")
        vendor = Vendor(name="Harbour Supplies")
        db.add_all([
            customer,
            vendor,
            Account(name="Bank", account_number="1000", account_type=AccountType.ASSET),
            Account(name="Accounts Receivable", account_number="1100", account_type=AccountType.ASSET),
            Account(name="Undeposited Funds", account_number="1200", account_type=AccountType.ASSET),
            Account(name="Accounts Payable", account_number="2000", account_type=AccountType.LIABILITY),
            Account(name="GST", account_number="2200", account_type=AccountType.LIABILITY),
            Account(name="Service Income", account_number="4000", account_type=AccountType.INCOME),
            Account(name="Expenses", account_number="6000", account_type=AccountType.EXPENSE),
            Settings(key="gst_basis", value=gst_basis),
            Settings(key="gst_period", value="two-monthly"),
            Settings(key="gst_number", value="123-456-789"),
            Settings(key="company_name", value="SlowBooks NZ"),
        ])
        db.commit()
        return customer, vendor

    def test_invoice_basis_calculates_gst101a_boxes_with_adjustments(self):
        from app.routes.bills import create_bill
        from app.routes.credit_memos import create_credit_memo
        from app.routes.invoices import create_invoice
        from app.routes.reports import gst_return_report, sales_tax_report
        from app.schemas.bills import BillCreate, BillLineCreate
        from app.schemas.credit_memos import CreditMemoCreate, CreditMemoLineCreate
        from app.schemas.invoices import InvoiceCreate, InvoiceLineCreate

        with self.Session() as db:
            customer, vendor = self._seed(db)
            create_invoice(InvoiceCreate(
                customer_id=customer.id,
                date=date(2026, 4, 1),
                lines=[
                    InvoiceLineCreate(description="Standard sale", quantity=1, rate=Decimal("100"), gst_code="GST15"),
                    InvoiceLineCreate(description="Zero sale", quantity=1, rate=Decimal("50"), gst_code="ZERO"),
                    InvoiceLineCreate(description="Exempt sale", quantity=1, rate=Decimal("25"), gst_code="EXEMPT"),
                ],
            ), db=db)
            create_credit_memo(CreditMemoCreate(
                customer_id=customer.id,
                date=date(2026, 4, 2),
                lines=[CreditMemoLineCreate(description="Credit", quantity=1, rate=Decimal("20"), gst_code="GST15")],
            ), db=db)
            create_bill(BillCreate(
                vendor_id=vendor.id,
                bill_number="B-1",
                date=date(2026, 4, 3),
                lines=[
                    BillLineCreate(description="Standard purchase", quantity=1, rate=Decimal("200"), gst_code="GST15"),
                    BillLineCreate(description="No GST purchase", quantity=1, rate=Decimal("40"), gst_code="NO_GST"),
                ],
            ), db=db)

            report = gst_return_report(
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 30),
                box9_adjustments=Decimal("5.00"),
                box13_adjustments=Decimal("2.00"),
                db=db,
            )
            alias = sales_tax_report(
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 30),
                db=db,
            )

        self.assertEqual(report["gst_basis"], "invoice")
        self.assertEqual(report["gst_period"], "two-monthly")
        self.assertEqual(report["boxes"]["5"], 142.0)
        self.assertEqual(report["boxes"]["6"], 50.0)
        self.assertEqual(report["boxes"]["7"], 92.0)
        self.assertEqual(report["boxes"]["8"], 12.0)
        self.assertEqual(report["boxes"]["9"], 5.0)
        self.assertEqual(report["boxes"]["10"], 17.0)
        self.assertEqual(report["boxes"]["11"], 230.0)
        self.assertEqual(report["boxes"]["12"], 30.0)
        self.assertEqual(report["boxes"]["13"], 2.0)
        self.assertEqual(report["boxes"]["14"], 32.0)
        self.assertEqual(report["boxes"]["15"], 15.0)
        self.assertEqual(report["net_position"], "refundable")
        self.assertEqual(report["excluded_totals"]["sales"], 25.0)
        self.assertEqual(report["excluded_totals"]["purchases"], 40.0)
        self.assertEqual(alias["report_type"], "gst_return")

    def test_payments_basis_prorates_payment_allocations(self):
        from app.routes.bill_payments import create_bill_payment
        from app.routes.bills import create_bill
        from app.routes.invoices import create_invoice
        from app.routes.payments import create_payment
        from app.routes.reports import gst_return_report
        from app.schemas.bills import BillCreate, BillLineCreate, BillPaymentAllocationCreate, BillPaymentCreate
        from app.schemas.invoices import InvoiceCreate, InvoiceLineCreate
        from app.schemas.payments import PaymentAllocationCreate, PaymentCreate

        with self.Session() as db:
            customer, vendor = self._seed(db, gst_basis="payments")
            invoice = create_invoice(InvoiceCreate(
                customer_id=customer.id,
                date=date(2026, 3, 15),
                lines=[InvoiceLineCreate(description="Standard sale", quantity=1, rate=Decimal("100"), gst_code="GST15")],
            ), db=db)
            bill = create_bill(BillCreate(
                vendor_id=vendor.id,
                bill_number="B-1",
                date=date(2026, 3, 15),
                lines=[BillLineCreate(description="Standard purchase", quantity=1, rate=Decimal("200"), gst_code="GST15")],
            ), db=db)
            create_payment(PaymentCreate(
                customer_id=customer.id,
                date=date(2026, 4, 5),
                amount=Decimal("57.50"),
                allocations=[PaymentAllocationCreate(invoice_id=invoice.id, amount=Decimal("57.50"))],
            ), db=db)
            create_bill_payment(BillPaymentCreate(
                vendor_id=vendor.id,
                date=date(2026, 4, 6),
                amount=Decimal("115.00"),
                allocations=[BillPaymentAllocationCreate(bill_id=bill.id, amount=Decimal("115.00"))],
            ), db=db)

            report = gst_return_report(
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 30),
                db=db,
            )

        self.assertEqual(report["gst_basis"], "payments")
        self.assertEqual(report["boxes"]["5"], 57.5)
        self.assertEqual(report["boxes"]["7"], 57.5)
        self.assertEqual(report["boxes"]["8"], 7.5)
        self.assertEqual(report["boxes"]["11"], 115.0)
        self.assertEqual(report["boxes"]["12"], 15.0)
        self.assertEqual(report["boxes"]["15"], 7.5)
        self.assertEqual(report["net_position"], "refundable")

    def test_pdf_endpoint_fills_gst101a_box_fields(self):
        from app.services.gst_return import _comb_text_positions
        from app.routes.invoices import create_invoice
        from app.routes.reports import gst_return_pdf
        from app.schemas.invoices import InvoiceCreate, InvoiceLineCreate

        with self.Session() as db:
            customer, _vendor = self._seed(db)
            create_invoice(InvoiceCreate(
                customer_id=customer.id,
                date=date(2026, 4, 1),
                lines=[InvoiceLineCreate(description="Standard sale", quantity=1, rate=Decimal("100"), gst_code="GST15")],
            ), db=db)
            response = gst_return_pdf(
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 30),
                box9_adjustments=Decimal("5.00"),
                box13_adjustments=Decimal("2.00"),
                return_due_date=date(2026, 5, 28),
                db=db,
            )

        positions = _comb_text_positions((0, 0, 110, 10), "115.00", 11)
        self.assertEqual(response.media_type, "application/pdf")
        self.assertEqual(response.headers["Content-Disposition"], 'attachment; filename="GST101A_2026-04-01_2026-04-30.pdf"')
        fields = PdfReader(io.BytesIO(response.body)).get_fields()
        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(response.body)).pages)
        compact_text = "".join(text.split())
        self.assertEqual([pair[1] for pair in positions], list("115.00"))
        self.assertEqual(positions[0][0], 55.0)
        self.assertEqual(positions[-1][0], 105.0)
        self.assertIn("01/04-30/04", compact_text)
        self.assertIn("28/05/26", compact_text)
        self.assertIn("123-456-789", compact_text)
        self.assertIn("115.00", compact_text)
        self.assertIn("5.00", compact_text)
        self.assertIn("20.00", compact_text)
        self.assertIn("2.00", compact_text)
        self.assertIn("18.00", compact_text)
        self.assertEqual(fields["refund / gst"]["/V"], "/No")


if __name__ == "__main__":
    unittest.main()
