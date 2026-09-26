// Evaluate invoices.js with stubs and run SalesLines.sendAllowingZero and
// InvoicesPage.duplicate the way the pages do when the server says a
// document adds up to $0.00. Prints one JSON object for the pytest side.
const fs = require('fs'), vm = require('vm');

const asked = [];
let answer = true;
const toasts = [];
const bodies = [];
let refuseFirst = true;

function zeroTotal(question) {
  const err = new Error('This invoice adds up to $0.00. Enter a rate on at least one line before saving it.');
  err.status = 409;
  err.detail = { code: 'zero_total', message: err.message, question };
  return err;
}

const ctx = {
  console,
  window: {},
  location: { hash: '#/invoices' },
  App: { settings: { home_currency: 'USD' }, navigate: () => {} },
  T: (s) => s,
  formatCurrency: (n) => '$' + (Number(n) || 0).toFixed(2),
  confirm: (msg) => { asked.push(msg); return answer; },
  toast: (msg) => { toasts.push(msg); },
  closeModal: () => {},
  API: {
    post: async (path, body) => {
      bodies.push(body === undefined ? null : body);
      if (refuseFirst && !(body && body.allow_zero_total)) {
        throw zeroTotal('This invoice adds up to $0.00. Duplicate it anyway?');
      }
      return { id: 2, invoice_number: '1002' };
    },
  },
};
vm.createContext(ctx);
vm.runInContext(
  fs.readFileSync('app/static/js/invoices.js', 'utf8')
    + '\nthis.SalesLines = SalesLines; this.InvoicesPage = InvoicesPage;',
  ctx,
);
const S = ctx.SalesLines, P = ctx.InvoicesPage;

(async () => {
  const out = {};

  // yes
  let sent = [];
  answer = true;
  let result = await S.sendAllowingZero(async (allow) => {
    sent.push(allow);
    if (!allow) throw zeroTotal('This invoice adds up to $0.00. Duplicate it anyway?');
    return 'INV-2';
  });
  out.yes = { asked: asked.splice(0), sent, result };

  // no
  sent = [];
  answer = false;
  result = await S.sendAllowingZero(async (allow) => {
    sent.push(allow);
    throw zeroTotal('This invoice adds up to $0.00. Save it anyway?');
  });
  asked.splice(0);
  out.no = { sent, result };

  // another refusal is passed on untouched
  answer = true;
  try {
    await S.sendAllowingZero(async () => {
      const err = new Error('Customer not found');
      err.status = 404;
      err.detail = 'Customer not found';
      throw err;
    });
    out.other = { asked: asked.splice(0), error: null };
  } catch (e) {
    out.other = { asked: asked.splice(0), error: e.message };
  }

  // the invoice view's Duplicate button
  answer = true;
  await P.duplicate(1);
  out.duplicate = { bodies: bodies.splice(0), toasts: toasts.splice(0) };

  console.log(JSON.stringify(out));
})();
