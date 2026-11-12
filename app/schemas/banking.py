from datetime import date as dt_date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator
from app.schemas.common import StrictModel

from app.models.banking import ReconciliationStatus
from app.schemas.attachments import PendingAttachmentIds


class BankAccountCreate(StrictModel):
    """A bank feed / statement identity for a ledger account that has
    bank_kind set. An opening balance posts a journal entry against 3900
    Opening Balance Equity; it is what the statement says (cash in the
    bank, or the amount owed on a card). When the ledger already carries
    the account on the opening date, only a difference can post, and only
    with `post_difference` set (the user confirmed it)."""

    name: str
    account_id: int
    bank_name: Optional[str] = None
    last_four: Optional[str] = None
    opening_balance: Decimal = Decimal("0")
    opening_date: Optional[dt_date] = None
    post_difference: bool = False


class BankAccountUpdate(StrictModel):
    name: Optional[str] = None
    account_id: Optional[int] = None
    bank_name: Optional[str] = None
    last_four: Optional[str] = None
    is_active: Optional[bool] = None
    # only null is accepted: dismisses the pre-2.10 balance banner
    legacy_balance: Optional[Decimal] = None

    @field_validator("legacy_balance")
    @classmethod
    def only_null(cls, v):
        if v is not None:
            raise ValueError("legacy_balance can only be cleared (null)")
        return v


class BankAccountResponse(BaseModel):
    id: int
    name: str
    account_id: Optional[int]
    account_name: Optional[str] = None
    bank_kind: Optional[str] = None
    bank_name: Optional[str]
    last_four: Optional[str]
    balance: Decimal  # the linked ledger account's balance (0 when unlinked)
    legacy_balance: Optional[Decimal] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LegacyBalancePost(StrictModel):
    date: dt_date


class BankSplit(StrictModel):
    """One line of a split register entry (#330): the account it posts to
    and its share of the amount, signed (money out negative, money in
    positive), and its own memo. A line may carry the opposite sign from
    the entry (#387): a card processor's fee on a deposit, a refund netted
    against a check. Never zero. A line may name its own class and job
    (an imported check's lines can); left out, it takes the entry's."""

    category_account_id: int
    amount: Decimal
    description: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    # The name on the line (#606): the donor or customer a deposit line came
    # from. Left out, it takes the entry's `customer_id`.
    customer_id: Optional[int] = None


class BankTransactionCreate(StrictModel):
    """A register entry: posts a journal entry. amount > 0 debits the bank
    or card account (money in / card payment), amount < 0 credits it
    (money out / card charge). `account_id` is the ledger account;
    `bank_account_id` (the feed) is accepted for older callers. With
    `splits`, the other side is several accounts whose amounts add up to
    `amount` (a check that pays several things; a loan payment that is
    principal and interest; a processor's deposit as the gross income and
    the fee taken out, the bank taking the net); `category_account_id` is
    then the first split's account, kept for older callers."""

    account_id: Optional[int] = None
    bank_account_id: Optional[int] = None
    date: dt_date
    amount: Decimal
    category_account_id: Optional[int] = None
    payee: Optional[str] = None
    description: Optional[str] = None
    check_number: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    splits: Optional[list[BankSplit]] = None
    # The vendor paid (#590). Left out, a payee that is exactly a vendor's
    # name picks that vendor; one that is not stays text. A transfer has none.
    vendor_id: Optional[int] = None
    # The customer (donor) a deposit came from (#606). On a plain entry it
    # is the name on the income line; with `splits` it is the default for
    # the lines that name none.
    customer_id: Optional[int] = None
    # Files attached on the form before this first save (#881, as #796):
    # the ids POST /api/attachments/pending gave.
    pending_attachments: PendingAttachmentIds = []


class BankEntryUpdate(BankTransactionCreate):
    """The + Entry form's fields again, for editing a register entry in
    place (#388). A field left out keeps what the entry has (its job, its
    class, its lines' own classes); `account_id` may be left out (the
    entry's own) but may not name another account. On an entry a
    completed reconciliation holds, the amount and date stay as they are
    (400 otherwise), and a change of category or split lines is refused
    with 409 until `confirm_reconciled` is true."""

    date: Optional[dt_date] = None
    amount: Optional[Decimal] = None
    confirm_reconciled: bool = False


class BankEntryResponse(BaseModel):
    id: int  # the journal entry
    account_id: int
    date: dt_date
    amount: Decimal
    category_account_id: int
    category_name: str
    payee: str
    description: str
    reference: str
    source_type: str
    status: str
    vendor_id: Optional[int] = None
    vendor_name: Optional[str] = None
    # the name on the entry's one income line (#606); the split lines say theirs
    customer_id: Optional[int] = None
    customer_name: Optional[str] = None


class BankSplitOut(BaseModel):
    category_account_id: int
    amount: Decimal
    description: Optional[str] = None
    # the line's own class and job, when they differ from the entry's
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    customer_id: Optional[int] = None


class BankEntryDetail(BankEntryResponse):
    """A register entry in the + Entry form's shape (#388): the signed
    amount, the category (or the split lines), payee, memo, reference,
    class and job; `reconciled_on` is the statement date of the completed
    reconciliation that holds it, if one does, and `reconciled_note` the
    sentence the form shows for it; `class_name` names the class (an
    archived one too, which the class list leaves out). After an edit,
    `uncleared` names the lines whose amount changed and so lost their
    cleared mark, and `note` says so in a sentence."""

    class_id: Optional[int] = None
    class_name: Optional[str] = None
    job_id: Optional[int] = None
    splits: Optional[list[BankSplitOut]] = None
    # the lines that are gifts from a named donor (#606), each of which can
    # get an acknowledgment letter: [{line_id, customer_name, amount}]
    gift_letters: list[dict] = []
    reconciled_on: Optional[dt_date] = None
    reconciled_note: Optional[str] = None
    uncleared: list[dict] = []
    note: Optional[str] = None


class BankTransactionResponse(BaseModel):
    """A statement line in the review queue."""

    id: int
    bank_account_id: int
    date: dt_date
    amount: Decimal
    payee: Optional[str]
    description: Optional[str]
    check_number: Optional[str]
    category_account_id: Optional[int]
    category_name: Optional[str] = None
    match_status: Optional[str] = None
    transaction_id: Optional[int] = None
    transaction_line_id: Optional[int] = None
    import_source: Optional[str] = None
    reconciled: bool
    created_at: datetime
    # the vendor the added line's entry names (#491)
    vendor_id: Optional[int] = None
    vendor_name: Optional[str] = None
    # a provider payout this deposit could be (#584): advisory only, nothing
    # posts. {provider, display_name, clearing_account_id, fee_account_id,
    # clearing_balance}; null unless the payee names a provider whose
    # clearing is on.
    suggested_payout: Optional[dict] = None

    model_config = {"from_attributes": True}


class StatementMatch(StrictModel):
    line_id: int


class StatementAdd(StrictModel):
    category_account_id: Optional[int] = None
    payee: Optional[str] = None
    memo: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    # the vendor the entry names (#491); left out, the payee's vendor
    vendor_id: Optional[int] = None
    # the customer (donor) the entry names (#606): the income line's name, or
    # the default for split lines that name none
    customer_id: Optional[int] = None
    # split the line across several accounts instead of one category (#584):
    # a provider payout clears its clearing account and books the fee. Signed
    # like a register split; mutually exclusive with category_account_id,
    # and the parts must add up to the statement amount.
    splits: Optional[list[BankSplit]] = None


class FeedLinePicks(StrictModel):
    """What the To review row chose for one line besides its category
    (#671): the entry's class and job, and the vendor or customer it names."""

    id: int
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    vendor_id: Optional[int] = None
    customer_id: Optional[int] = None
    # a name typed that is no vendor's: the entry's payee, as text
    payee: Optional[str] = None


class FeedAddAll(StrictModel):
    """Add all categorised, with each row's own picks (#671). A line not
    listed posts with its category alone, as before."""

    lines: list[FeedLinePicks] = []


class FeedExcludeAll(StrictModel):
    """Exclude all shown (#705): the review list's statement line ids."""

    line_ids: list[int] = Field(default_factory=list, max_length=10000)


class StatementPayout(StrictModel):
    """Record an unmatched deposit as a provider payout (#584): the server
    builds the split (CR clearing the gross, DR the fee, DR bank the net)
    so the clearing and fee account ids stay off the client. `gross` is the
    amount cleared from the clearing account; the fee is gross − the
    statement net, which must not be negative."""

    provider: str
    gross: Decimal


class StatementVendors(StrictModel):
    """Added feed lines whose payee should become (or be linked to) a
    vendor (#491)."""

    line_ids: list[int]


class StatementCategory(StrictModel):
    """The category picked for a statement line in the review list (null
    clears it). Kept on the line, so Add all posts it and a reload shows
    it."""

    category_account_id: Optional[int] = None


class ReconciliationCreate(StrictModel):
    """`account_id` is the ledger account; `bank_account_id` (a feed) is
    accepted for older callers."""

    account_id: Optional[int] = None
    bank_account_id: Optional[int] = None
    statement_date: dt_date
    statement_balance: Decimal


class ReconciliationUpdate(StrictModel):
    """Correct the statement's date or ending balance while the
    reconciliation is in progress (#389): a transposed digit was a fresh
    start. Both optional; an omitted field is left as it was."""

    statement_date: Optional[dt_date] = None
    statement_balance: Optional[Decimal] = None


class ReconciliationResponse(BaseModel):
    id: int
    account_id: Optional[int]
    account_name: Optional[str] = None
    bank_account_id: Optional[int]
    statement_date: dt_date
    statement_balance: Decimal
    beginning_balance: Decimal
    cleared_total: Optional[Decimal]
    status: ReconciliationStatus
    created_at: datetime
    completed_at: Optional[datetime]
    # Lines left ticked past the statement date that carry to the next
    # reconciliation (#400 review); only set on the update response.
    ticked_after_date: int = 0

    model_config = {"from_attributes": True}
