# HIPAA Compliance Assessment

**Status:** SlowBooks Pro 2026 is **not a HIPAA-covered system by default.**
It's accounting and payroll software, not a healthcare information system.
This document is an honest accounting of where its controls already align
with the HIPAA Security Rule and where the gaps are — so a business that
needs HIPAA-style assurances can make an informed decision.

---

## 1. Does HIPAA apply to SlowBooks?

HIPAA applies to **Covered Entities** (healthcare providers, health plans,
healthcare clearinghouses) and **Business Associates** that handle
**Protected Health Information** (PHI) on their behalf. PHI is anything
that identifies an individual *and* relates to their physical or mental
health, healthcare provision, or payment for healthcare.

**What SlowBooks stores that is potentially HIPAA-adjacent:**

| Data | HIPAA classification | Notes |
|------|----------------------|-------|
| Employee name + address | Not PHI by itself | Becomes PHI only when combined with healthcare information |
| Employee SSN (last 4 on the record; the full number on an uploaded W-4 or I-9) | Not PHI | An identifier, but no health data attached |
| Employee bank routing / account # | Not PHI | Financial data, encrypted at rest |
| Pay-stub gross / withholding | Not PHI | Wage data |
| HSA deduction *amount* | Not PHI | Reveals only that an HSA exists, not health details |
| Health insurance enrollment (a benefit code such as "Section 125 Health Premium", its rate, the carrier it is remitted to) | Borderline | The benefits engine stores the enrollment and the carrier as a vendor; no plan, claim or diagnosis details |
| Pre-tax FSA / dependent-care amount | Not PHI | Same |
| Actual claims, diagnoses, treatment | Would be PHI | **We don't store any of this** |

**Default verdict:** A business running SlowBooks for normal accounting +
payroll is not a HIPAA-regulated workflow. Customer becomes a Business
Associate only if they explicitly use the app to handle PHI on behalf of
a Covered Entity, which isn't the design intent.

---

## 2. HIPAA Security Rule mapping

For businesses that want HIPAA-aligned controls regardless of strict
applicability, here's where each Security Rule technical safeguard maps
to existing SlowBooks behavior.

### § 164.312(a)(1) — Access Control

| Spec | Implementation |
|------|---------------|
| Unique user identification (R) | ✅ Named users in four roles (administrator, bookkeeper, accountant, read-only), enforced centrally for every route; API tokens are their own principals (`token:<label>` in the audit log), never administrators (since 2.29.0; an older administrator token is refused since 2.31.2). Access is by route group, not per field |
| Emergency access procedure (R) | Manual — any administrator's sign-in, the recovery key of an encrypted company file, and the backup files |
| Automatic logoff (A) | ✅ `SESSION_IDLE_TIMEOUT_SECONDS` (default 14400s / 4h) clears sessions |
| Encryption + decryption (A) | ✅ Fernet for bank PII at rest; AES-128-CBC + HMAC-SHA256 |

### § 164.312(b) — Audit Controls

> "Implement hardware, software, and/or procedural mechanisms that record
> and examine activity in information systems that contain or use ePHI."

| Mechanism | Implementation |
|-----------|---------------|
| Login attempts | ✅ `LoginAttempt` table — success / failure with IP + UA |
| Database row writes | ✅ `register_audit_hooks(SessionLocal)` in `app/main.py` writes an `audit_log` row for every create / update / delete via SQLAlchemy event hooks |
| Document tampering | ✅ `DocumentAudit` table + SHA-256 content hash printed in tax-form PDF footers |
| Portal token usage | ✅ `Employee.portal_token_last_used` rolls forward on every authenticated portal request |

### § 164.312(c)(1) — Integrity

> "Implement policies and procedures to protect ePHI from improper
> alteration or destruction."

| Spec | Implementation |
|------|---------------|
| Authentication of ePHI (A) | ✅ Fernet ciphertext carries HMAC; tampered ciphertext fails to decrypt and returns None |
| Document integrity | ✅ SHA-256 hash + audit ID printed in tax-form PDFs; auditor can re-verify by regenerating |

### § 164.312(d) — Person or Entity Authentication

| Spec | Implementation |
|------|---------------|
| Verify identity before access | ✅ Argon2id password (default cost ~100ms/verify); 5/min rate-limited login |
| Session integrity | ✅ Starlette signed cookie + session rotation on login |
| Portal authentication | ✅ 192-bit `secrets.token_urlsafe(24)` token; 90-day idle + 1-year hard expiry |

### § 164.312(e)(1) — Transmission Security

| Spec | Implementation |
|------|---------------|
| Integrity controls (A) | ✅ On a production server behind a TLS proxy (`docker-compose.prod.yml`): HSTS `max-age=63072000; includeSubDomains; preload`. ⚠ The desktop app, Server Edition on a LAN and the single-host `docker compose up` serve plain HTTP by design (`APP_DEBUG=true` / `SLOWBOOKS_PRIVATE_NETWORK=1`), with no HSTS |
| Encryption (A) | ✅ `FORCE_HTTPS=true` (default in production) + `HTTPSRedirectMiddleware`; session cookie carries `Secure` flag. ⚠ Not on the plain-HTTP deployments above: a HIPAA-conscious deployment is the production image behind HTTPS ([cloud-hosting.md](cloud-hosting.md)) |
| Database transport | ✅ Startup fails hard in production if `DATABASE_URL` lacks `sslmode=require` (skipped by `SLOWBOOKS_PRIVATE_NETWORK=1`, where the database is on the host's own Docker network) |

### § 164.312(a)(2)(iv) — Encryption at Rest

| Field | Status |
|-------|--------|
| Bank routing # | ✅ Fernet-encrypted |
| Bank account # | ✅ Fernet-encrypted; only last-4 plaintext |
| AI provider API keys | ✅ Fernet-encrypted via `app/services/crypto.py` |
| Password hashes | ✅ Argon2id (one-way) |
| Company file (SQLite) | ✅ Optional whole-file SQLCipher encryption (#290), off by default; on a desktop the key is the practice's own (a recovery key shown once, unlocked by an administrator's sign-in), on a server the operator's secret; backups are ciphertext; PostgreSQL is the host's job |
| Employee SSN | ⚠ Only the last 4 digits on the record; an uploaded W-4 or I-9 (stored inside the company file) carries the full number, protected only by whole-file encryption |
| Employee name / address | ⚠ Plaintext in the database (not PHI in HIPAA terms); encrypted on disk when whole-file encryption is on |
| HSA / health-insurance deduction amounts | ⚠ Plaintext in the database (not PHI in HIPAA terms); same |

---

## 3. Administrative + Physical Safeguards

These are deployment-time controls, not code controls. SlowBooks can be
configured to support them but doesn't enforce them on its own.

| Safeguard | Notes |
|-----------|-------|
| § 164.308 Security Management Process | Customer responsibility — risk analysis, sanctions policy, etc. |
| § 164.308 Workforce Security | Customer responsibility — background checks, termination procedures |
| § 164.308 Information Access Management | Four roles with central enforcement, sign-in end dates, one-time passwords for new sign-ins, sessions ended on deactivation, role change or password reset; the access policy itself is the customer's |
| § 164.308 Security Awareness and Training | Customer responsibility |
| § 164.308 Security Incident Procedures | Customer responsibility; SlowBooks logs help with detection |
| § 164.308 Contingency Plan | ✅ Backups and restore from Settings → Backup / Restore: a `pg_dump` on PostgreSQL, a copy of the company file on the desktop and Server Edition (encrypted when the file is) |
| § 164.308 Evaluation | Periodic re-assessment — customer responsibility |
| § 164.308 Business Associate Contracts | Customer responsibility — if SlowBooks operator becomes a BA |
| § 164.310 Facility Access Controls | Physical deploy environment (datacenter / office) — customer responsibility |
| § 164.310 Workstation Use / Security | Customer responsibility |
| § 164.310 Device and Media Controls | Customer responsibility |

---

## 4. Gaps if you wanted to run SlowBooks in a HIPAA context

If a customer chose to store ePHI in SlowBooks (e.g. health-insurance
enrollment details beyond the deduction amount), here's what would need
to change to fully align with the Security Rule:

| Gap | Severity | Fix |
|-----|----------|-----|
| **Access control is by route group, not per field** | Medium | Four roles decide which screens and operations a person reaches; HIPAA's "minimum necessary" may want finer cuts (one person sees pay, another only time). Would require per-field access checks |
| **Employee data not encrypted per field** | Medium | Names, addresses, hire date, etc. are plaintext in the database; whole-file encryption (SQLCipher) covers them on disk, and a PostgreSQL server relies on the host's disk encryption. If treated as PHI, would need Fernet wrapping (same scheme as bank fields) |
| **No data-retention enforcement** | Medium | Pay stubs and employee records stay forever. HIPAA expects retention/destruction policies — would need a configurable retention period + automated purge |
| **No breach-notification flow** | Medium | If the audit log detects tampering or unauthorized access, there's no automated alerting. Would need email/webhook integration |
| **No employee data-export endpoint** | Low | Right-to-access — the portal lets an employee see their own data, but there's no "give me everything you have on me as a JSON export" feature |
| **No Business Associate Agreement (BAA) template** | Low | Documentation gap. Vendor would need to provide a signable BAA |
| **No FIPS 140-2 attestation** | Low | Python's `cryptography` library uses OpenSSL underneath; FIPS mode depends on the OpenSSL build the customer's OS ships. SlowBooks doesn't enforce a FIPS-validated build |
| **Encryption-at-rest key escrow** | Low | `PAYROLL_ENCRYPTION_SECRET` is operator-managed. HIPAA HSM / KMS integration would be a customer-driven enhancement |

---

## 5. What we already do that exceeds typical small-business norms

These aren't strictly HIPAA-required but are good signals for any
compliance regime (HIPAA, SOC 2, PCI-DSS):

- **Versioned ciphertext** (`v1:` prefix) — supports zero-downtime key rotation
- **Login + document audit trails**, every tax-form PDF with its own SHA-256 content hash
- **Idle session timeout** + automatic logoff
- **Startup fail-hard checks** on production misconfig (an encryption secret that is the dev default, empty or short; DB TLS; FORCE_HTTPS)
- **Whole-file encryption** of a company file (SQLCipher) with a recovery key shown once and per-administrator unlocking
- **"Ask for the password each time SlowBooks Pro starts"**, revocable sessions, sign-in end dates and one-time passwords for new sign-ins
- **Rate-limited login** + Argon2id password hashing (~100ms cost)
- **Content-Security-Policy** + HSTS + strict CORS
- **Atomic secret file writes** (`mkstemp` + `os.replace`)
- **CSV formula-injection protection** in exports
- **No hardcoded secrets** anywhere in the codebase
- **SSRF protection** on AI provider URL configuration

---

## 6. Recommendations for HIPAA-conscious deployments

If a customer is running SlowBooks for a small healthcare-adjacent business
(e.g. a physical therapy practice's accounting, where they want HIPAA-style
care even though only payroll/financial data is in scope):

1. **Set strong production env vars** — see [security-hardening.md](security-hardening.md) deployment checklist
2. **Run behind a TLS-terminating reverse proxy** with a valid cert (Let's Encrypt is fine)
3. **Restrict database access** — `pg_hba.conf` to only the app host, `sslmode=verify-full` with cert pinning
4. **Configure encrypted backups** — `pg_dump` output should be GPG-encrypted at rest if it leaves the server
5. **Set up offsite backup encryption keys** separately from the database — never co-locate the encryption secret with the data
6. **Enable system audit logging** at the OS level (auditd / journald) — captures process-level events SlowBooks doesn't see
7. **Rotate `PAYROLL_ENCRYPTION_SECRET` annually** — use the `PAYROLL_ENCRYPTION_SECRET_PREV` flow for zero-downtime rotation
8. **Review the Audit Log page and the `login_attempts` table weekly** — even a quick "show me failed logins in the last 7 days" run catches slow probes (`login_attempts` has no page of its own; query it in the database)

---

## 7. Honesty notes

- This document was written by an engineer doing a code audit, not by a HIPAA compliance officer. If HIPAA is contractually required for your deployment, retain a qualified compliance professional to do a real risk assessment.
- "Aligned with" HIPAA's technical safeguards is not the same as "HIPAA-certified." The OCR doesn't certify software products; certification, if any, applies to the deployed system as operated by the Covered Entity or Business Associate.
- The dependency scan (`pip-audit`) runs in CI on every change; an open advisory with no upstream fix would be listed in the release notes.

---

**Last updated:** 2026-10-31, for v2.31.3.
