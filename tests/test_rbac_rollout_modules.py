import asyncio
import importlib
import os
import sys
import types
import unittest
from datetime import date
from io import BytesIO
from unittest import mock

from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

weasyprint_stub = types.ModuleType('weasyprint')
weasyprint_stub.HTML = object
sys.modules.setdefault('weasyprint', weasyprint_stub)

from app.database import Base


class RbacRolloutModuleTests(unittest.TestCase):
    def setUp(self):
        import app.models  # noqa: F401

        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def _bootstrap_owner(self, db):
        from app.routes.auth import bootstrap_admin
        from app.schemas.auth import BootstrapAdminRequest

        return bootstrap_admin(BootstrapAdminRequest(
            email='owner@example.com',
            password='supersecret',
            full_name='Owner User',
        ), db=db)

    def _create_view_user(self, db, owner_token):
        from app.routes.auth import create_user, login
        from app.schemas.auth import LoginRequest, UserCreateRequest
        from app.services.auth import require_permissions

        owner_auth = require_permissions('users.manage')(db=db, authorization=f'Bearer {owner_token}')
        create_user(UserCreateRequest(
            email='viewer@example.com',
            password='viewersecret',
            full_name='Viewer User',
            role_key='staff',
            allow_permissions=[
                'contacts.view',
                'items.view',
                'sales.view',
                'purchasing.view',
                'banking.view',
                'import_export.view',
            ],
            deny_permissions=[],
        ), db=db, auth=owner_auth)
        return login(LoginRequest(email='viewer@example.com', password='viewersecret'), db=db)

    def _load_csv_routes(self):
        sys.modules.pop('app.routes.csv', None)
        with mock.patch('fastapi.dependencies.utils.ensure_multipart_is_installed', return_value=None):
            return importlib.import_module('app.routes.csv')

    def test_auth_meta_exposes_new_business_permissions_and_operations_admin_role(self):
        from app.routes.auth import auth_meta
        from app.services.auth import require_permissions

        with self.Session() as db:
            owner = self._bootstrap_owner(db)
            meta = auth_meta(db=db, auth=require_permissions('users.manage')(db=db, authorization=f'Bearer {owner.token}'))

        permission_keys = {entry.key for entry in meta.permissions}
        self.assertTrue({
            'contacts.view', 'contacts.manage',
            'items.view', 'items.manage',
            'sales.view', 'sales.manage',
            'purchasing.view', 'purchasing.manage',
            'banking.view', 'banking.manage',
            'import_export.view', 'import_export.manage',
        }.issubset(permission_keys))
        operations_admin = next(role for role in meta.roles if role.key == 'operations_admin')
        self.assertIn('sales.manage', operations_admin.permissions)
        self.assertIn('banking.manage', operations_admin.permissions)
        self.assertIn('import_export.manage', operations_admin.permissions)

    def test_contacts_items_sales_purchasing_banking_and_import_export_routes_require_new_permissions(self):
        from app.routes.auth import me
        from app.routes.banking import BankAccountCreate, create_bank_account, list_bank_accounts
        from app.routes.customers import list_customers
        from app.routes.estimates import create_estimate, list_estimates
        from app.routes.items import ItemCreate, list_items
        from app.routes.purchase_orders import create_po, list_pos
        from app.schemas.estimates import EstimateCreate, EstimateLineCreate
        from app.schemas.purchase_orders import POCreate, POLineCreate
        from app.services.auth import require_permissions

        csv_routes = self._load_csv_routes()

        with self.Session() as db:
            owner = self._bootstrap_owner(db)
            viewer = self._create_view_user(db, owner.token)

            viewer_contacts = require_permissions('contacts.view')(db=db, authorization=f'Bearer {viewer.token}')
            viewer_items = require_permissions('items.view')(db=db, authorization=f'Bearer {viewer.token}')
            viewer_sales = require_permissions('sales.view')(db=db, authorization=f'Bearer {viewer.token}')
            viewer_purchasing = require_permissions('purchasing.view')(db=db, authorization=f'Bearer {viewer.token}')
            viewer_banking = require_permissions('banking.view')(db=db, authorization=f'Bearer {viewer.token}')
            viewer_import_export = require_permissions('import_export.view')(db=db, authorization=f'Bearer {viewer.token}')

            self.assertIsInstance(list_customers(db=db, auth=viewer_contacts), list)
            self.assertIsInstance(list_items(db=db, auth=viewer_items), list)
            self.assertIsInstance(list_estimates(db=db, auth=viewer_sales), list)
            self.assertIsInstance(list_pos(db=db, auth=viewer_purchasing), list)
            self.assertIsInstance(list_bank_accounts(db=db, auth=viewer_banking), list)
            export_response = csv_routes.csv_export_customers(db=db, auth=viewer_import_export)
            self.assertEqual(export_response.media_type, 'text/csv')

            with self.assertRaises(HTTPException) as contacts_manage_ctx:
                require_permissions('contacts.manage')(db=db, authorization=f'Bearer {viewer.token}')
            self.assertEqual(contacts_manage_ctx.exception.status_code, 403)

            with self.assertRaises(HTTPException) as items_manage_ctx:
                require_permissions('items.manage')(db=db, authorization=f'Bearer {viewer.token}')
            self.assertEqual(items_manage_ctx.exception.status_code, 403)

            with self.assertRaises(HTTPException) as sales_manage_ctx:
                require_permissions('sales.manage')(db=db, authorization=f'Bearer {viewer.token}')
            self.assertEqual(sales_manage_ctx.exception.status_code, 403)

            with self.assertRaises(HTTPException) as purchasing_manage_ctx:
                require_permissions('purchasing.manage')(db=db, authorization=f'Bearer {viewer.token}')
            self.assertEqual(purchasing_manage_ctx.exception.status_code, 403)

            with self.assertRaises(HTTPException) as banking_manage_ctx:
                require_permissions('banking.manage')(db=db, authorization=f'Bearer {viewer.token}')
            self.assertEqual(banking_manage_ctx.exception.status_code, 403)

            with self.assertRaises(HTTPException) as import_manage_ctx:
                require_permissions('import_export.manage')(db=db, authorization=f'Bearer {viewer.token}')
            self.assertEqual(import_manage_ctx.exception.status_code, 403)

            owner_items = require_permissions('items.manage')(db=db, authorization=f'Bearer {owner.token}')
            created_item = ItemCreate(name='Widget', item_type='service', rate='10', cost='0')
            self.assertEqual(create_bank_account(BankAccountCreate(name='Main Bank', balance='0'), db=db, auth=require_permissions('banking.manage')(db=db, authorization=f'Bearer {owner.token}')).name, 'Main Bank')
            self.assertEqual(csv_routes.csv_import_customers.__name__, 'csv_import_customers')
            self.assertEqual(me(db=db, auth=None).authenticated, False)
            self.assertEqual(require_permissions('items.manage')(db=db, authorization=f'Bearer {owner.token}').user.email, 'owner@example.com')
            self.assertEqual(require_permissions('import_export.manage')(db=db, authorization=f'Bearer {owner.token}').user.email, 'owner@example.com')
            from app.routes.items import create_item
            create_item(created_item, db=db, auth=owner_items)
            with self.assertRaises(HTTPException) as estimate_ctx:
                create_estimate(EstimateCreate(customer_id=999, date=date(2026, 4, 15), lines=[EstimateLineCreate(description='Work', quantity='1', rate='10')]), db=db, auth=require_permissions('sales.manage')(db=db, authorization=f'Bearer {owner.token}'))
            self.assertEqual(estimate_ctx.exception.status_code, 404)
            with self.assertRaises(HTTPException) as po_ctx:
                create_po(POCreate(vendor_id=999, date=date(2026, 4, 15), lines=[POLineCreate(description='Paper', quantity=1, rate=5)]), db=db, auth=require_permissions('purchasing.manage')(db=db, authorization=f'Bearer {owner.token}'))
            self.assertEqual(po_ctx.exception.status_code, 404)

            upload = UploadFile(filename='customers.csv', file=BytesIO('Name\nAlice\n'.encode('utf-8')))
            result = asyncio.run(csv_routes.csv_import_customers(file=upload, db=db, auth=require_permissions('import_export.manage')(db=db, authorization=f'Bearer {owner.token}')))
            self.assertEqual(result['created'], 1)

    def test_dashboard_search_and_gst_routes_require_auth_but_no_extra_business_permission(self):
        from app.routes.dashboard import get_dashboard
        from app.routes.gst import list_gst_codes
        from app.routes.search import unified_search
        from app.services.auth import get_auth_context, require_permissions

        with self.Session() as db:
            owner = self._bootstrap_owner(db)
            viewer = self._create_view_user(db, owner.token)

            with self.assertRaises(HTTPException) as unauth_ctx:
                get_auth_context(db=db, authorization=None, required=True)
            self.assertEqual(unauth_ctx.exception.status_code, 401)

            viewer_auth = get_auth_context(db=db, authorization=f'Bearer {viewer.token}', required=True)
            self.assertIsInstance(get_dashboard(db=db, auth=viewer_auth), dict)
            self.assertIsInstance(unified_search(q='Al', db=db, auth=viewer_auth), dict)
            self.assertIsInstance(list_gst_codes(db=db, auth=viewer_auth), list)

            with self.assertRaises(HTTPException) as viewer_manage_ctx:
                require_permissions('sales.manage')(db=db, authorization=f'Bearer {viewer.token}')
            self.assertEqual(viewer_manage_ctx.exception.status_code, 403)
            self.assertIsInstance(get_dashboard(db=db, auth=get_auth_context(db=db, authorization=f'Bearer {owner.token}', required=True)), dict)


if __name__ == '__main__':
    unittest.main()
