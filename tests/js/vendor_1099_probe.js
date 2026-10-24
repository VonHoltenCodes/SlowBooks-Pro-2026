// The vendor form's 1099 Type select: rendered off unless the vendor is a
// 1099 vendor, and switched by the 1099 Vendor answer. Prints JSON:
// {"new": <select tag>, "not1099": <tag>, "is1099": <tag>, "toggle": [...]}.
const fs = require('fs'), vm = require('vm');

let modalHtml = '';
let construction = false;
const vendors = {
  7: { id: 7, name: 'Paper Co', is_1099_vendor: false, vendor_1099_type: null, country: 'US' },
  8: { id: 8, name: 'Jo Designer', is_1099_vendor: true, vendor_1099_type: 'NEC', country: 'US' },
  9: { id: 9, name: 'Ace', vendor_type: 'sub', coi_carrier: '"><script>x</script>', country: 'US' },
};
const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ctx = {
  console,
  window: {},
  escapeHtml,
  // construction mode is flipped per-scenario below
  Terms: { isConstruction: () => construction, isNonprofit: () => false },
  T: (s) => s,
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

  // ----- construction mode: the Sub section and the 1099 flip -----
  construction = true;
  await V.showForm();        // a new vendor in construction mode
  out.subNew = modalHtml;
  await V.showForm(9);       // an existing sub with an unsafe COI carrier
  out.subEdit = modalHtml;

  // toggleSubFields('sub') turns 1099 on (NEC) and reveals the fields;
  // back to '' clears the four. A fake form stands in for the DOM.
  const fields = {
    is_1099_vendor: { value: 'false' },
    vendor_1099_type: { value: '', disabled: true },
    license_number: { value: 'EC-1' },
    coi_carrier: { value: 'Travelers' },
    coi_policy_number: { value: 'P-1' },
    coi_expires: { value: '2027-01-01' },
  };
  const subRows = [{ hidden: true }, { hidden: true }, { hidden: true }, { hidden: true }];
  const subForm = {
    elements: { namedItem: (n) => fields[n] || null },
    querySelectorAll: () => subRows,
  };
  fields.is_1099_vendor.form = subForm;
  V.toggleSubFields({ value: 'sub', form: subForm });
  const onState = {
    is_1099_vendor: fields.is_1099_vendor.value,
    vendor_1099_type: fields.vendor_1099_type.value,
    hidden: subRows.map((r) => r.hidden),
  };
  V.toggleSubFields({ value: '', form: subForm });
  const offState = {
    hidden: subRows.map((r) => r.hidden),
    cleared: [fields.license_number, fields.coi_carrier, fields.coi_policy_number, fields.coi_expires]
      .map((f) => f.value),
  };
  out.subToggle = { on: onState, off: offState };
  console.log(JSON.stringify(out));
})();
