import os
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.database import Base
from app.models.accounts import Account, AccountType
from app.models.settings import Settings


class SystemAccountRoleAdminTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def _set_setting(self, db, key, value):
        db.add(Settings(key=key, value=str(value)))
        db.commit()

    def test_list_system_account_roles_reports_configured_fallback_and_missing_states(self):
        from app.routes.accounts import list_system_account_roles

        with self.Session() as db:
            configured = Account(name="Trade Debtors", account_number="610", account_type=AccountType.ASSET)
            fallback = Account(name="Operating Account", account_number="090", account_type=AccountType.ASSET)
            db.add_all([configured, fallback])
            db.commit()
            self._set_setting(db, "system_account_accounts_receivable_id", configured.id)

            rows = {row["role_key"]: row for row in list_system_account_roles(db=db)}

        self.assertEqual(rows["system_account_accounts_receivable_id"]["status"], "configured")
        self.assertEqual(rows["system_account_accounts_receivable_id"]["resolved_account"].id, configured.id)
        self.assertEqual(rows["system_account_default_bank_id"]["status"], "fallback")
        self.assertEqual(rows["system_account_default_bank_id"]["resolved_account"].id, fallback.id)
        self.assertEqual(rows["system_account_default_sales_income_id"]["status"], "missing")

    def test_update_system_account_role_accepts_valid_active_account(self):
        from app.routes.accounts import update_system_account_role
        from app.schemas.accounts import SystemAccountRoleUpdate

        with self.Session() as db:
            income = Account(name="Consulting Income", account_number="410", account_type=AccountType.INCOME)
            db.add(income)
            db.commit()

            updated = update_system_account_role(
                "system_account_default_sales_income_id",
                SystemAccountRoleUpdate(account_id=income.id),
                db=db,
            )

        self.assertEqual(updated["status"], "configured")
        self.assertEqual(updated["configured_account"].id, income.id)

    def test_update_system_account_role_rejects_wrong_account_type(self):
        from app.routes.accounts import update_system_account_role
        from app.schemas.accounts import SystemAccountRoleUpdate

        with self.Session() as db:
            wrong = Account(name="Operating Account", account_number="090", account_type=AccountType.ASSET)
            db.add(wrong)
            db.commit()

            with self.assertRaises(HTTPException) as ctx:
                update_system_account_role(
                    "system_account_default_sales_income_id",
                    SystemAccountRoleUpdate(account_id=wrong.id),
                    db=db,
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("income", ctx.exception.detail)

    def test_update_system_account_role_rejects_inactive_account(self):
        from app.routes.accounts import update_system_account_role
        from app.schemas.accounts import SystemAccountRoleUpdate

        with self.Session() as db:
            inactive = Account(
                name="Dormant PAYE Liability",
                account_number="2310",
                account_type=AccountType.LIABILITY,
                is_active=False,
            )
            db.add(inactive)
            db.commit()

            with self.assertRaises(HTTPException) as ctx:
                update_system_account_role(
                    "system_account_paye_payable_id",
                    SystemAccountRoleUpdate(account_id=inactive.id),
                    db=db,
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "Account must be active")

    def test_clear_role_mapping_returns_to_fallback_status(self):
        from app.routes.accounts import update_system_account_role
        from app.schemas.accounts import SystemAccountRoleUpdate

        with self.Session() as db:
            explicit = Account(name="Receivables Control", account_number="611", account_type=AccountType.ASSET)
            fallback = Account(name="Accounts Receivable", account_number="1100", account_type=AccountType.ASSET)
            db.add_all([explicit, fallback])
            db.commit()
            self._set_setting(db, "system_account_accounts_receivable_id", explicit.id)

            cleared = update_system_account_role(
                "system_account_accounts_receivable_id",
                SystemAccountRoleUpdate(account_id=None),
                db=db,
            )

        self.assertEqual(cleared["status"], "fallback")
        self.assertIsNone(cleared["configured_account"])
        self.assertEqual(cleared["resolved_account"].id, fallback.id)


if __name__ == "__main__":
    unittest.main()
