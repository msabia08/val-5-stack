"""What the live casino tables (blackjack.py, poker.py) share: a lock, a version number that long-polling waits on, the
action log Onkey's quips come from, and request-reference checks.

Every change to a table bumps its version and wakes the waiting requests (`changed()`), so `GET /api/poker?since=N`
answers as soon as anything happens, or after `timeout` seconds with nothing new. A ticker thread started by App
calls each manager's `tick()` to run the clocks (turn timers, betting windows, pauses between hands).
"""
import threading
import time

from .bets import BetError

LOG_KEEP = 40  # log entries a table keeps for the page


def check_ref(ref, what="request"):
    """A client-made retry reference: 16-80 ASCII letters, digits, hyphens or underscores."""
    if not isinstance(ref, str) or not 16 <= len(ref) <= 80 or not all(
            c.isascii() and (c.isalnum() or c in "-_") for c in ref):
        raise BetError(f"Invalid {what} reference. Reload the page and try again.")
    return ref


class LiveTable:
    """A table's version and action log. Subclasses keep their own game state."""

    def __init__(self):
        self.version = 0
        self.log = []
        self._log_id = 0

    def add_log(self, kind, bettor=None, now=None, **extra):
        self._log_id += 1
        self.log.append({"id": self._log_id, "ts": now or time.time(), "kind": kind, "bettor": bettor, **extra})
        del self.log[:-LOG_KEEP]


class LiveManager:
    """The lock and wake-up condition one manager's tables share. Lock order: a manager's lock first, then db.lock."""

    def __init__(self, db, clock=time.time):
        self.db = db
        self.clock = clock
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)

    def changed(self, table):
        table.version += 1
        self.cond.notify_all()

    def wait(self, table, since, timeout):
        """Block until `table` moves past version `since`, or `timeout` seconds pass."""
        with self.cond:
            self.cond.wait_for(lambda: table.version > since, timeout)
