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

from app.database import Base
from app.models.accounts import Account, AccountType
from app.models.contacts import Customer, Vendor
from app.models.credit_memos import CreditMemo, CreditMemoStatus
from app.models.email_log import EmailLog
from app.models.estimates import Estimate
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payroll import Employee, PayRun, PayRunStatus, PayStub
from app.models.purchase_orders import PurchaseOrder
from app.models.settings import Settings
from app.schemas.email import DocumentEmailRequest, StatementEmailRequest


class FakeSMTP:
    sent_messages = []

    def __init__(self, host, port, timeout=30):
        self.host = host
        self.port = port
        self.timeout = timeout

    def starttls(self):
        return None

    def login(self, user, password):
        return None

    def sendmail(self, from_email, recipients, message):
        self.__class__.sent_messages.append(
            {"from_email": from_email, "recipients": recipients, "message": message}
        )

    def quit(self):
        return None


class DocumentEmailDeliveryTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)
        FakeSMTP.sent_messages = []

    def _seed_smtp(self, db):
        for key, value in {
            "smtp_host": "smtp.example.com",
            "smtp_port": "587",
            "smtp_user": "mailer@example.com",
            "smtp_password": "secret",
            "smtp_from_email": "accounts@example.com",
            "smtp_from_name": "SlowBooks NZ",
            "smtp_use_tls": "true",
            "company_name": "SlowBooks NZ",
            "company_email": "accounts@example.com",
            "locale": "en-NZ",
            "currency": "NZD",
        }.items():
            db.add(Settings(key=key, value=value))
        db.commit()

    def test_send_email_logs_failure_when_smtp_not_configured(self):
        from app.services.email_service import send_email_or_raise

        with self.Session() as db:
            with self.assertRaises(ValueError):
                send_email_or_raise(
                    db,
                    to_email="customer@example.com",
                    subject="Test",
                    html_body="<p>Hello</p>",
                    entity_type="invoice",
                    entity_id=1,
                )
            log = db.query(EmailLog).one()

        self.assertEqual(log.status, "failed")
        self.assertIn("SMTP not configured", log.error_message)

    def test_invoice_email_route_sends_and_logs(self):
        from app.routes import invoices as invoices_route
        from app.services import email_service

        original_smtp = email_service.smtplib.SMTP
        original_generate_invoice_pdf = invoices_route.generate_invoice_pdf
        email_service.smtplib.SMTP = FakeSMTP
        invoices_route.generate_invoice_pdf = lambda *_args, **_kwargs: b"%PDF-invoice"

        try:
            with self.Session() as db:
                self._seed_smtp(db)
                customer = Customer(name="Aroha Ltd", email="customer@example.com")
                invoice = Invoice(
                    invoice_number="1001",
                    customer=customer,
                    date=date(2026, 4, 13),
                    due_date=date(2026, 4, 20),
                    status=InvoiceStatus.SENT,
                    subtotal=Decimal("100.00"),
                    tax_rate=Decimal("0.1500"),
                    tax_amount=Decimal("15.00"),
                    total=Decimal("115.00"),
                    balance_due=Decimal("115.00"),
                )
                db.add_all([customer, invoice])
                db.commit()

                result = invoices_route.email_invoice(
                    invoice.id,
                    DocumentEmailRequest(recipient="customer@example.com"),
                    db=db,
                )
                log = db.query(EmailLog).filter_by(entity_type="invoice", entity_id=invoice.id).one()
        finally:
            email_service.smtplib.SMTP = original_smtp
            invoices_route.generate_invoice_pdf = original_generate_invoice_pdf

        self.assertEqual(result["status"], "sent")
        self.assertEqual(log.status, "sent")
        self.assertEqual(len(FakeSMTP.sent_messages), 1)

    def test_document_email_routes_cover_estimate_statement_credit_memo_purchase_order_and_payslip(self):
        from app.routes import credit_memos as credit_memos_route
        from app.routes import estimates as estimates_route
        from app.routes import payroll as payroll_route
        from app.routes import purchase_orders as purchase_orders_route
        from app.routes import reports as reports_route
        from app.services import email_service

        original_smtp = email_service.smtplib.SMTP
        original_generate_estimate_pdf = estimates_route.generate_estimate_pdf
        original_generate_statement_pdf = reports_route.generate_statement_pdf
        original_generate_credit_memo_pdf = credit_memos_route.generate_credit_memo_pdf
        original_generate_purchase_order_pdf = purchase_orders_route.generate_purchase_order_pdf
        original_generate_payslip_pdf = payroll_route.generate_payroll_payslip_pdf
        email_service.smtplib.SMTP = FakeSMTP
        estimates_route.generate_estimate_pdf = lambda *_args, **_kwargs: b"%PDF-estimate"
        reports_route.generate_statement_pdf = lambda *_args, **_kwargs: b"%PDF-statement"
        credit_memos_route.generate_credit_memo_pdf = lambda *_args, **_kwargs: b"%PDF-credit-memo"
        purchase_orders_route.generate_purchase_order_pdf = lambda *_args, **_kwargs: b"%PDF-po"
        payroll_route.generate_payroll_payslip_pdf = lambda *_args, **_kwargs: b"%PDF-payslip"

        try:
            with self.Session() as db:
                self._seed_smtp(db)
                customer = Customer(name="Aroha Ltd", email="customer@example.com")
                vendor = Vendor(name="Harbour Supplies", email="vendor@example.com")
                employee = Employee(first_name="Aroha", last_name="Ngata")
                estimate = Estimate(
                    estimate_number="E-1001",
                    customer=customer,
                    date=date(2026, 4, 13),
                    expiration_date=date(2026, 4, 30),
                    subtotal=Decimal("100.00"),
                    tax_rate=Decimal("0.1500"),
                    tax_amount=Decimal("15.00"),
                    total=Decimal("115.00"),
                )
                credit_memo = CreditMemo(
                    memo_number="CM-0001",
                    customer=customer,
                    date=date(2026, 4, 13),
                    status=CreditMemoStatus.ISSUED,
                    subtotal=Decimal("50.00"),
                    tax_rate=Decimal("0.1500"),
                    tax_amount=Decimal("7.50"),
                    total=Decimal("57.50"),
                    balance_remaining=Decimal("57.50"),
                )
                po = PurchaseOrder(
                    po_number="PO-0001",
                    vendor=vendor,
                    date=date(2026, 4, 13),
                    expected_date=date(2026, 4, 20),
                    subtotal=Decimal("200.00"),
                    tax_rate=Decimal("0.1500"),
                    tax_amount=Decimal("30.00"),
                    total=Decimal("230.00"),
                )
                pay_run = PayRun(
                    period_start=date(2026, 4, 1),
                    period_end=date(2026, 4, 14),
                    pay_date=date(2026, 4, 15),
                    tax_year=2027,
                    status=PayRunStatus.PROCESSED,
                )
                db.add_all([customer, vendor, employee, estimate, credit_memo, po, pay_run])
                db.flush()
                stub = PayStub(
                    pay_run_id=pay_run.id,
                    employee_id=employee.id,
                    tax_code="M",
                    gross_pay=Decimal("1000.00"),
                    paye=Decimal("200.00"),
                    acc_earners_levy=Decimal("20.00"),
                    kiwisaver_employee_deduction=Decimal("30.00"),
                    employer_kiwisaver_contribution=Decimal("30.00"),
                    esct=Decimal("5.00"),
                    net_pay=Decimal("750.00"),
                )
                db.add(stub)
                db.commit()

                estimates_route.email_estimate(
                    estimate.id,
                    DocumentEmailRequest(recipient="customer@example.com"),
                    db=db,
                )
                reports_route.email_customer_statement(
                    customer.id,
                    StatementEmailRequest(recipient="customer@example.com", as_of_date=date(2026, 4, 30)),
                    db=db,
                )
                credit_memos_route.email_credit_memo(
                    credit_memo.id,
                    DocumentEmailRequest(recipient="customer@example.com"),
                    db=db,
                )
                purchase_orders_route.email_purchase_order(
                    po.id,
                    DocumentEmailRequest(recipient="vendor@example.com"),
                    db=db,
                )
                payroll_route.email_payroll_payslip(
                    pay_run.id,
                    employee.id,
                    DocumentEmailRequest(recipient="employee@example.com"),
                    db=db,
                )

                entity_types = [row.entity_type for row in db.query(EmailLog).order_by(EmailLog.id).all()]
        finally:
            email_service.smtplib.SMTP = original_smtp
            estimates_route.generate_estimate_pdf = original_generate_estimate_pdf
            reports_route.generate_statement_pdf = original_generate_statement_pdf
            credit_memos_route.generate_credit_memo_pdf = original_generate_credit_memo_pdf
            purchase_orders_route.generate_purchase_order_pdf = original_generate_purchase_order_pdf
            payroll_route.generate_payroll_payslip_pdf = original_generate_payslip_pdf

        self.assertEqual(
            entity_types,
            ["estimate", "statement", "credit_memo", "purchase_order", "payroll_payslip"],
        )
        self.assertEqual(len(FakeSMTP.sent_messages), 5)

    def test_payslip_email_rejects_draft_pay_run(self):
        from app.routes import payroll as payroll_route

        with self.Session() as db:
            employee = Employee(first_name="Aroha", last_name="Ngata")
            pay_run = PayRun(
                period_start=date(2026, 4, 1),
                period_end=date(2026, 4, 14),
                pay_date=date(2026, 4, 15),
                tax_year=2027,
                status=PayRunStatus.DRAFT,
            )
            db.add_all([employee, pay_run])
            db.flush()
            db.add(PayStub(pay_run_id=pay_run.id, employee_id=employee.id, tax_code="M", net_pay=Decimal("100.00")))
            db.commit()

            with self.assertRaises(HTTPException) as ctx:
                payroll_route.email_payroll_payslip(
                    pay_run.id,
                    employee.id,
                    DocumentEmailRequest(recipient="employee@example.com"),
                    db=db,
                )

        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
