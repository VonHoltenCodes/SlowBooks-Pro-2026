// ============================================================================
// Slowbooks Pro 2026 — Cloudflare Worker AI Gateway
// ============================================================================
//
// Runs inside each LAN owner's own Cloudflare account. Slowbooks points at
// this Worker instead of talking to an AI provider directly, so the real
// credentials never leave Cloudflare. Every installation gets its own
// Worker, its own shared secret, and its own usage quota.
//
// Architecture:
//   Slowbooks (your LAN)                  Cloudflare (worldwide edge)
//   ────────────────────                  ───────────────────────────
//   POST /v1/chat/completions   ───────►  Worker (this file)
//   Authorization: Bearer <T>                │
//                                            │ validates T == AUTH_TOKEN
//                                            ▼
//                                        env.AI.run(model, ...)
//                                            │
//                                            ▼
//                                        Workers AI (free tier: 10k neurons/day)
//
// Setup (~5 min):
//   1. Free Cloudflare account:  https://dash.cloudflare.com/sign-up
//   2. Install wrangler:         npm install -g wrangler && wrangler login
//   3. Generate shared secret:   openssl rand -hex 32
//   4. Store it in Cloudflare:   wrangler secret put AUTH_TOKEN  (paste it)
//   5. Deploy:                   wrangler deploy
//   6. Slowbooks → ⚙ AI          Provider: cloudflare_worker
//                                Worker URL: <printed by wrangler deploy>
//                                API key: the shared secret from step 3
//
// Supports OpenAI-compatible tool calling so Slowbooks' /ai-query endpoint
// works through this gateway unchanged.
// ============================================================================

const DEFAULT_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast";
const DEFAULT_MAX_TOKENS = 1024;
const DEFAULT_TEMPERATURE = 0.3;

export default {
  /**
   * @param {Request} request
   * @param {{ AI: any, AUTH_TOKEN?: string }} env
   * @param {ExecutionContext} ctx
   */
  async fetch(request, env, ctx) {
    // --- Method + path gating --------------------------------------------
    // Only accept POST. Accept both /v1/chat/completions (OpenAI-compat)
    // and /chat/completions so the gateway can be mounted under any route.
    if (request.method !== "POST") {
      return jsonError(405, "Method not allowed. Use POST /v1/chat/completions.");
    }

    const { pathname } = new URL(request.url);
    if (!pathname.endsWith("/chat/completions")) {
      return jsonError(404, `Unknown route: ${pathname}`);
    }

    // --- Shared-secret auth ----------------------------------------------
    // The shared secret MUST be set as a Cloudflare Worker secret:
    //   wrangler secret put AUTH_TOKEN
    // Never hardcode it here; never commit it.
    if (!env.AUTH_TOKEN) {
      return jsonError(
        500,
        "Worker misconfigured: AUTH_TOKEN secret not set. " +
          "Run `wrangler secret put AUTH_TOKEN`."
      );
    }

    const authHeader = request.headers.get("authorization") || "";
    const expected = `Bearer ${env.AUTH_TOKEN}`;
    if (!constantTimeEqual(authHeader, expected)) {
      return jsonError(401, "Unauthorized");
    }

    // --- Parse body -------------------------------------------------------
    let body;
    try {
      body = await request.json();
    } catch (_e) {
      return jsonError(400, "Invalid JSON body");
    }

    const model = body.model || DEFAULT_MODEL;
    const messages = Array.isArray(body.messages) ? body.messages : [];
    const maxTokens = Number(body.max_tokens) || DEFAULT_MAX_TOKENS;
    const temperature =
      typeof body.temperature === "number" ? body.temperature : DEFAULT_TEMPERATURE;
    const tools = Array.isArray(body.tools) ? body.tools : null;

    if (messages.length === 0) {
      return jsonError(400, "`messages` must be a non-empty array");
    }

    // --- Invoke Workers AI via binding -----------------------------------
    // The binding has implicit access to this Cloudflare account's Workers
    // AI quota — no API token needed. Secrets stay inside Cloudflare.
    const aiInput = {
      messages,
      max_tokens: maxTokens,
      temperature,
    };
    if (tools) aiInput.tools = tools;

    let aiResult;
    try {
      aiResult = await env.AI.run(model, aiInput);
    } catch (e) {
      return jsonError(502, `Workers AI error: ${String(e && e.message || e)}`);
    }

    // --- Translate to OpenAI-compat response shape -----------------------
    // Slowbooks' ai_service.parse_response() expects
    //   choices[0].message.content  (+ optional tool_calls)
    // so we normalise here regardless of the underlying model's raw shape.
    const responseText = typeof aiResult?.response === "string" ? aiResult.response : "";
    const rawToolCalls = Array.isArray(aiResult?.tool_calls) ? aiResult.tool_calls : [];

    const message = {
      role: "assistant",
      content: responseText || null,
    };

    if (rawToolCalls.length > 0) {
      message.tool_calls = rawToolCalls.map((tc, i) => ({
        id: `call_${i}_${Date.now()}`,
        type: "function",
        function: {
          name: tc.name || "",
          arguments: JSON.stringify(tc.arguments || tc.input || {}),
        },
      }));
    }

    const openaiResponse = {
      id: `chatcmpl-${crypto.randomUUID()}`,
      object: "chat.completion",
      created: Math.floor(Date.now() / 1000),
      model,
      choices: [
        {
          index: 0,
          message,
          finish_reason: rawToolCalls.length > 0 ? "tool_calls" : "stop",
        },
      ],
      usage: {
        prompt_tokens: 0,
        completion_tokens: 0,
        total_tokens: 0,
      },
    };

    return new Response(JSON.stringify(openaiResponse), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function jsonError(status, message) {
  return new Response(
    JSON.stringify({ error: { message, type: "slowbooks_gateway_error" } }),
    {
      status,
      headers: { "content-type": "application/json" },
    }
  );
}

/**
 * Constant-time string compare to avoid timing side-channels on the
 * shared secret.
 */
function constantTimeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}
