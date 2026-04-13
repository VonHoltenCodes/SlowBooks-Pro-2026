import csv
import io
import os
import sys
import types
import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

weasyprint_stub = types.ModuleType("weasyprint")
weasyprint_stub.HTML = object
sys.modules.setdefault("weasyprint", weasyprint_stub)

from app.database import Base
from app.models.contacts import Customer, Vendor
from app.schemas.contacts import CustomerCreate, VendorCreate
from app.services import csv_export, csv_import, email_service, pdf_service


class CapturingHTML:
    rendered = []

    def __init__(self, string):
        self.string = string
        self.__class__.rendered.append(string)

    def write_pdf(self):
        return self.string.encode()


class NzAddressLabelTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)
        CapturingHTML.rendered = []
        pdf_service.HTML = CapturingHTML
        self.company = {
            "locale": "en-NZ",
            "currency": "NZD",
            "company_name": "SlowBooks NZ",
            "company_address1": "1 Queen Street",
            "company_address2": "",
            "company_city": "Auckland",
            "company_state": "Auckland",
            "company_zip": "1010",
            "company_phone": "",
            "company_email": "",
        }

    def test_contact_models_and_create_schemas_default_to_nz(self):
        self.assertEqual(Customer().bill_country, "NZ")
        self.assertEqual(Customer().ship_country, "NZ")
        self.assertEqual(Vendor().country, "NZ")
        self.assertEqual(CustomerCreate(name="Aroha Ltd").bill_country, "NZ")
        self.assertEqual(CustomerCreate(name="Aroha Ltd").ship_country, "NZ")
        self.assertEqual(VendorCreate(name="Harbour Supplies").country, "NZ")

    def test_csv_export_uses_region_and_postcode_headers(self):
        with self.Session() as db:
            db.add(Customer(name="Aroha Ltd", bill_city="Auckland", bill_state="Auckland", bill_zip="1010"))
            db.add(Vendor(name="Harbour Supplies", city="Wellington", state="Wellington", zip="6011"))
            db.commit()

            customer_header = next(csv.reader(io.StringIO(csv_export.export_customers(db))))
            vendor_header = next(csv.reader(io.StringIO(csv_export.export_vendors(db))))

        self.assertIn("Region", customer_header)
        self.assertIn("Postcode", customer_header)
        self.assertNotIn("State", customer_header)
        self.assertNotIn("ZIP", customer_header)
        self.assertIn("Region", vendor_header)
        self.assertIn("Postcode", vendor_header)

    def test_csv_import_accepts_nz_and_legacy_address_headers(self):
        with self.Session() as db:
            csv_import.import_customers(
                db,
                "Name,Address,City,Region,Postcode\nAroha Ltd,1 Queen Street,Auckland,Auckland,1010\n",
            )
            csv_import.import_vendors(
                db,
                "Name,Address,City,State,ZIP\nLegacy Vendor,2 Old Road,Dunedin,Otago,9016\n",
            )

            customer = db.query(Customer).filter(Customer.name == "Aroha Ltd").one()
            vendor = db.query(Vendor).filter(Vendor.name == "Legacy Vendor").one()

        self.assertEqual(customer.bill_state, "Auckland")
        self.assertEqual(customer.bill_zip, "1010")
        self.assertEqual(customer.bill_country, "NZ")
        self.assertEqual(vendor.state, "Otago")
        self.assertEqual(vendor.zip, "9016")
        self.assertEqual(vendor.country, "NZ")

    def test_pdf_addresses_use_nz_city_region_postcode_lines(self):
        invoice = SimpleNamespace(
            invoice_number="1001",
            date=date(2026, 4, 13),
            due_date=date(2026, 4, 20),
            terms="Net 7",
            po_number=None,
            customer_name="Aroha Ltd",
            bill_address1="10 Lambton Quay",
            bill_address2="",
            bill_city="Wellington",
            bill_state="Wellington",
            bill_zip="6011",
            ship_address1="20 Queen Street",
            ship_address2="",
            ship_city="Auckland",
            ship_state="Auckland",
            ship_zip="1010",
            lines=[],
            subtotal=Decimal("0"),
            tax_rate=Decimal("0"),
            tax_amount=Decimal("0"),
            total=Decimal("0"),
            amount_paid=Decimal("0"),
            balance_due=Decimal("0"),
            notes=None,
        )
        estimate = SimpleNamespace(
            estimate_number="E-1001",
            date=date(2026, 4, 13),
            expiration_date=None,
            status=SimpleNamespace(value="draft"),
            customer=SimpleNamespace(name="Aroha Ltd"),
            bill_address1="10 Lambton Quay",
            bill_address2="",
            bill_city="Wellington",
            bill_state="Wellington",
            bill_zip="6011",
            lines=[],
            subtotal=Decimal("0"),
            tax_rate=Decimal("0"),
            tax_amount=Decimal("0"),
            total=Decimal("0"),
            notes=None,
        )
        customer = SimpleNamespace(
            name="Aroha Ltd",
            bill_address1="10 Lambton Quay",
            bill_address2="",
            bill_city="Wellington",
            bill_state="Wellington",
            bill_zip="6011",
        )

        pdf_service.generate_invoice_pdf(invoice, self.company)
        pdf_service.generate_estimate_pdf(estimate, self.company)
        pdf_service.generate_statement_pdf(customer, [], [], self.company, as_of_date=date(2026, 4, 30))

        rendered = "\n".join(CapturingHTML.rendered)
        self.assertIn("Auckland Auckland 1010", rendered)
        self.assertIn("Wellington Wellington 6011", rendered)
        self.assertNotIn("Auckland, Auckland 1010", rendered)
        self.assertNotIn("Wellington, Wellington 6011", rendered)

    def test_invoice_email_address_uses_nz_city_region_postcode_line(self):
        invoice = SimpleNamespace(
            invoice_number="1001",
            date=date(2026, 4, 13),
            due_date=date(2026, 4, 20),
            terms="Net 7",
            balance_due=Decimal("123.45"),
            notes=None,
            customer=SimpleNamespace(name="Aroha Ltd"),
        )

        html = email_service.render_invoice_email(invoice, self.company)

        self.assertIn("Auckland Auckland 1010", html)
        self.assertNotIn("Auckland, Auckland 1010", html)


if __name__ == "__main__":
    unittest.main()
