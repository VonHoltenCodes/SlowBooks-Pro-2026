import unittest
import os
from decimal import Decimal

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.services.gst_calculations import GstLineInput, calculate_document_gst


class GstCalculationServiceTests(unittest.TestCase):
    def test_exclusive_standard_rated_line_adds_gst(self):
        result = calculate_document_gst([
            GstLineInput(quantity=Decimal("2"), rate=Decimal("100"), gst_code="GST15", gst_rate=Decimal("0.1500")),
        ])

        self.assertEqual(result.subtotal, Decimal("200.00"))
        self.assertEqual(result.tax_amount, Decimal("30.00"))
        self.assertEqual(result.total, Decimal("230.00"))
        self.assertEqual(result.taxable_total, Decimal("200.00"))
        self.assertEqual(result.output_gst, Decimal("30.00"))

    def test_inclusive_standard_rated_line_extracts_three_twenty_thirds(self):
        result = calculate_document_gst([
            GstLineInput(quantity=Decimal("1"), rate=Decimal("115"), gst_code="GST15", gst_rate=Decimal("0.1500")),
        ], prices_include_gst=True)

        self.assertEqual(result.subtotal, Decimal("100.00"))
        self.assertEqual(result.tax_amount, Decimal("15.00"))
        self.assertEqual(result.total, Decimal("115.00"))

    def test_mixed_line_categories_keep_separate_totals(self):
        result = calculate_document_gst([
            GstLineInput(quantity=Decimal("1"), rate=Decimal("100"), gst_code="GST15", gst_rate=Decimal("0.1500")),
            GstLineInput(quantity=Decimal("1"), rate=Decimal("40"), gst_code="ZERO", gst_rate=Decimal("0.0000"), category="zero_rated"),
            GstLineInput(quantity=Decimal("1"), rate=Decimal("25"), gst_code="EXEMPT", gst_rate=Decimal("0.0000"), category="exempt"),
            GstLineInput(quantity=Decimal("1"), rate=Decimal("10"), gst_code="NO_GST", gst_rate=Decimal("0.0000"), category="no_gst"),
        ])

        self.assertEqual(result.subtotal, Decimal("175.00"))
        self.assertEqual(result.tax_amount, Decimal("15.00"))
        self.assertEqual(result.total, Decimal("190.00"))
        self.assertEqual(result.taxable_total, Decimal("100.00"))
        self.assertEqual(result.zero_rated_total, Decimal("40.00"))
        self.assertEqual(result.exempt_total, Decimal("25.00"))
        self.assertEqual(result.no_gst_total, Decimal("10.00"))

    def test_purchase_context_reports_input_gst(self):
        result = calculate_document_gst([
            GstLineInput(quantity=Decimal("1"), rate=Decimal("115"), gst_code="GST15", gst_rate=Decimal("0.1500")),
        ], prices_include_gst=True, gst_context="purchase")

        self.assertEqual(result.tax_amount, Decimal("15.00"))
        self.assertEqual(result.output_gst, Decimal("0.00"))
        self.assertEqual(result.input_gst, Decimal("15.00"))

    def test_rounds_each_line_to_cents(self):
        result = calculate_document_gst([
            GstLineInput(quantity=Decimal("1"), rate=Decimal("0.05"), gst_code="GST15", gst_rate=Decimal("0.1500")),
            GstLineInput(quantity=Decimal("1"), rate=Decimal("0.05"), gst_code="GST15", gst_rate=Decimal("0.1500")),
        ])

        self.assertEqual(result.subtotal, Decimal("0.10"))
        self.assertEqual(result.tax_amount, Decimal("0.02"))
        self.assertEqual(result.total, Decimal("0.12"))


if __name__ == "__main__":
    unittest.main()
