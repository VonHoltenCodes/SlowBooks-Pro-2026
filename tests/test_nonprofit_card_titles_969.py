"""A nonprofit's receivables cards are titled for what they hold (#969,
2.32.0 round 11, NEW-244, the owner's "label all of them"): pledges and
program-fee invoices both. A business is unchanged."""


def _cards(client):
    widgets = client.get("/api/dashboard/widgets").json()["widgets"]
    return {w["id"]: (w["title"], w["description"]) for w in widgets}


def test_a_nonprofits_receivables_cards_name_both_kinds(client, seed_accounts):
    r = client.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text
    cards = _cards(client)
    assert cards["receivables"] == (
        "Pledges & Invoices Receivable",
        "What donors owe (the Pledge Aging total), with how many are overdue",
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
    assert cards["receivables"][0] == "Total Receivables"
    assert cards["overdue_invoices"] == (
        "Overdue Invoices",
        "The oldest overdue invoices and who owes them",
    )
    assert cards["recent_invoices"] == ("Recent Invoices", "The last five invoices")
