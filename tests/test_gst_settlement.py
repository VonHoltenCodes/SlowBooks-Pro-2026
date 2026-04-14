import os
import sys
import types
import unittest
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

weasyprint_stub = types.ModuleType('weasyprint')
weasyprint_stub.HTML = object
sys.modules.setdefault('weasyprint', weasyprint_stub)

from app.database import Base
from app.models.accounts import Account, AccountType
from app.models.contacts import Customer, Vendor
from app.models.settings import Settings


class GstSettlementTests(unittest.TestCase):
    def setUp(self):
        from app.models.gst import GstCode  # noqa: F401
        from app.models.banking import BankAccount, BankTransaction, Reconciliation  # noqa: F401
        from app.models.gst_settlement import GstSettlement  # noqa: F401

        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def _seed(self, db):
        customer = Customer(name='Aroha Ltd')
        vendor = Vendor(name='Harbour Supplies')
        db.add_all([
            customer,
            vendor,
            Account(name='Business Bank Account', account_number='090', account_type=AccountType.ASSET),
            Account(name='Accounts Receivable', account_number='1100', account_type=AccountType.ASSET),
            Account(name='Accounts Payable', account_number='2000', account_type=AccountType.LIABILITY),
            Account(name='GST', account_number='2200', account_type=AccountType.LIABILITY),
            Account(name='Sales', account_number='4000', account_type=AccountType.INCOME),
            Account(name='Expenses', account_number='6000', account_type=AccountType.EXPENSE),
            Settings(key='gst_basis', value='invoice'),
            Settings(key='gst_period', value='two-monthly'),
            Settings(key='gst_number', value='123-456-789'),
            Settings(key='company_name', value='SlowBooks NZ'),
        ])
        db.commit()
        bank_account = __import__('app.models.banking', fromlist=['BankAccount']).BankAccount(
            name='Operating Account',
            account_id=db.query(Account).filter(Account.account_number == '090').one().id,
            bank_name='ASB',
            last_four='1234',
            balance=Decimal('0.00'),
        )
        db.add(bank_account)
        db.commit()
        return customer, vendor, bank_account

    def test_payable_period_can_be_settled_from_reconciled_bank_transaction(self):
        from app.models.banking import BankTransaction
        from app.routes.invoices import create_invoice
        from app.routes.reports import GstSettlementConfirmRequest, confirm_gst_settlement, gst_return_report
        from app.schemas.invoices import InvoiceCreate, InvoiceLineCreate

        with self.Session() as db:
            customer, _vendor, bank_account = self._seed(db)
            create_invoice(InvoiceCreate(
                customer_id=customer.id,
                date=date(2026, 4, 1),
                lines=[InvoiceLineCreate(description='Standard sale', quantity=1, rate=Decimal('100'), gst_code='GST15')],
            ), db=db)
            bank_txn = BankTransaction(
                bank_account_id=bank_account.id,
                date=date(2026, 5, 7),
                amount=Decimal('-15.00'),
                payee='Inland Revenue',
                description='GST payment',
                reconciled=True,
            )
            db.add(bank_txn)
            db.commit()

            report = gst_return_report(start_date=date(2026, 4, 1), end_date=date(2026, 4, 30), db=db)
            self.assertEqual(report['settlement']['status'], 'unsettled')
            self.assertEqual(len(report['settlement']['candidates']), 1)

            result = confirm_gst_settlement(GstSettlementConfirmRequest(
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 30),
                bank_transaction_id=bank_txn.id,
            ), db=db)
            refreshed_report = gst_return_report(start_date=date(2026, 4, 1), end_date=date(2026, 4, 30), db=db)
            gst_account = db.query(Account).filter(Account.account_number == '2200').one()

        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(result['net_position'], 'payable')
        self.assertEqual(result['net_gst'], 15.0)
        self.assertEqual(refreshed_report['settlement']['status'], 'confirmed')
        self.assertEqual(gst_account.balance, Decimal('0.00'))

    def test_refundable_period_can_be_settled_from_reconciled_bank_transaction(self):
        from app.models.banking import BankTransaction
        from app.routes.bills import create_bill
        from app.routes.reports import GstSettlementConfirmRequest, confirm_gst_settlement
        from app.schemas.bills import BillCreate, BillLineCreate

        with self.Session() as db:
            _customer, vendor, bank_account = self._seed(db)
            create_bill(BillCreate(
                vendor_id=vendor.id,
                bill_number='B-1',
                date=date(2026, 4, 3),
                lines=[BillLineCreate(description='Standard purchase', quantity=1, rate=Decimal('200'), gst_code='GST15')],
            ), db=db)
            bank_txn = BankTransaction(
                bank_account_id=bank_account.id,
                date=date(2026, 5, 20),
                amount=Decimal('30.00'),
                payee='Inland Revenue',
                description='GST refund',
                reconciled=True,
            )
            db.add(bank_txn)
            db.commit()

            result = confirm_gst_settlement(GstSettlementConfirmRequest(
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 30),
                bank_transaction_id=bank_txn.id,
            ), db=db)
            gst_account = db.query(Account).filter(Account.account_number == '2200').one()

        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(result['net_position'], 'refundable')
        self.assertEqual(result['net_gst'], 30.0)
        self.assertEqual(gst_account.balance, Decimal('0.00'))

    def test_unreconciled_or_mismatched_transaction_is_rejected(self):
        from app.models.banking import BankTransaction
        from app.routes.invoices import create_invoice
        from app.routes.reports import GstSettlementConfirmRequest, confirm_gst_settlement
        from app.schemas.invoices import InvoiceCreate, InvoiceLineCreate

        with self.Session() as db:
            customer, _vendor, bank_account = self._seed(db)
            create_invoice(InvoiceCreate(
                customer_id=customer.id,
                date=date(2026, 4, 1),
                lines=[InvoiceLineCreate(description='Standard sale', quantity=1, rate=Decimal('100'), gst_code='GST15')],
            ), db=db)
            wrong_amount = BankTransaction(
                bank_account_id=bank_account.id,
                date=date(2026, 5, 7),
                amount=Decimal('-10.00'),
                payee='Inland Revenue',
                description='Wrong GST payment',
                reconciled=True,
            )
            not_reconciled = BankTransaction(
                bank_account_id=bank_account.id,
                date=date(2026, 5, 8),
                amount=Decimal('-15.00'),
                payee='Inland Revenue',
                description='Unreconciled GST payment',
                reconciled=False,
            )
            db.add_all([wrong_amount, not_reconciled])
            db.commit()

            with self.assertRaises(HTTPException) as wrong_ctx:
                confirm_gst_settlement(GstSettlementConfirmRequest(start_date=date(2026, 4, 1), end_date=date(2026, 4, 30), bank_transaction_id=wrong_amount.id), db=db)
            with self.assertRaises(HTTPException) as unreconciled_ctx:
                confirm_gst_settlement(GstSettlementConfirmRequest(start_date=date(2026, 4, 1), end_date=date(2026, 4, 30), bank_transaction_id=not_reconciled.id), db=db)

        self.assertEqual(wrong_ctx.exception.status_code, 400)
        self.assertIn('amount', wrong_ctx.exception.detail.lower())
        self.assertEqual(unreconciled_ctx.exception.status_code, 400)
        self.assertIn('reconciled', unreconciled_ctx.exception.detail.lower())

    def test_period_and_bank_transaction_cannot_be_reused(self):
        from app.models.banking import BankTransaction
        from app.routes.invoices import create_invoice
        from app.routes.reports import GstSettlementConfirmRequest, confirm_gst_settlement
        from app.schemas.invoices import InvoiceCreate, InvoiceLineCreate

        with self.Session() as db:
            customer, _vendor, bank_account = self._seed(db)
            create_invoice(InvoiceCreate(
                customer_id=customer.id,
                date=date(2026, 4, 1),
                lines=[InvoiceLineCreate(description='Standard sale', quantity=1, rate=Decimal('100'), gst_code='GST15')],
            ), db=db)
            bank_txn = BankTransaction(
                bank_account_id=bank_account.id,
                date=date(2026, 5, 7),
                amount=Decimal('-15.00'),
                payee='Inland Revenue',
                description='GST payment',
                reconciled=True,
            )
            db.add(bank_txn)
            db.commit()
            confirm_gst_settlement(GstSettlementConfirmRequest(start_date=date(2026, 4, 1), end_date=date(2026, 4, 30), bank_transaction_id=bank_txn.id), db=db)

            with self.assertRaises(HTTPException) as second_ctx:
                confirm_gst_settlement(GstSettlementConfirmRequest(start_date=date(2026, 4, 1), end_date=date(2026, 4, 30), bank_transaction_id=bank_txn.id), db=db)

        self.assertEqual(second_ctx.exception.status_code, 400)
        self.assertIn('already settled', second_ctx.exception.detail.lower())

    def test_closing_date_blocks_gst_settlement_posting(self):
        from app.models.banking import BankTransaction
        from app.routes.invoices import create_invoice
        from app.routes.reports import GstSettlementConfirmRequest, confirm_gst_settlement
        from app.schemas.invoices import InvoiceCreate, InvoiceLineCreate

        with self.Session() as db:
            customer, _vendor, bank_account = self._seed(db)
            create_invoice(InvoiceCreate(
                customer_id=customer.id,
                date=date(2026, 4, 1),
                lines=[InvoiceLineCreate(description='Standard sale', quantity=1, rate=Decimal('100'), gst_code='GST15')],
            ), db=db)
            db.add(Settings(key='closing_date', value='2026-05-07'))
            bank_txn = BankTransaction(
                bank_account_id=bank_account.id,
                date=date(2026, 5, 7),
                amount=Decimal('-15.00'),
                payee='Inland Revenue',
                description='GST payment',
                reconciled=True,
            )
            db.add(bank_txn)
            db.commit()

            with self.assertRaises(HTTPException) as ctx:
                confirm_gst_settlement(GstSettlementConfirmRequest(start_date=date(2026, 4, 1), end_date=date(2026, 4, 30), bank_transaction_id=bank_txn.id), db=db)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn('closing date', ctx.exception.detail.lower())


if __name__ == '__main__':
    unittest.main()
