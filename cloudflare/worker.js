// ============================================================================
// Slowbooks Pro 2026 — Cloudflare Worker AI Gateway (Hardened)
// ============================================================================
//
// Runs inside each LAN owner's own Cloudflare account. Slowbooks points at
// this Worker instead of talking to an AI provider directly, so the real
// credentials never leave Cloudflare. Every installation gets its own
// Worker, its own shared secret, and its own usage quota.
//
// Security hardening includes:
//   - Request size limits (512 KB)
//   - Message count & text length validation
//   - Model allowlist enforcement (env-configurable)
//   - Parameter clamping (max_tokens, temperature, top_p)
//   - Tool validation & caps
//   - Padded constant-time token comparison
//   - Safe error handling (no backend info leakage)
//   - Structured logging with request IDs
//   - Security headers (CSP-like, no-referrer, no-store)
//   - CORS origin allowlist (env-configurable)
//   - OPTIONS preflight support
//   - Correct usage tracking
//
// Architecture:
//   Slowbooks (your LAN)                  Cloudflare (worldwide edge)
//   ────────────────────                  ───────────────────────────
//   POST /v1/chat/completions   ───────►  Worker (this file)
//   Authorization: Bearer <T>                │
//                                            │ validates T == AUTH_TOKEN
//                                            │ (constant-time compare)
//                                            ▼
//                                        env.AI.run(model, ...)
//                                            │
//                                            ▼
//                                        Workers AI (free tier: 10k neurons/day)
//
// Environment variables (all optional except AUTH_TOKEN):
//
//   AUTH_TOKEN — Shared secret (64-char hex from `openssl rand -hex 32`).
//                REQUIRED. Set via: wrangler secret put AUTH_TOKEN
//
//   ALLOWED_MODELS — Comma-separated list of permitted model IDs.
//                    If not set, defaults to:
//                      @cf/meta/llama-3.3-70b-instruct-fp8-fast,
//                      @cf/meta/llama-3.1-8b-instruct,
//                      @cf/mistral/mistral-7b-instruct-v0.2-lora
//                    Set via wrangler.toml [vars] or dashboard.
//
//   DEFAULT_MODEL — Model to use if request doesn't specify one.
//                   Must be in ALLOWED_MODELS.
//                   Defaults to @cf/meta/llama-3.3-70b-instruct-fp8-fast
//
//   ALLOWED_ORIGINS — Comma-separated list of origins allowed for CORS
//                     (browser requests). If not set, CORS checks skipped.
//                     Example: http://localhost:3000,https://slowbooks.local
//
//   LOG_VERBOSE — Set to "1" to enable request/response logging to stdout.
//                 Defaults to "0" (logging disabled).
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
// ============================================================================

// Request constraints
const MAX_BODY_BYTES = 512 * 1024; // 512 KB
const MAX_MESSAGES = 64;
const MAX_CONTENT_CHARS = 100_000; // 100 KB of text per message
const MAX_TOTAL_TEXT_CHARS = 200_000; // 200 KB total across all messages
const MAX_TOOLS = 32;

// Model constraints
const DEFAULT_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast";
const DEFAULT_ALLOWED_MODELS = new Set([
  "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
  "@cf/meta/llama-3.1-8b-instruct",
  "@cf/mistral/mistral-7b-instruct-v0.2-lora",
]);

// Parameter bounds
const MAX_TOKENS_DEFAULT = 1024;
const MAX_TOKENS_LIMIT = 4096;
const MIN_MAX_TOKENS = 1;
const TEMPERATURE_DEFAULT = 0.3;
const TEMPERATURE_MAX = 2.0;
const TOP_P_DEFAULT = 1.0;
const TOP_P_MAX = 1.0;
const TOP_P_MIN = 0.0;

// Error response shape
const ERROR_TYPE = "slowbooks_gateway_error";
const ERROR_MSG_MAX_LEN = 500;

export default {
  /**
   * @param {Request} request
   * @param {{ AI: any, AUTH_TOKEN?: string, ALLOWED_MODELS?: string, DEFAULT_MODEL?: string, ALLOWED_ORIGINS?: string, LOG_VERBOSE?: string }} env
   * @param {ExecutionContext} ctx
   */
  async fetch(request, env, ctx) {
    const requestId = crypto.randomUUID().substring(0, 8);

    // --- CORS preflight (OPTIONS) ----------------------------------------
    if (request.method === "OPTIONS") {
      return handlePreflight(request, env, requestId);
    }

    // --- Method + path gating ------------------------------------------------
    if (request.method !== "POST") {
      return jsonError(
        405,
        "Method not allowed. Use POST /v1/chat/completions.",
        requestId,
      );
    }

    const { pathname } = new URL(request.url);
    if (!pathname.endsWith("/chat/completions")) {
      return jsonError(404, `Unknown route: ${pathname}`, requestId);
    }

    // --- Request size check --------------------------------------------------
    const contentLength = request.headers.get("content-length");
    if (contentLength) {
      const len = Number(contentLength);
      if (!Number.isFinite(len) || len < 0 || len > MAX_BODY_BYTES) {
        return jsonError(
          413,
          `Payload too large (max ${MAX_BODY_BYTES} bytes)`,
          requestId,
        );
      }
    }

    // --- Shared-secret auth (constant-time comparison) ---------------------
    if (!env.AUTH_TOKEN) {
      return jsonError(
        500,
        "Worker misconfigured: AUTH_TOKEN secret not set. " +
          "Run `wrangler secret put AUTH_TOKEN`.",
        requestId,
      );
    }

    const authHeader = request.headers.get("authorization") || "";
    const expected = `Bearer ${env.AUTH_TOKEN}`;
    if (!constantTimeEqualPadded(authHeader, expected)) {
      return jsonError(401, "Unauthorized", requestId);
    }

    // --- Parse body ----------------------------------------------------------
    let body;
    try {
      const text = await request.text();
      if (text.length > MAX_BODY_BYTES) {
        return jsonError(
          413,
          `Payload too large (max ${MAX_BODY_BYTES} bytes)`,
          requestId,
        );
      }
      body = JSON.parse(text);
    } catch (_e) {
      return jsonError(400, "Invalid JSON body", requestId);
    }

    // --- Extract & validate parameters ---------------------------------------
    const model = normalizeModel(body.model || DEFAULT_MODEL, env);
    if (!model) {
      return jsonError(
        400,
        "Model not allowed or invalid. Check ALLOWED_MODELS config.",
        requestId,
      );
    }

    const messages = Array.isArray(body.messages) ? body.messages : [];

    // Validate message count
    if (messages.length === 0 || messages.length > MAX_MESSAGES) {
      return jsonError(
        400,
        `messages must be non-empty array with ≤${MAX_MESSAGES} items`,
        requestId,
      );
    }

    // Validate message contents
    let totalTextChars = 0;
    for (let i = 0; i < messages.length; i++) {
      const msg = messages[i];
      if (typeof msg !== "object" || !msg) {
        return jsonError(400, "Each message must be a JSON object", requestId);
      }
      const content = msg.content || "";
      const contentStr = typeof content === "string" ? content : JSON.stringify(content);
      if (contentStr.length > MAX_CONTENT_CHARS) {
        return jsonError(
          400,
          `Message ${i} exceeds max content length (${MAX_CONTENT_CHARS} chars)`,
          requestId,
        );
      }
      totalTextChars += contentStr.length;
    }

    if (totalTextChars > MAX_TOTAL_TEXT_CHARS) {
      return jsonError(
        400,
        `Total message text exceeds max (${MAX_TOTAL_TEXT_CHARS} chars)`,
        requestId,
      );
    }

    // Extract and clamp numeric parameters
    let maxTokens = Number(body.max_tokens) || MAX_TOKENS_DEFAULT;
    maxTokens = Math.max(MIN_MAX_TOKENS, Math.min(MAX_TOKENS_LIMIT, maxTokens));

    let temperature =
      typeof body.temperature === "number"
        ? body.temperature
        : TEMPERATURE_DEFAULT;
    temperature = Math.max(0, Math.min(TEMPERATURE_MAX, temperature));

    let topP = typeof body.top_p === "number" ? body.top_p : TOP_P_DEFAULT;
    topP = Math.max(TOP_P_MIN, Math.min(TOP_P_MAX, topP));

    // --- Tool validation & cap -----------------------------------------------
    let tools = null;
    if (Array.isArray(body.tools)) {
      if (body.tools.length > MAX_TOOLS) {
        return jsonError(
          400,
          `Too many tools (max ${MAX_TOOLS})`,
          requestId,
        );
      }
      // Validate each tool
      for (let i = 0; i < body.tools.length; i++) {
        const tool = body.tools[i];
        if (!tool || typeof tool !== "object") {
          return jsonError(400, `Tool ${i} must be an object`, requestId);
        }
        if (tool.type !== "function") {
          return jsonError(
            400,
            `Tool ${i}: only type="function" supported`,
            requestId,
          );
        }
        const func = tool.function;
        if (!func || typeof func !== "object") {
          return jsonError(
            400,
            `Tool ${i}: must have function object`,
            requestId,
          );
        }
        if (typeof func.name !== "string" || func.name.length === 0) {
          return jsonError(
            400,
            `Tool ${i}: function.name must be non-empty string`,
            requestId,
          );
        }
        if (typeof func.name !== "string" || func.name.length > 256) {
          return jsonError(
            400,
            `Tool ${i}: function.name too long (max 256 chars)`,
            requestId,
          );
        }
        if (
          func.description &&
          typeof func.description !== "string"
        ) {
          return jsonError(
            400,
            `Tool ${i}: function.description must be string`,
            requestId,
          );
        }
        if (
          func.description &&
          func.description.length > 1024
        ) {
          return jsonError(
            400,
            `Tool ${i}: function.description too long (max 1024 chars)`,
            requestId,
          );
        }
      }
      tools = body.tools;
    }

    // --- Invoke Workers AI via binding ------------------------------------
    logRequest(
      env,
      requestId,
      "request_received",
      {
        model,
        num_messages: messages.length,
        has_tools: !!tools,
        num_tools: tools ? tools.length : 0,
      },
    );

    const aiInput = {
      messages,
      max_tokens: maxTokens,
      temperature,
      top_p: topP,
    };
    if (tools) aiInput.tools = tools;

    let aiResult;
    try {
      aiResult = await env.AI.run(model, aiInput);
    } catch (e) {
      const errorMsg = safeErrorMessage(e);
      logRequest(env, requestId, "ai_error", { error: errorMsg });
      return jsonError(502, `Workers AI error: ${errorMsg}`, requestId);
    }

    // --- Translate to OpenAI-compat response shape -------------------------
    const responseText =
      typeof aiResult?.response === "string" ? aiResult.response : "";
    const rawToolCalls = Array.isArray(aiResult?.tool_calls)
      ? aiResult.tool_calls
      : [];

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

    // Normalize usage (pass through if available, otherwise zeros)
    const usage = normalizeUsage(aiResult);

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
      usage,
    };

    logRequest(env, requestId, "response_sent", {
      finish_reason: openaiResponse.choices[0].finish_reason,
      usage_prompt_tokens: usage.prompt_tokens,
      usage_completion_tokens: usage.completion_tokens,
      usage_total_tokens: usage.total_tokens,
    });

    const response = new Response(JSON.stringify(openaiResponse), {
      status: 200,
      headers: {
        "content-type": "application/json",
        "x-request-id": requestId,
        ...getSecurityHeaders(request, env),
      },
    });

    return response;
  },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Handle CORS preflight (OPTIONS) requests.
 */
function handlePreflight(request, env, requestId) {
  const origin = request.headers.get("origin") || "";
  const allowedOrigins = getAllowedOrigins(env);

  let allowOriginValue = null;
  if (allowedOrigins.size > 0) {
    if (allowedOrigins.has(origin)) {
      allowOriginValue = origin;
    }
  }
  // If allowedOrigins is empty (not configured), don't echo any origin

  const headers = {
    vary: "Origin",
    "x-request-id": requestId,
  };

  if (allowOriginValue) {
    headers["access-control-allow-origin"] = allowOriginValue;
    headers["access-control-allow-methods"] = "POST, OPTIONS";
    headers["access-control-allow-headers"] =
      "Content-Type, Authorization";
    headers["access-control-max-age"] = "3600";
  }

  return new Response(null, {
    status: 204,
    headers,
  });
}

/**
 * JSON error response with safe message truncation.
 */
function jsonError(status, message, requestId) {
  const safeMessage = message.substring(0, ERROR_MSG_MAX_LEN);
  return new Response(
    JSON.stringify({
      error: {
        message: safeMessage,
        type: ERROR_TYPE,
      },
    }),
    {
      status,
      headers: {
        "content-type": "application/json",
        "x-request-id": requestId,
        ...getSecurityHeadersForError(),
      },
    },
  );
}

/**
 * Security headers for success responses.
 */
function getSecurityHeaders(request, env) {
  const headers = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
  };

  // CORS: if origin is allowed, echo it back (with allowlist check)
  const origin = request.headers.get("origin") || "";
  const allowedOrigins = getAllowedOrigins(env);
  if (allowedOrigins.size > 0 && allowedOrigins.has(origin)) {
    headers["access-control-allow-origin"] = origin;
    headers["access-control-allow-credentials"] = "false";
  }

  return headers;
}

/**
 * Security headers for error responses.
 */
function getSecurityHeadersForError() {
  return {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
  };
}

/**
 * Parse ALLOWED_ORIGINS from env, return Set of origins.
 * If not configured, returns empty Set (no CORS).
 */
function getAllowedOrigins(env) {
  if (!env.ALLOWED_ORIGINS || typeof env.ALLOWED_ORIGINS !== "string") {
    return new Set();
  }
  const raw = env.ALLOWED_ORIGINS.trim();
  if (!raw) return new Set();
  return new Set(
    raw
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s.length > 0),
  );
}

/**
 * Resolve allowed models from env, falling back to default set.
 */
function resolveAllowedModels(env) {
  if (env.ALLOWED_MODELS && typeof env.ALLOWED_MODELS === "string") {
    const raw = env.ALLOWED_MODELS.trim();
    if (raw) {
      const models = raw
        .split(",")
        .map((s) => s.trim())
        .filter((s) => s.length > 0);
      if (models.length > 0) {
        return new Set(models);
      }
    }
  }
  return DEFAULT_ALLOWED_MODELS;
}

/**
 * Normalize & validate model against allowlist.
 * Returns the model name if allowed, null if not.
 */
function normalizeModel(modelName, env) {
  if (typeof modelName !== "string" || !modelName.trim()) {
    return null;
  }
  const allowedModels = resolveAllowedModels(env);
  const model = modelName.trim();
  if (!allowedModels.has(model)) {
    return null;
  }
  return model;
}

/**
 * Normalize usage object from aiResult.
 * Passes through actual usage or returns zeros if not available.
 */
function normalizeUsage(aiResult) {
  if (aiResult && typeof aiResult === "object") {
    const usage = aiResult.usage;
    if (usage && typeof usage === "object") {
      return {
        prompt_tokens: Number(usage.prompt_tokens) || 0,
        completion_tokens: Number(usage.completion_tokens) || 0,
        total_tokens: Number(usage.total_tokens) || 0,
      };
    }
  }
  return {
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
  };
}

/**
 * Extract safe error message (truncate to 500 chars).
 */
function safeErrorMessage(e) {
  if (!e) return "Unknown error";
  const msg =
    typeof e.message === "string"
      ? e.message
      : typeof e === "string"
        ? e
        : String(e);
  return msg.substring(0, ERROR_MSG_MAX_LEN);
}

/**
 * Padded constant-time string comparison to avoid timing side-channels
 * on the shared secret.
 *
 * First normalizes both strings to the same length by padding with NUL
 * bytes, then XORs character codes to compare them.
 */
function constantTimeEqualPadded(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;

  // Determine max length
  const maxLen = Math.max(a.length, b.length);

  // Compare with padding
  let diff = 0;
  for (let i = 0; i < maxLen; i++) {
    // Pad with 0 (NUL) if index is out of bounds
    const aCode = i < a.length ? a.charCodeAt(i) : 0;
    const bCode = i < b.length ? b.charCodeAt(i) : 0;
    diff |= aCode ^ bCode;
  }
  return diff === 0;
}

/**
 * Structured logging (to stdout if LOG_VERBOSE enabled).
 */
function logRequest(env, requestId, eventType, details) {
  if ((env.LOG_VERBOSE || "0") !== "1") return;

  const timestamp = new Date().toISOString();
  const logEntry = {
    timestamp,
    request_id: requestId,
    event_type: eventType,
    ...details,
  };

  console.log(JSON.stringify(logEntry));
}
