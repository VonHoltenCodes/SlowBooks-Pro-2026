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


class PayrollFilingAuditTests(unittest.TestCase):
    def setUp(self):
        import app.models  # noqa: F401

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

    def _seed_accounts(self, db):
        from app.models.accounts import Account, AccountType

        db.add_all([
            Account(name="Accounts Receivable", account_number="1100", account_type=AccountType.ASSET),
            Account(name="Accounts Payable", account_number="2000", account_type=AccountType.LIABILITY),
            Account(name="GST", account_number="2200", account_type=AccountType.LIABILITY),
            Account(name="Sales", account_number="4000", account_type=AccountType.INCOME),
            Account(name="Expenses", account_number="6000", account_type=AccountType.EXPENSE),
            Account(name="Wages Expense", account_number="7000", account_type=AccountType.EXPENSE),
            Account(name="Employer KiwiSaver Expense", account_number="7010", account_type=AccountType.EXPENSE),
            Account(name="PAYE Payable", account_number="2310", account_type=AccountType.LIABILITY),
            Account(name="KiwiSaver Payable", account_number="2315", account_type=AccountType.LIABILITY),
            Account(name="ESCT Payable", account_number="2320", account_type=AccountType.LIABILITY),
            Account(name="Child Support Payable", account_number="2325", account_type=AccountType.LIABILITY),
            Account(name="Payroll Clearing", account_number="2330", account_type=AccountType.LIABILITY),
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
        return employee, process_pay_run(draft.id, db=db)

    def test_employee_export_creates_generated_history_and_supersedes_previous_generated(self):
        from app.routes.employees import export_starter_employee_filing, get_employee_filing_history

        with self.Session() as db:
            self._seed_settings(db)
            employee = self._create_employee(db)
            export_starter_employee_filing(employee.id, db=db)
            export_starter_employee_filing(employee.id, db=db)
            history = get_employee_filing_history(employee.id, db=db)

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].status, 'generated')
        self.assertEqual(history[1].status, 'superseded')
        self.assertFalse(history[0].changed_since_source)

    def test_employee_filing_history_marks_changed_since_source_after_relevant_change(self):
        from app.routes.employees import export_starter_employee_filing, get_employee_filing_history, update_employee
        from app.schemas.payroll import EmployeeUpdate

        with self.Session() as db:
            self._seed_settings(db)
            employee = self._create_employee(db)
            export_starter_employee_filing(employee.id, db=db)
            update_employee(employee.id, EmployeeUpdate(tax_code='M SL'), db=db)
            history = get_employee_filing_history(employee.id, db=db)

        self.assertTrue(history[0].changed_since_source)

    def test_mark_employee_filing_record_filed_and_amended(self):
        from app.routes.employees import (
            export_starter_employee_filing,
            get_employee_filing_history,
            update_employee_filing_record,
        )
        from app.schemas.payroll_filing import PayrollFilingAuditStatusUpdate

        with self.Session() as db:
            self._seed_settings(db)
            employee = self._create_employee(db)
            export_starter_employee_filing(employee.id, db=db)
            audit = get_employee_filing_history(employee.id, db=db)[0]
            filed = update_employee_filing_record(
                employee.id,
                audit.id,
                PayrollFilingAuditStatusUpdate(status='filed', reference='IR-123', notes='Filed with IRD'),
                db=db,
            )
            amended = update_employee_filing_record(
                employee.id,
                audit.id,
                PayrollFilingAuditStatusUpdate(status='amended', reference='IR-124', notes='Corrected filing'),
                db=db,
            )

        self.assertEqual(filed.status, 'filed')
        self.assertEqual(filed.export_reference, 'IR-123')
        self.assertEqual(amended.status, 'amended')
        self.assertEqual(amended.export_reference, 'IR-124')

    def test_pay_run_export_creates_history_and_detects_changed_source(self):
        from app.models.payroll import PayStub
        from app.routes.payroll import export_employment_information, get_pay_run_filing_history

        with self.Session() as db:
            self._seed_settings(db)
            self._seed_accounts(db)
            _employee, pay_run = self._processed_run(db)
            export_employment_information(pay_run.id, db=db)
            stub = db.query(PayStub).filter(PayStub.pay_run_id == pay_run.id).first()
            stub.child_support_deduction = Decimal('10.00')
            db.commit()
            history = get_pay_run_filing_history(pay_run.id, db=db)

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].filing_type, 'employment_information')
        self.assertTrue(history[0].changed_since_source)


if __name__ == '__main__':
    unittest.main()
