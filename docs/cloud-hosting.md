# Hosting SlowBooks on a cloud server

One small Linux VPS, Docker, a reverse proxy with a real certificate, and
backups that leave the machine. This is the whole recipe. It takes about an
hour, and nothing in it is specific to SlowBooks except the last section.

Read the two-sentence licence note first, then the list of what the desktop
build is not for.

## Before you start

**Whose books.** LICENSE section 2 lets you run SlowBooks on a server for
yourself, your business, or your own clients' books. Section 3(b) does not let
you offer it to other people as a hosted service — giving people outside your
organization access to the interface or the API as the thing they pay for.
A cloud box for your own company is the first case. Hosting it for strangers
is the second, and this guide is not for that.

**Not the desktop build.** `SlowBooksPro.exe --serve-lan` and the macOS app
serve plain HTTP on a trusted office network. They have no TLS, no place for a
proxy to sit, and the docs say not to port-forward them. On a cloud server run
the Docker image, which is built for this: it refuses to start in debug mode,
requires a real session secret and encryption key, and emits `Secure` cookies
and HSTS.

**What is at stake.** A company file holds payroll records with Social
Security numbers, bank feeds, customer addresses and your tax filings. Treat
the server the way you would treat the filing cabinet those used to live in:
one door, one key, and a copy somewhere else.

## 1. The server

Any provider with a plain Linux virtual machine works: DigitalOcean, Hetzner,
Linode, Vultr, a small EC2 or Lightsail instance, a Compute Engine e2-small.
SlowBooks is not demanding.

| | |
|---|---|
| Size | 1 vCPU, 2 GB RAM, 25 GB disk is comfortable for one company and a handful of users; the stress-test company (two years of books, 1,900 documents) runs in well under 1 GB |
| OS | Ubuntu 24.04 LTS or Debian 12. This guide uses Ubuntu commands |
| Region | Near your users; the books do not care |
| Cost | Roughly $5 to $12 a month at the time of writing |

Create it with **an SSH key, not a password**, and note its public IP.

Point a DNS name at that IP before you go further, because the certificate
step needs one. `books.example.com` is the name used below; use your own.

## 2. Lock the door

Log in once as root and do these before installing anything.

```bash
# updates, a firewall, and an SSH lockout for brute force
apt update && apt upgrade -y
apt install -y ufw fail2ban unattended-upgrades
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp     # only so the proxy can get its certificate
ufw allow 443/tcp
ufw enable
systemctl enable --now fail2ban

# a normal user for everything after this; disable root logins
adduser books
usermod -aG sudo books
rsync --archive --chown=books:books ~/.ssh /home/books
sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin no/; s/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh
```

From here on log in as `books`. If your provider offers a firewall in its
control panel, set the same three ports there too; two layers cost nothing.

Port 3001 is never opened. The app only ever talks to the proxy on the
machine's own network.

## 3. Docker and the source

```bash
sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker books   # log out and in again afterwards
git clone https://github.com/VonHoltenCodes/SlowBooks-Pro-2026.git ~/slowbooks
cd ~/slowbooks
git checkout v2.18.0             # a release tag, never main
```

Pin a release tag. `main` moves between gates; a tag is a build that three
platforms passed.

## 4. Secrets and settings

The production compose file refuses to start without these. Generate them
fresh on the server; do not reuse anything from a laptop or another install.

```bash
cd ~/slowbooks
cp .env.example .env
chmod 600 .env

# generate the two secrets and a database password
echo "PAYROLL_ENCRYPTION_SECRET=$(openssl rand -base64 32)" >> .env
echo "SESSION_SECRET_KEY=$(openssl rand -hex 32)"           >> .env
echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"            >> .env
```

Then open `.env` and set the rest by hand:

| Key | Set it to |
|---|---|
| `POSTGRES_USER`, `POSTGRES_DB` | `bookkeeper` is fine for both |
| `APP_DEBUG` | `false` |
| `FORCE_HTTPS` | `true` |
| `TRUST_PROXY_HEADERS` | `true` — the app is behind a proxy you control, so the client address and scheme come from the proxy's headers. Leave it `false` anywhere the app is reachable without a proxy |
| `CORS_ALLOW_ORIGINS` | `https://books.example.com`, your real name, no wildcard |
| `SESSION_IDLE_TIMEOUT_SECONDS` | `14400` (four hours) is the default; shorter is fine |
| `EMPLOYER_EIN`, `EMPLOYER_STATE`, `SUTA_RATE` | yours, if you run payroll |

`PAYROLL_ENCRYPTION_SECRET` encrypts the payroll and API-key columns at rest.
**Copy it somewhere safe now.** A backup restored on a new server without this
exact value cannot read those columns, and there is no recovery.

The production compose also expects a certificate pair for PostgreSQL at
`./certs/postgres/`. The database is only reachable from the app container
on Docker's private network, so a self-signed pair is appropriate here:

```bash
mkdir -p certs/postgres
openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
  -subj "/CN=postgres" \
  -keyout certs/postgres/server.key -out certs/postgres/server.crt
sudo chown 70:70 certs/postgres/server.key   # the postgres user inside the alpine image
chmod 600 certs/postgres/server.key
```

## 5. Start it

The production compose file publishes nothing to the host: the app is only
reachable inside Docker's network. The proxy in the next step runs on the
host, so give it a door that only the host can see — port 3001 bound to
loopback, never to the public interface:

```bash
cat > docker-compose.proxy.yml <<'YML'
services:
  slowbooks:
    ports:
      - "127.0.0.1:3001:3001"
YML
docker compose -f docker-compose.prod.yml -f docker-compose.proxy.yml up -d --build
docker compose -f docker-compose.prod.yml -f docker-compose.proxy.yml logs -f slowbooks
```

The log ends with the startup checks passing and uvicorn listening on 3001.
If a check fails it says which setting is wrong and exits; fix `.env` and run
`up -d` again. `curl -sI http://127.0.0.1:3001/health` answers from the host;
from anywhere else the port does not exist, and the firewall would refuse it
anyway.

## 6. The proxy and the certificate

Caddy is the least to get wrong. It obtains and renews the certificate itself
and sets the forwarding headers the app needs.

```bash
sudo apt install -y caddy
sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
books.example.com {
    reverse_proxy localhost:3001
}
EOF
sudo systemctl reload caddy
```

Wait a few seconds, then open `https://books.example.com`. You should see
the first-run setup screen, and the browser's padlock should be clean. nginx
and Traefik are covered in [tls-proxy-setup.md](tls-proxy-setup.md) if you
already run one of those.

If you would rather run Caddy as a container, skip the loopback publish
above, put Caddy on the compose network, and proxy to `slowbooks:3001`.

## 7. First run, and the users

Open the site, set the operator password, and enter the company. Then, under
**Manage → Users**, add a named account for each person and stop using the
operator login for daily work. Roles are admin, bookkeeper and read-only;
[server-edition.md](server-edition.md) describes what each can reach.

On a multi-user install the sign-in screen lists the user names. That is by
design on a trusted network; on a public address it is the one thing you may
want to weigh, since it tells a stranger who works there. If it matters to
you, keep a single user and share the books by role elsewhere.

## 8. Backups that leave the machine

A backup on the same disk as the database is not a backup. Two layers:

**Nightly database dump, copied off the box.** Dump from the database
container itself — it has `pg_dump` and reaches the database over its own
socket, so no password or host is needed:

```bash
# /home/books/backup.sh
#!/bin/bash
set -euo pipefail
cd /home/books/slowbooks
mkdir -p /home/books/backups
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U bookkeeper bookkeeper | gzip > "/home/books/backups/slowbooks_$(date +%Y%m%d_%H%M%S).sql.gz"
# ship the newest one off the machine — pick one:
#   rclone copy /home/books/backups remote:slowbooks-backups        (any cloud bucket)
#   scp /home/books/backups/*.gz you@home-nas:/backups/slowbooks/   (a machine you own)
find /home/books/backups -name '*.gz' -mtime +30 -delete
```

```bash
chmod +x ~/backup.sh
(crontab -l 2>/dev/null; echo "15 2 * * * /home/books/backup.sh >> /home/books/backup.log 2>&1") | crontab -
```

Restoring is the same command the other way, from [operations.md](operations.md):
`docker compose -f docker-compose.prod.yml exec -T postgres psql -U bookkeeper bookkeeper < <(gunzip -c the-file.sql.gz)`
on a fresh stack that has run once. Uploaded receipts and attachments live in
the `slowbooks_uploads` volume; the weekly snapshot below covers them.

**Provider snapshots** of the whole disk, weekly, from the control panel.
They are cheap and they restore the proxy and the secrets along with the data.

**Test a restore once**, on a scratch server, before you need it. The steps
are in [operations.md](operations.md). Remember the encryption secret.

## 9. Keep it current

```bash
cd ~/slowbooks
git fetch --tags
git checkout v2.19.0          # the new tag
docker compose -f docker-compose.prod.yml -f docker-compose.proxy.yml up -d --build
```

The app migrates the database on start. Take a backup first; a release never
needs one, and the one time it does you will be glad. The Docker image is
gated on Linux with PostgreSQL every release, so the path you are on is the
path that was tested.

```bash
sudo apt update && sudo apt upgrade -y     # monthly, for the host itself
```

## What this gives you

| | |
|---|---|
| In transit | HTTPS from Caddy with an automatically renewed certificate; HSTS from the app |
| At rest | Payroll and API-key columns encrypted with your key; PostgreSQL over TLS inside Docker |
| Doors | 22, 80, 443 open; SSH by key only; fail2ban; the app and the database unreachable from outside |
| Sessions | `Secure`, `SameSite=Strict` cookie; four-hour idle timeout on a 30-day hard expiry; rate-limited sign-in, every attempt recorded |
| Recovery | A nightly dump off the box, a weekly disk snapshot, and a restore you have tried once |

## What it does not give you

- **Two-factor sign-in.** SlowBooks does not have it. The password and the
  key-only SSH are the perimeter. If you want a second factor, put the site
  behind a VPN (Tailscale is the easiest) or an identity-aware proxy such as
  Cloudflare Access, and open 443 only to that.
- **A managed service.** Nobody is watching this server but you. The
  provider's uptime alerts and the backup log are your monitoring.
- **Compliance paperwork.** [hipaa-compliance.md](hipaa-compliance.md) is
  honest about the gaps if you need to answer to an auditor.

If you would rather not expose the site to the internet at all, the VPN option
above is a good answer for a small company: the server has no public web port,
and every user's laptop is on the private network. Everything else in this
guide still applies.
