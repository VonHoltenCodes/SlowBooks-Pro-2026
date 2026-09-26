import logging

# ============================================================================
# Settings — QuickBooks 2003 had a 12-tab preferences dialog; we condensed
# everything into a single key-value store because nobody needs 12 tabs.
# ============================================================================

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.settings import DEFAULT_SETTINGS
from app.services.settings_service import (
    ENCRYPTED_SETTINGS_KEYS,
    SECRET_PLACEHOLDER,
    SettingValueError,
    clean_setting_value,
    get_all_settings,
    redact_secrets,
    set_setting,
)

# Aliases used by upstream Phase 9/10 routes that import from this module
_get_all = get_all_settings
_set = set_setting


# Settings keys whose values are credentials / secrets — never echo the
# plaintext value back via GET. Anyone with a session would otherwise be
# able to scrape every stored Stripe/QBO/SMTP credential and the closing-
# period override password. Empty values still report as empty so the UI
# can render an unconfigured state; non-empty values report SECRET_PLACEHOLDER
# so the operator can tell the value is set without exposing it.
# ONE list, in settings_service, beside the encryption it mirrors. This file
# used to carry a second frozenset identical to ENCRYPTED_SETTINGS_KEYS; they
# happened to agree, and nothing would have said so if they stopped. A
# credential added to one and not the other is encrypted at rest and rendered
# in plaintext by an email template (GHSA-c3v4-f43f-4wqm).
SECRET_KEYS = ENCRYPTED_SETTINGS_KEYS
_redact_secrets = redact_secrets


# Settings whose value is one of a fixed set. The SPA renders a <select>;
# this is the server-side twin so an API token cannot store "banana".
ENUM_SETTINGS = {
    "company_type": frozenset({"business", "nonprofit"}),
    "ocr_engine": frozenset({"auto", "tesseract"}),
}


class SettingsUpdate(BaseModel):
    # Accept any subset of DEFAULT_SETTINGS keys. Unknown keys are silently
    # ignored by the handler (same as before). We keep this permissive because
    # DEFAULT_SETTINGS is the authoritative key list, not the schema.
    model_config = ConfigDict(extra="allow")


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    """Every company setting, secrets redacted.

    Units to know before copying a value onto a document:
    `default_tax_rate` is a PERCENT string as the user types it ("8.9" =
    8.9%); a document's `tax_rate` (invoices, bills, estimates, credit
    memos, sales receipts, purchase orders, recurring templates) is a
    FRACTION (0.089). Divide by 100 before posting; the API rejects a
    document `tax_rate` above 1.
    """
    return _redact_secrets(get_all_settings(db))


def _guard_closing_period(request: Request, db: Session, fields: dict):
    """The closing-date lock is only a control if the principal it constrains
    cannot switch it off. A token could previously clear closing_date, post
    into the closed period, and set closing_date_password — while the agent
    documentation states the override password is "off-limits to agents
    entirely".

    Scoped deliberately to token principals. A human with a session is the
    person the lock exists to serve and can still move or clear it; requiring
    the override password from them would lock out anyone who set one and
    forgot it. Tokens may still move the date FORWARD (tightening the lock),
    only loosening is refused.
    """
    principal = getattr(getattr(request, "state", None), "token_principal", None)
    if principal is None:
        return  # session user — unchanged behaviour

    if "closing_date_password" in fields:
        raise HTTPException(
            status_code=403,
            detail="API tokens cannot set the closing-date override password.",
        )

    if "closing_date" not in fields:
        return

    def _parse(v):
        if not v:
            return None
        try:
            return date.fromisoformat(str(v))
        except ValueError:
            return None

    from app.services.closing_date import get_closing_date

    current = get_closing_date(db)
    if current is None:
        return  # no lock set yet — nothing to loosen

    incoming = _parse(fields.get("closing_date"))
    if incoming is None or incoming < current:
        raise HTTPException(
            status_code=403,
            detail=(
                f"API tokens cannot clear or roll back the closing date "
                f"(currently {current.isoformat()}). Moving it forward is "
                f"allowed; loosening it requires a signed-in user."
            ),
        )


@router.put("")
def update_settings(
    data: SettingsUpdate, request: Request, db: Session = Depends(get_db)
):
    # model_dump returns extras plus any declared fields. Still whitelisted
    # against DEFAULT_SETTINGS so unknown keys are silently dropped.
    #
    # For SECRET_KEYS: if the incoming value is the redaction placeholder,
    # skip the update. Otherwise the UI would round-trip the placeholder
    # back into storage and silently overwrite the real secret when the
    # operator edits any other setting without re-typing the password.
    _guard_closing_period(
        request,
        db,
        {k: v for k, v in data.model_dump().items() if k in DEFAULT_SETTINGS},
    )
    incoming = data.model_dump()
    if incoming.get("company_name"):
        from app.services.company_service import (
            _current_company_file,
            company_name_taken_by,
        )

        other = company_name_taken_by(
            incoming["company_name"], exclude_file=_current_company_file()
        )
        if other:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Another company file ({other}) is already named "
                    f"'{incoming['company_name'].strip()}'. Choose a name that "
                    "tells the two apart."
                ),
            )
    # Check every value before writing any: one refused field must not leave
    # the others half-saved, and the person sees every problem at once.
    cleaned = {}
    problems = []
    for key, value in data.model_dump().items():
        if key not in DEFAULT_SETTINGS:
            continue
        allowed = ENUM_SETTINGS.get(key)
        if allowed is not None and value not in allowed:
            raise HTTPException(
                status_code=422,
                detail=f"{key} must be one of: {', '.join(sorted(allowed))}",
            )
        if key in SECRET_KEYS and value == SECRET_PLACEHOLDER:
            continue
        try:
            cleaned[key] = clean_setting_value(key, value)
        except SettingValueError as exc:
            problems.append(str(exc))
    if problems:
        raise HTTPException(
            status_code=422,
            detail=" ".join(problems) + " Nothing was saved.",
        )
    for key, value in cleaned.items():
        set_setting(db, key, value)
    db.commit()
    if "company_name" in incoming:
        from app.services.company_service import sync_manifest_name

        sync_manifest_name(incoming.get("company_name"))
    return _redact_secrets(get_all_settings(db))


@router.post("/test-email")
def test_email(db: Session = Depends(get_db)):
    """Feature 8: Send a test email to verify SMTP settings."""
    settings = get_all_settings(db)
    if not settings.get("smtp_host"):
        raise HTTPException(status_code=400, detail="SMTP not configured")
    try:
        from app.services.email_service import send_email

        sent = send_email(
            db=db,
            to_email=settings.get("smtp_from_email") or settings.get("smtp_user", ""),
            subject="Slowbooks Pro 2026 — Test Email",
            html_body="<p>This is a test email from Slowbooks Pro 2026. SMTP is configured correctly.</p>",
            entity_type="settings_test",
        )
        if not sent:
            raise HTTPException(
                status_code=502,
                detail="Test email failed to send. See the email log for the reason.",
            )
        return {"status": "sent"}
    except HTTPException:
        # Don't let the catch-all below rewrite our own 502 into a 500.
        raise
    except Exception:
        # SMTP errors carry hostnames and server banners: log them, say only
        # that it failed (the email log has the reason, as the 502 says).
        logger.exception("Test email failed")
        raise HTTPException(
            status_code=500, detail="Email failed — see the email log for the reason"
        )
