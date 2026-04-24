# ============================================================================
# Decompiled from qbw32.exe!CCompanyInfo + CQBPreferences
# Offset: 0x00241200 / 0x0023F000
# Original stored company info in the .QBW file header (bytes 0x40-0x1FF)
# encrypted with a simple XOR 0x1F cipher. Preferences lived in the registry
# at HKCU\Software\Intuit\QuickBooks\12.0\Preferences.
# ============================================================================

from sqlalchemy import Column, Integer, String, Text, DateTime, func

from app.database import Base


class Settings(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# Default settings keys
DEFAULT_SETTINGS = {
    "company_name": "My Company",
    "company_address1": "",
    "company_address2": "",
    "company_city": "",
    "company_state": "",
    "company_zip": "",
    "company_phone": "",
    "company_email": "",
    "company_website": "",
    "company_tax_id": "",
    "default_terms": "Net 30",
    "default_tax_rate": "0.0",
    "invoice_prefix": "",
    "invoice_next_number": "1001",
    "estimate_prefix": "E-",
    "estimate_next_number": "1001",
    "invoice_notes": "Thank you for your business.",
    "invoice_footer": "",
    # Feature 10: Closing Date Enforcement
    "closing_date": "",
    "closing_date_password": "",
    # Feature 8: SMTP Email
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from_email": "",
    "smtp_from_name": "",
    "smtp_use_tls": "true",
    # Feature 15: Company Logo
    "company_logo_path": "",
    # Stripe Online Payments
    "stripe_enabled": "false",
    "stripe_publishable_key": "",
    "stripe_secret_key": "",
    "stripe_webhook_secret": "",
    # QuickBooks Online Integration
    "qbo_enabled": "false",
    "qbo_client_id": "",
    "qbo_client_secret": "",
    "qbo_redirect_uri": "http://localhost:3001/api/qbo/callback",
    "qbo_environment": "sandbox",
    "qbo_access_token": "",
    "qbo_refresh_token": "",
    "qbo_realm_id": "",
    "qbo_token_expires_at": "",
    "qbo_oauth_state": "",
    # Phase 10: Late Fee Automation
    "late_fee_enabled": "false",
    "late_fee_rate": "1.5",
    "late_fee_grace_days": "15",
}
