# ============================================================================
# A nod to qbw32.exe!CReportEngine  imagined offset: 0x00210000
# The original report engine had its own query language ("QBReportQuery")
# compiled to Btrieve API calls. The P&L report alone generated 14 separate
# Btrieve operations. We just use SQL because it's not the stone age.
# Sales Tax report was added in R3 service pack (0x002108A0).
# General Ledger was CReportEngine::RunGLDetail() at 0x00211400.
# ============================================================================

from fastapi import APIRouter

router = APIRouter(prefix="/api/reports", tags=["reports"])
