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
  (NoWait): each wait it can see (its docstring says which) is caught as
  it is asked for, not by a stopwatch noticing it afterwards.

No test here bounds the wall-clock time of anything: a slow or loaded
machine makes every test slower, never a different result. BACKSTOP is
only how long a test waits for something that should already have
happened (a hold, the start's thread) before failing instead of hanging.
"""

import functools
import os
import select
import selectors
import socket
import sys
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
_LOCKS = (_LockType, _RLockType)
# the app's own code: a lock it takes itself is checked where it is taken
_APP = os.path.dirname(os.path.dirname(os.path.abspath(link.__file__))) + os.sep
# a wait whose length the recorder cannot see (a C call's arguments are not
# shown to a profile function): always a wait
UNSEEN = "unseen"
# how far from the link module's globals its locks are looked for
_REACH = 3


def _would_block(lock) -> bool:
    """Whether taking ``lock`` now would wait: a Lock that is held (by any
    thread, this one too), or an RLock held by another thread."""
    if isinstance(lock, _RLockType):
        return repr(lock).startswith("<locked") and not lock._is_owned()
    return lock.locked()


def _ours(obj) -> bool:
    """An object of the app's own (or a SimpleNamespace) whose attributes
    may hold a lock: not a module, a class or a function."""
    kind = type(obj)
    return not isinstance(obj, type) and (
        kind is SimpleNamespace or kind.__module__.startswith("app.")
    )


class _WatchedLock:
    """A lock whose waits are seen by a NoWait. It is the same lock (``inner``)
    in every thread; only the bookkeeping is added. ``name`` says which lock
    it is, for the failure's message.

    Taking it when it is free is no wait. When it is held and a watched
    thread asks for it, that thread waits for it (for its own timeout,
    BACKSTOP at most), and the wait counts when the timeout ran out first,
    or when a hold it waited behind had its holder wait (see
    NoWait._holder_waits) while holding it. A holder that only computes,
    for as long as a slow machine makes that take, is not a wait."""

    def __init__(self, inner, waits: "NoWait", name: str = "a lock"):
        self._inner = inner
        self._waits = waits
        self._name = name

    def acquire(self, blocking=True, timeout=-1):
        waits = self._waits
        watched = waits._watched()
        with waits._inside():
            got = self._inner.acquire(False)
            if not got and blocking and not watched:
                got = self._inner.acquire(True, timeout)
        if got:
            waits._took(self._inner)
            return True
        if not blocking or not watched:
            return False
        asked = None if timeout is None or timeout < 0 else timeout
        behind = waits._waited_holds(self._inner)
        with waits._inside():
            # still waited for: going on without it would break the
            # product's own bookkeeping
            got = self._inner.acquire(
                True, BACKSTOP if asked is None else min(asked, BACKSTOP)
            )
        if not got or waits._waited_holds(self._inner) != behind:
            why = (
                " (its holder waited while holding it)"
                if got
                else " (it stayed held to the end of the wait)"
            )
            waits._asked(f"a wait for {self._name}{why}", asked, cut=0.0)
        if got:
            waits._took(self._inner)
        return got

    def release(self):
        # counted before it is let go, so the next holder sees the count
        self._waits._gave(self._inner)
        self._inner.release()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_exc):
        self.release()

    def __getattr__(self, name):
        return getattr(self._inner, name)


class NoWait:
    """Proves a call returns at once by what it asks for, not by a clock.

    While a call made watched (``watch``) runs, its own thread is watched in
    two ways, and neither looks at how long anything took.

    Recorders, which see how long each wait is asked to be: time.sleep,
    Event.wait, Condition.wait (so a queue's wait too), Thread.join,
    select.select and a selector's select (the selectors module); unless
    ``sockets`` is true, a socket's timeout, a blocking socket call and a
    name lookup too. A wait longer than LIMIT, a wait with no end, or waits
    adding up to more than LIMIT, are noted in ``asked``; the wait itself
    is then cut to LIMIT, so a call that waits fails at once instead of
    hanging. A wait that ends at once (an event already set, a thread
    already finished, a timeout of 0) is not a wait.

    Locks: every lock in the link module's globals, every lock the link
    module reaches from its globals through the app's own objects,
    SimpleNamespaces and dicts (_REACH steps at most: the open line's own
    lock and its connection's, for some), and every lock made while a
    watched call runs, become _WatchedLocks of the same lock. A watched
    thread that asks for one of them while it is held waits for it (to its
    own timeout, BACKSTOP at most: this wait is not cut), and the wait
    counts when its own timeout ran out first or when the holder waited (in
    one of the recorders above, in any thread) while holding it.
    A lock held only while its holder computes is no wait, so the result
    does not depend on how fast the holder runs.

    A profile function (sys.setprofile) on the watched thread, for what the
    recorders cannot reach: a call into C that would wait, made outside a
    recorder. That is an ``acquire`` by the app's own code of a lock not
    wrapped above (one kept only in a closure, say) while that lock is held
    (a Lock held by anyone, an RLock held by another thread); a call of the
    original time.sleep or select.select (as when the app took it under
    another name before the watch began); and a poll or epoll object's
    poll. A C call does not show its arguments, so each of these counts as
    a wait of a length it does not show (UNSEEN), even a try that asks for
    no wait at all, and is not cut.

    Not seen: a ``with`` on a held lock that is not wrapped (Python takes it
    without a call a profile function sees); a holder's wait that no
    recorder sees (with ``sockets=True``, a blocking socket read); and any
    other wait in another thread. Other threads, and the thread's own
    Thread.start handshake, are left as they are. The originals are put
    back when the last watched call returns.

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
        self._c_waits = {}
        self._pollers = ()
        # each thread's holds of a _WatchedLock: [id of the lock, waited]
        self._holding = threading.local()
        # how many holds of each lock had their holder wait
        self._counts = {}

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
            first = me not in self._threads
            self._threads[me] = self._threads.get(me, 0) + 1
        if first:
            before = sys.getprofile()
            sys.setprofile(self._profile)
        try:
            yield self
        finally:
            if first:
                sys.setprofile(before)
            with self._mutex:
                self._threads[me] -= 1
                if not self._threads[me]:
                    del self._threads[me]
                if not self._threads:
                    self._restore()

    def check(self) -> None:
        def length(seconds):
            if seconds is None:
                return "no end"
            if seconds == UNSEEN:
                return "a time it does not show"
            return f"{seconds:g} s"

        assert not self.asked, f"{self.what} asked its own thread to wait: " + (
            "; ".join(f"{what} for {length(s)}" for what, s in self.asked)
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

    def _held(self) -> list:
        held = getattr(self._holding, "held", None)
        if held is None:
            held = self._holding.held = []
        return held

    def _took(self, inner) -> None:
        self._held().append([id(inner), False])

    def _gave(self, inner) -> None:
        held = self._held()
        for at in range(len(held) - 1, -1, -1):
            if held[at][0] == id(inner):
                if held.pop(at)[1]:
                    self._counts[id(inner)] = self._counts.get(id(inner), 0) + 1
                return

    def _waited_holds(self, inner) -> int:
        return self._counts.get(id(inner), 0)

    def _holder_waits(self, skip=None) -> None:
        """The calling thread (any thread) waits: each _WatchedLock it holds
        (but ``skip``, the lock a Condition.wait lets go of) is held across
        a wait. Not counted inside a recorder (an Event's own Condition, a
        Thread.start handshake)."""
        if getattr(self._guard, "inside", False):
            return
        for hold in self._held():
            if hold[0] != skip:
                hold[1] = True

    def _profile(self, frame, event, arg) -> None:
        """The watched thread's profile function: a call into C that would
        wait, made outside the recorders (see the class's docstring)."""
        if event != "c_call" or getattr(self._guard, "inside", False):
            return
        owner = getattr(arg, "__self__", None)
        name = getattr(arg, "__name__", "")
        if isinstance(owner, _LOCKS):
            if (
                name in ("acquire", "acquire_lock")
                and frame.f_code.co_filename.startswith(_APP)
                and _would_block(owner)
            ):
                self._unseen("a wait for a held lock", frame)
        elif id(arg) in self._c_waits:
            self._unseen(self._c_waits[id(arg)], frame)
        elif name == "poll" and isinstance(owner, self._pollers):
            self._unseen(f"{type(owner).__name__}.poll", frame)

    def _unseen(self, what: str, frame) -> None:
        code = frame.f_code
        where = f"{code.co_name}, {os.path.basename(code.co_filename)} line {frame.f_lineno}"
        self.asked.append((f"{what} (in {where})", UNSEEN))

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
            if callable(owner) and name is None:
                owner()  # an undo of its own (a lock reached from the globals)
            else:
                setattr(owner, name, value)

    def _install(self) -> None:
        waits = self
        sleep = time.sleep
        select_select = select.select
        event_wait = threading.Event.wait
        condition_wait = threading.Condition.wait
        join = threading.Thread.join
        start = threading.Thread.start
        make_lock, make_rlock = threading.Lock, threading.RLock

        def waiting(timeout) -> bool:
            return timeout is None or timeout > 0

        def watched_sleep(seconds):
            if seconds > 0:
                waits._holder_waits()
            if waits._watched():
                seconds = waits._asked("time.sleep", seconds)
            with waits._inside():
                return sleep(seconds)

        def watched_select(rlist, wlist, xlist, timeout=None):
            if waiting(timeout):
                waits._holder_waits()
            if waits._watched():
                timeout = waits._asked("select.select", timeout)
            with waits._inside():
                return select_select(rlist, wlist, xlist, timeout)

        def watched_event_wait(event, timeout=None):
            if not event.is_set() and waiting(timeout):
                waits._holder_waits()
            if waits._watched() and not event.is_set():
                timeout = waits._asked("Event.wait", timeout)
            with waits._inside():
                return event_wait(event, timeout)

        def watched_condition_wait(condition, timeout=None):
            if waiting(timeout):
                own = getattr(condition, "_lock", None)
                waits._holder_waits(skip=id(getattr(own, "_inner", own)))
            if waits._watched():
                timeout = waits._asked("Condition.wait", timeout)
            with waits._inside():
                return condition_wait(condition, timeout)

        def watched_join(thread, timeout=None):
            if thread.is_alive() and waiting(timeout):
                waits._holder_waits()
            if waits._watched() and thread.is_alive():
                timeout = waits._asked("Thread.join", timeout)
            with waits._inside():
                return join(thread, timeout)

        def watched_start(thread):
            # its handshake with the new thread is not the call waiting
            with waits._inside():
                return start(thread)

        self._c_waits = {
            id(sleep): "time.sleep, not through its recorder",
            id(select_select): "select.select, not through its recorder",
        }
        pollers = []
        if hasattr(select, "poll"):
            pollers.append(type(select.poll()))
        if hasattr(select, "epoll"):
            pollers.append(select.epoll)
        self._pollers = tuple(pollers)
        self._set(time, "sleep", watched_sleep)
        self._set(select, "select", watched_select)
        self._set(threading.Event, "wait", watched_event_wait)
        self._set(threading.Condition, "wait", watched_condition_wait)
        self._set(threading.Thread, "join", watched_join)
        self._set(threading.Thread, "start", watched_start)
        self._install_selectors()
        made = "a lock made during the call"
        self._set(threading, "Lock", lambda: _WatchedLock(make_lock(), waits, made))
        self._set(threading, "RLock", lambda: _WatchedLock(make_rlock(), waits, made))
        self._install_locks(vars(link), "link", 0, set())
        if not self.sockets:
            self._install_sockets()

    def _install_selectors(self) -> None:
        waits = self
        for kind in vars(selectors).values():
            if not (
                isinstance(kind, type)
                and issubclass(kind, selectors.BaseSelector)
                and kind is not selectors.BaseSelector
                and "select" in vars(kind)
            ):
                continue
            real = vars(kind)["select"]

            def watched_select(selector, timeout=None, _real=real):
                if timeout is None or timeout > 0:
                    waits._holder_waits()
                    if waits._watched():
                        timeout = waits._asked("a selector's select", timeout)
                with waits._inside():
                    return _real(selector, timeout)

            self._set(kind, "select", watched_select)

    def _install_locks(self, where, path: str, depth: int, seen: set) -> None:
        """Every lock in ``where`` (a dict, or an object of the app's own)
        becomes a _WatchedLock of that same lock, and the objects and dicts
        it holds are looked into in turn, _REACH steps at most."""
        if depth > _REACH or id(where) in seen:
            return
        seen.add(id(where))
        if isinstance(where, dict):
            items = list(where.items())

            def put(key, value, _where=where):
                _where[key] = value

            def get(key, _where=where):
                return _where.get(key)

        elif _ours(where) and hasattr(where, "__dict__"):
            items = list(vars(where).items())

            def put(key, value, _where=where):
                setattr(_where, key, value)

            def get(key, _where=where):
                return getattr(_where, key, None)

        else:
            return
        for key, value in items:
            if isinstance(value, _LOCKS):
                wrapped = _WatchedLock(value, self, f"the lock {path}.{key}")
                put(key, wrapped)

                def undo(key=key, value=value, wrapped=wrapped, put=put, get=get):
                    # put back only what is still the wrapper: the call may
                    # have given the object a new lock of its own
                    if get(key) is wrapped:
                        put(key, value)

                self._saved.append((undo, None, None))
            elif isinstance(value, dict) or _ours(value):
                self._install_locks(value, f"{path}.{key}", depth + 1, seen)

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
                limit = sock.gettimeout()
                if limit is None or limit > 0:
                    waits._holder_waits()
                if waits._watched() and (limit is None or limit > LIMIT):
                    waits._asked(f"a blocking socket.{_name}", limit)
                return _real(sock, *args, **kw)

            self._set(socket.socket, name, watched_call)
