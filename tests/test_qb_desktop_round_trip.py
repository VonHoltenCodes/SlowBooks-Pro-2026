"""The full QuickBooks Desktop round trip (#344): every posted transaction
type exports (checks, deposits from the register, card charges, transfers,
bill payments and journal entries joined the IIF), the ledger blocks come
back in once (a second upload posts nothing), an accountant's General
Journal report posts as adjusting entries and a report holding anything
else is refused whole. Nobody on the fleet runs QuickBooks, so the round
trip is tested SlowBooks to SlowBooks: a company's export imported into a
fresh company gives the same Trial Balance."""

import io
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction
from app.routes.reports.financial import trial_balance
from app.services import iif_import
from app.services.iif_export import export_all, export_journal_entries
from tests.conftest import CHART_OF_ACCOUNTS, _test_engine

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/qb_desktop/Transaction_Detail_by_Account.csv"
START, END = "2026-01-01", "2026-12-31"


def _ok(r):
    assert r.status_code in (200, 201), r.text
    return r.json()


def _books(client, accounts):
    """A little of everything the export used to leave behind."""
    bank, card = accounts["1000"].id, accounts["2100"].id
    ins, adv, svc = accounts["6300"].id, accounts["6000"].id, accounts["4000"].id
    _ok(
        client.post(
            "/api/banking/transactions",
            json={
                "account_id": bank,
                "date": "2026-03-05",
                "amount": "-25.00",
                "category_account_id": adv,
                "payee": "Print shop",
                "description": "Flyers",
                "check_number": "1041",
            },
        )
    )
    _ok(
        client.post(
            "/api/banking/transactions",
            json={
                "account_id": bank,
                "date": "2026-03-06",
                "amount": "100.00",
                "category_account_id": svc,
                "payee": "Walk-in",
                "description": "Cash sale",
            },
        )
    )
    _ok(
        client.post(
            "/api/cc-charges",
            json={
                "date": "2026-03-07",
                "payee": "Cloud host",
                "account_id": ins,
                "card_account_id": card,
                "amount": "40.00",
                "memo": "March hosting",
            },
        )
    )
    _ok(
        client.post(
            "/api/transfers",
            json={
                "date": "2026-03-08",
                "from_account_id": bank,
                "to_account_id": card,
                "amount": "40.00",
                "memo": "Pay the card",
            },
        )
    )
    vendor = _ok(client.post("/api/vendors", json={"name": "Harbor Properties"}))
    bill = _ok(
        client.post(
            "/api/bills",
            json={
                "vendor_id": vendor["id"],
                "bill_number": "RENT-3",
                "date": "2026-03-01",
                "lines": [
                    {
                        "description": "March rent",
                        "quantity": 1,
                        "rate": 500,
                        "account_id": ins,
                    }
                ],
            },
        )
    )
    _ok(
        client.post(
            "/api/bill-payments",
            json={
                "vendor_id": vendor["id"],
                "date": "2026-03-09",
                "amount": 500,
                "check_number": "1042",
                "pay_from_account_id": bank,
                "allocations": [{"bill_id": bill["id"], "amount": 500}],
            },
        )
    )
    _ok(
        client.post(
            "/api/journal",
            json={
                "date": "2026-03-31",
                "description": "Accrue insurance",
                "reference": "AJE-1",
                "lines": [
                    {"account_id": ins, "debit": "120.00", "credit": "0"},
                    {
                        "account_id": accounts["2000"].id,
                        "debit": "0",
                        "credit": "120.00",
                    },
                ],
            },
        )
    )


def _blocks(text):
    out = []
    for line in text.splitlines():
        if line.startswith("TRNS\t"):
            out.append(line.split("\t")[1])
    return out


def test_every_posted_type_exports(client, db_session, seed_accounts):
    _books(client, seed_accounts)
    text = export_all(db_session)
    types = _blocks(text)
    for t in (
        "CHECK",
        "DEPOSIT",
        "CREDIT CARD",
        "TRANSFER",
        "BILLPMT",
        "GENERAL JOURNAL",
        "BILL",
    ):
        assert t in types, (t, types)
    assert "TRNS\tCHECK\t03/05/2026\tChecking\tPrint shop\t-25.00\t1041" in text
    assert (
        "SPL\tCHECK\t03/05/2026\tAdvertising & Marketing\tPrint shop\t25.00\t1041"
        in text
    )
    assert "TRNS\tCREDIT CARD\t03/07/2026\tCredit Card\tCloud host\t-40.00" in text
    assert "TRNS\tTRANSFER\t03/08/2026\tChecking\t\t-40.00" in text
    assert "SPL\tTRANSFER\t03/08/2026\tCredit Card\t\t40.00" in text
    assert (
        "TRNS\tBILLPMT\t03/09/2026\tChecking\tHarbor Properties\t-500.00\t1042" in text
    )
    assert (
        "SPL\tBILLPMT\t03/09/2026\tAccounts Payable\tHarbor Properties\t500.00\t1042"
        in text
    )
    assert "TRNS\tGENERAL JOURNAL\t03/31/2026\tInsurance\t\t120.00\tAJE-1" in text
    assert (
        "SPL\tGENERAL JOURNAL\t03/31/2026\tAccounts Payable\t\t-120.00\tAJE-1" in text
    )


def test_the_round_trip_gives_a_fresh_company_the_same_trial_balance(
    client, db_session, seed_accounts
):
    _books(client, seed_accounts)
    text = export_all(db_session)
    home = trial_balance(None, None, db_session)
    # a second company, the same chart, nothing else
    engine = _test_engine()
    other = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        for data in CHART_OF_ACCOUNTS:
            other.add(
                Account(
                    account_number=data["account_number"],
                    name=data["name"],
                    account_type=AccountType(data["account_type"]),
                    bank_kind=data.get("bank_kind"),
                    detail_type=data.get("detail_type"),
                    is_system=True,
                    balance=Decimal("0"),
                )
            )
        other.commit()
        result = iif_import.import_all(other, text)
        assert result["errors"] == [], result["errors"]
        assert result["unsupported"] == 0
        assert result["ledger_entries"] >= 5 and result["journal_entries"] == 1
        away = trial_balance(None, None, other)

        def by(tb):
            return {
                i["account_number"]: (i["total_debit"], i["total_credit"])
                for i in tb["items"]
            }

        assert by(away) == by(home)
        assert away["total_debit"] == home["total_debit"]
        # a second import of the same file posts nothing
        again = iif_import.import_all(other, text)
        assert again["ledger_entries"] == 0 and again["journal_entries"] == 0
        assert again["bills"] == 0 and again["deposits"] == 0
        assert by(trial_balance(None, None, other)) == by(home)
    finally:
        other.close()
        engine.dispose()


def test_re_importing_a_companys_own_export_posts_nothing_twice(
    client, db_session, seed_accounts
):
    _books(client, seed_accounts)
    text = export_all(db_session)
    before = db_session.query(Transaction).count()
    r = client.post(
        "/api/iif/import",
        files={"file": ("export.iif", io.BytesIO(text.encode("utf-8")), "text/plain")},
    )
    body = _ok(r)
    assert body["errors"] == [] and body["unsupported"] == 0
    assert body["ledger_entries"] == 0 and body["journal_entries"] == 0
    assert body["duplicates_skipped"] >= 6
    assert db_session.query(Transaction).count() == before


GJ_IIF = (
    "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tMEMO\tCLASS\n"
    "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tMEMO\tCLASS\n"
    "!ENDTRNS\n"
    "TRNS\tGENERAL JOURNAL\t03/31/2026\tInsurance\t\t120.00\tAJE-7\tYear-end accrual\t\n"
    "SPL\tGENERAL JOURNAL\t03/31/2026\tAccounts Payable\t\t-120.00\tAJE-7\tYear-end accrual\t\n"
    "ENDTRNS\n"
)


def test_general_journal_blocks_come_back_once_and_as_adjusting_on_request(
    client, db_session, seed_accounts
):
    def upload(adjusting):
        return _ok(
            client.post(
                "/api/iif/import",
                files={
                    "file": (
                        "aje.iif",
                        io.BytesIO(GJ_IIF.encode("utf-8")),
                        "text/plain",
                    )
                },
                data={"adjusting": "true" if adjusting else "false"},
            )
        )

    body = upload(adjusting=True)
    assert body["journal_entries"] == 1 and body["ledger_entries"] == 1, body
    entries = client.get("/api/journal", params={"start_date": "1900-01-01"}).json()[
        "entries"
    ]
    assert len(entries) == 1
    e = entries[0]
    assert e["reference"] == "AJE-7" and e["is_adjusting"] is True
    assert e["posted_by"] == "admin"
    assert e["description"] == "IIF Import — Year-end accrual"
    assert {(ln["account_number"], ln["debit"], ln["credit"]) for ln in e["lines"]} == {
        ("6300", 120.0, 0.0),
        ("2000", 0.0, 120.0),
    }
    again = upload(adjusting=False)
    assert again["journal_entries"] == 0 and again["duplicates_skipped"] == 1
    assert (
        len(
            client.get("/api/journal", params={"start_date": "1900-01-01"}).json()[
                "entries"
            ]
        )
        == 1
    )
    # an unbalanced block is refused with its figures
    bad = GJ_IIF.replace("-120.00", "-100.00")
    body = _ok(
        client.post(
            "/api/iif/import",
            files={"file": ("bad.iif", io.BytesIO(bad.encode("utf-8")), "text/plain")},
        )
    )
    assert body["journal_entries"] == 0
    assert any("does not sum to zero" in str(err) for err in body["errors"]), body[
        "errors"
    ]
    # the exported form of an adjusting entry reads back the same
    text = export_journal_entries(db_session)
    assert (
        "TRNS\tGENERAL JOURNAL\t03/31/2026\tInsurance\t\t120.00\tAJE-7\tYear-end accrual"
        in text
    )


def _ledger_csv(rows):
    head = ",,,,,Type,,Date,,Num,,Adj,,Name,,Memo,,Clr,,Split,,Debit,,Credit,,Original Amount,,Balance"
    return "\n".join([head] + rows) + "\n"


GJ_ONLY = _ledger_csv(
    [
        ",Insurance,,,,,,,,,,,,,,,,,,,,,,,,,,",
        ",,,,,General Journal,,03/31/2026,,9,,,,,,Year-end accrual,,,,Accounts Payable,,120.00,,,,120.00,,120.00",
        ",Total Insurance,,,,,,,,,,,,,,,,,,,,120.00,,0.00,,,,120.00",
        ",Accounts Payable,,,,,,,,,,,,,,,,,,,,,,,,,,",
        ",,,,,General Journal,,03/31/2026,,9,,,,,,Year-end accrual,,,,Insurance,,,,120.00,,-120.00,,-120.00",
        ",Total Accounts Payable,,,,,,,,,,,,,,,,,,,,0.00,,120.00,,,,-120.00",
        ",TOTAL,,,,,,,,,,,,,,,,,,,,120.00,,120.00,,,,0.00",
    ]
)


def test_the_adjusting_entries_import_takes_general_journal_rows_only(
    client, db_session, seed_accounts
):
    def upload(text, adjusting_only, name="report.csv"):
        return _ok(
            client.post(
                "/api/csv/import/qb-report",
                files={"file": (name, io.BytesIO(text.encode("utf-8")), "text/csv")},
                data={"adjusting_only": "true" if adjusting_only else "false"},
            )
        )

    # the whole-ledger fixture holds checks, deposits and transfers: refused whole
    whole = FIXTURE.read_text(encoding="cp1252")
    body = upload(whole, adjusting_only=True)
    assert body["journals"] == 0
    assert len(body["errors"]) == 1
    assert "General Journal rows only" in body["errors"][0]
    assert (
        "Check row" in body["errors"][0] and "Nothing was posted" in body["errors"][0]
    )
    assert (
        client.get("/api/journal", params={"start_date": "1900-01-01"}).json()[
            "entries"
        ]
        == []
    )

    body = upload(GJ_ONLY, adjusting_only=True)
    assert body["errors"] == [], body["errors"]
    assert body["journals"] == 1 and body["adjusting"] is True
    entries = client.get("/api/journal?adjusting=true&start_date=1900-01-01").json()[
        "entries"
    ]
    assert len(entries) == 1
    assert entries[0]["is_adjusting"] is True and entries[0]["posted_by"] == "admin"
    assert entries[0]["total_debit"] == 120.0
    again = upload(GJ_ONLY, adjusting_only=True)
    assert again["journals"] == 0 and again["duplicates_skipped"] == 1
    # the box on a report that is not the ledger
    other = "Type,Date,Num,Name,Memo,Amount\n"
    body = upload(other, adjusting_only=True)
    assert body["journals"] == 0 and body["errors"]


def test_the_page_offers_the_blocks_and_the_boxes():
    js = (ROOT / "app/static/js/iif.js").read_text(encoding="utf-8")
    for section in (
        "register-entries",
        "transfers",
        "bill-payments",
        "journal-entries",
    ):
        assert f"IIFPage.exportSection('{section}')" in js
    assert 'id="iif-adjusting"' in js and "formData.append('adjusting'," in js
    assert (
        'id="qbcsv-adjusting-only"' in js and "formData.append('adjusting_only'," in js
    )
    assert "['Journal entries', result.journal_entries]" in js
