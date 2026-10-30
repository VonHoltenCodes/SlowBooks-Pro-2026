"""The MCP write tools (#655): through the Bearer transport with a bookkeeper
token, against the seeded chart of accounts."""

import pytest

from app.mcp import tools
from app.mcp.client import ApiClient, ApiError
from app.mcp.server import build_server

LINE = {"description": "work", "quantity": 1, "rate": "100.00"}


def _customer_invoice(api):
    cust = tools.create_customer(api, {"name": "Pay Me Co"})
    inv = tools.create_invoice(
        api, {"customer_id": cust["id"], "date": "2026-09-10", "lines": [LINE]}
    )
    return cust, inv


def _bill(api, acct):
    vendor = tools.create_vendor(api, {"name": "Lumber Yard"})
    bill = tools.create_bill(
        api,
        {
            "vendor_id": vendor["id"],
            "date": "2026-09-10",
            "lines": [{"description": "wood", "amount": 100, "account_id": acct}],
        },
    )
    return vendor, bill


def test_create_master_records_and_find_them(api, seed_accounts):
    cust = tools.create_customer(api, {"name": "Made Customer"})
    vend = tools.create_vendor(api, {"name": "Made Vendor"})
    item = tools.create_item(api, {"name": "Made Item", "item_type": "service"})
    for kind, rec in (("customers", cust), ("vendors", vend), ("items", item)):
        assert tools.get_record(api, kind, rec["id"])["name"] == rec["name"]


def test_create_invoice_bill_expense_journal(api, seed_accounts):
    _, inv = _customer_invoice(api)
    assert tools.get_record(api, "invoices", inv["id"])["id"] == inv["id"]
    _, bill = _bill(api, seed_accounts["6000"].id)
    assert tools.get_record(api, "bills", bill["id"])["id"] == bill["id"]
    exp = tools.create_expense(
        api,
        {
            "date": "2026-09-11",
            "amount": "25.00",
            "expense_account_id": seed_accounts["6000"].id,
            "paid_from_account_id": seed_accounts["1000"].id,
        },
    )
    assert exp["id"]
    lines = [
        {"account_id": seed_accounts["6000"].id, "debit": "50.00", "credit": "0"},
        {"account_id": seed_accounts["1000"].id, "debit": "0", "credit": "50.00"},
    ]
    je = tools.create_journal_entry(
        api, {"date": "2026-09-12", "description": "Adjust", "lines": lines}
    )
    assert je["id"]
    lines[1]["credit"] = "40.00"
    with pytest.raises(ApiError) as exc:
        tools.create_journal_entry(
            api, {"date": "2026-09-12", "description": "Off", "lines": lines}
        )
    assert exc.value.status == 400 and exc.value.detail


def test_unknown_field_relays_the_422(api, seed_accounts):
    with pytest.raises(ApiError) as exc:
        tools.create_customer(api, {"name": "X", "bogus_field": 1})
    assert exc.value.status == 422 and "bogus_field" in exc.value.detail


def _pay_body(cust, inv, amount="40.00"):
    return {
        "customer_id": cust["id"],
        "date": "2026-09-12",
        "amount": amount,
        "allocations": [{"invoice_id": inv["id"], "amount": amount}],
    }


def test_record_payment_books_once_per_key(api, seed_accounts):
    cust, inv = _customer_invoice(api)
    first = tools.record_payment(api, _pay_body(cust, inv), "k-001")
    again = tools.record_payment(api, _pay_body(cust, inv), "k-001")
    assert again["id"] == first["id"]
    assert len(tools.list_records(api, "payments")) == 1
    with pytest.raises(ApiError) as exc:
        tools.record_payment(api, _pay_body(cust, inv, "50.00"), "k-001")
    assert exc.value.status == 409
    assert "different request" in exc.value.detail


def test_record_bill_payment_books_once_per_key(api, seed_accounts):
    vendor, bill = _bill(api, seed_accounts["6000"].id)
    body = {
        "vendor_id": vendor["id"],
        "date": "2026-09-12",
        "amount": 40,
        "allocations": [{"bill_id": bill["id"], "amount": 40}],
    }
    first = tools.record_bill_payment(api, body, "bk-001")
    again = tools.record_bill_payment(api, body, "bk-001")
    assert again["id"] == first["id"]
    assert len(tools.list_records(api, "bill_payments")) == 1
    voided = tools.void_record(api, "bill_payments", first["id"])
    assert voided["is_voided"] is True


@pytest.mark.parametrize("key", ["", "has space", "x" * 129, "bad/slash", None])
def test_bad_key_raises_before_any_request(key):
    calls = []
    spy = ApiClient(lambda *a: calls.append(a) or (201, {}))
    with pytest.raises(ValueError, match="key must be"):
        tools.record_payment(spy, {}, key)
    with pytest.raises(ValueError, match="key must be"):
        tools.record_bill_payment(spy, {}, key)
    assert calls == []


def test_void_record(api, seed_accounts):
    _, inv = _customer_invoice(api)
    tools.void_record(api, "invoices", inv["id"])
    assert tools.get_record(api, "invoices", inv["id"])["status"] == "void"
    with pytest.raises(ValueError, match="Kinds: invoices"):
        tools.void_record(api, "nope", 1)


def test_void_payment_passes_fee_refunded(api):
    seen = []
    spy = ApiClient(lambda m, p, params, b, h: seen.append((p, params)) or (200, {}))
    tools.void_record(spy, "payments", 7, fee_refunded=True)
    tools.void_record(spy, "payments", 8)
    assert seen == [
        ("/api/payments/7/void", {"fee_refunded": "true"}),
        ("/api/payments/8/void", {}),
    ]


def test_writes_are_audited_as_the_token(api, seed_accounts):
    made = tools.create_customer(api, {"name": "Audited Co"})
    rows = tools.api_get(api, "/api/audit", {"table_name": "customers"})
    mine = [r for r in rows if r["record_id"] == made["id"]]
    assert mine and mine[0]["username"] == "token:mcp-test"


def test_unknown_outcome_sentences():
    def lost(*a):
        raise ApiError(0, "SlowBooks Pro did not answer")

    gone = ApiClient(lost)
    with pytest.raises(ValueError, match="same key and the same data"):
        tools.record_payment(gone, {}, "k-1")
    with pytest.raises(ValueError, match="same key and the same data"):
        tools.record_bill_payment(gone, {}, "k-1")
    with pytest.raises(ValueError, match="list_records or search before retrying"):
        tools.create_invoice(gone, {})
    with pytest.raises(ValueError, match="before retrying"):
        tools.void_record(gone, "invoices", 1)

    # A refused connection sent nothing: no "unknown outcome" sentence.
    def refused(*a):
        raise ApiError(0, "Nothing was sent.", sent=False)

    with pytest.raises(ApiError, match="Nothing was sent") as exc:
        tools.record_payment(ApiClient(refused), {}, "k-1")
    assert "may or may not" not in str(exc.value)


def test_void_fee_refunded_only_on_payments(api):
    with pytest.raises(ValueError, match="payments only"):
        tools.void_record(api, "invoices", 1, fee_refunded=True)


def test_duplicate_409_names_the_tool_argument(api):
    tools.create_customer(api, {"name": "Dup Wording Co"})
    with pytest.raises(ApiError) as exc:
        tools.create_customer(api, {"name": "Dup Wording Co."})
    assert "?force=true" not in str(exc.value)
    assert "force: true" in str(exc.value)
    assert "?force=true" not in exc.value.payload["message"]  # the relayed JSON too


def test_void_refuses_a_documents_posting_from_the_journal(api, seed_accounts):
    """After voiding invoice 1001, void_record(journal_entries, <its posting>)
    reversed the posting a second time (Mac review; the route is main's)."""
    cust, inv = _customer_invoice(api)
    api.post(f"/api/invoices/{inv['id']}/send")
    rows = tools.list_records(api, "journal_entries", {"source_type": "invoice"})
    assert rows, "the invoice has no posting to void"
    posting = rows[-1]
    with pytest.raises(ValueError, match="posting of an invoice.*kind='invoices'"):
        tools.void_record(api, "journal_entries", posting["id"])
    tools.void_record(api, "invoices", inv["id"])
    with pytest.raises(ValueError, match="reverse it twice"):
        tools.void_record(api, "journal_entries", posting["id"])
    # A journal of its own still voids.
    je = tools.create_journal_entry(
        api,
        {
            "date": "2026-09-10",
            "description": "own entry",
            "lines": [
                {
                    "account_id": seed_accounts["6000"].id,
                    "debit": "10.00",
                    "credit": "0",
                },
                {
                    "account_id": seed_accounts["1000"].id,
                    "debit": "0",
                    "credit": "10.00",
                },
            ],
        },
    )
    voided = tools.void_record(api, "journal_entries", je["id"])
    # The reversal itself is left to the route, which names it.
    with pytest.raises(ApiError, match="reversal entry"):
        tools.void_record(api, "journal_entries", voided["id"])


def test_server_lists_eighteen_tools_with_writes_on_and_eight_off(api):
    listed = {t["name"]: t for t in build_server(api, writes=True).list_tools()}
    assert len(listed) == 18
    writes = [n for n, t in listed.items() if not t["annotations"]["readOnlyHint"]]
    assert len(writes) == 10
    assert listed["void_record"]["annotations"]["destructiveHint"] is True
    idem = {n for n in writes if listed[n]["annotations"]["idempotentHint"]}
    assert idem == {"record_payment", "record_bill_payment"}
    assert [n for n in writes if listed[n]["annotations"]["destructiveHint"]] == [
        "void_record"
    ]
    assert "client" not in listed["record_payment"]["inputSchema"]["properties"]
    # Off by default: the write tools are not even listed.
    off = {t["name"] for t in build_server(api).list_tools()}
    assert len(off) == 8 and not off & set(writes)
    assert "read-only" in build_server(api).instructions
