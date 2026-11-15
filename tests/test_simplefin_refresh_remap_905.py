"""SimpleFIN: refresh the account list, move a mapping, per-feed sync dates
and updating the token without disconnecting (#905).

A reissued card comes through SimpleFIN as a new account id. Before this the
only way to re-read the account list was a sync (which needs a mapping), the
old card's mapping blocked the new one (#819), a newly mapped feed took its
first transactions from the shared last sync rather than the first-sync
window, and a refused credential could only be replaced by disconnecting,
which forgets every mapping. The bridge is stubbed as in test_simplefin."""

import json
from datetime import date, datetime, time, timedelta, timezone

from app.models.audit import AuditLog
from app.models.banking import BankAccount, BankTransaction
from app.models.settings import Settings
from app.services import simplefin_service as sf
from app.services.settings_service import get_setting_raw, set_setting
from tests.test_simplefin import ACCESS_URL, _bearer, _resp, _token

NEW_ACCESS_URL = "https://user999:pass999@bridge.example.com/simplefin"


def _epoch(day):
    return int(datetime.combine(day, time(12), tzinfo=timezone.utc).timestamp())


def _acct(sf_id, name, txns=(), balance="10.00"):
    return {
        "id": sf_id,
        "name": name,
        "org": {"name": "Demo Bank"},
        "currency": "USD",
        "balance": balance,
        "transactions": list(txns),
    }


def _txn(txn_id, day, amount="-5.00"):
    return {"id": txn_id, "posted": _epoch(day), "amount": amount, "payee": txn_id}


def _bridge(monkeypatch, *bodies):
    """Answer each request with the next body; return the requests made."""
    calls = []
    queue = list(bodies)

    def fake_send(req, **kw):
        calls.append(req)
        body = queue.pop(0)
        return _resp(text=body) if isinstance(body, str) else _resp(json_body=body)

    monkeypatch.setattr(sf, "send", fake_send)
    return calls


def _bank(db, name):
    ba = BankAccount(name=name, bank_name="Demo Bank")
    db.add(ba)
    db.commit()
    return ba


def _connect(db, account_map=None, cache=None, reversed_ids=None, **extra):
    set_setting(db, "simplefin_access_url", ACCESS_URL)
    set_setting(db, "simplefin_account_map", json.dumps(account_map or {}))
    set_setting(db, "simplefin_accounts_cache", json.dumps(cache or []))
    set_setting(db, "simplefin_reversed", json.dumps(reversed_ids or []))
    for key, value in extra.items():
        set_setting(db, key, value)
    db.commit()


def _setting(db, key):
    db.expire_all()
    return get_setting_raw(db, key)


def _summary(sf_id, name):
    return {
        "id": sf_id,
        "name": name,
        "org": "Demo Bank",
        "currency": "USD",
        "balance": "10.00",
    }


# ---------------------------------------------------------------------------
# 1. Refresh accounts
# ---------------------------------------------------------------------------


def test_refresh_reads_balances_only_and_works_with_nothing_mapped(
    authed_client, db_session, monkeypatch
):
    _connect(db_session, cache=[_summary("ACT-1", "Checking")])
    calls = _bridge(
        monkeypatch,
        {
            "errors": [],
            "accounts": [_acct("ACT-1", "Checking"), _acct("ACT-2", "New Card")],
        },
    )
    r = authed_client.post("/api/simplefin/refresh")
    assert r.status_code == 200, r.text
    body = r.json()
    # balances only: no transactions asked for, no date window
    assert calls[0]["url"].endswith("/accounts")
    assert calls[0]["params"] == {"balances-only": 1}
    assert body["added"] == ["ACT-2"]
    assert body["gone"] == []
    assert {a["id"]: a["state"] for a in body["accounts"]} == {
        "ACT-1": "new",
        "ACT-2": "new",
    }
    cache = json.loads(_setting(db_session, "simplefin_accounts_cache"))
    assert [a["id"] for a in cache] == ["ACT-1", "ACT-2"]


def test_refresh_keeps_a_mapped_account_the_bridge_no_longer_reports_as_gone(
    authed_client, db_session, monkeypatch
):
    old = _bank(db_session, "Old Card")
    _connect(
        db_session,
        {"OLD": old.id},
        cache=[_summary("OLD", "Visa ...1111"), _summary("SPARE", "Spare")],
    )
    _bridge(monkeypatch, {"errors": [], "accounts": [_acct("NEW", "Visa ...2222")]})
    body = authed_client.post("/api/simplefin/refresh").json()
    states = {a["id"]: (a["state"], a["name"]) for a in body["accounts"]}
    # the mapped one stays, named as it was; the unmapped one simply leaves
    assert states == {
        "NEW": ("new", "Visa ...2222"),
        "OLD": ("gone", "Visa ...1111"),
    }
    assert body["gone"] == ["OLD"]
    # the mapping itself is untouched
    assert body["account_map"] == {"OLD": old.id}
    # and status says the same on the next page load
    status = authed_client.get("/api/simplefin/status").json()
    assert {a["id"]: a["state"] for a in status["accounts"]} == {
        "NEW": "new",
        "OLD": "gone",
    }


def test_status_marks_a_mapped_id_missing_from_an_old_cache_as_gone(
    authed_client, db_session
):
    """A cache written before 2.32.0 has no gone flags: a mapped id that is
    not in it is still drawn, as gone, so its mapping can be moved."""
    ba = _bank(db_session, "Checking")
    _connect(db_session, {"LOST": ba.id}, cache=[_summary("ACT-1", "Savings")])
    status = authed_client.get("/api/simplefin/status").json()
    assert {a["id"]: a["state"] for a in status["accounts"]} == {
        "ACT-1": "new",
        "LOST": "gone",
    }


def test_refresh_needs_a_connection_and_reports_a_bridge_failure(
    authed_client, db_session, monkeypatch
):
    assert authed_client.post("/api/simplefin/refresh").status_code == 400
    _connect(db_session)
    monkeypatch.setattr(sf, "send", lambda req, **kw: _resp(status=500))
    assert authed_client.post("/api/simplefin/refresh").status_code == 502


# ---------------------------------------------------------------------------
# 2. Move this mapping to…
# ---------------------------------------------------------------------------


def test_move_carries_the_account_and_reverse_signs_to_the_new_id(
    authed_client, db_session
):
    card = _bank(db_session, "Business Visa")
    other = _bank(db_session, "Checking")
    _connect(
        db_session,
        {"OLD": card.id, "CHK": other.id},
        cache=[
            dict(_summary("OLD", "Visa ...1111"), gone=True),
            _summary("NEW", "Visa ...2222"),
            _summary("CHK", "Checking"),
        ],
        reversed_ids=["OLD"],
        simplefin_feed_last_sync=json.dumps(
            {"OLD": "2026-11-01T00:00:00+00:00", "CHK": "2026-11-01T00:00:00+00:00"}
        ),
    )
    settings_row = (
        db_session.query(Settings).filter(Settings.key == "simplefin_account_map").one()
    )
    db_session.query(AuditLog).filter(AuditLog.table_name == "settings").delete()
    db_session.commit()

    r = authed_client.post(
        "/api/simplefin/move", json={"from_id": "OLD", "to_id": "NEW"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["account_map"] == {"NEW": card.id, "CHK": other.id}
    assert body["reversed"] == ["NEW"]
    # the new id has synced nothing: its first sync takes the full window
    assert body["feed_last_sync"] == {"CHK": "2026-11-01T00:00:00+00:00"}
    # the old row, unmapped and gone, leaves the table
    assert {a["id"]: a["state"] for a in body["accounts"]} == {
        "NEW": "mapped",
        "CHK": "mapped",
    }
    # audited, as every settings write is
    rows = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.table_name == "settings",
            AuditLog.record_id == settings_row.id,
        )
        .all()
    )
    assert any(
        r.new_values and json.loads(r.new_values["value"]).get("NEW") == card.id
        for r in rows
    )


def test_move_without_reverse_signs_leaves_the_new_id_as_the_bank_sends_it(
    authed_client, db_session
):
    card = _bank(db_session, "Card")
    _connect(
        db_session,
        {"OLD": card.id},
        cache=[_summary("OLD", "Old"), _summary("NEW", "New")],
        reversed_ids=["NEW"],  # a stale choice on the unmapped row
    )
    body = authed_client.post(
        "/api/simplefin/move", json={"from_id": "OLD", "to_id": "NEW"}
    ).json()
    assert body["account_map"] == {"NEW": card.id}
    assert body["reversed"] == []


def test_move_refusals(authed_client, db_session):
    card = _bank(db_session, "Card")
    chk = _bank(db_session, "Checking")
    _connect(
        db_session,
        {"OLD": card.id, "CHK": chk.id},
        cache=[
            _summary("OLD", "Old"),
            _summary("CHK", "Checking"),
            _summary("NEW", "New"),
            dict(_summary("GONE", "Gone"), gone=True),
        ],
    )

    def move(from_id, to_id):
        return authed_client.post(
            "/api/simplefin/move", json={"from_id": from_id, "to_id": to_id}
        )

    # from an id that is not mapped
    assert move("NEW", "OLD").status_code == 400
    # onto itself
    assert move("OLD", "OLD").status_code == 400
    # onto an id the bridge does not report (unknown, or gone)
    assert move("OLD", "NOPE").status_code == 400
    assert move("OLD", "GONE").status_code == 400
    # onto one that already imports somewhere: #819's rule still holds
    r = move("OLD", "CHK")
    assert r.status_code == 400
    assert "already imports" in r.json()["detail"]
    assert json.loads(_setting(db_session, "simplefin_account_map")) == {
        "OLD": card.id,
        "CHK": chk.id,
    }
    # and the map route still refuses two feeds into one account
    r = authed_client.post(
        "/api/simplefin/map", json={"mapping": {"OLD": card.id, "NEW": card.id}}
    )
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "Map each SimpleFIN account to a separate bank account"
    )


# ---------------------------------------------------------------------------
# 3. Per-feed last sync
# ---------------------------------------------------------------------------


def test_a_newly_mapped_feed_gets_the_first_sync_window(
    authed_client, db_session, monkeypatch
):
    today = date.today()
    chk = _bank(db_session, "Checking")
    card = _bank(db_session, "New Card")
    recent = (today - timedelta(days=2)).isoformat() + "T00:00:00+00:00"
    _connect(
        db_session,
        {"CHK": chk.id, "CARD": card.id},
        simplefin_last_sync=recent,
        simplefin_feed_last_sync=json.dumps({"CHK": recent}),
    )
    old_day = today - timedelta(days=40)  # inside CARD's window, not CHK's
    calls = _bridge(
        monkeypatch,
        {
            "errors": [],
            "accounts": [
                _acct(
                    "CHK",
                    "Checking",
                    [_txn("C-OLD", old_day), _txn("C-NEW", today)],
                ),
                _acct("CARD", "New Card", [_txn("K-OLD", old_day)]),
            ],
        },
    )
    r = authed_client.post("/api/simplefin/sync")
    assert r.status_code == 200, r.text
    first_window = today - timedelta(days=sf.FIRST_SYNC_DAYS)
    assert calls[0]["params"]["start-date"] == sf._epoch(first_window)
    assert r.json()["since"] == first_window.isoformat()

    def imported(ba):
        return {
            t.import_id
            for t in db_session.query(BankTransaction).filter(
                BankTransaction.bank_account_id == ba.id
            )
        }

    db_session.expire_all()
    # the new card's 40-day-old line came in; checking took only its own
    # window (its last sync less the overlap), not the card's reach
    assert imported(card) == {"K-OLD"}
    assert imported(chk) == {"C-NEW"}
    dates = json.loads(_setting(db_session, "simplefin_feed_last_sync"))
    assert set(dates) == {"CHK", "CARD"}
    assert dates["CHK"] == dates["CARD"] == _setting(db_session, "simplefin_last_sync")


def test_a_connection_from_before_seeds_its_feeds_from_the_shared_date(
    authed_client, db_session, monkeypatch
):
    """No per-feed dates kept yet: the feeds mapped then synced to the shared
    date, so a sync reaches back the overlap from it, as before."""
    chk = _bank(db_session, "Checking")
    _connect(
        db_session,
        {"CHK": chk.id},
        simplefin_last_sync="2026-11-10T08:00:00+00:00",
    )
    assert (get_setting_raw(db_session, "simplefin_feed_last_sync") or "") == ""
    status = authed_client.get("/api/simplefin/status").json()
    assert status["feed_last_sync"] == {"CHK": "2026-11-10T08:00:00+00:00"}
    calls = _bridge(monkeypatch, {"errors": [], "accounts": [_acct("CHK", "Chk")]})
    assert authed_client.post("/api/simplefin/sync").status_code == 200
    start = date(2026, 11, 10) - timedelta(days=sf.RESYNC_OVERLAP_DAYS)
    assert calls[0]["params"]["start-date"] == sf._epoch(start)


def test_mapping_a_feed_after_upgrade_does_not_inherit_the_shared_date(
    authed_client, db_session, monkeypatch
):
    chk = _bank(db_session, "Checking")
    card = _bank(db_session, "Card")
    _connect(
        db_session,
        {"CHK": chk.id},
        simplefin_last_sync="2026-11-10T08:00:00+00:00",
    )
    r = authed_client.post(
        "/api/simplefin/map", json={"mapping": {"CHK": chk.id, "CARD": card.id}}
    )
    assert r.status_code == 200
    dates = json.loads(_setting(db_session, "simplefin_feed_last_sync"))
    assert dates == {"CHK": "2026-11-10T08:00:00+00:00"}
    # unmapping forgets the date: mapped again, the feed starts afresh
    authed_client.post("/api/simplefin/map", json={"mapping": {"CARD": card.id}})
    assert json.loads(_setting(db_session, "simplefin_feed_last_sync")) == {}


def test_a_feed_the_bridge_skipped_keeps_its_date(
    authed_client, db_session, monkeypatch
):
    chk = _bank(db_session, "Checking")
    card = _bank(db_session, "Card")
    when = "2026-11-01T00:00:00+00:00"
    _connect(
        db_session,
        {"CHK": chk.id, "CARD": card.id},
        simplefin_last_sync=when,
        simplefin_feed_last_sync=json.dumps({"CHK": when, "CARD": when}),
    )
    _bridge(monkeypatch, {"errors": [], "accounts": [_acct("CHK", "Chk")]})
    r = authed_client.post("/api/simplefin/sync")
    assert r.status_code == 200
    assert r.json()["warnings"]  # the missing account is still warned about
    dates = json.loads(_setting(db_session, "simplefin_feed_last_sync"))
    assert dates["CARD"] == when
    assert dates["CHK"] != when


def test_claim_and_disconnect_clear_the_per_feed_dates(
    authed_client, db_session, monkeypatch
):
    _connect(db_session, simplefin_feed_last_sync=json.dumps({"A": "2026-11-01"}))
    _bridge(monkeypatch, ACCESS_URL, {"errors": [], "accounts": []})
    assert (
        authed_client.post(
            "/api/simplefin/claim", json={"setup_token": _token()}
        ).status_code
        == 200
    )
    assert _setting(db_session, "simplefin_feed_last_sync") == "{}"
    set_setting(db_session, "simplefin_feed_last_sync", json.dumps({"A": "x"}))
    db_session.commit()
    authed_client.post("/api/simplefin/disconnect")
    assert _setting(db_session, "simplefin_feed_last_sync") == "{}"


# ---------------------------------------------------------------------------
# 4. Update token without disconnecting
# ---------------------------------------------------------------------------


def test_update_token_keeps_mappings_and_reports_ids_no_longer_present(
    authed_client, db_session, monkeypatch
):
    chk = _bank(db_session, "Checking")
    card = _bank(db_session, "Card")
    when = "2026-11-01T00:00:00+00:00"
    _connect(
        db_session,
        {"CHK": chk.id, "OLD": card.id},
        cache=[_summary("CHK", "Checking"), _summary("OLD", "Visa ...1111")],
        reversed_ids=["CHK", "OLD", "STALE"],
        simplefin_last_sync=when,
        simplefin_feed_last_sync=json.dumps({"CHK": when, "OLD": when}),
    )
    calls = _bridge(
        monkeypatch,
        NEW_ACCESS_URL,
        {
            "errors": [],
            "accounts": [_acct("CHK", "Checking"), _acct("NEW", "Visa ...2222")],
        },
    )
    r = authed_client.post(
        "/api/simplefin/update-token", json={"setup_token": _token()}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # the new credential is what is stored now, and it is used balances-only
    assert _setting(db_session, "simplefin_access_url") == NEW_ACCESS_URL
    assert calls[1]["auth"] == ("user999", "pass999")
    assert calls[1]["params"] == {"balances-only": 1}
    # every mapping, reversal and date kept; the missing one reported
    assert body["account_map"] == {"CHK": chk.id, "OLD": card.id}
    assert body["reversed"] == ["CHK", "OLD"]
    assert body["feed_last_sync"] == {"CHK": when, "OLD": when}
    assert body["missing"] == [{"id": "OLD", "name": "Visa ...1111"}]
    assert {a["id"]: a["state"] for a in body["accounts"]} == {
        "CHK": "mapped",
        "NEW": "new",
        "OLD": "gone",
    }
    assert _setting(db_session, "simplefin_last_sync") == when
    # and the gone mapping moves to the reissued card in one step
    moved = authed_client.post(
        "/api/simplefin/move", json={"from_id": "OLD", "to_id": "NEW"}
    ).json()
    assert moved["account_map"] == {"CHK": chk.id, "NEW": card.id}
    assert moved["reversed"] == ["CHK", "NEW"]


def test_update_token_needs_a_connection_and_a_good_token(
    authed_client, db_session, monkeypatch
):
    r = authed_client.post(
        "/api/simplefin/update-token", json={"setup_token": _token()}
    )
    assert r.status_code == 400  # not connected: Connect is the way in
    ba = _bank(db_session, "Checking")
    _connect(db_session, {"CHK": ba.id})
    monkeypatch.setattr(sf, "send", lambda req, **kw: _resp(status=403))
    r = authed_client.post(
        "/api/simplefin/update-token", json={"setup_token": _token()}
    )
    assert r.status_code == 400
    # nothing changed
    assert _setting(db_session, "simplefin_access_url") == ACCESS_URL
    assert json.loads(_setting(db_session, "simplefin_account_map")) == {"CHK": ba.id}


def test_a_refused_credential_points_to_update_token(monkeypatch):
    monkeypatch.setattr(sf, "send", lambda req, **kw: _resp(status=403))
    try:
        sf.fetch_accounts(ACCESS_URL)
    except sf.SimpleFINError as exc:
        message = str(exc)
    else:  # pragma: no cover
        raise AssertionError("a 403 must raise")
    assert "Update token" in message
    assert "disconnect" not in message.lower()


# ---------------------------------------------------------------------------
# Roles and the page
# ---------------------------------------------------------------------------


def test_refresh_move_and_update_token_are_admin_only(
    authed_client, db_session, monkeypatch
):
    keeper = _bearer(
        authed_client.post(
            "/api/tokens", json={"label": "keeper", "role": "bookkeeper"}
        ).json()["token"]
    )
    reader = _bearer(
        authed_client.post(
            "/api/tokens", json={"label": "reader", "role": "readonly"}
        ).json()["token"]
    )
    card = _bank(db_session, "Card")
    _connect(
        db_session,
        {"OLD": card.id},
        cache=[_summary("OLD", "Old"), _summary("NEW", "New")],
    )

    def no_network(req, **kw):  # pragma: no cover - must not be reached
        raise AssertionError("a refused call reached the bridge")

    monkeypatch.setattr(sf, "send", no_network)
    for who in (keeper, reader):
        assert who.post("/api/simplefin/refresh").status_code == 403
        assert (
            who.post(
                "/api/simplefin/move", json={"from_id": "OLD", "to_id": "NEW"}
            ).status_code
            == 403
        )
        assert (
            who.post(
                "/api/simplefin/update-token", json={"setup_token": _token()}
            ).status_code
            == 403
        )
    assert json.loads(_setting(db_session, "simplefin_account_map")) == {"OLD": card.id}
    assert _setting(db_session, "simplefin_access_url") == ACCESS_URL


def test_the_banking_page_offers_refresh_move_and_update_token():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    js = (root / "app/static/js/banking.js").read_text(encoding="utf-8")
    assert 'data-admin onclick="BankingPage.refreshSimpleFIN()">Refresh accounts' in js
    assert (
        'data-admin onclick="BankingPage.showSimpleFINUpdateToken()">Update token…'
        in js
    )
    assert 'onclick="BankingPage.showSimpleFINMove(this)">Move this mapping to…' in js
    assert "Gone from SimpleFIN" in js and "New — not mapped" in js
    assert "API.post('/simplefin/refresh')" in js
    assert "API.post('/simplefin/move', { from_id: form.dataset.sfid" in js
    assert "API.post('/simplefin/update-token', { setup_token: token })" in js
    app_js = (root / "app/static/js/app.js").read_text(encoding="utf-8")
    for name in ("refreshSimpleFIN", "showSimpleFINMove", "showSimpleFINUpdateToken"):
        assert f"'BankingPage.{name}'" in app_js
