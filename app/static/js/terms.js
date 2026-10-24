/**
 * Terminology — one dictionary, applied at render.
 *
 * A nonprofit never sells anything, so "Customer", "Invoice" and "Profit &
 * Loss" are the wrong words on every screen. Settings -> Company Type
 * picks the vocabulary; T('Customer') returns 'Donor' for a nonprofit and
 * 'Customer' for everyone else. Nothing in the API changes name.
 *
 * Keys are ALWAYS the business words. This literal is parsed as JSON by
 * tests/test_terminology.py and compared with app/services/terminology.py,
 * so keep it double-quoted and free of trailing commas.
 */
const TERMS_NONPROFIT = {
    "Customer": "Donor",
    "Customers": "Donors",
    "Customer Center": "Donor Center",
    "New Customer": "New Donor",
    "Active Customers": "Active Donors",
    "Customer Statement": "Donor Statement",
    "Customers & Sales": "Donors & Contributions",
    "Income by Customer": "Contributions by Donor",
    "Invoice": "Pledge",
    "Invoices": "Pledges",
    "Create Invoice": "Create Pledge",
    "Create Invoices": "Create Pledges",
    "New Invoice": "New Pledge",
    "Recurring Invoices": "Recurring Pledges",
    "Overdue Invoices": "Overdue Pledges",
    "Recent Invoices": "Recent Pledges",
    "Total Invoiced": "Total Pledged",
    "Sales Receipt": "Donation",
    "Sales Receipts": "Donations",
    "Enter Sales Receipts": "Enter Donations",
    "Income": "Revenue & Support",
    "Total Income": "Total Revenue & Support",
    "Other Income": "Other Revenue & Support",
    "Total Other Income": "Total Other Revenue & Support",
    "Net Other Income": "Net Other Revenue & Support",
    "Net Operating Income": "Change in Net Assets from Operations",
    "Net Income": "Change in Net Assets",
    "Equity": "Net Assets",
    "Total Equity": "Total Net Assets",
    "Liabilities + Equity": "Liabilities + Net Assets",
    "Profit & Loss": "Statement of Activities",
    "P&L": "Activities",
    "P&L by Class": "Activities by Fund",
    "P&L: This Month vs Last": "Activities: This Month vs Last",
    "P&L: Year to Date": "Activities: Year to Date",
    "Balance Sheet": "Statement of Financial Position",
    "Balance Sheet Trend": "Statement of Financial Position Trend",
    "Class": "Fund",
    "Classes": "Funds",
    "Job": "Grant",
    "Jobs": "Grants",
    "Job Costs": "Grant Costs",
    "Job Cost Entries": "Grant Cost Entries",
    "Job Cost Entry": "Grant Cost Entry",
    "Job Budget vs Actual": "Grant Budget vs Actual",
    "Jobs: Budget vs Actual": "Grants: Budget vs Actual",
    "Job Profitability": "Grant Income & Costs",
    "Total Receivables": "Pledges Receivable",
    "A/R Aging": "Pledge Aging",
    "Accounts Receivable Aging": "Pledges Receivable Aging",
    "Monthly Revenue": "Monthly Revenue & Support",
    "Receivables": "Pledges Receivable",
    "Revenue by Customer": "Revenue & Support by Donor"
};

/**
 * A contractor keeps every business word (QuickBooks' own Contractor
 * Edition renames almost nothing); the one shorthand the trade speaks is
 * "Sub" for a subcontractor. Byte-identical with CONSTRUCTION in
 * app/services/terminology.py (tests/test_terminology.py checks).
 */
const TERMS_CONSTRUCTION = {
    "Subcontractor": "Sub",
    "Subcontractors": "Subs"
};

// The dictionary for each non-business company type; business has none.
const TERMS_DICTS = { nonprofit: TERMS_NONPROFIT, construction: TERMS_CONSTRUCTION };

const Terms = {
    mode: 'business',
    _re: {},

    init(settings) {
        const type = settings && settings.company_type;
        // own-key check: `'constructor' in TERMS_DICTS` is true by prototype
        const known = type && Object.prototype.hasOwnProperty.call(TERMS_DICTS, type);
        Terms.mode = known ? type : 'business';
    },

    isNonprofit() { return Terms.mode === 'nonprofit'; },

    isConstruction() { return Terms.mode === 'construction'; },

    _dict() { return TERMS_DICTS[Terms.mode] || null; },

    /**
     * Prose helper for sentences (empty states, hints, placeholders):
     * whole-word, longest key first, keeps the case of what it matched.
     * For labels and headings use T() directly.
     */
    text(s) {
        const d = Terms._dict();
        if (!d || !s) return s;
        if (!Terms._re[Terms.mode]) {
            const keys = Object.keys(d).sort((a, b) => b.length - a.length)
                .map(k => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
            Terms._re[Terms.mode] = new RegExp('\\b(' + keys.join('|') + ')\\b', 'gi');
        }
        return s.replace(Terms._re[Terms.mode], (found) => {
            const exact = Object.keys(d).find(k => k.toLowerCase() === found.toLowerCase());
            const out = exact ? d[exact] : found;
            // an all-caps KEY (P&L, A/R) is not a shouted sentence
            if (found === found.toUpperCase() && !(exact && exact === exact.toUpperCase())) return out.toUpperCase();
            // a lower-case word takes a lower-case phrase ("equity" -> "net assets",
            // not "net Assets"); an acronym inside the replacement keeps its capitals
            if (found[0] === found[0].toLowerCase()) {
                return out.split(' ').map(w => (w.length > 1 && w === w.toUpperCase() && /[A-Z]/.test(w)) ? w : w.toLowerCase()).join(' ');
            }
            return out;
        });
    },
};

/** T('Invoice') -> 'Pledge' in nonprofit mode; the key itself otherwise. */
function T(key) {
    const d = Terms._dict();
    if (!d || !key) return key;
    if (key in d) return d[key];
    if (key.endsWith('s') && key.slice(0, -1) in d) return d[key.slice(0, -1)] + 's';
    const cap = key[0].toUpperCase() + key.slice(1);
    if (cap !== key && (cap in d || (cap.endsWith('s') && cap.slice(0, -1) in d))) return T(cap).toLowerCase();
    return key;
}
