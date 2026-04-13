import os
import sys
import types
import unittest
from datetime import date
from decimal import Decimal

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
from app.models.accounts import Account, AccountType
from app.models.contacts import Customer, Vendor
from app.models.bills import Bill
from app.models.invoices import Invoice
from app.models.settings import Settings
from app.models.transactions import TransactionLine


class DocumentGstCalculationTests(unittest.TestCase):
    def setUp(self):
        from app.models.gst import GstCode  # noqa: F401

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def _seed_parties_and_accounts(self, db):
        customer = Customer(name="Aroha Ltd")
        vendor = Vendor(name="Harbour Supplies")
        db.add_all([
            customer,
            vendor,
            Account(name="Accounts Receivable", account_number="1100", account_type=AccountType.ASSET),
            Account(name="Accounts Payable", account_number="2000", account_type=AccountType.LIABILITY),
            Account(name="GST Control", account_number="2200", account_type=AccountType.LIABILITY),
            Account(name="Service Income", account_number="4000", account_type=AccountType.INCOME),
            Account(name="Expenses", account_number="6000", account_type=AccountType.EXPENSE),
        ])
        db.commit()
        return customer, vendor

    def _set_inclusive_prices(self, db):
        db.add(Settings(key="prices_include_gst", value="true"))
        db.commit()

    def test_invoice_totals_use_line_gst_codes_not_document_tax_rate(self):
        from app.routes.invoices import create_invoice
        from app.schemas.invoices import InvoiceCreate, InvoiceLineCreate

        with self.Session() as db:
            customer, _vendor = self._seed_parties_and_accounts(db)
            invoice = create_invoice(InvoiceCreate(
                customer_id=customer.id,
                date=date(2026, 4, 13),
                tax_rate=Decimal("0.9900"),
                lines=[
                    InvoiceLineCreate(description="Standard", quantity=1, rate=Decimal("100"), gst_code="GST15"),
                    InvoiceLineCreate(description="Zero", quantity=1, rate=Decimal("50"), gst_code="ZERO"),
                ],
            ), db=db)

        self.assertEqual(invoice.subtotal, Decimal("150.00"))
        self.assertEqual(invoice.tax_amount, Decimal("15.00"))
        self.assertEqual(invoice.total, Decimal("165.00"))
        self.assertEqual(invoice.tax_rate, Decimal("0.1000"))

    def test_estimate_po_credit_and_recurring_totals_use_line_gst_codes(self):
        from app.routes.credit_memos import create_credit_memo
        from app.routes.estimates import create_estimate
        from app.routes.purchase_orders import create_po
        from app.routes.recurring import create_recurring
        from app.schemas.credit_memos import CreditMemoCreate, CreditMemoLineCreate
        from app.schemas.estimates import EstimateCreate, EstimateLineCreate
        from app.schemas.purchase_orders import POCreate, POLineCreate
        from app.schemas.recurring import RecurringCreate, RecurringLineCreate
        from app.services.recurring_service import generate_due_invoices

        with self.Session() as db:
            customer, vendor = self._seed_parties_and_accounts(db)
            estimate = create_estimate(EstimateCreate(
                customer_id=customer.id,
                date=date(2026, 4, 13),
                tax_rate=Decimal("0.9900"),
                lines=[EstimateLineCreate(description="Estimate", quantity=1, rate=Decimal("100"), gst_code="ZERO")],
            ), db=db)
            po = create_po(POCreate(
                vendor_id=vendor.id,
                date=date(2026, 4, 13),
                tax_rate=0.99,
                lines=[POLineCreate(description="PO", quantity=1, rate=100, gst_code="EXEMPT")],
            ), db=db)
            credit = create_credit_memo(CreditMemoCreate(
                customer_id=customer.id,
                date=date(2026, 4, 13),
                tax_rate=0.99,
                lines=[CreditMemoLineCreate(description="Credit", quantity=1, rate=100, gst_code="NO_GST")],
            ), db=db)
            create_recurring(RecurringCreate(
                customer_id=customer.id,
                frequency="monthly",
                start_date=date(2026, 4, 13),
                tax_rate=0.99,
                lines=[RecurringLineCreate(description="Recurring", quantity=1, rate=100, gst_code="ZERO")],
            ), db=db)
            generated_ids = generate_due_invoices(db, as_of=date(2026, 4, 13))
            generated_invoice = db.query(Invoice).filter(Invoice.id == generated_ids[0]).one()

        self.assertEqual(estimate.tax_amount, Decimal("0.00"))
        self.assertEqual(estimate.total, Decimal("100.00"))
        self.assertEqual(Decimal(str(po.tax_amount)), Decimal("0.00"))
        self.assertEqual(Decimal(str(po.total)), Decimal("100.00"))
        self.assertEqual(Decimal(str(credit.tax_amount)), Decimal("0.00"))
        self.assertEqual(Decimal(str(credit.total)), Decimal("100.00"))
        self.assertEqual(generated_invoice.tax_amount, Decimal("0.00"))
        self.assertEqual(generated_invoice.total, Decimal("100.00"))

    def test_inclusive_invoice_posts_balanced_net_income_and_gst(self):
        from app.routes.invoices import create_invoice
        from app.schemas.invoices import InvoiceCreate, InvoiceLineCreate

        with self.Session() as db:
            customer, _vendor = self._seed_parties_and_accounts(db)
            self._set_inclusive_prices(db)
            invoice = create_invoice(InvoiceCreate(
                customer_id=customer.id,
                date=date(2026, 4, 13),
                lines=[InvoiceLineCreate(description="Inclusive", quantity=1, rate=Decimal("115"), gst_code="GST15")],
            ), db=db)
            stored_invoice = db.query(Invoice).filter(Invoice.id == invoice.id).one()
            lines = db.query(TransactionLine).filter(TransactionLine.transaction_id == stored_invoice.transaction_id).all()

        self.assertEqual(invoice.subtotal, Decimal("100.00"))
        self.assertEqual(invoice.tax_amount, Decimal("15.00"))
        self.assertEqual(invoice.total, Decimal("115.00"))
        self.assertEqual(sum(line.debit for line in lines), Decimal("115.00"))
        self.assertEqual(sum(line.credit for line in lines), Decimal("115.00"))
        self.assertEqual(
            sorted(line.credit for line in lines if line.credit > 0),
            [Decimal("15.00"), Decimal("100.00")],
        )

    def test_inclusive_bill_posts_balanced_net_expense_and_input_gst(self):
        from app.routes.bills import create_bill
        from app.schemas.bills import BillCreate, BillLineCreate

        with self.Session() as db:
            _customer, vendor = self._seed_parties_and_accounts(db)
            self._set_inclusive_prices(db)
            bill = create_bill(BillCreate(
                vendor_id=vendor.id,
                bill_number="B-1",
                date=date(2026, 4, 13),
                lines=[BillLineCreate(description="Inclusive", quantity=1, rate=115, gst_code="GST15")],
            ), db=db)
            stored_bill = db.query(Bill).filter(Bill.id == bill.id).one()
            lines = db.query(TransactionLine).filter(TransactionLine.transaction_id == stored_bill.transaction_id).all()

        self.assertEqual(Decimal(str(bill.subtotal)), Decimal("100.00"))
        self.assertEqual(Decimal(str(bill.tax_amount)), Decimal("15.00"))
        self.assertEqual(Decimal(str(bill.total)), Decimal("115.00"))
        self.assertEqual(sum(line.debit for line in lines), Decimal("115.00"))
        self.assertEqual(sum(line.credit for line in lines), Decimal("115.00"))
        self.assertEqual(
            sorted(line.debit for line in lines if line.debit > 0),
            [Decimal("15.00"), Decimal("100.00")],
        )


if __name__ == "__main__":
    unittest.main()
