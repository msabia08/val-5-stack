"""Roulette (the Casino's Roulette page): Onkey's wheel, played with betting credits against the house.

The wheel is a European one, 37 pockets, with the zero turned into the banana pocket. Every bet a real table takes
is on the layout (`BETS`, keyed by what it covers): a straight number, a split, a street (and the two trios with the
banana), a corner (and the first four), a six-line, a column, a dozen, and red / black, odd / even, low / high. They
pay what a real table pays (35, 17, 11, 8, 5, 2 and 1 to 1), with one twist: a straight bet on the banana pays
BANANA_PAYS to 1. At 36 to 1 that bet is exactly fair (no house edge); every other bet carries the single-zero edge,
1 in 37 (2.70%). `edge(key)` is each bet's edge, and a spin's expected take is the sum over its bets, which is what
the house ledger records.

Two tables, like blackjack: a solo table per bettor, where `spin()` takes the bets, draws the number and settles in
one transaction; and one shared table everyone bets on together, where `bet()` puts chips down during the betting
window (BET_WINDOW_S from the round's first bet) and `tick()` spins when it closes, settles every bet in one go, shows
the wheel for SPIN_S ('spinning'), the results for RESULT_S ('done'), and opens the next round. The number is drawn
with `secrets` (`_draw()`, which the self-test replaces) and the page only animates the ball to it. A bettor can put
at most TABLE_MAX credits on a spin, in chips of STAKES. Rows live in `roulette_spins` (one per solo spin, one per
batch of bets at the shared table); `(bettor, request_id)` stops a retry charging twice, and bets still waiting for
a spin are refunded at start-up and on a season reset (`void_open()`).
"""
import json
import secrets
import time

from . import house
from .bets import BetError
from .tables import LiveManager, LiveTable, check_ref

STAKES = (5, 10, 25, 50, 100, 250, 500)  # the chips, the same as blackjack's stakes
TABLE_MAX = 500  # the most one bettor can put on one spin, all bets together
MAX_SPOTS = 40  # bets in one request
POCKETS = 37
BANANA = 0  # the zero is the banana pocket
BANANA_PAYS = 36  # a straight bet on the banana pays this to 1 (a number pays 35): exactly fair at 36
BET_WINDOW_S = 20  # the shared table: how long betting stays open after the round's first bet
SPIN_S = 9  # how long the shared table shows the wheel turning before the results
RESULT_S = 7  # and how long the results stay up before the next round
HISTORY = 20  # past numbers a table remembers
# The wheel's pockets in order, clockwise (a European wheel).
WHEEL = (0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7,
         28, 12, 35, 3, 26)
REDS = frozenset((1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36))
PAYS = {1: 35, 2: 17, 3: 11, 4: 8, 6: 5, 12: 2, 18: 1}  # to 1, by how many numbers a bet covers
RULES = ["One banana pocket and the numbers 1 to 36", "A straight bet on the banana pays 36 to 1; a number pays 35 to 1",
         "Splits 17, streets 11, corners 8, six-lines 5, dozens and columns 2, even-money bets 1 to 1",
         "The banana beats every bet that doesn't cover it"]


def colour(n):
    return "banana" if n == BANANA else "red" if n in REDS else "black"


def _layout():
    """Every bet the table takes: key -> {"numbers", "pays" (to 1), "label", "kind"}."""
    bets = {}

    def add(kind, numbers, label, key=None, pays=None):
        numbers = tuple(sorted(numbers))
        key = key or f"{kind}:{'-'.join(map(str, numbers))}"
        bets[key] = {"numbers": numbers, "pays": PAYS[len(numbers)] if pays is None else pays, "label": label, "kind": kind}

    add("straight", (BANANA,), "Banana", pays=BANANA_PAYS)
    for n in range(1, 37):
        add("straight", (n,), str(n))
        if n % 3:  # a neighbour to the right, in the same row of three
            add("split", (n, n + 1), f"{n}/{n + 1} split")
        if n <= 33:  # and one in the next row
            add("split", (n, n + 3), f"{n}/{n + 3} split")
        if n % 3 and n <= 32:
            add("corner", (n, n + 1, n + 3, n + 4), f"Corner {n}-{n + 4}")
        if n % 3 == 1:
            add("street", (n, n + 1, n + 2), f"Street {n}-{n + 2}")
            if n <= 31:
                add("six", tuple(range(n, n + 6)), f"Line {n}-{n + 5}")
    for n in (1, 2, 3):
        add("split", (BANANA, n), f"Banana/{n} split")
    add("street", (0, 1, 2), "Banana-1-2 trio")
    add("street", (0, 2, 3), "Banana-2-3 trio")
    add("corner", (0, 1, 2, 3), "First four")
    for i, name in enumerate(("1st", "2nd", "3rd")):
        add("dozen", tuple(range(12 * i + 1, 12 * i + 13)), f"{name} dozen", key=f"dozen:{i + 1}")
        add("column", tuple(range(i + 1, 37, 3)), f"Column {i + 1}", key=f"column:{i + 1}")
    add("even", tuple(sorted(REDS)), "Red", key="red")
    add("even", tuple(n for n in range(1, 37) if n not in REDS), "Black", key="black")
    add("even", tuple(range(1, 37, 2)), "Odd", key="odd")
    add("even", tuple(range(2, 37, 2)), "Even", key="even")
    add("even", tuple(range(1, 19)), "1 to 18", key="low")
    add("even", tuple(range(19, 37)), "19 to 36", key="high")
    return bets


BETS = _layout()


def edge(key):
    """The house's edge on a bet: the share of the stake it expects to keep."""
    b = BETS[key]
    return 1 - (b["pays"] + 1) * len(b["numbers"]) / POCKETS


def payout(bets, number):
    """What a set of bets ({key: amount}) pays on `number`, stakes included, and the keys that won."""
    won = [k for k in bets if number in BETS[k]["numbers"]]
    return sum(bets[k] * (BETS[k]["pays"] + 1) for k in won), won


def check_bets(bets):
    """Validate a request's bets: {key: amount}, whole chips of 5 on real spots. Returns them as {key: int}."""
    if isinstance(bets, list):  # [{"key": ..., "amount": ...}] is fine too
        merged = {}
        for b in bets:
            if not isinstance(b, dict):
                raise BetError("Put some chips on the table first.")
            merged[b.get("key")] = merged.get(b.get("key"), 0) + (b.get("amount") if type(b.get("amount")) in (int, float) else 0)
        bets = merged
    if not isinstance(bets, dict) or not bets:
        raise BetError("Put some chips on the table first.")
    if len(bets) > MAX_SPOTS:
        raise BetError(f"That's too many bets for one spin (at most {MAX_SPOTS} spots).")
    out = {}
    for key, amount in bets.items():
        if key not in BETS:
            raise BetError("That bet isn't on the table. Reload the page and try again.")
        if type(amount) not in (int, float) or amount != int(amount) or amount < STAKES[0] or int(amount) % STAKES[0]:
            raise BetError(f"Bets are in chips of {STAKES[0]} credits.")
        out[key] = int(amount)
    if sum(out.values()) > TABLE_MAX:
        raise BetError(f"The table takes up to {TABLE_MAX} credits a spin.")
    return out


class Table(LiveTable):
    def __init__(self, key, shared):
        super().__init__()
        self.key, self.shared = key, shared
        self.round = 0
        self.history = []  # past numbers, newest first
        self.reset_round()

    def reset_round(self):
        self.phase = "betting"   # betting -> spinning -> done (the solo table goes straight from betting to done)
        self.entries = []        # this round's bets at the shared table: {"bettor", "row", "bets", "total"}
        self.deadline = None     # when betting closes
        self.spin_until = None   # when the wheel stops turning
        self.next_round_at = None
        self.result = None       # the number, once drawn
        self.results = {}        # bettor -> {"staked", "paid", "net", "won": [keys]}


class RouletteManager(LiveManager):
    def __init__(self, db, clock=None):
        super().__init__(db, clock or time.time)
        self.tables = {"shared": Table("shared", True)}
        with self.lock:
            self.void_open("The server restarted before the spin")

    # ---- helpers ---------------------------------------------------------------
    def _table(self, name, which):
        if which == "shared":
            return self.tables["shared"]
        if which != "solo":
            raise BetError("Pick the solo table or the shared table.")
        key = "solo:" + name.lower()
        if key not in self.tables:
            self.tables[key] = Table(key, False)
        return self.tables[key]

    def _bettor(self, name):
        b = self.db.get_bettor(name)
        if not b:
            raise BetError("Sign in as a bettor to play.")
        return b

    def _draw(self):
        """The pocket the ball lands in."""
        return secrets.randbelow(POCKETS)

    def _txn(self, fn):
        with self.db.lock:
            try:
                out = fn()
                self.db.conn.commit()
                return out
            except Exception:
                self.db.conn.rollback()
                raise

    def _settle_row(self, row_id, name, bets, number, now):
        """Pay one row's bets on `number` and record it for the house. Inside a transaction. Returns the result."""
        staked = sum(bets.values())
        paid, won = payout(bets, number)
        expected = sum(amount * edge(key) for key, amount in bets.items())
        self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance + ?, 2) WHERE name=?", (paid, name))
        self.db.conn.execute("UPDATE roulette_spins SET number=?, payout=?, status='settled', settled_ts=?, expected=? WHERE id=?",
                             (number, paid, now, expected, row_id))
        house.record(self.db.conn, "roulette", f"spin:{row_id}", name, staked, staked - paid, expected, now)
        return {"staked": staked, "paid": paid, "net": paid - staked, "won": won}

    def void_open(self, note):
        """Refund every bet still waiting for a spin and clear the tables (start-up, season reset). Under self.lock."""
        def run():
            for r in self.db.query("SELECT id, bettor, stake FROM roulette_spins WHERE status='playing'"):
                self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance + ?, 2) WHERE name=?", (r["stake"], r["bettor"]))
                self.db.conn.execute("UPDATE roulette_spins SET status='void', payout=?, note=?, settled_ts=? WHERE id=?",
                                     (r["stake"], note, self.clock(), r["id"]))
        self._txn(run)
        for t in self.tables.values():
            t.reset_round()
            self.changed(t)

    def _take(self, name, t, bets, request_id, now):
        """Charge the bets and insert their row ('playing'). Inside a transaction. Returns the row id."""
        total = sum(bets.values())
        self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance - ?, 2) WHERE name=?", (total, name))
        cur = self.db.conn.execute(
            "INSERT INTO roulette_spins(bettor, tbl, round, bets, stake, payout, status, created_ts, request_id) "
            "VALUES(?,?,?,?,?,0,'playing',?,?)",
            (name, "shared" if t.shared else "solo", t.round, json.dumps(bets), total, now, request_id))
        return cur.lastrowid

    # ---- the solo table: bets and spin in one go ---------------------------------------
    def spin(self, name, bets, request_id):
        bets = check_bets(bets)
        check_ref(request_id, "spin")
        with self.lock:
            now = self.clock()
            b = self._bettor(name)
            name = b["name"]
            t = self._table(name, "solo")
            if self.db.query_one("SELECT id FROM roulette_spins WHERE bettor=? AND request_id=?", (name, request_id)):
                return self.view(name, "solo")  # a retry: that spin already happened
            total = sum(bets.values())
            if b["balance"] < total:
                raise BetError("Not enough credits for those bets. Take some chips off.")
            t.reset_round()
            t.round += 1
            number = self._draw()

            def run():
                row = self._take(name, t, bets, request_id, now)
                return self._settle_row(row, name, bets, number, now)
            res = self._txn(run)
            t.phase, t.result, t.results = "done", number, {name: res}
            t.history.insert(0, number)
            del t.history[HISTORY:]
            self._log_result(t, name, res, number, now)
            self.changed(t)
            return self.view(name, "solo")

    def _log_result(self, t, name, res, number, now):
        kind = "banana" if number == BANANA and res["net"] > 0 else "win" if res["net"] > 0 else "push" if res["net"] == 0 else "lose"
        t.add_log(kind, name, now, amount=res["net"], staked=res["staked"], number=number,
                  straight=any(BETS[k]["kind"] == "straight" for k in res["won"]))

    # ---- the shared table: bet, then the clock spins -----------------------------------
    def bet(self, name, bets, request_id):
        bets = check_bets(bets)
        check_ref(request_id, "bet")
        with self.lock:
            now = self.clock()
            b = self._bettor(name)
            name = b["name"]
            t = self.tables["shared"]
            if self.db.query_one("SELECT id FROM roulette_spins WHERE bettor=? AND request_id=?", (name, request_id)):
                return self.view(name, "shared")  # a retry: those chips are already down
            if t.phase != "betting":
                raise BetError("No more bets. They open again after this spin.")
            total = sum(bets.values())
            down = sum(e["total"] for e in t.entries if e["bettor"] == name)
            if down + total > TABLE_MAX:
                raise BetError(f"The table takes up to {TABLE_MAX} credits a spin, and you have {down} down already.")
            if b["balance"] < total:
                raise BetError("Not enough credits for those bets. Take some chips off.")
            if not t.entries:
                t.round += 1
            row = self._txn(lambda: self._take(name, t, bets, request_id, now))
            t.entries.append({"bettor": name, "row": row, "bets": bets, "total": total})
            t.add_log("bet", name, now, amount=total)
            if t.deadline is None:
                t.deadline = now + BET_WINDOW_S
            self.changed(t)
            return self.view(name, "shared")

    def _spin_shared(self, t, now):
        number = self._draw()

        def run():
            out = {}
            for e in t.entries:
                res = self._settle_row(e["row"], e["bettor"], e["bets"], number, now)
                mine = out.setdefault(e["bettor"], {"staked": 0, "paid": 0, "net": 0, "won": []})
                for k in ("staked", "paid", "net"):
                    mine[k] += res[k]
                mine["won"] += [k for k in res["won"] if k not in mine["won"]]
            return out
        t.results = self._txn(run)
        t.phase, t.result, t.deadline, t.spin_until = "spinning", number, None, now + SPIN_S
        t.add_log("spin", None, now)

    def tick(self, now=None):
        """The shared table's clock: close the betting window and spin, show the results, open the next round."""
        with self.lock:
            now = now or self.clock()
            t = self.tables["shared"]
            if t.phase == "betting" and t.deadline and now >= t.deadline:
                self._spin_shared(t, now)
            elif t.phase == "spinning" and now >= t.spin_until:
                t.phase, t.next_round_at = "done", now + RESULT_S
                t.history.insert(0, t.result)
                del t.history[HISTORY:]
                t.add_log("number", None, now, number=t.result)
                for name, res in t.results.items():  # only now, so the log can't give the number away early
                    self._log_result(t, name, res, t.result, now)
            elif t.phase == "done" and t.next_round_at and now >= t.next_round_at:
                t.reset_round()
            else:
                return
            self.changed(t)

    # ---- what the page sees ------------------------------------------------------------
    @staticmethod
    def layout():
        """The table itself, the same for everyone: what the page needs to draw the wheel and the betting layout."""
        return {"stakes": STAKES, "table_max": TABLE_MAX, "wheel": WHEEL, "reds": sorted(REDS), "banana": BANANA,
                "banana_pays": BANANA_PAYS, "rules": RULES, "bet_window_s": BET_WINDOW_S, "spin_s": SPIN_S,
                "edge": round(1 / POCKETS, 4),
                "bets": {k: {"numbers": b["numbers"], "pays": b["pays"], "label": b["label"], "kind": b["kind"]} for k, b in BETS.items()}}

    def _last(self, name, which):
        """The bettor's latest settled spin at this kind of table, from the database (so a retry or a reload finds it)."""
        r = self.db.query_one("SELECT id, round, bets, number, stake, payout, settled_ts FROM roulette_spins WHERE bettor=? AND tbl=? "
                              "AND status='settled' ORDER BY id DESC LIMIT 1", (name, which))
        if not r:
            return None
        bets = json.loads(r["bets"])
        return {"id": r["id"], "round": r["round"], "number": r["number"], "colour": colour(r["number"]), "bets": bets,
                "staked": r["stake"], "paid": r["payout"], "net": round(r["payout"] - r["stake"], 2),
                "won": payout(bets, r["number"])[1], "settled_ts": r["settled_ts"]}

    def view(self, name, which):
        with self.lock:
            t = self._table(name, which) if name else self.tables["shared"]
            # While the wheel turns the page gets the number (it has to animate to it) but not who won what.
            shown = t.result if t.phase in ("spinning", "done") else None
            entries = {}
            for e in t.entries:
                mine = entries.setdefault(e["bettor"], {"bettor": e["bettor"], "bets": {}, "total": 0})
                mine["total"] += e["total"]
                for k, amount in e["bets"].items():
                    mine["bets"][k] = mine["bets"].get(k, 0) + amount
            seats = []
            for e in entries.values():
                res = t.results.get(e["bettor"]) if t.phase == "done" else None
                seats.append({**e, "net": res["net"] if res else None, "won": res["won"] if res else []})
            me = None
            if name:
                b = self.db.get_bettor(name)
                mine = entries.get(name)
                me = {"name": name, "balance": round(b["balance"], 2) if b else 0,
                      "down": mine["total"] if mine else 0, "bets": mine["bets"] if mine else {},
                      "last": self._last(name, "shared" if t.shared else "solo"), "season": self.season(name)}
            return {"table": "shared" if t.shared else "solo", "phase": t.phase, "round": t.round, "version": t.version,
                    "deadline": t.deadline, "spin_until": t.spin_until, "next_round_at": t.next_round_at,
                    "server_time": self.clock(), "result": shown, "colour": colour(shown) if shown is not None else None,
                    "history": [{"number": n, "colour": colour(n)} for n in t.history],
                    "seats": seats, "log": list(t.log), "me": me, **self.layout()}

    def season(self, name):
        r = self.db.query_one(
            "SELECT COUNT(*) AS spins, COALESCE(SUM(stake),0) AS staked, COALESCE(SUM(payout),0) AS returned, "
            "COUNT(CASE WHEN payout > stake THEN 1 END) AS wins, COALESCE(MAX(payout - stake),0) AS best, "
            "COUNT(CASE WHEN number = 0 THEN 1 END) AS bananas "
            "FROM roulette_spins WHERE bettor=? AND status='settled' AND season_id IS NULL", (name,))
        return {**r, "net": round(r["returned"] - r["staked"], 2)}
