# ============================================================================
# WA L&I — workers' compensation premium rates by risk classification.
# Unlike most payroll taxes, L&I is assessed PER HOUR WORKED, not as a % of
# wages. Each class has a composite hourly rate; a portion may be deducted
# from the worker, the remainder is the employer's expense.
#
# Rates are published annually by the WA Dept. of Labor & Industries. The
# figures below are representative 2026 composite rates for common classes —
# replace with the employer's actual rate notice for production use.
# ============================================================================

from decimal import Decimal

# class_code -> {"total": hourly composite, "employee": hourly employee share}
WA_LNI_RATES = {
    "5206": {
        "total": Decimal("0.0600"),
        "employee": Decimal("0.0250"),
    },  # Clerical / office
    "6303": {
        "total": Decimal("0.1400"),
        "employee": Decimal("0.0600"),
    },  # Outside sales / messengers
    "0101": {
        "total": Decimal("0.9800"),
        "employee": Decimal("0.3600"),
    },  # Excavation / earthwork
    "0510": {
        "total": Decimal("1.1200"),
        "employee": Decimal("0.4000"),
    },  # Wood-frame construction
    "0540": {"total": Decimal("1.4500"), "employee": Decimal("0.5200")},  # Roof work
    "3909": {
        "total": Decimal("0.2600"),
        "employee": Decimal("0.1000"),
    },  # Warehouse / stores
    "7100": {
        "total": Decimal("0.1100"),
        "employee": Decimal("0.0450"),
    },  # Restaurant / food service
}

# Fallback when an employee has no class code or an unknown one.
WA_LNI_DEFAULT = {"total": Decimal("0.2000"), "employee": Decimal("0.0800")}


def get_lni_rate(class_code: str | None) -> dict:
    """Return {'total', 'employee'} hourly L&I rates for a risk class code."""
    if class_code and class_code in WA_LNI_RATES:
        return WA_LNI_RATES[class_code]
    return WA_LNI_DEFAULT
