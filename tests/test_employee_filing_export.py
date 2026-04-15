import os
import sys
import types
import unittest
from datetime import date

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

weasyprint_stub = types.ModuleType("weasyprint")
weasyprint_stub.HTML = object
sys.modules.setdefault("weasyprint", weasyprint_stub)

from app.database import Base


class EmployeeFilingExportTests(unittest.TestCase):
    def setUp(self):
        from app.models.payroll import Employee  # noqa: F401
        from app.models.settings import Settings  # noqa: F401

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def _seed_settings(self, db):
        from app.models.settings import Settings

        db.add_all([
            Settings(key="company_name", value="SlowBooks NZ"),
            Settings(key="ird_number", value="987654321"),
            Settings(key="payroll_contact_name", value="Bill Smith"),
            Settings(key="payroll_contact_phone", value="041234567"),
            Settings(key="payroll_contact_email", value="payroll@email.com"),
        ])
        db.commit()

    def _create_employee(self, db, **overrides):
        from app.routes.employees import create_employee
        from app.schemas.payroll import EmployeeCreate

        payload = {
            "first_name": "Aroha",
            "last_name": "Ngata",
            "ird_number": "123456789",
            "pay_type": "salary",
            "pay_rate": 78000,
            "tax_code": "M",
            "kiwisaver_enrolled": True,
            "kiwisaver_rate": "0.0350",
            "student_loan": False,
            "child_support": False,
            "child_support_amount": "0.00",
            "esct_rate": "0.1750",
            "pay_frequency": "fortnightly",
            "start_date": date(2026, 4, 1),
            "end_date": None,
        }
        payload.update(overrides)
        return create_employee(EmployeeCreate(**payload), db=db)

    def test_starter_export_uses_start_date(self):
        from app.routes.employees import export_starter_employee_filing

        with self.Session() as db:
            self._seed_settings(db)
            employee = self._create_employee(db, start_date=date(2026, 4, 1))
            response = export_starter_employee_filing(employee.id, db=db)

        self.assertEqual(response.media_type, "text/csv")
        self.assertIn("StarterEmployee_1.csv", response.headers["Content-Disposition"])
        content = response.body.decode()
        self.assertIn("SED,987654321,123456789,Aroha Ngata", content)
        self.assertIn("20260401", content)
        self.assertIn(",M,", content)

    def test_leaver_export_uses_end_date(self):
        from app.routes.employees import export_leaver_employee_filing

        with self.Session() as db:
            self._seed_settings(db)
            employee = self._create_employee(db, start_date=date(2026, 1, 1), end_date=date(2026, 4, 30))
            response = export_leaver_employee_filing(employee.id, db=db)

        self.assertEqual(response.media_type, "text/csv")
        content = response.body.decode()
        self.assertIn("LED,987654321,123456789,Aroha Ngata", content)
        self.assertIn("20260430", content)

    def test_employee_address_fields_do_not_change_starter_export_shape(self):
        from app.routes.employees import export_starter_employee_filing

        with self.Session() as db:
            self._seed_settings(db)
            employee = self._create_employee(
                db,
                start_date=date(2026, 4, 1),
                address1="10 Queen Street",
                address2="Unit 2",
                city="Auckland",
                state="Auckland",
                zip="1010",
            )
            response = export_starter_employee_filing(employee.id, db=db)

        row = response.body.decode().strip().split(',')
        self.assertEqual(row[0], 'SED')
        self.assertEqual(row[3], 'Aroha Ngata')
        self.assertEqual(len(row), 8)

    def test_missing_required_date_is_rejected(self):
        from app.routes.employees import export_starter_employee_filing

        with self.Session() as db:
            self._seed_settings(db)
            employee = self._create_employee(db, start_date=None)
            with self.assertRaises(HTTPException) as ctx:
                export_starter_employee_filing(employee.id, db=db)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("start date", ctx.exception.detail.lower())


if __name__ == "__main__":
    unittest.main()
