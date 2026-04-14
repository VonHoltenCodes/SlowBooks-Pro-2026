import os
import sys
import types
import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace


os.environ["DATABASE_URL"] = "sqlite:///:memory:"

psycopg2_stub = types.ModuleType("psycopg2")
sys.modules.setdefault("psycopg2", psycopg2_stub)

from app.services.email_service import render_document_email, render_invoice_email


class EmailFormattingTests(unittest.TestCase):
    def test_invoice_email_uses_company_locale_and_currency(self):
        invoice = SimpleNamespace(
            invoice_number="1001",
            date=date(2026, 4, 13),
            due_date=date(2026, 4, 20),
            terms="Net 7",
            balance_due=Decimal("1234.5"),
            customer=SimpleNamespace(name="Aroha Ltd"),
            notes=None,
        )

        html = render_invoice_email(
            invoice,
            {"company_name": "SlowBooks NZ", "locale": "en-NZ", "currency": "NZD"},
        )

        self.assertIn("13 Apr 2026", html)
        self.assertIn("20 Apr 2026", html)
        self.assertIn("Amount Due: $1,234.50", html)
        self.assertNotIn("2026-04-13", html)

    def test_generic_document_email_uses_localized_formatting(self):
        html = render_document_email(
            document_label="Estimate",
            recipient_name="Aroha Ltd",
            document_number="E-1001",
            company_settings={"company_name": "SlowBooks NZ", "locale": "en-NZ", "currency": "NZD"},
            amount=Decimal("1234.5"),
            action_label="Valid until",
            action_value=date(2026, 4, 20),
        )

        self.assertIn("Estimate", html)
        self.assertIn("E-1001", html)
        self.assertIn("$1,234.50", html)
        self.assertIn("20 Apr 2026", html)


if __name__ == "__main__":
    unittest.main()
