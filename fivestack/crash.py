"""Crash (the Casino's Crash page): Onkey's banana rocket, played with betting credits against the house.

One round for everyone. Bets go down while the rocket is on the pad (`bet()`, one per bettor, a chip of STAKES);
BET_WINDOW_S after the round's first bet it launches, and its multiplier climbs from 1.00x (`multiplier_at()`:
e^(GROWTH x seconds), cut to cents). Somewhere on the way up it crashes. Cash out before that (`cashout()`, or an
auto cash-out set with the bet) and the stake is paid back times the multiplier you got out at; still aboard when it
crashes, the stake is lost. A rocket that makes it to MAX_MULT pays everyone still aboard at that.

The crash point is drawn at launch with `secrets` (`_draw()` → `crash_point()`, which the self-test replaces) and
kept on the server until the rocket has crashed: the chance it reaches a multiplier m is (1 - EDGE) / m, so every
cash-out target is worth the same, the stake less EDGE (3%), and that share of each stake is the expected take the
house ledger records. About 3 rounds in 100 crash on the pad at 1.00x.

The server's clock decides everything: a cash-out is paid at the multiplier when the request arrives, auto cash-outs
are paid at exactly their target by `tick()`, and a timer (`timers`, off under the self-test's clock) crashes the
rocket on time instead of at the casino clock's next quarter second. The page only draws the climb from the launch
time. Rows live in `crash_bets`; `(bettor, request_id)` stops a retry charging twice, and bets still on the pad or
in the air are refunded at start-up and on a season reset (`void_open()`).
"""
import math
import secrets
import threading
import time

from . import house
from .bets import BetError
from .tables import LiveManager, LiveTable, check_ref

STAKES = (5, 10, 25, 50, 100, 250)  # one bet a round, one of these
EDGE = 0.03  # the house's share of every stake, whatever the cash-out
MAX_MULT = 100.0  # the rocket never goes past this: everyone still aboard is paid there
MIN_AUTO = 1.01  # the lowest auto cash-out
GROWTH = 0.12  # the multiplier is e^(GROWTH x seconds in the air): 2x after 5.8 s, 10x after 19 s, 100x after 38 s
BET_WINDOW_S = 5  # how long the pad stays open after the round's first bet
RESULT_S = 5  # how long a crash stays up before the next round
HISTORY = 20  # past crash points the table remembers
DRAW_N = 1_000_000  # tickets in the draw
RULES = [f"Bet while the rocket is on the pad; it launches {BET_WINDOW_S} seconds after the first bet",
         "The multiplier climbs from 1.00x and the rocket can crash at any moment",
         "Cash out before the crash: your stake times the multiplier",
         "Still aboard when it crashes: the stake is lost",
         "A rocket that reaches 100x pays everyone aboard"]


def cents(mult):
    """A multiplier as whole cents (2.5 -> 250), which is how they're compared."""
    return int(round(mult * 100))


def multiplier_at(seconds):
    """The multiplier `seconds` after launch, cut to cents."""
    if seconds <= 0:
        return 1.0
    grown = math.exp(min(GROWTH * seconds, 50.0))
    return min(MAX_MULT, math.floor(grown * 100 + 1e-9) / 100)


def time_to(mult):
    """Seconds after launch at which the multiplier reaches `mult`."""
    return math.log(max(1.0, mult)) / GROWTH


def crash_point(ticket):
    """The crash point for one ticket of the draw (0 to DRAW_N - 1). (1 - EDGE) / u for u uniform on (0, 1], cut to
    cents, at least 1.00 (the rocket never leaves the pad) and at most MAX_MULT."""
    c = int(round((1 - EDGE) * 100 * DRAW_N)) // (ticket + 1)
    return max(100, min(cents(MAX_MULT), c)) / 100


def reach_chance(mult):
    """The chance the rocket gets to `mult` (1.01 to MAX_MULT) before it crashes."""
    return int(round((1 - EDGE) * 100 * DRAW_N)) // cents(mult) / DRAW_N


def check_stake(stake):
    if type(stake) not in (int, float) or stake != int(stake) or int(stake) not in STAKES:
        raise BetError(f"Pick a stake: {', '.join(map(str, STAKES))} credits.")
    return int(stake)


def check_auto(auto):
    """An auto cash-out target: None (cash out by hand), or MIN_AUTO to MAX_MULT in cents."""
    if auto in (None, "", 0):
        return None
    if type(auto) not in (int, float) or auto != auto or not MIN_AUTO - 1e-9 <= auto <= MAX_MULT + 1e-9:
        raise BetError(f"Auto cash-out is between {MIN_AUTO:.2f}x and {MAX_MULT:.0f}x, or leave it empty.")
    return cents(auto) / 100


class Table(LiveTable):
    def __init__(self):
        super().__init__()
        self.round = 0
        self.history = []  # past crash points, newest first
        self.reset_round()

    def reset_round(self):
        self.phase = "betting"   # betting -> flying -> crashed
        self.entries = []        # this round's bets: {"bettor", "row", "stake", "auto", "cashout", "paid"}
        self.deadline = None     # when the rocket launches
        self.started = None      # when it did
        self.crash = None        # the crash point: the server's secret until the rocket has crashed
        self.crash_at = None     # and when it gets there
        self.next_round_at = None


class CrashManager(LiveManager):
    def __init__(self, db, clock=None, timers=None):
        super().__init__(db, clock or time.time)
        self.timers = clock is None if timers is None else timers  # a real timer crashes the rocket on time
        self.table = Table()
        self.table.round = (self.db.query_one("SELECT MAX(round) AS n FROM crash_bets") or {}).get("n") or 0
        with self.lock:
            self.void_open("The server restarted before the round ended")

    # ---- helpers ---------------------------------------------------------------
    def _draw(self):
        """The round's crash point."""
        return crash_point(secrets.randbelow(DRAW_N))

    def _txn(self, fn):
        with self.db.lock:
            try:
                out = fn()
                self.db.conn.commit()
                return out
            except Exception:
                self.db.conn.rollback()
                raise

    def void_open(self, note):
        """Refund every bet still on the pad or in the air and clear the table (start-up, season reset). Under
        self.lock."""
        def run():
            for r in self.db.query("SELECT id, bettor, stake FROM crash_bets WHERE status='playing'"):
                self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance + ?, 2) WHERE name=?", (r["stake"], r["bettor"]))
                self.db.conn.execute("UPDATE crash_bets SET status='void', payout=?, note=?, settled_ts=? WHERE id=?",
                                     (r["stake"], note, self.clock(), r["id"]))
        self._txn(run)
        self.table.reset_round()
        self.changed(self.table)

    def _entry(self, name):
        return next((e for e in self.table.entries if e["bettor"] == name), None)

    # ---- the pad: bets -----------------------------------------------------------------
    def bet(self, name, stake, auto, request_id):
        stake, auto = check_stake(stake), check_auto(auto)
        check_ref(request_id, "bet")
        with self.lock:
            now = self.clock()
            self._advance(now)
            b = self.db.get_bettor(name)
            if not b:
                raise BetError("Sign in as a bettor to play.")
            name, t = b["name"], self.table
            if self.db.query_one("SELECT id FROM crash_bets WHERE bettor=? AND request_id=?", (name, request_id)):
                return self.view(name)  # a retry: that bet is already down
            if t.phase != "betting":
                raise BetError("The rocket has left. Bets open again after this round.")
            if self._entry(name):
                raise BetError("You're already aboard this round.")
            if b["balance"] < stake:
                raise BetError("Not enough credits for that stake.")
            if not t.entries:
                t.round += 1

            def run():
                self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance - ?, 2) WHERE name=?", (stake, name))
                return self.db.conn.execute(
                    "INSERT INTO crash_bets(bettor, round, stake, auto, payout, status, created_ts, request_id) "
                    "VALUES(?,?,?,?,0,'playing',?,?)", (name, t.round, stake, auto, now, request_id)).lastrowid
            row = self._txn(run)
            t.entries.append({"bettor": name, "row": row, "stake": stake, "auto": auto, "cashout": None, "paid": 0})
            t.add_log("bet", name, now, amount=stake)
            if t.deadline is None:
                t.deadline = now + BET_WINDOW_S
            self.changed(t)
            return self.view(name)

    # ---- the flight ----------------------------------------------------------------------
    def _launch(self, t, now):
        t.crash = self._draw()
        t.phase, t.deadline, t.started = "flying", None, now
        t.crash_at = now + time_to(t.crash)
        t.add_log("launch", None, now)
        if self.timers:  # crash on time, not at the casino clock's next quarter second
            timer = threading.Timer(max(0.0, t.crash_at - self.clock()) + 0.01, self._on_timer)
            timer.daemon = True
            timer.start()

    def _on_timer(self):
        try:
            self.tick()
        except Exception as e:  # the casino clock gets to it a moment later
            print(f"[casino] crash timer failed: {e}", flush=True)

    def _cash(self, t, e, mult, now, auto=False):
        """Pay one entry out at `mult` and record it for the house, in one transaction."""
        paid = round(e["stake"] * mult, 2)
        expected = e["stake"] * EDGE

        def run():
            self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance + ?, 2) WHERE name=?", (paid, e["bettor"]))
            self.db.conn.execute("UPDATE crash_bets SET cashout=?, payout=?, status='settled', settled_ts=?, expected=? WHERE id=?",
                                 (mult, paid, now, expected, e["row"]))
            house.record(self.db.conn, "crash", f"bet:{e['row']}", e["bettor"], e["stake"], e["stake"] - paid, expected, now)
        self._txn(run)
        e["cashout"], e["paid"] = mult, paid
        t.add_log("cashout", e["bettor"], now, mult=mult, amount=round(paid - e["stake"], 2), auto=auto)

    def _crash(self, t, now):
        """The rocket is gone: everyone still aboard loses their stake."""
        lost = [e for e in t.entries if e["cashout"] is None]

        def run():
            for e in lost:
                expected = e["stake"] * EDGE
                self.db.conn.execute("UPDATE crash_bets SET payout=0, status='settled', settled_ts=?, expected=? WHERE id=?",
                                     (now, expected, e["row"]))
                house.record(self.db.conn, "crash", f"bet:{e['row']}", e["bettor"], e["stake"], e["stake"], expected, now)
            self.db.conn.execute("UPDATE crash_bets SET crash=? WHERE round=? AND status='settled' AND crash IS NULL", (t.crash, t.round))
        self._txn(run)
        t.phase, t.next_round_at = "crashed", now + RESULT_S
        t.history.insert(0, t.crash)
        del t.history[HISTORY:]
        t.add_log("crash", None, now, mult=t.crash, aboard=len(lost), moon=cents(t.crash) >= cents(MAX_MULT))
        for e in lost:
            t.add_log("lose", e["bettor"], now, amount=-e["stake"], mult=t.crash)

    def _advance(self, now):
        """Move the round on to `now`: launch, pay the auto cash-outs that have been reached, crash, reopen. Returns
        whether anything changed. Under self.lock."""
        t, moved = self.table, False
        if t.phase == "betting" and t.deadline and now >= t.deadline:
            self._launch(t, t.deadline)  # the clock started when the window closed, not when a tick noticed
            moved = True
        if t.phase == "flying":
            over = now >= t.crash_at
            reached = cents(t.crash) if over else min(cents(t.crash), cents(multiplier_at(now - t.started)))
            moon = over and cents(t.crash) >= cents(MAX_MULT)
            for e in t.entries:
                if e["cashout"] is not None:
                    continue
                if e["auto"] and cents(e["auto"]) <= reached:
                    self._cash(t, e, e["auto"], now, auto=True)
                    moved = True
                elif moon:  # it made it all the way: everyone aboard is paid at the top
                    self._cash(t, e, MAX_MULT, now, auto=True)
                    moved = True
            if over:
                self._crash(t, now)
                moved = True
        elif t.phase == "crashed" and t.next_round_at and now >= t.next_round_at:
            t.reset_round()
            moved = True
        return moved

    def tick(self, now=None):
        """The table's clock."""
        with self.lock:
            if self._advance(now or self.clock()):
                self.changed(self.table)

    def cashout(self, name):
        with self.lock:
            now = self.clock()
            moved = self._advance(now)
            b = self.db.get_bettor(name)
            if not b:
                raise BetError("Sign in as a bettor to play.")
            name, t = b["name"], self.table
            e = self._entry(name)
            try:
                if e and e["cashout"] is not None:
                    return self.view(name)  # already out (a second click, or the auto cash-out got there first)
                if t.phase == "crashed" and e:
                    raise BetError(f"Too late: it crashed at {t.crash:.2f}x.")
                if t.phase != "flying" or not e:
                    raise BetError("You're not aboard this round.")
                mult = min(t.crash, multiplier_at(now - t.started))
                self._cash(t, e, mult, now)
                moved = True
                return self.view(name)
            finally:
                if moved:
                    self.changed(t)

    # ---- what the page sees ------------------------------------------------------------
    def _last(self, name):
        r = self.db.query_one("SELECT id, round, stake, auto, cashout, crash, payout, settled_ts FROM crash_bets WHERE bettor=? "
                              "AND status='settled' ORDER BY id DESC LIMIT 1", (name,))
        return {**r, "net": round(r["payout"] - r["stake"], 2)} if r else None

    def season(self, name):
        r = self.db.query_one(
            "SELECT COUNT(*) AS rounds, COALESCE(SUM(stake),0) AS staked, COALESCE(SUM(payout),0) AS returned, "
            "COUNT(cashout) AS cashed, COALESCE(MAX(cashout),0) AS best_mult, COALESCE(MAX(payout - stake),0) AS best "
            "FROM crash_bets WHERE bettor=? AND status='settled' AND season_id IS NULL", (name,))
        return {**r, "net": round(r["returned"] - r["staked"], 2)}

    def view(self, name=None):
        with self.lock:
            t, now = self.table, self.clock()
            crashed = t.phase == "crashed"
            seats = [{"bettor": e["bettor"], "stake": e["stake"], "cashout": e["cashout"], "paid": e["paid"],
                      "lost": crashed and e["cashout"] is None} for e in t.entries]
            me = None
            if name:
                b = self.db.get_bettor(name)
                name = b["name"] if b else name
                e = self._entry(name)
                me = {"name": name, "balance": round(b["balance"], 2) if b else 0,
                      "bet": {"stake": e["stake"], "auto": e["auto"], "cashout": e["cashout"], "paid": e["paid"]} if e else None,
                      "last": self._last(name), "season": self.season(name)}
            return {"phase": t.phase, "round": t.round, "version": t.version, "deadline": t.deadline, "started": t.started,
                    "next_round_at": t.next_round_at, "server_time": now,
                    # The crash point leaves the server only once the rocket has crashed.
                    "crash": t.crash if crashed else None,
                    "history": list(t.history), "seats": seats, "log": list(t.log), "me": me,
                    "stakes": STAKES, "growth": GROWTH, "max_mult": MAX_MULT, "min_auto": MIN_AUTO, "edge": EDGE,
                    "bet_window_s": BET_WINDOW_S, "result_s": RESULT_S, "rules": RULES}
