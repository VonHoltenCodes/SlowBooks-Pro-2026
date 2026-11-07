"""The register's date after a post, and the date keys, in a real browser
(#566, from discussion #561). After a post the quick row and the + Entry
dialog open with the date just posted — per register, for the page's life
— and + / - step any date box a day, T is today. The #481 far-date
question is asked for a date typed, not for one carried over. The entry
row stays under a date filter, and a post from it keeps the dates (#663).
The API is stubbed; the page is the real one (tests/test_browser_ui.py's
fixtures)."""

import datetime as dt
from urllib.parse import parse_qs, urlsplit

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_register_quick_add import CHART, REGISTER  # noqa: E402
from tests.test_browser_ui import _open  # noqa: E402
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)


def _register_page(browser, posted, at="#/banking/1", register=None, companies=None):
    """The Checking register (#/banking/1, or the address `at`) with the
    post stubbed; `posted` collects the bodies sent. The register's GET
    answers `register`, which a test may change as it goes; the company
    list answers `companies` (a list a test may change), else none."""

    def _transactions(route):
        posted.append(route.request.post_data_json)
        route.fulfill(status=201, json={"id": len(posted)})

    reg = REGISTER if register is None else register
    page = _open(browser, 1280, 800, "#/")
    if companies is not None:
        page.route("**/api/companies", lambda r: r.fulfill(json=companies))
    page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=reg))
    page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
    page.route("**/api/banking/transactions", _transactions)
    page.goto(page.url.split("#")[0] + at)
    page.wait_for_selector("#reg-start")
    return page


def _visit(page, at, ready):
    page.goto(page.url.split("#")[0] + at)
    page.wait_for_selector(ready)


def _post_quick(page, date, amount="25"):
    page.fill("#qe-date", date)
    page.select_option("#qe-category", "2")
    page.fill("#qe-out", amount)
    page.click("#reg-quick-entry button:has-text('Post')")
    page.wait_for_selector("#toast-container .toast:has-text('Entry posted')")
    # the register redraws after the post; wait for the fresh row
    page.wait_for_function("() => document.getElementById('qe-out').value === ''")


def test_the_date_sticks_after_a_post_in_the_row_and_the_dialog(browser):
    posted = []
    page = _register_page(browser, posted)
    try:
        today = dt.date.today().isoformat()
        assert page.input_value("#qe-date") == today

        _post_quick(page, "2026-10-10")
        assert posted[-1]["date"] == "2026-10-10"
        # the next row opens on the date just posted, not today
        assert page.input_value("#qe-date") == "2026-10-10"
        assert page.get_attribute("#qe-date", "data-carried") == "2026-10-10"

        # and so does + Entry
        page.click("text=+ Entry")
        page.wait_for_selector("#entry-payee")
        assert page.input_value("#modal-body [name='date']") == "2026-10-10"
        assert (
            page.get_attribute("#modal-body [name='date']", "data-carried")
            == "2026-10-10"
        )
        # a post from the dialog carries its own date back to the row
        page.fill("#modal-body [name='date']", "2026-10-12")
        page.fill("#modal-body [name='amount']", "-40")
        page.select_option("#entry-category", "2")
        page.click("#modal-body button[type='submit']")
        page.wait_for_function(
            "() => document.getElementById('qe-date')"
            " && document.getElementById('qe-date').value === '2026-10-12'"
        )
        assert posted[-1]["date"] == "2026-10-12"

        # another register starts on today: the date is one register's
        page.goto(page.url.split("#")[0] + "#/banking/4")
        page.wait_for_function(
            "() => location.hash === '#/banking/4' && document.getElementById('qe-date')"
            " && document.getElementById('qe-date').value !== '2026-10-12'"
        )
        assert page.input_value("#qe-date") == today
        assert page.get_attribute("#qe-date", "data-carried") == ""
        # and back: Checking still remembers its own
        page.goto(page.url.split("#")[0] + "#/banking/1")
        page.wait_for_function(
            "() => location.hash === '#/banking/1' && document.getElementById('qe-date')"
            " && document.getElementById('qe-date').value === '2026-10-12'"
        )
    finally:
        page.close()


def test_the_far_date_question_is_asked_once_not_for_the_carried_date(browser):
    posted = []
    page = _register_page(browser, posted)
    asked = []
    page.on("dialog", lambda d: (asked.append(d.message), d.accept()))
    try:
        page.evaluate(
            "() => { App.settings = Object.assign(App.settings || {},"
            " { warn_date_past: 'true', warn_date_past_days: '90' }); }"
        )
        # typed: asked, and accepted
        _post_quick(page, "2020-01-15")
        assert len(asked) == 1 and "in the past" in asked[0]
        assert posted[-1]["date"] == "2020-01-15"
        # carried from that post: not asked again
        _post_quick(page, "2020-01-15")
        assert len(asked) == 1
        assert posted[-1]["date"] == "2020-01-15"
        # moved a day with the key: a changed date, asked again
        page.focus("#qe-date")
        page.keyboard.press("+")
        assert page.input_value("#qe-date") == "2020-01-16"
        page.select_option("#qe-category", "2")
        page.fill("#qe-out", "5")
        page.click("#reg-quick-entry button:has-text('Post')")
        page.wait_for_function("() => document.getElementById('qe-out').value === ''")
        assert len(asked) == 2
        assert posted[-1]["date"] == "2020-01-16"
    finally:
        page.close()


def test_plus_minus_and_t_move_the_row_date(browser):
    page = _register_page(browser, [])
    try:
        today = dt.date.today().isoformat()
        page.fill("#qe-date", "2026-10-31")
        page.focus("#qe-date")
        page.keyboard.press("+")
        assert page.input_value("#qe-date") == "2026-11-01"
        page.keyboard.press("-")
        page.keyboard.press("-")
        assert page.input_value("#qe-date") == "2026-10-30"
        page.keyboard.press("h")
        assert page.input_value("#qe-date") == "2026-10-31"
        page.keyboard.press("m")
        assert page.input_value("#qe-date") == "2026-10-01"
        page.keyboard.press("t")
        assert page.input_value("#qe-date") == today
        # a text box keeps its + and -
        page.fill("#qe-memo", "")
        page.focus("#qe-memo")
        page.keyboard.type("a+b-c")
        assert page.input_value("#qe-memo") == "a+b-c"
        # the register's From filter is a date box too: the key moves it
        # and its onchange redraws the register
        page.fill("#reg-start", "2026-09-01")  # its change redraws the register
        page.wait_for_function(
            "() => location.hash.includes('start_date=2026-09-01')"
            " && document.getElementById('reg-start').value === '2026-09-01'"
        )
        page.focus("#reg-start")
        page.keyboard.press("-")
        page.wait_for_function(
            "() => location.hash.includes('start_date=2026-08-31')"
            " && document.getElementById('reg-start').value === '2026-08-31'"
        )
    finally:
        page.close()


def test_minus_still_types_a_minus_in_the_amount_boxes(browser):
    # the key handler takes only date boxes: a money box keeps its - (and +)
    page = _register_page(browser, [])
    try:
        page.focus("#qe-out")
        page.keyboard.type("-12.5")
        assert page.input_value("#qe-out") == "-12.5"
        page.click("text=+ Entry")
        page.wait_for_selector("#entry-payee")
        page.focus("#modal-body [name='amount']")
        page.keyboard.type("-40")
        assert page.input_value("#modal-body [name='amount']") == "-40"
        # and the dialog's date box did not move
        assert (
            page.input_value("#modal-body [name='date']") == dt.date.today().isoformat()
        )
    finally:
        page.close()


# ── #663: the entry row under a filter, and a post that keeps the dates ──

OCT_ENTRY = {
    "line_id": 11,
    "transaction_id": 11,
    "date": "2026-10-05",
    "description": "Fuel",
    "payee": "Shell",
    "reference": "",
    "source_type": "bank_entry",
    "source_link": None,
    "amount": -25.0,
    "debit": 0,
    "credit": 25.0,
    "payment": 25.0,
    "deposit": 0,
    "balance": -25.0,
    "cleared": False,
    "reconciliation_id": None,
    "editable": True,
    "printable": False,
    "voided": False,
    "voidable": True,
}

ROW_UNDER_ENTRIES = """() => {
    const row = document.getElementById('reg-quick-entry');
    const table = row && row.closest('table');
    return !!table && table.tBodies[0].textContent.includes('Shell');
}"""


def test_the_entry_row_stays_under_a_date_filter_not_a_class_one(browser):
    """Any From, To or class hid the entry row, so a register always worked
    from the start of the month never showed it (#663). A date filter keeps
    it, with entries in the range or none yet; a class filter puts a line
    in its place, since the running balance counts that class only."""
    register = {**REGISTER, "entries": []}
    page = _register_page(browser, [], "#/banking/1?start_date=2026-10-01", register)
    try:
        # nothing in the range yet: said, and the row is there for the first
        assert "Nothing posted to this account for these filters" in page.inner_text(
            "#page-content"
        )
        assert page.locator("#reg-quick-entry").count() == 1

        # entries in the range: the row follows them
        register["entries"] = [OCT_ENTRY]
        _visit(
            page,
            "#/banking/1?start_date=2026-10-01&end_date=2026-10-31",
            "#reg-opening",
        )
        assert page.evaluate(ROW_UNDER_ENTRIES)

        # a class: the line says why there is no row, and All classes brings it back
        page.route("**/api/classes*", lambda r: r.fulfill(json=CLASSES))
        register["class_name"] = "Site Prep"
        _visit(
            page, "#/banking/1?start_date=2026-10-01&class_id=2", "#reg-quick-entry-off"
        )
        assert page.locator("#reg-quick-entry").count() == 0
        # the company has a class of its own, so the row has a class box
        # (#671): the balance is the reason
        assert page.inner_text("#reg-quick-entry-off") == (
            "The entry row is hidden while a class is chosen: the running balance"
            " counts this class only. Choose All classes to use it, or post with"
            " + Entry."
        )
        page.select_option("#reg-class", "")
        page.wait_for_selector("#reg-quick-entry")
        assert (
            page.evaluate("() => location.hash") == "#/banking/1?start_date=2026-10-01"
        )

        # and an empty register under a class says it in the same place
        register["entries"] = []
        _visit(page, "#/banking/1?class_id=2", "#reg-quick-entry-off")
        assert page.locator("#reg-quick-entry").count() == 0

        # the default class counts the lines with no class, the row's among
        # them: the balance is the reason there
        register["class_name"] = "Unassigned"
        _visit(page, "#/banking/1?class_id=1", "#reg-quick-entry-off")
        assert page.inner_text("#reg-quick-entry-off") == (
            "The entry row is hidden while a class is chosen: the running balance"
            " counts this class only. Choose All classes to use it, or post with"
            " + Entry."
        )
    finally:
        page.close()


def _toast_with(page, words):
    page.locator("#toast-container .toast", has_text=words).first.wait_for()


def test_a_post_from_the_row_keeps_the_dates_and_says_when_it_falls_outside(browser):
    """The row's post went back to the register's bare address, which
    dropped the dates (#663). It keeps them; an entry dated before From or
    after To, which the list leaves out, is said so in the toast."""
    posted = []
    at = "#/banking/1?start_date=2026-10-01&end_date=2026-10-31"
    page = _register_page(browser, posted, at)
    try:
        _post_quick(page, "2026-10-12")
        assert posted[-1]["date"] == "2026-10-12"
        assert page.evaluate("() => location.hash") == at
        assert page.input_value("#reg-start") == "2026-10-01"
        assert page.input_value("#reg-end") == "2026-10-31"
        assert page.locator("#toast-container .toast", has_text="show it").count() == 0

        _post_quick(page, "2026-09-28")
        _toast_with(
            page,
            "Entry posted. Its date, Sep 28, 2026, is before the From date,"
            " so the register does not show it.",
        )
        _post_quick(page, "2026-11-02")
        _toast_with(
            page,
            "Entry posted. Its date, Nov 2, 2026, is after the To date,"
            " so the register does not show it.",
        )
        assert posted[-1]["date"] == "2026-11-02"
        assert page.evaluate("() => location.hash") == at
    finally:
        page.close()


# ── #663: a save, a void or a feed action keeps the dates and the place ──


class _Ledger:
    """A long register for the stubs to answer from: an entry a day from
    Sep 1, filtered by the address's dates and class as the service filters
    them, with the posts, edits, voids and the feed's excludes applied."""

    def __init__(self, days=60):
        start = dt.date(2026, 9, 1)
        self.entries = [
            self._entry(100 + i, (start + dt.timedelta(days=i)).isoformat())
            for i in range(days)
        ]
        self.review = []
        self.candidates = []

    @staticmethod
    def _entry(txn, date, payee=None, class_id=None):
        payee = payee or f"Payee {txn}"
        return {
            **OCT_ENTRY,
            "line_id": txn,
            "transaction_id": txn,
            "date": date,
            "payee": payee,
            "description": payee,
            "class_id": class_id,
        }

    def _by_id(self, txn):
        return next(e for e in self.entries if e["transaction_id"] == txn)

    def register(self, route):
        q = parse_qs(urlsplit(route.request.url).query)
        start, end, cls = (
            q.get(k, [""])[0] for k in ("start_date", "end_date", "class_id")
        )
        rows = sorted(
            (
                e
                for e in self.entries
                if (not start or e["date"] >= start)
                and (not end or e["date"] <= end)
                and (not cls or str(e["class_id"]) == cls)
            ),
            key=lambda e: (e["date"], e["transaction_id"]),
        )
        route.fulfill(
            json={
                **REGISTER,
                "entries": rows,
                "class_name": "Site Prep" if cls else None,
            }
        )

    def transactions(self, route):
        req = route.request
        path = urlsplit(req.url).path
        if req.method == "POST" and path.endswith("/api/banking/transactions"):
            body = req.post_data_json
            txn = 900 + len(self.entries)
            self.entries.append(
                self._entry(txn, body["date"], body.get("payee"), body.get("class_id"))
            )
            return route.fulfill(status=201, json={"id": txn, "date": body["date"]})
        if req.method == "POST" and path.endswith("/exclude"):
            line = int(path.split("/")[-2])
            self.review = [t for t in self.review if t["id"] != line]
            return route.fulfill(json={"status": "excluded"})
        if req.method == "POST" and path.split("/")[-1] in ("add", "payout"):
            return route.fulfill(json=self._post_line(int(path.split("/")[-2])))
        if path.endswith("/candidates"):
            return route.fulfill(json=self.candidates)
        if req.method == "POST" and path.endswith("/match"):
            line = int(path.split("/")[-2])
            t = next(t for t in self.review if t["id"] == line)
            self.review = [x for x in self.review if x["id"] != line]
            return route.fulfill(json={**t, "match_status": "manual"})
        # the review queue, a page at a time (fetchAllPages)
        skip = int(parse_qs(urlsplit(req.url).query).get("skip", ["0"])[0])
        route.fulfill(json=[] if skip else self.review)

    def _post_line(self, line):
        """A statement line into the books on its own date (Add, Payout)."""
        t = next(t for t in self.review if t["id"] == line)
        self.review = [x for x in self.review if x["id"] != line]
        self.entries.append(self._entry(900 + len(self.entries), t["date"], t["payee"]))
        return {**t, "match_status": "added", "vendor_name": None}

    def add_all(self, route):
        posted = [t["id"] for t in self.review if t.get("category_account_id")]
        for line in posted:
            self._post_line(line)
        route.fulfill(json={"added": len(posted), "skipped": [], "without_vendor": []})

    def entries_route(self, route):
        req = route.request
        parts = urlsplit(req.url).path.rstrip("/").split("/")
        if parts[-1] == "void":
            e = self._by_id(int(parts[-2]))
            e.update(voided=True, voidable=False, editable=False)
            return route.fulfill(json={"id": e["transaction_id"], "status": "void"})
        e = self._by_id(int(parts[-1]))
        if req.method == "PUT":
            body = req.post_data_json
            e.update(date=body["date"], payee=body.get("payee") or e["payee"])
            return route.fulfill(json={"id": e["transaction_id"], "date": e["date"]})
        route.fulfill(
            json={
                "id": e["transaction_id"],
                "date": e["date"],
                "amount": -25.0,
                "payee": e["payee"],
                "reference": "",
                "description": "",
                "category_account_id": 2,
                "class_id": None,
                "splits": None,
                "gift_letters": [],
            }
        )


def _ledger_page(browser, ledger, at, classes=None, feed=False):
    page = _open(browser, 1280, 800, "#/")
    page.route("**/api/banking/check-register*", ledger.register)
    page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
    page.route("**/api/banking/transactions**", ledger.transactions)
    page.route("**/api/banking/entries/**", ledger.entries_route)
    if classes is not None:
        page.route("**/api/classes*", lambda r: r.fulfill(json=classes))
    if feed:
        page.route("**/api/banking/accounts/5/feed/add-all", ledger.add_all)
        page.route(
            "**/api/banking/overview",
            lambda r: r.fulfill(
                json=[{"account_id": 1, "feed": {"bank_account_id": 5, "name": "Feed"}}]
            ),
        )
    page.on("dialog", lambda d: d.accept())
    page.goto(page.url.split("#")[0] + at)
    page.wait_for_selector("#page-content tr[data-txn]")
    return page


# within a pixel: a row scrolled to the nearest edge can sit a fraction past it
IN_VIEW = """(txn) => {
    const row = document.querySelector(`#page-content tr[data-txn="${txn}"]`);
    if (!row) return false;
    const box = document.getElementById('content').getBoundingClientRect();
    const r = row.getBoundingClientRect();
    return r.top >= box.top - 1 && r.bottom <= box.bottom + 1;
}"""
SCROLLED = "() => document.getElementById('content').scrollTop"


def _scroll_to(page, txn):
    page.evaluate(
        '(txn) => document.querySelector(`#page-content tr[data-txn="${txn}"]`)'
        ".scrollIntoView({ block: 'center' })",
        txn,
    )
    return page.evaluate(SCROLLED)


def test_a_new_entry_keeps_the_dates_and_comes_into_view(browser):
    """+ Entry went back to the register's bare address: the dates went,
    and the page stayed at the top, the entry somewhere below (#663). The
    register comes back on its dates with the entry's row in view; one
    dated past To is said so, and the page stays where it was."""
    ledger = _Ledger()
    at = "#/banking/1?start_date=2026-09-01"
    page = _ledger_page(browser, ledger, at)
    try:
        # the register opens at its foot (#728); scrolled back to the top
        page.evaluate("() => { document.getElementById('content').scrollTop = 0; }")
        assert page.evaluate(SCROLLED) == 0
        page.click("text=+ Entry")
        page.wait_for_selector("#entry-payee")
        page.fill("#modal-body [name='date']", "2026-10-20")
        page.fill("#modal-body [name='amount']", "-40")
        page.fill("#entry-payee", "Harbor Fuel")
        page.select_option("#entry-category", "2")
        page.click("#modal-body button[type='submit']")
        _toast_with(page, "Entry posted")
        new = ledger.entries[-1]["transaction_id"]
        page.wait_for_function(IN_VIEW, arg=new)
        assert page.evaluate(SCROLLED) > 0
        assert page.evaluate("() => location.hash") == at
        assert page.input_value("#reg-start") == "2026-09-01"

        # dated past the To date: said, and the page stays where it was. +
        # Entry pressed from the keyboard, the page scrolled down since:
        # closing the dialog gives the focus back to + Entry at the top,
        # which scrolls the page there, and the register puts it back
        at = "#/banking/1?start_date=2026-09-01&end_date=2026-09-30"
        _visit(page, at, "#reg-end[value='2026-09-30']")
        page.focus("#page-content .page-header button:has-text('+ Entry')")
        before = _scroll_to(page, 120)
        assert before > 200, "the page is not scrolled far enough to lose"
        page.keyboard.press("Enter")
        page.wait_for_selector("#entry-payee")
        page.evaluate(
            "() => { document.getElementById('reg-start').dataset.drawn = 'before'; }"
        )
        page.fill("#modal-body [name='date']", "2026-10-15")
        page.fill("#modal-body [name='amount']", "-12")
        page.select_option("#entry-category", "2")
        page.click("#modal-body button[type='submit']")
        _toast_with(
            page,
            "Entry posted. Its date, Oct 15, 2026, is after the To date,"
            " so the register does not show it.",
        )
        # the register drawn again, on the same dates and at the same place
        page.wait_for_function(
            "() => !document.getElementById('reg-start').dataset.drawn"
        )
        assert page.evaluate("() => location.hash") == at
        assert abs(page.evaluate(SCROLLED) - before) <= 1
    finally:
        page.close()


def test_an_edit_keeps_the_dates_and_brings_the_entry_into_view(browser):
    """An edit's Save went back to the bare address too (#663). The
    register keeps its dates, and the entry, moved to an earlier day, is
    brought into view where it now sits."""
    ledger = _Ledger()
    at = "#/banking/1?start_date=2026-09-01&end_date=2026-10-31"
    page = _ledger_page(browser, ledger, at)
    try:
        assert _scroll_to(page, 150) > 0
        page.locator('tr[data-txn="150"] button', has_text="Edit").click()
        page.wait_for_function(
            "() => document.getElementById('modal-title').textContent === 'Edit entry'"
        )
        page.fill("#modal-body [name='date']", "2026-09-03")
        page.click("#modal-body button[type='submit']")
        _toast_with(page, "Entry saved")
        page.wait_for_function(
            "() => (document.querySelector('tr[data-txn=\"150\"]') || {}).textContent"
            " && document.querySelector('tr[data-txn=\"150\"]').textContent.includes('Sep 3, 2026')"
        )
        page.wait_for_function(IN_VIEW, arg=150)
        assert page.evaluate("() => location.hash") == at
    finally:
        page.close()


# A redraw that comes back at the top of the page, as closing a dialog does
# when it gives the focus back to its button up there: Chromium itself
# keeps the scroll of a page drawn again the same, so the register's own
# restore is made to do the work, and the checks fail without it.
REDRAW_AT_THE_TOP = """() => {
    const navigate = App.navigate.bind(App);
    App.navigate = async (hash) => {
        const done = await navigate(hash);
        document.getElementById('content').scrollTop = 0;
        return done;
    };
}"""


def test_a_void_keeps_the_dates_and_the_place_on_the_page(browser):
    """A void went back to the bare address, which dropped the dates (the
    From kept puts only the From back), and the page drawn again (#663).
    It keeps From and To, and the place on the page."""
    ledger = _Ledger()
    at = "#/banking/1?start_date=2026-09-01&end_date=2026-10-31"
    page = _ledger_page(browser, ledger, at)
    try:
        top = _scroll_to(page, 140)
        assert top > 200
        page.evaluate(REDRAW_AT_THE_TOP)
        page.locator('tr[data-txn="140"] button', has_text="Void").click()
        _toast_with(page, "Voided")
        page.wait_for_function(
            "() => !document.querySelector('tr[data-txn=\"140\"] button[data-destructive]')"
        )
        assert page.evaluate("() => location.hash") == at
        assert abs(page.evaluate(SCROLLED) - top) <= 1
    finally:
        page.close()


def test_a_feed_action_keeps_the_dates_and_the_place_on_the_page(browser):
    """The feed's Add, Payout, Exclude, Match and the rest redrew the
    register at its bare address (#663): they keep its dates, and the page
    stays where it was."""
    ledger = _Ledger()
    ledger.review = [
        {
            "id": 70 + i,
            "date": "2026-10-01",
            "payee": f"Statement line {i}",
            "description": "",
            "amount": -5.0,
        }
        for i in range(20)
    ]
    at = "#/banking/1?start_date=2026-09-15&end_date=2026-10-31"
    page = _ledger_page(browser, ledger, at, feed=True)
    try:
        page.wait_for_selector("text=Statement line 12")
        row = page.locator("tr", has_text="Statement line 12")
        top = row.evaluate(
            "(tr) => { tr.scrollIntoView({ block: 'center' });"
            " return document.getElementById('content').scrollTop; }"
        )
        assert top > 200
        page.evaluate(REDRAW_AT_THE_TOP)
        row.locator("button", has_text="Exclude").click()
        page.wait_for_function(
            "() => !document.body.textContent.includes('Statement line 12')"
        )
        assert page.evaluate("() => location.hash") == at
        assert abs(page.evaluate(SCROLLED) - top) <= 1
    finally:
        page.close()


CLASSES = [
    {"id": 1, "name": "Unassigned", "is_system_default": True},
    {"id": 2, "name": "Site Prep"},
    {"id": 3, "name": "Old Job", "is_archived": True},
]


def test_an_entry_the_class_leaves_out_is_said_so(browser):
    """Kept on the register's class, an entry posted with another class
    leaves the list: the toast says so (#663)."""
    ledger = _Ledger(days=3)
    ledger.entries[0]["class_id"] = 2
    at = "#/banking/1?class_id=2"
    page = _ledger_page(browser, ledger, at, classes=CLASSES)
    try:
        page.click("text=+ Entry")
        page.wait_for_selector("#entry-payee")
        page.fill("#modal-body [name='date']", "2026-09-02")
        page.fill("#modal-body [name='amount']", "-9")
        page.select_option("#entry-category", "2")
        page.select_option("#modal-body [name='class_id']", "1")
        page.click("#modal-body button[type='submit']")
        _toast_with(
            page,
            "This entry is not in class Site Prep, so the register does not show it.",
        )
        assert page.evaluate("() => location.hash") == at
    finally:
        page.close()


def test_a_new_entry_takes_the_class_the_register_is_shown_for(browser):
    """+ Entry under a class filter opened on the company's default class,
    so an entry posted as the class line suggests left the list (#663). It
    opens on the class chosen, and the entry stays in view; an archived
    class, which the form does not offer, is not given."""
    ledger = _Ledger(days=3)
    ledger.entries[0]["class_id"] = 2
    at = "#/banking/1?class_id=2"
    page = _ledger_page(browser, ledger, at, classes=CLASSES)
    try:
        page.click("text=+ Entry")
        page.wait_for_selector("#entry-payee")
        assert page.input_value("#modal-body [name='class_id']") == "2"
        page.fill("#modal-body [name='date']", "2026-09-02")
        page.fill("#modal-body [name='amount']", "-9")
        page.select_option("#entry-category", "2")
        page.click("#modal-body button[type='submit']")
        _toast_with(page, "Entry posted")
        new = ledger.entries[-1]["transaction_id"]
        assert ledger.entries[-1]["class_id"] == 2
        page.wait_for_function(IN_VIEW, arg=new)
        assert (
            page.locator("#toast-container .toast", has_text="not in class").count()
            == 0
        )

        _visit(page, "#/banking/1?class_id=3", "#reg-quick-entry-off")
        page.click("text=+ Entry")
        page.wait_for_selector("#entry-payee")
        assert page.input_value("#modal-body [name='class_id']") == "1"
    finally:
        page.close()


# ── #663: the register opens on the last From date it used ──────────────

ON_REGISTER = """([n, start]) => typeof BankingPage !== 'undefined' && BankingPage._ctx
    && BankingPage._ctx.accountId === n && document.getElementById('reg-start')
    && document.getElementById('reg-start').value === start"""


def _open_register(page, n, start):
    page.goto(page.url.split("#")[0] + f"#/banking/{n}")
    page.wait_for_function(ON_REGISTER, arg=[n, start])


def test_the_register_opens_on_the_from_date_it_last_used(browser):
    """The filter lived only in the address, so every visit opened on the
    whole history (#663). A register opened with no dates in its address
    opens on the From date it was last read from, in its From box and its
    address, after a reload too; each account and company keeps its own,
    and Clear forgets it."""
    page = _register_page(browser, [])
    try:
        assert page.input_value("#reg-start") == ""
        page.fill("#reg-start", "2026-10-01")  # its change redraws the register
        page.wait_for_function(
            "() => location.hash === '#/banking/1?start_date=2026-10-01'"
        )

        # another register has none; this one, opened by its bare address
        # (a card on Banking, the chart, a link), opens on the date
        _open_register(page, 4, "")
        assert page.evaluate("() => location.hash") == "#/banking/4"
        _open_register(page, 1, "2026-10-01")
        assert (
            page.evaluate("() => location.hash") == "#/banking/1?start_date=2026-10-01"
        )
        assert "from Oct 1, 2026" in page.inner_text("#reg-filter-note")

        # a new page load keeps it
        page.goto(page.url.split("#")[0] + "#/")
        page.reload()
        page.wait_for_function("window.App && document.readyState === 'complete'")
        page.evaluate(
            "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
        )
        _open_register(page, 1, "2026-10-01")

        # another company's register 1 is not this one
        page.evaluate("() => { App._tabsOwner = '|Another Company'; }")
        _open_register(page, 4, "")
        _open_register(page, 1, "")
        page.evaluate("() => { App._tabsOwner = '|Harbor Light Bakery Two'; }")
        _open_register(page, 4, "")
        _open_register(page, 1, "2026-10-01")

        # Clear forgets it
        page.get_by_role("button", name="Clear", exact=True).click()
        page.wait_for_function("() => location.hash === '#/banking/1'")
        _open_register(page, 4, "")
        _open_register(page, 1, "")
        assert page.evaluate("() => location.hash") == "#/banking/1"
    finally:
        page.close()


# ── #663: From and To typed a key at a time ──────────────────────────────

# the register's filter bar, the From box's toolbar
BAR = "document.getElementById('reg-start').closest('.toolbar')"
MARK_BAR = f"() => {{ {BAR}.dataset.probe = 'typing'; }}"
# the bar as it was before typing: the register was not drawn again
SAME_BAR = (
    f"() => !!document.getElementById('reg-start') && {BAR}.dataset.probe === 'typing'"
)
OUT_OF_BAR = f"""() => !!document.getElementById('reg-start')
    && document.activeElement !== document.body && !{BAR}.contains(document.activeElement)"""
KEPT = "(n) => localStorage.getItem(BankingPage._fromKey(n))"


def _type(page, keys):
    for key in keys:
        page.keyboard.press(key)


def _tab_out_of_the_bar(page):
    """Tab on, a stop at a time, until the keyboard has left the filter bar
    (a date box takes several: its month, day, year and calendar)."""
    for _ in range(20):
        page.keyboard.press("Tab")
        if page.evaluate(OUT_OF_BAR):
            return
    raise AssertionError("Tab never left the register's filter bar")


def test_a_from_typed_a_key_at_a_time_applies_once_the_bar_is_left(browser):
    """Chromium fires change on the first key that forms a whole date, and
    the register was drawn again on it: 11 typed into the month gave
    January and the rest went nowhere, and that From was kept (#663). The
    date is typed whole and applies, and is kept, once the bar is left; the
    keyboard goes on past the bar."""
    page = _register_page(browser, [], "#/banking/1?start_date=2026-09-01")
    try:
        page.evaluate(MARK_BAR)
        page.focus("#reg-start")  # the month
        _type(page, "11152025")
        assert page.input_value("#reg-start") == "2025-11-15"
        assert page.evaluate(SAME_BAR), "drawn again while the date was typed"
        assert (
            page.evaluate("() => location.hash") == "#/banking/1?start_date=2026-09-01"
        )
        assert page.evaluate(KEPT, 1) == "2026-09-01"

        _tab_out_of_the_bar(page)
        page.wait_for_function(
            "() => location.hash === '#/banking/1?start_date=2025-11-15'"
            " && document.getElementById('reg-start').value === '2025-11-15'"
        )
        # the From is kept, the note and the focus follow the redraw: waited
        # for, not read a frame early (a 4-core box failed here under load)
        page.wait_for_function(
            "() => localStorage.getItem(BankingPage._fromKey(1)) === '2025-11-15'"
        )
        assert page.evaluate(KEPT, 1) == "2025-11-15"
        page.wait_for_function(
            "() => (document.getElementById('reg-filter-note')?.innerText || '')"
            ".includes('from Nov 15, 2025')"
        )
        # the register drawn again, the keyboard is past the bar, not lost
        page.wait_for_function(OUT_OF_BAR)
        page.wait_for_function(
            "() => !!document.getElementById('reg-quick-entry')"
            " && document.getElementById('reg-quick-entry').contains(document.activeElement)"
        )
    finally:
        page.close()


def test_an_empty_from_typed_a_key_at_a_time_is_not_year_0002(browser):
    """An empty From had change as soon as the year's first digit made a
    date: the register opened on Oct 1, 0002 and kept it (#663). Typed
    whole, Enter applies it, and the keyboard stays in the box."""
    page = _register_page(browser, [])
    try:
        page.evaluate(MARK_BAR)
        page.focus("#reg-start")
        _type(page, "10012026")
        assert page.input_value("#reg-start") == "2026-10-01"
        assert page.evaluate(SAME_BAR), "drawn again while the date was typed"
        assert page.evaluate("() => location.hash") == "#/banking/1"
        page.keyboard.press("Enter")
        page.wait_for_function(
            "() => location.hash === '#/banking/1?start_date=2026-10-01'"
            f" && !{BAR}.dataset.probe"
        )
        assert page.input_value("#reg-start") == "2026-10-01"
        assert page.evaluate(KEPT, 1) == "2026-10-01"
        page.wait_for_function("() => document.activeElement.id === 'reg-start'")
    finally:
        page.close()


def test_from_and_to_typed_one_after_the_other_apply_together(browser):
    """From to To is staying in the bar: no drawing between them, so the
    To being typed is not lost under one, and Enter applies both."""
    page = _register_page(browser, [])
    try:
        page.evaluate(MARK_BAR)
        page.focus("#reg-start")
        _type(page, "09012026")
        for _ in range(8):  # through From's parts to To's month
            page.keyboard.press("Tab")
            if page.evaluate("() => document.activeElement.id") == "reg-end":
                break
        assert page.evaluate("() => document.activeElement.id") == "reg-end"
        _type(page, "09302026")
        assert page.evaluate(SAME_BAR), "drawn again between From and To"
        assert page.evaluate("() => location.hash") == "#/banking/1"
        page.keyboard.press("Enter")
        page.wait_for_function(
            "() => location.hash === '#/banking/1?start_date=2026-09-01&end_date=2026-09-30'"
        )
        assert page.input_value("#reg-start") == "2026-09-01"
        assert page.input_value("#reg-end") == "2026-09-30"
        page.wait_for_function("() => document.activeElement.id === 'reg-end'")
    finally:
        page.close()


PICK = """([sel, date]) => {
    // what a pick from the calendar does: the box's value, then its
    // input and change events
    const box = document.querySelector(sel);
    box.value = date;
    box.dispatchEvent(new Event('input', { bubbles: true }));
    box.dispatchEvent(new Event('change', { bubbles: true }));
}"""


def test_a_date_picked_from_the_calendar_applies_at_once(browser):
    """A pick is no typing: it applies as it is made, as the other date
    filters' changes do, even after keys were typed in the box (the press
    on the box that opens the calendar ends the typing)."""
    page = _register_page(browser, [], "#/banking/1?start_date=2026-09-01")
    try:
        page.evaluate(MARK_BAR)
        page.focus("#reg-start")
        _type(page, "08")  # the month typed, not applied
        assert page.input_value("#reg-start") == "2026-08-01"
        assert page.evaluate(SAME_BAR), "drawn again while the date was typed"
        page.dispatch_event("#reg-start", "pointerdown")
        page.evaluate(PICK, ["#reg-start", "2026-08-03"])
        page.wait_for_function(
            "() => location.hash === '#/banking/1?start_date=2026-08-03'"
        )
        assert page.input_value("#reg-start") == "2026-08-03"
        # and To, with nothing typed in it
        page.evaluate(PICK, ["#reg-end", "2026-08-31"])
        page.wait_for_function(
            "() => location.hash === '#/banking/1?start_date=2026-08-03&end_date=2026-08-31'"
        )
    finally:
        page.close()


def test_a_click_that_leaves_the_register_is_not_raced_by_the_dates(browser):
    """A From typed and the page left with a click (Back): the register is
    not drawn again over the page the click opened, and the From is not
    kept; a click on the page itself applies it."""
    page = _register_page(browser, [], "#/banking/1?start_date=2026-09-01")
    try:
        page.focus("#reg-start")
        _type(page, "10012026")
        page.locator("#page-content .page-header button", has_text="Back").click()
        page.wait_for_function("() => location.hash === '#/banking'")
        page.wait_for_timeout(300)  # a register drawn late would land by now
        assert page.locator("#reg-start").count() == 0
        assert page.evaluate("() => location.hash") == "#/banking"
        assert page.evaluate(KEPT, 1) == "2026-09-01"

        # typed, then a click on the page (not a control): it applies
        _visit(page, "#/banking/1", "#reg-start[value='2026-09-01']")
        page.focus("#reg-start")
        _type(page, "10012026")
        page.click("#page-content h2")
        page.wait_for_function(
            "() => location.hash === '#/banking/1?start_date=2026-10-01'"
        )
        assert page.evaluate(KEPT, 1) == "2026-10-01"
    finally:
        page.close()


def test_a_date_with_a_year_no_books_hold_is_neither_applied_nor_kept(browser):
    """A year typed a digit at a time reads 0002, 0020, 0202 on its way to
    2026 (#663). Entered like that, or half typed, it is said so and the
    box goes back to the date in use; one in the address is ignored and
    said so; one kept is not used. None of them is kept."""
    page = _register_page(browser, [], "#/banking/1?start_date=2026-09-01")
    try:
        page.focus("#reg-start")
        _type(page, "10012")  # Oct 1, year 0002
        assert page.input_value("#reg-start") == "0002-10-01"
        page.keyboard.press("Enter")
        _toast_with(
            page,
            "The From date's year, 0002, is out of range: the year must be"
            " between 1900 and 2199 — kept from Sep 1, 2026",
        )
        assert page.input_value("#reg-start") == "2026-09-01"
        assert (
            page.evaluate("() => location.hash") == "#/banking/1?start_date=2026-09-01"
        )
        assert page.evaluate(KEPT, 1) == "2026-09-01"

        # half typed: the month emptied
        page.evaluate("() => document.activeElement.blur()")
        page.focus("#reg-start")
        page.keyboard.press("Backspace")
        assert page.evaluate(
            "() => document.getElementById('reg-start').validity.badInput"
        )
        page.keyboard.press("Enter")
        _toast_with(page, "The From date is not complete — kept from Sep 1, 2026")
        assert page.input_value("#reg-start") == "2026-09-01"
        assert page.evaluate(KEPT, 1) == "2026-09-01"

        # To the same way, and the bar left (not Enter)
        page.focus("#reg-end")
        _type(page, "123120")  # Dec 31, year 0020
        page.click("#page-content h2")
        _toast_with(page, "The To date's year, 0020, is out of range")
        assert page.input_value("#reg-end") == ""
        assert (
            page.evaluate("() => location.hash") == "#/banking/1?start_date=2026-09-01"
        )

        # in the address: said, ignored, not kept
        _visit(page, "#/banking/1?start_date=0002-10-01", "#reg-start[value='']")
        _toast_with(
            page,
            "start_date in the address is out of range (0002-10-01): the year"
            " must be between 1900 and 2199 — ignored",
        )
        page.wait_for_function("() => location.hash === '#/banking/1'")
        assert page.evaluate(KEPT, 1) == "2026-09-01"

        # kept by an earlier build: not used
        page.evaluate(
            "() => localStorage.setItem(BankingPage._fromKey(1), '0202-10-01')"
        )
        _open_register(page, 4, "")
        _open_register(page, 1, "")
        assert page.evaluate("() => location.hash") == "#/banking/1"
    finally:
        page.close()


def test_a_from_after_today_is_not_kept_nor_used(browser):
    """The From kept opens every link to the register with no dates in it
    (#663): one after today would open it on nothing yet. A From typed
    after today applies, and the one kept before it stays; one kept after
    today (the clock since moved back, an earlier build) is not used."""
    today = dt.date.today()
    ahead = (today + dt.timedelta(days=10)).isoformat()
    page = _register_page(browser, [], "#/banking/1?start_date=2026-09-01")
    try:
        assert page.evaluate(KEPT, 1) == "2026-09-01"
        page.fill("#reg-start", ahead)  # picked: applies at once
        page.wait_for_function(
            "(d) => location.hash === `#/banking/1?start_date=${d}`", arg=ahead
        )
        assert page.evaluate(KEPT, 1) == "2026-09-01"
        _open_register(page, 4, "")
        _open_register(page, 1, "2026-09-01")

        page.evaluate(
            "(d) => localStorage.setItem(BankingPage._fromKey(1), d)",
            (today + dt.timedelta(days=1)).isoformat(),
        )
        _open_register(page, 4, "")
        _open_register(page, 1, "")
        assert page.evaluate("() => location.hash") == "#/banking/1"
        # today itself is a day the register can open on
        page.evaluate(
            "(d) => localStorage.setItem(BankingPage._fromKey(1), d)", today.isoformat()
        )
        _open_register(page, 4, "")
        _open_register(page, 1, today.isoformat())
    finally:
        page.close()


def _reload(page):
    """A new page load (a company switch reloads the page)."""
    page.goto(page.url.split("#")[0] + "#/")
    page.reload()
    page.wait_for_function("window.App && document.readyState === 'complete'")
    page.evaluate(
        "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
    )


def test_the_from_kept_is_the_company_files_not_its_names(browser):
    """The From was kept by the company's name, as /api/auth/status gives
    it (#663): "" for a company with no name yet or still called My
    Company, so two of those shared one, while account ids start again in
    every company file and a rename dropped the dates. It is kept by the
    file the server says is open; with no file and no name, none is."""
    companies = [
        {"name": "Harbor Light Bakery Two", "file": "harbor.db", "is_current": True},
        {"name": "", "file": "unnamed.db", "is_current": False},
    ]
    page = _register_page(
        browser, [], "#/banking/1?start_date=2026-09-01", companies=companies
    )
    try:
        # renamed: the same file, the same From
        page.evaluate("() => { App._tabsOwner = '|Harbor Light Bakery Renamed'; }")
        _open_register(page, 4, "")
        _open_register(page, 1, "2026-09-01")
        assert page.evaluate("() => BankingPage._fromKey(1)").endswith(
            "|file:harbor.db:1"
        )

        # another file, with no name: not this one's From, and its own kept
        companies[0]["is_current"], companies[1]["is_current"] = False, True
        page.route(
            "**/api/auth/status",
            lambda r: r.fulfill(
                json={"authenticated": True, "multi_user": False, "company_name": ""}
            ),
        )
        _reload(page)
        _open_register(page, 1, "")
        page.fill("#reg-start", "2026-08-01")
        page.wait_for_function(
            "() => location.hash === '#/banking/1?start_date=2026-08-01'"
        )
        companies[0]["is_current"], companies[1]["is_current"] = True, False
        _reload(page)
        _open_register(page, 1, "2026-09-01")

        # no file named and no name: nothing is kept, under any key
        companies.clear()
        _reload(page)
        assert page.evaluate("() => BankingPage._fromKey(1)") is None
        _open_register(page, 1, "")
        page.fill("#reg-start", "2026-07-01")
        page.wait_for_function(
            "() => location.hash === '#/banking/1?start_date=2026-07-01'"
        )
        _open_register(page, 4, "")
        _open_register(page, 1, "")
        assert page.evaluate(
            "() => Object.keys(localStorage).filter(k => k.startsWith('slowbooks-register-from:'))"
            ".map(k => k.split(':').slice(1).join(':')).sort()"
        ) == ["|file:harbor.db:1", "|file:unnamed.db:1"]
    finally:
        page.close()


def _journal_entry(ledger, txn):
    e = ledger._by_id(txn)
    return {
        "id": txn,
        "date": e["date"],
        "description": e["payee"],
        "reference": "",
        "source_type": "bank_entry",
        "lines": [],
        "total_debit": 25,
        "total_credit": 25,
        "register_account_id": 1,
        "register_account_name": "Checking",
    }


REGISTER_LINK = """() => {
    const a = document.querySelector('#journal-register-note a');
    return a && a.getAttribute('href');
}"""


def test_the_journal_link_to_the_register_opens_with_the_entry_in_the_list(
    browser,
):
    """A journal entry posted from a register says "edit it there", linking
    to the register's bare address, which opens on the From kept (#663):
    one after the entry's date left it out. The link carries a From no
    later than the entry's date, so the entry is in the list."""
    ledger = _Ledger()
    page = _ledger_page(browser, ledger, "#/banking/1?start_date=2026-10-01")
    page.route(
        "**/api/journal/*",
        lambda r: r.fulfill(
            json=_journal_entry(
                ledger, int(urlsplit(r.request.url).path.split("/")[-1])
            )
        ),
    )
    try:
        assert page.locator('tr[data-txn="109"]').count() == 0  # Sep 10
        page.evaluate("async () => { await JournalPage.view(109); }")
        page.wait_for_selector("#journal-register-note a")
        assert page.evaluate(REGISTER_LINK) == "#/banking/1?start_date=2026-09-10"
        page.click("#journal-register-note a")
        page.wait_for_selector('#page-content tr[data-txn="109"]')
        assert (
            page.evaluate("() => location.hash") == "#/banking/1?start_date=2026-09-10"
        )

        # an entry after the From kept: the link opens on that From
        page.evaluate("async () => { await JournalPage.view(140); }")  # Oct 10
        page.wait_for_function(
            "() => document.getElementById('modal-title').textContent"
            " === 'Journal Entry #140'"
        )
        assert page.evaluate(REGISTER_LINK) == "#/banking/1?start_date=2026-09-10"
    finally:
        page.close()


def _statement_line(n, date, **more):
    return {
        "id": n,
        "date": date,
        "payee": f"Statement line {n}",
        "description": "",
        "amount": -5.0,
        **more,
    }


# the statement line no longer in the queue (its payee can still be in a
# toast: the offer to add it as a vendor)
GONE_FROM_REVIEW = """(n) => !document.querySelector(
    `#page-content button[onclick^="BankingPage.addLine(${n},"]`)"""


def test_the_review_actions_say_when_the_dates_leave_the_entry_out(browser):
    """+ Entry and the entry row say an entry dated outside From and To,
    which the list leaves out; the review queue's Add, Payout, Add all
    categorised and Match said nothing (#663). They say it the same way."""
    ledger = _Ledger()
    ledger.review = [
        _statement_line(70, "2026-09-01"),
        _statement_line(
            71,
            "2026-11-05",
            amount=50.0,
            suggested_payout={
                "provider": "stripe",
                "display_name": "Stripe",
                "clearing_balance": 80,
            },
        ),
        _statement_line(72, "2026-09-02", category_account_id=2),
        _statement_line(73, "2026-11-20", category_account_id=2),
        _statement_line(74, "2026-10-01"),
    ]
    ledger.candidates = [
        {
            "line_id": 105,
            "date": "2026-09-06",
            "payee": "Payee 105",
            "description": "",
            "reference": "",
            "source_type": "bank_entry",
            "days_off": 25,
        }
    ]
    at = "#/banking/1?start_date=2026-09-15&end_date=2026-10-31"
    page = _ledger_page(browser, ledger, at, feed=True)
    row = lambda n: page.locator("tr", has_text=f"Statement line {n}")  # noqa: E731
    try:
        page.wait_for_selector("text=Statement line 70")
        row(70).locator('button[onclick^="BankingPage.addLine"]').click()
        _toast_with(
            page,
            "Added to the books. Its date, Sep 1, 2026, is before the From date,"
            " so the register does not show it.",
        )
        page.wait_for_function(GONE_FROM_REVIEW, arg=70)

        row(71).locator("button", has_text="Payout").click()
        page.wait_for_selector("#payout-gross")
        page.click("#modal-body button[type='submit']")
        _toast_with(
            page,
            "Payout recorded. Its date, Nov 5, 2026, is after the To date,"
            " so the register does not show it.",
        )
        page.wait_for_function(GONE_FROM_REVIEW, arg=71)

        page.click("text=Add all categorised")
        _toast_with(
            page,
            "Added 2. 2 of them are dated before the From date or after the To"
            " date, so the register does not show them.",
        )
        page.wait_for_function(GONE_FROM_REVIEW, arg=72)

        row(74).locator("button", has_text="Match").click()
        page.locator("#modal-body button", has_text="Match").click()
        _toast_with(
            page,
            "Matched. Its date, Sep 6, 2026, is before the From date,"
            " so the register does not show it.",
        )
        assert page.evaluate("() => location.hash") == at
    finally:
        page.close()


ROW_TOP = """(txn) => document.querySelector(`#page-content tr[data-txn="${txn}"]`)
    .getBoundingClientRect().top - document.getElementById('content').getBoundingClientRect().top"""


def test_an_edit_to_a_row_on_screen_does_not_move_it(browser):
    """After an edit the entry's row was scrolled to the middle of the
    page, so a row on screen jumped on every edit (#663). One on screen
    stays where it was; one the edit moved off screen is brought in."""
    ledger = _Ledger()
    at = "#/banking/1?start_date=2026-09-01&end_date=2026-10-31"
    page = _ledger_page(browser, ledger, at)
    try:
        page.evaluate(
            "() => { const row = document.querySelector('tr[data-txn=\"120\"]');"
            " row.scrollIntoView({ block: 'start' });"
            " document.getElementById('content').scrollTop -= 60; }"
        )
        top = page.evaluate(ROW_TOP, 120)
        assert 40 < top < 120
        page.locator('tr[data-txn="120"] button', has_text="Edit").click()
        page.wait_for_function(
            "() => document.getElementById('modal-title').textContent === 'Edit entry'"
        )
        page.fill("#entry-payee", "Harbor Fuel")
        page.click("#modal-body button[type='submit']")
        _toast_with(page, "Entry saved")
        page.wait_for_function(
            "() => document.querySelector('tr[data-txn=\"120\"]').textContent.includes('Harbor Fuel')"
        )
        assert abs(page.evaluate(ROW_TOP, 120) - top) <= 1
    finally:
        page.close()


def test_a_transfer_keeps_the_dates_and_comes_into_view(browser):
    """A transfer posted from the register it leaves went back to the bare
    address, which dropped the dates (#663): it keeps them, its row comes
    into view, and one dated past To is said so."""
    ledger = _Ledger()

    def transfers(route):
        body = route.request.post_data_json
        txn = 900 + len(ledger.entries)
        ledger.entries.append(ledger._entry(txn, body["date"], "Transfer to savings"))
        route.fulfill(
            status=201,
            json={
                "id": txn,
                "date": body["date"],
                "from_account_id": body["from_account_id"],
                "to_account_id": body["to_account_id"],
            },
        )

    at = "#/banking/1?start_date=2026-09-01&end_date=2026-10-31"
    page = _ledger_page(browser, ledger, at)
    page.route("**/api/transfers", transfers)

    def transfer(date):
        page.locator("#page-content .page-header button", has_text="Transfer").click()
        page.wait_for_selector("#modal-body [name='from_account_id']")
        assert page.input_value("#modal-body [name='from_account_id']") == "1"
        page.fill("#modal-body [name='date']", date)
        page.fill("#modal-body [name='amount']", "100")
        page.click("#modal-body button[type='submit']")

    try:
        transfer("2026-10-25")
        _toast_with(page, "Transfer posted")
        page.wait_for_function(IN_VIEW, arg=ledger.entries[-1]["transaction_id"])
        assert page.evaluate(SCROLLED) > 0
        assert page.evaluate("() => location.hash") == at
        assert page.input_value("#reg-end") == "2026-10-31"

        transfer("2026-11-15")
        _toast_with(
            page,
            "Transfer posted. Its date, Nov 15, 2026, is after the To date,"
            " so the register does not show it.",
        )
        page.wait_for_selector("#modal-overlay.hidden", state="attached")
        assert page.evaluate("() => location.hash") == at
    finally:
        page.close()


def test_a_registers_filter_is_not_carried_to_another(browser):
    """The filter a register goes back to is its own (#663): from a view
    that belongs to another account (a reconciliation's Later), that
    account's register opens on its own address, not on this one's dates."""
    ledger = _Ledger()
    at = "#/banking/1?start_date=2026-09-01&end_date=2026-10-31"
    page = _ledger_page(browser, ledger, at)
    page.route(
        "**/api/banking/reconciliations/7/transactions",
        lambda r: r.fulfill(
            json={
                "account_id": 4,
                "statement_date": "2026-09-30",
                "statement_balance": 0,
                "beginning_balance": 0,
                "cleared_total": 0,
                "difference": 0,
                "ticked_after_date": 0,
                "transactions": [],
            }
        ),
    )
    try:
        assert page.evaluate("() => BankingPage._regUrl(1)") == at
        page.evaluate("async () => { await BankingPage.showReconcileView(7); }")
        page.locator("#page-content .page-header button", has_text="Later").click()
        page.wait_for_function(
            "() => BankingPage._ctx && BankingPage._ctx.accountId === 4"
        )
        assert page.evaluate("() => location.hash") == "#/banking/4"
        assert page.input_value("#reg-start") == ""
        assert page.input_value("#reg-end") == ""
    finally:
        page.close()


def test_a_from_in_the_address_is_the_one_kept(browser):
    """A register opened on an address with dates (a bookmark, a report's
    link, Back) is read from its From: that is the From kept, and the bare
    address opens on it next, without the To (#663)."""
    page = _register_page(
        browser, [], "#/banking/1?start_date=2026-09-15&end_date=2026-09-30"
    )
    try:
        assert page.evaluate(KEPT, 1) == "2026-09-15"
        _open_register(page, 4, "")
        _open_register(page, 1, "2026-09-15")
        assert (
            page.evaluate("() => location.hash") == "#/banking/1?start_date=2026-09-15"
        )
        assert page.input_value("#reg-end") == ""
        # an address with only a To keeps the From it had
        _visit(page, "#/banking/1?end_date=2026-09-20", "#reg-end[value='2026-09-20']")
        assert page.evaluate(KEPT, 1) == "2026-09-15"
    finally:
        page.close()


# ── #728: the register opens at its newest entries ──

ELEMENT_IN_VIEW = """(sel) => {
    const el = document.querySelector(sel);
    if (!el) return false;
    const box = document.getElementById('content').getBoundingClientRect();
    const r = el.getBoundingClientRect();
    return r.top >= box.top - 1 && r.bottom <= box.bottom + 1;
}"""


def _redrawn(page):
    """Mark the drawn filter bar; the wait that follows sees a new one."""
    page.evaluate(
        "() => { document.getElementById('reg-filters').dataset.probe = 'before'; }"
    )


def _wait_redrawn(page):
    page.wait_for_function(
        "() => document.getElementById('reg-filters')"
        " && document.getElementById('reg-filters').dataset.probe !== 'before'"
    )


def test_the_register_opens_with_the_newest_entries_and_the_entry_row_in_view(
    browser,
):
    """Years of entries and the entry row at the foot were a long scroll
    down, every time the register opened (#728). It opens with the newest
    rows and the entry row in view, the list still oldest to newest, and
    nothing on the page takes the focus."""
    ledger = _Ledger()
    page = _ledger_page(browser, ledger, "#/banking/1")
    try:
        page.wait_for_function(ELEMENT_IN_VIEW, arg="#reg-quick-entry")
        assert page.evaluate(SCROLLED) > 0
        assert page.evaluate(IN_VIEW, 159)  # the newest
        assert not page.evaluate(IN_VIEW, 100)  # the oldest is above
        dates = page.eval_on_selector_all(
            "#page-content tr[data-txn]",
            "rows => rows.map(r => r.cells[0].textContent)",
        )
        assert dates[0] == "Sep 1, 2026" and dates[-1] == "Oct 30, 2026"
        assert not page.evaluate(
            "() => document.getElementById('page-content')"
            ".contains(document.activeElement)"
        )

        # opened again from another page, and with a From: at the foot again
        _visit(page, "#/banking/4", "#reg-filters[data-account='4']")
        _visit(
            page,
            "#/banking/1?start_date=2026-09-05",
            "#reg-opening",
        )
        page.wait_for_function(ELEMENT_IN_VIEW, arg="#reg-quick-entry")
        assert page.input_value("#reg-start") == "2026-09-05"
    finally:
        page.close()


def test_a_filter_changed_by_hand_leaves_the_page_where_it_was(browser):
    """Only an opened register goes to its foot (#728): a date changed on
    the bar draws it again in place."""
    ledger = _Ledger()
    page = _ledger_page(browser, ledger, "#/banking/1")
    try:
        page.wait_for_function(ELEMENT_IN_VIEW, arg="#reg-quick-entry")
        # scrolled up by hand to the filter bar, the page not at its top
        top = page.evaluate(
            "() => { document.getElementById('reg-filters')"
            ".scrollIntoView({ block: 'start' });"
            " return document.getElementById('content').scrollTop; }"
        )
        assert top > 50
        _redrawn(page)
        page.fill("#reg-end", "2026-10-25")
        _wait_redrawn(page)
        assert page.evaluate("() => location.hash") == "#/banking/1?end_date=2026-10-25"
        assert abs(page.evaluate(SCROLLED) - top) <= 1
        assert not page.evaluate(ELEMENT_IN_VIEW, "#reg-quick-entry")
        # and a From, the list shorter at its top: the same
        _redrawn(page)
        page.fill("#reg-start", "2026-09-10")
        _wait_redrawn(page)
        assert abs(page.evaluate(SCROLLED) - top) <= 1
    finally:
        page.close()


# ── #729: the Category column ──


def test_the_category_column_names_the_account_or_counts_a_split(browser):
    ledger = _Ledger(days=3)
    one, split, none = ledger.entries
    one["category_lines"] = [
        {
            "account_id": 2,
            "account_number": "6100",
            "account_name": "Automobile",
            "amount": 25.0,
        }
    ]
    one["category"] = "Automobile"
    split["category_lines"] = [
        {
            "account_id": 2,
            "account_number": "6100",
            "account_name": "Automobile",
            "amount": 10.0,
        },
        {
            "account_id": 4,
            "account_number": "1400",
            "account_name": "Prepaid Expenses",
            "amount": 12.5,
        },
        {
            "account_id": 5,
            "account_number": "6400",
            "account_name": "Office Supplies",
            "amount": 2.5,
        },
    ]
    split["category"] = "3 accounts"
    page = _ledger_page(browser, ledger, "#/banking/1")
    try:
        heads = page.eval_on_selector_all(
            "#page-content thead th", "ths => ths.map(t => t.textContent.trim())"
        )
        assert heads[4:6] == ["Type", "Category"]
        col = heads.index("Category")
        cell = (
            '(txn, col) => document.querySelector(`tr[data-txn="${txn}"]`).cells[col]'
        )
        text = page.evaluate(
            f"([t, c]) => ({cell})(t, c).textContent", [one["transaction_id"], col]
        )
        assert text.strip() == "6100 - Automobile"  # numbers on
        got = page.evaluate(
            f"([t, c]) => {{ const td = ({cell})(t, c);"
            " return { text: td.firstChild.textContent, title: td.title,"
            " hidden: td.querySelector('.visually-hidden').textContent }; }",
            [split["transaction_id"], col],
        )
        assert got["text"] == "3 accounts"
        assert "-split-" not in got["text"].lower()
        assert got["title"].split("\n") == [
            "6100 - Automobile $10.00",
            "1400 - Prepaid Expenses $12.50",
            "6400 - Office Supplies $2.50",
        ]
        assert (
            "6100 - Automobile $10.00; 1400 - Prepaid Expenses $12.50" in got["hidden"]
        )
        assert (
            page.evaluate(
                f"([t, c]) => ({cell})(t, c).textContent", [none["transaction_id"], col]
            ).strip()
            == ""
        )
        # the entry row's Category box sits under the column
        assert page.evaluate(
            "(col) => document.getElementById('qe-category').closest('td').cellIndex === col",
            col,
        )
        # the Posting link of a register entry stays: the Memo cell is as it was
        assert (
            page.locator(
                f'tr[data-txn="{one["transaction_id"]}"] td:nth-child(3) a'
            ).count()
            == 1
        )
    finally:
        page.close()


def test_the_empty_register_has_the_populated_ones_columns(browser):
    posted = []
    page = _register_page(browser, posted)
    try:
        empty = page.eval_on_selector_all(
            "#page-content thead th", "ths => ths.map(t => t.textContent.trim())"
        )
    finally:
        page.close()
    ledger = _Ledger(days=2)
    page = _ledger_page(browser, ledger, "#/banking/1")
    try:
        full = page.eval_on_selector_all(
            "#page-content thead th", "ths => ths.map(t => t.textContent.trim())"
        )
    finally:
        page.close()
    assert empty == full
    assert full[:6] == ["Date", "Payee", "Memo", "Ref #", "Type", "Category"]
