# ============================================================================
# QBO Service — OAuth 2.0 helpers, token management, and client factory
#
# QuickBooks Online uses OAuth 2.0 Authorization Code flow:
#   1. User clicks "Connect" -> redirect to Intuit authorization URL
#   2. User approves -> Intuit redirects back with auth code
#   3. We exchange auth code for access_token (60 min) + refresh_token (100 days)
#   4. Before each API call, check expiry and auto-refresh if needed
#
# Tokens are stored in the settings table (same as Stripe keys, SMTP creds, etc.)
# ============================================================================

import uuid
from datetime import datetime, timezone

from intuitlib.client import AuthClient
from intuitlib.enums import Scopes
from quickbooks import QuickBooks
from sqlalchemy.orm import Session

from app.models.settings import Settings, DEFAULT_SETTINGS

# ============================================================================
# Settings helpers (mirror app/routes/settings.py pattern)
# ============================================================================


def _get_setting(db: Session, key: str) -> str:
    """Get a single setting value, falling back to DEFAULT_SETTINGS."""
    row = db.query(Settings).filter(Settings.key == key).first()
    if row:
        return row.value or ""
    return DEFAULT_SETTINGS.get(key, "")


def _set_setting(db: Session, key: str, value: str):
    """Upsert a single setting."""
    row = db.query(Settings).filter(Settings.key == key).first()
    if row:
        row.value = value
    else:
        row = Settings(key=key, value=value)
        db.add(row)


def get_all_qbo_settings(db: Session) -> dict:
    """Return all qbo_* settings as a dict."""
    result = {}
    for key in DEFAULT_SETTINGS:
        if key.startswith("qbo_"):
            result[key] = _get_setting(db, key)
    return result


# ============================================================================
# OAuth helpers
# ============================================================================


def _make_auth_client(db: Session) -> AuthClient:
    """Create an Intuit AuthClient from stored settings."""
    s = get_all_qbo_settings(db)
    environment = s.get("qbo_environment", "sandbox")
    return AuthClient(
        client_id=s["qbo_client_id"],
        client_secret=s["qbo_client_secret"],
        redirect_uri=s["qbo_redirect_uri"],
        environment=environment,
    )


def get_auth_url(db: Session) -> str:
    """Generate the Intuit OAuth authorization URL.

    Stores a random CSRF state token in settings for verification in callback.
    """
    state = uuid.uuid4().hex
    _set_setting(db, "qbo_oauth_state", state)
    db.commit()

    auth_client = _make_auth_client(db)
    url = auth_client.get_authorization_url(
        scopes=[Scopes.ACCOUNTING],
        state_token=state,
    )
    return url


def handle_callback(db: Session, code: str, state: str, realm_id: str):
    """Exchange authorization code for tokens and store them.

    Raises ValueError on CSRF mismatch.
    """
    stored_state = _get_setting(db, "qbo_oauth_state")
    if not stored_state or state != stored_state:
        raise ValueError("OAuth state mismatch — possible CSRF attack")

    auth_client = _make_auth_client(db)
    auth_client.get_bearer_token(code, realm_id=realm_id)

    # Store tokens
    _set_setting(db, "qbo_access_token", auth_client.access_token or "")
    _set_setting(db, "qbo_refresh_token", auth_client.refresh_token or "")
    _set_setting(db, "qbo_realm_id", realm_id)
    _set_setting(db, "qbo_oauth_state", "")  # clear CSRF token

    # Store expiry (access tokens last 3600 seconds = 60 min)
    if auth_client.expires_in:
        expires_at = datetime.now(timezone.utc).timestamp() + auth_client.expires_in
        _set_setting(db, "qbo_token_expires_at", str(int(expires_at)))

    db.commit()


def disconnect(db: Session):
    """Clear all stored QBO tokens."""
    for key in (
        "qbo_access_token",
        "qbo_refresh_token",
        "qbo_realm_id",
        "qbo_token_expires_at",
        "qbo_oauth_state",
    ):
        _set_setting(db, key, "")
    db.commit()


def is_connected(db: Session) -> bool:
    """Check if we have valid QBO tokens."""
    access = _get_setting(db, "qbo_access_token")
    realm = _get_setting(db, "qbo_realm_id")
    return bool(access and realm)


# ============================================================================
# QBO client factory
# ============================================================================


def _refresh_if_needed(db: Session):
    """Auto-refresh access token if expired."""
    expires_at_str = _get_setting(db, "qbo_token_expires_at")
    if not expires_at_str:
        return

    try:
        expires_at = int(expires_at_str)
    except ValueError:
        return

    now = int(datetime.now(timezone.utc).timestamp())
    if now < expires_at - 60:  # 60-second buffer
        return

    # Token expired or about to expire — refresh
    auth_client = _make_auth_client(db)
    auth_client.refresh(
        refresh_token=_get_setting(db, "qbo_refresh_token"),
    )

    _set_setting(db, "qbo_access_token", auth_client.access_token or "")
    if auth_client.refresh_token:
        _set_setting(db, "qbo_refresh_token", auth_client.refresh_token)

    if auth_client.expires_in:
        new_expires = datetime.now(timezone.utc).timestamp() + auth_client.expires_in
        _set_setting(db, "qbo_token_expires_at", str(int(new_expires)))

    db.commit()


def get_qbo_client(db: Session) -> QuickBooks:
    """Get a ready-to-use QuickBooks client with auto-refreshed tokens.

    Raises RuntimeError if not connected.
    """
    if not is_connected(db):
        raise RuntimeError("Not connected to QuickBooks Online")

    _refresh_if_needed(db)

    s = get_all_qbo_settings(db)
    environment = s.get("qbo_environment", "sandbox")

    auth_client = _make_auth_client(db)
    auth_client.access_token = s["qbo_access_token"]
    auth_client.refresh_token = s["qbo_refresh_token"]
    auth_client.realm_id = s["qbo_realm_id"]

    client = QuickBooks(
        auth_client=auth_client,
        refresh_token=s["qbo_refresh_token"],
        company_id=s["qbo_realm_id"],
        minorversion=65,
    )
    return client


def get_company_name(db: Session) -> str:
    """Fetch the connected company name from QBO."""
    try:
        client = get_qbo_client(db)
        from quickbooks.objects.company_info import CompanyInfo

        info_list = CompanyInfo.all(qb=client)
        if info_list:
            return getattr(info_list[0], "CompanyName", "") or ""
    except Exception:
        pass
    return ""
