# Operations Runbook

Day-2 operational tasks: backups, restores, key rotation, monitoring.
For first-time install see [INSTALL.md](../INSTALL.md); for the full
production-launch checklist see
[docs/release-checklist.md](release-checklist.md).

---

## Backups

### Quick local backup

```bash
PGHOST=localhost ./scripts/backup.sh
```

- Output: `~/bookkeeper-backups/bookkeeper_YYYYMMDD_HHMMSS.sql.gz`
- The dump carries `--clean --if-exists`, so it restores over a database the
  app has already set up (see Restore).
- Retention: keeps the 30 most recent dumps; older ones are pruned
  automatically. A failed dump leaves no file behind.
- `PGHOST=localhost` makes `pg_dump` connect over TCP with the password in
  `~/.pgpass` (`localhost:5432:bookkeeper:bookkeeper:<password>`, mode 600);
  the local socket uses peer authentication on stock Ubuntu, which refuses
  the `bookkeeper` role. `DB_NAME`, `DB_USER` and `BACKUP_DIR` override the
  defaults.
- The desktop app and Server Edition keep each company in one SQLite file and
  back it up from Settings → Backup / Restore; this script is for the
  PostgreSQL installs.

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

To take a one-off backup from a running container, into the volume the
`cp` above reads (without `BACKUP_DIR` it lands in the container's home
folder and is lost when the container is recreated):

```bash
docker compose exec -e BACKUP_DIR=/app/backups slowbooks ./scripts/backup.sh
```

Under `docker-compose.prod.yml` the app container has no `PG*` connection
settings; dump from the `postgres` container instead, as in
[cloud-hosting.md](cloud-hosting.md), "Backups that leave the machine".

### Restore

Stop the app first, and always pass `-v ON_ERROR_STOP=1`: without it psql
reports every failed statement and still exits 0, and a restore that
collided with the existing tables looked successful while nothing was
restored. A dump from `scripts/backup.sh` (or `pg_dump --clean --if-exists`)
replaces the tables the app's first start made; a plain dump restores only
into an empty database, so drop and recreate it first.

Native:

```bash
gunzip -c bookkeeper_20260523_020000.sql.gz | PGHOST=localhost psql -v ON_ERROR_STOP=1 -U bookkeeper bookkeeper
```

Docker:

```bash
docker compose stop slowbooks
gunzip -c bookkeeper_20260523_020000.sql.gz | \
    docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U bookkeeper bookkeeper
docker compose start slowbooks
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

The rewrap command needs the source tree; the desktop app and Server Edition
(the frozen build) have no rotation command, and their keys live in the
`.env` beside the data folder (`%LOCALAPPDATA%\SlowBooksPro\.env`, or
`C:\ProgramData\SlowBooksPro\.env` for a Server Edition install): keep that
file with the backups. Master key files (`.slowbooks-master.key`,
`.slowbooks-session.key`) are excluded in `.gitignore` — never commit them. Losing the master
key means entering every saved password and API key again: since 2.18 a
secret no key decrypts reads as not set, and Settings names each one. A
Docker install with no `SETTINGS_ENCRYPTION_KEY` derives its settings key
from `PAYROLL_ENCRYPTION_SECRET`, so keeping that secret keeps both.

---

## Monitoring + audit

The app emits enough breadcrumbs to back-trace any change. Wire these
into your SIEM, or just `tail -f` them for small deployments:

- **`audit_log`** — every model insert/update/delete with
  old/new values. Source = `api`.
- **`login_attempts`** — every sign-in attempt, any role, with IP +
  user-agent, success or failure.
- **`portal_accesses`** — every employee-portal hit (cookieless and
  authed), with the resolved employee_id when known.
- **`document_audits`** — a SHA-256 hash of every tax-form PDF ever
  generated, one row per form. A printed form's footer carries the hash +
  audit ID; an auditor can recompute the hash and confirm the document
  hasn't been edited.
- **`/health`** — unauthenticated liveness probe. Wire to your load
  balancer or k8s readiness probe.

---

## Several companies on a server

A PostgreSQL server serves exactly one database, the one its `DATABASE_URL`
names. On a desktop install each company is a file and the app's own picker
switches between them. Server Edition on Windows (company files, `--serve-lan`)
serves every company in its list, chosen at the sign-in screen: see
[server-edition.md](server-edition.md), "Several companies on one server". On a
PostgreSQL server (Docker, or a native install on PostgreSQL) there is
no picker to switch to, and the sign-in page never lists companies: it names
the company the server serves.

**Companies → + New Company** on a server install creates the new company as
its own PostgreSQL database, migrated and seeded, and lists it under Company
Files. Nothing serves it until a server points at it. Two ways:

1. **A second server, side by side** (the usual answer). In
   `docker-compose.yml`, add a service beside `slowbooks` with the database
   name you typed in the New Company dialog, another port, and its own
   volumes, then `docker compose up -d`:

   ```yaml
     slowbooks-second:
       build: .
       restart: unless-stopped
       depends_on:
         postgres:
           condition: service_healthy
       environment:
         DATABASE_URL: postgresql://${POSTGRES_USER:-bookkeeper}:${POSTGRES_PASSWORD:-bookkeeper}@postgres:5432/YOUR_DATABASE_NAME
         PGHOST: postgres
         PGPORT: "5432"
         PGUSER: ${POSTGRES_USER:-bookkeeper}
         PGPASSWORD: ${POSTGRES_PASSWORD:-bookkeeper}
         APP_HOST: "0.0.0.0"
         APP_PORT: "3001"
         SLOWBOOKS_PRIVATE_NETWORK: "${SLOWBOOKS_PRIVATE_NETWORK:-1}"
         FORCE_HTTPS: "false"
         PAYROLL_ENCRYPTION_SECRET: "${PAYROLL_ENCRYPTION_SECRET}"
       ports:
         - "127.0.0.1:3002:3001"
       volumes:
         - slowbooks_uploads_second:/app/app/static/uploads
         - slowbooks_backups_second:/app/backups
   ```

   and the two volumes under `volumes:` at the bottom
   (`slowbooks_uploads_second:` and `slowbooks_backups_second:`). The second
   company is at port 3002 with its own first-run setup, its own sign-ins and
   its own backups. Each service's `docker compose logs <service>` is its own.

2. **One server, one company at a time.** Change the one service's
   `DATABASE_URL` to the other database and `docker compose up -d` again. A
   restart signs everyone out of the company it was serving.

Backups are per database: `scripts/backup.sh` dumps the database `DB_NAME`
names (default `bookkeeper`), so for the second company pass its name:
`docker compose exec -e DB_NAME=YOUR_DATABASE_NAME -e BACKUP_DIR=/app/backups slowbooks-second ./scripts/backup.sh`.
Settings → Backup / Restore in each company does the same from the browser.

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

A unit for a native install, as an example (`/etc/systemd/system/slowbooks.service`;
the app reads its `.env` itself from the working directory):

```ini
[Unit]
Description=SlowBooks Pro
After=network.target postgresql.service

[Service]
User=slowbooks
WorkingDirectory=/opt/slowbooks
ExecStart=/opt/slowbooks/.venv/bin/python run.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

`sudo systemctl enable --now slowbooks`; logs go to the journal
(`journalctl -u slowbooks -f`) and rotate with its retention settings. Add a
cron line for `scripts/run_recurring.py` if the company uses recurring
invoices or memorized transactions and nobody opens the app daily.

---

## Production launch checklist

Use [docs/release-checklist.md](release-checklist.md) when going live
the first time. It covers secrets, TLS termination, required env vars,
HIPAA / tax-form caveats, and a final pre-flight test.
