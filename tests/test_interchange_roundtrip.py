"""Drift guard for the IIF/QBO type correspondences in iif_common/qbo_common.

The import and export directions of each format used to live in separate
modules with no shared source of truth; nothing failed if one side changed
alone. These tests pin the round-trip invariant: any type the export side
can emit must map back to the same internal enum on import (except the
documented lossy cases inherent to the external vocabulary).
"""

from types import SimpleNamespace

import pytest

from app.models.accounts import AccountType
from app.models.items import ItemType
from app.services.iif_common import (
    IIF_TO_ACCOUNT_TYPE,
    IIF_TO_ITEM_TYPE,
    account_to_iif_type,
    item_to_iif_type,
)
from app.services.qbo_common import (
    ACCOUNT_TYPE_TO_QBO,
    ITEM_TYPE_TO_QBO,
    QBO_TO_ACCOUNT_TYPE,
    QBO_TO_ITEM_TYPE,
)


def _acct(number: str, atype: AccountType):
    return SimpleNamespace(account_number=number, account_type=atype)


def _item(itype: ItemType):
    return SimpleNamespace(item_type=itype)


# Representative account numbers covering every branch of
# account_to_iif_type's range logic.
IIF_ACCOUNT_CASES = [
    ("1000", AccountType.ASSET),  # BANK
    ("1099", AccountType.ASSET),  # BANK upper bound
    ("1100", AccountType.ASSET),  # AR
    ("1300", AccountType.ASSET),  # OCASSET
    ("1500", AccountType.ASSET),  # FIXASSET
    ("3000", AccountType.ASSET),  # OASSET (>= 2000 fallthrough)
    ("2000", AccountType.LIABILITY),  # AP
    ("2100", AccountType.LIABILITY),  # OCLIAB
    ("2700", AccountType.LIABILITY),  # LTLIAB
    ("3900", AccountType.EQUITY),
    ("4000", AccountType.INCOME),
    ("6000", AccountType.EXPENSE),
    ("5000", AccountType.COGS),
]


@pytest.mark.parametrize("number,atype", IIF_ACCOUNT_CASES)
def test_iif_account_type_round_trips(number, atype):
    iif_type = account_to_iif_type(_acct(number, atype))
    assert iif_type in IIF_TO_ACCOUNT_TYPE, f"export emits unknown ACCNTTYPE {iif_type}"
    assert IIF_TO_ACCOUNT_TYPE[iif_type] == atype


@pytest.mark.parametrize(
    "itype,expected_back",
    [
        (ItemType.SERVICE, ItemType.SERVICE),
        (ItemType.PRODUCT, ItemType.PRODUCT),
        # Lossy: IIF has no MATERIAL type; PART re-imports as PRODUCT.
        (ItemType.MATERIAL, ItemType.PRODUCT),
        (ItemType.LABOR, ItemType.LABOR),
    ],
)
def test_iif_item_type_round_trips(itype, expected_back):
    iif_type = item_to_iif_type(_item(itype))
    assert iif_type in IIF_TO_ITEM_TYPE, f"export emits unknown item type {iif_type}"
    assert IIF_TO_ITEM_TYPE[iif_type] == expected_back


def test_qbo_account_type_round_trips():
    assert set(ACCOUNT_TYPE_TO_QBO) == set(AccountType), "every enum must export"
    for atype, (qbo_type, _subtype) in ACCOUNT_TYPE_TO_QBO.items():
        assert qbo_type in QBO_TO_ACCOUNT_TYPE, f"export emits unknown {qbo_type}"
        assert QBO_TO_ACCOUNT_TYPE[qbo_type] == atype


@pytest.mark.parametrize(
    "itype,expected_back",
    [
        (ItemType.SERVICE, ItemType.SERVICE),
        (ItemType.PRODUCT, ItemType.PRODUCT),
        (ItemType.MATERIAL, ItemType.MATERIAL),
        # Lossy: QBO has no labor item type; exports as Service.
        (ItemType.LABOR, ItemType.SERVICE),
    ],
)
def test_qbo_item_type_round_trips(itype, expected_back):
    qbo_type = ITEM_TYPE_TO_QBO[itype]
    assert qbo_type in QBO_TO_ITEM_TYPE, f"export emits unknown {qbo_type}"
    assert QBO_TO_ITEM_TYPE[qbo_type] == expected_back
