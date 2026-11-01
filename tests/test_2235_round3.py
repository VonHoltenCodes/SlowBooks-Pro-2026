"""2.23.5 round 3: the two lows round 2 left.

W-17 / NEW-146: after Enter on the register's entry row the cursor was not
in Payee. go() set location.hash and returned nothing, so postQuickEntry's
await ended before the redraw and the old row's Payee was focused a moment
before the new row replaced it. NEW-147: a negative running balance in a
column narrowed by the row's inputs broke after its minus sign and read as
positive."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_go_resolves_once_the_page_is_drawn_and_the_cursor_lands_in_payee():
    js = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")
    body = js[js.index("    go(hash) {") :][:120]
    assert "return App.navigate(hash);" in body
    assert "location.hash = hash" not in js
    post = js[js.index("async postQuickEntry(accountId) {") :][:4000]
    # on the register's own filter (#663)
    assert "await BankingPage.go(BankingPage._regUrl(accountId));" in post
    assert "BankingPage._focusPayee();" in post
    focus = js[js.index("_focusPayee(tries = 20) {") :][:800]
    assert "document.activeElement !== next" in focus
    assert "setTimeout(() => BankingPage._focusPayee(tries - 1), 50);" in focus
    # navigate is what draws the page: an async that awaits the render
    app = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    assert "async navigate(hash) {" in app
    assert "const html = await route.render(param, query);" in app


def test_an_amount_never_breaks_after_its_minus_sign():
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    rule = css[css.index("td.amount, th.amount {") :][:400]
    assert "white-space: nowrap;" in rule
