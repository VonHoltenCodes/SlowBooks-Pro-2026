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

## Option 1: Caddy — easiest

Caddy ships its own ACME client. One file, two lines, you're done.

```Caddyfile
books.example.com {
    reverse_proxy localhost:3001
}
```

Save as `/etc/caddy/Caddyfile`, then:

```bash
sudo apt install caddy
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
sudo apt install nginx certbot python3-certbot-nginx
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
        # SlowBooks downloads (pay stubs, tax-form PDFs) can be large.
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

# Should 308 to https
curl -fsSI http://books.example.com/health | grep -i location

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

The app reads `X-Forwarded-For` and `X-Forwarded-Proto` correctly when
those headers are present, so login-attempt IPs and same-origin checks
work as expected.

## Common pitfalls

| Symptom | Likely cause |
|---------|--------------|
| Browser refuses the session cookie | `FORCE_HTTPS=true` but the request reached the app over plain HTTP. Check the proxy is sending traffic via HTTPS to the browser. |
| `ERR_TOO_MANY_REDIRECTS` | The proxy is forwarding HTTPS to the app, and the app's `HTTPSRedirectMiddleware` is bouncing it again. Either set `FORCE_HTTPS=false` (the proxy is the TLS terminator) or make sure the proxy sets `X-Forwarded-Proto: https`. |
| HSTS won't go away after disable | The browser cached it for `HSTS_MAX_AGE` seconds (2 years by default). Use a private window to test cert changes. |
| Let's Encrypt rate-limited | You hit 5 cert requests per week for the same domain. Wait, or use the staging environment (`--staging` flag) while iterating. |
| 502 Bad Gateway from nginx | SlowBooks isn't running, or it's bound to `127.0.0.1` and nginx is in a different namespace. Check `ss -tlnp \| grep 3001`. |

## Once it's up

Walk [release-checklist.md](release-checklist.md) section 4 onward —
TLS is just step 4 of 11.
