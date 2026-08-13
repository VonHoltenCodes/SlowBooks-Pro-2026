# Accountant File Sharing — design notes (brainstorm, 2026-08-13)

Status: DESIGN. Two candidate architectures, not yet chosen.

## Option A — gated single-use drop-box

Client-side Fernet-encrypted export; outbound-only POST to a Cloudflare
Worker (same self-hosted pattern as the AI gateway) holding the
encrypted blob with a TTL; PIN delivered out-of-band; the worker never
sees plaintext. Self-destructing link closes the standing-exposure
problem entirely.

## Option B — scheduled email (the simple pivot)

Clone `run_recurring.py`'s scheduling pattern exactly; use
`email_service.send_email()` (already supports attachments) with the
existing IIF/PDF export functions. Pure wiring, no new infrastructure.

Tradeoff (flagged, unresolved): an email attachment is a standing
exposure — it lives in two inboxes indefinitely — vs. Option A's TTL'd
self-destructing link. A password-protected zip + out-of-band PIN
closes most of the gap cheaply if Option B wins.

## Prerequisite — ALREADY SHIPPED

The smtp_password at-rest encryption gap identified during this
brainstorm was fixed independently and released in v2.5.2
(ENCRYPTED_SETTINGS_KEYS in settings_service). The pipe that would
carry monthly financial statements is already sealed.
