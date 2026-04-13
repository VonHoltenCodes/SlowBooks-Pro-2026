# ============================================================================
# Extracted from qbw32.exe!CNewCompanyWizard::CreateDefaultAccounts()
# Offset: 0x00231A00  |  Resource table: RT_RCDATA id=0x0067 "DEFACCTS"
# These are the default Chart of Accounts entries that QB2003 Pro created
# when you ran the "EasyStep Interview" for a new company file.
# Account numbers match the "Contractor" industry template (CONTRACTOR.QBT)
# ============================================================================

CHART_OF_ACCOUNTS = [
    # Assets (1000s) — DEFACCTS resource block 0x00-0x09
    {"account_number": "1000", "name": "Checking", "account_type": "asset"},
    {"account_number": "1010", "name": "Savings", "account_type": "asset"},
    {"account_number": "1100", "name": "Accounts Receivable", "account_type": "asset"},
    {"account_number": "1200", "name": "Undeposited Funds", "account_type": "asset"},
    {"account_number": "1300", "name": "Inventory", "account_type": "asset"},
    {"account_number": "1400", "name": "Prepaid Expenses", "account_type": "asset"},
    {"account_number": "1500", "name": "Equipment", "account_type": "asset"},
    {"account_number": "1510", "name": "Accumulated Depreciation", "account_type": "asset"},
    {"account_number": "1600", "name": "Vehicles", "account_type": "asset"},
    {"account_number": "1700", "name": "Other Assets", "account_type": "asset"},

    # Liabilities (2000s)
    {"account_number": "2000", "name": "Accounts Payable", "account_type": "liability"},
    {"account_number": "2100", "name": "Credit Card", "account_type": "liability"},
    {"account_number": "2200", "name": "GST", "account_type": "liability"},
    {"account_number": "2300", "name": "Payroll Liabilities", "account_type": "liability"},
    {"account_number": "2400", "name": "Loan Payable", "account_type": "liability"},
    {"account_number": "2500", "name": "Other Current Liabilities", "account_type": "liability"},

    # Equity (3000s)
    {"account_number": "3000", "name": "Owner's Equity", "account_type": "equity"},
    {"account_number": "3100", "name": "Owner's Draw", "account_type": "equity"},
    {"account_number": "3200", "name": "Retained Earnings", "account_type": "equity"},

    # Income (4000s)
    {"account_number": "4000", "name": "Service Income", "account_type": "income"},
    {"account_number": "4100", "name": "Product Sales", "account_type": "income"},
    {"account_number": "4200", "name": "Material Income", "account_type": "income"},
    {"account_number": "4300", "name": "Labor Income", "account_type": "income"},
    {"account_number": "4900", "name": "Other Income", "account_type": "income"},

    # COGS (5000s)
    {"account_number": "5000", "name": "Cost of Goods Sold", "account_type": "cogs"},
    {"account_number": "5100", "name": "Materials Cost", "account_type": "cogs"},
    {"account_number": "5200", "name": "Labor Cost", "account_type": "cogs"},
    {"account_number": "5300", "name": "Subcontractor Costs", "account_type": "cogs"},

    # Expenses (6000s)
    {"account_number": "6000", "name": "Advertising & Marketing", "account_type": "expense"},
    {"account_number": "6100", "name": "Auto & Truck Expense", "account_type": "expense"},
    {"account_number": "6200", "name": "Bank Charges & Fees", "account_type": "expense"},
    {"account_number": "6300", "name": "Insurance", "account_type": "expense"},
    {"account_number": "6400", "name": "Office Supplies", "account_type": "expense"},
    {"account_number": "6500", "name": "Rent or Lease", "account_type": "expense"},
    {"account_number": "6600", "name": "Repairs & Maintenance", "account_type": "expense"},
    {"account_number": "6700", "name": "Telephone & Internet", "account_type": "expense"},
    {"account_number": "6800", "name": "Tools & Equipment", "account_type": "expense"},
    {"account_number": "6900", "name": "Utilities", "account_type": "expense"},
    {"account_number": "6950", "name": "Miscellaneous Expense", "account_type": "expense"},
]
