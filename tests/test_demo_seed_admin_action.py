import os
import sys
import types
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

weasyprint_stub = types.ModuleType("weasyprint")
weasyprint_stub.HTML = object
sys.modules.setdefault("weasyprint", weasyprint_stub)

from app.database import Base


class DemoSeedAdminActionTests(unittest.TestCase):
    def setUp(self):
        import app.models  # noqa: F401

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def _bootstrap_owner(self, db):
        from app.routes.auth import bootstrap_admin
        from app.schemas.auth import BootstrapAdminRequest
        from app.services.auth import require_permissions

        owner = bootstrap_admin(BootstrapAdminRequest(
            email="owner@example.com",
            password="supersecret",
            full_name="Owner User",
        ), db=db)
        owner_auth = require_permissions("settings.manage")(db=db, authorization=f"Bearer {owner.token}")
        return owner, owner_auth

    def test_load_demo_data_requires_admin_permission_and_runs_seed(self):
        from fastapi import HTTPException
        from app.routes.auth import create_user, login
        from app.routes.settings import load_demo_data
        from app.schemas.auth import LoginRequest, UserCreateRequest
        from app.services.auth import require_permissions

        with self.Session() as db:
            _owner, owner_auth = self._bootstrap_owner(db)
            create_user(UserCreateRequest(
                email="staff@example.com",
                password="staffsecret1",
                full_name="Staff User",
                role_key="staff",
                allow_permissions=[],
                deny_permissions=[],
                is_active=True,
            ), db=db, auth=owner_auth)
            login_response = login(LoginRequest(email="staff@example.com", password="staffsecret1"), db=db)

            with self.assertRaises(HTTPException):
                require_permissions("settings.manage")(db=db, authorization=f"Bearer {login_response.token}")

            with mock.patch("app.routes.settings.run_demo_seed") as seed_mock:
                result = load_demo_data(db=db, auth=owner_auth)

            seed_mock.assert_called_once_with()
            self.assertEqual(result["status"], "loaded")

    def test_load_chart_template_requires_admin_permission_and_runs_loader(self):
        from fastapi import HTTPException
        from app.routes.auth import create_user, login
        from app.routes.settings import load_chart_template
        from app.schemas.auth import LoginRequest, UserCreateRequest
        from app.services.auth import require_permissions

        with self.Session() as db:
            _owner, owner_auth = self._bootstrap_owner(db)
            create_user(UserCreateRequest(
                email="staff@example.com",
                password="staffsecret1",
                full_name="Staff User",
                role_key="staff",
                allow_permissions=[],
                deny_permissions=[],
                is_active=True,
            ), db=db, auth=owner_auth)
            login_response = login(LoginRequest(email="staff@example.com", password="staffsecret1"), db=db)

            with self.assertRaises(HTTPException):
                require_permissions("settings.manage")(db=db, authorization=f"Bearer {login_response.token}")

            with mock.patch("app.routes.settings.run_chart_template_load", return_value={"status": "loaded", "template_key": "xero"}) as load_mock:
                result = load_chart_template('xero', db=db, auth=owner_auth)

            load_mock.assert_called_once_with(db, 'xero')
            self.assertEqual(result["template_key"], "xero")


if __name__ == "__main__":
    unittest.main()
