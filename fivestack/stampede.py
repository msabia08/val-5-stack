"""Onkey Stampede: the Casino's 5 x 4 video slot, next to the classic machine in slots.py.

A spin shows four symbols on each of five reels. Wins pay "ways": a symbol on reels 1, 2 and 3 (and on, without a
gap), anywhere in each reel's four rows, pays its PAYS entry for that many reels times the ways it makes (the product
of how many times it shows on each of those reels), 1,024 ways at most. The Bongo Onkey (WILD, never on reel 1) stands
in for every symbol that pays ways. Spikes (SPIKE) are scatters: 3 or more anywhere pay SCATTER_PAYS and start free
spins. Golden fireballs (FIRE) carry credits (VALUES, a multiple of the stake); 6 or more start hold and spin. Every
fireball that lands also fills the spinner's own fire meter, and a full meter starts the jackpot pick.

Every spin also pays shapes, anywhere on the reels and on top of the ways: one symbol (wilds joining in) laid out in
one of the SHAPES, a pattern with a direction: 3, 4 or 5 in a row, a diagonal, a V, a peak, an arrow, a wall (a whole
reel), a long diagonal, a big V, a mountain, a zigzag, a cross or an X. A shape pays SHAPE_BASE by the symbol's tier x
its factor, unless a bigger shape of the same symbol covers it (a 5 in a row pays once, not as its 4s and 3s).
Different shapes can share cells. Fireballs make shapes too, paying FIRE_SHARE of the credits printed on them.

Spicy banana bunches (SPICY) are on the base reels: each is wild (it stands in for every paying symbol, in ways and in
shapes) and carries a pepper (SPICY_MULTS, drawn as it lands). When one lands on a spin that wins anything (ways or
shapes), Onkey eats it and breathes fire: every win on the spin is multiplied by its pepper, and two or more multiply
together.

Base spins have events (EVENTS, more below): before the reels stop, a Stampede drops wilds on reels 2-5 (always enough
for a win), Banana rain turns some symbols into ripe bananas (FIRE: the page draws them as bananas whose ripeness
shows their value), the scientist's clone ray copies reel 1 onto the reels next to it, Man Strudel slashes a line
across reels 2-4 and every cell he cuts turns wild (always a win), a Banana split doubles one symbol (it counts twice
in the ways), or a Music break drops a pair of bongo drums on a reel, and every reel showing a drum doubles every win
on the spin. A spin with no
event may instead plant the spike (two spikes showing: the reels without one respin once, looking for the third,
PLANT_CHANCE) or show the Golden Onkey, the secret symbol: never on the strips or in the pay table, a wild that
multiplies every win on the spin by GOLDEN_MULT and pays GOLDEN_SPOT just for being seen.

The Vault Heist: every Golden Onkey drops a key into the spinner's own key meter (kept between visits and seasons,
like the fire meter but apart from it), and KEYS_FULL keys start a heist. Onkey cracks the vault's locks one at a time
(the player picks which; the order changes nothing) while the scientist sends Man Strudel, in the brainbot, after it;
the first lock that holds ends it (Man Strudel gets there, and lets Onkey keep what he has), paying HEIST_PAYS by locks
opened, x the heist's stake: the average stake of the spins its keys came on, so raising the bet with three keys in
hand gains nothing. Past every lock is the vault door, which opens with a chance in proportion to that stake
(DOOR_CHANCE at the top stake) and pays the JACKPOT: the house's progressive jackpot, the one the daily wheel's rarest
slice pays, whole. That comes out of the house's money like the wheel's, so it's not part of rtp(); the heist's other
prizes are.

Two more meters fill from the events, and each starts a bonus at the average stake of the spins that filled it (like
the keys, so raising the bet at the last step gains nothing), paying its ways and shapes only: every clone ray pins a
photo to the scientist's evidence board, and LAB_FULL photos start the Big Experiment, one spin with reel 1 copied
onto every reel; every Strudel's slice adds to Man Strudel's friendship, and FRIEND_FULL slices make him switch sides
for DEFECT_SPINS spins, each slashing two lines of wilds.

Daily spins: DAILY_SPINS spins a day (the Pacific day, like the daily wheel) at DAILY_STAKE, whose stake the house
gives (house_payouts kind `stampede_daily`, free like the wheel's credits), so the machine's return is untouched.
Achievements (ACHIEVEMENTS): the first time a spin shows one, the bettor gets an earned-only cosmetic from
bananas.CATALOG (never sold, never on the wheel), which shows next to their name across the site.

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
JACKPOT_CACHE_S = 5
FEED_MULT = 10  # the win feed's spins: this many times the stake or more, or something special
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
    {"key": "fire", "name": "Ripe banana", "tier": "fire"},  # drawn as a banana whose ripeness shows its value
    {"key": "golden", "name": "Golden Onkey", "tier": "secret"},
    {"key": "spicy", "name": "Spicy banana bunch", "tier": "wild"},
]
(NINE, TEN, JACK, QUEEN, KING, ACE, COCONUT, DRUM, BANANA, VALORANT, GREG, ONKEY, WILD, SPIKE,
 FIRE, GOLDEN, SPICY) = range(len(SYMBOLS))
PAYING = range(WILD)  # the symbols that pay ways
WILDS = (WILD, GOLDEN, SPICY)  # stand in for every paying symbol, in ways and in shapes

# Ways pays, in multiples of the stake for one way of 3, 4 or 5 reels. Flat on purpose: the reels stack their symbols
# (two or three in a row), so a win usually makes several ways and pays more than the stake.
PAYS = {
    NINE: (0.04, 0.08, 0.15), TEN: (0.04, 0.08, 0.15), JACK: (0.04, 0.08, 0.16), QUEEN: (0.04, 0.08, 0.16),
    KING: (0.05, 0.08, 0.19), ACE: (0.05, 0.08, 0.19), COCONUT: (0.05, 0.12, 0.28), DRUM: (0.08, 0.15, 0.32),
    BANANA: (0.08, 0.17, 0.44), VALORANT: (0.1, 0.23, 0.52), GREG: (0.12, 0.28, 0.73), ONKEY: (0.15, 0.44, 1.42),
}
SCATTER_PAYS = {3: 1, 4: 5, 5: 25}  # spikes anywhere, in multiples of the stake
FS_AWARD = {3: 6, 4: 9, 5: 13}
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
# a bonus ends with about 10 bananas, and about 1 in 11,000 fills the grid.
HS_TICKETS = 10_000
HS_LAND = tuple(420 if n < 16 else 315 for n in range(CELLS))
# The fire meter: every fireball that lands in a base spin adds the spin's stake to the spinner's own meter (kept
# between visits and seasons), so it fills faster the more you bet. At METER_FULL credits it empties into the jackpot
# pick: fifteen fireballs, cracked open one at a time until one jackpot shows three times, or three smokes end it with
# nothing. PICK_OUTCOMES decides which (per 1,000), before the first pick. At a 10-credit bet that's about every 250
# spins; at 100, every 25.
METER_FULL = 3000
PICK_KINDS = ("mini", "minor", "major", "grand", "smoke")
PICK_OUTCOMES = (("mini", 400), ("minor", 140), ("major", 45), ("grand", 5), (None, 410))
JACKPOTS = {  # the size a jackpot starts at (credits), and what it grows by per credit staked
    "mini": {"name": "Mini", "seed": 100, "grow": 0.002},
    "minor": {"name": "Minor", "seed": 250, "grow": 0.002},
    "major": {"name": "Major", "seed": 800, "grow": 0.003},
    "grand": {"name": "Grand", "seed": 5000, "grow": 0.004},
}
# Events on base spins. Before the reels stop (tickets out of EVENT_TICKETS): a Stampede puts wilds on reels 2 and 3
# (STAMPEDE_SURE: how many, so the spin always wins) and on reels 4 and 5 (each cell, STAMPEDE_Q per 1,000); Banana
# rain turns each cell that isn't a fireball into one (RAIN_Q per 1,000). The scientist's clone ray copies reel 1 onto
# the reels after it (CLONE_COPIES: how many, weighted), fireballs and their values included, so whatever reel 1
# shows makes ways and rows. Strudel's slice: Man Strudel slashes one of SLICE_PATHS (the row he cuts on each of reels
# 2-4: straight across or a diagonal) and every cell he cuts turns into a wild, so everything on reel 1 wins on at
# least four reels.
EVENT_TICKETS = 1000
EVENTS = {"stampede": 22, "rain": 20, "clone": 20, "slice": 26, "split": 36, "music": 14}
CLONE_COPIES = ((2, 1),)  # (reels copied after reel 1, weight)
SLICE_PATHS = ((0, 0, 0), (1, 1, 1), (2, 2, 2), (3, 3, 3), (0, 1, 2), (1, 2, 3), (2, 1, 0), (3, 2, 1))  # rows on reels 2-4
# Banana split: on one reel picked at random, one of its paying symbols (picked at random) is split in two and counts
# twice in the ways (nothing happens on a reel without one). Music break: a pair of bongo drums lands on two touching
# rows of a random reel, and the spin's ways and shape wins are doubled for every reel showing a drum (BEAT_MULT each).
BEAT_MULT = 2
STAMPEDE_SURE = ((1, 1),)  # (wilds on each of reels 2 and 3, weight)
STAMPEDE_Q = 80
RAIN_Q = 80
# Spicy banana bunches: on the base strips of reels 3-5 (SPICY_STACKS a reel, in place of a plain banana bunch), each wild (in WILDS)
# and carrying a pepper, drawn as it lands from SPICY_MULTS (multiplier, weight). A spin with a win and a spicy banana
# multiplies every ways and shape win by its pepper (by all of them multiplied together, for several).
SPICY_STACKS = 1
SPICY_MULTS = ((2, 9), (3, 1))
# Shapes, paid on every spin on top of the ways, wherever they are on the reels: kind: (name, factor, forms). A form
# is its strokes (the lines the page draws through it), each a list of (reel, row) offsets; a shape pays wherever a
# form fits on the reels. It pays SHAPE_BASE for the symbol's tier x its factor, x the biggest multiplying wild in it
# (a free spin's wilds; unlike ways, a shape's wilds don't multiply each other). A shape needs SHAPE_REAL of the symbol
# itself (the rest can be wilds), and doesn't pay when a bigger shape of the same symbol holds all its cells.
# Fireballs make shapes the same way and pay FIRE_SHARE of the credits printed on them (they still fill the meter).
SHAPE_REAL = 2  # 1 or 2: _spots_ev() counts the shapes it falls short by
SHAPE_BASE = {"low": 0.2, "mid": 0.2, "high": 0.3, "top": 0.6}
SHAPES = {
    "row3": ("3 in a row", 1, [[[(0, 0), (1, 0), (2, 0)]]]),
    "diag": ("Diagonal", 1, [[[(0, 0), (1, 1), (2, 2)]], [[(0, 2), (1, 1), (2, 0)]]]),
    "v": ("V", 1, [[[(0, 0), (1, 1), (2, 0)]]]),
    "peak": ("Peak", 1, [[[(0, 1), (1, 0), (2, 1)]]]),
    "row4": ("4 in a row", 2.5, [[[(0, 0), (1, 0), (2, 0), (3, 0)]]]),
    "wall": ("Wall", 1.5, [[[(0, 0), (0, 1), (0, 2), (0, 3)]]]),
    "diag4": ("Long diagonal", 4, [[[(0, 0), (1, 1), (2, 2), (3, 3)]], [[(0, 3), (1, 2), (2, 1), (3, 0)]]]),
    "row5": ("5 in a row", 12, [[[(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)]]]),
    "bigv": ("Big V", 25, [[[(0, 0), (1, 1), (2, 2), (3, 1), (4, 0)]]]),
    "mountain": ("Mountain", 25, [[[(0, 2), (1, 1), (2, 0), (3, 1), (4, 2)]]]),
    "zigzag": ("Zigzag", 15, [[[(0, 0), (1, 1), (2, 0), (3, 1), (4, 0)]], [[(0, 1), (1, 0), (2, 1), (3, 0), (4, 1)]]]),
    "cross": ("Cross", 6, [[[(0, 1), (1, 1), (2, 1)], [(1, 0), (1, 1), (1, 2)]]]),
    "x": ("X", 25, [[[(0, 0), (1, 1), (2, 2)], [(2, 0), (1, 1), (0, 2)]]]),
}
FIRE_SHARE = 0.1
# Spike planted: a base spin without an event that shows exactly two spikes, PLANT_CHANCE times in 1,000, respins every
# reel without a spike once, looking for the third. The spin is judged on what the reels show after it.
PLANT_CHANCE = 250
# The Golden Onkey, the secret symbol: never on the reel strips or in the pay table. On a base spin without an event,
# GOLDEN_CHANCE in GOLDEN_TICKETS picks one of the 20 cells, and a paying symbol there turns into it. It's wild, it
# multiplies every ways and shape win on the spin by GOLDEN_MULT (after they're all counted, like a spicy banana), and
# pays GOLDEN_SPOT x the stake just for being seen. A spin it lands on can't plant.
GOLDEN_TICKETS = 10_000
GOLDEN_CHANCE = 51
GOLDEN_MULT = 3
GOLDEN_SPOT = 2
# The Vault Heist: every Golden Onkey drops a key; KEYS_FULL keys start a heist (StampedeManager.spin(), since the
# keys are the spinner's). Each of HEIST_LOCKS locks opens with LOCK_CHANCE in 1,000; it pays HEIST_PAYS[locks opened]
# x the heist's stake (the keys' average stake). With every lock open, the door opens with chance DOOR_CHANCE x that
# stake / the top stake and pays the house's jackpot on top.
KEYS_FULL = 4
HEIST_LOCKS = 3
LOCK_CHANCE = 500
HEIST_PAYS = (5, 10, 25, 50)
DOOR_CHANCE = 0.5
DOOR_TICKETS = 1_000_000
# The evidence board and Man Strudel's friendship: LAB_FULL clone rays start the Big Experiment, FRIEND_FULL slices
# make Man Strudel switch sides for DEFECT_SPINS spins; both at the average stake of the spins that filled them.
LAB_FULL = 6
EXPERIMENT_COPIES = 3  # the Big Experiment copies reel 1 onto this many reels after it
FRIEND_FULL = 5
DEFECT_SPINS = 3
DEFECT_CUTS = 1  # the paths Man Strudel slashes on each of his spins
# Daily spins: DAILY_SPINS a day at DAILY_STAKE, the stake given by the house.
DAILY_SPINS = 3
DAILY_STAKE = 10

# Reel strips: (symbol, stack height, how many stacks). build_strip() shuffles them the same way every time. Low and
# mid symbols come in pairs, so a reel shows fewer different symbols and a win makes more ways.
_LOW = [(s, 2, 4) for s in (NINE, TEN, JACK, QUEEN, KING, ACE)] + [(COCONUT, 2, 3), (DRUM, 2, 3)]
_FS_LOW = [(s, 2, 3) for s in (NINE, TEN, JACK, QUEEN, KING, ACE)] + [(COCONUT, 2, 3), (DRUM, 2, 3)]


def _high(banana, valorant, greg, onkey, spicy=0):
    return [(BANANA, 1, banana - spicy), (VALORANT, 1, valorant), (GREG, 1, greg), (ONKEY, 2, onkey)] + (
        [(SPICY, 1, spicy)] if spicy else [])


BASE_REELS = [
    _LOW + _high(4, 4, 4, 2) + [(SPIKE, 1, 1), (FIRE, 1, 3), (FIRE, 2, 1)],
    _LOW + _high(4, 4, 4, 2) + [(WILD, 1, 2), (SPIKE, 1, 2), (FIRE, 1, 3), (FIRE, 2, 1)],
    _LOW + _high(4, 4, 4, 2, SPICY_STACKS) + [(WILD, 1, 2), (SPIKE, 1, 2), (FIRE, 1, 2), (FIRE, 3, 1)],
    _LOW + _high(4, 4, 4, 2, SPICY_STACKS) + [(WILD, 1, 2), (SPIKE, 1, 2), (FIRE, 1, 3), (FIRE, 2, 1)],
    _LOW + _high(4, 4, 4, 2, SPICY_STACKS) + [(WILD, 1, 2), (SPIKE, 1, 2), (FIRE, 1, 3), (FIRE, 2, 1)],
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

def ways_wins(grid, wild_mults=None, split=None):
    """Every ways win on a grid (a list of five columns of four symbols). `wild_mults` maps (reel, row) to a wild's
    multiplier (free spins); `split` is a Banana split's (reel, row), which counts twice. Each win: {"symbol", "reels",
    "ways", "mult" (what it pays, x stake), "cells": [[reel, row], ...]}."""
    wild_mults = wild_mults or {}

    def weight(c, r):
        return wild_mults.get((c, r), 1) if grid[c][r] == WILD else 2 if (c, r) == split else 1
    out = []
    for s in PAYING:
        sums, cells = [], []
        for c in range(REELS):
            hit = [(r, weight(c, r)) for r in range(ROWS) if _is(grid[c][r], s) or grid[c][r] in WILDS]
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


def _is(x, s):
    """Whether a cell showing x is the paying symbol s itself (not a wild standing in)."""
    return x == s


def _spots():
    """Every place a shape fits on the reels: (kind, cells (sorted (reel, row) pairs), strokes ([[reel, row], ...]
    each)). A cell set two kinds could both make goes to the first."""
    out, seen = [], set()
    for kind, (_, _, forms) in SHAPES.items():
        for strokes in forms:
            cells = {cell for stroke in strokes for cell in stroke}
            w, h = max(c for c, _ in cells) + 1, max(r for _, r in cells) + 1
            for dc in range(REELS - w + 1):
                for dr in range(ROWS - h + 1):
                    placed = frozenset((c + dc, r + dr) for c, r in cells)
                    if placed in seen:
                        continue
                    seen.add(placed)
                    out.append((kind, tuple(sorted(placed)), [[[c + dc, r + dr] for c, r in st] for st in strokes]))
    return out


SPOTS = _spots()
# For each spot, the bigger spots holding all its cells: when one of those is made, this one doesn't pay.
COVERS = [[j for j, (_, big, _) in enumerate(SPOTS) if set(cells) < set(big)] for _, cells, _ in SPOTS]


def shape_pay(symbol, kind):
    return SHAPE_BASE[SYMBOLS[symbol]["tier"]] * SHAPES[kind][1]


def shape_wins(grid, wild_mults=None, values=None):
    """Every shape on a grid: {"kind", "symbol", "mult" (x stake), "cells", "strokes", "x" (its biggest multiplier)}.
    `wild_mults` maps (reel, row) to a free spin's wild multiplier; `values` ({(reel, row): multiple of the stake})
    are the fireballs' values, which fireball shapes pay a share of."""
    wild_mults = wild_mults or {}
    kinds = [(s, lambda x, s=s: x == s or x in WILDS, lambda x, s=s: x == s) for s in PAYING]
    if values is not None:
        kinds.append((FIRE, lambda x: x == FIRE, lambda x: x == FIRE))
    out = []
    for s, member, real in kinds:
        made = [all(member(grid[c][r]) for c, r in cells) for _, cells, _ in SPOTS]
        for i, (kind, cells, strokes) in enumerate(SPOTS):
            if not made[i] or any(made[j] for j in COVERS[i]) or sum(real(grid[c][r]) for c, r in cells) < SHAPE_REAL:
                continue
            if s == FIRE:
                x, mult = 1, FIRE_SHARE * sum(values[cell] for cell in cells)
            else:
                x = max(wild_mults.get((c, r), 1) if grid[c][r] == WILD else 1 for c, r in cells)
                mult = shape_pay(s, kind) * x
            out.append({"kind": kind, "symbol": s, "mult": round(mult, 4), "cells": [list(cell) for cell in cells],
                        "strokes": strokes, "x": x})
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
    elif kind == "clone":
        k = CLONE_COPIES[pick(rb, [w for _, w in CLONE_COPIES])][0]
        to = list(range(1, k + 1))
        for c in to:
            grid[c] = grid[0][:]
        event = {"kind": "clone", "from": 0, "to": to}
    elif kind == "split":
        c = rb(REELS)
        rows = [r for r in range(ROWS) if grid[c][r] in PAYING]
        event = {"kind": "split", "cell": [c, rows[rb(len(rows))]] if rows else None}
    elif kind == "music":
        c, r = rb(REELS), rb(ROWS - 1)
        cells = [[c, r], [c, r + 1]]
        for cc, rr in cells:
            grid[cc][rr] = DRUM
        event = {"kind": "music", "cells": cells}
    elif kind == "slice":
        path = SLICE_PATHS[rb(len(SLICE_PATHS))]
        cells = [[c + 1, r] for c, r in enumerate(path)]
        for c, r in cells:
            grid[c][r] = WILD
        event = {"kind": "slice", "cells": cells}
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
    kind = (event or {}).get("kind")
    split = tuple(event["cell"]) if kind == "split" and event["cell"] else None
    wins = ways_wins(grid, split=split)
    fires = fire_cells(grid)
    values = [[c, r, draw_value(rb)] for c, r in fires]
    if event and event["kind"] == "clone":  # a copied fireball carries its original's value
        first = {r: v for c, r, v in values if c == 0}
        values = [[c, r, first[r] if c in event["to"] else v] for c, r, v in values]
    shapes = shape_wins(grid, values={(c, r): v for c, r, v in values})
    spicy = [[c, r, SPICY_MULTS[pick(rb, [w for _, w in SPICY_MULTS])][0]]
             for c in range(REELS) for r in range(ROWS) if grid[c][r] == SPICY]
    won = bool(wins or shapes)
    heat = _prod(m for _, _, m in spicy) if spicy and won else None
    golden_x = GOLDEN_MULT if golden and won else None
    beat = BEAT_MULT ** sum(DRUM in col for col in grid) if kind == "music" and won else None
    boost = (heat or 1) * (golden_x or 1) * (beat or 1)
    if boost > 1:
        for w in wins + shapes:
            w["mult"] = round(w["mult"] * boost, 4)
    sc = scatter_win(grid)
    out = {"event": event, "spicy": spicy, "heat": heat, "golden_x": golden_x, "beat": beat, "key": bool(golden), "heist": None, "stops": stops, "landed": landed,
           "grid": grid, "wins": wins, "scatter": sc, "values": values, "free_spins": None, "hold": None,
           "shapes": shapes, "golden": golden, "plant": plant}
    out["line_mult"] = round(sum(w["mult"] for w in wins), 4)
    out["shape_mult"] = round(sum(x["mult"] for x in shapes), 4)
    out["spot"] = GOLDEN_SPOT if golden else 0
    if sc:
        out["free_spins"] = free_spins(rb, FS_AWARD[min(sc["count"], 5)])
    if len(fires) >= HS_TRIGGER:
        out["hold"] = hold_and_spin(rb, values)
    return out


def _bonus_spin(rb, grid, stake):
    """A bonus spin's grid, paid: its ways and shapes (bananas' too, with values drawn for them) x `stake`. Nothing
    else on it pays or starts anything."""
    values = [[c, r, draw_value(rb)] for c, r in fire_cells(grid)]
    wins = ways_wins(grid)
    shapes = shape_wins(grid, values={(c, r): v for c, r, v in values})
    mult = round(sum(w["mult"] for w in wins) + sum(x["mult"] for x in shapes), 4)
    return {"grid": grid, "values": values, "wins": wins, "shapes": shapes, "mult": mult, "cash": round(mult * stake, 2)}


def big_experiment(rb, stake):
    """The Big Experiment: one spin, reel 1 copied onto the next EXPERIMENT_COPIES reels (its bananas keep their
    values), at `stake`."""
    stops = [rb(len(s)) for s in BASE_STRIPS]
    landed = [window(s, stop) for s, stop in zip(BASE_STRIPS, stops)]
    out = _bonus_spin(rb, [landed[0][:] if c <= EXPERIMENT_COPIES else landed[c][:] for c in range(REELS)], stake)
    first = {r: v for c, r, v in out["values"] if c == 0}
    for v in out["values"]:  # the copies carry reel 1's values, so redo the bananas' shapes with them
        if v[0] <= EXPERIMENT_COPIES:
            v[2] = first[v[1]]
    out["shapes"] = shape_wins(out["grid"], values={(c, r): v for c, r, v in out["values"]})
    out["mult"] = round(sum(w["mult"] for w in out["wins"]) + sum(x["mult"] for x in out["shapes"]), 4)
    out["cash"] = round(out["mult"] * stake, 2)
    return {"stake": stake, "stops": stops, "landed": landed, "to": list(range(1, EXPERIMENT_COPIES + 1)), **out}


def strudel_spins(rb, stake):
    """Man Strudel on your side: DEFECT_SPINS spins, each with DEFECT_CUTS different SLICE_PATHS slashed into wilds."""
    spins, total = [], 0.0
    for _ in range(DEFECT_SPINS):
        stops = [rb(len(s)) for s in BASE_STRIPS]
        landed = [window(s, stop) for s, stop in zip(BASE_STRIPS, stops)]
        left = list(range(len(SLICE_PATHS)))
        paths = [SLICE_PATHS[left.pop(rb(len(left)))] for _ in range(DEFECT_CUTS)]
        cells = sorted({(c + 1, r) for path in paths for c, r in enumerate(path)})
        grid = [col[:] for col in landed]
        for c, r in cells:
            grid[c][r] = WILD
        spin = {"stops": stops, "landed": landed, "cuts": [[[c + 1, r] for c, r in enumerate(path)] for path in paths],
                **_bonus_spin(rb, grid, stake)}
        total += spin["mult"]
        spins.append(spin)
    return {"stake": stake, "spins": spins, "mult": round(total, 4), "cash": round(total * stake, 2)}


def heist(rb, stake, every=False):
    """A Vault Heist at `stake` (the keys' average): `locks` is how each lock went until the first that held, `opened`
    how many opened, `mult` what it pays (x its stake) and `cash` in credits. `door` is decided by the manager.
    `every` (demo mode) opens every lock."""
    locks = []
    while len(locks) < HEIST_LOCKS and (not locks or locks[-1]):
        locks.append(True if every else rb(1000) < LOCK_CHANCE)
    opened = sum(locks)
    return {"locks": locks, "opened": opened, "stake": stake, "mult": HEIST_PAYS[opened],
            "cash": round(HEIST_PAYS[opened] * stake, 2), "door": None, "jackpot": 0}


def heist_odds():
    """(chance of each count of locks opened, what a heist pays on average x stake)."""
    q = LOCK_CHANCE / 1000
    chances = [q ** n * (1 - q) for n in range(HEIST_LOCKS)] + [q ** HEIST_LOCKS]
    return chances, sum(c * m for c, m in zip(chances, HEIST_PAYS))


def door_chance(stake):
    """The vault door's chance of opening at a stake, once every lock is open."""
    return DOOR_CHANCE * stake / STAKES[-1]


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


def _shaped(r, kinds):
    """Whether a spin (its free spins included) made one of these shapes of a symbol (fireballs' don't count)."""
    spins = [r] + ((r.get("free_spins") or {}).get("spins") or [])
    return any(x["kind"] in kinds and x["symbol"] != FIRE for sp in spins for x in sp.get("shapes") or [])


# Achievements: (item id in bananas.CATALOG, what it takes, on a finished spin's result). The first spin that shows one
# gives the item, an earned-only cosmetic.
ACHIEVEMENTS = [
    ("tt-vault", lambda r: bool((r.get("heist") or {}).get("door"))),
    ("bd-key", lambda r: (r.get("heist") or {}).get("opened") == HEIST_LOCKS),
    ("bd-bigv", lambda r: _shaped(r, ("bigv",))),
    ("bd-mountain", lambda r: _shaped(r, ("mountain",))),
    ("bd-xmark", lambda r: _shaped(r, ("x",))),
    ("tt-zigzag", lambda r: _shaped(r, ("zigzag",))),
    ("tt-double", lambda r: (r.get("event") or {}).get("kind") == "clone" and r.get("mult", 0) >= 20),
]


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


def _line_ev(dists, wild_mean=1.0, plus=None):
    """`plus` ({reel: {symbol: extra}}) adds to a reel's expected count of a symbol (the Banana split's second half)."""
    masses = [_mass(d) for d in dists]
    plus = plus or {}
    ev = 0.0
    for s in PAYING:
        es = [sum(p * (w.count(s) + wild_mean * w.count(WILD) + w.count(GOLDEN) + w.count(SPICY)) for w, p in d)
              + plus.get(c, {}).get(s, 0.0) for c, d in enumerate(dists)]
        none = [sum(p for w, p in d if not any(_is(x, s) or x in WILDS for x in w)) for d in dists]
        prod = es[0] * es[1]
        for k in (3, 4, 5):
            prod *= es[k - 1]
            ev += PAYS[s][k - 3] * prod * (none[k] * _prod(masses[k + 1:]) if k < REELS else 1.0)
    return ev


# Shapes: whether a spot is made depends on each reel separately (its cells there all show the symbol or a wild), so
# the chance of a set of cells all being made is a product over the reels. A spot pays when it's made, holds
# SHAPE_REAL of the symbol itself (it's made, less made with none, less made with one) and none of its COVERS is made: by inclusion-exclusion, a sum of such products over the cell sets of the spot
# with some of its covers (_cover_terms()). A reel's part is _Reel.part(): its windows coded per cell (0 not the
# symbol, 1 the symbol, 2 a wild, 3 a free spin's multiplying wild) as bit masks, with their chances.

@lru_cache(maxsize=None)
def _cover_terms():
    """For each spot: its cells' row masks per reel, and [(sign, row masks per reel of the union), ...]."""
    from itertools import combinations

    def masks(cells):
        m = [0] * REELS
        for c, r in cells:
            m[c] |= 1 << r
        return tuple(m)
    out = []
    for i, (_, cells, _) in enumerate(SPOTS):
        terms = {}
        for k in range(len(COVERS[i]) + 1):
            for chosen in combinations(COVERS[i], k):
                key = masks(set(cells).union(*(SPOTS[j][1] for j in chosen)))
                terms[key] = terms.get(key, 0) + (-1) ** k
        out.append((masks(cells), [(sign, key) for key, sign in terms.items() if sign]))
    return out


def _fs_below(m):
    """The chance a free spin's wild multiplies by less than m."""
    return sum(w for v, w in FS_WILD_MULTS if v < m) / sum(w for _, w in FS_WILD_MULTS)


class _Reel:
    """One reel's windows for one symbol, as (member mask, wild mask, multiplying-wild mask, chance). part() is the
    chance its cells in `need` all show the symbol or a wild, those in `wild` wilds, those in `real` the symbol itself,
    and those in `own` multiplying by less than `below` (when given)."""
    def __init__(self, coded):
        self.items, self.memo = [], {}
        for pat, p in coded.items():
            self.items.append((sum(1 << r for r, x in enumerate(pat) if x),
                               sum(1 << r for r, x in enumerate(pat) if x >= 2),
                               sum(1 << r for r, x in enumerate(pat) if x == 3), p))

    def part(self, need, own, wild, real, below):
        key = (need, own, wild, real, below)
        got = self.memo.get(key)
        if got is None:
            got = 0.0
            for member, wilds, mults, p in self.items:
                if need & member != need or wild & wilds != wild or real & wilds:
                    continue
                if below and own & mults:
                    p *= _fs_below(below) ** bin(own & mults).count("1")
                got += p
            self.memo[key] = got
        return got


def _code(dist, s, free):
    """A reel's windows as cell codes for symbol s (see above), with their chances."""
    def code(x):
        if x == s:
            return 1
        if s == FIRE:
            return 0
        if free and x == WILD:
            return 3
        return 2 if x in WILDS else 0
    out = {}
    for w, p in dist:
        pat = tuple(code(x) for x in w)
        out[pat] = out.get(pat, 0) + p
    return out


_REELS = {}


def _reel(dist, s, free):
    """_Reel for a distribution object, kept while breakdown() runs, so a reel shared between kinds of spin is coded
    once."""
    key = (id(dist), s, free)
    if key not in _REELS:
        _REELS[key] = (dist, _Reel(_code(dist, s, free)))
    return _REELS[key][1]


def _spots_ev(reels, pay, free=False):
    """What one symbol's shapes pay on `reels` (a _Reel each): pay(i) is spot i's pay; a free spin's is x its biggest
    wild multiplier."""
    levels = sorted({1} | {m for m, _ in FS_WILD_MULTS}) if free else None
    ev = 0.0
    for i, (own, terms) in enumerate(_cover_terms()):
        # The ways it falls short of SHAPE_REAL: (wilds, the symbol itself) as row masks per reel, cell by cell.
        cells = SPOTS[i][1]
        short = [(own, (0,) * REELS)] + ([
            (tuple(m & ~(1 << r) if k == c else m for k, m in enumerate(own)),
             tuple(1 << r if k == c else 0 for k in range(REELS))) for c, r in cells] if SHAPE_REAL == 2 else [])
        got = 0.0
        for sign, need in terms:
            def made(below):
                total = 0.0
                for k, (wild, real) in enumerate([((0,) * REELS, (0,) * REELS)] + short):
                    out = 1.0
                    for c, reel in enumerate(reels):
                        out *= reel.part(need[c], own[c], wild[c], real[c], below)
                        if not out:
                            break
                    total += out if not k else -out
                return total
            e = made(None)
            if levels:  # x its biggest multiplier: the top level, less each step it falls short of
                e *= levels[-1]
                for lo, hi in zip(levels, levels[1:]):
                    e -= (hi - lo) * made(hi)
            got += sign * e
        if got:
            ev += pay(i) * got
    return ev


def _pay_of(s):
    if s == FIRE:
        mean = value_mean()
        return lambda i: FIRE_SHARE * mean * len(SPOTS[i][1])
    return lambda i: shape_pay(s, SPOTS[i][0])


def _shapes_ev(dists, free=False):
    """Every symbol's shapes and (on the base reels) the fireballs', on reel distributions (weighted by _hot() on the
    base reels)."""
    kinds = list(PAYING) + ([] if free else [FIRE])
    return sum(_spots_ev([_reel(d, s, free) for d in dists], _pay_of(s), free) for s in kinds)


def _spot_ev(dists):
    masses = [_mass(d) for d in dists]
    return GOLDEN_SPOT * sum(sum(p * w.count(GOLDEN) for w, p in d) * _prod(masses[:c] + masses[c + 1:])
                             for c, d in enumerate(dists))


def _scatter_ev(dists):
    cd = _count_dist(dists, SPIKE)
    return sum(p * SCATTER_PAYS.get(min(n, 5), 0) for n, p in cd.items() if n >= 3), cd


@lru_cache(maxsize=None)
def _fs_math():
    """Expected value of one free spin, the retrigger chance, and the expected number of spins for each award."""
    d = _dists(FS_STRIPS)
    wm = sum(m * w for m, w in FS_WILD_MULTS) / sum(w for _, w in FS_WILD_MULTS)
    sc_ev, cd = _scatter_ev(d)
    try:
        per_spin = _line_ev(d, wm) + sc_ev + _shapes_ev(d, free=True)
    finally:
        _REELS.clear()
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


def spicy_mean():
    return sum(m * w for m, w in SPICY_MULTS) / sum(w for _, w in SPICY_MULTS)


_HOT = {}


def _hot(dist, beat=False):
    """A reel's windows weighted by what they multiply a win by, on average: wins (and nothing else) are worked out on
    these, so they come out multiplied by every reel's peppers and the Golden Onkey's GOLDEN_MULT. A pepper is drawn on
    its own for each spicy banana, so a window's weight is the mean pepper to the power of how many it shows. Kept per
    distribution object while breakdown() runs, so the shapes' reels are coded once."""
    key = (id(dist), beat)  # beat: a Music break's spin, where a reel showing a drum doubles every win too
    if key not in _HOT:
        mean = spicy_mean()
        _HOT[key] = (dist, [(w, p * mean ** w.count(SPICY) * GOLDEN_MULT ** w.count(GOLDEN)
                             * (BEAT_MULT if beat and DRUM in w else 1)) for w, p in dist])
    return _HOT[key][1]


def value_mean():
    return sum(m * w for m, w in VALUES) / sum(w for _, w in VALUES)


def _mode_math(dists, shapes=True, beat=False):
    """One kind of base spin: what it pays, its features, and how many fireballs it adds to the meter."""
    hot = [_hot(d, beat) for d in dists]
    line = _line_ev(hot)
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
    return {"line": line, "shapes": _shapes_ev(hot) if shapes else 0.0, "spot": _spot_ev(dists), "scatter": sc_ev,
            "free_spins": fs_ev, "hold": hs_cash, "fs_p": fs_p, "hs_p": hs_p, "full_p": full_p,
            "fires": sum(n * p for n, p in fcd.items())}


def _clone_shapes(base, k):
    """The shapes on a clone ray spin that copies reel 1 onto the next k reels: for each symbol, each way reel 1 can
    show it (as cell codes) on reels 1 to k + 1 at once, and the other reels as they are."""
    hot = [_hot(d) for d in base]
    total = 0.0
    for s in list(PAYING) + [FIRE]:
        rest = [_reel(d, s, False) for d in hot[k + 1:]]
        for pat, p in _code(hot[0], s, False).items():
            total += _spots_ev([_Reel({pat: p})] + [_Reel({pat: 1.0}) for _ in range(k)] + rest, _pay_of(s))
    return total


def _set_wild(dist, r):
    """A reel's windows with row r turned into a wild."""
    out = {}
    for w, p in dist:
        v = w[:r] + (WILD,) + w[r + 1:]
        out[v] = out.get(v, 0) + p
    return list(out.items())


@lru_cache(maxsize=None)
def breakdown():
    """The machine's exact return, part by part (per credit staked), and how often things happen (per spin)."""
    try:
        return _breakdown()
    finally:
        _REELS.clear()
        _HOT.clear()


def _breakdown():
    from itertools import combinations
    base = _dists(BASE_STRIPS)
    pe = {k: n / EVENT_TICKETS for k, n in EVENTS.items()}
    calm = 1 - sum(pe.values())  # no event: where the Golden Onkey and the spike plant happen
    g, q = GOLDEN_CHANCE / GOLDEN_TICKETS, PLANT_CHANCE / 1000
    stampede = [_force(d, WILD, STAMPEDE_SURE) if c in (1, 2) else _convert(d, WILD, STAMPEDE_Q / 1000) if c > 2 else d
                for c, d in enumerate(base)]
    terms = [(calm * (1 - g), base), (pe["stampede"], stampede),
             (pe["rain"], [_convert(d, FIRE, RAIN_Q / 1000) for d in base]), (pe["split"], base)]
    # Strudel's slice: each path as likely.
    terms += [(pe["slice"] / len(SLICE_PATHS), [_set_wild(d, path[c - 1]) if 1 <= c <= len(path) else d for c, d in enumerate(base)])
              for path in SLICE_PATHS]
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
    # The Music break: a drum pair on two touching rows of one reel, each placement as likely; its wins doubled for
    # every reel showing a drum (_hot(beat=True)).
    music = []
    for c in range(REELS):
        for r in range(ROWS - 1):
            drums = [(w[:r] + (DRUM, DRUM) + w[r + 2:], p) for w, p in base[c]]
            music.append([drums if i == c else d for i, d in enumerate(base)])
    keys = ("line", "shapes", "spot", "scatter", "free_spins", "hold", "fs_p", "hs_p", "full_p", "fires")
    out = {k: 0.0 for k in keys}
    parts = {}  # what each kind of spin adds to the return
    pay_keys = ("line", "shapes", "spot", "scatter", "free_spins", "hold")
    for kind, (p, dists) in zip(["calm", "stampede", "rain", "split"] + ["slice"] * len(SLICE_PATHS) + ["calm"] * 1000, terms):
        m = _mode_math(dists)
        for k in keys:
            out[k] += p * m[k]
        parts[kind] = parts.get(kind, 0) + p * sum(m[k] for k in pay_keys)
    for dists in music:
        p = pe["music"] / len(music)
        m = _mode_math(dists, beat=True)
        for k in keys:
            out[k] += p * m[k]
        parts["music"] = parts.get("music", 0) + p * sum(m[k] for k in pay_keys)
    # The Banana split: on top of a plain spin, one paying symbol on a random reel counts twice in the ways. On reel c
    # each paying symbol in a window is the one split with chance 1 / (paying symbols showing): that adds to the reel's
    # expected count of its symbol.
    hot = [_hot(d) for d in base]
    plain = _line_ev(hot)
    extra = 0.0
    for c in range(REELS):
        plus = {}
        for w, p in hot[c]:
            n = sum(x in PAYING for x in w)
            for x in w:
                if x in PAYING:
                    plus[x] = plus.get(x, 0.0) + p / n
        extra += (_line_ev(hot, plus={c: plus}) - plain) / REELS
    out["line"] += pe["split"] * extra
    parts["split"] += pe["split"] * extra
    # The clone ray: reel 1's window on the copied reels too, one window at a time (its shapes a symbol at a time).
    copies = sum(w for _, w in CLONE_COPIES)
    for k, wk in CLONE_COPIES:
        p = pe["clone"] * wk / copies
        for w, pw in base[0]:
            m = _mode_math([[(w, pw)]] + [[(w, 1.0)]] * k + base[k + 1:], shapes=False)
            for key in keys:
                out[key] += p * m[key]
            parts["clone"] = parts.get("clone", 0) + p * sum(m[k] for k in ("line", "scatter", "free_spins", "hold"))
        sh = p * _clone_shapes(base, k)
        out["shapes"] += sh
        parts["clone"] += sh
    # The Big Experiment: a clone ray in LAB_FULL starts it (at the photos' average stake, so per credit staked it's
    # this), one spin with reel 1 on every reel, paying ways and shapes.
    k = EXPERIMENT_COPIES
    exp = sum(_line_ev([[(w, pw)]] + [[(w, 1.0)]] * k + base[k + 1:]) for w, pw in base[0]) + _clone_shapes(base, k)
    out["experiment_spin"] = exp
    out["experiment"] = pe["clone"] / LAB_FULL * exp
    # Man Strudel on your side: a slice in FRIEND_FULL starts DEFECT_SPINS spins, each with DEFECT_CUTS different paths cut
    # into wilds, paying ways and shapes (no peppers: a spicy banana is only a wild there).
    from itertools import combinations as pairs_of
    pairs = list(pairs_of(range(len(SLICE_PATHS)), DEFECT_CUTS))
    defect = 0.0
    for chosen in pairs:
        dists = []
        for c, d in enumerate(base):
            rows = {SLICE_PATHS[i][c - 1] for i in chosen} if 1 <= c <= len(SLICE_PATHS[0]) else set()
            for r in rows:
                d = _set_wild(d, r)
            dists.append(d)
        defect += (_line_ev(dists) + _shapes_ev(dists)) / len(pairs)
    out["defect_spin"] = defect
    out["defect"] = pe["slice"] / FRIEND_FULL * DEFECT_SPINS * defect
    # What each event pays on average when it comes (x stake), everything on the spin included.
    out["event_pays"] = {k: parts[k] / pe[k] for k in EVENTS if pe[k]}
    out["plant_p"], out["golden_p"] = plant_p, golden_p
    # The Vault Heist: on a Golden Onkey spin with a key. The door's jackpot is the house's, not the machine's.
    chances, mean = heist_odds()
    out["heist_p"] = golden_p / KEYS_FULL  # per spin at one stake: a key a Golden Onkey, KEYS_FULL keys a heist
    out["heist"] = out["heist_p"] * mean
    out["door_p"] = out["heist_p"] * chances[-1] * DOOR_CHANCE / STAKES[-1]  # per credit staked
    # How often a spicy banana lands (on a spin without an event: the events can cover one).
    out["spicy_p"] = 1 - _prod(sum(p for w, p in d if SPICY not in w) for d in base)
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
    out["rtp"] = (out["line"] + out["shapes"] + out["spot"] + out["heist"] + out["experiment"] + out["defect"]
                  + out["scatter"] + out["free_spins"] + out["hold"] + out["jackpots"])
    out["experiment_p"] = pe["clone"] / LAB_FULL
    out["defect_p"] = pe["slice"] / FRIEND_FULL
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
FORCES = ("win", "big", "diagonal", "v", "big_shape", "fire_shape", "spicy", "golden", "planted", "detonated", "stampede",
          "rain", "clone", "slice", "split", "music", "experiment", "defect", "heist", "vault", "free_spins", "hold", "full", "pick", "smoke", "mini", "minor", "major",
          "grand")


def _forced(force, rb=secrets.randbelow):
    """A spin that shows `force`, drawn again until it does (demo mode only). A full grid is filled on purpose; the
    pick's forces only need a spin with a fireball."""
    want = {
        "win": lambda r: r["line_mult"] > 0,
        "big": lambda r: cash_mult(r) >= 15,
        "stampede": lambda r: r["event"] and r["event"]["kind"] == "stampede",
        "clone": lambda r: r["event"] and r["event"]["kind"] == "clone",
        "experiment": lambda r: r["event"] and r["event"]["kind"] == "clone",  # the manager tops the board up
        "defect": lambda r: r["event"] and r["event"]["kind"] == "slice",
        "split": lambda r: r["event"] and r["event"]["kind"] == "split" and r["event"]["cell"] and r["wins"],
        "music": lambda r: r["event"] and r["event"]["kind"] == "music" and r["beat"],
        "slice": lambda r: r["event"] and r["event"]["kind"] == "slice",
        "rain": lambda r: r["event"] and r["event"]["kind"] == "rain",
        "spicy": lambda r: r["heat"],
        "diagonal": lambda r: any(x["kind"] in ("diag", "diag4") for x in r["shapes"]),
        "v": lambda r: any(x["kind"] in ("v", "peak", "bigv", "mountain") for x in r["shapes"]),
        "big_shape": lambda r: any(len(x["cells"]) == 5 and x["symbol"] != FIRE for x in r["shapes"]),
        "fire_shape": lambda r: any(x["symbol"] == FIRE for x in r["shapes"]),
        "golden": lambda r: r["golden_x"],
        "heist": lambda r: r["golden"],  # the manager tops the keys up, so his key is the last one
        "vault": lambda r: r["golden"],
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
        "shape_base": SHAPE_BASE, "shapes": {k: {"name": n, "factor": f, "forms": forms} for k, (n, f, forms) in SHAPES.items()},
        "fire_share": FIRE_SHARE, "plant_chance": b["plant_p"],
        "fs_retrigger": FS_RETRIGGER, "fs_cap": FS_CAP, "fs_wild_mults": [m for m, _ in FS_WILD_MULTS],
        "hs_trigger": HS_TRIGGER, "hs_respins": HS_RESPINS, "full_grid_bonus": FULL_GRID_BONUS,
        "values": [{"mult": m, "chance": w / total} for m, w in VALUES],
        "jackpots": [{"key": k, "name": j["name"], "seed": j["seed"]} for k, j in JACKPOTS.items()],
        "meter_full": METER_FULL, "fires_per_spin": b["fires"], "pick_kinds": PICK_KINDS,
        "pick_chances": {(o or "smoke"): w / picks for o, w in PICK_OUTCOMES},
        "spicy_mults": [m for m, _ in SPICY_MULTS], "spicy_chance": b["spicy_p"],
        "lab_full": LAB_FULL, "friend_full": FRIEND_FULL, "defect_spins": DEFECT_SPINS, "beat_mult": BEAT_MULT,
        "vault": {"keys": KEYS_FULL, "locks": HEIST_LOCKS, "lock_chance": LOCK_CHANCE / 1000, "pays": HEIST_PAYS, "chance": b["heist_p"],
                  "door": {str(s): door_chance(s) for s in STAKES}},
        "strips": BASE_STRIPS, "fs_strips": FS_STRIPS,
        "rtp": round(b["rtp"] * 100, 2), "fs_chance": b["fs_p"], "hs_chance": b["hs_p"],
        "pick_per_credit": b["pick_p"], "event_chance": b["event_p"], "jackpots_per_credit": b["jackpot_hits"],
    }


def _features(result):
    """The features a stored spin showed: free spins, hold and spin, its event, its spicy bananas' heat (the Inferno's
    multiplier on spins from before them), the spike plant, the pick, its jackpot."""
    heist_ = result.get("heist") or {}
    return {"free_spins": bool(result.get("free_spins")), "hold": bool(result.get("hold")), "plant": bool(result.get("plant")),
            "experiment": bool(result.get("experiment")), "defect": bool(result.get("defect")),
            "heist": heist_.get("opened") if heist_ else None, "vault": heist_.get("jackpot") or 0,
            "event": (result.get("event") or {}).get("kind"), "inferno": result.get("inferno"), "heat": result.get("heat"),
            "pick": bool(result.get("pick")), "jackpots": [j["key"] for j in result.get("jackpots") or []]}


class StampedeManager:
    def __init__(self, db, demo=False, house=None):
        # `house` (HouseManager) holds the JACKPOT the vault door pays; without one the door has nothing to pay.
        self.db, self.demo, self.house = db, demo, house
        self._jackpot = (0.0, None)  # (when, size): the jackpot, cached for JACKPOT_CACHE_S between page polls
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

    def jackpot(self, fresh=False):
        """The JACKPOT the vault door pays: the house's progressive jackpot, in whole credits (the daily wheel's)."""
        if not self.house:
            return None
        at, size = self._jackpot
        if fresh or size is None or time.time() - at > JACKPOT_CACHE_S:
            size = int(max(0.0, self.house.summary_pots()[1]))
            self._jackpot = (time.time(), size)
        return size

    def pots(self):
        rows = {r["key"]: r for r in self.db.query("SELECT * FROM stampede_pots")}
        return [{"key": k, "name": j["name"], "seed": j["seed"], "grow": j["grow"],
                 "size": round(rows[k]["size"], 2) if k in rows else j["seed"],
                 "hits": rows[k]["hits"] if k in rows else 0} for k, j in JACKPOTS.items()]

    def progress(self, name):
        """A bettor's evidence board and Man Strudel's friendship: the stakes of the spins that filled each."""
        r = self.db.query_one("SELECT lab, friend FROM stampede_progress WHERE lower(bettor)=lower(?)", (name,))
        return {"lab": json.loads(r["lab"]) if r else [], "friend": json.loads(r["friend"]) if r else [],
                "lab_full": LAB_FULL, "friend_full": FRIEND_FULL}

    def keys(self, name):
        """A bettor's key meter: the stake of each key held (oldest first), and how many make a heist."""
        r = self.db.query_one("SELECT stakes FROM stampede_keys WHERE lower(bettor)=lower(?)", (name,))
        return {"stakes": json.loads(r["stakes"]) if r else [], "full": KEYS_FULL}

    def daily(self, name, now=None):
        """A bettor's daily spins today: how many are left, of DAILY_SPINS, at DAILY_STAKE (none without a house)."""
        if not self.house:
            return None
        used = self.db.query_one("SELECT COUNT(*) AS n FROM house_payouts WHERE kind='stampede_daily' AND ref LIKE ?",
                                 (f"stampede-daily:{name.lower()}:{wheel_day(now or time.time())}:%",))["n"]
        return {"left": max(0, DAILY_SPINS - used), "total": DAILY_SPINS, "stake": DAILY_STAKE}

    def achievements(self, name):
        """Each achievement, what it gives and whether the bettor has it."""
        from .bananas import ITEMS
        owned = {r["item_id"] for r in self.db.query("SELECT item_id FROM banana_items WHERE lower(bettor)=lower(?)", (name,))}
        return [{"id": i, "name": ITEMS[i]["name"], "slot": ITEMS[i]["slot"], "earn": ITEMS[i]["earn"], "look": ITEMS[i]["look"],
                 "have": i in owned} for i, _ in ACHIEVEMENTS]

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

    def spin(self, name, stake, request_id, force=None, daily=False):
        """One spin. `daily` makes it one of the day's free spins: the stake is DAILY_STAKE and the house gives it."""
        if daily:
            stake = DAILY_STAKE
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
                        "meter": self.meter(name), "keys": self.keys(name), "jackpot": self.jackpot(),
                        "daily": self.daily(name), "progress": self.progress(name)}
            now = time.time()
            if daily:  # the house gives the stake, once per daily spin (the ref carries the request, so a retry can't)
                left = self.daily(name, now)
                if not left or not left["left"]:
                    raise BetError("No daily spins left today. They come back at midnight Pacific.")
                self.house.pay(name, DAILY_STAKE, "stampede_daily",
                               f"stampede-daily:{name.lower()}:{wheel_day(now)}:{request_id}", None, "Onkey Stampede: a daily spin")
                bettor = self.db.get_bettor(name)
            if bettor["balance"] < stake:
                raise BetError("Not enough credits for that spin. Choose a smaller stake.")
            result = _forced(force) if force else play()
            result["daily"] = bool(daily)
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
            # The key meter: the Golden Onkey's key goes in at this stake; KEYS_FULL keys start the Vault Heist, at the
            # keys' average stake. Past every lock, the door: its chance grows with that stake, and it pays the house's
            # jackpot.
            keys = self.keys(name)["stakes"]
            if force in ("heist", "vault"):
                keys = (keys + [stake] * KEYS_FULL)[:KEYS_FULL - 1]  # demo mode tops the keys up
            result["keys"] = {"before": len(keys), "full": KEYS_FULL}
            if result.get("key"):
                keys.append(stake)
            vault = 0
            if len(keys) >= KEYS_FULL:
                hs = heist(secrets.randbelow, round(sum(keys[:KEYS_FULL]) / KEYS_FULL, 2), every=force == "vault")
                keys = keys[KEYS_FULL:]
                result["heist"] = hs
                if hs["opened"] == HEIST_LOCKS:
                    hs["door"] = force == "vault" or secrets.randbelow(DOOR_TICKETS) < door_chance(hs["stake"]) * DOOR_TICKETS
                    if hs["door"]:
                        vault = self.jackpot(fresh=True) or 0
                        hs["jackpot"] = vault
            result["keys"]["after"] = len(keys)
            # The evidence board and Man Strudel's friendship fill from the clone ray and the slice; full, their bonus
            # plays at the average stake of the spins that filled it.
            prog = self.progress(name)
            lab, friend = prog["lab"], prog["friend"]
            kind = (result.get("event") or {}).get("kind")
            if force == "experiment":
                lab = (lab + [stake] * LAB_FULL)[:LAB_FULL - 1]
            if force == "defect":
                friend = (friend + [stake] * FRIEND_FULL)[:FRIEND_FULL - 1]
            result["progress"] = {"lab": len(lab) + (kind == "clone"), "friend": len(friend) + (kind == "slice")}
            if kind == "clone":
                lab.append(stake)
                if len(lab) >= LAB_FULL:
                    result["experiment"] = big_experiment(secrets.randbelow, round(sum(lab[:LAB_FULL]) / LAB_FULL, 2))
                    lab = lab[LAB_FULL:]
            if kind == "slice":
                friend.append(stake)
                if len(friend) >= FRIEND_FULL:
                    result["defect"] = strudel_spins(secrets.randbelow, round(sum(friend[:FRIEND_FULL]) / FRIEND_FULL, 2))
                    friend = friend[FRIEND_FULL:]
            cash = cash_mult(result)
            payout = round(stake * cash + sum((result.get(k) or {}).get("cash", 0) for k in ("heist", "experiment", "defect"))
                           + sum(j["amount"] for j in jackpots), 2)
            mult = round(payout / stake, 4)
            result.update({"cash_mult": cash, "jackpots": jackpots, "mult": mult})
            # Achievements: the earned-only cosmetics this spin unlocks for the first time.
            owned = {r["item_id"] for r in self.db.query(
                "SELECT item_id FROM banana_items WHERE lower(bettor)=lower(?)", (name,))}
            result["unlocked"] = [i for i, test in ACHIEVEMENTS if i not in owned and test(result)]
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
                self.db.conn.execute(
                    "INSERT INTO stampede_progress(bettor, lab, friend, updated_ts) VALUES(?,?,?,?) "
                    "ON CONFLICT(bettor) DO UPDATE SET lab=excluded.lab, friend=excluded.friend, updated_ts=excluded.updated_ts",
                    (name, json.dumps(lab), json.dumps(friend), now))
                self.db.conn.execute(
                    "INSERT INTO stampede_keys(bettor, stakes, updated_ts) VALUES(?,?,?) "
                    "ON CONFLICT(bettor) DO UPDATE SET stakes=excluded.stakes, updated_ts=excluded.updated_ts",
                    (name, json.dumps(keys), now))
                for item_id in result["unlocked"]:
                    self.db.conn.execute("INSERT INTO banana_items(bettor, item_id, price, bought_ts) VALUES(?,?,?,?)",
                                         (name, item_id, 0, now))
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
            if vault:  # from the house's jackpot, once per spin, like the daily wheel's
                self.house.pay(name, vault, "jackpot", f"stampede:{sid}", None, "Onkey Stampede: the Vault Heist")
                self._jackpot = (0.0, None)
            row = self.db.query_one("SELECT * FROM stampede_spins WHERE id=?", (sid,))
            return {"spin": self.public(row), "balance": round(self.db.get_bettor(name)["balance"], 2),
                    "pots": self.pots(), "meter": self.meter(name), "keys": self.keys(name), "jackpot": self.jackpot(),
                    "daily": self.daily(name), "progress": self.progress(name)}

    def big_wins(self, limit=5):
        """This season's biggest wins by anyone (a payout above the stake), biggest first."""
        return [self.brief(r) for r in self.db.query(
            "SELECT * FROM stampede_spins WHERE season_id IS NULL AND payout > stake ORDER BY payout DESC, id LIMIT ?",
            (limit,))]

    def feed(self, limit=20, name=None):
        """The latest notable spins, newest first (everyone's, or one bettor's): a payout of FEED_MULT x the stake or
        more, a bonus, a heist, a jackpot, or an achievement unlocked. Each is brief(), plus its best shape and what
        it unlocked."""
        rows = self.db.query(
            "SELECT * FROM stampede_spins WHERE (payout >= stake * ? OR result LIKE '%\"heist\":{%' OR result LIKE '%\"jackpots\":[{%' "
            "OR result LIKE '%\"free_spins\":{%' OR result LIKE '%\"hold\":{%' OR result LIKE '%\"experiment\":{%' OR result LIKE '%\"defect\":{%' OR result LIKE '%\"unlocked\":[\"%')"
            + (" AND lower(bettor)=lower(?)" if name else "") + " ORDER BY id DESC LIMIT ?",
            (FEED_MULT, name, limit) if name else (FEED_MULT, limit))
        out = []
        for r in rows:
            res = json.loads(r["result"])
            shapes = [x for x in res.get("shapes") or [] if x["kind"] in SHAPES]
            best = max(shapes, key=lambda x: SHAPES[x["kind"]][1], default=None)
            out.append({**self.brief(r), "shape": SHAPES[best["kind"]][0] if best and SHAPES[best["kind"]][1] >= 4 else None,
                        "unlocked": res.get("unlocked") or [], "daily": bool(res.get("daily"))})
        return out

    def jackpot_log(self, limit=8):
        """The latest jackpots won, every season."""
        return self.db.query(
            "SELECT bettor, key, amount, created_ts FROM stampede_jackpots ORDER BY id DESC LIMIT ?", (limit,))

    def summary(self, me=None):
        out = {"machine": machine(), "pots": self.pots(), "jackpot": self.jackpot(), "jackpot_log": self.jackpot_log(),
               "big_wins": self.big_wins(), "feed": self.feed(), "october": october(),
               "forces": list(FORCES) if self.demo else [], "me": None, "history": [], "meter": None, "keys": None,
               "daily": None, "achievements": None, "my_feed": [], "progress": None}
        if me:
            with self.db.lock:
                name = me["name"]
                rows = self.db.query(
                    "SELECT * FROM stampede_spins WHERE bettor=? AND season_id IS NULL ORDER BY id DESC", (name,))
                out["meter"] = self.meter(name)
                out["keys"] = self.keys(name)
                out["progress"] = self.progress(name)
                out["daily"] = self.daily(name)
                out["achievements"] = self.achievements(name)
                out["my_feed"] = self.feed(name=name)
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
