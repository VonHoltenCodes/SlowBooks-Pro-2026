from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS as XERO_SAMPLE_DEFAULT_CHART


MAS_DEFAULT_CHART = [
    # Assets
    {"account_number": "1000", "name": "Checking", "account_type": "asset"},
    {"account_number": "1010", "name": "Savings", "account_type": "asset"},
    {"account_number": "1100", "name": "Accounts Receivable", "account_type": "asset"},
    {"account_number": "1200", "name": "Undeposited Funds", "account_type": "asset"},
    {"account_number": "1300", "name": "Inventory", "account_type": "asset"},
    {"account_number": "1400", "name": "Prepaid Expenses", "account_type": "asset"},
    {"account_number": "1500", "name": "Equipment", "account_type": "asset"},
    {"account_number": "1510", "name": "Accumulated Depreciation", "account_type": "asset"},
    {"account_number": "1600", "name": "Vehicles", "account_type": "asset"},
    # Liabilities
    {"account_number": "2000", "name": "Accounts Payable", "account_type": "liability"},
    {"account_number": "2100", "name": "Credit Card", "account_type": "liability"},
    {"account_number": "2200", "name": "GST", "account_type": "liability"},
    {"account_number": "2300", "name": "Wages Payable - Payroll", "account_type": "liability"},
    {"account_number": "2310", "name": "PAYE Payable", "account_type": "liability"},
    {"account_number": "2320", "name": "KiwiSaver Payable", "account_type": "liability"},
    {"account_number": "2330", "name": "ESCT Payable", "account_type": "liability"},
    {"account_number": "2340", "name": "Child Support Payable", "account_type": "liability"},
    {"account_number": "2400", "name": "Loan Payable", "account_type": "liability"},
    # Equity
    {"account_number": "3000", "name": "Owner's Equity", "account_type": "equity"},
    {"account_number": "3100", "name": "Owner's Draw", "account_type": "equity"},
    {"account_number": "3200", "name": "Retained Earnings", "account_type": "equity"},
    # Income
    {"account_number": "4000", "name": "Service Income", "account_type": "income"},
    {"account_number": "4100", "name": "Product Sales", "account_type": "income"},
    {"account_number": "4900", "name": "Other Income", "account_type": "income"},
    # COGS
    {"account_number": "5000", "name": "Cost of Goods Sold", "account_type": "cogs"},
    {"account_number": "5100", "name": "Materials Cost", "account_type": "cogs"},
    # Expenses
    {"account_number": "6000", "name": "Advertising & Marketing", "account_type": "expense"},
    {"account_number": "6200", "name": "Bank Charges & Fees", "account_type": "expense"},
    {"account_number": "6300", "name": "Insurance", "account_type": "expense"},
    {"account_number": "6400", "name": "Office Supplies", "account_type": "expense"},
    {"account_number": "6500", "name": "Rent or Lease", "account_type": "expense"},
    {"account_number": "6700", "name": "Telephone & Internet", "account_type": "expense"},
    {"account_number": "6800", "name": "Tools & Equipment", "account_type": "expense"},
    {"account_number": "6900", "name": "Utilities", "account_type": "expense"},
    {"account_number": "6950", "name": "General Expenses", "account_type": "expense"},
    {"account_number": "6970", "name": "Salaries", "account_type": "expense"},
    {"account_number": "6971", "name": "KiwiSaver Employer Contributions", "account_type": "expense"},
]


CHART_TEMPLATES = {
    "xero": {
        "label": "Xero sample default chart",
        "accounts": XERO_SAMPLE_DEFAULT_CHART,
        "system_account_numbers": {
            "system_account_default_bank_id": "090",
            "system_account_accounts_receivable_id": "610",
            "system_account_undeposited_funds_id": "615",
            "system_account_default_sales_income_id": "200",
            "system_account_default_expense_id": "429",
            "system_account_accounts_payable_id": "800",
            "system_account_payroll_clearing_id": "814",
            "system_account_gst_control_id": "820",
            "system_account_paye_payable_id": "825",
            "system_account_kiwisaver_payable_id": "826",
            "system_account_esct_payable_id": "827",
            "system_account_child_support_payable_id": "828",
            "system_account_wages_expense_id": "477",
            "system_account_employer_kiwisaver_expense_id": "478",
        },
    },
    "mas": {
        "label": "MAS chart of accounts",
        "accounts": MAS_DEFAULT_CHART,
        "system_account_numbers": {
            "system_account_default_bank_id": "1000",
            "system_account_accounts_receivable_id": "1100",
            "system_account_undeposited_funds_id": "1200",
            "system_account_default_sales_income_id": "4000",
            "system_account_default_expense_id": "6950",
            "system_account_accounts_payable_id": "2000",
            "system_account_payroll_clearing_id": "2300",
            "system_account_gst_control_id": "2200",
            "system_account_paye_payable_id": "2310",
            "system_account_kiwisaver_payable_id": "2320",
            "system_account_esct_payable_id": "2330",
            "system_account_child_support_payable_id": "2340",
            "system_account_wages_expense_id": "6970",
            "system_account_employer_kiwisaver_expense_id": "6971",
        },
    },
}
