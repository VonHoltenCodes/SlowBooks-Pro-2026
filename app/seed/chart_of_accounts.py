# ============================================================================
# Default Chart of Accounts — the entries QuickBooks 2003 Pro created when
# you ran the "EasyStep Interview" for a new company file. Account numbers
# match its "Contractor" industry template.
#
# "detail_type" (#390) is given where the type's default is wrong for the
# account; every other account takes the default (Bank for a bank_kind,
# AR / AP on 1100 / 2000, else Other Current Asset, Expense, ...).
# ============================================================================

CHART_OF_ACCOUNTS = [
    # Assets (1000s)
    {
        "account_number": "1000",
        "name": "Checking",
        "account_type": "asset",
        "bank_kind": "bank",
    },
    {
        "account_number": "1010",
        "name": "Savings",
        "account_type": "asset",
        "bank_kind": "bank",
    },
    {"account_number": "1100", "name": "Accounts Receivable", "account_type": "asset"},
    {"account_number": "1200", "name": "Undeposited Funds", "account_type": "asset"},
    {"account_number": "1300", "name": "Inventory", "account_type": "asset"},
    {"account_number": "1400", "name": "Prepaid Expenses", "account_type": "asset"},
    {
        "account_number": "1500",
        "name": "Equipment",
        "account_type": "asset",
        "detail_type": "fixed_asset",
    },
    {
        "account_number": "1510",
        "name": "Accumulated Depreciation",
        "account_type": "asset",
        "detail_type": "fixed_asset",
    },
    {
        "account_number": "1600",
        "name": "Vehicles",
        "account_type": "asset",
        "detail_type": "fixed_asset",
    },
    {
        "account_number": "1700",
        "name": "Other Assets",
        "account_type": "asset",
        "detail_type": "other_asset",
    },
    # Liabilities (2000s)
    {"account_number": "2000", "name": "Accounts Payable", "account_type": "liability"},
    {
        "account_number": "2100",
        "name": "Credit Card",
        "account_type": "liability",
        "bank_kind": "credit_card",
    },
    {
        "account_number": "2200",
        "name": "Sales Tax Payable",
        "account_type": "liability",
    },
    {
        "account_number": "2300",
        "name": "Payroll Liabilities",
        "account_type": "liability",
    },
    {
        "account_number": "2310",
        "name": "Federal Income Tax Payable",
        "account_type": "liability",
    },
    {
        "account_number": "2320",
        "name": "State Income Tax Payable",
        "account_type": "liability",
    },
    {
        "account_number": "2330",
        "name": "Social Security Payable",
        "account_type": "liability",
    },
    {"account_number": "2340", "name": "Medicare Payable", "account_type": "liability"},
    {"account_number": "2350", "name": "FUTA Payable", "account_type": "liability"},
    {
        "account_number": "2360",
        "name": "State Unemployment (SUTA) Payable",
        "account_type": "liability",
    },
    {
        "account_number": "2370",
        "name": "Other Payroll Deductions Payable",
        "account_type": "liability",
    },
    {
        "account_number": "2380",
        "name": "Employee Benefits Payable",
        "account_type": "liability",
    },
    {
        "account_number": "2390",
        "name": "Accrued PTO Liability",
        "account_type": "liability",
    },
    {
        "account_number": "2400",
        "name": "Loan Payable",
        "account_type": "liability",
        "detail_type": "long_term_liability",
    },
    {
        "account_number": "2500",
        "name": "Other Current Liabilities",
        "account_type": "liability",
    },
    # Equity (3000s) — the sole-proprietor block. EQUITY_SEEDS below holds the
    # partnership and corporation blocks; chart_for_entity() swaps them in.
    {"account_number": "3000", "name": "Owner's Equity", "account_type": "equity"},
    {"account_number": "3100", "name": "Owner's Draw", "account_type": "equity"},
    {"account_number": "3200", "name": "Retained Earnings", "account_type": "equity"},
    # Income (4000s)
    {"account_number": "4000", "name": "Service Income", "account_type": "income"},
    {"account_number": "4100", "name": "Product Sales", "account_type": "income"},
    {"account_number": "4200", "name": "Material Income", "account_type": "income"},
    {"account_number": "4300", "name": "Labor Income", "account_type": "income"},
    # 4400 In-Kind Contributions, like the net-asset accounts, is a nonprofit
    # account: it is created when the company switches to nonprofit, or by
    # the first in-kind gift (accounting.ensure_nonprofit_accounts), never
    # seeded into a business chart.
    {
        "account_number": "4900",
        "name": "Other Income",
        "account_type": "income",
        "detail_type": "other_income",
    },
    # COGS (5000s)
    {"account_number": "5000", "name": "Cost of Goods Sold", "account_type": "cogs"},
    {"account_number": "5100", "name": "Materials Cost", "account_type": "cogs"},
    {"account_number": "5200", "name": "Labor Cost", "account_type": "cogs"},
    {"account_number": "5300", "name": "Subcontractor Costs", "account_type": "cogs"},
    # Expenses (6000s)
    {
        "account_number": "6000",
        "name": "Advertising & Marketing",
        "account_type": "expense",
    },
    {
        "account_number": "6100",
        "name": "Auto & Truck Expense",
        "account_type": "expense",
    },
    {"account_number": "6110", "name": "Wages & Salaries", "account_type": "expense"},
    {
        "account_number": "6120",
        "name": "Payroll Tax Expense",
        "account_type": "expense",
    },
    {
        "account_number": "6130",
        "name": "Workers Compensation Insurance",
        "account_type": "expense",
    },
    {
        "account_number": "6140",
        "name": "Employee Expense Reimbursements",
        "account_type": "expense",
    },
    {
        "account_number": "6150",
        "name": "Employee Benefits Expense",
        "account_type": "expense",
    },
    {
        "account_number": "6160",
        "name": "Paid Time Off Expense",
        "account_type": "expense",
    },
    {
        "account_number": "6200",
        "name": "Bank Charges & Fees",
        "account_type": "expense",
    },
    {"account_number": "6300", "name": "Insurance", "account_type": "expense"},
    {"account_number": "6400", "name": "Office Supplies", "account_type": "expense"},
    {"account_number": "6500", "name": "Rent or Lease", "account_type": "expense"},
    {
        "account_number": "6600",
        "name": "Repairs & Maintenance",
        "account_type": "expense",
    },
    {
        "account_number": "6700",
        "name": "Telephone & Internet",
        "account_type": "expense",
    },
    {"account_number": "6800", "name": "Tools & Equipment", "account_type": "expense"},
    {
        "account_number": "6810",
        "name": "Depreciation Expense",
        "account_type": "expense",
    },
    {"account_number": "6900", "name": "Utilities", "account_type": "expense"},
    {
        "account_number": "6950",
        "name": "Miscellaneous Expense",
        "account_type": "expense",
    },
    {"account_number": "6960", "name": "Bad Debt Expense", "account_type": "expense"},
]


# ============================================================================
# Entity-aware equity (#488, step 2). The equity block a company's legal
# form expects, keyed by family: a sole proprietor (and a single-member LLC
# taxed as one) keeps the block above; a partnership (and an LLC taxed as
# one) gets capital and distribution accounts per partner; a corporation
# (C corp, S corp, and an LLC taxed as either) gets stock, paid-in capital
# and shareholder distributions. 3200 Retained Earnings is a control account
# (control_accounts.py) and is in every block; 3900 Opening Balance Equity is
# created on demand and never seeded.
#
# Partnerships get two flat "Partner 1 / Partner 2" pairs rather than one
# Partners' Capital with sub-accounts: a Form 1065 Schedule K-1 and Schedule
# L want capital per partner, two is the smallest partnership, the names are
# placeholders to rename, and a third partner is one Add account away. Flat
# accounts keep the seed the shape of every other seeded account.
#
# A distributions account is "contra-equity" in the accountant's sense — an
# equity account that normally carries a debit balance — exactly as Owner's
# Draw does today; the chart has no separate contra flag.
EQUITY_SEEDS = {
    "sole_proprietor": [
        ("3000", "Owner's Equity"),
        ("3100", "Owner's Draw"),
        ("3200", "Retained Earnings"),
    ],
    "partnership": [
        ("3000", "Partner 1 Capital"),
        ("3010", "Partner 2 Capital"),
        ("3100", "Partner 1 Distributions"),
        ("3110", "Partner 2 Distributions"),
        ("3200", "Retained Earnings"),
    ],
    "corporation": [
        ("3000", "Common Stock"),
        ("3010", "Additional Paid-in Capital"),
        ("3100", "Shareholder Distributions"),
        ("3200", "Retained Earnings"),
    ],
}

# return_form (settings_service.entity_info) -> equity family. Form 990 and
# an unset entity have no family: the chart is left as it is.
_FAMILY_OF_RETURN = {
    "Schedule C": "sole_proprietor",
    "Form 1065": "partnership",
    "Form 1120": "corporation",
    "Form 1120-S": "corporation",
}


def equity_family(return_form: str | None) -> str:
    """The EQUITY_SEEDS key for the return a company files, or "" when the
    chart should not follow the entity (unset, an LLC with no election, a
    nonprofit on Form 990)."""
    return _FAMILY_OF_RETURN.get(return_form or "", "")


def equity_entries(family: str) -> list[dict]:
    """The equity block of ``family`` in the seed's own entry shape."""
    return [
        {"account_number": number, "name": name, "account_type": "equity"}
        for number, name in EQUITY_SEEDS[family]
    ]


def chart_for_entity(family: str) -> list[dict]:
    """CHART_OF_ACCOUNTS with its equity block replaced by ``family``'s; the
    chart itself for "" or an unknown family."""
    if family not in EQUITY_SEEDS:
        return list(CHART_OF_ACCOUNTS)
    out = []
    placed = False
    for entry in CHART_OF_ACCOUNTS:
        if entry["account_type"] != "equity":
            out.append(entry)
        elif not placed:
            out.extend(equity_entries(family))
            placed = True
    return out
