# ============================================================================
# Shared APIRouter for the reports package.
# ============================================================================

from fastapi import APIRouter

router = APIRouter(prefix="/api/reports", tags=["reports"])
