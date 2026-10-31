# TLS Proxy Setup

SlowBooks doesn't terminate TLS itself. It expects to sit behind a
reverse proxy that handles the HTTPS handshake and forwards plain HTTP
to the app on a private network. This is the standard pattern for
self-hosted Python apps — uvicorn is great at serving HTTP, not great
at cert management.

This guide covers three options. Pick one.

## Why this matters

- The app emits HSTS and `Secure` session cookies whenever
  `FORCE_HTTPS=true` (the production default).
- The browser will refuse those cookies if it received them over plain
  HTTP. So the proxy MUST give the browser HTTPS.
- The proxy → app hop is allowed to be plain HTTP because it stays
  inside the host / Docker network and never touches the internet.

```
   Browser <──HTTPS──> [Proxy: nginx / Caddy / Traefik]  <──HTTP──> SlowBooks
                                                                    (port 3001)
```

Two things every option needs, covered at the end: `FORWARDED_ALLOW_IPS`
naming the proxy (or every page redirects to itself), and a door the proxy
can reach. `docker-compose.prod.yml` publishes no port: a proxy on the host
needs `127.0.0.1:3001:3001` added (see [cloud-hosting.md](cloud-hosting.md),
step 5), and a proxy in the compose network reaches `slowbooks:3001`. A
native install should set `APP_HOST=127.0.0.1` so only the proxy reaches the
app (the example `.env` ships `0.0.0.0`).

## Option 1: Caddy — easiest

Caddy ships its own ACME client. One file, two lines, you're done.

```Caddyfile
books.example.com {
    reverse_proxy localhost:3001
}
```

Install Caddy from its own repository (https://caddyserver.com/docs/install;
Ubuntu's package is several versions behind), replace the
`/etc/caddy/Caddyfile` it installed with the two lines above, then:

```bash
sudo systemctl reload caddy
```

Caddy automatically requests a Let's Encrypt cert for `books.example.com`,
renews it forever, and forwards everything to SlowBooks on port 3001.
Done.

Requirements: port 80 and port 443 must be reachable from the public
internet so the ACME HTTP-01 challenge can complete.

## Option 2: nginx — most common

More moving parts, but it's what most ops teams already know.

### Step 1: install nginx + certbot

```bash
sudo apt install nginx
sudo snap install --classic certbot      # certbot's recommended install; the apt package works too
sudo ln -s /snap/bin/certbot /usr/bin/certbot
```

### Step 2: site config

`/etc/nginx/sites-available/slowbooks`:

```nginx
server {
    listen 80;
    server_name books.example.com;
    # certbot will inject the http -> https redirect here.

    location / {
        proxy_pass http://127.0.0.1:3001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Uploads: attachments go to 50 MB; receipt scans can be larger.
        client_max_body_size 50M;
        proxy_read_timeout 300s;
    }
}
```

Then:

```bash
sudo ln -s /etc/nginx/sites-available/slowbooks /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### Step 3: get a cert

```bash
sudo certbot --nginx -d books.example.com
```

Certbot rewrites the nginx config to add TLS, restarts nginx, and
schedules auto-renewal. Verify renewal works:

```bash
sudo certbot renew --dry-run
```

## Option 3: Traefik — Docker-native

If you're running `docker-compose.prod.yml`, Traefik can sit alongside
it in the same compose file with auto-cert management.

Add to your prod compose (snippet, not a full file):

```yaml
services:
  traefik:
    image: traefik:v3
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --entrypoints.web.address=:80
      - --entrypoints.websecure.address=:443
      - --entrypoints.web.http.redirections.entrypoint.to=websecure
      - --entrypoints.web.http.redirections.entrypoint.scheme=https
      - --certificatesresolvers.le.acme.email=you@example.com
      - --certificatesresolvers.le.acme.storage=/letsencrypt/acme.json
      - --certificatesresolvers.le.acme.httpchallenge=true
      - --certificatesresolvers.le.acme.httpchallenge.entrypoint=web
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - traefik_certs:/letsencrypt

  slowbooks:
    # ... existing config ...
    labels:
      - traefik.enable=true
      - traefik.http.routers.slowbooks.rule=Host(`books.example.com`)
      - traefik.http.routers.slowbooks.entrypoints=websecure
      - traefik.http.routers.slowbooks.tls.certresolver=le
      - traefik.http.services.slowbooks.loadbalancer.server.port=3001

volumes:
  traefik_certs:
```

Bring it up:

```bash
docker compose -f docker-compose.prod.yml up -d
```

Traefik watches the Docker socket, sees the labels, requests a cert,
and starts forwarding traffic.

## Verifying it works

After whichever option:

```bash
# Should return 200 with a real cert
curl -fsSL https://books.example.com/health

# Should redirect to https (301 from nginx and certbot, 308 from Caddy)
curl -sSI http://books.example.com/health | grep -i location

# Cert should grade A or A+
# Open https://www.ssllabs.com/ssltest/analyze.html?d=books.example.com
```

## Cloud load balancers

If you're behind AWS ALB / GCP Load Balancer / Azure Application Gateway:

- Provision a cert in the cloud's cert manager (ACM, etc.)
- Configure the LB to listen on 443 with that cert
- Forward to the SlowBooks instance on port 3001 over plain HTTP
- Make sure the LB sets `X-Forwarded-Proto: https` so SlowBooks knows
  the original scheme

The app honours `X-Forwarded-For` and `X-Forwarded-Proto` only from the
addresses in `FORWARDED_ALLOW_IPS` (below), so set it to the load balancer's
subnet. A health check that speaks plain HTTP to the instance is answered
`307` (the redirect to HTTPS): configure the check to accept 200–399, or
check over HTTPS through the balancer.

## Client IP and rate limiting behind the proxy

Two settings matter once a proxy is in front. Both fail silently — the
app serves fine without them — so they are easy to miss.

### `FORWARDED_ALLOW_IPS` — required behind a proxy

uvicorn parses `X-Forwarded-For`, but only trusts it from the addresses
in `--forwarded-allow-ips`, which defaults to `127.0.0.1`. A proxy in
another container or pod has a different address, so by default the
header is ignored and **every request reports the proxy's IP**. That has
two invisible consequences:

- the login rate limiter (keyed on client IP) collapses into **one
  shared bucket for all users** — 5 login attempts/minute becomes 5
  across everyone, so one noisy client locks out real logins; and
- `login_attempts` / `portal_accesses` record the proxy's address, so
  the security audit tables are useless for forensics.

Set `FORWARDED_ALLOW_IPS` to the proxy's address or subnet:

```bash
# Docker compose default bridge network, for example
FORWARDED_ALLOW_IPS=172.18.0.0/16
```

`docker-compose.prod.yml` passes it through (it assumes a proxy). Nothing
else is needed: uvicorn reads `FORWARDED_ALLOW_IPS` from the environment
itself and honours `X-Forwarded-For` / `X-Forwarded-Proto` from those
addresses, so the client address the rate limiter and the audit tables see
is the one uvicorn decided on. (The older `TRUST_PROXY_HEADERS` knob is
retired; the app no longer parses the header a second time.)
**Never set it to `0.0.0.0/0` or `*`** — that trusts the header from any
source, letting a client forge its own IP and evade the rate limiter.

For the Traefik option above, the two containers share the compose
network, so set `FORWARDED_ALLOW_IPS` to that network's CIDR:
`docker network inspect <folder>_default --format '{{(index .IPAM.Config 0).Subnet}}'`
prints it. For nginx or Caddy on the same host forwarding to a **native**
app from `127.0.0.1`, the uvicorn default already covers it. For the app
**in Docker** with the proxy on the host, the app sees the Docker bridge
gateway (172.x.0.1), not 127.0.0.1, so the same `docker network inspect`
subnet is the value to set.

### `RATE_LIMIT_STORAGE_URI` — required for multiple workers

Rate-limit counters live in memory by default, which is per-worker and
per-replica. `docker-compose.prod.yml` runs `APP_WORKERS=4`, so a "5 per
minute" limit is really 20 — and more if you scale to several replicas.
Point the counters at shared storage so the limit means what it says:

```bash
RATE_LIMIT_STORAGE_URI=redis://redis:6379/0
```

Neither compose file ships a Redis, so add one beside the app (and an
unreachable one stops the app at startup):

```yaml
services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
```

Or set `APP_WORKERS=1` and leave the URI unset. Leave it unset for a
single-worker container or the desktop app, where the in-memory default is
correct. The app logs a warning at startup when
`APP_WORKERS > 1` and this is unset. The `redis` client ships with the app,
and when the URI is set the storage is health-checked once at startup: an
unreachable Redis stops the app with a message naming the variable and the
host, rather than turning every login into a 500.

## Common pitfalls

| Symptom | Likely cause |
|---------|--------------|
| Browser refuses the session cookie | `FORCE_HTTPS=true` but the request reached the app over plain HTTP. Check the proxy is sending traffic via HTTPS to the browser. |
| `ERR_TOO_MANY_REDIRECTS` | `FORWARDED_ALLOW_IPS` is unset (or does not cover the proxy's address). uvicorn then ignores the proxy's `X-Forwarded-Proto: https`, sees plain `http` on every request, and `FORCE_HTTPS` redirects to HTTPS forever. Set `FORWARDED_ALLOW_IPS` to the proxy's address or CIDR (above) and check the proxy sends `X-Forwarded-Proto: https`. |
| HSTS won't go away after disable | The browser cached it for `HSTS_MAX_AGE` seconds (2 years by default). Use a private window to test cert changes. |
| Let's Encrypt rate-limited | You hit 5 cert requests per week for the same domain. Wait, or use the staging environment (`--staging` flag) while iterating. |
| 502 Bad Gateway from nginx or Caddy | SlowBooks isn't running, or nothing listens on the host's port 3001: `docker-compose.prod.yml` publishes no port until you add `127.0.0.1:3001:3001` (cloud-hosting.md step 5). Check `ss -tlnp \| grep 3001`. |

## Once it's up

Walk [release-checklist.md](release-checklist.md) section 4 onward —
TLS is just step 4 of 11.
