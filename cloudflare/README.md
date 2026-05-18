# Slowbooks Pro 2026 — Cloudflare Workers AI Gateway

Optional per-installation AI gateway. Runs inside your own Cloudflare
account so the actual AI credentials never touch Slowbooks' database.

## Why bother?

- **Your keys stay in Cloudflare.** Slowbooks only ever holds a shared
  secret scoped to your one Worker. Even if someone dumps the Slowbooks
  SQLite file, they can't talk to Workers AI as you.
- **Free tier.** Cloudflare Workers AI gives every account 10,000
  neurons per day for free — plenty for hundreds of AI Insights runs
  and tool-calling Q&A sessions.
- **Per-person lockdown.** Every LAN owner installs their own Worker in
  their own Cloudflare account. One compromised install can't reach
  another. One abused install can't burn someone else's quota.
- **Zero leakage.** The Worker never logs the AUTH_TOKEN, never echoes
  it back, and never stores request bodies. Constant-time comparison
  on the shared secret prevents timing side-channels.

## One-time setup (~5 minutes)

### 1. Cloudflare account

Free tier is all you need:
https://dash.cloudflare.com/sign-up

### 2. Install wrangler

```bash
npm install -g wrangler
wrangler login
```

`wrangler login` opens a browser tab; approve the OAuth request and
come back to the terminal.

### 3. Generate a shared secret

```bash
openssl rand -hex 32
```

Copy the 64-char hex string. You'll paste it twice: once into
Cloudflare (step 4), once into Slowbooks (step 6). Never commit it.

### 4. Store the secret in Cloudflare

From the `cloudflare/` directory of your Slowbooks checkout:

```bash
cd cloudflare
wrangler secret put AUTH_TOKEN
# Paste the hex string from step 3, press Enter
```

Wrangler encrypts the secret at rest inside Cloudflare. It never shows
up in source control, in logs, or in the Worker's response payloads.

### 5. Deploy the Worker

```bash
wrangler deploy
```

Wrangler prints your Worker URL, e.g.
`https://slowbooks-ai.yourname.workers.dev`. Copy it.

### 6. Wire Slowbooks to the Worker

1. Open Slowbooks → Analytics → **⚙ AI**
2. Provider: **Cloudflare Workers AI** (or the dedicated
   _Cloudflare Worker Gateway_ option if your version of Slowbooks
   ships one separately)
3. Cloudflare account ID: your 32-char hex ID from the top right of
   https://dash.cloudflare.com/
4. Worker URL: the one `wrangler deploy` just printed
5. API key: the shared secret from step 3
6. Click **Save**, then **Test** — you should see the word `ok`

## Smoke test from the command line

You can verify the Worker without touching Slowbooks:

```bash
curl -sS https://slowbooks-ai.yourname.workers.dev/v1/chat/completions \
  -H "Authorization: Bearer <your-shared-secret>" \
  -H "Content-Type: application/json" \
  -d '{
        "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "messages": [
          {"role": "user", "content": "Reply with the word ok and nothing else."}
        ],
        "max_tokens": 16
      }'
```

You should get an OpenAI-shaped JSON response with `choices[0].message.content: "ok"`.

## Environment variables

The Worker respects several environment variables to customize behaviour.
All are optional except `AUTH_TOKEN`.

### AUTH_TOKEN (required)

The shared secret that Slowbooks sends as a Bearer token. Generate it with
`openssl rand -hex 32` and store it in Cloudflare as a secret:

```bash
cd cloudflare
wrangler secret put AUTH_TOKEN
# Paste the 64-char hex string from `openssl rand -hex 32`, press Enter
```

### ALLOWED_MODELS (optional)

Comma-separated list of model IDs to permit. If not set, defaults to:

```
@cf/meta/llama-3.3-70b-instruct-fp8-fast,@cf/meta/llama-3.1-8b-instruct,@cf/mistral/mistral-7b-instruct-v0.2-lora
```

To restrict which models Slowbooks can invoke:

```bash
wrangler deploy --env production
# (first set ALLOWED_MODELS in wrangler.toml [vars] section, or in the
# Cloudflare dashboard under Variables > Configuration > Variables)
```

### DEFAULT_MODEL (optional)

Which model to use if Slowbooks' request doesn't specify one. Must be a model
in `ALLOWED_MODELS`. Defaults to `@cf/meta/llama-3.3-70b-instruct-fp8-fast`.

Set via wrangler.toml or Cloudflare dashboard.

### ALLOWED_ORIGINS (optional)

Comma-separated list of browser origins allowed for CORS. If not set, CORS is
not enforced (useful for same-origin deployments). Example:

```
http://localhost:3000,https://slowbooks.local
```

Set via wrangler.toml or Cloudflare dashboard.

### LOG_VERBOSE (optional)

Set to `"1"` to enable structured logging of requests and responses to stdout
(visible in `wrangler tail`). Defaults to `"0"` (logging disabled) to avoid
logging sensitive data.

```bash
wrangler deploy --env production
# (first set LOG_VERBOSE = "1" in wrangler.toml [vars], or dashboard)
wrangler tail  # watch live logs
```

## Rotating the shared secret

Routine hygiene — do it whenever you rotate other credentials:

```bash
cd cloudflare
wrangler secret put AUTH_TOKEN   # paste a fresh `openssl rand -hex 32`
```

Then update Slowbooks → ⚙ AI → API key with the new value and click
**Save**. Old Slowbooks installs using the old secret will get `401`s
immediately.

## Operational notes

- **Live logs:** `wrangler tail` streams every request to your terminal
  (minus the Authorization header, which wrangler sanitises).
- **Custom domain:** if you don't like `workers.dev`, bind the Worker
  to any domain you own via the Cloudflare dashboard → Workers &
  Pages → your Worker → Triggers.
- **Rate limits:** Cloudflare applies the free 10k-neurons/day quota
  automatically. Add `[limits]` to `wrangler.toml` if you want
  additional guardrails.
- **Auditing:** every Worker invocation is visible in the Cloudflare
  dashboard under Workers & Pages → slowbooks-ai → Logs.

## What this does _not_ protect against

- A compromised Slowbooks install still has the shared secret, so it
  can still invoke _your_ Worker. Mitigation: rotate the secret if you
  suspect compromise; you'll detect unexpected Worker traffic in the
  Cloudflare dashboard.
- Workers AI itself is a third-party service. Cloudflare's terms and
  privacy policy apply to anything you send through it.

## Uninstalling

```bash
cd cloudflare
wrangler delete
```

That tears down the Worker. Slowbooks will start returning 502s on AI
requests until you either redeploy or switch to a different provider
in ⚙ AI settings.
