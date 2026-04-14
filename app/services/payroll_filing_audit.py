import hashlib
import json
from datetime import datetime, UTC

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.payroll_filing import PayrollFilingAudit, PayrollFilingStatus
from app.schemas.payroll_filing import PayrollFilingAuditResponse


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _json_default(value):
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value)


def _snapshot_payload(snapshot: dict) -> tuple[str, str]:
    serialized = json.dumps(snapshot, sort_keys=True, default=_json_default)
    return serialized, hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def employee_filing_snapshot(employee, filing_type: str, settings: dict) -> dict:
    filing_date = employee.start_date if filing_type == 'starter' else employee.end_date
    return {
        'filing_type': filing_type,
        'company_ird_number': str(settings.get('ird_number') or '').strip(),
        'employee_id': employee.id,
        'first_name': employee.first_name,
        'last_name': employee.last_name,
        'ird_number': employee.ird_number,
        'tax_code': employee.tax_code,
        'pay_frequency': employee.pay_frequency,
        'kiwisaver_enrolled': bool(employee.kiwisaver_enrolled),
        'filing_date': filing_date,
    }


def pay_run_filing_snapshot(pay_run, settings: dict) -> dict:
    return {
        'filing_type': 'employment_information',
        'company_ird_number': str(settings.get('ird_number') or '').strip(),
        'contact_name': settings.get('payroll_contact_name') or settings.get('company_name'),
        'contact_phone': settings.get('payroll_contact_phone') or settings.get('company_phone'),
        'contact_email': settings.get('payroll_contact_email') or settings.get('company_email'),
        'pay_run_id': pay_run.id,
        'period_start': pay_run.period_start,
        'period_end': pay_run.period_end,
        'pay_date': pay_run.pay_date,
        'tax_year': pay_run.tax_year,
        'status': getattr(pay_run.status, 'value', pay_run.status),
        'stubs': [
            {
                'employee_id': stub.employee_id,
                'employee_ird_number': stub.employee.ird_number if stub.employee else None,
                'employee_name': f"{stub.employee.first_name} {stub.employee.last_name}".strip() if stub.employee else None,
                'tax_code': stub.tax_code,
                'pay_frequency': stub.employee.pay_frequency if stub.employee else None,
                'hours': str(stub.hours or 0),
                'gross_pay': str(stub.gross_pay or 0),
                'paye': str(stub.paye or 0),
                'acc_earners_levy': str(stub.acc_earners_levy or 0),
                'student_loan_deduction': str(stub.student_loan_deduction or 0),
                'kiwisaver_employee_deduction': str(stub.kiwisaver_employee_deduction or 0),
                'employer_kiwisaver_contribution': str(stub.employer_kiwisaver_contribution or 0),
                'esct': str(stub.esct or 0),
                'child_support_deduction': str(stub.child_support_deduction or 0),
                'net_pay': str(stub.net_pay or 0),
            }
            for stub in sorted(pay_run.stubs, key=lambda item: item.employee_id)
        ],
    }


def create_filing_audit(
    db: Session,
    *,
    filing_type: str,
    export_filename: str,
    source_snapshot: dict,
    employee_id: int | None = None,
    pay_run_id: int | None = None,
    generated_by_user_id: int | None = None,
) -> PayrollFilingAudit:
    serialized, source_hash = _snapshot_payload(source_snapshot)
    prior_generated = (
        db.query(PayrollFilingAudit)
        .filter(
            PayrollFilingAudit.filing_type == filing_type,
            PayrollFilingAudit.employee_id == employee_id,
            PayrollFilingAudit.pay_run_id == pay_run_id,
            PayrollFilingAudit.status == PayrollFilingStatus.GENERATED,
        )
        .all()
    )
    for record in prior_generated:
        record.status = PayrollFilingStatus.SUPERSEDED
        record.status_updated_at = _utcnow()
        record.status_updated_by_user_id = generated_by_user_id

    audit = PayrollFilingAudit(
        filing_type=filing_type,
        status=PayrollFilingStatus.GENERATED,
        employee_id=employee_id,
        pay_run_id=pay_run_id,
        source_hash=source_hash,
        source_snapshot=serialized,
        export_filename=export_filename,
        generated_by_user_id=generated_by_user_id,
        status_updated_by_user_id=generated_by_user_id,
    )
    db.add(audit)
    db.flush()
    return audit


def _record_snapshot_hash_for_current_source(db: Session, record: PayrollFilingAudit, settings: dict) -> str:
    if record.employee_id:
        employee = record.employee
        snapshot = employee_filing_snapshot(employee, record.filing_type, settings)
    elif record.pay_run_id:
        snapshot = pay_run_filing_snapshot(record.pay_run, settings)
    else:
        snapshot = {}
    _serialized, source_hash = _snapshot_payload(snapshot)
    return source_hash


def audit_record_response(db: Session, record: PayrollFilingAudit, settings: dict) -> PayrollFilingAuditResponse:
    changed_since_source = _record_snapshot_hash_for_current_source(db, record, settings) != record.source_hash
    return PayrollFilingAuditResponse(
        id=record.id,
        filing_type=record.filing_type,
        status=record.status.value if hasattr(record.status, 'value') else str(record.status),
        employee_id=record.employee_id,
        pay_run_id=record.pay_run_id,
        export_filename=record.export_filename,
        export_reference=record.export_reference,
        notes=record.notes,
        generated_by_user_id=record.generated_by_user_id,
        status_updated_by_user_id=record.status_updated_by_user_id,
        generated_at=record.generated_at,
        status_updated_at=record.status_updated_at,
        changed_since_source=changed_since_source,
    )


def list_employee_filing_history(db: Session, employee_id: int, settings: dict) -> list[PayrollFilingAuditResponse]:
    records = (
        db.query(PayrollFilingAudit)
        .filter(PayrollFilingAudit.employee_id == employee_id)
        .order_by(PayrollFilingAudit.generated_at.desc(), PayrollFilingAudit.id.desc())
        .all()
    )
    return [audit_record_response(db, record, settings) for record in records]


def list_pay_run_filing_history(db: Session, pay_run_id: int, settings: dict) -> list[PayrollFilingAuditResponse]:
    records = (
        db.query(PayrollFilingAudit)
        .filter(PayrollFilingAudit.pay_run_id == pay_run_id)
        .order_by(PayrollFilingAudit.generated_at.desc(), PayrollFilingAudit.id.desc())
        .all()
    )
    return [audit_record_response(db, record, settings) for record in records]


def update_filing_audit_status(
    db: Session,
    *,
    record: PayrollFilingAudit,
    status: str,
    reference: str | None,
    notes: str | None,
    user_id: int | None = None,
    settings: dict,
) -> PayrollFilingAuditResponse:
    normalized = str(status or '').lower()
    if normalized not in {'filed', 'amended'}:
        raise HTTPException(status_code=400, detail='Unsupported filing status update')
    if getattr(record.status, 'value', record.status) == PayrollFilingStatus.SUPERSEDED.value:
        raise HTTPException(status_code=400, detail='Cannot update a superseded filing record')

    record.status = PayrollFilingStatus.FILED if normalized == 'filed' else PayrollFilingStatus.AMENDED
    record.export_reference = reference
    record.notes = notes
    record.status_updated_at = _utcnow()
    record.status_updated_by_user_id = user_id
    db.commit()
    db.refresh(record)
    return audit_record_response(db, record, settings)
