# ============================================================================
# The employer as payroll and tax documents print it.
# ----------------------------------------------------------------------------
# The pay stub, the 1099-NEC / 1096 and the New-Hire Report used to read the
# employer from environment config (COMPANY_NAME, default "My Company"),
# which a desktop company never sets — a stub named "My Company" as the
# employer (2.17.3, skytech W-M10). Every payroll document now prints the
# company in Settings, as invoices and the W-2 do; config fills only what
# Settings leaves blank.
# ============================================================================

from sqlalchemy.orm import Session

from app import config
from app.services.settings_service import get_all_settings

# the settings employer_block() reads (Form history's Check, #678, walks
# their audit-log history back to find the block a form was generated with)
EMPLOYER_SETTING_KEYS = (
    "company_name",
    "company_address1",
    "company_city",
    "company_state",
    "company_zip",
    "company_phone",
    "company_email",
    "company_tax_id",
)


def employer_block(db: Session) -> dict:
    """Name, address, phone, email and EIN of the company, for payroll PDFs."""
    return employer_block_from(get_all_settings(db))


def employer_block_from(s: dict) -> dict:
    """employer_block() from a settings dict already in hand."""
    return {
        "name": s.get("company_name") or config.COMPANY_NAME,
        "address": s.get("company_address1") or config.COMPANY_ADDRESS or "",
        "city": s.get("company_city") or "",
        "state": s.get("company_state") or config.EMPLOYER_STATE,
        "zip": s.get("company_zip") or "",
        "phone": s.get("company_phone") or config.COMPANY_PHONE or "",
        "email": s.get("company_email") or config.COMPANY_EMAIL or "",
        "ein": s.get("company_tax_id") or config.EMPLOYER_EIN,
    }
