import os
import sys
import types
import unittest
from io import BytesIO
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

weasyprint_stub = types.ModuleType('weasyprint')
weasyprint_stub.HTML = object
sys.modules.setdefault('weasyprint', weasyprint_stub)

from app.database import Base


class XeroImportTests(unittest.TestCase):
    def setUp(self):
        import app.models  # noqa: F401

        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def _bundle(self):
        return {
            'chart_of_accounts': ('xero_chart_of_accounts.csv', "Code,Name,Type,Status\n200,Sales,Revenue,ACTIVE\n300,Purchases,Direct Costs,ACTIVE\n610,Accounts Receivable,Current Asset,ACTIVE\n800,Accounts Payable,Current Liability,ACTIVE\n820,GST,Current Liability,ACTIVE\n090,Business Bank Account,Bank,ACTIVE\n"),
            'general_ledger': ('xero_general_ledger.csv', "Date,Source,Reference,Description,Account Code,Account Name,Debit,Credit\n2026-04-01,INV,1001,Invoice 1001,610,Accounts Receivable,115.00,0.00\n2026-04-01,INV,1001,Invoice 1001,200,Sales,0.00,100.00\n2026-04-01,INV,1001,Invoice 1001,820,GST,0.00,15.00\n"),
            'trial_balance': ('xero_trial_balance.csv', "Code,Name,Balance\n200,Sales,100.00\n610,Accounts Receivable,115.00\n820,GST,15.00\n"),
            'profit_and_loss': ('xero_profit_and_loss.csv', "Name,Amount\nNet Profit,100.00\n"),
            'balance_sheet': ('xero_balance_sheet.csv', "Name,Amount\nTotal Assets,115.00\nTotal Liabilities,15.00\nTotal Equity,100.00\n"),
        }

    def test_dry_run_passes_for_balanced_matching_bundle(self):
        from app.services.xero_import import dry_run_import

        summary = dry_run_import(self._bundle())

        self.assertTrue(summary['import_ready'])
        self.assertEqual(summary['counts']['accounts'], 6)
        self.assertEqual(summary['counts']['journal_lines'], 3)
        self.assertEqual(summary['journal_groups'], 1)
        self.assertTrue(summary['verification']['trial_balance_ok'])
        self.assertTrue(summary['verification']['profit_loss_ok'])
        self.assertTrue(summary['verification']['balance_sheet_ok'])

    def test_import_creates_accounts_and_journal_history(self):
        from app.models.accounts import Account
        from app.models.transactions import Transaction
        from app.routes.reports import balance_sheet, profit_loss
        from app.services.xero_import import execute_import

        with self.Session() as db:
            result = execute_import(db, self._bundle())
            accounts = db.query(Account).filter(Account.account_number.in_(['200', '610', '820'])).all()
            transactions = db.query(Transaction).filter(Transaction.source_type == 'xero_import').all()
            pnl = profit_loss(start_date=date(2026, 4, 1), end_date=date(2026, 4, 30), db=db)
            bs = balance_sheet(as_of_date=date(2026, 4, 30), db=db)

        self.assertEqual(result['imported_accounts'], 6)
        self.assertEqual(result['imported_transactions'], 1)
        self.assertEqual(result['imported_transaction_lines'], 3)
        self.assertEqual(len(accounts), 3)
        self.assertEqual(len(transactions), 1)
        self.assertEqual(pnl['net_income'], 100.0)
        self.assertEqual(bs['total_assets'], 115.0)
        self.assertEqual(bs['total_liabilities'], 15.0)

    def test_import_blocks_when_verification_fails(self):
        from fastapi import HTTPException
        from app.services.xero_import import execute_import

        bad = self._bundle()
        bad['trial_balance'] = ('xero_trial_balance.csv', "Code,Name,Balance\n200,Sales,90.00\n610,Accounts Receivable,115.00\n820,GST,15.00\n")
        with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                execute_import(db, bad)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn('dry-run', ctx.exception.detail.lower())
