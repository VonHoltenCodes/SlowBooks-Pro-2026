# Operations Runbook

Day-2 operational tasks: backups, restores, key rotation, monitoring.
For first-time install see [INSTALL.md](../INSTALL.md); for the full
production-launch checklist see
[docs/release-checklist.md](release-checklist.md).

---

## Backups

### Quick local backup

```bash
./scripts/backup.sh
```

- Output: `~/bookkeeper-backups/bookkeeper-YYYY-MM-DD-HHMM.sql.gz`
- Compression: gzip
- Retention: keeps the 30 most recent dumps; older ones are pruned
  automatically.

### Scheduled production backup

```cron
0 2 * * *  /opt/slowbooks/scripts/backup.sh
```

**Always encrypt** dumps before they leave the host:

```bash
gpg --encrypt --recipient your-key@example.com bookkeeper-2026-05-23.sql.gz
```

Store the encrypted copy somewhere physically separate from the
database server (S3, Backblaze B2, an offsite NAS — anywhere that isn't
the same machine). A backup you've never restored from isn't a backup;
test restore quarterly.

### Docker backups

When running under `docker compose`, backups land in a Docker volume
inside the container. To copy them out to your host:

```bash
docker compose cp slowbooks:/app/backups ./my-backups
```

To take a one-off backup from a running container:

```bash
docker compose exec slowbooks ./scripts/backup.sh
```

### Restore

Native:

```bash
gunzip -c bookkeeper-2026-05-23.sql.gz | psql -U bookkeeper bookkeeper
```

Docker:

```bash
docker compose exec -T postgres psql -U bookkeeper bookkeeper \
    < <(gunzip -c bookkeeper-2026-05-23.sql.gz)
```

`pg_dump` not found?
- Docker: included automatically; nothing to do.
- Native Linux: `sudo apt install postgresql-client`
- Native macOS: `brew install postgresql@17`

---

## Encryption key rotation

Bank PII (routing + account numbers) is Fernet-encrypted with a
versioned ciphertext prefix (`v1:`), supporting zero-downtime
rotation. The new key reads existing ciphertexts via the PREV
fallback while you rewrap.

### One-time rotation

```bash
# 1. Generate the new master key
python -c "import secrets; print(secrets.token_urlsafe(48))"

# 2. Set in env (do NOT remove the old key yet)
export PAYROLL_ENCRYPTION_SECRET="<new key>"
export PAYROLL_ENCRYPTION_SECRET_PREV="<old key>"

# 3. Restart the app — all reads work (new + prev tried in order),
#    all writes use the new key.

# 4. Rewrap existing rows under the new key (idempotent, safe to
#    re-run, supports --dry-run):
python -m app.services.encryption rewrap --dry-run
python -m app.services.encryption rewrap

# 5. Confirm nothing left on PREV, then unset PREV.
```

Master key files (`.slowbooks-master.key`, `.slowbooks-session.key`)
are excluded in `.gitignore` — never commit them. Losing the master
key means losing every encrypted secret in the database.

---

## Monitoring + audit

The app emits enough breadcrumbs to back-trace any change. Wire these
into your SIEM, or just `tail -f` them for small deployments:

- **`audit_log`** — every model insert/update/delete with
  old/new values. Source = `api`.
- **`login_attempts`** — every admin login attempt with IP +
  user-agent, success or failure.
- **`portal_accesses`** — every employee-portal hit (cookieless and
  authed), with the resolved employee_id when known.
- **`document_audits`** — SHA-256 hash chain for every tax-form PDF
  ever generated. A printed form's footer carries the hash + audit
  ID; an auditor can verify the document hasn't been edited.
- **`/health`** — unauthenticated liveness probe. Wire to your load
  balancer or k8s readiness probe.

---

## Stopping, restarting, log rotation

### Docker

```bash
docker compose down              # stop (data persists in volumes)
docker compose up -d             # restart in background
docker compose down -v           # stop AND delete all data — destructive
docker compose logs -f slowbooks # tail
```

Docker handles log rotation via its log driver. Configure rotation
size + count in `docker-compose.yml` if needed:

```yaml
services:
  slowbooks:
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
```

### Native (systemd)

If running under systemd, logs go to the journal. Rotate normally
via `journalctl` retention settings.

---

## Production launch checklist

Use [docs/release-checklist.md](release-checklist.md) when going live
the first time. It covers secrets, TLS termination, required env vars,
HIPAA / tax-form caveats, and a final pre-flight test.
