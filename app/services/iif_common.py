# ============================================================================
# IIF type correspondences shared by iif_import.py and iif_export.py.
#
# QuickBooks' ACCNTTYPE is our detail type (#390): the fifteen posting types
# map one to one, both directions read the same table, and the base
# AccountType is derived from the detail type. Keeping both directions in
# one module (plus tests/test_interchange_roundtrip.py) is what stops them
# drifting apart.
#
# NONPOSTING (estimates, purchase orders) is not a ledger account here; the
# lists import skips it rather than make an asset of it.
#
# Known-lossy round trips (inherent to the IIF vocabulary, not bugs):
#   ItemType.MATERIAL exports as PART and re-imports as ItemType.PRODUCT.
# ============================================================================

from app.models.accounts import (
    BASE_TYPE_OF,
    Account,
    AccountType,
    DetailType,
)
from app.models.items import Item, ItemType

IIF_TO_DETAIL_TYPE = {
    "BANK": DetailType.BANK,
    "AR": DetailType.ACCOUNTS_RECEIVABLE,
    "OCASSET": DetailType.OTHER_CURRENT_ASSET,
    "FIXASSET": DetailType.FIXED_ASSET,
    "OASSET": DetailType.OTHER_ASSET,
    "AP": DetailType.ACCOUNTS_PAYABLE,
    # a credit card is what is owed on it (it fell back to EXPENSE: 2.23.4
    # gate, NEW-125)
    "CCARD": DetailType.CREDIT_CARD,
    "OCLIAB": DetailType.OTHER_CURRENT_LIABILITY,
    "LTLIAB": DetailType.LONG_TERM_LIABILITY,
    "EQUITY": DetailType.EQUITY,
    "INC": DetailType.INCOME,
    "COGS": DetailType.COST_OF_GOODS_SOLD,
    "EXP": DetailType.EXPENSE,
    "EXINC": DetailType.OTHER_INCOME,
    "EXEXP": DetailType.OTHER_EXPENSE,
}
DETAIL_TYPE_TO_IIF = {d: iif for iif, d in IIF_TO_DETAIL_TYPE.items()}
# The base type of each ACCNTTYPE, derived: what the import matches an
# existing account on.
IIF_TO_ACCOUNT_TYPE: dict[str, AccountType] = {
    iif: BASE_TYPE_OF[d] for iif, d in IIF_TO_DETAIL_TYPE.items()
}
# Read but not imported: QuickBooks' non-posting accounts.
IIF_SKIPPED_TYPES = {"NONPOSTING"}

IIF_TO_ITEM_TYPE = {
    "SERV": ItemType.SERVICE,
    "PART": ItemType.PRODUCT,
    "OTHC": ItemType.LABOR,
    "INVENTORY": ItemType.PRODUCT,
    "NON-INVENTORY": ItemType.MATERIAL,
    # QuickBooks' discount item: a service item here, marked as a discount
    # so a line on it may carry a negative price (#639). The export writes
    # it back as SERV: an item's own type is what it carries.
    "DISC": ItemType.SERVICE,
}


def account_to_iif_type(acct: Account) -> str:
    """The account's ACCNTTYPE: its detail type, read back through the
    import's table (#390). The number-range guess this replaced sent 2400
    Loan Payable out as OCLIAB and 2500 Other Current Liabilities as LTLIAB,
    and never wrote CCARD."""
    from app.models.accounts import _enum, default_detail_type

    try:
        detail = _enum(DetailType, acct.detail_type)  # value or NAME form
    except ValueError:
        detail = None  # a stray stored value: write what the type implies
    if detail is None:
        detail = default_detail_type(
            acct.account_type, acct.bank_kind, acct.account_number
        )
    return DETAIL_TYPE_TO_IIF[detail]


def item_to_iif_type(item: Item) -> str:
    """Map SlowBooks ItemType to IIF INVITEMTYPE."""
    return {
        "service": "SERV",
        "product": "PART",
        "material": "PART",
        "labor": "OTHC",
    }[item.item_type.value]
