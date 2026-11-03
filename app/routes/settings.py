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
    entity_info,
    get_all_settings,
    get_setting_raw,
    redact_secrets,
    set_setting,
    unreadable_secret_keys,
)

# Aliases used by upstream Phase 9/10 routes that import from this module
_get_all = get_all_settings
_set = set_setting


# Settings keys whose values are credentials / secrets — never echo the
# plaintext value back via GET. Anyone with a session would otherwise be
# able to scrape every stored Stripe/SMTP credential and the closing-
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
    "company_type": frozenset({"business", "nonprofit", "construction"}),
    "ocr_engine": frozenset({"auto", "tesseract"}),
    # Legal entity type (#488) — kept away from company_type so a sibling PR
    # that edits that line merges without a conflict. "" is unset and allowed
    # (an existing company that has not chosen yet); llc_tax_election is only
    # meaningful for an LLC.
    "entity_type": frozenset(
        {
            "",
            "sole_proprietor",
            "general_partnership",
            "limited_partnership",
            "llp",
            "llc",
            "c_corp",
            "s_corp",
        }
    ),
    "llc_tax_election": frozenset(
        {"", "disregarded", "partnership", "s_corp", "c_corp"}
    ),
    "ask_password_on_start": frozenset({"true", "false"}),
    "invoice_show_logo": frozenset({"true", "false"}),
    "invoice_logo_only": frozenset({"true", "false"}),
    "class_warn_blank": frozenset({"true", "false"}),
    "new_lines_taxable": frozenset({"true", "false"}),
    "use_account_numbers": frozenset({"true", "false"}),
    "check_stock": frozenset({"plain", "preprinted"}),
    "warn_date_past": frozenset({"true", "false"}),
    "warn_date_future": frozenset({"true", "false"}),
    "warn_duplicate_check_number": frozenset({"true", "false"}),
    "warn_edit_transaction": frozenset({"true", "false"}),
    "inventory_refuse_oversell": frozenset({"true", "false"}),
    "auto_enter_on_open": frozenset({"true", "false"}),
}


class SettingsUpdate(BaseModel):
    # Accept any subset of DEFAULT_SETTINGS keys. Unknown keys are silently
    # ignored by the handler (same as before). We keep this permissive because
    # DEFAULT_SETTINGS is the authoritative key list, not the schema.
    model_config = ConfigDict(extra="allow")


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _with_entity(db: Session) -> dict:
    """Settings with secrets redacted, plus a computed `entity` object
    (settings_service.entity_info) so one table — the server's — decides the
    entity label, federal return and the Schedule C gate. The UI reads this
    rather than copying the table (#488)."""
    settings = get_all_settings(db)
    result = _redact_secrets(settings)
    result["entity"] = entity_info(settings)
    return result


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    """Every company setting, secrets redacted.

    Units to know before copying a value onto a document:
    `default_tax_rate` is a PERCENT string as the user types it ("8.9" =
    8.9%, up to four decimal places: "8.875"); a document's `tax_rate`
    (invoices, bills, estimates, credit memos, sales receipts, purchase
    orders, recurring templates) is a FRACTION (0.089), kept to six places.
    Divide by 100 before posting; the API rejects a document `tax_rate`
    above 1.
    """
    return _with_entity(db)


@router.get("/unreadable-secrets")
def get_unreadable_secrets(db: Session = Depends(get_db)):
    """Settings saved encrypted under a key this install no longer has.

    They read as not set, so what uses them stops (email, a payment
    provider, a bank feed) instead of every page failing; Settings names
    them to be entered again. Recreating a Docker container before 2.18
    lost its settings key this way."""
    return {"keys": unreadable_secret_keys(db)}


@router.get("/payroll-accounts")
def get_payroll_accounts(db: Session = Depends(get_db)):
    """Where each piece of a pay run posts (#620): the pieces in posting
    order (net pay, gross wages, employer payroll taxes, each payable), each
    with its settings key, the account chosen for it (`chosen`, "" for the
    default), the seeded account it posts to when none is chosen
    (`default`), what it takes in words (`wanted`), and `problem`, the
    sentence that says why a chosen account no longer works; plus
    `choices`, per kind (bank, liability, expense), the active accounts a
    piece of that kind may be pointed at. The Settings page draws its
    Payroll Accounts pickers from this."""
    from app.services import payroll_accounts

    return payroll_accounts.describe(db)


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

    Since #659 no token reaches settings at all (an administrator's token is
    refused at the door and no other role writes settings); this stays as
    the backstop if that ever changes.
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


def _clearing_account_for_save(db, provider: str, value: str):
    """Resolve and validate a chosen account id from the settings form, or
    raise a 400. Shared shape for the clearing and fee pickers."""
    from app.models.accounts import Account

    try:
        acct_id = int(value)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail=f"{provider.title()} account id must be a number.",
        )
    acct = db.query(Account).filter(Account.id == acct_id).first()
    if acct is None:
        raise HTTPException(
            status_code=400,
            detail=f"The chosen {provider.title()} account does not exist.",
        )
    if acct.is_active is False:
        raise HTTPException(
            status_code=400,
            detail=f"The chosen {provider.title()} account is inactive.",
        )
    return acct


def _guard_clearing_accounts(db: Session, cleaned: dict) -> None:
    """A chosen online-payment clearing account must be an ordinary, active
    asset account — not a bank-feed account (deposits land there at payout,
    #584) and not a control account (1100 A/R, 1200 Undeposited Funds, …,
    which the posting code resolves by number). The fee account must be an
    active expense account. Refuse with a 400 at save rather than letting a
    bad id post nowhere later."""
    from app.models.accounts import AccountType
    from app.services.control_accounts import is_control

    for provider in ("stripe", "paypal", "square"):
        value = cleaned.get(f"{provider}_clearing_account_id")
        if value:  # "" clears the override and uses the default account
            acct = _clearing_account_for_save(db, provider, value)
            if (
                acct.account_type != AccountType.ASSET
                or acct.bank_kind
                or is_control(acct.account_number, acct.account_type)
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"The {provider.title()} clearing account must be an "
                        "ordinary asset account — not a bank, credit-card or "
                        "control account."
                    ),
                )
        fee_value = cleaned.get(f"{provider}_fee_account_id")
        if fee_value:
            fee_acct = _clearing_account_for_save(db, provider, fee_value)
            if fee_acct.account_type != AccountType.EXPENSE:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"The {provider.title()} fee account must be an "
                        "expense account."
                    ),
                )


def _guard_payroll_accounts(db: Session, cleaned: dict) -> None:
    """A chosen payroll posting account (#620) must exist, be active and be
    a kind the piece takes: a bank account or a liability for net pay, an
    expense or cost of goods sold account for wages and employer taxes, a
    liability that is not a credit card or a control account for each
    payable. Refused with a 400 saying so at save, rather than a pay run
    that posts somewhere wrong later. "" clears the choice (the seeded
    default); the table is services/payroll_accounts.py."""
    from app.models.accounts import Account
    from app.services import payroll_accounts

    for key, value in cleaned.items():
        piece = payroll_accounts.PIECE_BY_KEY.get(key)
        if piece is None or not value:
            continue
        acct_id = payroll_accounts.parse_id(value)
        if acct_id is None:
            raise HTTPException(
                status_code=400,
                detail=f"The {piece.label} account id must be a number.",
            )
        problem = payroll_accounts.account_problem(db.get(Account, acct_id), piece, db)
        if problem:
            raise HTTPException(status_code=400, detail=problem)


def _clearing_off_warnings(db: Session, cleaned: dict) -> list[str]:
    """Turning a provider's clearing off while its clearing account still
    holds money is allowed, with a warning (#584): payouts stay available
    until the balance is zero, and new captures go straight to the bank side
    (Undeposited Funds)."""
    from app.services.payments.clearing import (
        DISPLAY_NAMES,
        PROVIDER_NAMES,
        clearing_balance,
        clearing_enabled_db,
    )

    warnings = []
    for provider in PROVIDER_NAMES:
        key = f"{provider}_clearing_enabled"
        if cleaned.get(key) == "false" and clearing_enabled_db(db, provider):
            held = clearing_balance(db, provider)
            if held > 0:
                warnings.append(
                    f"{DISPLAY_NAMES[provider]} Clearing still holds {held:,.2f}. "
                    "New payments now skip it, but you can keep recording "
                    "payouts until it is paid out."
                )
    return warnings


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
                detail=(
                    f"{key} must be one of: "
                    + ", ".join(sorted(x for x in allowed if x))
                ),
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
    _guard_clearing_accounts(db, cleaned)
    _guard_payroll_accounts(db, cleaned)
    # The "Taxed as" select is hidden for a non-LLC but the form still submits
    # its last value; drop a stale election so it never lingers (#488).
    if "llc_tax_election" in cleaned:
        effective_type = cleaned.get("entity_type")
        if effective_type is None:
            effective_type = get_setting_raw(db, "entity_type") or ""
        if effective_type != "llc":
            cleaned["llc_tax_election"] = ""
    clearing_warnings = _clearing_off_warnings(db, cleaned)
    for key, value in cleaned.items():
        set_setting(db, key, value)
    # The equity accounts follow a newly chosen entity type while the chart
    # is still the untouched seed and nothing has posted (#488 step 2); the
    # answer says what happened either way.
    equity = None
    if "entity_type" in cleaned or "llc_tax_election" in cleaned:
        from app.services.entity_equity import apply_entity_equity

        db.flush()
        equity = apply_entity_equity(db, entity_info(get_all_settings(db)))
    db.commit()
    if "company_name" in incoming:
        from app.services.company_service import sync_manifest_name

        sync_manifest_name(incoming.get("company_name"))
    result = _with_entity(db)
    if equity is not None:
        result["entity_equity"] = equity
    if clearing_warnings:
        result["clearing_warnings"] = clearing_warnings
    return result


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
            # send_email() swallows the SMTP error and logs it; read that row
            # back so the failure can say the reason itself (#447) instead of
            # sending the user off to find a log that had no UI.
            from app.models.email_log import EmailLog
            from app.services.email_errors import (
                explain_send_failure,
                failure_sentence,
            )

            row = (
                db.query(EmailLog)
                .filter(EmailLog.entity_type == "settings_test")
                .order_by(EmailLog.id.desc())
                .first()
            )
            error_text = row.error_message if row else ""
            reason, _ = explain_send_failure(error_text, settings.get("smtp_host", ""))
            detail = "Test email failed to send."
            if reason:
                detail += f" {reason}"
            detail += " " + failure_sentence(error_text, settings.get("smtp_host", ""))
            detail += " The full list is under Settings → Email (SMTP) → Recent sends."
            raise HTTPException(status_code=502, detail=detail)
        return {"status": "sent"}
    except HTTPException:
        # Don't let the catch-all below rewrite our own 502 into a 500.
        raise
    except Exception:
        # A failure before send_email ran (never SMTP, which it catches): log
        # the traceback and point at the list without echoing internals.
        logger.exception("Test email failed")
        raise HTTPException(
            status_code=500,
            detail=(
                "Email failed — see Settings → Email (SMTP) → Recent sends "
                "for the reason."
            ),
        )
