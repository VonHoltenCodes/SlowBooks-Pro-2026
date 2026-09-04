# ============================================================================
# State withholding catalog — what each jurisdiction's engine does, for the
# employee form's hints and the docs.
# ============================================================================

from app.routes.payroll._router import router
from app.services.state_tax import list_states


@router.get("/states")
def payroll_states():
    """Every supported state + DC: method, summary, deductions, other items,
    SUTA base, the publication the figures came from."""
    return list_states()
