"""Forecasts tab: each player's games against what the odds engine would have predicted for them beforehand.

Every complete 5-stack game is predicted from that player's own earlier 5-stack games only, weighted exactly like
the odds (recency, same map, same agent: see OddsEngine.samples), so the page is an honest replay and also a check
on the player-prop lines (kills, deaths and assists per game are exactly those lines; the per-round versions take
game length out, since a 26-round game gives more kills than a 16-round one). Each prediction is a range meant to
hold COVERAGE of games. One stat at a time (the page
fetches the one it shows): results are grouped into map x role cells (agents inside each cell), with map and role
totals, for the grid on the page.
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


def _forecast_games(rows, engine, full, key):
    """rows: one member's lines, newest first. Returns their forecast games for one stat, oldest first."""
    games = []
    for i, r in enumerate(rows):
        prior = rows[i + 1:i + 1 + HISTORY]
        if len(prior) < MIN_PRIOR or ending(r) != COMPLETE:  # a surrender's numbers aren't a full game's
            continue
        samples = engine.samples(prior, full, r.get("map"), r.get("agent"), relative=False)
        actual = player_metrics(r, (r.get("rounds_won") or 0) + (r.get("rounds_lost") or 0))
        rng = engine.stat_range(samples, key, COVERAGE, STAT[key]["floor_h"])
        if rng is None or actual.get(key) is None:
            continue
        lo, mid, hi = rng
        dp = DIGITS.get(key, 1)
        games.append({"match_id": r["match_id"], "ts": r.get("started_ts"), "map": r.get("map") or "Unknown",
                      "agent": r.get("agent"), "role": role_of(r.get("agent")), "result": r.get("result"),
                      "rounds_won": r.get("rounds_won"), "rounds_lost": r.get("rounds_lost"),
                      "prior": len(prior), "pred": [round(lo, dp), round(mid, dp), round(min(hi, PCT_CAP.get(key, hi)), dp)],
                      "actual": round(actual[key], dp)})
    games.reverse()
    return games


def _place(value, lo, hi):
    return "above" if value > hi else "below" if value < lo else "inside"


def _cell(games, dp=1):
    """Averages (to dp decimals) and range hits for one group of forecast games."""
    if not games:
        return None
    n = len(games)
    avg = lambda f: round(sum(f(g) for g in games) / n, dp)  # noqa: E731
    places = [_place(g["actual"], g["pred"][0], g["pred"][2]) for g in games]
    agents = defaultdict(list)
    for g in games:
        agents[g["agent"] or "Unknown"].append(g)
    return {
        "games": n, "lo": avg(lambda g: g["pred"][0]), "mid": avg(lambda g: g["pred"][1]), "hi": avg(lambda g: g["pred"][2]),
        "actual": avg(lambda g: g["actual"]), "diff": avg(lambda g: g["actual"] - g["pred"][1]),
        "above": places.count("above"), "inside": places.count("inside"), "below": places.count("below"),
        "agents": sorted(({"agent": a, "games": len(gs), "actual": round(sum(g["actual"] for g in gs) / len(gs), dp),
                           "mid": round(sum(g["pred"][1] for g in gs) / len(gs), dp)} for a, gs in agents.items()),
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
    """The Forecasts tab's data (/api/forecasts?stat=kills&player=<puuid>): every player's overall record against
    the forecast for the picker, and the full detail (games, grid) for one player, the first by default."""
    if stat not in STAT:
        raise ValueError(f"unknown stat {stat!r}")
    members, matches = db.members(), db.matches()
    out = {"stats": [{"key": d["key"], "label": d["label"], "short": d["short"], "group": d["group"],
                      "lower_is_better": d["key"] in LOWER_IS_BETTER} for d in STATS],
           "stat": stat, "roles": list(ROLES) + ["Unknown"], "coverage": COVERAGE, "min_prior": MIN_PRIOR,
           "min_cell": MIN_CELL, "players": [], "player": None}
    if not members or not matches:
        return out
    full = full_game_rounds(matches)
    by_member = defaultdict(list)
    for r in db.player_rows():  # newest first
        by_member[r["puuid"]].append(r)
    if not any(m["puuid"] == puuid for m in members):
        puuid = members[0]["puuid"]
    for m in members:
        rows = by_member.get(m["puuid"], [])
        games = _forecast_games(rows, engine, full, stat)
        overall = _cell(games, DIGITS.get(stat, 1))
        summary = {"puuid": m["puuid"], "nickname": m.get("nickname") or m["name"], "total_games": len(rows),
                   "overall": overall and {k: v for k, v in overall.items() if k != "agents"}}
        out["players"].append(summary)
        if m["puuid"] == puuid:
            out["player"] = {**summary, "games": games, **(_grid(games, stat) or {"cells": [], "best": None, "worst": None})}
    return out
