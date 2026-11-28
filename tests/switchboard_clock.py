"""A press deadline the tests control, for the relay tests.

The product's press deadline (switchboard_link._Deadline) is wall-clock
time: a threading.Timer shuts the press's connections when it fires, and
left() counts down on time.monotonic(). A test that gives the press a short
real deadline lets the speed of the machine decide which step the press
reaches before its time is up: on a slow runner the link, the token and the
fsynced writes of a Connect against a relay that answers at once used up a
half-second deadline, and the press said the relay could not be reached.

PressClock is the same deadline with no timer: it runs out only when the
test says so (run_out(), from any thread), and until then every socket
timeout the press sets is long enough that nothing but the deadline (or the
relay's own answer) can end a wait. Its run_out() is the product's own
_end(): the press's connections are shut exactly as at a real deadline.

What a test proves with it does not depend on the machine:

- a press that answers while the relay is still holding its answer (see
  Holder) did not wait for the relay; with no timer and a wait limited only
  by HELD, the deadline running out is what ended it;
- a press against a relay that answers at once never has its time run out,
  so its outcome is the relay's answer, however slow the machine.
"""

import threading

from app.services import switchboard_link as link

# What left() says while the deadline has not run out: far longer than any
# test, so no socket timeout set from it can end a wait by itself.
HELD = 3600.0
# How long a relay that holds its answer (Holder) keeps it at most when the
# test never lets it go: reached only when a press waits for the relay
# instead of ending at its deadline, which is then a failure (Holder.held_out).
BACKSTOP = 60.0


class PressClock(link._Deadline):
    """A press deadline that runs out only when the test says so: no
    wall-clock timer, so how long the token, the fsynced writes and the
    relay calls take on a slow runner cannot change which step the press
    reaches before its time is up. ``made`` holds every one made, oldest
    first; ``ran_out`` every one whose time a test ran out."""

    made: list = []
    ran_out: list = []

    def __init__(self, seconds: float):
        self.at = None
        self.seconds = seconds
        self._fired = False
        self._lock = threading.Lock()
        self._held = []
        # never started: close() cancels it as it would the product's
        self._timer = threading.Timer(HELD, lambda: None)
        PressClock.made.append(self)

    def left(self) -> float:
        return 0.0 if self._fired else HELD

    def over(self) -> bool:
        return self._fired

    def run_out(self) -> None:
        """The press's time is up: its connections are shut, as at the
        product's deadline (its own _end)."""
        PressClock.ran_out.append(self)
        self._end()

    @classmethod
    def latest(cls) -> "PressClock":
        return cls.made[-1]


def hold_the_clock(monkeypatch) -> type:
    """Every press deadline from here on is a PressClock."""
    PressClock.made = []
    PressClock.ran_out = []
    monkeypatch.setattr(link, "_Deadline", PressClock)
    return PressClock


class Holder:
    """A relay's answer held back until the test lets it go: ``hold()``
    runs out the press's time (the latest PressClock) and then keeps the
    answer for up to BACKSTOP. ``let_go()`` ends the hold. ``held_out`` is
    set when the hold ran to its backstop: the press was waiting for the
    relay, not ending at its deadline."""

    def __init__(self):
        self.holding = threading.Event()
        self.released = threading.Event()
        self.held_out = threading.Event()

    def hold(self) -> None:
        self.holding.set()
        PressClock.latest().run_out()
        if not self.released.wait(BACKSTOP):
            self.held_out.set()

    def let_go(self) -> None:
        self.released.set()


def settle_the_start() -> None:
    """link.start_in_background() returns at once and does its work in a
    thread: wait for that thread to have finished, so what the start did
    (or did not do) can be checked directly, not by waiting a while and
    seeing that nothing happened."""
    for thread in threading.enumerate():
        if thread.name == "claude-relay-start":
            thread.join(BACKSTOP)
            assert not thread.is_alive(), "the start's own thread did not finish"
