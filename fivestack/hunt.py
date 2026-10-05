"""Banana Hunt: credits for a menial task.

Onkey has dropped bananas all over the field. The server puts one banana somewhere in a FIELD_W x FIELD_H field
(`start()`), the page draws it, and every click the bettor lands on it (`click()`: within TARGET_R of its centre)
pays CREDIT_PER_BANANA credit and moves the banana somewhere else, at least MIN_HOP away. Misses pay nothing and
leave the banana where it is. The server decides every position and judges every click, so the page can't fake a
hit; two more things keep a script from farming it: hits closer than MIN_INTERVAL_S to the last paid one aren't
paid (the page has Onkey throw the next banana in from his corner, which takes about that long), and each bettor is
capped at `hunt_daily_max` credits a day (`hunt_days`, one row per bettor per Pacific day, like the daily wheel; the
day turns at midnight Pacific). Past the cap there's only a small top-up: a bettor with fewer than `hunt_floor`
credits (50) can keep picking until they have that many, so nobody is stuck with nothing (`_room()`). Credits picked are reported on
the leaderboard in their own column (`hunt`) and left out of betting profit, like game rewards. A season reset tags
the day rows with the season and the cap carries on by the day.

The extras (all on unless the manager is built with `extras=False`) make it a game. None of them raises the day's cap,
which counts credits: they only get a bettor there sooner. The one exception is the scientist's boss fight, whose
prize is paid on top of the cap.

- What Onkey throws (`_new_target()`): mostly a plain banana; sometimes a golden one (GOLD_VALUE credits, but it rots
  GOLD_TTL_S after landing), a bunch (BUNCH_SIZE at once, BUNCH_TTL_S to sweep them, BUNCH_BONUS for all of them), a
  banana with a rotten decoy beside it (picking the decoy freezes the bettor FREEZE_S and breaks the combo), or a
  banana Greg is walking to (he takes it GREG_S after it lands unless it's picked or he's shooed first). From Onkey's
  lore (docs/onkey-lore.md): Man Strudel, the scientist's friendly henchman, sometimes walks up to ask to pet Onkey
  and takes nothing.
- The boss fight, also from the lore: BOSS_CHANCE of throws aren't a banana but the scientist coming for Onkey himself
  (a target of kind "boss"). His claws come down for Onkey in BOSS_WAVES waves, each (how many claws, how many seconds
  they take to reach him); a click names a claw (`claw`, its id) and stops it. Stop a whole wave in time and the next
  comes BOSS_GAP_S later; stop all of them and the bettor is paid BOSS_PRIZE credits on top of the day's cap
  (`db.hunt_bonus`, the day row's `bonus`, which the cap doesn't count). One claw reaching Onkey ends it
  (`_lapse()`: 'boss_lost'): the combo is gone, and on the page Man Strudel sets Onkey free. There's no limit on how
  many fights a day brings.
- Catching it in the air: a click on the banana while it flies (`air`, the page's progress along the arc, which
  `arc_at()` turns into a spot to judge the click against) pays double.
- The combo: picks in a row without a miss (`_mult()`: x2 from COMBO_STEPS[0] in a row, x3 from COMBO_STEPS[1]); a
  miss, a rotten banana, a theft or COMBO_IDLE_S without a pick resets it.
- The hidden item (`_hidden()`): one pick a day, a different one for every bettor, also turns up shop bananas or a
  daily wheel token (ledger reason `hunt`, once per bettor per day).
- The field of the day (`theme()`): the scenery the page draws, one of THEMES by the Pacific day.

Targets and combos live in memory (`self.targets`, `self.combos`, one per bettor); a restart just means starting the
hunt again.
"""
import hashlib
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
FLOOR = 50  # config hunt_floor: past the day's cap, a bettor with fewer credits than this can pick back up to it

# The throw, as the page draws it: from Onkey's hand in the bottom-right corner along an arc, AIR_S in the air.
HAND = (FIELD_W - 96, FIELD_H - 104)
AIR_S = 0.87
AIR_R = 48  # a click within this of the flying banana catches it
AIR_FROM, AIR_TO = 0.12, 0.96  # the part of the flight a catch counts in
AIR_MIN_INTERVAL_S = 0.3  # a catch can come sooner after the last paid pick than a pick off the ground
AIR_MULT = 2
SLACK_S = 0.8  # the page starts each throw a moment after the server made it: timers allow this much
# What Onkey throws: chances per throw (the rest are plain bananas).
GOLD_CHANCE, BUNCH_CHANCE, ROTTEN_CHANCE, GREG_CHANCE = 0.05, 0.04, 0.12, 0.10
GOLD_VALUE, GOLD_TTL_S = 5, 2.5
BUNCH_SIZE, BUNCH_TTL_S, BUNCH_BONUS, BUNCH_INTERVAL_S, BUNCH_GAP = 5, 2.0, 3, 0.1, 90
FREEZE_S = 2.0  # how long a rotten banana stops a bettor picking
DECOY_GAP = 110  # the rotten decoy lands at least this far from the real banana
STRUDEL_CHANCE = 0.04
GREG_S = 1.0  # how long Greg takes to walk to the banana once it has landed
# The scientist's boss fight: this share of throws, his claws come for Onkey instead. Each wave is (claws, the seconds
# they take to reach Onkey); they start BOSS_INTRO_S after he turns up (he says his line first), and each later wave
# BOSS_GAP_S after the one before was cleared.
BOSS_CHANCE = 1 / 60
BOSS_WAVES = ((3, 3.0), (4, 2.8), (5, 2.6), (6, 2.4), (7, 2.2))
BOSS_INTRO_S, BOSS_GAP_S = 2.2, 1.0
BOSS_PRIZE = 100  # credits for clearing every wave, on top of the day's cap
COMBO_STEPS = (10, 25)  # picks in a row for x2, then x3
COMBO_IDLE_S = 8.0
HIDDEN_FROM, HIDDEN_TO = 5, 50  # the hidden item is one of the day's picks in this range
HIDDEN_BANANAS = 25
THEMES = ("jungle", "night", "rain", "beach", "ruins")


def arc_at(x, y, k):
    """Where a banana thrown to (x, y) is at progress k (0 in Onkey's hand, 1 landed): the page draws the same arc."""
    x0, y0 = HAND
    rise = max(110.0, min(240.0, math.hypot(x - x0, y - y0) * 0.4))
    return x0 + (x - x0) * k, y0 + (y - y0) * k - rise * 4 * k * (1 - k)


def theme(day):
    """The field of the day: one of THEMES, by the Pacific day."""
    return THEMES[int(hashlib.sha256(f"hunt-field:{day}".encode()).hexdigest(), 16) % len(THEMES)]


class HuntManager:
    def __init__(self, db, cfg=None, extras=True):
        self.db = db
        self.daily_max = max(0, int((cfg or {}).get("hunt_daily_max", DAILY_MAX)))
        self.floor = max(0, int((cfg or {}).get("hunt_floor", FLOOR)))
        self.extras = extras
        self.targets = {}  # lower-cased bettor name -> the target (see _new_target)
        self.combos = {}  # lower-cased bettor name -> {"n": picks in a row, "ts": the last pick, "frozen": until when}
        self.lock = threading.Lock()
        self.rng = secrets.SystemRandom()
        self._serial = 0

    @property
    def enabled(self):
        return self.daily_max > 0

    def terms(self, now=None):
        return {"enabled": self.enabled, "daily_max": self.daily_max, "floor": self.floor, "per_banana": CREDIT_PER_BANANA,
                "field": {"w": FIELD_W, "h": FIELD_H, "r": TARGET_R}, "min_interval_s": MIN_INTERVAL_S,
                "extras": self.extras, "theme": theme(wheel_day(now or time.time())) if self.extras else THEMES[0],
                "air": {"s": AIR_S, "r": AIR_R, "from": AIR_FROM, "to": AIR_TO, "mult": AIR_MULT, "hand": list(HAND)},
                "gold": {"value": GOLD_VALUE, "ttl_s": GOLD_TTL_S}, "freeze_s": FREEZE_S, "greg_s": GREG_S,
                "boss": {"waves": [list(w) for w in BOSS_WAVES], "prize": BOSS_PRIZE},
                "bunch": {"size": BUNCH_SIZE, "ttl_s": BUNCH_TTL_S, "bonus": BUNCH_BONUS},
                "combo": {"steps": list(COMBO_STEPS), "idle_s": COMBO_IDLE_S}}

    def _room(self, today, balance):
        """How many more credits a bettor may pick right now, and whether that's only because they're under the
        floor: what the day's cap leaves, else (with fewer than `hunt_floor` credits) what gets them back to it."""
        cap_left = max(0, int(math.floor(self.daily_max - today + 1e-9)))
        if cap_left > 0:
            return cap_left, False
        if balance < self.floor:
            return int(math.ceil(self.floor - balance - 1e-9)), True
        return 0, False

    # ---- the banana ------------------------------------------------------------------
    def _spot(self, away=(), gap=MIN_HOP):
        """Somewhere in the field, at least `gap` from every point in `away` when there's room to be."""
        for _ in range(40):
            x = self.rng.randint(MARGIN, FIELD_W - MARGIN)
            y = self.rng.randint(MARGIN, FIELD_H - MARGIN)
            if all((x - p["x"]) ** 2 + (y - p["y"]) ** 2 >= gap ** 2 for p in away):
                break
        return {"x": x, "y": y}

    def _new_target(self, prev=None, now=0.0, paid_ts=0.0):
        """What Onkey throws next. A target is {"id", "kind" (banana / golden / bunch), "x", "y", "born" (when it was
        thrown), "paid_ts" (the last paid pick), and by kind: "expires" (golden, bunch), "items" (bunch: each
        {"x", "y", "picked"}), "decoy" (a rotten banana's spot), "greg" ({"x", "y": where he starts, "arrives"})}."""
        self._serial += 1
        spot = self._spot([prev] if prev else ())
        t = {"id": self._serial, "kind": "banana", **spot, "born": now, "paid_ts": paid_ts}
        if not self.extras:
            return t
        roll = self.rng.random()
        if roll < GOLD_CHANCE:
            t.update(kind="golden", expires=now + AIR_S + GOLD_TTL_S + SLACK_S)
        elif roll < GOLD_CHANCE + BUNCH_CHANCE:
            items = [spot]
            while len(items) < BUNCH_SIZE:
                items.append(self._spot(items, BUNCH_GAP))
            t.update(kind="bunch", items=[{**i, "picked": False} for i in items], expires=now + AIR_S + BUNCH_TTL_S + SLACK_S)
        elif roll < GOLD_CHANCE + BUNCH_CHANCE + ROTTEN_CHANCE:
            t["decoy"] = self._spot([spot], DECOY_GAP)
        elif roll < GOLD_CHANCE + BUNCH_CHANCE + ROTTEN_CHANCE + GREG_CHANCE:
            # Greg comes in from the side further from the banana, level with it or thereabouts.
            gx = 0 if spot["x"] > FIELD_W / 2 else FIELD_W
            gy = min(FIELD_H - MARGIN, max(MARGIN, spot["y"] + self.rng.randint(-120, 120)))
            t["greg"] = {"x": gx, "y": gy, "arrives": now + AIR_S + GREG_S + SLACK_S}
        elif roll < GOLD_CHANCE + BUNCH_CHANCE + ROTTEN_CHANCE + GREG_CHANCE + BOSS_CHANCE:
            t.update(kind="boss", boss=self._wave(0, now, BOSS_INTRO_S))  # no banana: the scientist comes for Onkey
        elif roll < GOLD_CHANCE + BUNCH_CHANCE + ROTTEN_CHANCE + GREG_CHANCE + BOSS_CHANCE + STRUDEL_CHANCE:
            t["strudel"] = {"x": 0 if spot["x"] > FIELD_W / 2 else FIELD_W, "y": spot["y"]}  # a visit: he takes nothing
        return t

    def _wave(self, n, now, wait):
        """Wave `n` of the boss fight: its claws ({"id", "x": where along the top each comes down, "hit"}), how long the
        page waits before they move (`wait`) and how long they take (`secs`), and the server's own deadline."""
        count, secs = BOSS_WAVES[n]
        return {"wave": n, "wait": wait, "secs": secs, "begins": now + wait, "deadline": now + wait + secs + SLACK_S,
                "claws": [{"id": i, "x": self.rng.randint(MARGIN, FIELD_W - MARGIN), "hit": False} for i in range(count)]}

    def _combo(self, key, now):
        c = self.combos.setdefault(key, {"n": 0, "ts": 0.0, "frozen": 0.0})
        if c["n"] and now - c["ts"] > COMBO_IDLE_S:
            c["n"] = 0
        return c

    @staticmethod
    def _mult(n):
        return 1 + sum(n >= step for step in COMBO_STEPS)

    def _lapse(self, key, t, now):
        """What became of a target left too long: 'rotted' (a golden banana), 'bunch_over', 'stolen' (Greg got there),
        'boss_lost' (a claw of the boss fight reached Onkey), or None while it's still good. A lapsed target is
        replaced."""
        why = None
        if t.get("expires") and now > t["expires"]:
            why = "rotted" if t["kind"] == "golden" else "bunch_over"
        elif t.get("greg") and now > t["greg"]["arrives"]:
            why = "stolen"
            self._combo(key, now)["n"] = 0
        elif t["kind"] == "boss" and now > t["boss"]["deadline"]:
            why = "boss_lost"
            self._combo(key, now)["n"] = 0
        if why:
            self.targets[key] = self._new_target(t, now, t["paid_ts"])
        return why

    def _target(self, name, now, early=0.0):
        """The bettor's target, a new one if there's none; lapsed ones are replaced (`early`: seconds of the timers'
        slack to take back, when it's the page's own timer that ran out). Returns (target, what lapsed)."""
        key = name.lower()
        with self.lock:
            t = self.targets.get(key)
            if t is None:
                t = self.targets[key] = self._new_target(None, now)
                return t, None
            why = self._lapse(key, t, now + early)
            if why:
                self.targets[key]['born'] = now
            return self.targets[key], why

    @staticmethod
    def _public(t):
        if not t:
            return None
        out = {"id": t["id"], "kind": t["kind"], "x": t["x"], "y": t["y"]}
        if t["kind"] == "bunch":
            out["items"] = [{"x": i["x"], "y": i["y"], "picked": i["picked"]} for i in t["items"]]
        if t.get("decoy"):
            out["decoy"] = dict(t["decoy"])
        if t.get("greg"):
            out["greg"] = {"x": t["greg"]["x"], "y": t["greg"]["y"]}
        if t["kind"] == "boss":
            b = t["boss"]
            out["boss"] = {"wave": b["wave"], "waves": len(BOSS_WAVES), "wait": b["wait"], "secs": b["secs"],
                           "claws": [dict(c) for c in b["claws"]]}
        if t.get("strudel"):
            out["strudel"] = dict(t["strudel"])
        return out

    # ---- the day ---------------------------------------------------------------------
    def _day(self, name, now):
        """(bananas picked, credits picked) by this bettor today (whatever season the rows belong to)."""
        row = self.db.query_one("SELECT COALESCE(SUM(bananas), 0) AS n, COALESCE(SUM(credits), 0) AS c FROM hunt_days "
                                "WHERE lower(bettor)=lower(?) AND day=?", (name, wheel_day(now)))
        return (int(row["n"]), float(row["c"])) if row else (0, 0.0)

    def today(self, name, now=None):
        """Credits this bettor picked today: what the day's cap counts."""
        return int(round(self._day(name, now or time.time())[1]))

    def _hidden(self, name, day):
        """(which of the day's picks hides the item, what it is: 'bananas' / 'boost' / 'insurance') for a bettor."""
        secret = self.db.get_meta("cookie_secret") or ""
        h = int(hashlib.sha256(f"hunt-item:{secret}:{name.lower()}:{day}".encode()).hexdigest(), 16)
        roll = (h >> 16) % 100
        return HIDDEN_FROM + h % (HIDDEN_TO - HIDDEN_FROM + 1), "bananas" if roll < 60 else "boost" if roll < 85 else "insurance"

    def _found(self, name, day, picks, now):
        """Hand over the day's hidden item if this pick was the one: once per bettor per day."""
        if not self.extras:
            return None
        nth, prize = self._hidden(name, day)
        if picks != nth:
            return None
        amount = HIDDEN_BANANAS if prize == "bananas" else 0
        with self.db.lock:
            cur = self.db.conn.execute(
                "INSERT OR IGNORE INTO banana_ledger(bettor, delta, reason, ref, note, created_ts) VALUES(?,?,'hunt',?,?,?)",
                (name, amount, f"hunt:{name.lower()}:{day}", f"Banana Hunt: the hidden item ({prize})", now))
            if cur.rowcount and prize != "bananas":
                self.db.conn.execute("INSERT INTO wheel_perks(bettor, kind, status, created_ts) VALUES(?,?,'ready',?)", (name, prize, now))
            self.db.conn.commit()
            if not cur.rowcount:
                return None
        return {"kind": prize, "amount": amount,
                "label": f"{HIDDEN_BANANAS} bananas" if prize == "bananas" else "a boost token" if prize == "boost" else "an insurance token"}

    # ---- reading ---------------------------------------------------------------------
    def status(self, name, now=None):
        """The bettor's hunt: today's credits and what's left (and whether only the floor allows it), their totals
        and combo, and the banana (None once they're done)."""
        now = now or time.time()
        bettor = self.db.get_bettor(name)
        balance = bettor["balance"] if bettor else 0.0
        picks, credits = self._day(name, now)
        left, under = self._room(credits, balance)
        totals = self.db.hunt_totals().get(name.lower()) or {}
        t = self.targets.get(name.lower()) if left else None
        combo = self._combo(name.lower(), now)
        return {"name": name, "today": int(round(credits)), "picks": picks, "left": left, "done": left == 0, "under_floor": under,
                "floor": self.floor,
                "balance": round(balance, 2), "day": wheel_day(now),
                "resets_ts": next_reset(now), "season": round(totals.get("season") or 0, 2),
                "all_time": round(totals.get("all_time") or 0, 2), "bananas": int(totals.get("bananas") or 0),
                "combo": combo["n"], "mult": self._mult(combo["n"]),
                "target": self._public(t)}

    def start(self, name, now=None):
        """Make sure the bettor has a banana to go for and return their status. One already down is thrown again by
        the page, so its timers start over; it's never swapped for another (that would let a page fish for a golden
        one)."""
        bettor = self.db.get_bettor(name)
        if not bettor:
            raise BetError("Sign in as a bettor first.")
        if not self.enabled:
            raise BetError("The hunt is closed.")
        now = now or time.time()
        name = bettor["name"]
        if self._room(self._day(name, now)[1], bettor["balance"])[0] > 0:
            t, _ = self._target(name, now)
            with self.lock:
                shift = now - t["born"]
                t["born"] = now
                if t.get("expires"):
                    t["expires"] += shift
                if t.get("greg"):
                    t["greg"]["arrives"] += shift
                if t["kind"] == "boss":  # the page shows the wave from its start again
                    t["boss"]["begins"], t["boss"]["deadline"] = now + t["boss"]["wait"], now + t["boss"]["wait"] + t["boss"]["secs"] + SLACK_S
        return self.status(name, now)

    def summary(self, me=None, now=None):
        return {"hunt": self.terms(now), "board": self.board(), "me": self.status(me["name"], now) if me else None}

    def board(self, limit=10):
        """Who has picked the most: this season's credits, with today's and all-time counts."""
        today = wheel_day(time.time())
        rows = self.db.query(
            """SELECT bettor, SUM(CASE WHEN season_id IS NULL THEN credits + COALESCE(bonus, 0) ELSE 0 END) AS season,
                      SUM(credits + COALESCE(bonus, 0)) AS all_time, SUM(bananas) AS bananas,
                      SUM(CASE WHEN day=? THEN bananas ELSE 0 END) AS today
               FROM hunt_days GROUP BY lower(bettor) ORDER BY season DESC, all_time DESC, lower(bettor) LIMIT ?""",
            (today, int(limit)))
        return [{"name": r["bettor"], "season": round(r["season"] or 0, 2), "all_time": round(r["all_time"] or 0, 2),
                 "bananas": int(r["bananas"] or 0), "today": int(r["today"] or 0)} for r in rows]

    # ---- clicking ---------------------------------------------------------------------
    def click(self, name, x, y, now=None, air=None, shoo=False, claw=None):
        """Judge a click at (x, y) in field pixels. A hit pays and moves the banana; the reply carries the banana to
        draw next (None once the day's cap is reached), today's credits and what's left, and the combo. `air`: the
        click was on the banana in flight, this far along its arc (0-1). `shoo`: the click was on Greg. `claw`: the
        click was on that claw of the boss fight (its id)."""
        bettor = self.db.get_bettor(name)
        if not bettor:
            raise BetError("Sign in as a bettor first.")
        if not self.enabled:
            raise BetError("The hunt is closed.")
        try:
            x, y = float(x), float(y)
            air = None if air is None else float(air)
        except (TypeError, ValueError):
            raise BetError("Where did you click?")
        if x != x or y != y or (air is not None and air != air):  # NaN
            raise BetError("Where did you click?")
        now = now or time.time()
        name = bettor["name"]
        key = name.lower()
        picks, credits = self._day(name, now)
        left, under = self._room(credits, bettor["balance"])
        if left <= 0:
            return {"hit": False, "reason": "done", "today": int(round(credits)), "left": 0, "done": True, "under_floor": False,
                    "target": None, "combo": 0, "mult": 1}
        t, lapsed = self._target(name, now)
        combo = self._combo(key, now)

        def reply(hit, reason=None, **more):
            out = {"hit": hit, "today": int(round(credits)), "left": left, "done": left == 0, "under_floor": under,
                   "target": self._public(self.targets.get(key)), "combo": combo["n"], "mult": self._mult(combo["n"]), **more}
            if reason:
                out["reason"] = reason
            return out

        if lapsed:  # it rotted, the bunch's time ran out or Greg took it before this click: here's the next one
            return reply(False, lapsed)
        if t["kind"] == "boss":  # the fight: only a click on a claw that's coming counts, and nothing else costs anything
            b = t["boss"]
            hit = next((c for c in b["claws"] if type(claw) is int and c["id"] == claw and not c["hit"]), None)
            if hit is None or now < b["begins"]:
                return reply(False, "boss")
            hit["hit"] = True
            if not all(c["hit"] for c in b["claws"]):
                return reply(False, "claw_hit")
            if b["wave"] + 1 < len(BOSS_WAVES):
                with self.lock:
                    t["boss"] = self._wave(b["wave"] + 1, now, BOSS_GAP_S)
                return reply(False, "wave_cleared")
            self.db.hunt_bonus(name, wheel_day(now), BOSS_PRIZE, now)  # every wave stopped: paid on top of the cap
            with self.lock:
                self.targets[key] = self._new_target(t, now, now)
            return reply(True, paid=BOSS_PRIZE, kind="boss", air=False, swept=False, bunch_bonus=0, found=None)
        if now < combo["frozen"]:
            return reply(False, "frozen", frozen_s=round(combo["frozen"] - now, 2))
        if shoo:
            if t.get("greg"):
                with self.lock:
                    t.pop("greg", None)
                return reply(False, "shooed")
            return reply(False, "miss")  # nobody there: not a miss that breaks the combo
        near = lambda p, r=TARGET_R: (x - p["x"]) ** 2 + (y - p["y"]) ** 2 <= r ** 2  # noqa: E731
        if t.get("decoy") and air is None and near(t["decoy"]):
            with self.lock:
                combo["n"], combo["frozen"] = 0, now + FREEZE_S
                self.targets[key] = self._new_target(t, now, t["paid_ts"])
            return reply(False, "rotten", frozen_s=FREEZE_S)

        value, item = CREDIT_PER_BANANA, None
        if t["kind"] == "bunch":
            item = next((i for i in t["items"] if not i["picked"] and near(i)), None)
            if item is None:
                combo["n"] = 0
                return reply(False, "miss")
            if now - t["paid_ts"] < BUNCH_INTERVAL_S:
                return reply(False, "too_fast")
        elif air is not None:
            ax, ay = arc_at(t["x"], t["y"], min(1.0, max(0.0, air)))
            if not (AIR_FROM <= air <= AIR_TO) or now - t["born"] > AIR_S + SLACK_S * 2 or not near({"x": ax, "y": ay}, AIR_R):
                combo["n"] = 0
                return reply(False, "miss")
            if now - t["paid_ts"] < AIR_MIN_INTERVAL_S:
                return reply(False, "too_fast")
            value *= AIR_MULT
        else:
            if now - t["paid_ts"] < MIN_INTERVAL_S:
                return reply(False, "too_fast")
            if not near(t):
                combo["n"] = 0
                return reply(False, "miss")
        if t["kind"] == "golden":
            value *= GOLD_VALUE

        # A hit. The combo counts it, then multiplies it; the bunch's bonus comes on top. Never past what's left.
        combo["n"], combo["ts"] = combo["n"] + 1, now
        mult = self._mult(combo["n"])
        swept = False
        if item is not None:
            item["picked"] = True
            swept = all(i["picked"] for i in t["items"])
        pay = min(value * mult + (BUNCH_BONUS if swept else 0), left)
        self.db.hunt_pay(name, wheel_day(now), pay, now)
        picks, credits = picks + 1, credits + pay
        found = self._found(name, wheel_day(now), picks, now)
        left, under = self._room(credits, bettor["balance"] + pay)
        with self.lock:
            t["paid_ts"] = now
            if left <= 0:
                self.targets.pop(key, None)
            elif item is None or swept:
                self.targets[key] = self._new_target(t, now, now)
        return reply(True, paid=pay, kind=t["kind"], air=air is not None, swept=swept, bunch_bonus=BUNCH_BONUS if swept else 0,
                     found=found)

    def nudge(self, name, now=None):
        """The page's timer ran out (a golden banana rotted, a bunch's time is up, Greg arrived): replace the target
        if the server agrees, and say what happened. Never swaps a target that's still good."""
        bettor = self.db.get_bettor(name)
        if not bettor:
            raise BetError("Sign in as a bettor first.")
        now = now or time.time()
        name = bettor["name"]
        picks, credits = self._day(name, now)
        left, under = self._room(credits, bettor["balance"])
        if left <= 0:
            return {"reason": "done", "today": int(round(credits)), "left": 0, "done": True, "under_floor": False, "target": None,
                    "combo": 0, "mult": 1}
        t, lapsed = self._target(name, now, early=SLACK_S + 0.2)
        combo = self._combo(name.lower(), now)
        return {"reason": lapsed, "today": int(round(credits)), "left": left, "done": False, "under_floor": under,
                "target": self._public(t), "combo": combo["n"], "mult": self._mult(combo["n"])}
