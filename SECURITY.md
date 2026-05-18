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

- **Single-user session authentication** (Phase 9.7) — password-protected access with bcrypt hashing, session cookies (`strict` SameSite, 30-day TTL)
- **Security headers** on all responses — X-Content-Type-Options: nosniff, X-Frame-Options: DENY, strict Referrer-Policy, restricted Permissions-Policy
- **CORS lockdown** — explicit origin allowlist (no wildcards), configurable via `CORS_ALLOW_ORIGINS`
- **Rate limiting** — configurable via slowapi to prevent brute-force attacks
- **Path traversal protection** — backup and attachment endpoints validated with `Path.is_relative_to()`
- **Sensitive key filtering** — password hashes and session secrets never returned from the settings API
- **Atomic secret file writes** — session key and encryption master key use `mkstemp` + `os.replace()` to prevent race conditions
- **Fernet encryption** — AI provider API keys encrypted at rest (AES-128-CBC + HMAC-SHA256)
- **SSRF protection** — AI provider URLs validated against private IP ranges, localhost, link-local, and metadata endpoints
- **Non-root Docker** — container runs as UID 1000, not root
- **Pinned dependencies** — all requirements.txt entries have upper-bound version caps
- **MIME and extension validation** — file uploads checked against allowlists
- **CSV formula injection protection** — exported CSVs neutralize `=`, `+`, `-`, `@` cell prefixes
- **Constant-time auth** — Cloudflare Worker gateway uses byte-wise constant-time comparison

## Known considerations

- Slowbooks is designed to run on a **local network or single machine** — session auth provides single-user protection but is not a substitute for network-level security on the public internet. Consider a reverse proxy with TLS for external access
- OAuth tokens (Stripe, QBO) are stored in the database — secure your database access accordingly
- SMTP passwords are stored in the settings table — same as above
- The session secret is auto-generated to `.slowbooks-session.key` on first run — do not commit this file
