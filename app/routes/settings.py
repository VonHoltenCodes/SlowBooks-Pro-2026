# ============================================================================
# Decompiled from qbw32.exe!CPreferencesDialog  Offset: 0x0023F800
# Original: tabbed dialog (IDD_PREFERENCES) with 12 tabs. We condensed
# everything into a single key-value store because nobody needs 12 tabs.
# ============================================================================

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.settings import Settings, DEFAULT_SETTINGS


class SettingsUpdate(BaseModel):
    # Accept any subset of DEFAULT_SETTINGS keys. Unknown keys are silently
    # ignored by the handler (same as before). We keep this permissive because
    # DEFAULT_SETTINGS is the authoritative key list, not the schema.
    model_config = ConfigDict(extra="allow")

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _get_all(db: Session) -> dict:
    rows = db.query(Settings).all()
    result = dict(DEFAULT_SETTINGS)
    for row in rows:
        result[row.key] = row.value
    return result


def _set(db: Session, key: str, value: str):
    row = db.query(Settings).filter(Settings.key == key).first()
    if row:
        row.value = value
    else:
        row = Settings(key=key, value=value)
        db.add(row)


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    return _get_all(db)


@router.put("")
def update_settings(data: SettingsUpdate, db: Session = Depends(get_db)):
    # model_dump returns extras plus any declared fields. Still whitelisted
    # against DEFAULT_SETTINGS so unknown keys are silently dropped.
    for key, value in data.model_dump().items():
        if key in DEFAULT_SETTINGS:
            _set(db, key, str(value) if value is not None else "")
    db.commit()
    return _get_all(db)


@router.post("/test-email")
def test_email(db: Session = Depends(get_db)):
    """Feature 8: Send a test email to verify SMTP settings."""
    settings = _get_all(db)
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
