"""The daily wheel (Betting's Wheel page): one free spin a day per bettor, paid by the house.

The day turns over at midnight Pacific time (3 AM Eastern), so a late night on the East Coast still counts as the
same day, and it follows daylight saving time (computed here, since Windows has no time zone database in the standard
library). Each slice's size on the wheel is its chance (`SEGMENTS` weights out of 1000). Prizes:

- credits, free: they're recorded as house payouts (kind `wheel`, so they show in the standings' House column) but
  don't come out of the pot;
- the progressive jackpot, the whole of it, as the rarest slice: the only prize paid from the house's money;
- bananas (Onkey's Shop money; ledger reason `wheel`, not counted as earned);
- a free shop cosmetic the bettor doesn't own yet (bananas instead if they own them all);
- a boost token (their next single of up to TOKEN_MAX_STAKE credits pays TOKEN_BOOST more profit) or an insurance
  token (their next single, if it loses, gets its stake back up to TOKEN_MAX_STAKE credits, also free): `wheel_perks`,
  used by BetManager.place() and paid by HouseManager.refunds();
- another spin today, or nothing at all ("Onkey ate it").

The server picks the slice (`secrets`); the page only animates to it. In demo mode (`unlimited`) there's no daily
limit and a spin can ask for a slice by key, so the page can be tried out (and tested) as often as you like.
"""
import datetime
import json
import secrets
import time

from .bananas import ITEMS
from .bets import TOKEN_BOOST, TOKEN_MAX_STAKE, BetError

# key, label (what the slice says), kind, amount, weight (out of 1000; the slice's share of the wheel)
SEGMENTS = [
    {"key": "c50", "label": "50 credits", "kind": "credits", "amount": 50, "weight": 210},
    {"key": "b50", "label": "50 bananas", "kind": "bananas", "amount": 50, "weight": 130},
    {"key": "boost", "label": "Boost token", "kind": "boost", "amount": None, "weight": 90},
    {"key": "c100", "label": "100 credits", "kind": "credits", "amount": 100, "weight": 150},
    {"key": "ate", "label": "Onkey ate it", "kind": "nothing", "amount": None, "weight": 55},
    {"key": "c200", "label": "200 credits", "kind": "credits", "amount": 200, "weight": 80},
    {"key": "insure", "label": "Insurance", "kind": "insurance", "amount": None, "weight": 80},
    {"key": "b100", "label": "100 bananas", "kind": "bananas", "amount": 100, "weight": 70},
    {"key": "jackpot", "label": "Jackpot", "kind": "jackpot", "amount": None, "weight": 5},
    {"key": "again", "label": "Spin again", "kind": "again", "amount": None, "weight": 50},
    {"key": "item", "label": "Free cosmetic", "kind": "item", "amount": None, "weight": 30},
    {"key": "c400", "label": "400 credits", "kind": "credits", "amount": 400, "weight": 50},
]
TOTAL_WEIGHT = sum(s["weight"] for s in SEGMENTS)
ALL_ITEMS_OWNED_BANANAS = 100  # the cosmetic slice when they already own every item


def _sunday(year, month, n):
    """The n-th Sunday of a month, as a date."""
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(6 - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)


def _utc(d, hour):
    return datetime.datetime(d.year, d.month, d.day, hour, tzinfo=datetime.timezone.utc).timestamp()


def pacific_offset(ts):
    """Seconds Pacific time is ahead of UTC at `ts`: -7h from the second Sunday of March 2 AM to the first Sunday of
    November 2 AM (US daylight saving time), else -8h."""
    year = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).year
    start = _utc(_sunday(year, 3, 2), 10)  # 2 AM PST
    end = _utc(_sunday(year, 11, 1), 9)  # 2 AM PDT
    return -7 * 3600 if start <= ts < end else -8 * 3600


def wheel_day(ts):
    """The wheel's day for `ts`: the Pacific date, as YYYY-MM-DD."""
    return datetime.datetime.fromtimestamp(ts + pacific_offset(ts), datetime.timezone.utc).date().isoformat()


def next_reset(ts):
    """When the next wheel day starts: the next midnight Pacific after `ts`."""
    local = datetime.datetime.fromtimestamp(ts + pacific_offset(ts), datetime.timezone.utc).date()
    midnight = _utc(local + datetime.timedelta(days=1), 0)
    guess = midnight - pacific_offset(midnight)
    return midnight - pacific_offset(guess)


def draw_segment():
    """A slice index, each at its weight out of TOTAL_WEIGHT."""
    ticket = secrets.randbelow(TOTAL_WEIGHT)
    for i, s in enumerate(SEGMENTS):
        if ticket < s["weight"]:
            return i
        ticket -= s["weight"]
    return len(SEGMENTS) - 1


UNLIMITED_SPINS = 99  # what spins_left says in demo mode


class WheelManager:
    def __init__(self, db, house, unlimited=False):
        self.db, self.house, self.unlimited = db, house, unlimited

    def spins_left(self, name, now=None):
        """1 if they haven't spun today (or won "Spin again" with their last spin today), else 0; always
        UNLIMITED_SPINS in demo mode."""
        if self.unlimited:
            return UNLIMITED_SPINS
        day = wheel_day(now or time.time())
        rows = self.db.query("SELECT prize FROM wheel_spins WHERE lower(bettor)=lower(?) AND day=?", (name, day))
        return max(0, 1 + sum(r["prize"] == "again" for r in rows) - len(rows))

    def perks(self, name):
        return self.db.query("SELECT id, kind, created_ts FROM wheel_perks WHERE lower(bettor)=lower(?) AND status='ready' "
                             "ORDER BY id", (name,))

    def summary(self, me=None, now=None):
        now = now or time.time()
        recent = [self._public(r) for r in self.db.query("SELECT * FROM wheel_spins ORDER BY id DESC LIMIT 12")]
        out = {"segments": SEGMENTS, "total_weight": TOTAL_WEIGHT, "jackpot": int(max(0.0, self.house.summary_pots()[1])),
               "next_reset": next_reset(now), "day": wheel_day(now), "recent": recent,
               "token": {"boost": TOKEN_BOOST, "max_stake": TOKEN_MAX_STAKE}, "unlimited": self.unlimited,
               "biggest": [self._public(r) for r in self.db.query(
                   "SELECT * FROM wheel_spins WHERE prize='jackpot' ORDER BY amount DESC LIMIT 3")],
               "me": None}
        if me:
            mine = self.db.query("SELECT * FROM wheel_spins WHERE lower(bettor)=lower(?) ORDER BY id DESC LIMIT 10", (me["name"],))
            out["me"] = {"name": me["name"], "spins_left": self.spins_left(me["name"], now),
                         "perks": self.perks(me["name"]), "history": [self._public(r) for r in mine]}
        return out

    @staticmethod
    def _public(r):
        return {k: r[k] for k in ("id", "bettor", "prize", "label", "amount", "detail", "created_ts", "segment")}

    def spin(self, name, now=None, segment=None, force=None):
        """Spin the wheel for a bettor: one a day (see spins_left), paid straight away. `segment` (tests only) fixes
        the slice; `force`, a slice's key, does the same in demo mode only. Returns the spin (with `segment`, the slice
        index the page lands on)."""
        keys = [s["key"] for s in SEGMENTS]
        if force and self.unlimited and force in keys:
            segment = keys.index(force)
        now = now or time.time()
        bettor = self.db.get_bettor(name)
        if not bettor:
            raise BetError("Sign in to spin the wheel.")
        name = bettor["name"]
        with self.db.lock:
            if not self.spins_left(name, now):
                raise BetError("You've had today's spin. The wheel resets at midnight Pacific (3 AM Eastern).")
            i = draw_segment() if segment is None else segment
            seg = SEGMENTS[i]
            cur = self.db.conn.execute(
                "INSERT INTO wheel_spins(bettor, day, segment, prize, label, amount, detail, created_ts) VALUES(?,?,?,?,?,?,?,?)",
                (name, wheel_day(now), i, seg["key"], seg["label"], None, None, now))
            spin_id = cur.lastrowid
            self.db.conn.commit()
            amount, label, detail = self._award(name, seg, spin_id)
            self.db.conn.execute("UPDATE wheel_spins SET amount=?, label=?, detail=? WHERE id=?", (amount, label, detail, spin_id))
            self.db.conn.commit()
        row = self.db.query_one("SELECT * FROM wheel_spins WHERE id=?", (spin_id,))
        return {**self._public(row), "balance": self.db.get_bettor(name)["balance"],
                "spins_left": self.spins_left(name, now)}

    def _award(self, name, seg, spin_id):
        """Pay one slice. Returns (amount, label, detail) for the spin row."""
        kind, ref = seg["kind"], f"wheel:{spin_id}"
        if kind == "credits":
            self.house.pay(name, seg["amount"], "wheel", ref, None, f"Daily wheel: {seg['label']}")
            return seg["amount"], seg["label"], None
        if kind == "jackpot":
            jackpot = int(max(0.0, self.house.summary_pots()[1]))  # whole credits; the fraction stays in the jackpot
            paid = self.house.pay(name, jackpot, "jackpot", ref, None, "Daily wheel: the jackpot")
            return paid, f"The jackpot: {paid:g} credits", None
        if kind == "bananas":
            self._bananas(name, seg["amount"], ref, f"Daily wheel: {seg['label']}")
            return seg["amount"], seg["label"], None
        if kind in ("boost", "insurance"):
            self.db.conn.execute("INSERT INTO wheel_perks(bettor, kind, status, spin_id, created_ts) VALUES(?,?,'ready',?,?)",
                                 (name, kind, spin_id, time.time()))
            self.db.conn.commit()
            return None, seg["label"], None
        if kind == "item":
            owned = {r["item_id"] for r in self.db.query("SELECT item_id FROM banana_items WHERE lower(bettor)=lower(?)", (name,))}
            options = [i for i in ITEMS.values() if i["id"] not in owned]
            if not options:
                self._bananas(name, ALL_ITEMS_OWNED_BANANAS, ref, "Daily wheel: you own every cosmetic")
                return ALL_ITEMS_OWNED_BANANAS, f"{ALL_ITEMS_OWNED_BANANAS} bananas (you own every cosmetic)", None
            item = options[secrets.randbelow(len(options))]
            self.db.conn.execute("INSERT INTO banana_items(bettor, item_id, price, bought_ts) VALUES(?,?,?,?)",
                                 (name, item["id"], 0, time.time()))
            self.db.conn.commit()
            return None, f"Free cosmetic: {item['name']}", json.dumps({"item_id": item["id"], "slot": item["slot"]})
        return None, seg["label"], None  # spin again / Onkey ate it

    def _bananas(self, name, amount, ref, note):
        self.db.conn.execute("INSERT OR IGNORE INTO banana_ledger(bettor, delta, reason, ref, note, created_ts) "
                             "VALUES(?,?,'wheel',?,?,?)", (name, amount, ref, note, time.time()))
        self.db.conn.commit()
