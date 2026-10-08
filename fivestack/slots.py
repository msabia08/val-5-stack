"""Instant virtual-credit slots. Outcomes, payment and retry protection live on the server."""
import json
import secrets
import time

from . import house
from .bets import BetError
from .wheel import wheel_day

STAKES = (5, 10, 25, 50, 100, 250)
# Daily spins: DAILY_SPINS free spins a day (the Pacific day, like the daily wheel) at DAILY_STAKE, the stake given by
# the house (house_payouts kind `slots_daily`, free like the wheel's credits), so the machine's return is untouched.
DAILY_SPINS = 3
DAILY_STAKE = 100
# Hold: a losing spin that shows a pair on the line is offered a hold HOLD_OFFER of the time (1 in 3). Taking it costs
# the spin's stake again, keeps the pair and spins the third reel alone: it lands the pair's symbol, paying its line,
# with chance HOLD_WIN / the line's multiplier, so a hold returns exactly HOLD_WIN (95%, the machine's own return)
# whatever the symbol, and the house edge is the same 5% on a hold as on a spin. No Golden Onkey on a hold.
HOLD_OFFER = (1, 3)
HOLD_WIN = (19, 20)
HOLD_RTP = HOLD_WIN[0] / HOLD_WIN[1]
# Saved spins store these indexes, so keep the order and add new symbols at the end; a symbol's rank comes from its
# multiplier in "triples". "img" replaces the emoji on the page ("glow" adds a golden glow to it). A "secret" symbol is
# left out of the pay table and off the page's reel strips (it only shows where a reel stops on it). It is wild (it
# fills any gap in a line) and pays on its own when spotted (payouts()); everything it pays is a bonus on top of the
# house edge: rtp() leaves it out unless asked.
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
    # A spin picks its outcome first (draw_spin), each with its tickets out of "tickets" (outcomes()). The six regular
    # lines, by "lines": the bigger the payout the rarer the line: cherry 1 in 20 (3x), bell 1 in 25 (4x), spike 1 in
    # 50 (8x), diamond 1 in 125 (20x), banana 1 in 250 (40x) and Onkey 1 in 500 (80x). Each pays back 15-16% of
    # stakes, 95.00% in all: a 5% house edge. The secret Golden Onkey's outcomes come on top (rtp(m, True)), and one
    # spin in 272 shows one: three of them, 1 in 20,000 (100x, "lines"' last entry, the top prize); a lone one, "spot1"
    # tickets ("spotted" pays 2x the stake); a pair it finishes, "wild1" tickets per symbol, and a symbol with two of
    # them, "wild2" per symbol: filling in a line, one Golden Onkey doubles it and two triple it ("wild_factors"), and
    # no win with a Golden Onkey pays more than "golden_cap" (100x, the jackpot), so the credit economy can take a hit
    # at the biggest stake. A win about every 8 spins. Every other spin loses,
    # and is shown with each reel drawn from "show" without the Golden Onkey (cherries most, Onkey least); the page
    # builds its reel strips from "show" too.
    "jackpot": {"name": "Slots", "icon": "🎰", "desc": "Match three symbols. Win up to 100× your stake.",
                "tickets": 1_000_000, "lines": [4000, 50000, 40000, 8000, 20000, 2000, 50],
                "triples": [40, 3, 4, 20, 8, 80, 100], "show": [50, 100, 90, 60, 80, 40, 1],
                "spotted": 2, "spot1": 3000, "wild_factors": [1, 2, 3], "golden_cap": 100,
                "wild1": [20, 250, 200, 40, 100, 10, 0], "wild2": [2, 2, 2, 2, 2, 2, 0]},
}
WILD = next(i for i, s in enumerate(SYMBOLS) if s.get("secret"))


def payouts(machine, reels):
    """What a spin's reels pay, as parts that add up. A line ({"kind": "line", "symbol", "mult", "wild": how many
    Golden Onkeys filled it in}) when the symbols that aren't Golden Onkeys all match, and then what the Golden Onkeys
    add ("wild": {"count", "factor": x2 for one, x3 for two, "mult": what that adds, "capped": true when the line
    times the factor went over "golden_cap" and was cut to it}). A lone Golden Onkey with no line is "spotted"
    ({"count": 1, "mult": 2}, on the stake). Three Golden Onkeys are their own 100x line, the top prize."""
    golden = reels.count(WILD)
    if golden == 3:
        return [{"kind": "line", "symbol": WILD, "mult": machine["triples"][WILD], "wild": 0}]
    rest = [r for r in reels if r != WILD]
    if len(set(rest)) == 1:
        base = machine["triples"][rest[0]]
        parts = [{"kind": "line", "symbol": rest[0], "mult": base, "wild": golden}]
        if golden:
            factor = machine["wild_factors"][golden]
            total = min(base * factor, machine.get("golden_cap", base * factor))
            parts.append({"kind": "wild", "count": golden, "factor": factor, "mult": total - base,
                          "capped": total < base * factor})
        return parts
    if golden and machine.get("spotted"):
        return [{"kind": "spotted", "count": golden, "mult": machine["spotted"]}]
    return []


def multiplier(machine, reels):
    """The whole spin's multiplier: every part payouts() finds."""
    return sum(p["mult"] for p in payouts(machine, reels))


def hold_pair(reels):
    """(the odd reel's index, the pair's symbol) when exactly two of three reels match and none is a Golden Onkey,
    else None."""
    if len(reels) != 3 or WILD in reels or len(set(reels)) != 2:
        return None
    odd = next(i for i, r in enumerate(reels) if reels.count(r) == 1)
    return odd, reels[(odd + 1) % 3]


def offers_hold():
    """Whether a losing pair is offered a hold: HOLD_OFFER of the time."""
    return secrets.randbelow(HOLD_OFFER[1]) < HOLD_OFFER[0]


def hold_chance(mult):
    """The chance a hold lands a line paying `mult`: HOLD_WIN / mult."""
    return HOLD_WIN[0] / (HOLD_WIN[1] * mult)


def hold_wins(mult):
    """Draw a hold on a line paying `mult`: True with chance hold_chance(mult), exactly."""
    return secrets.randbelow(HOLD_WIN[1] * mult) < HOLD_WIN[0]


def draw_reel(weights):
    """One reel's symbol, drawn in proportion to its weight."""
    ticket = secrets.randbelow(sum(weights))
    for i, w in enumerate(weights):
        if ticket < w:
            return i
        ticket -= w
    raise AssertionError("unreachable")


def outcomes(machine):
    """Every outcome draw_spin() picks from, in ticket order, as (kind, symbol, tickets): "line" (three of a kind),
    "wild1" (a pair and a Golden Onkey), "wild2" (one symbol and two Golden Onkeys) or "spot1" (a lone Golden Onkey)."""
    out = [("line", i, n) for i, n in enumerate(machine["lines"])]
    out += [("wild1", i, n) for i, n in enumerate(machine.get("wild1", [])) if n]
    out += [("wild2", i, n) for i, n in enumerate(machine.get("wild2", [])) if n]
    if machine.get("spot1"):
        out.append(("spot1", WILD, machine["spot1"]))
    return out


def example(kind, symbol):
    """One set of reels for an outcome, for pricing it (where the Golden Onkeys sit doesn't change the pay)."""
    return {"line": [symbol] * 3, "wild1": [symbol, symbol, WILD], "wild2": [symbol, WILD, WILD], "spot1": [WILD, 0, 1]}[kind]


def draw_spin(machine):
    """A spin's three reels. The outcome comes first, with its chance from outcomes(), its Golden Onkeys on random
    reels. Otherwise a loss, each reel drawn from the display weights "show" without the Golden Onkey and redrawn if
    all three match; a lone Golden Onkey's other two reels are drawn the same way, redrawn if they match."""
    plain = [0 if s.get("secret") else w for w, s in zip(machine["show"], SYMBOLS)]
    ticket = secrets.randbelow(machine["tickets"])
    for kind, i, n in outcomes(machine):
        if ticket >= n:
            ticket -= n
            continue
        if kind == "line":
            return [i, i, i]
        spot = secrets.randbelow(3)
        if kind == "wild1":
            return [WILD if k == spot else i for k in range(3)]
        if kind == "wild2":
            return [i if k == spot else WILD for k in range(3)]
        while True:  # spot1
            pair = [draw_reel(plain), draw_reel(plain)]
            if pair[0] != pair[1]:
                pair.insert(spot, WILD)
                return pair
    while True:
        reels = [draw_reel(plain) for _ in range(3)]
        if not reels[0] == reels[1] == reels[2]:
            return reels


def chances(machine):
    """Each symbol's chance of landing three of itself in a row (Golden Onkeys filling in aren't counted)."""
    return [n / machine["tickets"] for n in machine["lines"]]


def win_chance(machine):
    """The chance a spin pays anything."""
    return sum(n for _, _, n in outcomes(machine)) / machine["tickets"]


def rtp(machine, secret=False):
    """The expected return per credit staked: the house edge's side (the regular lines), plus everything the secret
    Golden Onkey pays only when secret=True."""
    total = 0
    for kind, i, n in outcomes(machine):
        if kind == "line" and not SYMBOLS[i].get("secret"):
            total += machine["triples"][i] * n
        elif secret:
            total += multiplier(machine, example(kind, i)) * n
    return total / machine["tickets"]


def boosted(machine, factor):
    """A copy of a machine with every Golden Onkey outcome `factor` times as likely and the regular lines as they are,
    for testing in demo mode (DEMO_GOLDEN_BOOST): it pays far more than it takes, so never on the real site."""
    out = {**machine, "lines": [n * factor if SYMBOLS[i].get("secret") else n for i, n in enumerate(machine["lines"])]}
    for key in ("wild1", "wild2"):
        out[key] = [n * factor for n in machine.get(key, [])]
    out["spot1"] = machine.get("spot1", 0) * factor
    assert sum(n for _, _, n in outcomes(out)) <= out["tickets"], "boost too big for the machine's tickets"
    return out


# Demo mode (app.py) makes the Golden Onkey this many times as likely: about 1 spin in 14 instead of 1 in 272.
DEMO_GOLDEN_BOOST = 20


class SlotManager:
    def __init__(self, db, golden_boost=1, giver=None):
        self.db = db
        self.giver = giver  # the HouseManager that gives the daily spins' stakes; no daily spins without one
        self.machines = {k: boosted(m, golden_boost) if golden_boost != 1 else m for k, m in MACHINES.items()}

    @staticmethod
    def public(row):
        reels = json.loads(row["reels"])
        out = {**row, "reels": reels, "net": row["payout"] - row["stake"], "parts": SlotManager.parts(row, reels)}
        # A spin offered a hold says what it is: the reel that spins again, the pair's symbol, its line and the chance.
        m, pair = MACHINES.get(row["machine"]), hold_pair(reels)
        out["hold"] = None
        if row.get("offer") and m and pair:
            mult = m["triples"][pair[1]]
            out["hold"] = {"reel": pair[0], "symbol": pair[1], "mult": mult, "chance": hold_chance(mult)}
        return out

    @staticmethod
    def parts(row, reels):
        """What a stored spin paid, part by part (payouts()). A spin from before Golden Onkeys paid on their own
        keeps what it recorded: a three of a kind, or nothing."""
        m = MACHINES.get(row["machine"])
        if m and len(reels) == 3 and all(0 <= r < len(SYMBOLS) for r in reels):
            parts = payouts(m, reels)
            if sum(p["mult"] for p in parts) == row["multiplier"]:
                return parts
        if row["multiplier"] and reels and all(0 <= r < len(SYMBOLS) for r in reels):
            rest = [r for r in reels if r != WILD]
            symbol = rest[0] if rest and len(set(rest)) == 1 else reels[0]
            return [{"kind": "line", "symbol": symbol, "mult": row["multiplier"], "wild": 0}]
        return []

    def daily(self, name, now=None):
        """A bettor's daily spins today: how many are left, of DAILY_SPINS, at DAILY_STAKE (None without a giver)."""
        if not self.giver:
            return None
        used = self.db.query_one("SELECT COUNT(*) AS n FROM house_payouts WHERE kind='slots_daily' AND ref LIKE ?",
                                 (f"slots-daily:{name.lower()}:{wheel_day(now or time.time())}:%",))["n"]
        return {"left": max(0, DAILY_SPINS - used), "total": DAILY_SPINS, "stake": DAILY_STAKE}

    @staticmethod
    def check_ref(request_id):
        if not isinstance(request_id, str) or not 16 <= len(request_id) <= 80 or not all(
                c.isascii() and (c.isalnum() or c in "-_") for c in request_id):
            raise BetError("Invalid spin reference. Reload the page and try again.")

    def hold(self, name, spin_id, request_id):
        """Hold the pair of a spin that was offered it and spin its third reel again, for the same stake. Only the
        bettor's latest spin can be held, once. The hold is a slot_spins row of its own (`held` = the spin's id)."""
        self.check_ref(request_id)
        if type(spin_id) is not int:
            raise BetError("That hold has passed. Spin again.")
        with self.db.lock:
            bettor = self.db.get_bettor(name)
            if not bettor:
                raise BetError("Sign in as a bettor to spin.")
            name = bettor["name"]
            old = self.db.query_one("SELECT * FROM slot_spins WHERE bettor=? AND request_id=?", (name, request_id))
            if old:
                if old["held"] != spin_id:
                    raise BetError("That spin reference has already been used.")
                return {"spin": self.public(old), "balance": round(bettor["balance"], 2), "daily": self.daily(name)}
            base = self.db.query_one("SELECT * FROM slot_spins WHERE id=? AND bettor=? AND season_id IS NULL", (spin_id, name))
            pair = hold_pair(json.loads(base["reels"])) if base and base["offer"] and base["machine"] in self.machines else None
            latest = self.db.query_one("SELECT MAX(id) AS id FROM slot_spins WHERE bettor=?", (name,))["id"]
            if not pair or latest != spin_id:  # a later spin (or a hold, which is one) closes the offer
                raise BetError("That hold has passed. Spin again.")
            stake = base["stake"]
            if bettor["balance"] < stake:
                raise BetError("Not enough credits to hold.")
            m = self.machines[base["machine"]]
            odd, symbol = pair
            reels, mult = json.loads(base["reels"]), m["triples"][symbol]
            if hold_wins(mult):
                reels[odd] = symbol
            else:  # anything but the pair's symbol or a Golden Onkey, which would pay
                reels[odd], mult = draw_reel([0 if i in (symbol, WILD) else w for i, w in enumerate(m["show"])]), 0
            payout = stake * mult
            try:
                self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance + ?, 2) WHERE name=?", (payout - stake, name))
                cur = self.db.conn.execute(
                    "INSERT INTO slot_spins(bettor, machine, stake, reels, multiplier, payout, created_ts, request_id, rtp, held) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (name, base["machine"], stake, json.dumps(reels), mult, payout, time.time(), request_id, HOLD_RTP, spin_id))
                house.record(self.db.conn, "slots", f"spin:{cur.lastrowid}", name, stake, stake - payout, stake * (1 - HOLD_RTP))
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
            row = self.db.query_one("SELECT * FROM slot_spins WHERE id=?", (cur.lastrowid,))
            return {"spin": self.public(row), "balance": round(self.db.get_bettor(name)["balance"], 2), "daily": self.daily(name)}

    def spin(self, name, machine, stake, request_id, daily=False):
        """One spin. `daily` makes it one of the day's free spins: the stake is DAILY_STAKE and the house gives it."""
        if daily:
            stake = DAILY_STAKE
        machine = "jackpot" if machine is None else machine
        if not isinstance(machine, str):
            raise BetError("This machine is no longer available. Reload the page.")
        if type(stake) not in (int, float) or stake not in STAKES:
            raise BetError(f"Choose a stake of {', '.join(map(str, STAKES[:-1]))} or {STAKES[-1]} credits.")
        self.check_ref(request_id)
        with self.db.lock:
            bettor = self.db.get_bettor(name)
            if not bettor:
                raise BetError("Sign in as a bettor to spin.")
            name = bettor["name"]
            old = self.db.query_one("SELECT * FROM slot_spins WHERE bettor=? AND request_id=?", (name, request_id))
            if old:
                if old["machine"] != machine or old["stake"] != stake or old["held"]:
                    raise BetError("That spin reference has already been used.")
                return {"spin": self.public(old), "balance": round(bettor["balance"], 2), "daily": self.daily(name)}
            # Old results remain recoverable, but retired machines cannot take new stakes.
            if machine not in self.machines:
                raise BetError("This machine is no longer available. Reload the page.")
            if daily:  # the house gives the stake, once per daily spin (the ref carries the request, so a retry can't)
                now = time.time()
                left = self.daily(name, now)
                if not left or not left["left"]:
                    raise BetError("No free spins left today. They come back at midnight Pacific.")
                self.giver.pay(name, DAILY_STAKE, "slots_daily", f"slots-daily:{name.lower()}:{wheel_day(now)}:{request_id}",
                               None, "Slots: a daily spin")
                bettor = self.db.get_bettor(name)
            if bettor["balance"] < stake:
                raise BetError("Not enough credits for that spin. Choose a smaller stake.")
            m = self.machines[machine]
            reels = draw_spin(m)
            mult = multiplier(m, reels)
            payout = stake * mult
            offer = 1 if not mult and hold_pair(reels) and offers_hold() else None  # a losing pair, now and then
            try:
                self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance + ?, 2) WHERE name=?", (payout - stake, name))
                # rtp is the return this spin was played at (without the secret jackpot), so the house's expected take
                # stays right if the odds change.
                cur = self.db.conn.execute(
                    "INSERT INTO slot_spins(bettor, machine, stake, reels, multiplier, payout, created_ts, request_id, rtp, offer) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (name, machine, stake, json.dumps(reels), mult, payout, time.time(), request_id, rtp(m), offer))
                house.record(self.db.conn, "slots", f"spin:{cur.lastrowid}", name, stake, stake - payout, stake * (1 - rtp(m)))
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
            row = self.db.query_one("SELECT * FROM slot_spins WHERE id=?", (cur.lastrowid,))
            return {"spin": self.public(row), "balance": round(self.db.get_bettor(name)["balance"], 2), "daily": self.daily(name)}

    def house(self):
        """What the house has taken from slots, every season, since spins recorded their return (rtp): the estimate
        (each stake times the edge it was played at, which leaves out the secret symbol), what actually happened
        (stakes minus every payout), and what spins showing the secret symbol paid, which the edge doesn't cover."""
        r = self.db.query_one(
            "SELECT COUNT(*) AS spins, MIN(created_ts) AS since, COALESCE(SUM(stake),0) AS staked, "
            "COALESCE(SUM(payout),0) AS paid, COALESCE(SUM(stake * (1 - rtp)),0) AS expected "
            "FROM slot_spins WHERE rtp IS NOT NULL")
        secret = {i for i, s in enumerate(SYMBOLS) if s.get("secret")}
        jackpots = sum(row["payout"] for row in self.db.query(
            "SELECT reels, payout FROM slot_spins WHERE rtp IS NOT NULL AND payout > 0")
            if secret & set(json.loads(row["reels"])))
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
        """How often a bettor has hit each symbol's line (Golden Onkeys filling in included), every season, since
        spins recorded their return."""
        rows = self.db.query(
            "SELECT machine, reels, multiplier, created_ts FROM slot_spins WHERE bettor=? AND rtp IS NOT NULL", (name,))
        hits = [0] * len(SYMBOLS)
        for r in rows:
            for p in self.parts(r, json.loads(r["reels"])):
                if p["kind"] == "line":
                    hits[p["symbol"]] += 1
        return {"spins": len(rows), "since": min((r["created_ts"] for r in rows), default=None), "hits": hits}

    def summary(self, me=None):
        machines = [{"key": key, **m, "rtp": round(rtp(m) * 100, 2), "rtp_with_secret": round(rtp(m, True) * 100, 2),
                     "chances": chances(m), "win_chance": win_chance(m)}
                    for key, m in self.machines.items()]
        out = {"machines": machines, "symbols": SYMBOLS, "stakes": STAKES, "me": None, "history": [], "lines": None, "daily": None,
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
                out["daily"] = self.daily(name)
        return out
