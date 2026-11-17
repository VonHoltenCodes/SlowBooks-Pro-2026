"""A nonprofit's receivables cards are titled for what they hold (#969,
2.32.0 round 11, NEW-244, the owner's "label all of them"): pledges and
program-fee invoices both. A business is unchanged."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _cards(client):
    widgets = client.get("/api/dashboard/widgets").json()["widgets"]
    return {w["id"]: (w["title"], w["description"]) for w in widgets}


def test_a_nonprofits_receivables_cards_name_both_kinds(client, seed_accounts):
    r = client.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text
    cards = _cards(client)
    assert cards["receivables"] == (
        "Pledges & Invoices Receivable",
        "What donors owe (the Pledge Aging total), with how many pledges and invoices are overdue",
    )
    assert cards["overdue_invoices"] == (
        "Overdue Pledges & Invoices",
        "The oldest overdue pledges and invoices and who owes them",
    )
    assert cards["recent_invoices"] == (
        "Recent Pledges & Invoices",
        "The last five pledges and invoices",
    )


def test_a_business_keeps_its_titles(client, seed_accounts):
    cards = _cards(client)
    assert cards["receivables"] == (
        "Total Receivables",
        "What customers owe (the A/R Aging total), with how many invoices are overdue",
    )
    assert cards["overdue_invoices"] == (
        "Overdue Invoices",
        "The oldest overdue invoices and who owes them",
    )
    assert cards["recent_invoices"] == ("Recent Invoices", "The last five invoices")


def test_the_cash_position_row_and_the_aging_card_name_both_kinds(
    client, seed_accounts
):
    # #981: the vocabulary's "Receivables" reaches the Cash Position row
    # (T('Receivables') in dashboard.js) and the Pledge Aging description
    from app.services.terminology import Terms

    np = Terms("nonprofit")
    assert np("Receivables") == "Pledges & Invoices Receivable"
    r = client.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text
    cards = _cards(client)
    assert cards["ar_aging"] == (
        "Pledge Aging",
        "Pledges & Invoices Receivable by age bucket",
    )
    js = (ROOT / "app/static/js/terms.js").read_text(encoding="utf-8")
    assert '"Receivables": "Pledges & Invoices Receivable"' in js
