import os
import sys
import types
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

weasyprint_stub = types.ModuleType('weasyprint')
weasyprint_stub.HTML = object
sys.modules.setdefault('weasyprint', weasyprint_stub)

from app.database import Base


class ChartTemplateLoaderTests(unittest.TestCase):
    def setUp(self):
        import app.models  # noqa: F401

        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def test_loads_xero_template_on_clean_ledger(self):
        from app.models.accounts import Account
        from app.models.settings import Settings
        from app.services.chart_template_loader import load_chart_template

        with self.Session() as db:
            result = load_chart_template(db, 'xero')
            account_numbers = {row.account_number for row in db.query(Account).all()}
            system_settings = {row.key: row.value for row in db.query(Settings).all()}

        self.assertEqual(result['status'], 'loaded')
        self.assertEqual(result['template_key'], 'xero')
        self.assertIn('090', account_numbers)
        self.assertIn('820', account_numbers)
        self.assertIn('system_account_gst_control_id', system_settings)

    def test_loads_mas_template_on_clean_ledger(self):
        from app.models.accounts import Account
        from app.services.chart_template_loader import load_chart_template

        with self.Session() as db:
            result = load_chart_template(db, 'mas')
            account_numbers = {row.account_number for row in db.query(Account).all()}

        self.assertEqual(result['template_key'], 'mas')
        self.assertIn('1000', account_numbers)
        self.assertIn('2200', account_numbers)
        self.assertIn('2300', account_numbers)

    def test_rejects_unknown_template(self):
        from app.services.chart_template_loader import load_chart_template

        with self.Session() as db:
            with self.assertRaises(ValueError) as ctx:
                load_chart_template(db, 'unknown')

        self.assertIn('unknown chart template', str(ctx.exception).lower())

    def test_rejects_non_clean_ledger(self):
        from app.models.contacts import Customer
        from app.services.chart_template_loader import load_chart_template

        with self.Session() as db:
            db.add(Customer(name='Existing Customer'))
            db.commit()
            with self.assertRaises(ValueError) as ctx:
                load_chart_template(db, 'xero')

        self.assertIn('clean ledger', str(ctx.exception).lower())


if __name__ == '__main__':
    unittest.main()
