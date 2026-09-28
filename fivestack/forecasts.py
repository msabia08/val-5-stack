"""Forecasts tab: each player's games against what the odds engine would have predicted for them beforehand.

Every complete 5-stack game is predicted from that player's own earlier 5-stack games only, weighted exactly like
the odds (recency, same map, same agent: see OddsEngine.samples), so the page is an honest replay and also a check
on the player-prop lines (kills, deaths and assists per game are exactly those lines; the per-round versions take
game length out, since a 26-round game gives more kills than a 16-round one).

Each forecast (OddsEngine.stat_range) has a range from the smoothed distribution's percentiles that should hold
COVERAGE of games (lopsided when the stat is), the typical game (its median, where a betting line sits) and the
expected value (its mean). "Better / worse than forecast" compares the actual value with the range; the average
difference uses the expected value, so a well-calibrated forecast averages out to zero even for skewed stats.

One stat at a time (the page fetches the one it shows), with full detail for one player: results are grouped into
map x role cells (agents inside each cell), with map and role totals, for the grid on the page.
"""
from collections import defaultdict

from .gamestate import COMPLETE, ending, full_game_rounds
from .insights import AGENT_ROLE, ROLES
from .odds import STAT_DEFS
from .stats import player_metrics

MIN_PRIOR = 5  # a game gets a forecast once the player has this many earlier 5-stack games
HISTORY = 60  # earlier games used per forecast; older ones weigh under 7% at the default 15-game half-life
COVERAGE = 0.8  # share of games each forecast range should hold
MIN_CELL = 3  # games a map x role cell needs before the takeaway names it
LOWER_IS_BETTER = {"deaths", "dpr"}
PCT_CAP = {"hs_pct": 100.0}
# group: "game" = per-game counts (the player-prop lines), "round" = the same per round, "score" = ACS, ADR, HS%.
# floor_h: smoothing floor; the per-round ones are about the counts' floors spread over a 22-round game.
PER_ROUND = [{"key": "kpr", "label": "Kills per round", "short": "Kills", "floor_h": 0.055},
             {"key": "dpr", "label": "Deaths per round", "short": "Deaths", "floor_h": 0.055},
             {"key": "apr", "label": "Assists per round", "short": "Assists", "floor_h": 0.045}]
STATS = ([{**d, "short": d["label"], "group": "game"} for d in STAT_DEFS if d["kind"] == "count"]
         + [{**d, "group": "round"} for d in PER_ROUND]
         + [{**d, "short": d["label"], "group": "score"} for d in STAT_DEFS if d["kind"] != "count"])
STAT = {d["key"]: d for d in STATS}
DIGITS = {"kpr": 3, "dpr": 3, "apr": 3}  # decimals kept; everything else is rounded to 1


def role_of(agent):
    return AGENT_ROLE.get((agent or "").lower(), "Unknown")


def _forecastable(rows):
    """Indexes of the rows (newest first) that get a forecast: complete games with MIN_PRIOR earlier ones."""
    return [i for i, r in enumerate(rows) if len(rows) - i - 1 >= MIN_PRIOR and ending(r) == COMPLETE]


def forecast_one(row, earlier, engine, full, key):
    """The forecast for one game (row) from the player's earlier 5-stack lines (newest first), rounded like the page
    shows it: {"range": [low, high], "typical", "expected"}. None when there's no data for the stat."""
    samples = engine.samples(earlier[:HISTORY], full, row.get("map"), row.get("agent"), relative=False)
    fc = engine.stat_range(samples, key, COVERAGE, STAT[key]["floor_h"])
    if fc is None:
        return None
    dp, cap = DIGITS.get(key, 1), PCT_CAP.get(key)
    low, typical, high, expected = fc
    if cap is not None:
        high = min(high, cap)
    return {"range": [round(low, dp), round(high, dp)], "typical": round(typical, dp), "expected": round(expected, dp)}


def _forecast_games(rows, engine, full, key):
    """rows: one member's lines, newest first. Returns their forecast games for one stat, oldest first."""
    games, dp = [], DIGITS.get(key, 1)
    for i in _forecastable(rows):
        r = rows[i]
        prior = rows[i + 1:i + 1 + HISTORY]
        actual = player_metrics(r, (r.get("rounds_won") or 0) + (r.get("rounds_lost") or 0)).get(key)
        fc = forecast_one(r, prior, engine, full, key)
        if fc is None or actual is None:
            continue
        games.append({"match_id": r["match_id"], "ts": r.get("started_ts"), "map": r.get("map") or "Unknown",
                      "agent": r.get("agent"), "role": role_of(r.get("agent")), "result": r.get("result"),
                      "rounds_won": r.get("rounds_won"), "rounds_lost": r.get("rounds_lost"), "prior": len(prior),
                      **fc, "actual": round(actual, dp)})
    games.reverse()
    return games


def _place(g):
    return "above" if g["actual"] > g["range"][1] else "below" if g["actual"] < g["range"][0] else "inside"


def _cell(games, dp=1):
    """Averages (to dp decimals) and range hits for one group of forecast games."""
    if not games:
        return None
    n = len(games)
    avg = lambda f: round(sum(f(g) for g in games) / n, dp)  # noqa: E731
    places = [_place(g) for g in games]
    agents = defaultdict(list)
    for g in games:
        agents[g["agent"] or "Unknown"].append(g)
    return {
        "games": n, "low": avg(lambda g: g["range"][0]), "high": avg(lambda g: g["range"][1]),
        "typical": avg(lambda g: g["typical"]), "expected": avg(lambda g: g["expected"]),
        "actual": avg(lambda g: g["actual"]), "diff": avg(lambda g: g["actual"] - g["expected"]),
        "above": places.count("above"), "inside": places.count("inside"), "below": places.count("below"),
        "agents": sorted(({"agent": a, "games": len(gs), "actual": round(sum(g["actual"] for g in gs) / len(gs), dp),
                           "expected": round(sum(g["expected"] for g in gs) / len(gs), dp)} for a, gs in agents.items()),
                         key=lambda x: -x["games"]),
    }


def _grid(games, key):
    """Map x role cells plus map totals (role None) and role totals (map None), and where the player beat or missed
    the forecast most."""
    dp = DIGITS.get(key, 1)
    overall = _cell(games, dp)
    if not overall:
        return None
    sign = -1 if key in LOWER_IS_BETTER else 1
    cells = []
    for m in sorted({g["map"] for g in games}):
        for role in [None] + sorted({g["role"] for g in games}):
            c = _cell([g for g in games if g["map"] == m and (role is None or g["role"] == role)], dp)
            if c:
                cells.append({"map": m, "role": role, **c})
    for role in sorted({g["role"] for g in games}):
        c = _cell([g for g in games if g["role"] == role], dp)
        if c:
            cells.append({"map": None, "role": role, **c})
    named = [c for c in cells if c["map"] and c["role"] and c["games"] >= MIN_CELL]
    best = max(named, key=lambda c: sign * c["diff"], default=None)
    worst = min(named, key=lambda c: sign * c["diff"], default=None)
    brief = lambda c: {k: c[k] for k in ("map", "role", "diff", "games")}  # noqa: E731
    return {"overall": overall, "cells": cells,  # best / worst: where they beat or miss the forecast most
            "best": brief(best) if best and sign * best["diff"] > 0 else None,
            "worst": brief(worst) if worst and sign * worst["diff"] < 0 else None}


def build_forecasts(db, engine, stat="acs", puuid=None):
    """The Forecasts tab's data (/api/forecasts?stat=kills&player=<puuid>): every player's count of forecast games
    for the picker, and the full detail (games, grid) for one player, the first with forecasts by default."""
    if stat not in STAT:
        raise ValueError(f"unknown stat {stat!r}")
    members, matches = db.members(), db.matches()
    out = {"stats": [{"key": d["key"], "label": d["label"], "short": d["short"], "group": d["group"],
                      "lower_is_better": d["key"] in LOWER_IS_BETTER} for d in STATS],
           "stat": stat, "roles": list(ROLES) + ["Unknown"], "coverage": COVERAGE, "min_prior": MIN_PRIOR,
           "min_cell": MIN_CELL, "players": [], "player": None}
    if not members or not matches:
        return out
    by_member = defaultdict(list)
    for r in db.player_rows():  # newest first
        by_member[r["puuid"]].append(r)
    for m in members:
        rows = by_member.get(m["puuid"], [])
        out["players"].append({"puuid": m["puuid"], "nickname": m.get("nickname") or m["name"],
                               "total_games": len(rows), "forecast_games": len(_forecastable(rows))})
    picked = next((p for p in out["players"] if p["puuid"] == puuid), None) \
        or next((p for p in out["players"] if p["forecast_games"]), out["players"][0])
    games = _forecast_games(by_member.get(picked["puuid"], []), engine, full_game_rounds(matches), stat)
    out["player"] = {**picked, "games": games,
                     **(_grid(games, stat) or {"overall": None, "cells": [], "best": None, "worst": None})}
    return out
