# ============================================================================
# Tax Forms — payroll tax-form generation
# ----------------------------------------------------------------------------
# Aggregates PROCESSED pay stubs into the federal/state employer returns and
# per-employee wage statements that payroll departments file each period:
#   form_941   — quarterly federal employer return (IRS Form 941)
#   form_940   — annual FUTA return (IRS Form 940)
#   w2_w3      — per-employee W-2 + W-3 transmittal
#   state_sui  — quarterly state unemployment (SUI/SUTA)
#
# All money is decimal.Decimal, quantized to cents. A pay stub is assigned to
# a calendar quarter/year by its PayRun.pay_date.
#
# DISCLAIMER: Box mappings model the published IRS form structure. Verify
# against the current-year forms before relying on these for actual filing.
# ============================================================================

from app.services.tax_forms import form_941, form_940, w2_w3, state_sui

__all__ = ["form_941", "form_940", "w2_w3", "state_sui"]
