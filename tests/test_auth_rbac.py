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


class AuthRbacTests(unittest.TestCase):
    def setUp(self):
        import app.models  # noqa: F401

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def _seed_payroll_basics(self, db):
        from app.models.accounts import Account, AccountType
        from app.models.contacts import Customer, Vendor

        customer = Customer(name="Aroha Ltd")
        vendor = Vendor(name="Harbour Supplies")
        db.add_all([
            customer,
            vendor,
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
        return customer, vendor

    def test_bootstrap_admin_creates_owner_session_and_me_context(self):
        from app.routes.auth import bootstrap_admin, me
        from app.schemas.auth import BootstrapAdminRequest

        with self.Session() as db:
            response = bootstrap_admin(BootstrapAdminRequest(
                email="owner@example.com",
                password="supersecret",
                full_name="Owner User",
            ), db=db)
            me_response = me(db=db, auth=None)

            self.assertTrue(response.token)
            self.assertEqual(response.user.email, "owner@example.com")
            self.assertEqual(response.user.membership.role_key, "owner")
            self.assertIn("users.manage", response.user.membership.effective_permissions)
            self.assertFalse(me_response.authenticated)

    def test_login_and_permission_override_support_granular_payroll_access(self):
        from app.routes.auth import bootstrap_admin, create_user, login
        from app.schemas.auth import BootstrapAdminRequest, LoginRequest, UserCreateRequest
        from app.services.auth import require_permissions

        with self.Session() as db:
            owner = bootstrap_admin(BootstrapAdminRequest(
                email="owner@example.com",
                password="supersecret",
                full_name="Owner User",
            ), db=db)
            owner_checker = require_permissions("users.manage")
            owner_auth = owner_checker(db=db, authorization=f"Bearer {owner.token}")

            create_user(UserCreateRequest(
                email="viewer@example.com",
                password="viewersecret",
                full_name="Viewer User",
                role_key="payroll_viewer",
                allow_permissions=["payroll.process"],
                deny_permissions=[],
            ), db=db, auth=owner_auth)

            viewer_login = login(LoginRequest(email="viewer@example.com", password="viewersecret"), db=db)
            process_auth = require_permissions("payroll.process")(db=db, authorization=f"Bearer {viewer_login.token}")

            self.assertEqual(process_auth.user.email, "viewer@example.com")
            self.assertIn("payroll.process", process_auth.permissions)
            with self.assertRaises(Exception):
                require_permissions("users.manage")(db=db, authorization=f"Bearer {viewer_login.token}")

    def test_payroll_and_employee_routes_require_permissions(self):
        from fastapi import HTTPException
        from app.routes.auth import bootstrap_admin, create_user, login
        from app.routes.employees import create_employee, list_employees
        from app.routes.payroll import create_pay_run, process_pay_run
        from app.schemas.auth import BootstrapAdminRequest, LoginRequest, UserCreateRequest
        from app.schemas.payroll import EmployeeCreate, PayRunCreate, PayStubInput
        from app.services.auth import require_permissions

        with self.Session() as db:
            self._seed_payroll_basics(db)
            from app.models.settings import Settings
            db.add_all([
                Settings(key="ird_number", value="987654321"),
                Settings(key="payroll_contact_name", value="Bill Smith"),
                Settings(key="payroll_contact_phone", value="041234567"),
                Settings(key="payroll_contact_email", value="payroll@email.com"),
            ])
            db.commit()
            owner = bootstrap_admin(BootstrapAdminRequest(
                email="owner@example.com",
                password="supersecret",
                full_name="Owner User",
            ), db=db)
            owner_auth = require_permissions("users.manage")(db=db, authorization=f"Bearer {owner.token}")

            create_user(UserCreateRequest(
                email="viewer@example.com",
                password="viewersecret",
                full_name="Viewer User",
                role_key="payroll_viewer",
                allow_permissions=[],
                deny_permissions=[],
            ), db=db, auth=owner_auth)
            viewer_login = login(LoginRequest(email="viewer@example.com", password="viewersecret"), db=db)

            with self.assertRaises(HTTPException) as unauth_ctx:
                require_permissions("employees.view_private")(db=db, authorization=None)
            self.assertEqual(unauth_ctx.exception.status_code, 401)

            viewer_auth = require_permissions("employees.view_private")(db=db, authorization=f"Bearer {viewer_login.token}")
            manage_checker = require_permissions("employees.manage")
            with self.assertRaises(HTTPException) as manage_ctx:
                manage_checker(db=db, authorization=f"Bearer {viewer_login.token}")
            self.assertEqual(manage_ctx.exception.status_code, 403)

            owner_employee_auth = require_permissions("employees.manage")(db=db, authorization=f"Bearer {owner.token}")
            employee = create_employee(EmployeeCreate(
                first_name="Aroha",
                last_name="Ngata",
                ird_number="123456789",
                pay_type="salary",
                pay_rate=85000,
                tax_code="M",
                pay_frequency="fortnightly",
            ), db=db, auth=owner_employee_auth)

            listed = list_employees(active_only=True, db=db, auth=viewer_auth)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].first_name, "Aroha")

            owner_payroll_auth = require_permissions("payroll.create")(db=db, authorization=f"Bearer {owner.token}")
            run = create_pay_run(PayRunCreate(
                period_start=date(2026, 4, 1),
                period_end=date(2026, 4, 14),
                pay_date=date(2026, 4, 15),
                stubs=[PayStubInput(employee_id=employee.id, hours=0)],
            ), db=db, auth=owner_payroll_auth)

            with self.assertRaises(HTTPException) as process_ctx:
                require_permissions("payroll.process")(db=db, authorization=f"Bearer {viewer_login.token}")
            self.assertEqual(process_ctx.exception.status_code, 403)

    def test_filing_history_and_status_updates_follow_existing_payroll_permissions(self):
        from fastapi import HTTPException
        from app.routes.auth import bootstrap_admin, create_user, login
        from app.routes.employees import create_employee, export_starter_employee_filing, get_employee_filing_history, update_employee_filing_record
        from app.routes.payroll import create_pay_run, process_pay_run, export_employment_information, get_pay_run_filing_history, update_pay_run_filing_record
        from app.schemas.auth import BootstrapAdminRequest, LoginRequest, UserCreateRequest
        from app.schemas.payroll import EmployeeCreate, PayRunCreate, PayStubInput
        from app.schemas.payroll_filing import PayrollFilingAuditStatusUpdate
        from app.services.auth import require_permissions

        with self.Session() as db:
            self._seed_payroll_basics(db)
            from app.models.settings import Settings
            db.add_all([
                Settings(key="ird_number", value="987654321"),
                Settings(key="payroll_contact_name", value="Bill Smith"),
                Settings(key="payroll_contact_phone", value="041234567"),
                Settings(key="payroll_contact_email", value="payroll@email.com"),
            ])
            db.commit()
            owner = bootstrap_admin(BootstrapAdminRequest(
                email="owner@example.com",
                password="supersecret",
                full_name="Owner User",
            ), db=db)
            owner_auth = require_permissions("users.manage")(db=db, authorization=f"Bearer {owner.token}")
            create_user(UserCreateRequest(
                email="viewer@example.com",
                password="viewersecret",
                full_name="Viewer User",
                role_key="payroll_viewer",
                allow_permissions=[],
                deny_permissions=[],
            ), db=db, auth=owner_auth)
            viewer_login = login(LoginRequest(email="viewer@example.com", password="viewersecret"), db=db)
            viewer_auth = require_permissions("employees.view_private")(db=db, authorization=f"Bearer {viewer_login.token}")

            employee = create_employee(EmployeeCreate(
                first_name="Aroha",
                last_name="Ngata",
                ird_number="123456789",
                pay_type="salary",
                pay_rate=85000,
                tax_code="M",
                pay_frequency="fortnightly",
                start_date=date(2026, 4, 1),
            ), db=db, auth=require_permissions("employees.manage")(db=db, authorization=f"Bearer {owner.token}"))
            export_starter_employee_filing(employee.id, db=db, auth=require_permissions("employees.filing.export")(db=db, authorization=f"Bearer {owner.token}"))
            employee_history = get_employee_filing_history(employee.id, db=db, auth=viewer_auth)
            self.assertEqual(len(employee_history), 1)

            with self.assertRaises(HTTPException) as employee_update_ctx:
                require_permissions("employees.filing.export")(db=db, authorization=f"Bearer {viewer_login.token}")
            self.assertEqual(employee_update_ctx.exception.status_code, 403)

            run = create_pay_run(PayRunCreate(
                period_start=date(2026, 4, 1),
                period_end=date(2026, 4, 14),
                pay_date=date(2026, 4, 15),
                stubs=[PayStubInput(employee_id=employee.id, hours=0)],
            ), db=db, auth=require_permissions("payroll.create")(db=db, authorization=f"Bearer {owner.token}"))
            processed = process_pay_run(run.id, db=db, auth=require_permissions("payroll.process")(db=db, authorization=f"Bearer {owner.token}"))
            export_employment_information(processed.id, db=db, auth=require_permissions("payroll.filing.export")(db=db, authorization=f"Bearer {owner.token}"))
            pay_run_history = get_pay_run_filing_history(processed.id, db=db, auth=require_permissions("payroll.view")(db=db, authorization=f"Bearer {viewer_login.token}"))
            self.assertEqual(len(pay_run_history), 1)

            with self.assertRaises(HTTPException) as payroll_update_ctx:
                require_permissions("payroll.filing.export")(db=db, authorization=f"Bearer {viewer_login.token}")
            self.assertEqual(payroll_update_ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
