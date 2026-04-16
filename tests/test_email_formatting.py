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

    def test_invoice_email_escapes_untrusted_html_fields(self):
        invoice = SimpleNamespace(
            invoice_number="1001",
            date=date(2026, 4, 13),
            due_date=date(2026, 4, 20),
            terms="<b>Net 7</b>",
            balance_due=Decimal("1234.5"),
            customer=SimpleNamespace(name="<script>alert(1)</script>"),
            notes="<img src=x onerror=alert(1)>",
        )

        html = render_invoice_email(
            invoice,
            {"company_name": "SlowBooks NZ", "locale": "en-NZ", "currency": "NZD"},
        )

        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        self.assertIn("&lt;b&gt;Net 7&lt;/b&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img src=x onerror=alert(1)>", html)
        self.assertNotIn("<b>Net 7</b>", html)


if __name__ == "__main__":
    unittest.main()
