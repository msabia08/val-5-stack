"""Instant virtual-credit slots. Outcomes, payment and retry protection live on the server."""
import json
import secrets
import time

from . import house
from .bets import BetError

STAKES = (5, 10, 25, 50, 100, 250, 500)
# Saved spins store these indexes, so keep the order and add new symbols at the end; a symbol's rank comes from its
# multiplier in "triples". "img" replaces the emoji on the page ("glow" adds a golden glow to it). A "secret" symbol is
# left out of the pay table and off the page's reel strips (it only shows where a reel stops on it), and its jackpot is
# a bonus on top of the house edge: rtp() leaves it out unless asked.
SYMBOLS = [
    {"key": "banana", "icon": "🍌", "name": "Banana"},
    {"key": "cherry", "icon": "🍒", "name": "Cherry"},
    {"key": "bell", "icon": "🔔", "name": "Bell"},
    {"key": "diamond", "icon": "💎", "name": "Diamond"},
    {"key": "spike", "icon": "💣", "name": "Spike"},
    {"key": "monkey", "icon": "🐒", "name": "Onkey", "img": "/assets/onkey-logo.png"},
    {"key": "golden", "icon": "🌟", "name": "Golden Onkey", "img": "/assets/onkey-logo.png", "glow": True, "secret": True},
]
MACHINES = {
    # A spin picks its line first (draw_spin): each symbol's triple has "lines" tickets out of "tickets", so the
    # bigger the payout the rarer the line: cherry 1 in 20 (3x), bell 1 in 25 (4x), spike 1 in 50 (8x), diamond 1 in
    # 125 (20x), banana 1 in 250 (40x) and Onkey 1 in 500 (80x), a win about every 8 spins. Each line pays back 15-16%
    # of stakes, 95.00% in all: a 5% house edge. The secret Golden Onkey, 1 in 10,000 (200x), adds 2% on top (97% in
    # all). A losing spin is then shown with each reel drawn from "show" (cherries most, Onkey least, the Golden Onkey
    # on 1 reel in 421, so it's rarely seen unless it hits); the page builds its reel strips from it too.
    "jackpot": {"name": "Slots", "icon": "🎰", "desc": "Match three symbols. Win up to 200× your stake.",
                "tickets": 100_000, "lines": [400, 5000, 4000, 800, 2000, 200, 10],
                "triples": [40, 3, 4, 20, 8, 80, 200], "show": [50, 100, 90, 60, 80, 40, 1]},
}


def multiplier(machine, reels):
    """Only three of a kind pays."""
    return machine["triples"][reels[0]] if reels[0] == reels[1] == reels[2] else 0


def draw_reel(weights):
    """One reel's symbol, drawn in proportion to its weight."""
    ticket = secrets.randbelow(sum(weights))
    for i, w in enumerate(weights):
        if ticket < w:
            return i
        ticket -= w
    raise AssertionError("unreachable")


def draw_spin(machine):
    """A spin's three reels. The line comes first, each symbol's triple with its chance in chances(); otherwise a
    losing combination, each reel drawn from the display weights "show" and redrawn if all three match."""
    ticket = secrets.randbelow(machine["tickets"])
    for i, n in enumerate(machine["lines"]):
        if ticket < n:
            return [i, i, i]
        ticket -= n
    while True:
        reels = [draw_reel(machine["show"]) for _ in range(3)]
        if not reels[0] == reels[1] == reels[2]:
            return reels


def chances(machine):
    """Each symbol's chance of landing three in a row."""
    return [n / machine["tickets"] for n in machine["lines"]]


def rtp(machine, secret=False):
    """The expected return per credit staked (only triples pay): the house edge's side, without the secret jackpot
    unless secret=True."""
    return sum(m * c for m, c, s in zip(machine["triples"], chances(machine), SYMBOLS) if secret or not s.get("secret"))


class SlotManager:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def public(row):
        return {**row, "reels": json.loads(row["reels"]), "net": row["payout"] - row["stake"]}

    def spin(self, name, machine, stake, request_id):
        machine = "jackpot" if machine is None else machine
        if not isinstance(machine, str):
            raise BetError("This machine is no longer available. Reload the page.")
        if type(stake) not in (int, float) or stake not in STAKES:
            raise BetError(f"Choose a stake of {', '.join(map(str, STAKES[:-1]))} or {STAKES[-1]} credits.")
        if not isinstance(request_id, str) or not 16 <= len(request_id) <= 80 or not all(
                c.isascii() and (c.isalnum() or c in "-_") for c in request_id):
            raise BetError("Invalid spin reference. Reload the page and try again.")
        with self.db.lock:
            bettor = self.db.get_bettor(name)
            if not bettor:
                raise BetError("Sign in as a bettor to spin.")
            name = bettor["name"]
            old = self.db.query_one("SELECT * FROM slot_spins WHERE bettor=? AND request_id=?", (name, request_id))
            if old:
                if old["machine"] != machine or old["stake"] != stake:
                    raise BetError("That spin reference has already been used.")
                return {"spin": self.public(old), "balance": round(bettor["balance"], 2)}
            # Old results remain recoverable, but retired machines cannot take new stakes.
            if machine not in MACHINES:
                raise BetError("This machine is no longer available. Reload the page.")
            if bettor["balance"] < stake:
                raise BetError("Not enough credits for that spin. Choose a smaller stake.")
            m = MACHINES[machine]
            reels = draw_spin(m)
            mult = multiplier(m, reels)
            payout = stake * mult
            try:
                self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance + ?, 2) WHERE name=?", (payout - stake, name))
                # rtp is the return this spin was played at (without the secret jackpot), so the house's expected take
                # stays right if the odds change.
                cur = self.db.conn.execute(
                    "INSERT INTO slot_spins(bettor, machine, stake, reels, multiplier, payout, created_ts, request_id, rtp) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (name, machine, stake, json.dumps(reels), mult, payout, time.time(), request_id, rtp(m)))
                house.record(self.db.conn, "slots", f"spin:{cur.lastrowid}", name, stake, stake - payout, stake * (1 - rtp(m)))
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
            row = self.db.query_one("SELECT * FROM slot_spins WHERE id=?", (cur.lastrowid,))
            return {"spin": self.public(row), "balance": round(self.db.get_bettor(name)["balance"], 2)}

    def house(self):
        """What the house has taken from slots, every season, since spins recorded their return (rtp): the estimate
        (each stake times the edge it was played at, which leaves out the secret jackpot), what actually happened
        (stakes minus every payout), and the secret jackpots paid, which the edge doesn't cover."""
        r = self.db.query_one(
            "SELECT COUNT(*) AS spins, MIN(created_ts) AS since, COALESCE(SUM(stake),0) AS staked, "
            "COALESCE(SUM(payout),0) AS paid, COALESCE(SUM(stake * (1 - rtp)),0) AS expected "
            "FROM slot_spins WHERE rtp IS NOT NULL")
        secret = {i for i, s in enumerate(SYMBOLS) if s.get("secret")}
        jackpots = sum(row["payout"] for row in self.db.query(
            "SELECT reels, payout FROM slot_spins WHERE rtp IS NOT NULL AND payout > 0")
            if json.loads(row["reels"])[0] in secret)
        return {"since": r["since"], "spins": r["spins"], "staked": r["staked"], "paid": r["paid"],
                "secret_paid": jackpots, "expected_take": round(r["expected"], 2),
                "actual_take": round(r["staked"] - r["paid"], 2)}

    def big_wins(self, limit=5):
        """This season's biggest wins by anyone, biggest payout first (the earlier one first on a tie)."""
        return [{k: r[k] for k in ("bettor", "stake", "payout", "multiplier", "created_ts", "reels", "net")}
                for r in map(self.public, self.db.query(
                    "SELECT * FROM slot_spins WHERE season_id IS NULL AND payout > stake ORDER BY payout DESC, id LIMIT ?",
                    (limit,)))]

    def lines(self, name):
        """How often a bettor has hit each symbol's triple, every season, since spins recorded their return."""
        rows = self.db.query("SELECT reels, created_ts FROM slot_spins WHERE bettor=? AND rtp IS NOT NULL", (name,))
        hits = [0] * len(SYMBOLS)
        for r in rows:
            reels = json.loads(r["reels"])
            if reels[0] == reels[1] == reels[2] and reels[0] < len(SYMBOLS):
                hits[reels[0]] += 1
        return {"spins": len(rows), "since": min((r["created_ts"] for r in rows), default=None), "hits": hits}

    def summary(self, me=None):
        machines = [{"key": key, **m, "rtp": round(rtp(m) * 100, 2), "rtp_with_secret": round(rtp(m, True) * 100, 2),
                     "chances": chances(m)}
                    for key, m in MACHINES.items()]
        out = {"machines": machines, "symbols": SYMBOLS, "stakes": STAKES, "me": None, "history": [], "lines": None,
               "house": self.house(), "big_wins": self.big_wins()}
        if me:
            with self.db.lock:
                name = me["name"]
                totals = self.db.query_one(
                    "SELECT COUNT(*) AS spins, COALESCE(SUM(stake),0) AS staked, COALESCE(SUM(payout),0) AS returned, "
                    "COUNT(CASE WHEN payout > stake THEN 1 END) AS wins FROM slot_spins WHERE bettor=? AND season_id IS NULL",
                    (name,))
                best = self.db.query_one(
                    "SELECT * FROM slot_spins WHERE bettor=? AND season_id IS NULL AND payout > stake "
                    "ORDER BY payout DESC, id DESC LIMIT 1", (name,))
                # A win pays back more than the stake. Spins since the last one this season (all of them if none).
                dry = self.db.query_one(
                    "SELECT COUNT(*) AS n FROM slot_spins WHERE bettor=? AND season_id IS NULL AND id > COALESCE("
                    "(SELECT MAX(id) FROM slot_spins WHERE bettor=? AND season_id IS NULL AND payout > stake), 0)",
                    (name, name))["n"]
                out["me"] = {"name": name, **totals, "net": totals["returned"] - totals["staked"],
                             "best": self.public(best) if best else None, "since_win": dry}
                out["history"] = [self.public(row) for row in self.db.query(
                    "SELECT * FROM slot_spins WHERE bettor=? AND season_id IS NULL ORDER BY id DESC LIMIT 10", (name,))]
                out["lines"] = self.lines(name)
        return out
