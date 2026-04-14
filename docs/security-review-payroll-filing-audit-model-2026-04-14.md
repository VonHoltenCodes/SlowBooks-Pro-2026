# Security Review — Payroll Filing Audit Model (2026-04-14)

## Scope Reviewed
- `app/models/payroll_filing.py`
- `app/services/payroll_filing_audit.py`
- `app/routes/employees.py`
- `app/routes/payroll.py`
- `app/static/js/employees.js`
- `app/static/js/payroll.js`
- `alembic/versions/g7b8c9d0e1f2_add_payroll_filing_audits.py`

## Review Focus
- Whether filing history now persists sensitive payroll/export state safely
- Whether changed-since-filing detection relies on controlled snapshots rather than arbitrary code execution or unsafe serialization
- Whether filing history/status operations stay behind existing payroll RBAC permissions

## Findings
1. **Payroll filing is now auditable instead of stateless**
   - Starter/leaver and Employment Information exports now create filing-audit records with lifecycle status and source hashes.
   - This reduces the prior risk of losing filing provenance when underlying employee/pay-run data later changes.

2. **Snapshot comparison is deterministic and local**
   - Filing snapshots are serialized to JSON text and hashed with SHA-256; no external calls or unsafe dynamic evaluation are introduced.
   - The comparison uses filing-relevant payroll/settings fields only.

3. **History and status updates stay inside the existing payroll/employee permission model**
   - Employee filing history/status uses employee filing/view permissions; pay-run filing history/status uses payroll filing/view permissions.
   - This keeps the new audit data aligned with the RBAC foundation rather than adding another trust boundary.

## Residual Risks
- The slice still does not submit filings directly to IRD; lifecycle states are internal app tracking only.
- Broader RBAC rollout beyond the currently protected domains remains future work.
- Filing snapshots include sensitive payroll-related data, so the repo's remaining trust-boundary rollout still matters.

## Conclusion
- No new CRITICAL/HIGH issues identified in this slice.
- Residual risk is **LOW to MEDIUM** and primarily tied to broader platform access boundaries rather than the filing-audit implementation itself.
