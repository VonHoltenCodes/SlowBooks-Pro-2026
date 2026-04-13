# ============================================================================
# Payroll — placeholder routes for the NZ payroll rebuild
# PAYE calculations, KiwiSaver deductions, student loan deductions, ESCT,
# and NZ payslips are implemented in later slices.
# ============================================================================

from fastapi import APIRouter, HTTPException

from app.schemas.payroll import PayRunCreate

router = APIRouter(prefix="/api/payroll", tags=["payroll"])

PAYROLL_NZ_PLACEHOLDER_DETAIL = (
    "NZ payroll setup is ready, but PAYE calculations, KiwiSaver deductions, "
    "and NZ payslips are not implemented yet."
)


@router.get("")
def list_pay_runs():
    raise HTTPException(status_code=410, detail=PAYROLL_NZ_PLACEHOLDER_DETAIL)


@router.get("/{run_id}")
def get_pay_run(run_id: int):
    raise HTTPException(status_code=410, detail=PAYROLL_NZ_PLACEHOLDER_DETAIL)


@router.post("")
def create_pay_run(data: PayRunCreate):
    raise HTTPException(status_code=410, detail=PAYROLL_NZ_PLACEHOLDER_DETAIL)


@router.post("/{run_id}/process")
def process_pay_run(run_id: int):
    raise HTTPException(status_code=410, detail=PAYROLL_NZ_PLACEHOLDER_DETAIL)
