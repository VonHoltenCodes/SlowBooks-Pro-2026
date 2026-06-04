# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest (main branch) | Yes |

## Reporting a Vulnerability

If you discover a security vulnerability in Slowbooks Pro, please report it responsibly:

1. **Do NOT open a public issue** for security vulnerabilities
2. Use GitHub's [private vulnerability reporting](https://github.com/VonHoltenCodes/SlowBooks-Pro-2026/security/advisories/new) to submit a report
3. Or email **trentonvonholten@gmail.com** with details

### What to include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response timeline

- **Acknowledgment**: Within 48 hours
- **Assessment**: Within 1 week
- **Fix**: As soon as practical, depending on severity

## Scope

This policy applies to the Slowbooks Pro 2026 codebase. Security issues in third-party dependencies should be reported to those projects directly (though we appreciate a heads-up).

## Security measures in place

**Authentication & sessions**
- **Single-user session authentication** — Argon2id password hash, session cookie with `same_site=strict`, 30-day TTL
- **`Secure` cookie flag** — set automatically when `FORCE_HTTPS=true` (default in production)
- **Login rate limit** — 5 attempts per minute per IP via slowapi
- **Atomic secret file writes** — session key uses `mkstemp` + `os.replace()` to prevent race conditions

**Transport security**
- **App-level HTTPS redirect** — `HTTPSRedirectMiddleware` is added when `FORCE_HTTPS=true`; behind a TLS proxy this is a no-op
- **HSTS** — `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload` (2-year preload-list minimum)
- **Database TLS** — startup fails hard in production if `DATABASE_URL` does not include `sslmode=require` (or stronger)

**Response headers**
- `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`
- **Content-Security-Policy** — default-src self, frame-ancestors none, object-src none, form-action self, base-uri self
- Routes can opt into stricter values (e.g. portal pages use `Referrer-Policy: no-referrer`)

**Data at rest**
- **Field-level encryption** — bank routing/account numbers encrypted with Fernet (AES-128-CBC + HMAC-SHA256), key derived via PBKDF2-SHA256 at 480k iterations
- **Versioned ciphertext** — `v1:` prefix lets us rotate `PAYROLL_ENCRYPTION_SECRET` without downtime (set `PAYROLL_ENCRYPTION_SECRET_PREV` during transition)
- **Encrypted API keys** — AI provider keys (OpenAI, Anthropic) encrypted with the same Fernet scheme
- **Sensitive key filtering** — password hashes and session secrets never returned from the settings API

**Self-service portal**
- **192-bit tokens** — `secrets.token_urlsafe(24)`, unique-constrained
- **Token expiration** — 1-year hard expiry + 90-day idle sliding window
- **Rate limiting** — 30/min GET, 10/min POST per endpoint
- **No-referrer + no-store** — portal pages do not leak the URL token via `Referer` or shared caches

**Input validation & injection**
- **SQLAlchemy ORM throughout** — no user input concatenated into SQL
- One raw `CREATE DATABASE` is guarded by an allowlist regex (alphanumeric + underscore/hyphen only)
- **MIME and extension validation** — file uploads checked against allowlists
- **Path traversal protection** — backup and attachment endpoints validated with `Path.is_relative_to()`
- **CSV formula injection protection** — exported CSVs neutralize `=`, `+`, `-`, `@` cell prefixes

**Other**
- **CORS lockdown** — explicit origin allowlist (no wildcards), defaults to localhost
- **SSRF protection** — AI provider URLs validated against private IP ranges, localhost, link-local, and metadata endpoints
- **Non-root Docker** — container runs as UID 1000, not root
- **Pinned dependencies** — all `requirements.txt` entries have upper-bound version caps
- **Constant-time auth** — Cloudflare Worker gateway uses byte-wise constant-time comparison

For the engineering log of the production hardening pass (with file pointers, OWASP coverage, and the production deployment checklist), see [`docs/security-hardening.md`](docs/security-hardening.md).

## Known considerations

- Slowbooks is designed to run on a **local network or single machine** — session auth provides single-user protection but is not a substitute for network-level security on the public internet. Run behind a TLS-terminating reverse proxy for external access
- OAuth tokens (Stripe, QBO) are stored in the database — secure your database access accordingly
- SMTP passwords are stored in the settings table — same as above
- The session secret is auto-generated to `.slowbooks-session.key` on first run — do not commit this file
- Portal tokens appear in URLs; the `no-referrer` header + token expiration mitigate the main leak vectors, but treat the token URL as you would a password reset link
