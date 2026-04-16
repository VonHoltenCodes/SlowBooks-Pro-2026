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

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.database import Base
from app.models.accounts import Account, AccountType
from app.models.contacts import Customer, Vendor
from app.models.invoices import Invoice, InvoiceStatus
from app.models.settings import Settings


class NZForumCarryoverTests(unittest.TestCase):
    def setUp(self):
        from app.models.payments import Payment, PaymentAllocation  # noqa: F401
        from app.models.transactions import Transaction, TransactionLine  # noqa: F401
        from app.models.bills import Bill, BillLine, BillPayment, BillPaymentAllocation  # noqa: F401
        from app.models.estimates import Estimate, EstimateLine  # noqa: F401
        from app.models.banking import BankAccount, BankTransaction, Reconciliation  # noqa: F401
        from app.models.items import Item  # noqa: F401

        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)

    def _set_role(self, db, key, account_id):
        db.add(Settings(key=key, value=str(account_id)))
        db.commit()

    def test_payment_void_restores_invoice_balance_and_posts_reversal(self):
        from app.models.payments import Payment
        from app.models.transactions import Transaction
        from app.routes.payments import create_payment, void_payment
        from app.schemas.payments import PaymentAllocationCreate, PaymentCreate

        with self.Session() as db:
            customer = Customer(name='Aroha Ltd')
            ar = Account(name='Trade Debtors', account_number='610', account_type=AccountType.ASSET)
            clearing = Account(name='Receipt Clearing', account_number='615', account_type=AccountType.ASSET)
            db.add_all([customer, ar, clearing])
            db.commit()
            self._set_role(db, 'system_account_accounts_receivable_id', ar.id)
            self._set_role(db, 'system_account_undeposited_funds_id', clearing.id)

            invoice = Invoice(
                invoice_number='1001',
                customer_id=customer.id,
                status=InvoiceStatus.SENT,
                date=date(2026, 4, 1),
                subtotal=Decimal('115.00'),
                tax_rate=Decimal('0.1500'),
                tax_amount=Decimal('15.00'),
                total=Decimal('115.00'),
                amount_paid=Decimal('0.00'),
                balance_due=Decimal('115.00'),
            )
            db.add(invoice)
            db.commit()

            payment = create_payment(PaymentCreate(
                customer_id=customer.id,
                date=date(2026, 4, 15),
                amount=Decimal('115.00'),
                allocations=[PaymentAllocationCreate(invoice_id=invoice.id, amount=Decimal('115.00'))],
            ), db=db)
            refreshed_invoice = db.query(Invoice).filter_by(id=invoice.id).one()
            self.assertEqual(refreshed_invoice.status, InvoiceStatus.PAID)
            self.assertEqual(Decimal(str(refreshed_invoice.balance_due)), Decimal('0.00'))

            voided = void_payment(payment.id, db=db)
            payment_row = db.query(Payment).filter_by(id=payment.id).one()
            invoice_row = db.query(Invoice).filter_by(id=invoice.id).one()
            txns = db.query(Transaction).order_by(Transaction.id).all()

        self.assertTrue(voided.is_voided)
        self.assertTrue(payment_row.is_voided)
        self.assertEqual(invoice_row.status, InvoiceStatus.SENT)
        self.assertEqual(Decimal(str(invoice_row.amount_paid)), Decimal('0.00'))
        self.assertEqual(Decimal(str(invoice_row.balance_due)), Decimal('115.00'))
        self.assertEqual([txn.source_type for txn in txns], ['payment', 'payment_void'])

    def test_cannot_void_payment_after_deposit(self):
        from app.routes.deposits import create_deposit
        from app.routes.payments import create_payment, void_payment
        from app.schemas.deposits import DepositCreate
        from app.schemas.payments import PaymentAllocationCreate, PaymentCreate

        with self.Session() as db:
            customer = Customer(name='Aroha Ltd')
            ar = Account(name='Trade Debtors', account_number='610', account_type=AccountType.ASSET)
            clearing = Account(name='Receipt Clearing', account_number='615', account_type=AccountType.ASSET)
            bank = Account(name='Operating Account', account_number='090', account_type=AccountType.ASSET)
            db.add_all([customer, ar, clearing, bank])
            db.commit()
            self._set_role(db, 'system_account_accounts_receivable_id', ar.id)
            self._set_role(db, 'system_account_undeposited_funds_id', clearing.id)

            invoice = Invoice(
                invoice_number='1002',
                customer_id=customer.id,
                status=InvoiceStatus.SENT,
                date=date(2026, 4, 1),
                subtotal=Decimal('100.00'),
                tax_rate=Decimal('0'),
                tax_amount=Decimal('0'),
                total=Decimal('100.00'),
                amount_paid=Decimal('0.00'),
                balance_due=Decimal('100.00'),
            )
            db.add(invoice)
            db.commit()

            payment = create_payment(PaymentCreate(
                customer_id=customer.id,
                date=date(2026, 4, 10),
                amount=Decimal('100.00'),
                allocations=[PaymentAllocationCreate(invoice_id=invoice.id, amount=Decimal('100.00'))],
            ), db=db)
            create_deposit(DepositCreate(
                date=date(2026, 4, 11),
                deposit_to_account_id=bank.id,
                payment_ids=[payment.id],
                reference='DEP-1',
            ), db=db)

            with self.assertRaises(HTTPException) as ctx:
                void_payment(payment.id, db=db)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn('deposited', ctx.exception.detail)

    def test_manual_journal_void_and_check_register(self):
        from app.routes.banking import check_register
        from app.routes.journal import create_manual_journal, void_manual_journal
        from app.schemas.journal import JournalEntryCreate, JournalLineCreate

        with self.Session() as db:
            bank = Account(name='Operating Account', account_number='090', account_type=AccountType.ASSET)
            equity = Account(name='Owner Funds', account_number='950', account_type=AccountType.EQUITY)
            expense = Account(name='Office Expenses', account_number='600', account_type=AccountType.EXPENSE)
            db.add_all([bank, equity, expense])
            db.commit()

            opening = create_manual_journal(JournalEntryCreate(
                date=date(2026, 4, 1),
                description='Opening funds',
                reference='J-1',
                lines=[
                    JournalLineCreate(account_id=bank.id, debit=Decimal('500.00')),
                    JournalLineCreate(account_id=equity.id, credit=Decimal('500.00')),
                ],
            ), db=db)
            spend = create_manual_journal(JournalEntryCreate(
                date=date(2026, 4, 2),
                description='Office spend',
                reference='J-2',
                lines=[
                    JournalLineCreate(account_id=expense.id, debit=Decimal('125.00')),
                    JournalLineCreate(account_id=bank.id, credit=Decimal('125.00')),
                ],
            ), db=db)
            void_manual_journal(spend.id, db=db)
            register = check_register(account_id=bank.id, db=db)

        self.assertEqual(opening.reference, 'J-1')
        self.assertEqual(register['account_name'], 'Operating Account')
        self.assertEqual(len(register['entries']), 3)
        self.assertEqual(register['entries'][-1]['balance'], 500.0)

    def test_credit_card_charge_posts_expense_and_liability(self):
        from app.models.transactions import Transaction
        from app.routes.cc_charges import create_cc_charge
        from app.schemas.cc_charges import CCChargeCreate

        with self.Session() as db:
            expense = Account(name='Travel', account_number='730', account_type=AccountType.EXPENSE)
            card = Account(name='Corporate Card', account_number='820', account_type=AccountType.LIABILITY)
            db.add_all([expense, card])
            db.commit()

            charge = create_cc_charge(CCChargeCreate(
                date=date(2026, 4, 20),
                payee='Air NZ',
                account_id=expense.id,
                credit_card_account_id=card.id,
                amount=Decimal('80.00'),
                reference='CC-1',
                memo='Client trip',
            ), db=db)
            txn = db.query(Transaction).filter_by(id=charge.id).one()
            account_ids = {line.account_id for line in txn.lines}

        self.assertEqual(charge.account_name, 'Travel')
        self.assertEqual(charge.credit_card_account_name, 'Corporate Card')
        self.assertEqual(account_ids, {expense.id, card.id})

    def test_vendor_default_expense_account_used_for_bill_lines(self):
        from app.models.bills import Bill
        from app.routes.bills import create_bill
        from app.schemas.bills import BillCreate, BillLineCreate

        with self.Session() as db:
            vendor_default = Account(name='Subscriptions', account_number='610', account_type=AccountType.EXPENSE)
            generic_default = Account(name='General Expenses', account_number='600', account_type=AccountType.EXPENSE)
            ap = Account(name='Trade Creditors', account_number='810', account_type=AccountType.LIABILITY)
            db.add_all([vendor_default, generic_default, ap])
            db.commit()
            vendor_default_id = vendor_default.id
            vendor = Vendor(name='Spark NZ', default_expense_account_id=vendor_default_id)
            db.add(vendor)
            db.commit()
            self._set_role(db, 'system_account_accounts_payable_id', ap.id)
            self._set_role(db, 'system_account_default_expense_id', generic_default.id)

            bill = create_bill(BillCreate(
                vendor_id=vendor.id,
                bill_number='B-100',
                date=date(2026, 4, 30),
                terms='Net 30',
                lines=[BillLineCreate(description='Monthly plan', quantity=1, rate=50, gst_code='NO_GST', gst_rate=Decimal('0'))],
            ), db=db)
            bill_row = db.query(Bill).filter_by(id=bill.id).one()
            line_account_id = bill_row.lines[0].account_id

        self.assertEqual(line_account_id, vendor_default_id)


if __name__ == '__main__':
    unittest.main()
