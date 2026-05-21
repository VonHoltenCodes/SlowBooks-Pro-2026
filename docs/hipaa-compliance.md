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
| Employee SSN (last 4) | Not PHI | An identifier, but no health data attached |
| Employee bank routing / account # | Not PHI | Financial data, encrypted at rest |
| Pay-stub gross / withholding | Not PHI | Wage data |
| HSA deduction *amount* | Not PHI | Reveals only that an HSA exists, not health details |
| Health insurance deduction *amount* | Not PHI | Same — premium amount without enrollment/claim details |
| Pre-tax FSA / dependent-care amount | Not PHI | Same |
| Insurance carrier name (if stored) | Borderline | Could imply health context; we don't currently store this |
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
| Unique user identification (R) | Single-operator design; no multi-tenant users yet |
| Emergency access procedure (R) | Manual — operator has the master password + backup files |
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
| Integrity controls (A) | ✅ HTTPS enforced; HSTS `max-age=63072000; includeSubDomains; preload` |
| Encryption (A) | ✅ `FORCE_HTTPS=true` (default in production) + `HTTPSRedirectMiddleware`; session cookie carries `Secure` flag |
| Database transport | ✅ Startup fails hard in production if `DATABASE_URL` lacks `sslmode=require` |

### § 164.312(a)(2)(iv) — Encryption at Rest

| Field | Status |
|-------|--------|
| Bank routing # | ✅ Fernet-encrypted |
| Bank account # | ✅ Fernet-encrypted; only last-4 plaintext |
| AI provider API keys | ✅ Fernet-encrypted via `app/services/crypto.py` |
| Password hashes | ✅ Argon2id (one-way) |
| Employee SSN | ⚠ Only last 4 digits stored — full SSN never collected |
| Employee name / address | ⚠ Plaintext (not PHI in HIPAA terms) |
| HSA / health-insurance deduction amounts | ⚠ Plaintext (not PHI in HIPAA terms) |

---

## 3. Administrative + Physical Safeguards

These are deployment-time controls, not code controls. SlowBooks can be
configured to support them but doesn't enforce them on its own.

| Safeguard | Notes |
|-----------|-------|
| § 164.308 Security Management Process | Customer responsibility — risk analysis, sanctions policy, etc. |
| § 164.308 Workforce Security | Customer responsibility — background checks, termination procedures |
| § 164.308 Information Access Management | SlowBooks is single-operator; multi-user RBAC is not implemented |
| § 164.308 Security Awareness and Training | Customer responsibility |
| § 164.308 Security Incident Procedures | Customer responsibility; SlowBooks logs help with detection |
| § 164.308 Contingency Plan | ✅ `pg_dump` backup via `app/services/backup_service.py` + restore flow |
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
| **No role-based access control** | High | SlowBooks is single-operator. HIPAA expects "minimum necessary" — different staff see different data. Would require a user model + role assignment + per-field access checks |
| **Employee data not encrypted at rest** | Medium | Names, addresses, hire date, etc. are plaintext. If treated as PHI, would need Fernet wrapping (same scheme as bank fields) |
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
- **Login + document audit trails** with content-hash chaining
- **Idle session timeout** + automatic logoff
- **Startup fail-hard checks** on production misconfig (encryption secret, DB TLS, FORCE_HTTPS)
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
8. **Review `login_attempts` and `audit_log` tables weekly** — even a quick "show me failed logins in the last 7 days" run catches slow probes

---

## 7. Honesty notes

- This document was written by an engineer doing a code audit, not by a HIPAA compliance officer. If HIPAA is contractually required for your deployment, retain a qualified compliance professional to do a real risk assessment.
- "Aligned with" HIPAA's technical safeguards is not the same as "HIPAA-certified." The OCR doesn't certify software products; certification, if any, applies to the deployed system as operated by the Covered Entity or Business Associate.
- The pyjwt PYSEC-2025-183 advisory (no upstream fix) is currently tracked in `docs/todo.md` — note it in any formal risk assessment until a patched release lands.

---

**Last updated:** 2026-05-21 — alongside the tax-form audit-hash work.
