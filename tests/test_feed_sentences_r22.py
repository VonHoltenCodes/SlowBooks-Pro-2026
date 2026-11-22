# ruff: noqa: F811  (pytest fixtures imported from another module)
"""Round 22's bank-feed refusals, in sentences (W-190, W-191, W-192).

- Add selected answered "not added 1: Pick a category (account) to add
  this line", naming no line: each line it leaves out now comes back with
  its date, the bank's text and its amount beside its own reason (W-190).
- A To review memo over 500 characters got the validator's own text, "String
  should have at most 500 characters": a sentence now, as a rule's memo
  gets (W-191).
- Pay bills from a feed line over a bill's balance, or the same bill twice,
  answered "Allocation exceeds bill balance": now the sentence Receive
  payment gives an invoice, naming the bill and the amounts (W-192)."""

from app.models.bills import BillPayment
from tests.test_feed_receive_pay_1056 import _ok, _pay, books  # noqa: F401


def test_add_selected_names_each_line_it_did_not_add(client, books):
    lines = books["lines"]
    a, b = lines["MOBILE DEPOSIT"], lines["DEPOSIT 2"]
    excluded = lines["CHECK 1201"]
    _ok(client.post(f"/api/banking/transactions/{excluded['id']}/exclude"))
    out = _ok(
        client.post(
            f"/api/banking/accounts/{books['feed']['id']}/feed/add-all",
            json={"line_ids": [a["id"], b["id"], excluded["id"]]},
        )
    )
    assert out["added"] == 0
    named = {s["id"]: s for s in out["skipped"]}
    assert named[a["id"]] == {
        "id": a["id"],
        "reason": named[a["id"]]["reason"],
        "date": "2026-09-10",
        "payee": "MOBILE DEPOSIT",
        "amount": "450.00",
    }
    assert "category" in named[a["id"]]["reason"]
    assert (named[b["id"]]["date"], named[b["id"]]["payee"]) == (
        "2026-09-11",
        "DEPOSIT 2",
    )
    assert named[excluded["id"]]["reason"] == (
        "That line is excluded; restore it to add it"
    )
    assert named[excluded["id"]]["amount"] == "-150.00"


def test_a_memo_over_500_characters_is_refused_in_a_sentence(client, books):
    line = books["lines"]["ACH FLOUR MILL"]
    sentence = "A memo can be at most 500 characters; this one has 501."
    for method, path, body in [
        ("patch", f"/api/banking/transactions/{line['id']}", {"memo": "m" * 501}),
        (
            "post",
            f"/api/banking/accounts/{books['feed']['id']}/feed/add-all",
            {
                "line_ids": [line["id"]],
                "lines": [{"id": line["id"], "memo": "m" * 501}],
            },
        ),
        ("post", f"/api/banking/transactions/{line['id']}/add", {"memo": "m" * 501}),
        (
            "post",
            f"/api/banking/transactions/{line['id']}/pay-bills",
            {
                "vendor_id": books["vendor"]["id"],
                "allocations": [{"bill_id": books["bills"][0]["id"], "amount": "1"}],
                "memo": "m" * 501,
            },
        ),
    ]:
        r = getattr(client, method)(path, json=body)
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == sentence
    # 500 is kept, and spaces around it do not count
    kept = _ok(
        client.patch(
            f"/api/banking/transactions/{line['id']}", json={"memo": " " + "m" * 500}
        )
    )
    assert kept["pick_memo"] == "m" * 500


def test_pay_bills_over_a_bills_balance_names_the_bill_and_the_amounts(
    client, db_session, books  # noqa: F811
):
    line = books["lines"]["ACH FLOUR MILL"]
    second = books["bills"][1]  # FM-1, 80.00
    over = _pay(
        client,
        line,
        books["vendor"]["id"],
        [{"bill_id": second["id"], "amount": "150"}],
    )
    assert over.status_code == 400, over.text
    assert (
        over.json()["detail"]
        == "$150.00 is more than the $80.00 still due on bill FM-1."
    )
    twice = _pay(
        client,
        line,
        books["vendor"]["id"],
        [
            {"bill_id": second["id"], "amount": "60"},
            {"bill_id": second["id"], "amount": "60"},
        ],
    )
    assert twice.status_code == 400, twice.text
    assert (
        twice.json()["detail"]
        == "$60.00 is more than the $20.00 still due on bill FM-1."
    )
    db_session.expire_all()
    assert db_session.query(BillPayment).count() == 0
