import os
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.database import Base
from app.models.accounts import Account, AccountType


class GstPostingAccountTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def test_get_gst_account_renames_legacy_sales_tax_payable(self):
        from app.services.accounting import get_gst_account_id

        with self.Session() as db:
            db.add(Account(
                name="Sales Tax Payable",
                account_number="2200",
                account_type=AccountType.LIABILITY,
                is_system=True,
            ))
            db.commit()

            account_id = get_gst_account_id(db)
            account = db.query(Account).filter(Account.id == account_id).one()

        self.assertEqual(account.account_number, "2200")
        self.assertEqual(account.name, "GST")
        self.assertEqual(account.account_type, AccountType.LIABILITY)

    def test_get_gst_account_creates_missing_system_account(self):
        from app.services.accounting import get_gst_account_id

        with self.Session() as db:
            account_id = get_gst_account_id(db)
            account = db.query(Account).filter(Account.id == account_id).one()

        self.assertEqual(account.account_number, "2200")
        self.assertEqual(account.name, "GST")
        self.assertEqual(account.account_type, AccountType.LIABILITY)
        self.assertTrue(account.is_system)

    def test_seed_chart_names_account_2200_gst(self):
        from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS

        gst_account = next(row for row in CHART_OF_ACCOUNTS if row["account_number"] == "2200")
        self.assertEqual(gst_account["name"], "GST")
        self.assertEqual(gst_account["account_type"], "liability")


if __name__ == "__main__":
    unittest.main()
