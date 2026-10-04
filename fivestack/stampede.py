"""Onkey Stampede: the Casino's 5 x 4 video slot, next to the classic machine in slots.py.

A spin shows four symbols on each of five reels. Wins pay "ways": a symbol on reels 1, 2 and 3 (and on, without a
gap), anywhere in each reel's four rows, pays its PAYS entry for that many reels times the ways it makes (the product
of how many times it shows on each of those reels), 1,024 ways at most. The Bongo Onkey (WILD, never on reel 1) stands
in for every symbol that pays ways. Spikes (SPIKE) are scatters: 3 or more anywhere pay SCATTER_PAYS and start free
spins. Golden fireballs (FIRE) carry credits (VALUES, a multiple of the stake); 6 or more start hold and spin. Every
fireball that lands also fills the spinner's own fire meter, and a full meter starts the jackpot pick.

Base spins have events (EVENTS, more below): before the reels stop, a Stampede drops wilds on reels 2-5 (always enough
for a win) or Banana rain turns some symbols into fireballs; after a ways win, Onkey's Inferno can multiply it.

Free spins use their own reels (FS_STRIPS): more wilds, and each wild carries x2 or x3 (FS_WILD_MULTS); a way's wilds
multiply each other. 3+ spikes add FS_RETRIGGER spins, never past FS_CAP in all.

Hold and spin: the fireballs stay where they landed and HS_RESPINS respins start. Each empty cell lands a fireball with
chance HS_LAND[filled] per respin; a new fireball resets the respins. It ends when the respins run out or every cell is
full, paying every fireball's value, and FULL_GRID_BONUS for a full grid.

The jackpots (JACKPOTS: Mini, Minor, Major, Grand) are shared by everyone and are credit amounts, the same for
everyone. Every spin grows each one by its `grow` x the stake. They're won only in the jackpot pick: every fireball
landed in a base spin adds the spin's stake to the player's own fire meter, so a bigger bet fills it faster, and at
METER_FULL credits it empties into a pick of fireballs (pick_game()) that ends on three of one jackpot or three smokes
(nothing), PICK_OUTCOMES deciding which; the jackpot won goes back to its seed. Picks come at the same rate per credit
staked at every stake, so over time a jackpot pays seed x how often it's won, plus `grow`, per credit staked, and
rtp() is exact whatever the pots show. A spin pays everything at once on the server; the page only plays it back.

Every chance here is exact: rtp() and breakdown() work from every reel position (the reels stop independently), so the
self-test can check the return. Nothing here touches the house's pot or the daily wheel's jackpot: each spin writes its
expected take (stake x (1 - rtp)) to house_ledger, which is what grows the wheel's jackpot.
"""
import json
import random
import secrets
import time
from functools import lru_cache

from . import house
from .bets import BetError
from .db import SCHEMA
from .wheel import wheel_day

STAKES = (2, 5, 10, 25, 50, 100)
REELS, ROWS = 5, 4
CELLS = REELS * ROWS

# Saved spins store these indexes: keep the order and add new symbols at the end.
SYMBOLS = [
    {"key": "nine", "name": "9", "tier": "low"},
    {"key": "ten", "name": "10", "tier": "low"},
    {"key": "jack", "name": "J", "tier": "low"},
    {"key": "queen", "name": "Q", "tier": "low"},
    {"key": "king", "name": "K", "tier": "low"},
    {"key": "ace", "name": "A", "tier": "low"},
    {"key": "coconut", "name": "Coconut", "tier": "mid"},
    {"key": "drum", "name": "Bongo drum", "tier": "mid"},
    {"key": "banana", "name": "Banana bunch", "tier": "high"},
    {"key": "valorant", "name": "Valorant", "tier": "high"},
    {"key": "greg", "name": "Greg", "tier": "high"},
    {"key": "golden", "name": "Golden Onkey", "tier": "top"},
    {"key": "wild", "name": "Bongo Onkey", "tier": "wild"},
    {"key": "spike", "name": "Spike", "tier": "scatter"},
    {"key": "fire", "name": "Golden fireball", "tier": "fire"},
]
(NINE, TEN, JACK, QUEEN, KING, ACE, COCONUT, DRUM, BANANA, VALORANT, GREG, GOLDEN, WILD, SPIKE,
 FIRE) = range(len(SYMBOLS))
PAYING = range(WILD)  # the symbols that pay ways

# Ways pays, in multiples of the stake for one way of 3, 4 or 5 reels. Flat on purpose: the reels stack their symbols
# (two or three in a row), so a win usually makes several ways and pays more than the stake.
PAYS = {
    NINE: (0.20, 0.40, 0.80), TEN: (0.20, 0.40, 0.80), JACK: (0.20, 0.45, 0.90), QUEEN: (0.20, 0.45, 0.90),
    KING: (0.25, 0.50, 1.10), ACE: (0.25, 0.50, 1.10), COCONUT: (0.30, 0.70, 1.60), DRUM: (0.40, 0.80, 1.80),
    BANANA: (0.45, 1.00, 2.50), VALORANT: (0.50, 1.25, 3.00), GREG: (0.65, 1.60, 4.00), GOLDEN: (0.80, 2.50, 8.00),
}
SCATTER_PAYS = {3: 1, 4: 5, 5: 25}  # spikes anywhere, in multiples of the stake
FS_AWARD = {3: 8, 4: 12, 5: 20}
FS_RETRIGGER = 5
FS_CAP = 50
FS_WILD_MULTS = ((2, 3), (3, 1))  # (multiplier, weight) for each wild in free spins
HS_TRIGGER = 6
HS_RESPINS = 3
# A fireball's value: (multiple of the stake, weight). Fireballs only ever pay credits; the jackpots come from the
# fire meter's pick.
VALUES = ((0.5, 300), (1, 300), (2, 170), (3, 100), (5, 70), (10, 35), (15, 15), (25, 10))
FULL_GRID_BONUS = 100  # x the stake, on top of the fireballs, for filling all 20 cells in hold and spin
# Each empty cell's chance (per HS_TICKETS) of landing a fireball on a respin, by how many cells are already filled:
# a bonus ends with about 12 fireballs, and about 1 in 785 fills the grid.
HS_TICKETS = 10_000
HS_LAND = tuple(600 if n < 16 else 450 for n in range(CELLS))
# The fire meter: every fireball that lands in a base spin adds the spin's stake to the spinner's own meter (kept
# between visits and seasons), so it fills faster the more you bet. At METER_FULL credits it empties into the jackpot
# pick: fifteen fireballs, cracked open one at a time until one jackpot shows three times, or three smokes end it with
# nothing. PICK_OUTCOMES decides which (per 1,000), before the first pick. At a 10-credit bet that's about every 250
# spins; at 100, every 25.
METER_FULL = 3000
PICK_KINDS = ("mini", "minor", "major", "grand", "smoke")
PICK_OUTCOMES = (("mini", 430), ("minor", 160), ("major", 55), ("grand", 5), (None, 350))
JACKPOTS = {  # the size a jackpot starts at (credits), and what it grows by per credit staked
    "mini": {"name": "Mini", "seed": 100, "grow": 0.002},
    "minor": {"name": "Minor", "seed": 250, "grow": 0.002},
    "major": {"name": "Major", "seed": 800, "grow": 0.003},
    "grand": {"name": "Grand", "seed": 5000, "grow": 0.004},
}
# Events on base spins. Before the reels stop (tickets out of EVENT_TICKETS): a Stampede puts wilds on reels 2 and 3
# (STAMPEDE_SURE: how many, so the spin always wins) and on reels 4 and 5 (each cell, STAMPEDE_Q per 1,000); Banana
# rain turns each cell that isn't a fireball into one (RAIN_Q per 1,000). After a spin with a ways win, Onkey's
# Inferno multiplies its ways wins (INFERNO_CHANCE per 1,000 winning spins, INFERNO_MULTS by weight).
EVENT_TICKETS = 1000
EVENTS = {"stampede": 12, "rain": 12}
STAMPEDE_SURE = ((1, 1),)  # (wilds on each of reels 2 and 3, weight)
STAMPEDE_Q = 80
RAIN_Q = 150
INFERNO_CHANCE = 137
INFERNO_MULTS = ((2, 6), (3, 3), (5, 1))

# Reel strips: (symbol, stack height, how many stacks). build_strip() shuffles them the same way every time. Low and
# mid symbols come in pairs, so a reel shows fewer different symbols and a win makes more ways.
_LOW = [(s, 2, 4) for s in (NINE, TEN, JACK, QUEEN, KING, ACE)] + [(COCONUT, 2, 3), (DRUM, 2, 3)]
_FS_LOW = [(s, 2, 3) for s in (NINE, TEN, JACK, QUEEN, KING, ACE)] + [(COCONUT, 2, 3), (DRUM, 2, 3)]


def _high(banana, valorant, greg, golden):
    return [(BANANA, 1, banana), (VALORANT, 1, valorant), (GREG, 1, greg), (GOLDEN, 2, golden)]


BASE_REELS = [
    _LOW + _high(4, 4, 4, 2) + [(SPIKE, 1, 1), (FIRE, 1, 3), (FIRE, 2, 1)],
    _LOW + _high(4, 4, 4, 2) + [(WILD, 1, 2), (SPIKE, 1, 2), (FIRE, 1, 3), (FIRE, 2, 1)],
    _LOW + _high(4, 4, 4, 2) + [(WILD, 1, 2), (SPIKE, 1, 2), (FIRE, 1, 2), (FIRE, 3, 1)],
    _LOW + _high(4, 4, 4, 2) + [(WILD, 1, 2), (SPIKE, 1, 2), (FIRE, 1, 3), (FIRE, 2, 1)],
    _LOW + _high(4, 4, 4, 2) + [(WILD, 1, 2), (SPIKE, 1, 2), (FIRE, 1, 3), (FIRE, 2, 1)],
]
FS_REELS = [
    _FS_LOW + _high(4, 4, 4, 2) + [(SPIKE, 1, 1)],
    _FS_LOW + _high(4, 4, 4, 2) + [(WILD, 1, 3), (SPIKE, 1, 1)],
    _FS_LOW + _high(4, 4, 4, 2) + [(WILD, 1, 2), (WILD, 3, 1), (SPIKE, 1, 1)],
    _FS_LOW + _high(4, 4, 4, 2) + [(WILD, 1, 3), (SPIKE, 1, 1)],
    _FS_LOW + _high(4, 4, 4, 2) + [(WILD, 1, 2), (SPIKE, 1, 1)],
]


def build_strip(blocks, seed):
    """A reel strip from its blocks, shuffled the same way every time, with spikes at least ROWS apart (so a reel
    never shows two) and no two stacks of the same symbol touching."""
    rng = random.Random(seed)
    items = [(sym, h) for sym, h, n in blocks for _ in range(n)]
    for _ in range(10_000):
        rng.shuffle(items)
        strip = [sym for sym, h in items for _ in range(h)]
        heads = [sym for sym, h in items]
        if any(heads[i] == heads[i - 1] and heads[i] in (WILD, FIRE, GOLDEN) for i in range(len(heads))):
            continue
        spikes = [i for i, s in enumerate(strip) if s == SPIKE]
        n = len(strip)
        if all(min((b - a) % n, (a - b) % n) >= ROWS for a in spikes for b in spikes if a != b):
            return strip
    raise AssertionError("no strip layout found")


BASE_STRIPS = [build_strip(b, 100 + i) for i, b in enumerate(BASE_REELS)]
FS_STRIPS = [build_strip(b, 200 + i) for i, b in enumerate(FS_REELS)]


def window(strip, stop):
    """The four symbols a reel shows when it stops at `stop`, top to bottom."""
    return [strip[(stop + r) % len(strip)] for r in range(ROWS)]


def pick(rb, weights):
    """An index drawn in proportion to `weights`, using rb(n) (a random integer below n)."""
    ticket = rb(sum(weights))
    for i, w in enumerate(weights):
        if ticket < w:
            return i
        ticket -= w
    raise AssertionError("unreachable")


# ---- evaluating a grid ------------------------------------------------------------------------------------------

def ways_wins(grid, wild_mults=None):
    """Every ways win on a grid (a list of five columns of four symbols). `wild_mults` maps (reel, row) to a wild's
    multiplier (free spins). Each win:
    {"symbol", "reels", "ways", "mult" (what it pays, x stake), "cells": [[reel, row], ...]}."""
    wild_mults = wild_mults or {}
    out = []
    for s in PAYING:
        sums, cells = [], []
        for c in range(REELS):
            hit = [(r, wild_mults.get((c, r), 1) if grid[c][r] == WILD else 1)
                   for r in range(ROWS) if grid[c][r] == s or grid[c][r] == WILD]
            if not hit:
                break
            sums.append(sum(m for _, m in hit))
            cells += [[c, r] for r, _ in hit]
        k = len(sums)
        if k < 3:
            continue
        ways = 1
        for v in sums:
            ways *= v
        mult = PAYS[s][k - 3] * ways
        out.append({"symbol": s, "reels": k, "ways": ways, "mult": round(mult, 4), "cells": cells})
    return out


def scatter_win(grid):
    cells = [[c, r] for c in range(REELS) for r in range(ROWS) if grid[c][r] == SPIKE]
    n = len(cells)
    return {"count": n, "mult": SCATTER_PAYS.get(min(n, 5), 0), "cells": cells} if n >= 3 else None


def fire_cells(grid):
    return [[c, r] for c in range(REELS) for r in range(ROWS) if grid[c][r] == FIRE]


def draw_value(rb):
    """A fireball's value, a multiple of the stake."""
    return VALUES[pick(rb, [w for _, w in VALUES])][0]


# ---- playing a spin ---------------------------------------------------------------------------------------------

def draw_event(rb):
    ticket = rb(EVENT_TICKETS)
    for kind, n in EVENTS.items():
        if ticket < n:
            return kind
        ticket -= n
    return None


def base_spin(rb):
    """One base spin: the event before the reels stop (if any), where they stop, what they landed on, and the grid
    after the event."""
    kind = draw_event(rb)
    stops = [rb(len(s)) for s in BASE_STRIPS]
    landed = [window(s, stop) for s, stop in zip(BASE_STRIPS, stops)]
    grid = [col[:] for col in landed]
    event = None
    if kind == "stampede":
        cells = []
        for c in (1, 2):
            k = STAMPEDE_SURE[pick(rb, [w for _, w in STAMPEDE_SURE])][0]
            cells += [[c, r] for r in sorted(_sample(rb, range(ROWS), k))]
        cells += [[c, r] for c in (3, 4) for r in range(ROWS) if rb(1000) < STAMPEDE_Q]
        for c, r in cells:
            grid[c][r] = WILD
        event = {"kind": "stampede", "cells": cells}
    elif kind == "rain":
        cells = [[c, r] for c in range(REELS) for r in range(ROWS) if grid[c][r] != FIRE and rb(1000) < RAIN_Q]
        for c, r in cells:
            grid[c][r] = FIRE
        event = {"kind": "rain", "cells": cells}
    return event, stops, landed, grid


def _sample(rb, items, k):
    items, out = list(items), []
    for _ in range(k):
        out.append(items.pop(rb(len(items))))
    return out


def free_spins(rb, awarded):
    spins, total, played = [], 0.0, 0
    while played < awarded:
        stops = [rb(len(s)) for s in FS_STRIPS]
        grid = [window(s, stop) for s, stop in zip(FS_STRIPS, stops)]
        mults = {(c, r): FS_WILD_MULTS[pick(rb, [w for _, w in FS_WILD_MULTS])][0]
                 for c in range(REELS) for r in range(ROWS) if grid[c][r] == WILD}
        wins = ways_wins(grid, mults)
        sc = scatter_win(grid)
        extra = min(FS_RETRIGGER, FS_CAP - awarded) if sc else 0
        awarded += extra
        mult = sum(w["mult"] for w in wins) + (sc["mult"] if sc else 0)
        total += mult
        played += 1
        spins.append({"stops": stops, "grid": grid, "wilds": [[c, r, m] for (c, r), m in sorted(mults.items())],
                      "wins": wins, "scatter": sc, "retrigger": extra, "mult": round(mult, 4)})
    return {"awarded": awarded, "spins": spins, "mult": round(total, 4)}


def hold_and_spin(rb, start, land=HS_LAND):
    """`start`: [[reel, row, value], ...] for the fireballs that triggered it. Only demo mode's forced full grid
    changes `land`. Pays every fireball's value, plus FULL_GRID_BONUS when all 20 cells fill."""
    held = {(c, r): v for c, r, v in start}
    respins, rounds = HS_RESPINS, []
    while respins and len(held) < CELLS:
        p = land[len(held)]
        new = [[c, r, draw_value(rb)] for c in range(REELS) for r in range(ROWS)
               if (c, r) not in held and rb(HS_TICKETS) < p]
        for c, r, v in new:
            held[(c, r)] = v
        respins = HS_RESPINS if new else respins - 1
        rounds.append({"new": new, "respins": respins})
    full = len(held) == CELLS
    values = sum(held.values())
    return {"start": start, "rounds": rounds, "full": full, "values": values,
            "bonus": FULL_GRID_BONUS if full else 0, "cash": values + (FULL_GRID_BONUS if full else 0)}


def play(rb=secrets.randbelow):
    """One whole spin, every feature it starts played out, in multiples of the stake. The fire meter and its jackpot
    pick belong to the spinner, so StampedeManager.spin() adds them (pick_game())."""
    event, stops, landed, grid = base_spin(rb)
    wins = ways_wins(grid)
    inferno = None
    if wins and rb(1000) < INFERNO_CHANCE:
        inferno = INFERNO_MULTS[pick(rb, [w for _, w in INFERNO_MULTS])][0]
        for w in wins:
            w["mult"] = round(w["mult"] * inferno, 4)
    sc = scatter_win(grid)
    fires = fire_cells(grid)
    values = [[c, r, draw_value(rb)] for c, r in fires]
    out = {"event": event, "inferno": inferno, "stops": stops, "landed": landed, "grid": grid, "wins": wins, "scatter": sc,
           "values": values, "free_spins": None, "hold": None}
    out["line_mult"] = round(sum(w["mult"] for w in wins), 4)
    if sc:
        out["free_spins"] = free_spins(rb, FS_AWARD[min(sc["count"], 5)])
    if len(fires) >= HS_TRIGGER:
        out["hold"] = hold_and_spin(rb, values)
    return out


def pick_outcome(rb=secrets.randbelow):
    """Which jackpot the pick gives (None: three smokes, nothing), by PICK_OUTCOMES."""
    return PICK_OUTCOMES[pick(rb, [w for _, w in PICK_OUTCOMES])][0]


def pick_game(outcome, rb=secrets.randbelow):
    """The pick as the page plays it: `picks` is what each click cracks open in turn (the winner's third is last,
    every other kind at most twice), `rest` what the unpicked fireballs held. Three of each kind make the board."""
    win = outcome or "smoke"
    picks = [k for k in PICK_KINDS if k != win for _ in range(rb(3))] + [win, win]
    for i in range(len(picks) - 1, 0, -1):
        j = rb(i + 1)
        picks[i], picks[j] = picks[j], picks[i]
    picks.append(win)
    rest = [k for k in PICK_KINDS for _ in range(3 - picks.count(k))]
    for i in range(len(rest) - 1, 0, -1):
        j = rb(i + 1)
        rest[i], rest[j] = rest[j], rest[i]
    return {"outcome": outcome, "picks": picks, "rest": rest}


def cash_mult(result):
    """Everything a played spin pays except its jackpots, x stake."""
    m = result["line_mult"] + (result["scatter"]["mult"] if result["scatter"] else 0)
    if result["free_spins"]:
        m += result["free_spins"]["mult"]
    if result["hold"]:
        m += result["hold"]["cash"]
    return round(m, 4)


# ---- exact math ---------------------------------------------------------------------------------------------------

def _dists(strips):
    """Each reel's windows with their chances: [[(p, window tuple), ...] per reel]."""
    out = []
    for s in strips:
        seen = {}
        for stop in range(len(s)):
            w = tuple(window(s, stop))
            seen[w] = seen.get(w, 0) + 1 / len(s)
        out.append(list(seen.items()))
    return out


def _convert(dist, sym, q):
    """A reel's windows after each cell that isn't `sym` turns into `sym` with chance q (Banana rain, a Stampede's
    loose wilds)."""
    out = {}
    for w, p in dist:
        variants = [((), 1.0)]
        for cell in w:
            nxt = []
            for pre, pp in variants:
                if cell == sym:
                    nxt.append((pre + (sym,), pp))
                else:
                    nxt.append((pre + (sym,), pp * q))
                    nxt.append((pre + (cell,), pp * (1 - q)))
            variants = nxt
        for v, pv in variants:
            out[v] = out.get(v, 0) + p * pv
    return list(out.items())


def _force(dist, sym, counts):
    """A reel's windows after `sym` takes k cells picked at random, k drawn from `counts` ((k, weight), ...)."""
    from itertools import combinations
    total = sum(w for _, w in counts)
    out = {}
    for w, p in dist:
        for k, wk in counts:
            rows = list(combinations(range(ROWS), k))
            for chosen in rows:
                v = tuple(sym if r in chosen else w[r] for r in range(ROWS))
                out[v] = out.get(v, 0) + p * wk / total / len(rows)
    return list(out.items())


def _count_dist(dists, sym):
    """Chance of each total count of `sym` across the reels."""
    total = {0: 1.0}
    for dist in dists:
        nxt = {}
        for w, p in dist:
            k = w.count(sym)
            for n, q in total.items():
                nxt[n + k] = nxt.get(n + k, 0) + p * q
        total = nxt
    return total


def _line_ev(dists, wild_mean=1.0):
    ev = 0.0
    for s in PAYING:
        es = [sum(p * (w.count(s) + wild_mean * w.count(WILD)) for w, p in d) for d in dists]
        none = [sum(p for w, p in d if w.count(s) + w.count(WILD) == 0) for d in dists]
        prod = es[0] * es[1]
        for k in (3, 4, 5):
            prod *= es[k - 1]
            ev += PAYS[s][k - 3] * prod * (none[k] if k < REELS else 1.0)
    return ev


def inferno_factor():
    """What Onkey's Inferno multiplies a base spin's ways wins by, on average (it only ever lands on a win)."""
    total = sum(w for _, w in INFERNO_MULTS)
    mean = sum(m * w for m, w in INFERNO_MULTS) / total
    return 1 + INFERNO_CHANCE / 1000 * (mean - 1)


def _scatter_ev(dists):
    cd = _count_dist(dists, SPIKE)
    return sum(p * SCATTER_PAYS.get(min(n, 5), 0) for n, p in cd.items() if n >= 3), cd


@lru_cache(maxsize=None)
def _fs_math():
    """Expected value of one free spin, the retrigger chance, and the expected number of spins for each award."""
    d = _dists(FS_STRIPS)
    wm = sum(m * w for m, w in FS_WILD_MULTS) / sum(w for _, w in FS_WILD_MULTS)
    sc_ev, cd = _scatter_ev(d)
    per_spin = _line_ev(d, wm) + sc_ev
    q = sum(p for n, p in cd.items() if n >= 3)
    spins = {}
    for n, award in FS_AWARD.items():
        states, expect = {award: 1.0}, 0.0
        for i in range(FS_CAP + 1):
            nxt = {}
            for a, p in states.items():
                if a <= i:
                    expect += p * a
                    continue
                for a2, p2 in ((min(a + FS_RETRIGGER, FS_CAP), q), (a, 1 - q)):
                    nxt[a2] = nxt.get(a2, 0) + p * p2
            states = nxt
        spins[n] = expect
    return {"per_spin": per_spin, "retrigger": q, "spins": spins}


@lru_cache(maxsize=None)
def _hs_math():
    """For each starting count of fireballs: the expected final count and the chance the grid fills."""
    from math import comb

    @lru_cache(maxsize=None)
    def go(n, r):
        if n == CELLS or r == 0:
            return float(n), float(n == CELLS)
        p = HS_LAND[n] / HS_TICKETS
        empty = CELLS - n
        e = full = 0.0
        for k in range(empty + 1):
            pk = comb(empty, k) * p ** k * (1 - p) ** (empty - k)
            en, fn = go(n + k, HS_RESPINS) if k else go(n, r - 1)
            e += pk * en
            full += pk * fn
        return e, full
    return {n: go(n, HS_RESPINS) for n in range(HS_TRIGGER, CELLS + 1)}


def value_mean():
    return sum(m * w for m, w in VALUES) / sum(w for _, w in VALUES)


def _mode_math(dists):
    """One kind of base spin: line and scatter pay, the features, and how many fireballs it adds to the meter."""
    line = _line_ev(dists) * inferno_factor()
    sc_ev, scd = _scatter_ev(dists)
    fs = _fs_math()
    fs_ev = sum(p * fs["spins"][min(n, 5)] * fs["per_spin"] for n, p in scd.items() if n >= 3)
    fs_p = sum(p for n, p in scd.items() if n >= 3)
    fcd = _count_dist(dists, FIRE)
    hs, mean = _hs_math(), value_mean()
    hs_cash = hs_p = full_p = 0.0
    for n, p in fcd.items():
        if n < HS_TRIGGER:
            continue
        final, full = hs[n]
        hs_p += p
        full_p += p * full
        hs_cash += p * (final * mean + full * FULL_GRID_BONUS)
    return {"line": line, "scatter": sc_ev, "free_spins": fs_ev, "hold": hs_cash, "fs_p": fs_p, "hs_p": hs_p,
            "full_p": full_p, "fires": sum(n * p for n, p in fcd.items())}


@lru_cache(maxsize=None)
def breakdown():
    """The machine's exact return, part by part (per credit staked), and how often things happen (per spin)."""
    base = _dists(BASE_STRIPS)
    pe = {k: n / EVENT_TICKETS for k, n in EVENTS.items()}
    stampede = [_force(d, WILD, STAMPEDE_SURE) if c in (1, 2) else _convert(d, WILD, STAMPEDE_Q / 1000) if c > 2 else d
                for c, d in enumerate(base)]
    modes = [(1 - sum(pe.values()), _mode_math(base)), (pe["stampede"], _mode_math(stampede)),
             (pe["rain"], _mode_math([_convert(d, FIRE, RAIN_Q / 1000) for d in base]))]
    out = {k: 0.0 for k in ("line", "scatter", "free_spins", "hold", "fs_p", "hs_p", "full_p", "fires")}
    for p, m in modes:
        for k in out:
            out[k] += p * m[k]
    # Each fireball adds the stake to the meter, so picks come once every METER_FULL / fires credits staked, at any
    # stake: per credit staked a jackpot pays its seed x how often it's won, plus its growth. pick_p and jackpot_hits
    # are per credit staked (per spin, multiply by the stake).
    out["pick_p"] = out["fires"] / METER_FULL
    total = sum(w for _, w in PICK_OUTCOMES)
    hits = {k: out["pick_p"] * sum(w for o, w in PICK_OUTCOMES if o == k) / total for k in JACKPOTS}
    jackpots = {k: hits[k] * j["seed"] + j["grow"] for k, j in JACKPOTS.items()}
    out["jackpots"] = sum(jackpots.values())
    out["jackpot_parts"] = jackpots
    out["jackpot_hits"] = hits
    out["rtp"] = out["line"] + out["scatter"] + out["free_spins"] + out["hold"] + out["jackpots"]
    out["event_p"] = sum(pe.values())
    return out


def rtp():
    return breakdown()["rtp"]


def october(ts=None):
    """Whether the Halloween look is on: October, Pacific time (the wheel's day)."""
    return wheel_day(ts or time.time())[5:7] == "10"


# ---- the machine on the site --------------------------------------------------------------------------------------

# Demo mode can ask for a spin that shows something (StampedeManager.spin(force=...)), to try the animations: "pick"
# fills the meter so the jackpot pick comes up, and a jackpot's name (or "smoke") also decides how the pick ends.
FORCES = ("win", "big", "stampede", "rain", "inferno", "free_spins", "hold", "full", "pick", "smoke", "mini", "minor",
          "major", "grand")


def _forced(force, rb=secrets.randbelow):
    """A spin that shows `force`, drawn again until it does (demo mode only). A full grid is filled on purpose; the
    pick's forces only need a spin with a fireball."""
    want = {
        "win": lambda r: r["line_mult"] > 0,
        "big": lambda r: cash_mult(r) >= 15,
        "stampede": lambda r: r["event"] and r["event"]["kind"] == "stampede",
        "rain": lambda r: r["event"] and r["event"]["kind"] == "rain",
        "inferno": lambda r: r["inferno"],
        "free_spins": lambda r: r["free_spins"],
        "hold": lambda r: r["hold"],
        "full": lambda r: r["hold"],
    }.get(force, lambda r: r["values"])
    for _ in range(200_000):
        r = play(rb)
        if want(r):
            while force == "full" and not r["hold"]["full"]:
                r["hold"] = hold_and_spin(rb, r["hold"]["start"], land=(2500,) * CELLS)
            return r
    raise BetError("Couldn't find a spin like that. Try again.")


def machine():
    """What the page needs to draw and explain the machine."""
    b = breakdown()
    total = sum(w for _, w in VALUES)
    picks = sum(w for _, w in PICK_OUTCOMES)
    return {
        "symbols": SYMBOLS, "reels": REELS, "rows": ROWS, "stakes": STAKES,
        "pays": {str(k): v for k, v in PAYS.items()}, "scatter_pays": SCATTER_PAYS, "fs_award": FS_AWARD,
        "fs_retrigger": FS_RETRIGGER, "fs_cap": FS_CAP, "fs_wild_mults": [m for m, _ in FS_WILD_MULTS],
        "hs_trigger": HS_TRIGGER, "hs_respins": HS_RESPINS, "full_grid_bonus": FULL_GRID_BONUS,
        "values": [{"mult": m, "chance": w / total} for m, w in VALUES],
        "jackpots": [{"key": k, "name": j["name"], "seed": j["seed"]} for k, j in JACKPOTS.items()],
        "meter_full": METER_FULL, "fires_per_spin": b["fires"], "pick_kinds": PICK_KINDS,
        "pick_chances": {(o or "smoke"): w / picks for o, w in PICK_OUTCOMES},
        "inferno_mults": [m for m, _ in INFERNO_MULTS],
        "strips": BASE_STRIPS, "fs_strips": FS_STRIPS,
        "rtp": round(b["rtp"] * 100, 2), "fs_chance": b["fs_p"], "hs_chance": b["hs_p"],
        "pick_per_credit": b["pick_p"], "event_chance": b["event_p"], "jackpots_per_credit": b["jackpot_hits"],
    }


def _features(result):
    """The features a stored spin showed: free spins, hold and spin, its event, the Inferno, the pick, its jackpot."""
    return {"free_spins": bool(result.get("free_spins")), "hold": bool(result.get("hold")),
            "event": (result.get("event") or {}).get("kind"), "inferno": result.get("inferno"),
            "pick": bool(result.get("pick")), "jackpots": [j["key"] for j in result.get("jackpots") or []]}


class StampedeManager:
    def __init__(self, db, demo=False):
        self.db, self.demo = db, demo
        with db.lock:
            # Tables from an unreleased version (jackpots kept as multiples of the stake, meters counting fireballs)
            # are replaced; nothing in them was ever on the live site.
            for table, old in (("stampede_pots", "mult"), ("stampede_meters", "fill"), ("stampede_jackpots", "mult")):
                if old in {r["name"] for r in db.query(f"PRAGMA table_info({table})")}:
                    db.conn.execute(f"DROP TABLE {table}")
                    db.conn.executescript(SCHEMA)
            for k, j in JACKPOTS.items():
                db.conn.execute("INSERT OR IGNORE INTO stampede_pots(key, size, hits) VALUES(?,?,0)", (k, j["seed"]))
            db.conn.commit()

    def pots(self):
        rows = {r["key"]: r for r in self.db.query("SELECT * FROM stampede_pots")}
        return [{"key": k, "name": j["name"], "seed": j["seed"], "grow": j["grow"],
                 "size": round(rows[k]["size"], 2) if k in rows else j["seed"],
                 "hits": rows[k]["hits"] if k in rows else 0} for k, j in JACKPOTS.items()]

    def meter(self, name):
        """A bettor's fire meter: credits of fireballs so far (`heat`) out of `full`."""
        r = self.db.query_one("SELECT heat FROM stampede_meters WHERE lower(bettor)=lower(?)", (name,))
        return {"heat": round(r["heat"], 2) if r else 0, "full": METER_FULL}

    @staticmethod
    def public(row):
        return {**row, "result": json.loads(row["result"]), "net": round(row["payout"] - row["stake"], 2)}

    @staticmethod
    def brief(row):
        """A spin without its reels, for lists."""
        return {"id": row["id"], "bettor": row["bettor"], "stake": row["stake"], "multiplier": row["multiplier"],
                "payout": row["payout"], "net": round(row["payout"] - row["stake"], 2), "created_ts": row["created_ts"],
                **_features(json.loads(row["result"]))}

    def spin(self, name, stake, request_id, force=None):
        if type(stake) not in (int, float) or stake not in STAKES:
            raise BetError(f"Choose a stake of {', '.join(map(str, STAKES[:-1]))} or {STAKES[-1]} credits.")
        if not isinstance(request_id, str) or not 16 <= len(request_id) <= 80 or not all(
                c.isascii() and (c.isalnum() or c in "-_") for c in request_id):
            raise BetError("Invalid spin reference. Reload the page and try again.")
        if not self.demo or force not in FORCES:
            force = None  # only demo mode can ask for a spin
        with self.db.lock:
            bettor = self.db.get_bettor(name)
            if not bettor:
                raise BetError("Sign in as a bettor to spin.")
            name = bettor["name"]
            old = self.db.query_one("SELECT * FROM stampede_spins WHERE bettor=? AND request_id=?", (name, request_id))
            if old:
                if old["stake"] != stake:
                    raise BetError("That spin reference has already been used.")
                return {"spin": self.public(old), "balance": round(bettor["balance"], 2), "pots": self.pots(),
                        "meter": self.meter(name)}
            if bettor["balance"] < stake:
                raise BetError("Not enough credits for that spin. Choose a smaller stake.")
            result = _forced(force) if force else play()
            now = time.time()
            pots = {p["key"]: p["size"] for p in self.pots()}
            for k, j in JACKPOTS.items():  # every spin grows every jackpot by its share of the stake, this one included
                pots[k] += j["grow"] * stake
            # The fire meter: each of this spin's fireballs adds the stake; a full meter empties into the jackpot pick
            # (anything over the top carries on into the next one).
            added = len(result["values"]) * stake
            heat = self.meter(name)["heat"]
            if force in ("pick", "smoke", "mini", "minor", "major", "grand"):
                heat = max(heat, METER_FULL - added)  # demo mode tops the meter up
            result["meter"] = {"before": round(heat, 2), "added": added, "full": METER_FULL}
            heat += added
            jackpots = []
            if heat >= METER_FULL:
                heat -= METER_FULL
                outcome = {"smoke": None, "pick": pick_outcome()}.get(force, force) if force else pick_outcome()
                result["pick"] = pick_game(outcome)
                if outcome:
                    jackpots.append({"key": outcome, "name": JACKPOTS[outcome]["name"], "amount": round(pots[outcome], 2)})
                    pots[outcome] = JACKPOTS[outcome]["seed"]
            result["meter"]["after"] = round(heat, 2)
            cash = cash_mult(result)
            payout = round(stake * cash + sum(j["amount"] for j in jackpots), 2)
            mult = round(payout / stake, 4)
            result.update({"cash_mult": cash, "jackpots": jackpots, "mult": mult})
            edge = rtp()
            try:
                self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance + ?, 2) WHERE name=?", (payout - stake, name))
                cur = self.db.conn.execute(
                    "INSERT INTO stampede_spins(bettor, stake, result, multiplier, payout, rtp, created_ts, request_id) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (name, stake, json.dumps(result, separators=(",", ":")), mult, payout, edge, now, request_id))
                sid = cur.lastrowid
                for k, v in pots.items():
                    self.db.conn.execute("UPDATE stampede_pots SET size=? WHERE key=?", (v, k))
                self.db.conn.execute(
                    "INSERT INTO stampede_meters(bettor, heat, updated_ts) VALUES(?,?,?) "
                    "ON CONFLICT(bettor) DO UPDATE SET heat=excluded.heat, updated_ts=excluded.updated_ts",
                    (name, round(heat, 2), now))
                for j in jackpots:
                    self.db.conn.execute(
                        "UPDATE stampede_pots SET hits=hits+1, last_bettor=?, last_payout=?, last_ts=? WHERE key=?",
                        (name, j["amount"], now, j["key"]))
                    self.db.conn.execute(
                        "INSERT INTO stampede_jackpots(spin_id, bettor, key, amount, created_ts) VALUES(?,?,?,?,?)",
                        (sid, name, j["key"], j["amount"], now))
                house.record(self.db.conn, "stampede", f"spin:{sid}", name, stake, stake - payout, stake * (1 - edge), now)
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
            row = self.db.query_one("SELECT * FROM stampede_spins WHERE id=?", (sid,))
            return {"spin": self.public(row), "balance": round(self.db.get_bettor(name)["balance"], 2),
                    "pots": self.pots(), "meter": self.meter(name)}

    def big_wins(self, limit=5):
        """This season's biggest wins by anyone (a payout above the stake), biggest first."""
        return [self.brief(r) for r in self.db.query(
            "SELECT * FROM stampede_spins WHERE season_id IS NULL AND payout > stake ORDER BY payout DESC, id LIMIT ?",
            (limit,))]

    def jackpot_log(self, limit=8):
        """The latest jackpots won, every season."""
        return self.db.query(
            "SELECT bettor, key, amount, created_ts FROM stampede_jackpots ORDER BY id DESC LIMIT ?", (limit,))

    def summary(self, me=None):
        out = {"machine": machine(), "pots": self.pots(), "jackpot_log": self.jackpot_log(),
               "big_wins": self.big_wins(), "october": october(), "forces": list(FORCES) if self.demo else [],
               "me": None, "history": [], "meter": None}
        if me:
            with self.db.lock:
                name = me["name"]
                rows = self.db.query(
                    "SELECT * FROM stampede_spins WHERE bettor=? AND season_id IS NULL ORDER BY id DESC", (name,))
                out["meter"] = self.meter(name)
            briefs = [self.brief(r) for r in rows]
            staked = sum(r["stake"] for r in rows)
            returned = sum(r["payout"] for r in rows)
            best = max((b for b in briefs if b["net"] > 0), key=lambda b: (b["payout"], b["id"]), default=None)
            out["me"] = {"name": name, "spins": len(rows), "staked": staked, "returned": round(returned, 2),
                         "net": round(returned - staked, 2), "wins": sum(b["net"] > 0 for b in briefs),
                         "free_spins": sum(b["free_spins"] for b in briefs), "holds": sum(b["hold"] for b in briefs),
                         "picks": sum(b["pick"] for b in briefs), "best": best}
            out["history"] = briefs[:12]
        return out
