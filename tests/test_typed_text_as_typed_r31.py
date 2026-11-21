"""What a person typed is said back as typed (gate round 30, W-246).

A refusal that repeats what was typed passes through the vocabulary swap
every HTTP error crosses (app/main.py), and the swap reworded the typed
text with the prose: a nonprofit's Always CC of "not a customer" came back
"Always CC: not an email address: not a donor. Nothing was saved."

The rule is one mechanism, not a case each: a refusal that repeats typed
text is built with said() (terminology.Sentence), whose template is our
prose and whose filled-in values are data; Terms.worded, at the boundary,
rewords the prose and never the data. Business and construction read as
they did; only the nonprofit's prose changes words.
"""

import pytest

from app.services.terminology import AsWritten, Sentence, Terms, said

# -- the mechanism -------------------------------------------------------------


def test_said_keeps_the_values_and_rewords_the_template():
    npo = Terms("nonprofit")
    s = said("Customer '{}' was not found on invoice {}.", "Customer Care", "Invoice 7")
    assert s == "Customer 'Customer Care' was not found on invoice Invoice 7."
    assert npo.worded(s) == "Donor 'Customer Care' was not found on pledge Invoice 7."
    # business has no words to swap; the text is the text
    assert Terms("business").worded(s) == s


def test_a_sentence_keeps_its_pieces_through_join_plus_and_str_of_an_exception():
    npo = Terms("nonprofit")
    cc = said("Always CC: not an email address: {}.", "not a customer")
    bcc = said("Always BCC: not an email address: {}.", "invoice desk")
    joined = Sentence(" ").join([cc, bcc]) + " Nothing was saved for this customer."
    assert isinstance(joined, Sentence)
    assert npo.worded(joined) == (
        "Always CC: not an email address: not a customer. "
        "Always BCC: not an email address: invoice desk. "
        "Nothing was saved for this donor."
    )
    # a refusal raised with a Sentence and read back with str() keeps it
    assert npo.worded(str(ValueError(cc))) == (
        "Always CC: not an email address: not a customer."
    )
    # prose added in front keeps the data after it
    assert npo.worded("Customer: " + said("{}", "customer")) == "Donor: customer"


def test_plain_text_and_as_written_read_as_they_always_did():
    npo = Terms("nonprofit")
    assert npo.worded("Customer not found") == "Donor not found"
    # an address in plain prose is still kept (text_keeping_addresses)
    assert npo.worded("No invoice for invoices@customer.example.test") == (
        "No pledge for invoices@customer.example.test"
    )
    assert npo.worded(AsWritten("Customer not found")) == "Customer not found"
    assert said("{:,.2f} and {!r}", 1234.5, "a") == "1,234.50 and 'a'"


# -- through the routes ----------------------------------------------------------


def _company_type(client, kind):
    r = client.put("/api/settings", json={"company_type": kind})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("kind", ["nonprofit", "construction", "business"])
@pytest.mark.parametrize(
    "key,label", [("smtp_always_cc", "Always CC"), ("smtp_always_bcc", "Always BCC")]
)
def test_always_cc_and_bcc_say_what_was_typed(client, kind, key, label):
    _company_type(client, kind)
    r = client.put("/api/settings", json={key: "not a customer"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == (
        f"{label}: not an email address: not a customer. Nothing was saved."
    )
    # an address with an accented letter is said back as typed too
    r = client.put("/api/settings", json={key: "josé@invoice.example.test"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == (
        f"{label}: not an email address: josé@invoice.example.test. Nothing was saved."
    )


def test_both_refused_at_once_keep_both_as_typed(client):
    _company_type(client, "nonprofit")
    r = client.put(
        "/api/settings",
        json={"smtp_always_cc": "not a customer", "smtp_always_bcc": "invoice desk"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == (
        "Always CC: not an email address: not a customer. "
        "Always BCC: not an email address: invoice desk. Nothing was saved."
    )


def test_a_deposit_account_typed_as_words_is_said_as_typed(client, seed_accounts):
    _company_type(client, "nonprofit")
    r = client.put("/api/settings", json={"default_deposit_account_id": "customer"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == (
        "Default deposit account: no account customer in the chart. Nothing was saved."
    )


def test_a_duplicate_class_says_the_name_as_typed(client):
    _company_type(client, "nonprofit")
    assert client.post("/api/classes", json={"name": "Customer Care"}).status_code in (
        200,
        201,
    )
    r = client.post("/api/classes", json={"name": "Customer Care"})
    assert r.status_code == 409, r.text
    # "Class" is our word and reads "Fund"; the class's name is the person's
    assert r.json()["detail"] == "Fund 'Customer Care' already exists"


def test_a_duplicate_item_says_the_name_as_typed(client, seed_accounts):
    _company_type(client, "nonprofit")
    body = {"name": "Invoice binder", "item_type": "service"}
    first = client.post("/api/items", json=body)
    assert first.status_code in (200, 201), first.text
    r = client.post("/api/items", json=body)
    assert r.status_code == 409, r.text
    assert r.json()["detail"].startswith(
        'There is already an item named "Invoice binder".'
    )


def test_a_duplicate_job_says_the_name_as_typed(client):
    _company_type(client, "nonprofit")
    who = client.post("/api/customers", json={"name": "Lakeside Library"})
    assert who.status_code in (200, 201), who.text
    body = {"customer_id": who.json()["id"], "name": "Customer outreach"}
    first = client.post("/api/jobs", json=body)
    assert first.status_code in (200, 201), first.text
    r = client.post("/api/jobs", json=body)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "This donor already has a grant named 'Customer outreach'"
    )


def test_a_duplicate_payment_term_says_the_name_as_typed(client):
    _company_type(client, "nonprofit")
    body = {"name": "Customer special", "kind": "standard", "net_days": 45}
    first = client.post("/api/payment-terms", json=body)
    assert first.status_code in (200, 201), first.text
    r = client.post("/api/payment-terms", json=body)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == '"Customer special" is already on the terms list.'


def test_an_unknown_filter_is_named_as_sent(client):
    _company_type(client, "nonprofit")
    r = client.get("/api/jobs/profitability", params={"invoice": "1"})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail.startswith("invoice is not a filter here. This request accepts: ")
    assert "customer_id" in detail


def test_an_unknown_query_parameter_is_named_as_sent(client):
    _company_type(client, "nonprofit")
    body = {"invoice_id": 1, "discount": "1.00", "discount_account_id": 1}
    r = client.post("/api/payments/1/discount?customer=1", json=body)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "'customer' is not a parameter this accepts."
