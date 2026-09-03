# ============================================================================
# Settings — one key-value table, merged over DEFAULT_SETTINGS on read.
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
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# Default settings keys
DEFAULT_SETTINGS = {
    # Receipt-scanning engine preference: "auto" = platform-native engine
    # first (Windows OCR / Apple Vision), tesseract fallback; "tesseract"
    # = prefer tesseract when installed (sharper region reads). The
    # SLOWBOOKS_OCR_ENGINE env var (support tool) outranks this.
    "ocr_engine": "auto",
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
    "operator_name": "",
    "operator_email": "",
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
    # Report PDF paper size: letter | a4
    "pdf_paper_size": "letter",
    # Opening-balance wizard readiness metadata
    "chart_setup_source": "",
    "chart_setup_ready_at": "",
    # Multi-currency: ISO code the general ledger is kept in
    "home_currency": "USD",
    # Stripe Online Payments
    "stripe_enabled": "false",
    "stripe_publishable_key": "",
    "stripe_secret_key": "",
    "stripe_webhook_secret": "",
    # PayPal Online Payments
    "paypal_enabled": "false",
    "paypal_environment": "sandbox",  # sandbox | live
    "paypal_client_id": "",
    "paypal_client_secret": "",
    "paypal_webhook_id": "",
    # Square Online Payments
    "square_enabled": "false",
    "square_environment": "sandbox",  # sandbox | production
    "square_access_token": "",
    "square_location_id": "",
    "square_webhook_signature_key": "",
    # Square signs webhooks over the EXACT notification URL registered in
    # its dashboard; set this to that URL (required for webhook verification)
    "square_notification_url": "",
    # SimpleFIN bank feeds (user-held bridge.simplefin.org credential).
    # access_url embeds basic-auth creds — listed in SECRET_KEYS.
    "simplefin_access_url": "",
    "simplefin_account_map": "{}",  # JSON {simplefin id: bank_account_id}
    "simplefin_accounts_cache": "[]",  # last-seen bridge accounts, for the UI
    "simplefin_last_sync": "",
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
