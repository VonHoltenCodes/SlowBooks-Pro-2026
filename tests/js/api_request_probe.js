// Drive api.js's API.request against canned server responses and print, as
// JSON, what the calling page would get back and what was sent.
//
//   node tests/js/api_request_probe.js '<scenario json>'
//
// scenario = {
//   "call": ["POST", "/invoices", {...body}],
//   "responses": [{"status": 403, "body": {...}, "headers": {...}}, ...],
//   "answers": ["pw", null],  // what the closing-date prompt returns, in turn
//   "auth": true              // what SlowbooksAuth.promptAuth() resolves to
// }
// A call still unsettled after a moment is reported as {"pending": true}.
const fs = require('fs'), vm = require('vm');

const scenario = JSON.parse(process.argv[2]);
const responses = (scenario.responses || []).slice();
const answers = (scenario.answers || []).slice();
const requests = [];
const prompts = [];

function makeResponse(r) {
  const headers = {};
  Object.entries(r.headers || {}).forEach(([k, v]) => { headers[k.toLowerCase()] = String(v); });
  return {
    status: r.status,
    ok: r.status >= 200 && r.status < 300,
    statusText: r.statusText || '',
    headers: { get: (k) => (k.toLowerCase() in headers ? headers[k.toLowerCase()] : null) },
    json: async () => r.body,
  };
}

const ctx = {
  console,
  URLSearchParams,
  setTimeout,
  localStorage: { getItem: () => null },
  window: {},
  fetch: async (url, opts) => {
    requests.push({ url, method: opts.method, headers: Object.assign({}, opts.headers), body: opts.body || null });
    const r = responses.shift();
    if (!r) throw new Error('the probe ran out of responses');
    return makeResponse(r);
  },
};
let authPrompts = 0;
if ('auth' in scenario) {
  ctx.window.SlowbooksAuth = { promptAuth: async () => { authPrompts++; return scenario.auth; } };
}
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/api.js', 'utf8') + '\nthis.API = API;', ctx);

// The in-page password dialog, answered from the scenario.
ctx.API.askClosingDatePassword = async (message, wrong) => {
  prompts.push({ message, wrong: !!wrong });
  return answers.length ? answers.shift() : null;
};

(async () => {
  const [method, path, body] = scenario.call;
  const out = { requests, prompts };
  const PENDING = {};
  try {
    const call = ctx.API.request(method, path, body || null);
    const value = await Promise.race([call, new Promise(r => setTimeout(() => r(PENDING), 300))]);
    if (value === PENDING) out.pending = true;
    else out.value = value;
  } catch (e) {
    out.error = { message: e.message, status: e.status === undefined ? null : e.status };
  }
  if ('auth' in scenario) out.auth_prompts = authPrompts;
  console.log(JSON.stringify(out));
})();
