# Release / Production Deployment Checklist

Before exposing SlowBooks Pro 2026 to anyone other than localhost, walk
this list. Everything here is enforced or documented somewhere in the
codebase — this file is the index, not the source of truth.

## 1. Secrets — generate fresh, never commit

```bash
export PAYROLL_ENCRYPTION_SECRET=$(openssl rand -base64 32)
export SESSION_SECRET_KEY=$(openssl rand -hex 32)
export POSTGRES_PASSWORD=$(openssl rand -hex 24)
```

- `PAYROLL_ENCRYPTION_SECRET` — symmetric key for bank PII. Losing it means
  losing access to every encrypted field. Back it up somewhere separate
  from the database.
- `SESSION_SECRET_KEY` — cookie signer. Losing it just invalidates every
  current session; not catastrophic, but inconvenient.
- The setup wizard collects the operator password on first boot. Argon2id
  hashes it; you never need to set it via env var.

## 2. Database

- Postgres 16+ (we tested against 17). SQLite is supported but only for
  dev / tests.
- `DATABASE_URL` MUST include `sslmode=require` or `sslmode=verify-full`.
  The app refuses to start in production without it.
- Apply migrations once: `alembic upgrade head` (or rely on
  `Base.metadata.create_all()` on first boot).
- Take a baseline backup before opening to users.

## 3. Required environment

Set with real values, not the dev defaults in `.env.example`:

```bash
APP_DEBUG=false
FORCE_HTTPS=true
DATABASE_URL=postgresql://user:pass@host:5432/db?sslmode=require
PAYROLL_ENCRYPTION_SECRET=<from step 1>
SESSION_SECRET_KEY=<from step 1>
CORS_ALLOW_ORIGINS=https://books.your-domain.com
```

Recommended:

```bash
SESSION_IDLE_TIMEOUT_SECONDS=14400      # 4-hour idle cap
HSTS_MAX_AGE=63072000                   # 2-year HSTS preload minimum
RATE_LIMIT_ENABLED=1                    # default; only off for load tests
EMPLOYER_EIN=12-3456789                 # required for tax forms
EMPLOYER_STATE=WA
SUTA_RATE=0.012                         # your state's experience rate
```

## 4. TLS termination

The app emits HSTS and uses `Secure` cookies whenever `FORCE_HTTPS=true`,
but it doesn't terminate TLS itself. Pick one of:

- **Caddy** — easiest, two-line Caddyfile, automatic Let's Encrypt
- **nginx + certbot** — most common, ops teams already know it
- **Traefik** — Docker-native, sits in the same compose file
- Cloud LB / ALB with an ACM cert (AWS / GCP / Azure)

Copy-paste configs for each in [tls-proxy-setup.md](tls-proxy-setup.md).

Verify:
- `https://your-domain/health` returns 200
- `http://your-domain/health` 308-redirects to https
- The cert chain is complete (e.g. `ssllabs.com/ssltest` rates it A+)

**Optional: submit to the HSTS preload list.** After your domain has
been serving HTTPS-only with a valid HSTS header for a week or so,
submit it at https://hstspreload.org/. Once accepted, every modern
browser will refuse plain HTTP to your domain even on first visit.
The HSTS header is already preload-compatible (`max-age=63072000;
includeSubDomains; preload`) — you just need to register.

## 5. Backups

`scripts/backup.sh` shells out to `pg_dump`. Schedule it:

```cron
0 2 * * *  /opt/slowbooks/scripts/backup.sh
```

Encrypt the dumps before they leave the host (`gpg --encrypt --recipient ...`)
and store them somewhere separate from the database server. Test restore
quarterly — a backup you've never restored from isn't a backup.

## 6. Monitoring + audit

Watch these tables (or wire them into your SIEM):

- `login_attempts` — failed logins, especially repeated from one IP.
  Rate limiting catches fast attackers; this catches slow ones.
- `audit_log` — every database write goes here via SQLAlchemy event hooks.
- `document_audits` — tax-form generation history with content hashes.
  Operator can verify any PDF against this table.

Cheap nightly audit query:

```sql
SELECT count(*) FROM login_attempts
WHERE success = false AND created_at > now() - interval '24 hours';
```

## 7. Updates

```bash
# Quarterly
pip-audit -r requirements.txt
# Bump anything flagged, run the test suite, redeploy
```

The branch has been kept CVE-clean as of the last release commit.

## 8. Tax-form caveats

Despite the audit-hash footer, the W-2 / W-3 / 940 / 941 PDFs are NOT
pixel-exact replicas of the IRS-published forms. Operators must verify
the numbers against the official IRS instructions before filing. The
"not an official IRS form" disclaimer is in every footer for this
reason.

For SUI filings the scaffolding exists (`app/services/tax_forms/state_sui.py`)
but per-state form rendering is not implemented. Don't rely on SlowBooks
for state unemployment filings without confirming the state accepts the
output format.

## 9. HIPAA / compliance context

SlowBooks is not a HIPAA-covered system by default. See
[hipaa-compliance.md](hipaa-compliance.md) for the Security Rule mapping,
the eight remaining gaps, and recommendations for compliance-conscious
deployments.

## 10. Pre-flight test

Once the env is set:

```bash
# Boot should print no warnings
docker compose -f docker-compose.prod.yml up

# Healthcheck
curl -fsSL https://your-domain/health

# Setup wizard (only callable until a password exists)
curl https://your-domain/api/auth/status

# Login + protected endpoint
curl -c /tmp/jar -X POST https://your-domain/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"password":"<your-operator-password>"}'
curl -b /tmp/jar https://your-domain/api/employees
```

If any of those returns 5xx or 401-when-expecting-200, do not open to
users until you've diagnosed.

## 11. After launch

- Watch `audit_log` and `login_attempts` for the first 48 hours.
- Confirm the daily backup ran and the file is readable + decryptable.
- Rotate `PAYROLL_ENCRYPTION_SECRET` annually (no downtime — see
  [security-hardening.md](security-hardening.md) for the procedure).
- Re-run `pip-audit` monthly.
