"""SimpleFIN bank feeds: token claim, request builders, sync dedup,
bank-rule parity with OFX, settings redaction, and route error paths."""

import base64
import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.models.accounts import Account, AccountType
from app.models.bank_rules import BankRule
from app.models.banking import BankAccount, BankTransaction
from app.services import simplefin_service as sf
from app.services.settings_service import get_setting_raw, set_setting

CLAIM_URL = "https://bridge.example.com/simplefin/claim/DEMO"
ACCESS_URL = "https://user123:pass456@bridge.example.com/simplefin"

SF_DATA = {
    "errors": [],
    "accounts": [
        {
            "id": "ACT-1",
            "name": "Demo Checking",
            "org": {"name": "Demo Bank"},
            "currency": "USD",
            "balance": "1234.56",
            "transactions": [
                {
                    "id": "TXN-1",
                    "posted": 1786320000,  # 2026-08-10 UTC
                    "amount": "-55.50",
                    "description": "Fishing bait",
                    "payee": "Johns Fishin Shack",
                },
                {
                    "id": "TXN-2",
                    "posted": 1786233600,
                    "amount": "2500.00",
                    "description": "Payroll",
                    "payee": "ACME LLC",
                },
                {
                    "id": "TXN-PENDING",
                    "posted": 1786320000,
                    "amount": "-9.99",
                    "description": "Pending card hold",
                    "pending": True,
                },
            ],
        }
    ],
}


def _token(url=CLAIM_URL):
    return base64.b64encode(url.encode()).decode()


def _resp(status=200, text="", json_body=None):
    if json_body is not None:
        return httpx.Response(status, json=json_body)
    return httpx.Response(status, text=text)


def _mk_bank_account(db_session, name="Feed Checking"):
    ba = BankAccount(name=name, bank_name="Demo Bank")
    db_session.add(ba)
    db_session.commit()
    return ba


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_decode_setup_token_roundtrip():
    assert sf.decode_setup_token(_token()) == CLAIM_URL


def test_decode_setup_token_rejects_http():
    with pytest.raises(sf.SimpleFINError):
        sf.decode_setup_token(_token("http://insecure.example.com/claim"))


def test_decode_setup_token_rejects_garbage():
    with pytest.raises(sf.SimpleFINError):
        sf.decode_setup_token("not-base64!!!")
    with pytest.raises(sf.SimpleFINError):
        sf.decode_setup_token("")


def test_build_accounts_request_splits_credentials():
    req = sf.build_accounts_request(ACCESS_URL, start_date=date(2026, 8, 1))
    assert req["url"] == "https://bridge.example.com/simplefin/accounts"
    assert req["auth"] == ("user123", "pass456")
    assert req["params"]["start-date"] == 1785542400  # 2026-08-01T00:00Z
    # credentials must not leak into the URL itself
    assert "user123" not in req["url"]


def test_build_accounts_request_rejects_credless_url():
    with pytest.raises(sf.SimpleFINError):
        sf.build_accounts_request("https://bridge.example.com/simplefin")


def test_to_import_rows_shapes_and_skips_pending():
    rows, errors = sf.to_import_rows(SF_DATA["accounts"][0])
    assert errors == []
    assert [r["fitid"] for r in rows] == ["TXN-1", "TXN-2"]
    assert rows[0]["amount"] == Decimal("-55.50")
    assert rows[0]["date"] == date(2026, 8, 10)
    assert rows[0]["payee"] == "Johns Fishin Shack"


def test_to_import_rows_flags_malformed():
    rows, errors = sf.to_import_rows(
        {"name": "X", "transactions": [{"id": "T", "posted": "nope", "amount": "1"}]}
    )
    assert rows == []
    assert len(errors) == 1
    assert "nope" not in errors[0]  # message stays generic, no raw data echo


def test_parse_account_map_tolerates_junk():
    assert sf.parse_account_map('{"A": 3, "B": "4", "C": "x"}') == {"A": 3, "B": 4}
    assert sf.parse_account_map("not json") == {}
    assert sf.parse_account_map("[1,2]") == {}


# ---------------------------------------------------------------------------
# Claim + fetch against a mocked transport
# ---------------------------------------------------------------------------


def test_claim_access_url(monkeypatch):
    monkeypatch.setattr(sf, "send", lambda req, **kw: _resp(text=ACCESS_URL + "\n"))
    assert sf.claim_access_url(_token()) == ACCESS_URL


def test_claim_access_url_rejected_token(monkeypatch):
    monkeypatch.setattr(sf, "send", lambda req, **kw: _resp(status=403))
    with pytest.raises(sf.SimpleFINError):
        sf.claim_access_url(_token())


def test_fetch_accounts_error_paths(monkeypatch):
    monkeypatch.setattr(sf, "send", lambda req, **kw: _resp(status=500))
    with pytest.raises(sf.SimpleFINError):
        sf.fetch_accounts(ACCESS_URL)
    monkeypatch.setattr(sf, "send", lambda req, **kw: _resp(text="<html>"))
    with pytest.raises(sf.SimpleFINError):
        sf.fetch_accounts(ACCESS_URL)


# ---------------------------------------------------------------------------
# Sync: dedup + bank rules through the shared OFX path
# ---------------------------------------------------------------------------


def test_sync_imports_dedups_and_applies_rules(db_session):
    ba = _mk_bank_account(db_session)
    expense = Account(name="Bait Expense", account_type=AccountType.EXPENSE)
    db_session.add(expense)
    db_session.commit()
    db_session.add(
        BankRule(
            name="bait",
            pattern="fishin",
            rule_type="contains",
            account_id=expense.id,
            priority=10,
            is_active=True,
        )
    )
    db_session.commit()

    result = sf.sync_accounts(db_session, SF_DATA, {"ACT-1": ba.id})
    assert result["imported"] == 2
    assert result["skipped"] == 0
    assert result["warnings"] == []

    txns = (
        db_session.query(BankTransaction)
        .filter(BankTransaction.bank_account_id == ba.id)
        .all()
    )
    assert {t.import_id for t in txns} == {"TXN-1", "TXN-2"}
    assert all(t.import_source == "simplefin" for t in txns)
    bait = next(t for t in txns if t.import_id == "TXN-1")
    assert bait.match_status == "auto"
    assert bait.category_account_id == expense.id

    # Second sync of the same window: everything dedups
    again = sf.sync_accounts(db_session, SF_DATA, {"ACT-1": ba.id})
    assert again["imported"] == 0
    assert again["skipped"] == 2


def test_sync_warns_on_missing_mapped_account(db_session):
    ba = _mk_bank_account(db_session)
    result = sf.sync_accounts(db_session, SF_DATA, {"GONE": ba.id})
    assert result["imported"] == 0
    assert len(result["warnings"]) == 1


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_claim_route_stores_secret_and_returns_accounts(
    authed_client, db_session, monkeypatch
):
    responses = [_resp(text=ACCESS_URL), _resp(json_body=SF_DATA)]
    monkeypatch.setattr(sf, "send", lambda req, **kw: responses.pop(0))
    r = authed_client.post("/api/simplefin/claim", json={"setup_token": _token()})
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True
    assert body["accounts"][0]["id"] == "ACT-1"
    assert get_setting_raw(db_session, "simplefin_access_url") == ACCESS_URL

    # The access URL is a credential: GET /api/settings must redact it
    settings = authed_client.get("/api/settings").json()
    assert ACCESS_URL not in json.dumps(settings)
    assert settings["simplefin_access_url"] == "********"


def test_claim_route_bad_token_is_400(authed_client):
    r = authed_client.post(
        "/api/simplefin/claim", json={"setup_token": "!!definitely not base64!!"}
    )
    assert r.status_code == 400


def test_map_route_validates_bank_account(authed_client, db_session):
    set_setting(db_session, "simplefin_access_url", ACCESS_URL)
    db_session.commit()
    r = authed_client.post("/api/simplefin/map", json={"mapping": {"ACT-1": 99999}})
    assert r.status_code == 404

    ba = _mk_bank_account(db_session, name="Mapped Checking")
    r = authed_client.post(
        "/api/simplefin/map", json={"mapping": {"ACT-1": ba.id, "ACT-2": 0}}
    )
    assert r.status_code == 200
    assert r.json()["account_map"] == {"ACT-1": ba.id}


def test_sync_route_requires_connection_and_mapping(authed_client, db_session):
    r = authed_client.post("/api/simplefin/sync")
    assert r.status_code == 400  # not connected

    set_setting(db_session, "simplefin_access_url", ACCESS_URL)
    db_session.commit()
    r = authed_client.post("/api/simplefin/sync")
    assert r.status_code == 400  # connected but nothing mapped


def test_sync_route_end_to_end(authed_client, db_session, monkeypatch):
    ba = _mk_bank_account(db_session, name="Synced Checking")
    set_setting(db_session, "simplefin_access_url", ACCESS_URL)
    set_setting(db_session, "simplefin_account_map", json.dumps({"ACT-1": ba.id}))
    db_session.commit()
    monkeypatch.setattr(sf, "send", lambda req, **kw: _resp(json_body=SF_DATA))

    r = authed_client.post("/api/simplefin/sync")
    assert r.status_code == 200
    assert r.json()["imported"] == 2
    assert get_setting_raw(db_session, "simplefin_last_sync")

    status = authed_client.get("/api/simplefin/status").json()
    assert status["connected"] is True
    assert status["accounts"][0]["name"] == "Demo Checking"
    assert status["account_map"] == {"ACT-1": ba.id}


def test_sync_route_bridge_down_is_502(authed_client, db_session, monkeypatch):
    ba = _mk_bank_account(db_session, name="Down Checking")
    set_setting(db_session, "simplefin_access_url", ACCESS_URL)
    set_setting(db_session, "simplefin_account_map", json.dumps({"ACT-1": ba.id}))
    db_session.commit()
    monkeypatch.setattr(sf, "send", lambda req, **kw: _resp(status=500))
    r = authed_client.post("/api/simplefin/sync")
    assert r.status_code == 502


def test_disconnect_route_clears_settings(authed_client, db_session):
    set_setting(db_session, "simplefin_access_url", ACCESS_URL)
    set_setting(db_session, "simplefin_account_map", '{"ACT-1": 1}')
    db_session.commit()
    r = authed_client.post("/api/simplefin/disconnect")
    assert r.status_code == 200
    assert (get_setting_raw(db_session, "simplefin_access_url") or "") == ""
    status = authed_client.get("/api/simplefin/status").json()
    assert status["connected"] is False


# ---------------------------------------------------------------------------
# SSRF guard — user-supplied bridge URLs must never reach private space
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/simplefin/claim/X",
        "https://10.0.0.5/simplefin/claim/X",
        "https://192.168.68.1/simplefin/claim/X",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/simplefin/claim/X",
        "http://bridge.example.com/simplefin/claim/X",
    ],
)
def test_ssrf_guard_rejects_non_public(url):
    with pytest.raises(sf.SimpleFINError):
        sf._assert_public_https(url)


def test_ssrf_guard_allows_public_literal():
    # Literal public IP: getaddrinfo resolves numerically, no DNS involved
    sf._assert_public_https("https://1.1.1.1/simplefin")
