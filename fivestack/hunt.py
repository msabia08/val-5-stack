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
- More kinds of banana: a frozen one takes two clicks (the first cracks the ice) and pays FROZEN_VALUE; a bouncing one
  pays BOUNCE_VALUE but hops to a new spot every BOUNCE_S, BOUNCE_HOPS times, before it settles; a split one breaks
  into SPLIT_SIZE pieces where it is when picked (a small bunch, SPLIT_BONUS for all of them). And corn: now and
  then Onkey's throw is an ear of corn and nothing else. It looks the part, costs CORN_COST credits and the combo if
  it's picked, and is gone by itself CORN_TTL_S after it lands (reason corn_gone, nothing lost).
- Throw patterns: a volley is VOLLEY_SIZE steel bananas thrown one after another (VOLLEY_GAP_S apart) along a line or
  an arc. Steel can't be caught: each can only be picked once it has landed (a click sooner isn't paid and costs
  nothing), and picking every one before the time runs out pays VOLLEY_BONUS on top.
- Hold and drag (as in a rhythm game): a green banana isn't ripe, so a click doesn't pick it: the bettor holds the
  button down on it for HOLD_S and it ripens (the click arrives with `held`, the seconds held), paying GREEN_VALUE;
  left alone it goes brown instead, and GREEN_TTL_S after landing it's rotten and gone (nothing lost). A
  vine banana hangs on a vine (`path`, VINE_POINTS spots along a curve, `_vine()`): the bettor drags it along the vine
  to the far end, and the click arrives with `trail`, where the pointer was and when (ms since the press) as the
  banana passed each spot of the path, which `_slid()` checks; it pays VINE_VALUE. Neither can be caught in the air,
  and letting go early or slipping off costs nothing. A volley sometimes (VOLLEY_VINE_CHANCE) ends with a vine
  banana: thrown sixth, straight after the steel ones, it lands close to the last of them (VINE_NEAR) and is on the
  field with them (the volley's `vine`), to be dragged before the volley's clock, VOLLEY_VINE_TTL_S longer, runs out.
- Onkey's bongos: now and then he throws his pair of bongos instead of a banana (a target of kind "bongos"). Each drum
  has a meter of its own that every tap on it fills by BONGO_GAIN and that drains BONGO_DRAIN a second between taps,
  so only quick tapping fills it; a full drum stays full. The page runs the meters and, when both are full, sends the
  taps (`taps`: [drum, ms since the first] each), which `_drummed()` plays back the same way; it pays BONGO_VALUE.
- The boss fight, also from the lore: BOSS_CHANCE of throws aren't a banana but the scientist coming for Onkey himself
  (a target of kind "boss"). Onkey goes to the middle of the field and the claws come in for him from every side, in BOSS_WAVES waves, each
  (how many claws, how many seconds they take to reach him); a click names a claw (`claw`, its id) and stops it. Stop a whole wave in time and the next
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

Playing on: once nothing more can be earned today, the page can still ask to keep playing (`free` on `start()`,
`click()` and `nudge()`). The game goes on as before and the combo still counts, but nothing is paid, taken or
recorded: no credits, no corn cost, no boss prize, no hidden item, nothing in `hunt_days`. `free` means nothing while
there are still credits to earn, so it can't be used to dodge anything.

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
# More to throw (chances per throw, after the ones above).
CORN_CHANCE, FROZEN_CHANCE, BOUNCE_CHANCE, SPLIT_CHANCE, VOLLEY_CHANCE = 0.08, 0.06, 0.05, 0.05, 0.06
CORN_TTL_S = 2.0  # how long an ear of corn lies there before the next throw
CORN_COST = 3  # credits a picked ear of corn takes back (never more than the bettor has), with the combo
FROZEN_VALUE = 2  # a frozen banana: the first click cracks the ice, the second picks it
BOUNCE_VALUE, BOUNCE_S, BOUNCE_HOPS, BOUNCE_GAP = 3, 1.2, 3, 150  # it hops BOUNCE_HOPS times, BOUNCE_S apart, then stays
SPLIT_SIZE, SPLIT_BONUS, SPLIT_R = 3, 1, 110  # the pieces land about SPLIT_R from where it was
VOLLEY_SIZE, VOLLEY_GAP_S, VOLLEY_TTL_S, VOLLEY_BONUS, VOLLEY_STEP = 5, 0.35, 1.25, 5, 120
VOLLEY_EARLY_S = 0.1  # a volley's steel bananas can't be picked in the air: this much before one lands is the soonest
# Onkey's corner: nothing lands on him (within ONKEY_R of ONKEY_AT, about the middle of his picture), where it's hard to see.
ONKEY_AT, ONKEY_R = (FIELD_W - 76, FIELD_H - 70), 110
# Hold and drag. A green banana ripens when held for HOLD_S (the page may report HOLD_EARLY_S less), and left alone
# goes brown and is gone GREEN_TTL_S after it lands. A vine banana is
# dragged along a curve of VINE_POINTS spots, VINE_MIN_LEN to VINE_MAX_LEN from end to end, bowed by up to VINE_BOW
# of that and swaying VINE_SWAY to either side of it as a vine does; the pointer has to pass within VINE_R of each
# spot in turn. Both are checked against the server's own
# clock too: neither can be done sooner after the throw than the flight and the hold or drag itself (which is counted
# as VINE_MIN_S at least) take, less GRIP_SLACK_S.
GREEN_CHANCE, VINE_CHANCE = 0.05, 0.05
GREEN_VALUE, HOLD_S, HOLD_EARLY_S, GREEN_TTL_S = 3, 0.8, 0.05, 4.0
VINE_VALUE, VINE_POINTS, VINE_R, VINE_MIN_S = 4, 25, 80, 0.2
VINE_MIN_LEN, VINE_MAX_LEN, VINE_BOW, VINE_SWAY = 280, 440, 0.35, 14
GRIP_SLACK_S = 0.15
# Onkey's bongos: two drums, each with a meter a tap fills by BONGO_GAIN and that drains BONGO_DRAIN a second between
# taps on it (a full one stays full). Taps closer together than BONGO_MIN_GAP_MS aren't a hand's; they land with their
# middle at least BONGO_EDGE inside the field, so both drums fit however the page lays them out.
BONGO_CHANCE = 0.05
BONGO_VALUE, BONGO_GAIN, BONGO_DRAIN, BONGO_MIN_GAP_MS, BONGO_MAX_TAPS, BONGO_EDGE, BONGO_R = 6, 0.125, 0.3, 20, 400, 110, 110
VOLLEY_VINE_CHANCE = 0.5  # this share of volleys end with a vine banana, thrown straight after the steel ones...
VINE_NEAR = (60, 110)  # ...which lands this far (px, from and to) from the volley's last banana...
VOLLEY_VINE_TTL_S = 2.0  # ...and gives the volley this much longer on its clock
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
                "corn": {"cost": CORN_COST, "ttl_s": CORN_TTL_S}, "frozen": {"value": FROZEN_VALUE},
                "bounce": {"value": BOUNCE_VALUE, "hop_s": BOUNCE_S, "hops": BOUNCE_HOPS},
                "split": {"size": SPLIT_SIZE, "bonus": SPLIT_BONUS},
                "volley": {"size": VOLLEY_SIZE, "gap_s": VOLLEY_GAP_S, "ttl_s": VOLLEY_TTL_S, "bonus": VOLLEY_BONUS,
                           "vine_ttl_s": VOLLEY_VINE_TTL_S},
                "bongos": {"value": BONGO_VALUE, "gain": BONGO_GAIN, "drain": BONGO_DRAIN, "min_gap_ms": BONGO_MIN_GAP_MS},
                "green": {"value": GREEN_VALUE, "hold_s": HOLD_S, "ttl_s": GREEN_TTL_S}, "vine": {"value": VINE_VALUE, "r": VINE_R},
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
    @staticmethod
    def _on_onkey(x, y):
        return (x - ONKEY_AT[0]) ** 2 + (y - ONKEY_AT[1]) ** 2 < ONKEY_R ** 2

    def _spot(self, away=(), gap=MIN_HOP):
        """Somewhere in the field, off Onkey's corner and at least `gap` from every point in `away` when there's room
        to be."""
        for _ in range(40):
            x = self.rng.randint(MARGIN, FIELD_W - MARGIN)
            y = self.rng.randint(MARGIN, FIELD_H - MARGIN)
            if not self._on_onkey(x, y) and all((x - p["x"]) ** 2 + (y - p["y"]) ** 2 >= gap ** 2 for p in away):
                break
        return {"x": x, "y": y}

    def _new_target(self, prev=None, now=0.0, paid_ts=0.0):
        """What Onkey throws next. A target is {"id", "kind" (banana / golden / bunch / frozen / bouncy / split / volley /
        green / vine / bongos / boss), "x", "y", "born" (when it was thrown), "paid_ts" (the last paid pick), and by kind:
        "expires" (golden, bunch, volley), "items" (bunch, volley: each {"x", "y", "picked"}), "vine" (volley: {"path", "done"},
        the vine banana thrown after its steel ones, when there is one), "hops" (bouncy: where it
        goes next), "path" (vine: the spots to drag it through, the first its own), "decoy" (a rotten banana's spot,
        with its "kind"), "greg" ({"x", "y": where he starts, "arrives"})}."""
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
            t["decoy"] = {**self._spot([spot], DECOY_GAP), "kind": "rotten"}
        elif roll < GOLD_CHANCE + BUNCH_CHANCE + ROTTEN_CHANCE + GREG_CHANCE:
            # Greg comes in from the side further from the banana, level with it or thereabouts.
            gx = 0 if spot["x"] > FIELD_W / 2 else FIELD_W
            gy = min(FIELD_H - MARGIN, max(MARGIN, spot["y"] + self.rng.randint(-120, 120)))
            t["greg"] = {"x": gx, "y": gy, "arrives": now + AIR_S + GREG_S + SLACK_S}
        elif roll < GOLD_CHANCE + BUNCH_CHANCE + ROTTEN_CHANCE + GREG_CHANCE + BOSS_CHANCE:
            t.update(kind="boss", boss=self._wave(0, now, BOSS_INTRO_S))  # no banana: the scientist comes for Onkey
        elif roll < GOLD_CHANCE + BUNCH_CHANCE + ROTTEN_CHANCE + GREG_CHANCE + BOSS_CHANCE + STRUDEL_CHANCE:
            t["strudel"] = {"x": 0 if spot["x"] > FIELD_W / 2 else FIELD_W, "y": spot["y"]}  # a visit: he takes nothing
        else:
            roll -= GOLD_CHANCE + BUNCH_CHANCE + ROTTEN_CHANCE + GREG_CHANCE + BOSS_CHANCE + STRUDEL_CHANCE
            if roll < CORN_CHANCE:
                t.update(kind="corn", expires=now + AIR_S + CORN_TTL_S + SLACK_S)  # thrown alone, no banana with it
            elif roll < CORN_CHANCE + FROZEN_CHANCE:
                t.update(kind="frozen", cracked=False)
            elif roll < CORN_CHANCE + FROZEN_CHANCE + BOUNCE_CHANCE:
                hops = [spot]
                while len(hops) <= BOUNCE_HOPS:
                    hops.append(self._spot(hops, BOUNCE_GAP))
                t.update(kind="bouncy", hops=hops)
            elif roll < CORN_CHANCE + FROZEN_CHANCE + BOUNCE_CHANCE + SPLIT_CHANCE:
                t["kind"] = "split"
            elif roll < CORN_CHANCE + FROZEN_CHANCE + BOUNCE_CHANCE + SPLIT_CHANCE + VOLLEY_CHANCE:
                items = self._row(spot)
                t.update(kind="volley", x=items[0]["x"], y=items[0]["y"], items=[{**i, "picked": False} for i in items],
                         expires=now + (VOLLEY_SIZE - 1) * VOLLEY_GAP_S + AIR_S + VOLLEY_TTL_S + SLACK_S)
                if self.rng.random() < VOLLEY_VINE_CHANCE:  # a vine banana on the end of it, by where the last one lands
                    t["vine"] = {"path": self._vine(self._near(items[-1])), "done": False}
                    t["expires"] += VOLLEY_GAP_S + VOLLEY_VINE_TTL_S
            elif roll < CORN_CHANCE + FROZEN_CHANCE + BOUNCE_CHANCE + SPLIT_CHANCE + VOLLEY_CHANCE + GREEN_CHANCE:
                t.update(kind="green", expires=now + AIR_S + GREEN_TTL_S + SLACK_S)  # not ripe: held, not clicked, or it goes brown
            elif roll < CORN_CHANCE + FROZEN_CHANCE + BOUNCE_CHANCE + SPLIT_CHANCE + VOLLEY_CHANCE + GREEN_CHANCE + VINE_CHANCE:
                t.update(kind="vine", path=self._vine(spot))  # on a vine: it's dragged to the far end
            elif roll < (CORN_CHANCE + FROZEN_CHANCE + BOUNCE_CHANCE + SPLIT_CHANCE + VOLLEY_CHANCE + GREEN_CHANCE + VINE_CHANCE
                         + BONGO_CHANCE):
                t.update(kind="bongos", **self._roomy(prev))  # Onkey's bongos: tapped fast, both drums
        return t

    def _roomy(self, prev=None):
        """A spot for the bongos: BONGO_EDGE or more inside the field, clear of Onkey, and away from where the last
        target was when there's room."""
        for _ in range(40):
            x, y = self.rng.randint(BONGO_EDGE, FIELD_W - BONGO_EDGE), self.rng.randint(BONGO_EDGE, FIELD_H - BONGO_EDGE)
            if math.hypot(x - ONKEY_AT[0], y - ONKEY_AT[1]) >= ONKEY_R + BONGO_R and (
                    not prev or math.hypot(x - prev["x"], y - prev["y"]) >= MIN_HOP):
                return {"x": x, "y": y}
        return {"x": FIELD_W // 2, "y": FIELD_H // 2}

    @staticmethod
    def _drummed(t, taps, now):
        """Whether `taps` ([drum (0 or 1), ms since the first tap] each, in order) fill both of the bongos' meters, played
        back as the page plays them: a tap adds BONGO_GAIN to its drum's meter, which has drained BONGO_DRAIN a second
        since that drum's last tap; a full drum stays full. No two taps closer than BONGO_MIN_GAP_MS, and none of it
        sooner after the throw than the flight and the drumming take."""
        if not isinstance(taps, list) or not 2 <= len(taps) <= BONGO_MAX_TAPS:
            return False
        meter, last, prev = [0.0, 0.0], [None, None], None
        for tap in taps:
            if not isinstance(tap, (list, tuple)) or len(tap) != 2 or tap[0] not in (0, 1) or isinstance(tap[0], bool):
                return False
            try:
                drum, ms = int(tap[0]), float(tap[1])
            except (TypeError, ValueError):
                return False
            if not ms >= (0 if prev is None else prev + BONGO_MIN_GAP_MS):  # NaN fails too
                return False
            prev = ms
            if meter[drum] >= 1 - 1e-9:
                continue  # it's full already
            if last[drum] is not None:
                meter[drum] = max(0.0, meter[drum] - BONGO_DRAIN * (ms - last[drum]) / 1000)
            meter[drum] += BONGO_GAIN
            last[drum] = ms
        return min(meter) >= 1 - 1e-9 and prev / 1000 <= now - t["born"] - AIR_S + GRIP_SLACK_S

    def _near(self, p):
        """Somewhere VINE_NEAR from `p`, in the field and off Onkey (`p`'s own spot when there's no room)."""
        for _ in range(40):
            turn, far = math.radians(self.rng.randint(0, 359)), self.rng.randint(*VINE_NEAR)
            x, y = round(p["x"] + far * math.cos(turn)), round(p["y"] + far * math.sin(turn))
            if MARGIN <= x <= FIELD_W - MARGIN and MARGIN <= y <= FIELD_H - MARGIN and not self._on_onkey(x, y):
                return {"x": x, "y": y}
        return {"x": p["x"], "y": p["y"]}

    def _vine(self, start):
        """The vine a banana at `start` hangs on: VINE_POINTS spots along a curve (a quadratic Bezier) from it to an end
        VINE_MIN_LEN to VINE_MAX_LEN away, bowed to one side and swaying from side to side along the way, all of it in
        the field and none of it on Onkey."""
        inside = lambda p: MARGIN <= p["x"] <= FIELD_W - MARGIN and MARGIN <= p["y"] <= FIELD_H - MARGIN  # noqa: E731
        x0, y0 = start["x"], start["y"]

        def curve(x1, y1, x2, y2, sway=0.0, bends=0.0):
            # From the start, bent toward (x1, y1), to (x2, y2), and `sway` to either side of that `bends` times over
            # (whole or half bends, so both ends stay where they are).
            reach = math.hypot(x2 - x0, y2 - y0) or 1.0
            nx, ny = -(y2 - y0) / reach, (x2 - x0) / reach  # across the straight line between the ends
            out = []
            for i in range(VINE_POINTS):
                k = i / (VINE_POINTS - 1)
                off = sway * math.sin(2 * math.pi * bends * k)
                out.append({"x": round((1 - k) ** 2 * x0 + 2 * k * (1 - k) * x1 + k * k * x2 + nx * off),
                            "y": round((1 - k) ** 2 * y0 + 2 * k * (1 - k) * y1 + k * k * y2 + ny * off)})
            return out

        for _ in range(60):
            heading = math.radians(self.rng.randint(0, 359))
            length = self.rng.randint(VINE_MIN_LEN, VINE_MAX_LEN)
            bow = length * self.rng.randint(-100, 100) / 100 * VINE_BOW
            x2, y2 = x0 + length * math.cos(heading), y0 + length * math.sin(heading)
            # The curve's control point: off the middle of the straight line, to one side.
            x1, y1 = (x0 + x2) / 2 - bow * math.sin(heading), (y0 + y2) / 2 + bow * math.cos(heading)
            path = curve(x1, y1, x2, y2, VINE_SWAY * (self.rng.randint(0, 1) * 2 - 1), self.rng.randint(2, 4) / 2)
            if all(inside(p) for p in path) and not any(self._on_onkey(p["x"], p["y"]) for p in path[1:]):
                return path
        x2 = x0 + (300 if x0 < FIELD_W / 2 else -300)  # no room found from there: straight across, toward the middle
        return curve((x0 + x2) / 2, y0, x2, y0)

    @staticmethod
    def _slid(t, trail, now):
        """Whether `trail` is a drag of a vine banana all the way along its vine: one [x, y, ms since the press] per spot
        of the path, in order, each within VINE_R of its spot, the times never going back, and none of it sooner
        after the throw than the flight and the drag (VINE_MIN_S at least) take, by the server's own clock."""
        path = t["path"]
        if not isinstance(trail, list) or len(trail) != len(path):
            return False
        last = 0.0
        for spot, sample in zip(path, trail):
            if not isinstance(sample, (list, tuple)) or len(sample) != 3:
                return False
            try:
                x, y, ms = (float(v) for v in sample)
            except (TypeError, ValueError):
                return False
            if not ms >= last or not (x - spot["x"]) ** 2 + (y - spot["y"]) ** 2 <= VINE_R ** 2:  # NaN fails both
                return False
            last = ms
        return max(VINE_MIN_S, last / 1000) <= now - t["born"] - AIR_S + GRIP_SLACK_S

    def _row(self, start):
        """VOLLEY_SIZE spots VOLLEY_STEP apart for a volley, from `start`: along a straight line, or bending into an arc,
        in whichever direction keeps them all in the field."""
        low_x, high_x, low_y, high_y = MARGIN, FIELD_W - MARGIN, MARGIN, FIELD_H - MARGIN
        for _ in range(60):
            heading = math.radians(self.rng.randint(0, 359))
            bend = math.radians(self.rng.randint(-1, 1) * 22)  # 0: a line; either side: an arc
            pts, x, y = [], float(start["x"]), float(start["y"])
            for _i in range(VOLLEY_SIZE):
                pts.append({"x": round(x), "y": round(y)})
                x, y, heading = x + VOLLEY_STEP * math.cos(heading), y + VOLLEY_STEP * math.sin(heading), heading + bend
            if all(low_x <= p["x"] <= high_x and low_y <= p["y"] <= high_y and not self._on_onkey(p["x"], p["y"]) for p in pts):
                return pts
        y = min(high_y, max(low_y, start["y"]))  # no room from there: straight across the middle
        return [{"x": FIELD_W // 2 + (i - VOLLEY_SIZE // 2) * VOLLEY_STEP, "y": y} for i in range(VOLLEY_SIZE)]

    def _pieces(self, t):
        """Where a split banana's pieces land: SPLIT_SIZE spots about SPLIT_R from it, kept inside the field."""
        turn = self.rng.randint(0, 359)
        out = []
        for i in range(SPLIT_SIZE):
            a = math.radians(turn + i * 360 / SPLIT_SIZE)
            out.append({"x": round(min(FIELD_W - MARGIN, max(MARGIN, t["x"] + SPLIT_R * math.cos(a)))),
                        "y": round(min(FIELD_H - MARGIN, max(MARGIN, t["y"] + SPLIT_R * math.sin(a)))), "picked": False})
        return out

    def _wave(self, n, now, wait):
        """Wave `n` of the boss fight: its claws ({"id", "x", "y": where on the field's border each comes in from, on any
        of the four sides, "hit"}), how long the page waits before they move (`wait`) and how long they take to reach
        Onkey in the middle (`secs`), and the server's own deadline."""
        count, secs = BOSS_WAVES[n]
        return {"wave": n, "wait": wait, "secs": secs, "begins": now + wait, "deadline": now + wait + secs + SLACK_S,
                "claws": [{"id": i, **self._edge(), "hit": False} for i in range(count)]}

    def _edge(self):
        """A point on the field's border: a side picked at random, then somewhere along it."""
        side = self.rng.randint(0, 3)
        if side in (0, 2):  # top, bottom
            return {"x": self.rng.randint(MARGIN, FIELD_W - MARGIN), "y": 0 if side == 0 else FIELD_H}
        return {"x": FIELD_W if side == 1 else 0, "y": self.rng.randint(MARGIN, FIELD_H - MARGIN)}

    def _combo(self, key, now):
        c = self.combos.setdefault(key, {"n": 0, "ts": 0.0, "frozen": 0.0})
        if c["n"] and now - c["ts"] > COMBO_IDLE_S:
            c["n"] = 0
        return c

    @staticmethod
    def _mult(n):
        return 1 + sum(n >= step for step in COMBO_STEPS)

    def _lapse(self, key, t, now):
        """What became of a target left too long: 'rotted' (a golden banana, or a green one gone brown), 'bunch_over', 'stolen' (Greg got there),
        'boss_lost' (a claw of the boss fight reached Onkey), or None while it's still good. A lapsed target is
        replaced."""
        why = None
        if t.get("expires") and now > t["expires"]:
            why = {"golden": "rotted", "green": "rotted", "corn": "corn_gone"}.get(t["kind"], "bunch_over")
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
        if t["kind"] in ("bunch", "volley"):
            out["items"] = [{"x": i["x"], "y": i["y"], "picked": i["picked"]} for i in t["items"]]
        if t.get("vine"):
            out["vine"] = {"path": [dict(p) for p in t["vine"]["path"]], "done": t["vine"]["done"]}
        if t["kind"] == "bunch" and "bonus" in t:
            out["pieces"] = True  # a split banana's pieces: they appear where it was, Onkey doesn't throw them
        if t["kind"] == "frozen":
            out["cracked"] = t["cracked"]
        if t["kind"] == "bouncy":
            out["hops"] = [dict(h) for h in t["hops"]]
        if t["kind"] == "vine":
            out["path"] = [dict(p) for p in t["path"]]
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
    def status(self, name, now=None, free=False):
        """The bettor's hunt: today's credits and what's left (and whether only the floor allows it), their totals
        and combo, and the banana (None once they're done). `free`: they're playing on past the cap for nothing, so
        there's still a banana, and `done` (the hunt is closed) is false."""
        now = now or time.time()
        bettor = self.db.get_bettor(name)
        balance = bettor["balance"] if bettor else 0.0
        picks, credits = self._day(name, now)
        left, under = self._room(credits, balance)
        totals = self.db.hunt_totals().get(name.lower()) or {}
        free = bool(free) and left == 0
        t = self.targets.get(name.lower()) if left or free else None
        combo = self._combo(name.lower(), now)
        return {"name": name, "today": int(round(credits)), "picks": picks, "left": left, "done": left == 0 and not free, "free": free,
                "under_floor": under, "floor": self.floor,
                "balance": round(balance, 2), "day": wheel_day(now),
                "resets_ts": next_reset(now), "season": round(totals.get("season") or 0, 2),
                "all_time": round(totals.get("all_time") or 0, 2), "bananas": int(totals.get("bananas") or 0),
                "combo": combo["n"], "mult": self._mult(combo["n"]),
                "target": self._public(t)}

    def start(self, name, now=None, free=False):
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
        if self._room(self._day(name, now)[1], bettor["balance"])[0] > 0 or free:
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
        return self.status(name, now, free)

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
    def click(self, name, x, y, now=None, air=None, shoo=False, claw=None, held=None, trail=None, free=False, taps=None):
        """Judge a click at (x, y) in field pixels. A hit pays and moves the banana; the reply carries the banana to
        draw next (None once the day's cap is reached), today's credits and what's left, and the combo. `air`: the
        click was on the banana in flight, this far along its arc (0-1). `shoo`: the click was on Greg. `claw`: the
        click was on that claw of the boss fight (its id). `held`: the button was held down on the spot this many
        seconds (a green banana). `trail`: the drag that ended at (x, y), for a vine banana (see `_slid()`). `taps`: the taps that filled Onkey's
        bongos (see `_drummed()`). `free`:
        past what can be earned today, play on for nothing (the reply says `free`, and `paid` is 0)."""
        bettor = self.db.get_bettor(name)
        if not bettor:
            raise BetError("Sign in as a bettor first.")
        if not self.enabled:
            raise BetError("The hunt is closed.")
        try:
            x, y = float(x), float(y)
            air = None if air is None else float(air)
            held = None if held is None else float(held)
        except (TypeError, ValueError):
            raise BetError("Where did you click?")
        if x != x or y != y or (air is not None and air != air) or (held is not None and held != held):  # NaN
            raise BetError("Where did you click?")
        now = now or time.time()
        name = bettor["name"]
        key = name.lower()
        picks, credits = self._day(name, now)
        left, under = self._room(credits, bettor["balance"])
        free = bool(free) and left <= 0  # nothing left to earn today, and they're playing on for nothing
        if left <= 0 and not free:
            return {"hit": False, "reason": "done", "today": int(round(credits)), "left": 0, "done": True, "under_floor": False,
                    "target": None, "combo": 0, "mult": 1}
        # A hold that began before a green banana went brown still counts: its length is taken off the clock.
        held_on = self.targets.get(key) if held is not None else None
        grace = min(HOLD_S + GRIP_SLACK_S, max(0.0, held)) if held_on and held_on["kind"] == "green" else 0.0
        t, lapsed = self._target(name, now, early=-grace)
        combo = self._combo(key, now)

        def reply(hit, reason=None, **more):
            out = {"hit": hit, "today": int(round(credits)), "left": left, "done": left == 0 and not free, "under_floor": under,
                   "target": self._public(self.targets.get(key)), "combo": combo["n"], "mult": self._mult(combo["n"]), **more}
            if free:
                out["free"] = True
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
            if not free:
                self.db.hunt_bonus(name, wheel_day(now), BOSS_PRIZE, now)  # every wave stopped: paid on top of the cap
            with self.lock:
                self.targets[key] = self._new_target(t, now, now)
            return reply(True, paid=0 if free else BOSS_PRIZE, kind="boss", air=False, swept=False, bunch_bonus=0, found=None)
        if now < combo["frozen"]:
            return reply(False, "frozen", frozen_s=round(combo["frozen"] - now, 2))
        if shoo:
            if t.get("greg"):
                with self.lock:
                    t.pop("greg", None)
                return reply(False, "shooed")
            return reply(False, "miss")  # nobody there: not a miss that breaks the combo
        near = lambda p, r=TARGET_R: (x - p["x"]) ** 2 + (y - p["y"]) ** 2 <= r ** 2  # noqa: E731
        if t["kind"] == "corn":  # corn, in the air or down: it costs credits (what the bettor has, at most) and the combo
            if air is not None:
                ax, ay = arc_at(t["x"], t["y"], min(1.0, max(0.0, air)))
                on = AIR_FROM <= air <= AIR_TO and near({"x": ax, "y": ay}, AIR_R)
            else:
                on = near(t)
            if not on:
                combo["n"] = 0
                return reply(False, "miss")
            cost = 0 if free else min(CORN_COST, max(0, int(bettor["balance"])))
            if cost:
                self.db.hunt_bonus(name, wheel_day(now), -cost, now)
            with self.lock:
                combo["n"] = 0
                self.targets[key] = self._new_target(t, now, t["paid_ts"])
            return reply(False, "corn", lost=cost)
        if t.get("decoy") and air is None and near(t["decoy"]):
            with self.lock:
                combo["n"], combo["frozen"] = 0, now + FREEZE_S
                self.targets[key] = self._new_target(t, now, t["paid_ts"])
            return reply(False, "rotten", frozen_s=FREEZE_S)

        value, item, dragged = CREDIT_PER_BANANA, None, False  # dragged: this was a volley's vine banana
        if t["kind"] in ("green", "vine", "bongos") and air is not None:
            return reply(False, "too_fast")  # none can be caught: one has to ripen, one to be dragged, one to be played
        if t["kind"] == "bongos":  # both meters filled by quick tapping: a click alone does nothing
            if taps is None:
                if near(t, BONGO_R):
                    return reply(False, "bongo")  # on them, but no drumming came with it: nothing lost
                combo["n"] = 0
                return reply(False, "miss")
            if not self._drummed(t, taps, now):
                return reply(False, "offbeat")
        elif t["kind"] == "vine":  # dragged along its vine to the far end: a click alone doesn't pick it
            if trail is None:
                if near(t):
                    return reply(False, "vine")  # on it, but not dragged: nothing lost
                combo["n"] = 0
                return reply(False, "miss")
            if not self._slid(t, trail, now) or not near(t["path"][-1], VINE_R):
                return reply(False, "slipped")
        elif t["kind"] == "bunch":
            item = next((i for i in t["items"] if not i["picked"] and near(i)), None)
            if item is None:
                combo["n"] = 0
                return reply(False, "miss")
            if now - t["paid_ts"] < BUNCH_INTERVAL_S:
                return reply(False, "too_fast")
        elif t["kind"] == "volley":  # steel bananas, each thrown VOLLEY_GAP_S after the one before: only once it's down
            if air is not None:
                return reply(False, "too_fast")  # nothing to catch: a click on one in the air costs nothing
            tail = t.get("vine") if t.get("vine") and not t["vine"]["done"] else None  # the vine banana on the end of it
            if trail is not None:  # a drag: of that vine banana, thrown after the steel ones
                thrown = {"path": tail["path"], "born": t["born"] + VOLLEY_SIZE * VOLLEY_GAP_S} if tail else None
                if not thrown or not self._slid(thrown, trail, now) or not near(tail["path"][-1], VINE_R):
                    return reply(False, "slipped")
                tail["done"], dragged, value = True, True, VINE_VALUE
            else:
                n, item = next(((n, i) for n, i in enumerate(t["items"]) if not i["picked"] and near(i)), (0, None))
                if item is None:
                    if tail and near(tail["path"][0]):
                        return reply(False, "vine")  # on the vine banana, but not dragged: nothing lost
                    combo["n"] = 0
                    return reply(False, "miss")
                if now - (t["born"] + n * VOLLEY_GAP_S) < AIR_S - VOLLEY_EARLY_S or now - t["paid_ts"] < BUNCH_INTERVAL_S:
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
            spots = [t]
            if t["kind"] == "bouncy":  # wherever it is now, or was a moment ago on the page (which runs a little behind)
                landed = now - t["born"] - AIR_S
                last = len(t["hops"]) - 1
                spots = t["hops"][max(0, min(last, int((landed - SLACK_S * 1.25) // BOUNCE_S))):max(0, min(last, int(landed // BOUNCE_S))) + 1]
            if not any(near(s) for s in spots):
                combo["n"] = 0
                return reply(False, "miss")
        if t["kind"] == "golden":
            value *= GOLD_VALUE
        elif t["kind"] == "bouncy":
            value *= BOUNCE_VALUE
        elif t["kind"] == "green":  # held until it ripens: a click, or letting go early, doesn't pick it
            if held is None or held < HOLD_S - HOLD_EARLY_S or now - t["born"] < AIR_S + HOLD_S - GRIP_SLACK_S:
                return reply(False, "unripe")
            value *= GREEN_VALUE
        elif t["kind"] == "vine":
            value *= VINE_VALUE
        elif t["kind"] == "bongos":
            value *= BONGO_VALUE
        elif t["kind"] == "frozen":
            if not t["cracked"]:  # the ice first: nothing paid, nothing lost
                with self.lock:
                    t["cracked"] = True
                return reply(False, "cracked")
            value *= FROZEN_VALUE
        elif t["kind"] == "split":  # it breaks into pieces where it is: a small bunch to sweep
            with self.lock:
                t.update(kind="bunch", items=self._pieces(t), bonus=SPLIT_BONUS, expires=now + BUNCH_TTL_S + SLACK_S)
                t.pop("decoy", None)
            return reply(False, "split")

        # A hit. The combo counts it, then multiplies it; the bunch's bonus comes on top. Never past what's left.
        combo["n"], combo["ts"] = combo["n"] + 1, now
        mult = self._mult(combo["n"])
        swept = False
        if item is not None:
            item["picked"] = True
            swept = all(i["picked"] for i in t["items"])
        # Whether that was the last of it: a volley with a vine banana needs both the bananas and the drag.
        over = not dragged and (item is None or swept)
        if t["kind"] == "volley" and t.get("vine"):
            over = t["vine"]["done"] and all(i["picked"] for i in t["items"])
        bonus = 0
        if swept:  # a bunch's (or a split banana's) bonus, or a volley's
            bonus = VOLLEY_BONUS if t["kind"] == "volley" else t.get("bonus", BUNCH_BONUS)
        pay, found = min(value * mult + bonus, left), None
        if not free:  # playing on for nothing leaves no trace: no credits, no pick counted, no hidden item
            self.db.hunt_pay(name, wheel_day(now), pay, now)
            picks, credits = picks + 1, credits + pay
            found = self._found(name, wheel_day(now), picks, now)
            left, under = self._room(credits, bettor["balance"] + pay)
        with self.lock:
            t["paid_ts"] = now
            if left <= 0 and not free:
                self.targets.pop(key, None)
            elif over:
                self.targets[key] = self._new_target(t, now, now)
        return reply(True, paid=pay, kind="vine" if dragged else t["kind"], air=air is not None, swept=swept, bunch_bonus=bonus, found=found)

    def nudge(self, name, now=None, free=False):
        """The page's timer ran out (a golden banana rotted, a bunch's time is up, Greg arrived): replace the target
        if the server agrees, and say what happened. Never swaps a target that's still good."""
        bettor = self.db.get_bettor(name)
        if not bettor:
            raise BetError("Sign in as a bettor first.")
        now = now or time.time()
        name = bettor["name"]
        picks, credits = self._day(name, now)
        left, under = self._room(credits, bettor["balance"])
        free = bool(free) and left <= 0
        if left <= 0 and not free:
            return {"reason": "done", "today": int(round(credits)), "left": 0, "done": True, "under_floor": False, "target": None,
                    "combo": 0, "mult": 1}
        t, lapsed = self._target(name, now, early=SLACK_S + 0.2)
        combo = self._combo(name.lower(), now)
        return {"reason": lapsed, "today": int(round(credits)), "left": left, "done": False, "free": free, "under_floor": under,
                "target": self._public(t), "combo": combo["n"], "mult": self._mult(combo["n"])}
