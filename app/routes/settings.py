# ============================================================================
# Decompiled from qbw32.exe!CPreferencesDialog  Offset: 0x0023F800
# Original: tabbed dialog (IDD_PREFERENCES) with 12 tabs. We condensed
# everything into a single key-value store because nobody needs 12 tabs.
# ============================================================================

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.settings import Settings, DEFAULT_SETTINGS
from app.services.auth import require_permissions
from app.services.chart_template_loader import load_chart_template as run_chart_template_load
from scripts.seed_nz_demo_data import seed as run_demo_seed

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


@router.get("/public")
def get_public_settings(db: Session = Depends(get_db)):
    result = _get_all(db)
    allowed_keys = (
        "company_name",
        "country",
        "currency",
        "locale",
        "timezone",
        "tax_regime",
        "gst_basis",
        "gst_period",
        "prices_include_gst",
    )
    return {key: result.get(key, DEFAULT_SETTINGS.get(key, "")) for key in allowed_keys}


@router.get("")
def get_settings(
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("settings.manage")),
):
    return _get_all(db)


@router.put("")
def update_settings(
    data: dict,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("settings.manage")),
):
    for key, value in data.items():
        if key in DEFAULT_SETTINGS:
            _set(db, key, str(value) if value is not None else "")
    db.commit()
    return _get_all(db)


@router.post("/test-email")
def test_email(
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("settings.manage")),
):
    """Feature 8: Send a test email to verify SMTP settings."""
    settings = _get_all(db)
    if not settings.get("smtp_host"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="SMTP not configured")
    try:
        from app.services.email_service import send_email_or_raise
        send_email_or_raise(
            db,
            to_email=settings.get("smtp_from_email") or settings.get("smtp_user", ""),
            subject="Slowbooks Pro 2026 — Test Email",
            html_body="<p>This is a test email from Slowbooks Pro 2026. SMTP is configured correctly.</p>",
            entity_type="settings_test",
            entity_id=0,
        )
        return {"status": "sent"}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"Email failed: {str(e)}")



@router.post("/load-chart-template/{template_key}")
def load_chart_template(
    template_key: str,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("settings.manage")),
):
    from fastapi import HTTPException
    try:
        return run_chart_template_load(db, template_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/load-demo-data")
def load_demo_data(
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("settings.manage")),
):
    run_demo_seed()
    return {"status": "loaded"}
