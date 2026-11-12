"""A second SimpleFIN feed into one account is refused (#819).

SimpleFIN took two of its feeds mapped into one chart account where Mercury
refuses the same mapping, and two feeds into one register bring its lines in
twice. POST /api/simplefin/map now refuses with Mercury's sentence, whether
the two rows name the feed record or the chart account, and saving a feed
again onto the account it already has is not a second feed."""

import json

from app.models.accounts import Account, AccountType
from app.models.banking import BankAccount
from app.services.settings_service import get_setting_raw, set_setting
from tests.test_simplefin import ACCESS_URL

REFUSAL = "Map each SimpleFIN account to a separate bank account"


def _connected(db, account_map=None):
    set_setting(db, "simplefin_access_url", ACCESS_URL)
    set_setting(db, "simplefin_account_map", json.dumps(account_map or {}))
    db.commit()


def _saved_map(db):
    db.expire_all()
    return json.loads(get_setting_raw(db, "simplefin_account_map") or "{}")


def _bank(db, name):
    acct = Account(name=name, account_type=AccountType.ASSET, bank_kind="bank")
    db.add(acct)
    db.commit()
    feed = BankAccount(name=name, account_id=acct.id)
    db.add(feed)
    db.commit()
    return acct, feed


def test_two_feeds_into_one_feed_record_are_refused(authed_client, db_session):
    _, feed = _bank(db_session, "Operating")
    _connected(db_session, {"ACT-1": feed.id})
    r = authed_client.post(
        "/api/simplefin/map", json={"mapping": {"ACT-1": feed.id, "ACT-2": feed.id}}
    )
    assert r.status_code == 400
    assert r.json()["detail"] == REFUSAL
    assert _saved_map(db_session) == {"ACT-1": feed.id}


def test_two_feeds_into_one_chart_account_are_refused(authed_client, db_session):
    acct, feed = _bank(db_session, "Operating")
    _connected(db_session)
    # one row by the feed record, the other by the chart account it belongs to
    r = authed_client.post(
        "/api/simplefin/map",
        json={"mapping": {"ACT-1": feed.id}, "ledger_mapping": {"ACT-2": acct.id}},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == REFUSAL
    # both by a chart account with no feed record yet: none is made
    bare = Account(name="Savings", account_type=AccountType.ASSET, bank_kind="bank")
    db_session.add(bare)
    db_session.commit()
    r = authed_client.post(
        "/api/simplefin/map",
        json={"ledger_mapping": {"ACT-1": bare.id, "ACT-2": bare.id}},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == REFUSAL
    db_session.expire_all()
    assert (
        db_session.query(BankAccount).filter(BankAccount.account_id == bare.id).count()
        == 0
    )
    assert _saved_map(db_session) == {}


def test_two_feed_records_on_one_chart_account_are_one_register(
    authed_client, db_session
):
    acct, feed = _bank(db_session, "Operating")
    twin = BankAccount(name="Operating (old)", account_id=acct.id)
    db_session.add(twin)
    db_session.commit()
    _connected(db_session)
    r = authed_client.post(
        "/api/simplefin/map", json={"mapping": {"ACT-1": feed.id, "ACT-2": twin.id}}
    )
    assert r.status_code == 400
    assert r.json()["detail"] == REFUSAL


def test_a_feed_saved_again_onto_its_own_account_is_allowed(authed_client, db_session):
    acct, feed = _bank(db_session, "Operating")
    _, other = _bank(db_session, "Savings")
    _connected(db_session, {"ACT-1": feed.id, "ACT-2": other.id})
    # the same mapping saved again
    r = authed_client.post(
        "/api/simplefin/map", json={"mapping": {"ACT-1": feed.id, "ACT-2": other.id}}
    )
    assert r.status_code == 200
    assert r.json()["account_map"] == {"ACT-1": feed.id, "ACT-2": other.id}
    # the same row named both ways at once (a feed record and its chart account)
    r = authed_client.post(
        "/api/simplefin/map",
        json={
            "mapping": {"ACT-1": feed.id, "ACT-2": other.id},
            "ledger_mapping": {"ACT-1": acct.id},
        },
    )
    assert r.status_code == 200
    assert _saved_map(db_session) == {"ACT-1": feed.id, "ACT-2": other.id}
    # moving a feed onto the account another feed leaves in the same save
    r = authed_client.post("/api/simplefin/map", json={"mapping": {"ACT-2": feed.id}})
    assert r.status_code == 200
    assert _saved_map(db_session) == {"ACT-2": feed.id}
