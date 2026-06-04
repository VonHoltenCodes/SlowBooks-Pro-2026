"""GET /api/settings must not echo plaintext secrets.

Pre-fix the settings GET returned every stored credential (Stripe secret
key, SMTP password, QBO refresh token, closing-period override password)
in cleartext. Anyone with a session — and anyone who could induce the
operator to share a screenshot or copy a settings URL — saw the values.

Redaction shows SECRET_PLACEHOLDER when a secret is set and "" when it
isn't, so the UI can render a "configured / not configured" state without
ever exposing the value. The PUT round-trips the placeholder as a no-op
so editing any other setting doesn't accidentally overwrite the real
credential with the literal "********".
"""

from app.routes.settings import SECRET_KEYS, SECRET_PLACEHOLDER


def _set_settings(client, **kwargs):
    r = client.put("/api/settings", json=kwargs)
    assert r.status_code == 200, r.text
    return r.json()


def test_get_redacts_each_secret_key(client):
    written = {k: f"real-{k}-value" for k in SECRET_KEYS}
    _set_settings(client, **written)

    got = client.get("/api/settings").json()
    for k in SECRET_KEYS:
        assert (
            got[k] == SECRET_PLACEHOLDER
        ), f"settings[{k!r}] leaked plaintext: {got[k]!r}"


def test_get_reports_empty_for_unset_secrets(client):
    got = client.get("/api/settings").json()
    for k in SECRET_KEYS:
        assert got[k] == "", f"unset secret {k!r} reported as {got[k]!r}"


def test_put_placeholder_does_not_overwrite_stored_secret(client, db_session):
    """The UI re-PUTs the GET response when the operator edits one field
    without touching the password. Receiving the placeholder must be a
    no-op rather than overwriting the real secret with "********"."""
    _set_settings(client, stripe_secret_key="sk_live_REAL")

    # Read back, prove it's redacted, and submit the redacted value back.
    got = client.get("/api/settings").json()
    assert got["stripe_secret_key"] == SECRET_PLACEHOLDER

    _set_settings(
        client, stripe_secret_key=SECRET_PLACEHOLDER, company_name="Edited Co"
    )

    # Underlying value preserved
    from app.services.settings_service import get_all_settings

    stored = get_all_settings(db_session)
    assert stored["stripe_secret_key"] == "sk_live_REAL"
    assert stored["company_name"] == "Edited Co"


def test_put_real_value_overwrites_secret(client, db_session):
    """The operator can still rotate credentials by submitting a new
    non-placeholder value."""
    _set_settings(client, stripe_secret_key="sk_live_OLD")
    _set_settings(client, stripe_secret_key="sk_live_NEW")

    from app.services.settings_service import get_all_settings

    stored = get_all_settings(db_session)
    assert stored["stripe_secret_key"] == "sk_live_NEW"


def test_non_secret_settings_pass_through(client):
    _set_settings(client, company_name="Acme", invoice_prefix="INV-")
    got = client.get("/api/settings").json()
    assert got["company_name"] == "Acme"
    assert got["invoice_prefix"] == "INV-"
