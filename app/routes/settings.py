# ============================================================================
# Decompiled from qbw32.exe!CPreferencesDialog  Offset: 0x0023F800
# Original: tabbed dialog (IDD_PREFERENCES) with 12 tabs. We condensed
# everything into a single key-value store because nobody needs 12 tabs.
# ============================================================================

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.settings import DEFAULT_SETTINGS
from app.services.settings_service import get_all_settings, set_setting

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    return get_all_settings(db)


@router.put("")
def update_settings(data: dict, db: Session = Depends(get_db)):
    for key, value in data.items():
        if key in DEFAULT_SETTINGS:
            set_setting(db, key, str(value) if value is not None else "")
    db.commit()
    return get_all_settings(db)


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
