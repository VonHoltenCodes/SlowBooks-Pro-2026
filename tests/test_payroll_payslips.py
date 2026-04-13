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

from app.database import Base


class PayrollPayslipRouteTests(unittest.TestCase):
    def setUp(self):
        from app.models.accounts import Account  # noqa: F401
        from app.models.payroll import Employee, PayRun, PayStub  # noqa: F401
        from app.models.settings import Settings  # noqa: F401
        from app.models.transactions import Transaction  # noqa: F401

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def _create_employee(self, db, **overrides):
        from app.routes.employees import create_employee
        from app.schemas.payroll import EmployeeCreate

        data = {
            "first_name": "Aroha",
            "last_name": "Ngata",
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
        }
        data.update(overrides)
        return create_employee(EmployeeCreate(**data), db=db)

    def _seed_company(self, db):
        from app.models.settings import Settings

        db.add_all([
            Settings(key="company_name", value="SlowBooks NZ"),
            Settings(key="company_address1", value="123 Harbour Street"),
            Settings(key="company_city", value="Auckland"),
            Settings(key="company_state", value="Auckland"),
            Settings(key="company_zip", value="1010"),
            Settings(key="ird_number", value="123-456-789"),
        ])
        db.commit()

    def _processed_run(self, db):
        from app.routes.payroll import create_pay_run, process_pay_run
        from app.schemas.payroll import PayRunCreate, PayStubInput

        employee = self._create_employee(db)
        draft = create_pay_run(PayRunCreate(
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 14),
            pay_date=date(2026, 4, 15),
            stubs=[PayStubInput(employee_id=employee.id)],
        ), db=db)
        processed = process_pay_run(draft.id, db=db)
        return employee, processed

    def test_processed_pay_run_returns_payslip_pdf(self):
        from app.routes.payroll import payroll_payslip_pdf

        with self.Session() as db:
            self._seed_company(db)
            employee, processed = self._processed_run(db)
            response = payroll_payslip_pdf(processed.id, employee.id, db=db)

        self.assertEqual(response.media_type, "application/pdf")
        self.assertIn(f"PaySlip_{processed.id}_{employee.id}.pdf", response.headers["Content-Disposition"])
        body = response.body.decode()
        self.assertIn("Payslip", body)
        self.assertIn("Aroha Ngata", body)
        self.assertIn("15 Apr 2026", body)
        self.assertIn("PAYE", body)
        self.assertIn("Net Pay", body)

    def test_draft_pay_run_payslip_is_rejected(self):
        from fastapi import HTTPException

        from app.routes.payroll import create_pay_run, payroll_payslip_pdf
        from app.schemas.payroll import PayRunCreate, PayStubInput

        with self.Session() as db:
            self._seed_company(db)
            employee = self._create_employee(db)
            draft = create_pay_run(PayRunCreate(
                period_start=date(2026, 4, 1),
                period_end=date(2026, 4, 14),
                pay_date=date(2026, 4, 15),
                stubs=[PayStubInput(employee_id=employee.id)],
            ), db=db)
            with self.assertRaises(HTTPException) as ctx:
                payroll_payslip_pdf(draft.id, employee.id, db=db)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("processed", ctx.exception.detail.lower())

    def test_employee_not_in_run_is_rejected(self):
        from fastapi import HTTPException

        from app.routes.payroll import payroll_payslip_pdf

        with self.Session() as db:
            self._seed_company(db)
            employee, processed = self._processed_run(db)
            outsider = self._create_employee(db, first_name="Wiremu", last_name="Kingi", pay_type="hourly", pay_rate=30)
            with self.assertRaises(HTTPException) as ctx:
                payroll_payslip_pdf(processed.id, outsider.id, db=db)

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("not found", ctx.exception.detail.lower())


if __name__ == "__main__":
    unittest.main()
