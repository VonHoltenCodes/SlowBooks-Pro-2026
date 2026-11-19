// Run auth.js's promptAuth() against a given /api/auth/status answer, on a
// minimal fake DOM, and print which screen it painted and what the status
// bar says behind it.
//
//   node tests/js/auth_prompt_probe.js '{"status": {...}}'
const fs = require('fs'), vm = require('vm');

const scenario = JSON.parse(process.argv[2]);

function fakeEl(tag) {
  return {
    tagName: tag,
    id: '',
    children: [],
    textContent: '',
    innerHTML: '',
    removed: false,
    setAttribute(k, v) { if (k === 'id') this.id = v; },
    appendChild(c) { this.children.push(c); return c; },
    remove() { this.removed = true; },
    querySelector() { return fakeEl('stub'); },
    addEventListener() {},
    focus() {},
  };
}

const body = fakeEl('body');
const statusText = fakeEl('span');
const statusCompany = fakeEl('span');
statusText.textContent = 'Error loading page';
statusCompany.textContent = 'Company: bookkeeper.sbk';

const ctx = {
  console,
  // utils.js's escapeHtml, which hint.js uses on the page (#977)
  escapeHtml: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'),
  window: { addEventListener() {} },
  fetch: async () => ({ ok: true, json: async () => scenario.status }),
  document: {
    body,
    createElement: fakeEl,
    addEventListener() {},
    getElementById(id) {
      if (id === 'status-text') return statusText;
      if (id === 'status-company') return statusCompany;
      return body.children.find(c => c.id === id && !c.removed) || null;
    },
  },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/hint.js', 'utf8'), ctx);  // hint(), hintList() (#977)
vm.runInContext(fs.readFileSync('app/static/js/auth.js', 'utf8'), ctx);

(async () => {
  const auth = ctx.window.SlowbooksAuth;
  // Two callers at once (the boot check and a page's first request) share
  // one status check and one overlay.
  const [a, b] = await Promise.all([auth.promptAuth(), auth.promptAuth()]);
  const overlays = body.children.filter(c => !c.removed);
  const html = overlays.length ? overlays[0].innerHTML : '';
  const screen = html.includes('id="auth-password-confirm"') ? 'setup'
    : html.includes('id="auth-title"') ? 'login' : 'none';
  const again = await auth.promptAuth();
  console.log(JSON.stringify({
    results: [a, b, again],
    overlays: overlays.length,
    screen,
    html,
    status_text: statusText.textContent,
    status_company: statusCompany.textContent,
  }));
})();
