from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.banking import BankAccount, BankTransaction
from app.models.gst_settlement import GstSettlement, GstSettlementStatus
from app.services.accounting import create_journal_entry, get_gst_account_id
from app.services.closing_date import check_closing_date


@dataclass(frozen=True)
class GstSettlementCandidate:
    id: int
    bank_account_id: int
    date: date
    amount: Decimal
    payee: str
    description: str


def _expected_bank_amount(report: dict) -> Decimal:
    net_gst = Decimal(str(report.get('net_gst', 0)))
    position = report.get('net_position')
    if position == 'payable':
        return -net_gst
    if position == 'refundable':
        return net_gst
    return Decimal('0.00')


def get_active_settlement(db: Session, start_date: date, end_date: date) -> GstSettlement | None:
    return db.query(GstSettlement).filter(
        GstSettlement.start_date == start_date,
        GstSettlement.end_date == end_date,
        GstSettlement.status == GstSettlementStatus.CONFIRMED,
    ).first()


def settlement_candidates(db: Session, report: dict) -> list[dict]:
    expected_amount = _expected_bank_amount(report)
    if expected_amount == Decimal('0.00'):
        return []
    txns = (
        db.query(BankTransaction)
        .join(BankAccount, BankTransaction.bank_account_id == BankAccount.id)
        .filter(BankTransaction.reconciled == True)
        .filter(BankTransaction.transaction_id == None)
        .filter(BankAccount.account_id != None)
        .filter(BankTransaction.amount == expected_amount)
        .order_by(BankTransaction.date.desc(), BankTransaction.id.desc())
        .all()
    )
    return [
        {
            'id': txn.id,
            'bank_account_id': txn.bank_account_id,
            'date': txn.date.isoformat(),
            'amount': float(txn.amount),
            'payee': txn.payee or '',
            'description': txn.description or '',
        }
        for txn in txns
    ]


def build_settlement_state(db: Session, start_date: date, end_date: date, report: dict) -> dict:
    settlement = get_active_settlement(db, start_date, end_date)
    expected_amount = _expected_bank_amount(report)
    state = {
        'status': 'unsettled',
        'direction': 'payment' if report.get('net_position') == 'payable' else 'refund' if report.get('net_position') == 'refundable' else 'none',
        'expected_bank_amount': float(expected_amount),
        'candidates': [],
        'settlement': None,
    }
    if expected_amount == Decimal('0.00'):
        state['status'] = 'no_settlement_required'
        return state
    if settlement:
        state['status'] = 'confirmed'
        state['settlement'] = {
            'id': settlement.id,
            'settlement_date': settlement.settlement_date.isoformat(),
            'net_position': settlement.net_position,
            'net_gst': float(settlement.net_gst),
            'bank_transaction_id': settlement.bank_transaction_id,
            'transaction_id': settlement.transaction_id,
        }
        return state
    state['candidates'] = settlement_candidates(db, report)
    return state


def confirm_settlement(
    db: Session,
    *,
    start_date: date,
    end_date: date,
    bank_transaction_id: int,
    report: dict,
) -> dict:
    if get_active_settlement(db, start_date, end_date):
        raise HTTPException(status_code=400, detail='GST period is already settled')

    bank_txn = db.query(BankTransaction).filter(BankTransaction.id == bank_transaction_id).first()
    if not bank_txn:
        raise HTTPException(status_code=404, detail='Bank transaction not found')
    if not bank_txn.reconciled:
        raise HTTPException(status_code=400, detail='Bank transaction must be reconciled before confirming GST settlement')
    if bank_txn.transaction_id:
        raise HTTPException(status_code=400, detail='Bank transaction is already linked to another accounting transaction')

    expected_amount = _expected_bank_amount(report)
    if Decimal(str(bank_txn.amount)) != expected_amount:
        raise HTTPException(status_code=400, detail='Bank transaction amount does not match the GST settlement amount')

    bank_account = db.query(BankAccount).filter(BankAccount.id == bank_txn.bank_account_id).first()
    if not bank_account or not bank_account.account_id:
        raise HTTPException(status_code=400, detail='Bank transaction must belong to a bank account linked to the chart of accounts')

    check_closing_date(db, bank_txn.date)

    gst_account_id = get_gst_account_id(db)
    if not gst_account_id:
        raise HTTPException(status_code=400, detail='GST control account is not available')

    net_gst = Decimal(str(report.get('net_gst', 0)))
    if report.get('net_position') == 'payable':
        journal_lines = [
            {'account_id': gst_account_id, 'debit': net_gst, 'credit': Decimal('0.00'), 'description': f'GST settlement {start_date} to {end_date}'},
            {'account_id': bank_account.account_id, 'debit': Decimal('0.00'), 'credit': net_gst, 'description': bank_txn.payee or bank_txn.description or 'GST payment'},
        ]
    elif report.get('net_position') == 'refundable':
        journal_lines = [
            {'account_id': bank_account.account_id, 'debit': net_gst, 'credit': Decimal('0.00'), 'description': bank_txn.payee or bank_txn.description or 'GST refund'},
            {'account_id': gst_account_id, 'debit': Decimal('0.00'), 'credit': net_gst, 'description': f'GST settlement {start_date} to {end_date}'},
        ]
    else:
        raise HTTPException(status_code=400, detail='GST period does not require settlement')

    txn = create_journal_entry(
        db,
        bank_txn.date,
        f'GST settlement {start_date} to {end_date}',
        journal_lines,
        source_type='gst_settlement',
        source_id=bank_transaction_id,
        reference=f'GST-{start_date}-{end_date}',
    )
    bank_txn.transaction_id = txn.id
    gst_settlement = GstSettlement(
        start_date=start_date,
        end_date=end_date,
        settlement_date=bank_txn.date,
        net_position=report.get('net_position'),
        net_gst=net_gst,
        box9_adjustments=Decimal(str(report.get('boxes', {}).get('9', 0))),
        box13_adjustments=Decimal(str(report.get('boxes', {}).get('13', 0))),
        status=GstSettlementStatus.CONFIRMED,
        bank_transaction_id=bank_txn.id,
        transaction_id=txn.id,
    )
    db.add(gst_settlement)
    db.commit()
    db.refresh(gst_settlement)
    return {
        'status': 'confirmed',
        'net_position': gst_settlement.net_position,
        'net_gst': float(gst_settlement.net_gst),
        'settlement_id': gst_settlement.id,
        'bank_transaction_id': gst_settlement.bank_transaction_id,
    }
