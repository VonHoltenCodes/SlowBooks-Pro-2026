"""A bank feed's "Imports into" list offers the chart's own bank accounts.

A support email (2.31.1): SimpleFIN connected, the bank's accounts listed, and
the "Imports into" dropdown beside each feed empty. It listed only bank feed
records (BankAccount), which exist once an account is added with + New Bank
Account on the Banking page; a company whose bank accounts are in the chart saw
an empty list and no word why (Mercury's section had the hint, SimpleFIN's did
not). Both lists now offer every bank account in the chart (SimpleFIN its
credit-card accounts too), and choosing one with no feed record makes it."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.models.accounts import Account, AccountType
from app.models.banking import BankAccount
from app.services.settings_service import set_setting

from tests.test_mercury import (  # noqa: F401  (fixtures)
    ACCOUNT,
    TOKEN,
    transport,
)
from tests.test_simplefin import ACCESS_URL

ROOT = Path(__file__).resolve().parent.parent


def _ledger(db, name, kind="bank", active=True):
    a = Account(
        name=name,
        bank_kind=kind,
        account_type=(
            AccountType.LIABILITY if kind == "credit_card" else AccountType.ASSET
        ),
        is_active=active,
    )
    db.add(a)
    db.commit()
    return a


def _feeds_for(db, account_id):
    return db.query(BankAccount).filter(BankAccount.account_id == account_id).all()


def test_simplefin_maps_a_chart_bank_account_with_no_feed_record(
    authed_client, db_session
):
    set_setting(db_session, "simplefin_access_url", ACCESS_URL)
    db_session.commit()
    checking = _ledger(db_session, "Operating Checking")
    card = _ledger(db_session, "Company Visa", kind="credit_card")
    assert _feeds_for(db_session, checking.id) == []

    r = authed_client.post(
        "/api/simplefin/map",
        json={"ledger_mapping": {"ACT-1": checking.id, "ACT-2": card.id}},
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    (feed,) = _feeds_for(db_session, checking.id)
    (card_feed,) = _feeds_for(db_session, card.id)
    assert feed.name == "Operating Checking" and feed.is_active
    assert r.json()["account_map"] == {"ACT-1": feed.id, "ACT-2": card_feed.id}

    # mapped again: the same feed record, never a second one
    r = authed_client.post(
        "/api/simplefin/map", json={"ledger_mapping": {"ACT-1": checking.id}}
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert len(_feeds_for(db_session, checking.id)) == 1
    assert r.json()["account_map"]["ACT-1"] == feed.id
    # an existing feed record still maps by its own id, as before
    r = authed_client.post("/api/simplefin/map", json={"mapping": {"ACT-1": feed.id}})
    assert r.status_code == 200 and r.json()["account_map"] == {"ACT-1": feed.id}


def test_simplefin_refuses_an_account_that_is_not_a_bank_account(
    authed_client, db_session, seed_accounts
):
    set_setting(db_session, "simplefin_access_url", ACCESS_URL)
    db_session.commit()
    income = seed_accounts["4000"]
    r = authed_client.post(
        "/api/simplefin/map", json={"ledger_mapping": {"ACT-1": income.id}}
    )
    assert r.status_code == 400, r.text
    assert "is not a bank or credit-card account" in r.json()["detail"]
    retired = _ledger(db_session, "Old Savings", active=False)
    r = authed_client.post(
        "/api/simplefin/map", json={"ledger_mapping": {"ACT-1": retired.id}}
    )
    assert r.status_code == 400 and "inactive" in r.json()["detail"]
    db_session.expire_all()
    assert _feeds_for(db_session, income.id) == []
    assert _feeds_for(db_session, retired.id) == []


def test_mercury_maps_a_chart_bank_account_and_refuses_a_card(
    client, db_session, seed_accounts, transport  # noqa: F811
):
    assert (
        client.post("/api/mercury/connect", json={"api_token": TOKEN}).status_code
        == 200
    )
    checking = _ledger(db_session, "Mercury Checking")
    r = client.post("/api/mercury/map", json={"ledger_mapping": {ACCOUNT: checking.id}})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    (feed,) = _feeds_for(db_session, checking.id)
    assert r.json()["account_map"] == {ACCOUNT: feed.id}

    card = _ledger(db_session, "Mercury Card", kind="credit_card")
    r = client.post("/api/mercury/map", json={"ledger_mapping": {ACCOUNT: card.id}})
    assert r.status_code == 400
    assert "bank accounts only" in r.json()["detail"]
    db_session.expire_all()
    assert _feeds_for(db_session, card.id) == []


def test_the_page_offers_the_chart_and_says_what_to_do_when_it_is_empty():
    js = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    assert "_importOptions(rows, feeds, mapped, kinds) {" in js
    assert 'opts.push(`<option value="acct:${a.account_id}"' in js
    # each section's row cell draws the list (#718: or says why it is empty)
    assert (
        "BankingPage._importCell('mercury', a.id, a.name, rows, feeds, feed.account_map[a.id], ['bank'])"
        in js
    )
    assert (
        "BankingPage._importCell('simplefin', a.id, a.name, rows, feeds, feed.account_map[a.id], ['bank', 'credit_card'])"
        in js
    )
    assert "const opts = BankingPage._importOptions(rows, feeds, mapped, kinds);" in js
    # both saves send a chart account as ledger_mapping
    assert (
        "await API.post('/simplefin/map', { mapping, ledger_mapping, reversed });" in js
    )
    assert "await API.post('/mercury/map', { mapping, ledger_mapping });" in js
    # a row with nothing to offer says so in the row, with both ways out
    assert "No account in your chart is marked as ${what} yet." in js
    assert ">+ New bank account</button>" in js
    assert ">Use an account I already have…</button>" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_both_connected_sections_draw_the_chart_accounts():
    """Each connected section named its table rows `rows`, the name of the
    chart's accounts it was given, so the first look at the chart threw
    ("Cannot access 'rows' before initialization") and a connected feed's
    section never drew. Drawn here with the chart's accounts and a feed
    record no longer listed: each mapping box offers both, with the one
    mapped chosen."""
    probe = r"""
const fs = require('fs'), vm = require('vm');
const ctx = { console, window: {}, document: { addEventListener: () => {} },
  location: { origin: 'http://127.0.0.1:3001', hash: '#/banking' },
  setTimeout: () => 0, clearTimeout: () => {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/utils.js', 'utf8')
  + '\n' + fs.readFileSync('app/static/js/banking.js', 'utf8')
  + '\nthis.BankingPage = BankingPage;', ctx);
const rows = [
  { account_id: 7, account_number: '1000', name: 'Operating Checking', bank_kind: 'bank',
    feed: { bank_account_id: 3, name: 'Operating Checking' } },
  { account_id: 8, account_number: '2100', name: 'Company Visa', bank_kind: 'credit_card', feed: null },
];
const feeds = [{ id: 3, name: 'Operating Checking', is_active: true, bank_kind: 'bank' },
               { id: 9, name: 'Old Savings', is_active: true, bank_kind: 'bank', account_name: null }];
const mercury = ctx.BankingPage._renderMercurySection({ connected: true,
  accounts: [{ id: 'm1', name: 'Mercury Checking', last_four: '1234', balance: '10.00',
               available_balance: '10.00' }],
  account_map: { m1: 3 }, webhook: { path: '/api/mercury/webhook' }, last_sync: null }, feeds, rows);
const simplefin = ctx.BankingPage._renderFeedSection({ connected: true,
  accounts: [{ id: 'ACT-1', name: 'Demo Checking', org: 'Demo', balance: '1.00', currency: 'USD' }],
  account_map: { 'ACT-1': 9 }, reversed: [], last_sync: null }, feeds, rows);
console.log(JSON.stringify({ mercury, simplefin }));
"""
    out = subprocess.run(
        ["node", "-e", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    mercury, simplefin = got["mercury"], got["simplefin"]
    # Mercury: bank accounts only, the mapped feed's chart account chosen
    assert '<option value="acct:7" selected>' in mercury
    assert 'value="acct:8"' not in mercury
    assert '<option value="feed:9">Old Savings</option>' in mercury
    assert "Mercury Checking" in mercury and "No account in your chart" not in mercury
    # SimpleFIN: bank and card accounts, the unlisted feed record kept and chosen
    assert 'value="acct:7"' in simplefin and 'value="acct:8"' in simplefin
    assert '<option value="feed:9" selected>Old Savings</option>' in simplefin
    assert "Demo Checking" in simplefin and "No account in your chart" not in simplefin
