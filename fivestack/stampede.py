"""Onkey Stampede: the Casino's 5 x 4 video slot, next to the classic machine in slots.py.

A spin shows four symbols on each of five reels. Wins pay "ways": a symbol on reels 1, 2 and 3 (and on, without a
gap), anywhere in each reel's four rows, pays its PAYS entry for that many reels times the ways it makes (the product
of how many times it shows on each of those reels), 1,024 ways at most. The Bongo Onkey (WILD, never on reel 1) stands
in for every symbol that pays ways. Spikes (SPIKE) are scatters: 3 or more anywhere pay SCATTER_PAYS and start free
spins. Golden fireballs (FIRE) carry credits (VALUES, a multiple of the stake); 6 or more start hold and spin. Every
fireball that lands also fills the spinner's own fire meter, and a full meter starts the jackpot pick.

Every spin also pays shapes, anywhere on the reels and on top of the ways: touching cells of one symbol (wilds joining
in) make a group, and a group pays once, for the biggest shape it makes (shape_kind(): three in a straight line, four,
a 2 x 2 square, a wall (a whole reel), five, a block of 6-7, a mega block of 8+), SHAPE_BASE by the symbol's tier x
SHAPE_FACTOR by the shape. Fireballs make shapes too, paying FIRE_SHARE of the credits printed on them. Most shapes pay
less than the stake; they're there to be seen. Onkey's Inferno multiplies them with the ways, and a multiplying wild in
one (the Golden Onkey, a free spin's wild) multiplies it, the biggest one if there are several.

Base spins have events (EVENTS, more below): before the reels stop, a Stampede drops wilds on reels 2-5 (always enough
for a win) or Banana rain turns some symbols into fireballs; after a win (ways or shapes), Onkey's Inferno can multiply
it. A spin
with no event may instead plant the spike (two spikes showing: the reels without one respin once, looking for the third,
PLANT_CHANCE) or show the Golden Onkey, the secret symbol: never on the strips or in the pay table, a wild worth
GOLDEN_MULT that pays GOLDEN_SPOT just for being seen.

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
    {"key": "onkey", "name": "Onkey", "tier": "top"},
    {"key": "wild", "name": "Bongo Onkey", "tier": "wild"},
    {"key": "spike", "name": "Spike", "tier": "scatter"},
    {"key": "fire", "name": "Golden fireball", "tier": "fire"},
    {"key": "golden", "name": "Golden Onkey", "tier": "secret"},
]
(NINE, TEN, JACK, QUEEN, KING, ACE, COCONUT, DRUM, BANANA, VALORANT, GREG, ONKEY, WILD, SPIKE,
 FIRE, GOLDEN) = range(len(SYMBOLS))
PAYING = range(WILD)  # the symbols that pay ways
WILDS = (WILD, GOLDEN)  # stand in for every paying symbol, in ways and in shapes

# Ways pays, in multiples of the stake for one way of 3, 4 or 5 reels. Flat on purpose: the reels stack their symbols
# (two or three in a row), so a win usually makes several ways and pays more than the stake.
PAYS = {
    NINE: (0.10, 0.25, 0.45), TEN: (0.10, 0.25, 0.45), JACK: (0.10, 0.25, 0.50), QUEEN: (0.10, 0.25, 0.50),
    KING: (0.15, 0.25, 0.60), ACE: (0.15, 0.25, 0.60), COCONUT: (0.15, 0.40, 0.90), DRUM: (0.25, 0.45, 1.00),
    BANANA: (0.25, 0.55, 1.35), VALORANT: (0.30, 0.70, 1.65), GREG: (0.40, 0.90, 2.25), ONKEY: (0.45, 1.35, 4.50),
}
SCATTER_PAYS = {3: 1, 4: 5, 5: 25}  # spikes anywhere, in multiples of the stake
FS_AWARD = {3: 7, 4: 10, 5: 15}
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
# Inferno multiplies its ways and shape wins (INFERNO_CHANCE per 1,000 winning spins, INFERNO_MULTS by weight).
EVENT_TICKETS = 1000
EVENTS = {"stampede": 12, "rain": 12}
STAMPEDE_SURE = ((1, 1),)  # (wilds on each of reels 2 and 3, weight)
STAMPEDE_Q = 80
RAIN_Q = 150
INFERNO_CHANCE = 122
INFERNO_MULTS = ((2, 6), (3, 3), (5, 1))
# Shapes, paid on every spin on top of the ways, wherever they are on the reels. Touching cells (side by side or one
# above the other) of one paying symbol, wilds joining in, make a group; a group needs one real symbol, and pays once,
# for the shape it makes (shape_kind()), so shapes never overlap: three in a straight line, four (in a line or bent), a
# square (2 x 2), a wall (a whole reel), five, a block (6 or 7) and a mega block (8 or more). It pays SHAPE_BASE for the
# symbol's tier x SHAPE_FACTOR for the shape, x the biggest multiplying wild in it (the Golden Onkey, a free spin's
# wilds; unlike ways, a shape's wilds don't multiply each other).
# Fireballs make shapes the same way and pay FIRE_SHARE of the credits printed on them (they still fill the meter).
SHAPE_BASE = {"low": 0.04, "mid": 0.08, "high": 0.2, "top": 0.4}
SHAPE_FACTOR = {"three": 1, "four": 2, "square": 2.5, "wall": 3, "five": 4, "block": 6, "mega": 10}
FIRE_SHARE = 0.2
# Spike planted: a base spin without an event that shows exactly two spikes, PLANT_CHANCE times in 1,000, respins every
# reel without a spike once, looking for the third. The spin is judged on what the reels show after it.
PLANT_CHANCE = 250
# The Golden Onkey, the secret symbol: never on the reel strips or in the pay table. On a base spin without an event,
# GOLDEN_CHANCE in GOLDEN_TICKETS picks one of the 20 cells, and a paying symbol there turns into it. It's wild worth
# GOLDEN_MULT ways on its reel, and pays GOLDEN_SPOT x the stake just for being seen. A spin it lands on can't plant.
GOLDEN_TICKETS = 10_000
GOLDEN_CHANCE = 50
GOLDEN_MULT = 3
GOLDEN_SPOT = 5

# Reel strips: (symbol, stack height, how many stacks). build_strip() shuffles them the same way every time. Low and
# mid symbols come in pairs, so a reel shows fewer different symbols and a win makes more ways.
_LOW = [(s, 2, 4) for s in (NINE, TEN, JACK, QUEEN, KING, ACE)] + [(COCONUT, 2, 3), (DRUM, 2, 3)]
_FS_LOW = [(s, 2, 3) for s in (NINE, TEN, JACK, QUEEN, KING, ACE)] + [(COCONUT, 2, 3), (DRUM, 2, 3)]


def _high(banana, valorant, greg, onkey):
    return [(BANANA, 1, banana), (VALORANT, 1, valorant), (GREG, 1, greg), (ONKEY, 2, onkey)]


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
        if any(heads[i] == heads[i - 1] and heads[i] in (WILD, FIRE, ONKEY) for i in range(len(heads))):
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
    multiplier (free spins); a Golden Onkey counts GOLDEN_MULT. Each win:
    {"symbol", "reels", "ways", "mult" (what it pays, x stake), "cells": [[reel, row], ...]}."""
    wild_mults = wild_mults or {}

    def weight(c, r):
        return GOLDEN_MULT if grid[c][r] == GOLDEN else wild_mults.get((c, r), 1) if grid[c][r] == WILD else 1
    out = []
    for s in PAYING:
        sums, cells = [], []
        for c in range(REELS):
            hit = [(r, weight(c, r)) for r in range(ROWS) if grid[c][r] == s or grid[c][r] in WILDS]
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


def shape_kind(n, cells=None):
    """The shape a group of n touching cells makes (cells: (reel, row) pairs, needed for 3 or 4), or None."""
    if n >= 8:
        return "mega"
    if n >= 6:
        return "block"
    if n == 5:
        return "five"
    if n < 3:
        return None
    reels, rows = {c for c, _ in cells}, {r for _, r in cells}
    if n == 3:
        return "three" if len(reels) == 1 or len(rows) == 1 else None
    if len(reels) == 1:
        return "wall"
    return "square" if len(reels) == 2 and len(rows) == 2 else "four"


def shape_pay(symbol, kind):
    return SHAPE_BASE[SYMBOLS[symbol]["tier"]] * SHAPE_FACTOR[kind]


def _groups(member):
    """The groups of touching cells for which member(c, r) is true: lists of (reel, row), in reading order."""
    seen, out = set(), []
    for c in range(REELS):
        for r in range(ROWS):
            if (c, r) in seen or not member(c, r):
                continue
            group, todo = [], [(c, r)]
            seen.add((c, r))
            while todo:
                x, y = todo.pop()
                group.append((x, y))
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < REELS and 0 <= ny < ROWS and (nx, ny) not in seen and member(nx, ny):
                        seen.add((nx, ny))
                        todo.append((nx, ny))
            out.append(sorted(group))
    return out


def shape_wins(grid, wild_mults=None, values=None):
    """Every shape on a grid: {"kind", "symbol", "mult" (x stake), "cells", "x" (its biggest wild multiplier)}.
    `wild_mults` maps (reel, row) to a free spin's wild multiplier; `values` ({(reel, row): multiple of the stake})
    are the fireballs' values, which fireball shapes pay a share of."""
    wild_mults = wild_mults or {}

    def weight(c, r):
        return GOLDEN_MULT if grid[c][r] == GOLDEN else wild_mults.get((c, r), 1) if grid[c][r] == WILD else 1
    out = []
    for s in PAYING:
        for group in _groups(lambda c, r: grid[c][r] == s or grid[c][r] in WILDS):
            kind = shape_kind(len(group), group)
            if not kind or not any(grid[c][r] == s for c, r in group):
                continue
            x = max(weight(c, r) for c, r in group)
            out.append({"kind": kind, "symbol": s, "mult": round(shape_pay(s, kind) * x, 4),
                        "cells": [list(cell) for cell in group], "x": x})
    if values is not None:
        for group in _groups(lambda c, r: grid[c][r] == FIRE):
            kind = shape_kind(len(group), group)
            if kind:
                out.append({"kind": kind, "symbol": FIRE, "x": 1, "cells": [list(cell) for cell in group],
                            "mult": round(FIRE_SHARE * sum(values[cell] for cell in group), 4)})
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
    """One base spin: the event before the reels stop (if any), where they stop, what they landed on, the Golden Onkey
    or the spike plant (only without an event, never both), and the grid after all that."""
    kind = draw_event(rb)
    stops = [rb(len(s)) for s in BASE_STRIPS]
    landed = [window(s, stop) for s, stop in zip(BASE_STRIPS, stops)]
    grid = [col[:] for col in landed]
    event = golden = plant = None
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
    elif rb(GOLDEN_TICKETS) < GOLDEN_CHANCE:
        c, r = divmod(rb(CELLS), ROWS)
        if grid[c][r] in PAYING:
            grid[c][r] = GOLDEN
            golden = [c, r]
    else:
        spikes = [[c, r] for c in range(REELS) for r in range(ROWS) if grid[c][r] == SPIKE]
        if len(spikes) == 2 and rb(1000) < PLANT_CHANCE:
            reels = [c for c in range(REELS) if SPIKE not in grid[c]]
            again = [rb(len(BASE_STRIPS[c])) for c in reels]
            for c, stop in zip(reels, again):
                grid[c] = window(BASE_STRIPS[c], stop)
            plant = {"spikes": spikes, "reels": reels, "stops": again, "landed": [grid[c][:] for c in reels],
                     "found": sum(col.count(SPIKE) for col in grid) >= 3}
    return event, stops, landed, grid, golden, plant


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
        shapes = shape_wins(grid, mults)
        sc = scatter_win(grid)
        extra = min(FS_RETRIGGER, FS_CAP - awarded) if sc else 0
        awarded += extra
        mult = sum(w["mult"] for w in wins) + sum(x["mult"] for x in shapes) + (sc["mult"] if sc else 0)
        total += mult
        played += 1
        spins.append({"stops": stops, "grid": grid, "wilds": [[c, r, m] for (c, r), m in sorted(mults.items())],
                      "wins": wins, "shapes": shapes, "scatter": sc, "retrigger": extra, "mult": round(mult, 4)})
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
    event, stops, landed, grid, golden, plant = base_spin(rb)
    wins = ways_wins(grid)
    fires = fire_cells(grid)
    values = [[c, r, draw_value(rb)] for c, r in fires]
    shapes = shape_wins(grid, values={(c, r): v for c, r, v in values})
    inferno = None
    if (wins or shapes) and rb(1000) < INFERNO_CHANCE:
        inferno = INFERNO_MULTS[pick(rb, [w for _, w in INFERNO_MULTS])][0]
        for w in wins + shapes:
            w["mult"] = round(w["mult"] * inferno, 4)
    sc = scatter_win(grid)
    out = {"event": event, "inferno": inferno, "stops": stops, "landed": landed, "grid": grid, "wins": wins, "scatter": sc,
           "values": values, "free_spins": None, "hold": None, "shapes": shapes, "golden": golden, "plant": plant}
    out["line_mult"] = round(sum(w["mult"] for w in wins), 4)
    out["shape_mult"] = round(sum(x["mult"] for x in shapes), 4)
    out["spot"] = GOLDEN_SPOT if golden else 0
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
    m = result["line_mult"] + result.get("shape_mult", 0) + result.get("spot", 0)
    m += result["scatter"]["mult"] if result["scatter"] else 0
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


# Every function below is linear in each reel's distribution, so it also takes a reel's windows restricted to some of
# them (chances that don't add up to 1): that's how breakdown() works out the spike plant and the Golden Onkey, as sums
# and differences of such products.

def _mass(dist):
    return sum(p for _, p in dist)


def _prod(xs):
    out = 1.0
    for x in xs:
        out *= x
    return out


def _line_ev(dists, wild_mean=1.0):
    masses = [_mass(d) for d in dists]
    ev = 0.0
    for s in PAYING:
        es = [sum(p * (w.count(s) + wild_mean * w.count(WILD) + GOLDEN_MULT * w.count(GOLDEN)) for w, p in d)
              for d in dists]
        none = [sum(p for w, p in d if s not in w and WILD not in w and GOLDEN not in w) for d in dists]
        prod = es[0] * es[1]
        for k in (3, 4, 5):
            prod *= es[k - 1]
            ev += PAYS[s][k - 3] * prod * (none[k] * _prod(masses[k + 1:]) if k < REELS else 1.0)
    return ev


# Shapes span the reels, so their exact value comes from a pass over the reels left to right (_group_ev()). Its state is
# the groups touching the last reel: which row belongs to which group, and each group's size, whether it has a real
# symbol, how many multiplying wilds it holds and, while it's small enough for its shape to matter, its cells. A group
# no longer touching the newest reel is finished and pays. Cells are coded 0 (not in), 1 (the symbol), 2 (a plain wild),
# 3 (a multiplying wild).
_EMPTY = ((-1,) * ROWS, ())
_NONE = (0,) * ROWS
_STEP = {}


def _step(state, pat, cap):
    """The next reel's cells (`pat`) added to `state`: (the new state, the finished groups as (kind, size, wilds x))."""
    key = (state, pat, cap)
    if key in _STEP:
        return _STEP[key]
    labels, comps = state
    parent = list(range(len(comps) + ROWS))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    base = len(comps)
    for r in range(ROWS):
        if not pat[r]:
            continue
        if labels[r] >= 0:
            parent[find(base + r)] = find(labels[r])
        if r and pat[r - 1]:
            parent[find(base + r)] = find(base + r - 1)
    merged = {}
    for i, (n, real, k, cells) in enumerate(comps):
        root = find(i)
        cells = None if cells is None else frozenset((dc - 1, rr) for dc, rr in cells)
        merged.setdefault(root, []).append((n, real, k, cells, False))
    for r in range(ROWS):
        if pat[r]:
            merged.setdefault(find(base + r), []).append((1, pat[r] == 1, pat[r] == 3, frozenset({(0, r)}), True))
    closed, new_comps, label_of = [], [], {}
    for root, parts in merged.items():
        n = sum(x[0] for x in parts)
        real, k = any(x[1] for x in parts), sum(x[2] for x in parts)
        cells = frozenset().union(*(x[3] for x in parts)) if n <= 4 and all(x[3] is not None for x in parts) else None
        if not any(x[4] for x in parts):  # touches no cell of the new reel: finished
            kind = shape_kind(n, cells)
            if kind and real:
                closed.append((kind, n, k))
            continue
        label_of[root] = (min(n, cap), real, k, cells)
    labels2, order = [], {}
    for r in range(ROWS):
        if not pat[r]:
            labels2.append(-1)
            continue
        root = find(base + r)
        if root not in order:
            order[root] = len(new_comps)
            new_comps.append(label_of[root])
        labels2.append(order[root])
    out = ((tuple(labels2), tuple(new_comps)), tuple(closed))
    _STEP[key] = out
    return out


def _patterns(dist, code):
    """A reel's windows as patterns of cell codes, with their chances."""
    out = {}
    for w, p in dist:
        pat = tuple(code(x) for x in w)
        out[pat] = out.get(pat, 0) + p
    return out


def _group_ev(options, need, pay, factor, cap):
    # factor(k): what a group holding k multiplying wilds is multiplied by, on average (its biggest wild's multiplier).
    """What the groups of one symbol pay: options[c] lists (pattern chances, tag) for reel c, and only reel choices
    whose tags add up to `need` count (how breakdown() sums over the spike plant's reels or the Golden Onkey's cell in
    one pass). pay(kind, size) is what a group pays, x factor(k) for k multiplying wilds in it."""
    states = {(0, _EMPTY): (1.0, 0.0)}

    def gain(closed):
        return sum(pay(kind, n) * factor(k) for kind, n, k in closed)
    for c in range(REELS):
        nxt = {}
        for (tag, st), (p, ev) in states.items():
            for dist, inc in options[c]:
                t = tag + inc
                if t > need:
                    continue
                for pat, q in dist.items():
                    st2, closed = _step(st, pat, cap)
                    old = nxt.get((t, st2), (0.0, 0.0))
                    nxt[(t, st2)] = (old[0] + p * q, old[1] + ev * q + (p * q * gain(closed) if closed else 0.0))
        states = nxt
    total = 0.0
    for (tag, st), (p, ev) in states.items():
        if tag == need:
            total += ev + p * gain(_step(st, _NONE, cap)[1])
    return total


def _fs_max(k):
    """What the biggest of k free-spin wild multipliers is worth on average (1 with none)."""
    if not k:
        return 1.0
    total = sum(w for _, w in FS_WILD_MULTS)
    cdf = lambda m: sum(w for v, w in FS_WILD_MULTS if v <= m) / total  # noqa: E731
    ms = sorted({v for v, _ in FS_WILD_MULTS})
    return sum(m * (cdf(m) ** k - (cdf(prev) ** k if prev else 0)) for prev, m in zip([None] + ms, ms))


def _shapes_ev(options, need, free=False):
    """Every symbol's shapes, and (on the base reels) the fireballs', for one kind of spin: a group's multiplying wild is
    the Golden Onkey (there's only ever one) on the base reels, a free spin's wilds on the free-spin reels."""
    multiplier = _fs_max if free else (lambda k: GOLDEN_MULT if k else 1)
    total = 0.0
    for s in PAYING:
        def code(x, s=s):
            return 1 if x == s else 3 if x == GOLDEN or (free and x == WILD) else 2 if x == WILD else 0
        opts = [[(_patterns(d, code), inc) for d, inc in reel] for reel in options]
        total += _group_ev(opts, need, lambda kind, n, s=s: shape_pay(s, kind), multiplier, 8)
    if not free:
        mean = value_mean()
        opts = [[(_patterns(d, lambda x: 1 if x == FIRE else 0), inc) for d, inc in reel] for reel in options]
        total += _group_ev(opts, need, lambda kind, n: FIRE_SHARE * mean * n, lambda k: 1, CELLS)
    return total


def _spot_ev(dists):
    masses = [_mass(d) for d in dists]
    return GOLDEN_SPOT * sum(sum(p * w.count(GOLDEN) for w, p in d) * _prod(masses[:c] + masses[c + 1:])
                             for c, d in enumerate(dists))


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
    per_spin = _line_ev(d, wm) + sc_ev + _shapes_ev([[(x, 0)] for x in d], 0, free=True)
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
    return {"line": line, "spot": _spot_ev(dists), "scatter": sc_ev, "free_spins": fs_ev,
            "hold": hs_cash, "fs_p": fs_p, "hs_p": hs_p, "full_p": full_p, "fires": sum(n * p for n, p in fcd.items())}


@lru_cache(maxsize=None)
def breakdown():
    """The machine's exact return, part by part (per credit staked), and how often things happen (per spin)."""
    from itertools import combinations
    base = _dists(BASE_STRIPS)
    pe = {k: n / EVENT_TICKETS for k, n in EVENTS.items()}
    calm = 1 - sum(pe.values())  # no event: where the Golden Onkey and the spike plant happen
    g, q = GOLDEN_CHANCE / GOLDEN_TICKETS, PLANT_CHANCE / 1000
    stampede = [_force(d, WILD, STAMPEDE_SURE) if c in (1, 2) else _convert(d, WILD, STAMPEDE_Q / 1000) if c > 2 else d
                for c, d in enumerate(base)]
    terms = [(calm * (1 - g), base), (pe["stampede"], stampede),
             (pe["rain"], [_convert(d, FIRE, RAIN_Q / 1000) for d in base])]
    # The spike plant: for each pair of reels showing the two spikes, the spin as it landed is replaced by the spin
    # after the other reels respin (those reels' windows from scratch).
    plant_p = 0.0
    for pair in combinations(range(REELS), 2):
        landed = [[(w, p) for w, p in d if (SPIKE in w) == (c in pair)] for c, d in enumerate(base)]
        after = [landed[c] if c in pair else [(w, p * _mass(landed[c])) for w, p in d] for c, d in enumerate(base)]
        plant_p += calm * (1 - g) * q * _prod(_mass(d) for d in landed)
        terms += [(calm * (1 - g) * q, after), (-calm * (1 - g) * q, landed)]
    # The Golden Onkey: one cell in twenty, if it holds a paying symbol.
    golden_p = 0.0
    for c in range(REELS):
        for r in range(ROWS):
            turned = [(w[:r] + (GOLDEN,) + w[r + 1:] if w[r] in PAYING else w, p) for w, p in base[c]]
            golden_p += calm * g / CELLS * sum(p for w, p in base[c] if w[r] in PAYING)
            terms.append((calm * g / CELLS, [turned if i == c else d for i, d in enumerate(base)]))
    keys = ("line", "spot", "scatter", "free_spins", "hold", "fs_p", "hs_p", "full_p", "fires")
    out = {k: 0.0 for k in keys}
    for p, dists in terms:
        m = _mode_math(dists)
        for k in keys:
            out[k] += p * m[k]
    out["plant_p"], out["golden_p"] = plant_p, golden_p
    # Shapes, in one pass a kind of spin: the spike plant's reels (two showing a spike) and the Golden Onkey's reel are
    # picked along the way, by tag.
    one = lambda d: [(d, 0)]  # noqa: E731
    spiked = [([(w, p) for w, p in d if SPIKE in w], [(w, p) for w, p in d if SPIKE not in w]) for d in base]
    turned = [[(w[:r] + (GOLDEN,) + w[r + 1:] if w[r] in PAYING else w, p) for r in range(ROWS) for w, p in d]
              for d in base]
    shapes = (calm * (1 - g) * _shapes_ev([one(d) for d in base], 0)
              + pe["stampede"] * _shapes_ev([one(d) for d in stampede], 0)
              + pe["rain"] * _shapes_ev([one(_convert(d, FIRE, RAIN_Q / 1000)) for d in base], 0)
              + calm * (1 - g) * q * _shapes_ev([[(sp, 1), ([(w, p * _mass(no)) for w, p in d], 0)]
                                                 for d, (sp, no) in zip(base, spiked)], 2)
              - calm * (1 - g) * q * _shapes_ev([[(sp, 1), (no, 0)] for sp, no in spiked], 2)
              + calm * g / CELLS * _shapes_ev([[(d, 0), (t, 1)] for d, t in zip(base, turned)], 1))
    out["shapes"] = shapes * inferno_factor()
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
    out["rtp"] = (out["line"] + out["shapes"] + out["spot"] + out["scatter"] + out["free_spins"] + out["hold"]
                  + out["jackpots"])
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
FORCES = ("win", "big", "wall", "block", "fire_shape", "golden", "planted", "detonated", "stampede", "rain", "inferno", "free_spins", "hold",
          "full", "pick", "smoke", "mini", "minor", "major", "grand")


def _forced(force, rb=secrets.randbelow):
    """A spin that shows `force`, drawn again until it does (demo mode only). A full grid is filled on purpose; the
    pick's forces only need a spin with a fireball."""
    want = {
        "win": lambda r: r["line_mult"] > 0,
        "big": lambda r: cash_mult(r) >= 15,
        "stampede": lambda r: r["event"] and r["event"]["kind"] == "stampede",
        "rain": lambda r: r["event"] and r["event"]["kind"] == "rain",
        "inferno": lambda r: r["inferno"],
        "wall": lambda r: any(x["kind"] == "wall" for x in r["shapes"]),
        "block": lambda r: any(x["kind"] in ("block", "mega") and x["symbol"] != FIRE for x in r["shapes"]),
        "fire_shape": lambda r: any(x["symbol"] == FIRE for x in r["shapes"]),
        "golden": lambda r: r["golden"],
        "planted": lambda r: r["plant"],
        "detonated": lambda r: r["plant"] and r["plant"]["found"],
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
        "shape_base": SHAPE_BASE, "shape_factor": SHAPE_FACTOR, "fire_share": FIRE_SHARE, "plant_chance": b["plant_p"],
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
    """The features a stored spin showed: free spins, hold and spin, its event, the Inferno, the spike plant, the pick,
    its jackpot."""
    return {"free_spins": bool(result.get("free_spins")), "hold": bool(result.get("hold")), "plant": bool(result.get("plant")),
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
