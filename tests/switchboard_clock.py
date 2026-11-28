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

- a press held by the relay (see Holder) ended at its deadline: the
  deadline's own end step shut the very connection the relay was holding
  (PressClock.shut), and the relay saw its end of that connection closed
  by the press before the test let it go (ended_by_its_deadline). A press
  that waits out a socket timeout of its own, or whose deadline shuts
  nothing, fails that, however long or short its wait;
- a press against a relay that answers at once never has its time run out,
  so its outcome is the relay's answer, however slow the machine;
- a call that must return at once asks its own thread to wait for nothing
  (NoWait): a sleep, a wait for an event, a thread, a lock or a socket is
  caught as it is asked for, not by a stopwatch noticing it afterwards.
  AT_ONCE is only the last line of defence behind that.
"""

import functools
import socket
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

from app.services import switchboard_link as link

# What left() says while the deadline has not run out: far longer than any
# test, so no socket timeout set from it can end a wait by itself.
HELD = 3600.0
# How long a relay that holds its answer (Holder) keeps it at most when the
# test never lets it go: reached only when a press waits for the relay
# instead of ending at its deadline, which is then a failure (Holder.held_out).
BACKSTOP = 60.0
# The most a call that returns at once may ask its own thread to wait, one
# wait or all of them together: a poll's tick (the record lock polls its
# file every 0.05 s when another window holds it), never a wait for the
# relay, a thread, a timer or another holder.
LIMIT = 0.1
# A wall-clock bound on a call that returns at once, behind NoWait, for a
# wait no recorder sees. 20 s: the bounds that flaked on slow runners were
# half a second to a second for a whole Connect, and the 5 s and 10 s ones
# never did, so a slow runner is far inside it; and it is a third of
# BACKSTOP, so a call that waited for a hold to give up fails here too.
AT_ONCE = 20.0


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
        # the press's own address on each connection the end step shut
        self.shut = None
        PressClock.made.append(self)

    def left(self) -> float:
        return 0.0 if self._fired else HELD

    def over(self) -> bool:
        return self._fired

    def run_out(self) -> None:
        """The press's time is up: its connections are shut, as at the
        product's deadline (its own _end). ``shut`` is what that end step
        did: the press's address on each connection it held that the step
        closed (a press that never handed it a connection, or an end step
        that shuts nothing, leaves it empty)."""
        with self._lock:
            held = [(twin, _address(twin)) for twin in self._held]
        self._end()
        self.shut = [where for twin, where in held if twin.fileno() == -1]
        PressClock.ran_out.append(self)

    @classmethod
    def latest(cls) -> "PressClock":
        return cls.made[-1]


def _address(sock):
    try:
        return sock.getsockname()
    except OSError:
        return None


def hold_the_clock(monkeypatch) -> type:
    """Every press deadline from here on is a PressClock."""
    PressClock.made = []
    PressClock.ran_out = []
    monkeypatch.setattr(link, "_Deadline", PressClock)
    return PressClock


class Holder:
    """A relay's answer held back until the test lets it go: ``hold(conn)``
    runs out the press's time (the latest PressClock) and then waits, with
    its answer unsent, for the press to close its end of ``conn``, for up
    to BACKSTOP. ``let_go()`` ends the hold. ``holds`` has one entry for
    each connection held: the press's address on it (``peer``), the press's
    deadline (``clock``), and ``closed``, set when the press closed its end
    while the test had not let the relay go yet. ``held_out`` is set when a
    hold ran to its backstop: the press was waiting for the relay, not
    ending at its deadline."""

    def __init__(self):
        self.holds = []
        self.holding = threading.Event()
        self.released = threading.Event()
        self.held_out = threading.Event()

    def hold(self, conn) -> None:
        one = SimpleNamespace(
            peer=conn.getpeername(),
            clock=PressClock.latest(),
            closed=threading.Event(),
        )
        self.holds.append(one)
        self.holding.set()
        one.clock.run_out()
        if self._closed_by_the_press(conn):
            one.closed.set()
        elif not self.released.is_set():
            self.held_out.set()

    def _closed_by_the_press(self, conn) -> bool:
        """Whether the press closed its end of ``conn`` (an end of file, or
        a reset) before the test let the relay go or BACKSTOP passed. The
        press has sent its whole request: it has nothing more to say."""
        until = time.monotonic() + BACKSTOP
        while not self.released.is_set():
            left = until - time.monotonic()
            if left <= 0:
                return False
            conn.settimeout(min(left, 0.25))
            try:
                if not conn.recv(65536):
                    return not self.released.is_set()
            except TimeoutError:
                continue
            except OSError:
                return not self.released.is_set()
        return False

    def let_go(self) -> None:
        self.released.set()


def ended_by_its_deadline(holder: Holder, clocks: list) -> None:
    """Each press in ``clocks`` (its PressClock, in order) was held by the
    relay and ended at its deadline: its deadline's end step shut the
    connection the relay was holding, and the relay saw the press close it
    before the test let it go. A press that ended any other way (a socket
    timeout of its own, an end step that shuts nothing, a relay that gave
    up) fails here."""
    assert holder.holding.is_set(), "the relay never held an answer"
    assert [
        one.clock for one in holder.holds
    ] == clocks, "a press the relay held is not the one whose deadline ran out"
    for one in holder.holds:
        assert one.clock in PressClock.ran_out, "the held press's time never ran out"
        assert one.peer in one.clock.shut, (
            f"the deadline's end step shut {len(one.clock.shut)} connection(s) "
            "and not the one the relay was holding: something else ended the press"
        )
        assert one.closed.wait(BACKSTOP), (
            "the relay never saw the press close the held connection before "
            "the test let it go"
        )
    assert not holder.held_out.is_set(), "a hold reached its backstop"


def settle_the_start() -> None:
    """link.start_in_background() returns at once and does its work in a
    thread: wait for that thread to have finished, so what the start did
    (or did not do) can be checked directly, not by waiting a while and
    seeing that nothing happened."""
    for thread in threading.enumerate():
        if thread.name == "claude-relay-start":
            thread.join(BACKSTOP)
            assert not thread.is_alive(), "the start's own thread did not finish"


# -- a call that returns at once asks for no wait ------------------------------------

_LockType = type(threading.Lock())
_RLockType = type(threading.RLock())


class _WatchedLock:
    """A lock whose waits are seen by a NoWait: one free at once is taken
    with no wait; one that is held is a wait, asked for as long as the
    caller says (no timeout: with no end)."""

    def __init__(self, inner, waits: "NoWait"):
        self._inner = inner
        self._waits = waits

    def acquire(self, blocking=True, timeout=-1):
        if self._inner.acquire(False):
            return True
        if not blocking:
            return False
        if self._waits._watched():
            asked = None if timeout is None or timeout < 0 else timeout
            # the lock is still waited for (to BACKSTOP at most): going on
            # without it would break the product's own bookkeeping
            timeout = self._waits._asked("a wait for a lock", asked, cut=BACKSTOP)
        return self._inner.acquire(True, timeout)

    def release(self):
        self._inner.release()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_exc):
        self.release()

    def __getattr__(self, name):
        return getattr(self._inner, name)


class NoWait:
    """Proves a call returns at once by what it asks for, not by a clock.

    While a call made watched (``watch``) runs, the ways its own thread
    could be made to wait are replaced with recorders: time.sleep,
    Event.wait, Condition.wait (so a queue's wait too), Thread.join, a wait
    for a lock (every lock of the link module, and every lock made during
    the call), and, unless ``sockets`` is true, a socket's timeout, a
    blocking socket call and a name lookup. A wait longer than LIMIT, a
    wait with no end, or waits adding up to more than LIMIT, are noted in
    ``asked`` and fail ``check()``; the wait itself is then cut to LIMIT
    (a lock's to BACKSTOP), so a call that waits fails at once instead of
    hanging. A wait that would end at once (an event already set, a thread
    already finished, a free lock) is not a wait. Other threads, and the
    thread's own Thread.start handshake, are left as they are. The
    originals are put back when the last watched call returns.

    ``sockets=True`` is for a press: it talks to the relay, and its wait
    for the relay's answer is what the press's deadline ends (see
    ended_by_its_deadline); every other wait is still caught."""

    def __init__(self, what: str, sockets: bool = False):
        self.what = what
        self.sockets = sockets
        self.asked = []
        self._total = 0.0
        self._threads = {}
        self._guard = threading.local()
        self._mutex = threading.Lock()
        self._saved = []

    # -- what is watched

    def watch(self, monkeypatch, owner, name: str) -> None:
        """Every call of owner.name from here on is watched."""
        real = getattr(owner, name)

        @functools.wraps(real)
        def watched(*args, **kw):
            with self.watching():
                return real(*args, **kw)

        monkeypatch.setattr(owner, name, watched)

    @contextmanager
    def watching(self):
        me = threading.get_ident()
        with self._mutex:
            if not self._threads:
                self._install()
            self._threads[me] = self._threads.get(me, 0) + 1
        try:
            yield self
        finally:
            with self._mutex:
                self._threads[me] -= 1
                if not self._threads[me]:
                    del self._threads[me]
                if not self._threads:
                    self._restore()

    def check(self) -> None:
        assert not self.asked, f"{self.what} asked its own thread to wait: " + (
            "; ".join(
                f"{what} for {'no end' if s is None else f'{s:g} s'}"
                for what, s in self.asked
            )
        )

    # -- the recorders

    def _watched(self) -> bool:
        return threading.get_ident() in self._threads and not getattr(
            self._guard, "inside", False
        )

    def _asked(self, what: str, seconds, cut: float = LIMIT):
        """A wait of ``seconds`` (None: with no end) is asked for on a
        watched thread: what is waited for instead."""
        if seconds is not None and self._total + max(seconds, 0.0) <= LIMIT:
            self._total += max(seconds, 0.0)
            return seconds
        self.asked.append((what, seconds))
        return cut if seconds is None else min(seconds, cut)

    @contextmanager
    def _inside(self):
        was = getattr(self._guard, "inside", False)
        self._guard.inside = True
        try:
            yield
        finally:
            self._guard.inside = was

    def _set(self, owner, name, value) -> None:
        self._saved.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    def _restore(self) -> None:
        while self._saved:
            owner, name, value = self._saved.pop()
            setattr(owner, name, value)

    def _install(self) -> None:
        waits = self
        sleep = time.sleep
        event_wait = threading.Event.wait
        condition_wait = threading.Condition.wait
        join = threading.Thread.join
        start = threading.Thread.start
        make_lock, make_rlock = threading.Lock, threading.RLock

        def watched_sleep(seconds):
            if waits._watched():
                seconds = waits._asked("time.sleep", seconds)
            return sleep(seconds)

        def watched_event_wait(event, timeout=None):
            if waits._watched() and not event.is_set():
                timeout = waits._asked("Event.wait", timeout)
            with waits._inside():
                return event_wait(event, timeout)

        def watched_condition_wait(condition, timeout=None):
            if waits._watched():
                timeout = waits._asked("Condition.wait", timeout)
            with waits._inside():
                return condition_wait(condition, timeout)

        def watched_join(thread, timeout=None):
            if waits._watched() and thread.is_alive():
                timeout = waits._asked("Thread.join", timeout)
            with waits._inside():
                return join(thread, timeout)

        def watched_start(thread):
            # its handshake with the new thread is not the call waiting
            with waits._inside():
                return start(thread)

        self._set(time, "sleep", watched_sleep)
        self._set(threading.Event, "wait", watched_event_wait)
        self._set(threading.Condition, "wait", watched_condition_wait)
        self._set(threading.Thread, "join", watched_join)
        self._set(threading.Thread, "start", watched_start)
        self._set(threading, "Lock", lambda: _WatchedLock(make_lock(), waits))
        self._set(threading, "RLock", lambda: _WatchedLock(make_rlock(), waits))
        for name, value in list(vars(link).items()):
            if isinstance(value, (_LockType, _RLockType)):
                self._set(link, name, _WatchedLock(value, waits))
        if not self.sockets:
            self._install_sockets()

    def _install_sockets(self) -> None:
        waits = self
        lookup = socket.getaddrinfo
        settimeout = socket.socket.settimeout

        def watched_lookup(*args, **kw):
            if waits._watched():
                waits._asked("a name lookup", None)
            return lookup(*args, **kw)

        def watched_settimeout(sock, value):
            if waits._watched() and (value is None or value > LIMIT):
                waits._asked("a socket timeout", value)
            return settimeout(sock, value)

        self._set(socket, "getaddrinfo", watched_lookup)
        self._set(socket.socket, "settimeout", watched_settimeout)
        for name in ("connect", "connect_ex", "accept", "recv", "recv_into", "sendall"):
            real = getattr(socket.socket, name)

            def watched_call(sock, *args, _real=real, _name=name, **kw):
                if waits._watched():
                    limit = sock.gettimeout()
                    if limit is None or limit > LIMIT:
                        waits._asked(f"a blocking socket.{_name}", limit)
                return _real(sock, *args, **kw)

            self._set(socket.socket, name, watched_call)


@contextmanager
def at_once(what: str):
    """The last line of defence behind NoWait: the block takes under
    AT_ONCE of wall-clock time, however slow the machine (see AT_ONCE)."""
    began = time.monotonic()
    yield
    took = time.monotonic() - began
    assert took < AT_ONCE, f"{what} took {took:.1f} s (at most {AT_ONCE:g} s)"
