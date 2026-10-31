# SlowBooks Pro 2026 — Cloudflare Worker AI Gateway

Optional. Runs inside your own Cloudflare account so the AI credential never
touches SlowBooks' company file: SlowBooks holds only a shared secret that
opens your one Worker, and the Worker calls Cloudflare's Workers AI models
with the account's own binding.

If you would rather not run a Worker, **Settings → AI Insights** also takes a
Cloudflare API token directly (provider *Cloudflare Workers AI (direct)*), or
a key for Anthropic, xAI, Groq, OpenAI, Google Gemini or any OpenAI-compatible
endpoint. This page is for the Worker.

## Why bother?

- **Your keys stay in Cloudflare.** SlowBooks only ever holds a shared
  secret scoped to your one Worker. Even if someone copies the company
  file, they can't talk to Workers AI as you.
- **Per-installation lockdown.** Every office installs its own Worker in
  its own Cloudflare account. One compromised install can't reach
  another, and one abused install can't burn someone else's allowance.
- **Zero leakage.** The Worker never logs the shared secret, never echoes
  it back, and never stores request bodies. The secret is compared in
  constant time.
- **Cost.** Workers AI has a free daily allowance (10,000 neurons a day at
  the time of writing) and paid accounts are billed per neuron. An AI
  Insights run is capped at 1,024 output tokens, which on the default
  70B model is a few dozen runs a day on the free allowance; Cloudflare's
  pricing page has the current figures.

## One-time setup (about 10 minutes)

### 1. Cloudflare account

The free plan is enough: https://dash.cloudflare.com/sign-up

### 2. Install wrangler

Wrangler, Cloudflare's command-line tool, needs **Node.js 22 or later**
(Ubuntu 24.04's packaged Node is 18, which it refuses). Install Node from
https://nodejs.org or your package manager's current channel, then, from the
`cloudflare/` folder of your SlowBooks checkout:

```bash
cd cloudflare
npm install wrangler@latest
npx wrangler login
```

`wrangler login` opens a browser tab; approve the request and come back to
the terminal. The commands below use `npx wrangler`; a global install
(`npm install -g wrangler`) works too, as plain `wrangler`.

### 3. Generate a shared secret

```bash
openssl rand -hex 32
```

Copy the 64-character string. You'll paste it twice: once into Cloudflare
(step 4), once into SlowBooks (step 6). Never commit it.

### 4. Store the secret in Cloudflare

```bash
npx wrangler secret put AUTH_TOKEN
# Paste the hex string from step 3, press Enter
```

Wrangler encrypts the secret at rest inside Cloudflare. It never shows up
in source control, in logs, or in the Worker's responses.

### 5. Deploy the Worker

```bash
npx wrangler deploy
```

Plain `deploy`: this `wrangler.toml` has no environments, so `--env
production` would create a second Worker named `slowbooks-ai-production`
with no secret, which SlowBooks never calls. Wrangler prints your Worker
URL, e.g. `https://slowbooks-ai.yourname.workers.dev`. Copy it.

### 6. Wire SlowBooks to the Worker

1. Open SlowBooks → **Settings → AI Insights** (an administrator's setting).
2. Provider: **Cloudflare Worker Gateway (self-hosted)**.
3. Worker URL: the one `wrangler deploy` printed.
4. API key: the shared secret from step 3.
5. Model: pick one from the list. The list is this Worker's allowlist
   (`ALLOWED_MODELS`, below); the default is
   `@cf/meta/llama-3.3-70b-instruct-fp8-fast`.
6. Click **Save AI settings**, then **Test**. A good answer names the model
   and says the provider answered.

No account ID is needed for the gateway; that field belongs to the direct
provider.

## Smoke test from the command line

```bash
curl -sS https://slowbooks-ai.yourname.workers.dev/v1/models \
  -H "Authorization: Bearer <your-shared-secret>"

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

The first answers the allowed models in OpenAI's list shape; the second an
OpenAI-shaped response with `choices[0].message.content: "ok"`.

## Environment variables

All optional except `AUTH_TOKEN`. Set the optional ones in the `[vars]`
section of `wrangler.toml` and deploy again; a value set in the Cloudflare
dashboard is overwritten by the next deploy unless `wrangler.toml` has
`keep_vars = true`.

### AUTH_TOKEN (required)

The shared secret SlowBooks sends as a Bearer token. Set it with
`npx wrangler secret put AUTH_TOKEN`, never in `wrangler.toml`.

### ALLOWED_MODELS (optional)

Comma-separated model IDs the Worker will run, and the list `GET /v1/models`
answers (what SlowBooks' model picker shows). Default:

```
@cf/meta/llama-3.3-70b-instruct-fp8-fast,@cf/mistral/mistral-7b-instruct-v0.2-lora
```

Cloudflare retires models from time to time (`@cf/meta/llama-3.1-8b-instruct`,
in the old default, went on 2026-05-30); the current list is at
https://developers.cloudflare.com/workers-ai/models/. Pick text-generation
models.

### DEFAULT_MODEL (optional)

The model used when a request names none. Must be in `ALLOWED_MODELS`.
Default `@cf/meta/llama-3.3-70b-instruct-fp8-fast`. SlowBooks always names
the model it was given, so this only matters for other callers.

### LOG_VERBOSE (optional)

Set to `"1"` to log each request's model, message count and token usage
(never the text or the secret) to the Worker's logs, visible with
`npx wrangler tail`. Default `"0"`.

### ALLOWED_ORIGINS (optional)

Browser origins allowed for CORS. SlowBooks calls the Worker from its own
server, not from the browser, so this is only for other clients of yours.
Unset, no CORS headers are sent.

## Rotating the shared secret

```bash
npx wrangler secret put AUTH_TOKEN   # paste a fresh `openssl rand -hex 32`
```

Then paste the new value into SlowBooks → Settings → AI Insights → API key
and **Save AI settings**. Anything still using the old secret gets `401`
at once.

## Operational notes

- **Live logs:** `npx wrangler tail` streams every request to your terminal
  (the Authorization header is never logged).
- **Custom domain:** bind the Worker to a domain you own from the Worker's
  settings in the Cloudflare dashboard, then put that address in SlowBooks.
- **Limits:** the Worker refuses bodies over 512 KB, more than 64 messages,
  and models not on the allowlist, and clamps `max_tokens` to 4,096. The
  `[limits]` section of `wrangler.toml` caps CPU time per request (paid
  plans); request rate limiting is Cloudflare's rate-limiting binding, not
  covered here.
- **Auditing:** every invocation is counted on the Worker's page in the
  Cloudflare dashboard, under its metrics and logs.

## What this does _not_ protect against

- A compromised SlowBooks install still has the shared secret, so it can
  still invoke _your_ Worker. Rotate the secret if you suspect compromise;
  unexpected traffic shows in the Worker's metrics.
- Workers AI is a third-party service. Cloudflare's terms and privacy
  policy apply to what you send through it, as with every AI provider
  SlowBooks offers.

## Uninstalling

```bash
npx wrangler delete
```

That removes the Worker. SlowBooks' AI features then fail with a 502 until
you redeploy or choose another provider under Settings → AI Insights.
