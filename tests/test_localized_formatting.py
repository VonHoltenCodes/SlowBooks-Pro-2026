import unittest
from datetime import date
from decimal import Decimal

from app.services.formatting import format_currency, format_date


class LocalizedFormattingTests(unittest.TestCase):
    def test_format_currency_uses_nz_settings(self):
        settings = {"locale": "en-NZ", "currency": "NZD"}

        self.assertEqual(format_currency(Decimal("1234.5"), settings), "$1,234.50")
        self.assertEqual(format_currency(None, settings), "$0.00")

    def test_format_date_uses_nz_day_month_year_order(self):
        settings = {"locale": "en-NZ"}

        self.assertEqual(format_date(date(2026, 4, 13), settings), "13 Apr 2026")
        self.assertEqual(format_date("2026-04-13", settings), "13 Apr 2026")
        self.assertEqual(format_date(None, settings), "")


if __name__ == "__main__":
    unittest.main()
