import os
import unittest
from datetime import date

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.database import Base


class NzPayrollDataModelTests(unittest.TestCase):
    def setUp(self):
        from app.models.payroll import Employee, PayRun, PayStub  # noqa: F401

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def test_employee_create_and_response_use_nz_payroll_fields(self):
        from app.routes.employees import create_employee
        from app.schemas.payroll import EmployeeCreate, EmployeeResponse

        with self.Session() as db:
            employee = create_employee(EmployeeCreate(
                first_name="Aroha",
                last_name="Ngata",
                ird_number="123-456-789",
                pay_type="salary",
                pay_rate=85000,
                tax_code="M",
                kiwisaver_enrolled=True,
                kiwisaver_rate="0.0350",
                student_loan=True,
                child_support=True,
                child_support_amount="125.00",
                esct_rate="0.3000",
                pay_frequency="fortnightly",
                start_date=date(2026, 4, 1),
            ), db=db)
            response = EmployeeResponse.model_validate(employee).model_dump()

        self.assertEqual(response["ird_number"], "123-456-789")
        self.assertEqual(response["tax_code"], "M")
        self.assertTrue(response["kiwisaver_enrolled"])
        self.assertEqual(str(response["kiwisaver_rate"]), "0.0350")
        self.assertTrue(response["child_support"])
        self.assertEqual(str(response["child_support_amount"]), "125.00")
        self.assertEqual(response["pay_frequency"], "fortnightly")
        self.assertNotIn("ssn_last_four", response)
        self.assertNotIn("filing_status", response)
        self.assertNotIn("allowances", response)

    def test_employee_address_fields_round_trip_through_create_update_and_response(self):
        from app.routes.employees import create_employee, get_employee, update_employee
        from app.schemas.payroll import EmployeeCreate, EmployeeResponse, EmployeeUpdate

        with self.Session() as db:
            employee = create_employee(EmployeeCreate(
                first_name="Hemi",
                last_name="Rangi",
                ird_number="222-333-444",
                tax_code="M",
                address1="12 Karangahape Road",
                address2="Unit 3",
                city="Auckland",
                state="Auckland",
                zip="1010",
            ), db=db)
            updated = update_employee(employee.id, EmployeeUpdate(
                address1="99 Cuba Street",
                city="Wellington",
                state="Wellington",
                zip="6011",
            ), db=db)
            fetched = get_employee(employee.id, db=db)
            response = EmployeeResponse.model_validate(fetched).model_dump()

        self.assertEqual(updated.address1, "99 Cuba Street")
        self.assertEqual(updated.address2, "Unit 3")
        self.assertEqual(updated.city, "Wellington")
        self.assertEqual(updated.state, "Wellington")
        self.assertEqual(updated.zip, "6011")
        self.assertEqual(response["address1"], "99 Cuba Street")
        self.assertEqual(response["address2"], "Unit 3")
        self.assertEqual(response["city"], "Wellington")
        self.assertEqual(response["state"], "Wellington")
        self.assertEqual(response["zip"], "6011")

    def test_employee_update_supports_nz_fields(self):
        from app.routes.employees import create_employee, update_employee
        from app.schemas.payroll import EmployeeCreate, EmployeeUpdate

        with self.Session() as db:
            employee = create_employee(EmployeeCreate(
                first_name="Mere",
                last_name="Tai",
                ird_number="111-222-333",
                tax_code="M",
            ), db=db)
            updated = update_employee(employee.id, EmployeeUpdate(
                tax_code="ME",
                kiwisaver_enrolled=True,
                kiwisaver_rate="0.0400",
                child_support=True,
                child_support_amount="80.00",
                end_date=date(2026, 12, 31),
            ), db=db)

        self.assertEqual(updated.tax_code, "ME")
        self.assertTrue(updated.kiwisaver_enrolled)
        self.assertEqual(str(updated.kiwisaver_rate), "0.0400")
        self.assertTrue(updated.child_support)
        self.assertEqual(str(updated.child_support_amount), "80.00")
        self.assertEqual(updated.end_date, date(2026, 12, 31))

    def test_pay_stub_model_uses_nz_deduction_fields(self):
        from app.models.payroll import PayStub

        columns = set(PayStub.__table__.columns.keys())

        self.assertIn("paye", columns)
        self.assertIn("acc_earners_levy", columns)
        self.assertIn("student_loan_deduction", columns)
        self.assertIn("kiwisaver_employee_deduction", columns)
        self.assertIn("employer_kiwisaver_contribution", columns)
        self.assertIn("esct", columns)
        self.assertIn("child_support_deduction", columns)
        self.assertNotIn("federal_tax", columns)
        self.assertNotIn("state_tax", columns)
        self.assertNotIn("ss_tax", columns)
        self.assertNotIn("medicare_tax", columns)

    def test_payroll_routes_create_draft_and_process_run(self):
        from app.routes.employees import create_employee
        from app.routes.payroll import create_pay_run, process_pay_run
        from app.schemas.payroll import PayRunCreate
        from app.schemas.payroll import EmployeeCreate, PayStubInput

        with self.Session() as db:
            employee = create_employee(EmployeeCreate(
                first_name="Aroha",
                last_name="Ngata",
                pay_type="salary",
                pay_rate=78000,
                tax_code="M",
                kiwisaver_enrolled=True,
                kiwisaver_rate="0.0350",
                pay_frequency="fortnightly",
            ), db=db)
            draft = create_pay_run(PayRunCreate(
                period_start=date(2026, 4, 1),
                period_end=date(2026, 4, 14),
                pay_date=date(2026, 4, 15),
                stubs=[PayStubInput(employee_id=employee.id)],
            ), db=db)
            processed = process_pay_run(draft.id, db=db)

        self.assertEqual(draft.status, "draft")
        self.assertEqual(processed.status, "processed")
        self.assertGreater(processed.total_gross, 0)
        self.assertGreater(processed.total_net, 0)


if __name__ == "__main__":
    unittest.main()
