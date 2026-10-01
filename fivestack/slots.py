"""Instant virtual-credit slots. Outcomes, payment and retry protection live on the server."""
import json
import secrets
import time

from .bets import BetError

STAKES = (5, 10, 25, 50, 100)
SYMBOLS = [
    {"key": "banana", "icon": "🍌", "name": "Banana"},
    {"key": "cherry", "icon": "🍒", "name": "Cherry"},
    {"key": "bell", "icon": "🔔", "name": "Bell"},
    {"key": "diamond", "icon": "💎", "name": "Diamond"},
    {"key": "spike", "icon": "💣", "name": "Spike"},
    {"key": "monkey", "icon": "🐒", "name": "Onkey"},
]
MACHINES = {
    "jackpot": {"name": "Slots", "icon": "🎰", "desc": "Match three symbols. Win up to 80× your stake.",
                "triples": [8, 12, 20, 30, 50, 80], "pair": 0},
}


def multiplier(machine, reels):
    if reels[0] == reels[1] == reels[2]:
        return machine["triples"][reels[0]]
    return machine["pair"] if len(set(reels)) == 2 else 0


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
            raise BetError("Choose a stake of 5, 10, 25, 50 or 100 credits.")
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
            reels = [secrets.randbelow(len(SYMBOLS)) for _ in range(3)]
            mult = multiplier(MACHINES[machine], reels)
            payout = stake * mult
            try:
                self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance + ?, 2) WHERE name=?", (payout - stake, name))
                cur = self.db.conn.execute(
                    "INSERT INTO slot_spins(bettor, machine, stake, reels, multiplier, payout, created_ts, request_id) "
                    "VALUES(?,?,?,?,?,?,?,?)", (name, machine, stake, json.dumps(reels), mult, payout, time.time(), request_id))
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
            row = self.db.query_one("SELECT * FROM slot_spins WHERE id=?", (cur.lastrowid,))
            return {"spin": self.public(row), "balance": round(self.db.get_bettor(name)["balance"], 2)}

    def summary(self, me=None):
        machines = [{"key": key, **m, "rtp": round((sum(m["triples"]) + 90 * m["pair"]) / 216 * 100, 2)}
                    for key, m in MACHINES.items()]
        out = {"machines": machines, "symbols": SYMBOLS, "stakes": STAKES, "me": None, "history": []}
        if me:
            with self.db.lock:
                name = me["name"]
                totals = self.db.query_one(
                    "SELECT COUNT(*) AS spins, COALESCE(SUM(stake),0) AS staked, "
                    "COALESCE(SUM(payout),0) AS returned FROM slot_spins WHERE bettor=? AND season_id IS NULL", (name,))
                out["me"] = {"name": name, **totals, "net": totals["returned"] - totals["staked"]}
                out["history"] = [self.public(row) for row in self.db.query(
                    "SELECT * FROM slot_spins WHERE bettor=? AND season_id IS NULL ORDER BY id DESC LIMIT 10", (name,))]
        return out
