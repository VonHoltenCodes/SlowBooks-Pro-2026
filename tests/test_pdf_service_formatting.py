import sys
import types
import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace


weasyprint_stub = types.ModuleType("weasyprint")
weasyprint_stub.HTML = object
sys.modules.setdefault("weasyprint", weasyprint_stub)

from app.services import pdf_service


class CapturingHTML:
    rendered = []

    def __init__(self, string):
        self.string = string
        self.__class__.rendered.append(string)

    def write_pdf(self):
        return self.string.encode()


class PdfServiceFormattingTests(unittest.TestCase):
    def setUp(self):
        CapturingHTML.rendered = []
        pdf_service.HTML = CapturingHTML
        self.company = {"locale": "en-NZ", "currency": "NZD", "company_name": "SlowBooks NZ"}

    def test_invoice_pdf_uses_rendered_company_settings(self):
        invoice = SimpleNamespace(
            invoice_number="1001",
            date=date(2026, 4, 13),
            due_date=date(2026, 4, 20),
            terms="Net 7",
            po_number=None,
            customer_name="Aroha Ltd",
            bill_address1="",
            bill_address2="",
            bill_city="",
            bill_state="",
            bill_zip="",
            ship_address1="",
            lines=[SimpleNamespace(description="Consulting", quantity=1, rate=Decimal("1234.5"), amount=Decimal("1234.5"))],
            subtotal=Decimal("1234.5"),
            tax_rate=Decimal("0"),
            tax_amount=Decimal("0"),
            total=Decimal("1234.5"),
            amount_paid=Decimal("0"),
            balance_due=Decimal("1234.5"),
            notes=None,
        )

        pdf_service.generate_invoice_pdf(invoice, self.company)

        self.assertIn("13 Apr 2026", CapturingHTML.rendered[-1])
        self.assertIn("20 Apr 2026", CapturingHTML.rendered[-1])
        self.assertIn("$1,234.50", CapturingHTML.rendered[-1])

    def test_estimate_pdf_uses_rendered_company_settings(self):
        estimate = SimpleNamespace(
            estimate_number="E-1001",
            date=date(2026, 4, 13),
            expiration_date=date(2026, 5, 13),
            status=SimpleNamespace(value="draft"),
            customer=SimpleNamespace(name="Aroha Ltd"),
            bill_address1="",
            bill_address2="",
            bill_city="",
            bill_state="",
            bill_zip="",
            lines=[SimpleNamespace(description="Consulting", quantity=1, rate=Decimal("1234.5"), amount=Decimal("1234.5"))],
            subtotal=Decimal("1234.5"),
            tax_rate=Decimal("0"),
            tax_amount=Decimal("0"),
            total=Decimal("1234.5"),
            notes=None,
        )

        pdf_service.generate_estimate_pdf(estimate, self.company)

        self.assertIn("13 Apr 2026", CapturingHTML.rendered[-1])
        self.assertIn("13 May 2026", CapturingHTML.rendered[-1])
        self.assertIn("$1,234.50", CapturingHTML.rendered[-1])

    def test_statement_pdf_uses_rendered_company_settings(self):
        customer = SimpleNamespace(
            name="Aroha Ltd",
            bill_address1="",
            bill_address2="",
            bill_city="",
            bill_state="",
            bill_zip="",
        )
        invoices = [
            SimpleNamespace(
                date=date(2026, 4, 13),
                invoice_number="1001",
                notes="",
                total=Decimal("1234.5"),
            )
        ]
        payments = [
            SimpleNamespace(
                date=date(2026, 4, 20),
                reference="PMT-1",
                check_number="",
                method="Bank",
                amount=Decimal("234.5"),
            )
        ]

        pdf_service.generate_statement_pdf(
            customer, invoices, payments, self.company, as_of_date=date(2026, 4, 30)
        )

        self.assertIn("30 Apr 2026", CapturingHTML.rendered[-1])
        self.assertIn("13 Apr 2026", CapturingHTML.rendered[-1])
        self.assertIn("20 Apr 2026", CapturingHTML.rendered[-1])
        self.assertIn("$1,234.50", CapturingHTML.rendered[-1])
        self.assertIn("$234.50", CapturingHTML.rendered[-1])

    def test_payroll_payslip_pdf_uses_nz_payroll_labels_and_company_settings(self):
        pay_run = SimpleNamespace(
            id=7,
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 14),
            pay_date=date(2026, 4, 15),
            tax_year=2027,
        )
        employee = SimpleNamespace(
            first_name="Aroha",
            last_name="Ngata",
        )
        stub = SimpleNamespace(
            tax_code="M",
            hours=Decimal("0"),
            gross_pay=Decimal("3000.00"),
            paye=Decimal("600.78"),
            acc_earners_levy=Decimal("52.50"),
            student_loan_deduction=Decimal("0.00"),
            kiwisaver_employee_deduction=Decimal("105.00"),
            employer_kiwisaver_contribution=Decimal("105.00"),
            esct=Decimal("18.37"),
            child_support_deduction=Decimal("0.00"),
            net_pay=Decimal("2241.72"),
        )

        pdf_service.generate_payroll_payslip_pdf(pay_run, stub, employee, self.company)

        self.assertIn("Payslip", CapturingHTML.rendered[-1])
        self.assertIn("Aroha Ngata", CapturingHTML.rendered[-1])
        self.assertIn("15 Apr 2026", CapturingHTML.rendered[-1])
        self.assertIn("PAYE", CapturingHTML.rendered[-1])
        self.assertIn("ACC Earners' Levy", CapturingHTML.rendered[-1])
        self.assertIn("$2,241.72", CapturingHTML.rendered[-1])


if __name__ == "__main__":
    unittest.main()
