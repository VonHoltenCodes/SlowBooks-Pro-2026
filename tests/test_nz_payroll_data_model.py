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
                kiwisaver_rate="0.0300",
                student_loan=True,
                child_support=False,
                esct_rate="0.3000",
                pay_frequency="fortnightly",
                start_date=date(2026, 4, 1),
            ), db=db)
            response = EmployeeResponse.model_validate(employee).model_dump()

        self.assertEqual(response["ird_number"], "123-456-789")
        self.assertEqual(response["tax_code"], "M")
        self.assertTrue(response["kiwisaver_enrolled"])
        self.assertEqual(response["pay_frequency"], "fortnightly")
        self.assertNotIn("ssn_last_four", response)
        self.assertNotIn("filing_status", response)
        self.assertNotIn("allowances", response)

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
                end_date=date(2026, 12, 31),
            ), db=db)

        self.assertEqual(updated.tax_code, "ME")
        self.assertTrue(updated.kiwisaver_enrolled)
        self.assertEqual(str(updated.kiwisaver_rate), "0.0400")
        self.assertEqual(updated.end_date, date(2026, 12, 31))

    def test_payroll_endpoints_are_disabled_until_paye_slice(self):
        from app.routes.payroll import PAYROLL_NZ_PLACEHOLDER_DETAIL, create_pay_run, process_pay_run
        from app.schemas.payroll import PayRunCreate

        with self.assertRaises(HTTPException) as create_ctx:
            create_pay_run(PayRunCreate(
                period_start=date(2026, 4, 1),
                period_end=date(2026, 4, 14),
                pay_date=date(2026, 4, 15),
                stubs=[],
            ))
        self.assertEqual(create_ctx.exception.status_code, 410)
        self.assertEqual(create_ctx.exception.detail, PAYROLL_NZ_PLACEHOLDER_DETAIL)

        with self.assertRaises(HTTPException) as process_ctx:
            process_pay_run(1)
        self.assertEqual(process_ctx.exception.status_code, 410)
        self.assertEqual(process_ctx.exception.detail, PAYROLL_NZ_PLACEHOLDER_DETAIL)


if __name__ == "__main__":
    unittest.main()
