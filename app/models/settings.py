# ============================================================================
# Settings — one key-value table, merged over DEFAULT_SETTINGS on read.
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
    # Company type drives the vocabulary (Customer/Donor, Invoice/Pledge,
    # Class/Fund ...), the nonprofit nav items and reports, the default
    # dashboard layout and the on-demand net-asset accounts. Data never
    # changes name; only what is rendered. business | nonprofit
    "company_type": "business",
    # Legal entity type (#488) — separate from company_type. Drives which
    # federal return the company files (Schedule C only for a sole proprietor
    # or a single-member LLC taxed as one). "" = unset, so existing companies
    # see no change until they choose. llc_tax_election is only meaningful
    # when entity_type == "llc". Books never change; this is tax form only.
    # entity_type: sole_proprietor | general_partnership | limited_partnership
    #              | llp | llc | c_corp | s_corp
    # llc_tax_election: disregarded | partnership | s_corp | c_corp
    "entity_type": "",
    "llc_tax_election": "",
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
    # Desktop: "true" signs the company out whenever the app is quit, so the
    # next start asks for the password (services/auth.py). Off by default.
    "ask_password_on_start": "false",
    "default_terms": "Net 30",
    "default_tax_rate": "0.0",
    "invoice_prefix": "",
    "invoice_next_number": "1001",
    "estimate_prefix": "E-",
    "estimate_next_number": "1001",
    "invoice_notes": "Thank you for your business.",
    "invoice_footer": "",
    # Giving statements (#415, nonprofit mode): the ministry's own footer on
    # the year-end statement (its address, website, EIN, a verse) replaces the
    # old product footer. Plain text with line breaks, escaped on the page;
    # empty prints what printed before, minus the product name. The opening
    # message is a saved giving-statement message (invoice_messages table), not
    # a settings key. The IRS sentence is always printed and is not editable.
    "giving_statement_footer": "",
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
    "invoice_show_logo": "true",
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
    # JSON [simplefin id, ...]: feeds whose bank connection reports amounts
    # with the opposite sign (deposits negative); imported flipped
    "simplefin_reversed": "[]",
    "simplefin_accounts_cache": "[]",  # last-seen bridge accounts, for the UI
    "simplefin_last_sync": "",
    # Class tracking (#243): "true" starts every entry form with no class
    # chosen and asks before a transaction is saved without one (it would
    # be reported under Uncategorized); "false" starts the picker on
    # Uncategorized and asks nothing.
    "class_warn_blank": "false",
    # Sidebar items a company doesn't use, hidden from the menu (#334): a
    # JSON list of nav-link pages ("payroll", "hr-onboarding", …) and nav
    # sections ("section:Payroll & HR"). The pages stay reachable by
    # address. Empty = everything shows.
    "hidden_menu": "[]",
    # Direct deposit (discussion #211): what the bank needs at the top of
    # an ACH file, saved once so a processed pay run downloads its file
    # from the page. The bank's routing number is the file's destination
    # and the originating DFI; the two 10-character identifiers are the
    # bank's to assign (usually "1" + EIN) and fall back to the EIN; the
    # company account funds the credits and is encrypted at rest.
    "ach_bank_routing": "",
    "ach_bank_name": "",
    "ach_immediate_origin": "",
    "ach_company_id": "",
    "ach_company_account": "",
    # Phase 10: Late Fee Automation
    "late_fee_enabled": "false",
    "late_fee_rate": "1.5",
    "late_fee_grace_days": "15",
}
