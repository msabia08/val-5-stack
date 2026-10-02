"""Banana Hunt: credits for a menial task.

Onkey has dropped bananas all over the field. The server puts one banana somewhere in a FIELD_W x FIELD_H field
(`start()`), the page draws it, and every click the bettor lands on it (`click()`: within TARGET_R of its centre)
pays CREDIT_PER_BANANA credit and moves the banana somewhere else, at least MIN_HOP away. Misses pay nothing and
leave the banana where it is. The server decides every position and judges every click, so the page can't fake a
hit; two more things keep a script from farming it: hits closer than MIN_INTERVAL_S to the last paid one aren't
paid (the page has Onkey throw the next banana in from his corner, which takes about that long, so a person can't
click sooner anyway), and each bettor is capped at `hunt_daily_max` bananas a day (`hunt_days`, one row per bettor
per Pacific day, like the daily wheel; the day turns at midnight Pacific). The cap has a floor under it: a bettor
with fewer than `hunt_floor` credits keeps picking past the cap until they have that many, so nobody is stuck broke
(`_room()`). Credits picked are reported on the leaderboard in their own column (`hunt`) and left out of betting
profit, like game rewards. A season reset tags the day rows with the season and the cap carries on by the day.

Targets live in memory (`self.targets`, one per bettor); a restart just means starting the hunt again.
"""
import math
import secrets
import threading
import time

from .bets import BetError
from .wheel import next_reset, wheel_day

FIELD_W, FIELD_H = 1200, 600  # the field the page draws, in CSS pixels
TARGET_R = 34  # a click within this of the banana's centre is a hit
MARGIN = 48  # the banana's centre stays this far inside the field
MIN_HOP = 160  # the banana never lands closer than this to where it was
MIN_INTERVAL_S = 0.6  # the fastest paid pace: Onkey's throw on the page takes a little longer than this
CREDIT_PER_BANANA = 1
DAILY_MAX = 250  # config hunt_daily_max
FLOOR = 250  # config hunt_floor: with fewer credits than this, the daily cap doesn't apply


class HuntManager:
    def __init__(self, db, cfg=None):
        self.db = db
        self.daily_max = max(0, int((cfg or {}).get("hunt_daily_max", DAILY_MAX)))
        self.floor = max(0, int((cfg or {}).get("hunt_floor", FLOOR)))
        self.targets = {}  # lower-cased bettor name -> {"x", "y", "paid_ts"}
        self.lock = threading.Lock()

    @property
    def enabled(self):
        return self.daily_max > 0

    def terms(self):
        return {"enabled": self.enabled, "daily_max": self.daily_max, "floor": self.floor, "per_banana": CREDIT_PER_BANANA,
                "field": {"w": FIELD_W, "h": FIELD_H, "r": TARGET_R}, "min_interval_s": MIN_INTERVAL_S}

    def _room(self, today, balance):
        """How many more bananas a bettor may pick right now, and whether that's only because they're under the
        floor: what the day's cap leaves, else (with fewer than `hunt_floor` credits) what gets them back to it."""
        cap_left = max(0, self.daily_max - today)
        if cap_left > 0:
            return cap_left, False
        if balance < self.floor:
            return int(math.ceil(self.floor - balance - 1e-9)), True
        return 0, False

    # ---- the banana ------------------------------------------------------------------
    @staticmethod
    def _place(prev=None):
        """Somewhere in the field, at least MIN_HOP from `prev` when there's room to be."""
        rng = secrets.SystemRandom()
        for _ in range(40):
            x = rng.randint(MARGIN, FIELD_W - MARGIN)
            y = rng.randint(MARGIN, FIELD_H - MARGIN)
            if prev is None or (x - prev["x"]) ** 2 + (y - prev["y"]) ** 2 >= MIN_HOP ** 2:
                break
        return {"x": x, "y": y}

    def _target(self, name, fresh=False):
        key = name.lower()
        with self.lock:
            t = self.targets.get(key)
            if fresh or t is None:
                t = {**self._place(t), "paid_ts": 0.0}
                self.targets[key] = t
            return t

    @staticmethod
    def _public(t):
        return {"x": t["x"], "y": t["y"]} if t else None

    # ---- reading ---------------------------------------------------------------------
    def today(self, name, now=None):
        """Bananas this bettor picked today (whatever season the rows belong to)."""
        row = self.db.query_one("SELECT COALESCE(SUM(bananas), 0) AS n FROM hunt_days WHERE lower(bettor)=lower(?) AND day=?",
                                (name, wheel_day(now or time.time())))
        return int(row["n"]) if row else 0

    def status(self, name, now=None):
        """The bettor's hunt: today's count and what's left (and whether only the floor allows it), their totals,
        and the banana (None once they're done)."""
        now = now or time.time()
        bettor = self.db.get_bettor(name)
        balance = bettor["balance"] if bettor else 0.0
        today = self.today(name, now)
        left, under = self._room(today, balance)
        totals = self.db.hunt_totals().get(name.lower()) or {}
        t = self.targets.get(name.lower()) if left else None
        return {"name": name, "today": today, "left": left, "done": left == 0, "under_floor": under, "floor": self.floor,
                "balance": round(balance, 2), "day": wheel_day(now),
                "resets_ts": next_reset(now), "season": round(totals.get("season") or 0, 2),
                "all_time": round(totals.get("all_time") or 0, 2), "bananas": int(totals.get("bananas") or 0),
                "target": self._public(t)}

    def start(self, name, now=None):
        """Put a banana down (a fresh one, wherever the last one was) and return the bettor's status."""
        bettor = self.db.get_bettor(name)
        if not bettor:
            raise BetError("Sign in as a bettor first.")
        if not self.enabled:
            raise BetError("The hunt is closed.")
        now = now or time.time()
        if self._room(self.today(bettor["name"], now), bettor["balance"])[0] > 0:
            self._target(bettor["name"], fresh=True)
        return self.status(bettor["name"], now)

    def summary(self, me=None, now=None):
        return {"hunt": self.terms(), "board": self.board(), "me": self.status(me["name"], now) if me else None}

    def board(self, limit=10):
        """Who has picked the most: this season's credits, with today's and all-time counts."""
        today = wheel_day(time.time())
        rows = self.db.query(
            """SELECT bettor, SUM(CASE WHEN season_id IS NULL THEN credits ELSE 0 END) AS season,
                      SUM(credits) AS all_time, SUM(bananas) AS bananas,
                      SUM(CASE WHEN day=? THEN bananas ELSE 0 END) AS today
               FROM hunt_days GROUP BY lower(bettor) ORDER BY season DESC, all_time DESC, lower(bettor) LIMIT ?""",
            (today, int(limit)))
        return [{"name": r["bettor"], "season": round(r["season"] or 0, 2), "all_time": round(r["all_time"] or 0, 2),
                 "bananas": int(r["bananas"] or 0), "today": int(r["today"] or 0)} for r in rows]

    # ---- clicking ---------------------------------------------------------------------
    def click(self, name, x, y, now=None):
        """Judge a click at (x, y) in field pixels. A hit pays a credit and moves the banana; the reply carries the
        banana to draw next (None once the day's cap is reached), today's count and what's left."""
        bettor = self.db.get_bettor(name)
        if not bettor:
            raise BetError("Sign in as a bettor first.")
        if not self.enabled:
            raise BetError("The hunt is closed.")
        try:
            x, y = float(x), float(y)
        except (TypeError, ValueError):
            raise BetError("Where did you click?")
        if x != x or y != y:  # NaN
            raise BetError("Where did you click?")
        now = now or time.time()
        name = bettor["name"]
        today = self.today(name, now)
        left, under = self._room(today, bettor["balance"])
        if left <= 0:
            return {"hit": False, "reason": "done", "today": today, "left": 0, "done": True, "under_floor": False, "target": None}
        t = self._target(name)
        reply = {"today": today, "left": left, "done": False, "under_floor": under, "target": self._public(t)}
        if now - t["paid_ts"] < MIN_INTERVAL_S:
            return {"hit": False, "reason": "too_fast", **reply}
        if (x - t["x"]) ** 2 + (y - t["y"]) ** 2 > TARGET_R ** 2:
            return {"hit": False, "reason": "miss", **reply}
        self.db.hunt_pay(name, wheel_day(now), CREDIT_PER_BANANA, now)
        today += 1
        left, under = self._room(today, bettor["balance"] + CREDIT_PER_BANANA)
        with self.lock:
            if left > 0:
                nxt = {**self._place(t), "paid_ts": now}
                self.targets[name.lower()] = nxt
            else:
                nxt = None
                self.targets.pop(name.lower(), None)
        return {"hit": True, "paid": CREDIT_PER_BANANA, "today": today, "left": left, "done": left == 0, "under_floor": under,
                "target": self._public(nxt)}
