# ============================================================================
# Decompiled from qbw32.exe!CPreferencesDialog  Offset: 0x0023F800
# Original: tabbed dialog (IDD_PREFERENCES) with 12 tabs. We condensed
# everything into a single key-value store because nobody needs 12 tabs.
# ============================================================================

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.settings import DEFAULT_SETTINGS
from app.services.settings_service import get_all_settings, set_setting

# Aliases used by upstream Phase 9/10 routes that import from this module
_get_all = get_all_settings
_set = set_setting


# Settings keys whose values are credentials / secrets — never echo the
# plaintext value back via GET. Anyone with a session would otherwise be
# able to scrape every stored Stripe/QBO/SMTP credential and the closing-
# period override password. Empty values still report as empty so the UI
# can render an unconfigured state; non-empty values report SECRET_PLACEHOLDER
# so the operator can tell the value is set without exposing it.
SECRET_KEYS = frozenset(
    {
        "closing_date_password",
        "smtp_password",
        "stripe_secret_key",
        "stripe_webhook_secret",
        "qbo_client_secret",
        "qbo_access_token",
        "qbo_refresh_token",
    }
)
SECRET_PLACEHOLDER = "********"


def _redact_secrets(settings: dict) -> dict:
    """Return a copy of `settings` with secret values replaced by the
    placeholder when non-empty."""
    return {
        k: (SECRET_PLACEHOLDER if (k in SECRET_KEYS and v) else v)
        for k, v in settings.items()
    }


class SettingsUpdate(BaseModel):
    # Accept any subset of DEFAULT_SETTINGS keys. Unknown keys are silently
    # ignored by the handler (same as before). We keep this permissive because
    # DEFAULT_SETTINGS is the authoritative key list, not the schema.
    model_config = ConfigDict(extra="allow")


router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    return _redact_secrets(get_all_settings(db))


@router.put("")
def update_settings(data: SettingsUpdate, db: Session = Depends(get_db)):
    # model_dump returns extras plus any declared fields. Still whitelisted
    # against DEFAULT_SETTINGS so unknown keys are silently dropped.
    #
    # For SECRET_KEYS: if the incoming value is the redaction placeholder,
    # skip the update. Otherwise the UI would round-trip the placeholder
    # back into storage and silently overwrite the real secret when the
    # operator edits any other setting without re-typing the password.
    for key, value in data.model_dump().items():
        if key not in DEFAULT_SETTINGS:
            continue
        if key in SECRET_KEYS and value == SECRET_PLACEHOLDER:
            continue
        set_setting(db, key, str(value) if value is not None else "")
    db.commit()
    return _redact_secrets(get_all_settings(db))


@router.post("/test-email")
def test_email(db: Session = Depends(get_db)):
    """Feature 8: Send a test email to verify SMTP settings."""
    settings = get_all_settings(db)
    if not settings.get("smtp_host"):
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="SMTP not configured")
    try:
        from app.services.email_service import send_email

        send_email(
            to_email=settings.get("smtp_from_email") or settings.get("smtp_user", ""),
            subject="Slowbooks Pro 2026 — Test Email",
            html_body="<p>This is a test email from Slowbooks Pro 2026. SMTP is configured correctly.</p>",
            settings=settings,
        )
        return {"status": "sent"}
    except Exception as e:
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail=f"Email failed: {str(e)}")
