# Security Hardening — SlowBooks Pro 2026

This is the engineering log of the production-readiness security pass. It
records what each change does, where it lives, and how it was tested, so
future maintainers can reason about the threat model without re-deriving it.

For the public security policy and responsible-disclosure address, see
[../SECURITY.md](../SECURITY.md).

---

## Hardening Pass Summary

| # | Area | What changed | Where |
|---|------|--------------|-------|
| 1 | Startup checks | Fail hard in production if `PAYROLL_ENCRYPTION_SECRET` is the dev default | `app/main.py:startup_security_checks()` |
| 2 | Startup checks | Fail hard in production if `DATABASE_URL` does not specify `sslmode` | `app/main.py:startup_security_checks()` |
| 3 | Startup checks | Fail hard in production if `FORCE_HTTPS=false` | `app/main.py:startup_security_checks()` |
| 4 | Transport | App-level HTTP→HTTPS redirect via `HTTPSRedirectMiddleware` | `app/main.py` |
| 5 | Transport | `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload` | `app/main.py:security_headers()` |
| 6 | Transport | Session cookie `Secure` flag tied to `FORCE_HTTPS` | `app/main.py` |
| 7 | XSS | `Content-Security-Policy` (frame-ancestors none, object-src none, form-action self) | `app/main.py:_CSP` |
| 8 | Header model | `security_headers` middleware uses setdefault semantics so routes can override | `app/main.py:_set_if_unset()` |
| 9 | Portal | Token expiration — 90-day sliding idle + 1-year hard expiry | `app/routes/portal.py`, `app/models/payroll.py` |
| 10 | Portal | `Referrer-Policy: no-referrer` + `Cache-Control: no-store` on portal responses | `app/routes/portal.py:_PORTAL_HEADERS` |
| 11 | Portal | Rate limiting on every portal endpoint (30/min GET, 10/min POST) | `app/routes/portal.py` |
| 12 | Encryption | Ciphertext now prefixed with `v1:` to enable clean key rotation | `app/services/encryption.py` |
| 13 | Encryption | Support `PAYROLL_ENCRYPTION_SECRET_PREV` for in-flight rotation | `app/services/encryption.py` |

All 452 tests pass after every change.

---

## Why each change matters

### Startup fail-hard checks

`APP_DEBUG=false` is the production switch. When it's set, three checks run
at app startup and the process exits if any fails:

1. `PAYROLL_ENCRYPTION_SECRET` must not be the well-known dev default. If it
   were, anyone with the source code could decrypt bank PII at rest.
2. `DATABASE_URL` must include `sslmode` (Postgres) or `ssl=` (other
   drivers). Without TLS, payroll data crosses the wire in plaintext.
3. `FORCE_HTTPS` must be true. Without it, the app would happily serve
   over plain HTTP, leaking session cookies and portal tokens.

The reasoning is the same in all three cases: the cheapest moment to fix a
config mistake is before the app accepts its first request.

### App-level HTTPS + HSTS

Production deployments terminate TLS at a reverse proxy, but defense in
depth says the app shouldn't trust the proxy unconditionally. With
`FORCE_HTTPS=true`:

- `HTTPSRedirectMiddleware` 308-redirects plain HTTP to HTTPS at the app.
  Behind a TLS proxy this is a no-op (the proxy already speaks HTTPS); in
  front of a misconfigured proxy it closes the gap.
- HSTS tells browsers to refuse plain HTTP for two years. The
  `includeSubDomains` and `preload` directives are required for HSTS
  preload-list submission.
- The session cookie carries the `Secure` flag, so the browser will refuse
  to send it over an HTTP downgrade.

### Content-Security-Policy

The previous header set blocked clickjacking (`X-Frame-Options: DENY`),
MIME sniffing, and referrer leakage but did nothing against script
injection. CSP fills that gap:

```
default-src 'self';
script-src 'self' 'unsafe-inline' https://js.stripe.com;
style-src 'self' 'unsafe-inline';
img-src 'self' data: blob:;
font-src 'self' data:;
connect-src 'self' https://api.stripe.com;
frame-src https://js.stripe.com https://hooks.stripe.com;
frame-ancestors 'none';
form-action 'self';
base-uri 'self';
object-src 'none'
```

`'unsafe-inline'` is still in `script-src` and `style-src`. Honest accounting:

- **index.html** — zero inline event handlers as of the recent cleanup.
  The 11 `onclick=`/`oninput=` attributes that used to live here moved
  to `app/static/js/bootstrap.js`, which wires them via
  `addEventListener` on DOMContentLoaded. The static shell page would
  work under a stricter CSP today.
- **JS-rendered modals** still use inline `onclick="Foo.bar(x)"` in
  template-literal HTML strings across roughly two dozen `app/static/js/*.js`
  files. Removing `'unsafe-inline'` would require converting each to
  either `addEventListener` after `innerHTML`, or a delegated
  `data-action` dispatcher at the document level. That's a real
  multi-file refactor; tracked in `docs/todo.md`.
- **Inline `style="..."` attributes** show up in those same JS-rendered
  modals, which is why `style-src` also still has `'unsafe-inline'`.

Defense-in-depth gap, not an active vulnerability — `autoescape=True` in
every Jinja2 environment blocks XSS at the template level. The CSP would
catch a hypothetical autoescape bypass; today it can't.

### Portal token expiration

Portal tokens are 192 bits of entropy from `secrets.token_urlsafe(24)` —
unguessable. But a URL containing one can leak via browser history, server
access logs, or shared bookmarks, so we need time-based mitigation:

- **Hard expiry**: 1 year from issuance. Stored in
  `Employee.portal_token_expires_at`.
- **Idle expiry**: 90 days of inactivity. Every authenticated request
  rolls `Employee.portal_token_last_used` forward, so an actively used
  token never expires before the 1-year wall.

Expired tokens return `410 Gone` (rather than 404) so the SPA can
distinguish "wrong link" from "your link aged out." Admins can mint a
fresh token with `POST /api/employees/{id}/portal-token`.

The `_to_utc()` helper normalizes naive timestamps (SQLite) and aware
ones (Postgres) so the comparison works in both environments.

### Portal no-referrer

`Referrer-Policy: no-referrer` and `Cache-Control: no-store` ship on
every portal HTML response and redirect. The first prevents the URL —
which contains the token — from leaking to other origins via the
`Referer` header. The second keeps the page out of intermediate caches.

The app-level `security_headers` middleware uses setdefault semantics, so
the portal can override the default `strict-origin-when-cross-origin`
without the middleware clobbering it.

### Encryption key versioning

Previously, rotating `PAYROLL_ENCRYPTION_SECRET` would have required
decrypting every stored ciphertext with the old key, then re-encrypting
with the new — a destructive, downtime-prone migration. Now:

1. Move the live secret into `PAYROLL_ENCRYPTION_SECRET_PREV`.
2. Set the new secret as `PAYROLL_ENCRYPTION_SECRET`.
3. Bounce the app. New writes use the new key; old reads transparently
   fall through to the previous key.
4. Run a re-wrap migration when convenient (no rush — both keys work).
5. Drop `PAYROLL_ENCRYPTION_SECRET_PREV` once everything is rewrapped.

Ciphertext is now prefixed with `v1:` so older unversioned blobs still
decrypt cleanly. Decrypt tries the current key first, then any
configured previous key.

---

## OWASP Top 10 coverage

| Risk | Mitigation |
|------|-----------|
| A01 — Broken Access Control | Single-user session auth, portal token expiry, rate limiting |
| A02 — Cryptographic Failures | TLS enforced (transport) + Fernet at rest + key rotation path |
| A03 — Injection | SQLAlchemy ORM throughout; one raw `CREATE DATABASE` guarded by allowlist regex; AST-audited subprocess callsites (zero `shell=True`) |
| A04 — Insecure Design | Startup checks fail hard on critical misconfig |
| A05 — Security Misconfiguration | CSP, HSTS, security headers, secure cookie, locked-down CORS |
| A06 — Vulnerable Components | Pinned versions, upper-bound caps in `requirements.txt` |
| A07 — Identification & Authentication Failures | Argon2id passwords, 5/min rate-limited login, 192-bit portal tokens |
| A08 — Software & Data Integrity | Fernet ciphertext is authenticated (AES + HMAC); session cookie signed |
| A09 — Logging & Monitoring | No PII (passwords, SSN, account #) in logs; audit hooks on row writes |
| A10 — SSRF | AI provider URLs validated against private IPs and metadata endpoints |

---

## Production deployment checklist

Before exposing the app to anything other than localhost:

```bash
# Required env vars (process exits at startup if any are wrong)
export APP_DEBUG=false
export PAYROLL_ENCRYPTION_SECRET=$(openssl rand -base64 32)
export DATABASE_URL="postgresql://user:pass@host:5432/db?sslmode=require"
export FORCE_HTTPS=true                  # default in production, explicit is better
export CORS_ALLOW_ORIGINS="https://yourdomain.com"
export SESSION_SECRET_KEY=$(openssl rand -base64 48)  # else autogenerated to a file

# Recommended
export RATE_LIMIT_ENABLED=1              # default on; set to 0 only for load tests
export HSTS_MAX_AGE=63072000             # 2 years, the preload-list minimum
```

Then run behind nginx or Traefik with a real TLS cert. The app's own
`HTTPSRedirectMiddleware` then becomes a no-op because the proxy already
speaks HTTPS to it.

---

## Frontend ↔ Backend wiring audit

After the security pass we ran a spider-web audit of every `API.*` call
against every `@router` decorator to catch disconnects. See
[wiring-audit.md](wiring-audit.md) for the methodology and findings —
four real breakages were found and fixed.

---

## Test coverage

Security-relevant tests live in `tests/test_tier3.py`:

```
test_portal_token_hard_expiry_blocks_access
test_portal_token_idle_expiry_blocks_access
test_portal_last_used_rolls_forward_on_access
test_portal_responses_send_no_referrer_header
test_portal_token_mint_sets_expiry
test_security_headers_present
test_encryption_roundtrip_with_version_prefix
test_encryption_decrypts_legacy_unprefixed_ciphertext
test_encryption_returns_none_for_garbage
test_portal_bank_account_encryption          (Fernet at rest)
test_portal_invalid_bank_account_routing     (input validation)
test_portal_dashboard_requires_valid_token   (auth)
test_portal_w4_update                        (token-gated mutation)
```

Run the full suite:

```bash
python -m pytest tests/ -q
# 452 passed
```

### Shell-injection surface audit

`tests/test_subprocess_safety_audit.py` is a CI-gated AST-based regression
test that verifies:

1. Zero `subprocess.*` calls use `shell=True` anywhere under `app/` or
   `scripts/`.
2. Zero `os.system` / `os.popen` / `commands.getoutput` calls anywhere in
   production code.
3. Every bash script in `scripts/` double-quotes all `$VAR` expansions
   (regex audit).

The test walks the AST of every `.py` file using `ast.NodeVisitor`; the
shell-script scan uses a regex that flags bare `$` outside
double-quotes. Both run in `< 1 s`. Adding any `shell=True` or
`os.system` call will break CI immediately.
