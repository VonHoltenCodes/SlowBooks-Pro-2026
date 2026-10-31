# ============================================================================
# WA L&I — workers' compensation premium rates by risk classification.
# Unlike most payroll taxes, L&I is assessed PER HOUR WORKED, not as a % of
# wages. Each class has a composite hourly rate; a portion may be deducted
# from the worker, the remainder is the employer's expense.
#
# Figures: WA Dept. of Labor & Industries, "2026 Hourly Rates by Risk
# Classification Code and Fund" — the Composite Rate and Payroll Deduction
# columns, which "assume an experience factor of 1.0000" (the base rates; an
# employer's rate notice applies its own experience factor).
# https://lni.wa.gov/insurance/_docs/2026RatesByClassCodeFund.pdf
# Class titles from WAC 296-17A (app.leg.wa.gov). Verified 2026-10-31.
# ============================================================================

from decimal import Decimal

# class_code -> {"total": hourly composite, "employee": hourly worker deduction}
WA_LNI_RATES = {
    "4904": {
        "total": Decimal("0.2223"),
        "employee": Decimal("0.10070"),
    },  # Clerical office, N.O.C.
    "5206": {
        "total": Decimal("0.9828"),
        "employee": Decimal("0.21480"),
    },  # Permanent yard or shop operations (construction, logging, trucking)
    "6303": {
        "total": Decimal("0.3019"),
        "employee": Decimal("0.11335"),
    },  # Outside sales personnel / messengers
    "0101": {
        "total": Decimal("2.0272"),
        "employee": Decimal("0.35320"),
    },  # Excavation work and land clearing, N.O.C.
    "0510": {
        "total": Decimal("4.6938"),
        "employee": Decimal("0.78965"),
    },  # Wood frame building construction or alterations, N.O.C.
    "0507": {
        "total": Decimal("5.7930"),
        "employee": Decimal("1.00340"),
    },  # Roofwork construction and repair
    "3905": {
        "total": Decimal("0.5126"),
        "employee": Decimal("0.16165"),
    },  # Restaurants, N.O.C.
    "3909": {
        "total": Decimal("0.8528"),
        "employee": Decimal("0.22775"),
    },  # Caterers
    "7100": {
        "total": Decimal("0.2325"),
        "employee": Decimal("0.10080"),
    },  # Exempt limited liability company members, N.O.C.
}

# Fallback when an employee has no class code or an unknown one. This is a
# placeholder, not an L&I figure: the right rate is the one on the
# employer's rate notice for the employee's class.
WA_LNI_DEFAULT = {"total": Decimal("0.2000"), "employee": Decimal("0.0800")}


def get_lni_rate(class_code: str | None) -> dict:
    """Return {'total', 'employee'} hourly L&I rates for a risk class code."""
    if class_code and class_code in WA_LNI_RATES:
        return WA_LNI_RATES[class_code]
    return WA_LNI_DEFAULT
