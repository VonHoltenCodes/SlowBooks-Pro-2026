// The vendor form's 1099 Type select: rendered off unless the vendor is a
// 1099 vendor, and switched by the 1099 Vendor answer. Prints JSON:
// {"new": <select tag>, "not1099": <tag>, "is1099": <tag>, "toggle": [...]}.
const fs = require('fs'), vm = require('vm');

let modalHtml = '';
const vendors = {
  7: { id: 7, name: 'Paper Co', is_1099_vendor: false, vendor_1099_type: null, country: 'US' },
  8: { id: 8, name: 'Jo Designer', is_1099_vendor: true, vendor_1099_type: 'NEC', country: 'US' },
};
const ctx = {
  console,
  window: {},
  escapeHtml: (s) => String(s ?? ''),
  countryOptions: () => '<option>US</option>',
  openModal: (_title, html) => { modalHtml = html; },
  API: {
    get: async (path) => {
      if (path === '/accounts') return [];
      const m = /^\/vendors\/(\d+)$/.exec(path);
      if (m) return vendors[m[1]];
      throw new Error('unexpected ' + path);
    },
  },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/vendors.js', 'utf8') + '\nthis.VendorsPage = VendorsPage;', ctx);
const V = ctx.VendorsPage;

const typeTag = () => (/<select name="vendor_1099_type"[^>]*>/.exec(modalHtml) || [''])[0];
const answerTag = () => (/<select name="is_1099_vendor"[^>]*>/.exec(modalHtml) || [''])[0];

(async () => {
  const out = {};
  await V.showForm();
  out.new = typeTag();
  out.answer = answerTag();
  await V.showForm(7);
  out.not1099 = typeTag();
  await V.showForm(8);
  out.is1099 = typeTag();

  // The switch: Yes turns the type on; No turns it off and empties it.
  const type = { disabled: true, value: '' };
  const form = { elements: { namedItem: (n) => (n === 'vendor_1099_type' ? type : null) } };
  const answer = { form, value: 'true' };
  const steps = [];
  V.toggle1099Type(answer);
  steps.push({ answer: 'Yes', disabled: type.disabled, value: type.value });
  type.value = 'MISC';
  answer.value = 'false';
  V.toggle1099Type(answer);
  steps.push({ answer: 'No', disabled: type.disabled, value: type.value });
  out.toggle = steps;
  console.log(JSON.stringify(out));
})();
