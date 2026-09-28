"""Aggregation of per-game member lines into overall / per-agent / per-map stats."""
import math
import time
from collections import defaultdict

# Stats compared between a member's 5-stack games and their other games: key, label, +1 if higher is better.
DEVIATION_METRICS = [
    ("acs", "ACS", 1),
    ("kpr", "Kills / round", 1),
    ("dpr", "Deaths / round", -1),
    ("apr", "Assists / round", 1),
    ("adr", "ADR", 1),
    ("hs_pct", "Headshot %", 1),
    ("kd", "K/D", 1),
    ("win_rate", "Win rate", 1),
]
MIN_DEVIATION_GAMES = 5  # per side, before a difference is judged at all
CLEAR_Z, LEANING_Z = 2.0, 1.0


def safe_div(a, b):
    return (a / b) if b else 0.0


def player_metrics(p, rounds):
    """Derived per-game numbers for one member line. `rounds` is total rounds in the match."""
    rounds = rounds or 1
    kills = p.get("kills") or 0
    deaths = p.get("deaths") or 0
    assists = p.get("assists") or 0
    score = p.get("score") or 0
    dmg = p.get("damage_dealt") or 0
    heads = p.get("headshots") or 0
    shots = heads + (p.get("bodyshots") or 0) + (p.get("legshots") or 0)
    return {
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "score": score,
        "acs": safe_div(score, rounds),
        "adr": safe_div(dmg, rounds),
        "hs_pct": (heads / shots * 100.0) if shots else None,
        "kd": kills / max(1, deaths),
        "kda": (kills + assists) / max(1, deaths),
        "kpr": safe_div(kills, rounds),
        "dpr": safe_div(deaths, rounds),
        "apr": safe_div(assists, rounds),
        "rounds": rounds,
    }


def _rounds(row):
    return (row.get("rounds_won") or 0) + (row.get("rounds_lost") or 0)


def aggregate(rows):
    g = len(rows)
    if g == 0:
        return {"games": 0, "wins": 0, "losses": 0, "draws": 0, "win_rate": None}
    wins = sum(1 for r in rows if r.get("result") == "win")
    losses = sum(1 for r in rows if r.get("result") == "loss")
    draws = g - wins - losses
    k = sum(r.get("kills") or 0 for r in rows)
    d = sum(r.get("deaths") or 0 for r in rows)
    a = sum(r.get("assists") or 0 for r in rows)
    s = sum(r.get("score") or 0 for r in rows)
    dmg = sum(r.get("damage_dealt") or 0 for r in rows)
    heads = sum(r.get("headshots") or 0 for r in rows)
    shots = heads + sum((r.get("bodyshots") or 0) + (r.get("legshots") or 0) for r in rows)
    rounds = sum(_rounds(r) for r in rows)
    return {
        "games": g,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "win_rate": wins / g,
        "avg_kills": k / g,
        "avg_deaths": d / g,
        "avg_assists": a / g,
        "avg_score": s / g,
        "kd": k / max(1, d),
        "kda": (k + a) / max(1, d),
        "acs": safe_div(s, rounds),
        "adr": safe_div(dmg, rounds),
        "hs_pct": (heads / shots * 100.0) if shots else None,
        "kpr": safe_div(k, rounds),
        "avg_rounds": rounds / g,
    }


def _group(rows, key):
    groups = defaultdict(list)
    for r in rows:
        groups[r.get(key) or "Unknown"].append(r)
    out = []
    for name, grp in groups.items():
        agg = aggregate(grp)
        agg[key] = name
        agg["last_played"] = grp[0].get("started_at")
        out.append(agg)
    out.sort(key=lambda x: (-x["games"], x[key]))
    return out


def _best(rows, metric_key):
    best = None
    for r in rows:
        m = player_metrics(r, _rounds(r))
        v = m.get(metric_key)
        if v is None:
            continue
        if best is None or v > best["value"]:
            best = {
                "value": v,
                "match_id": r["match_id"],
                "map": r.get("map"),
                "agent": r.get("agent"),
                "started_at": r.get("started_at"),
                "result": r.get("result"),
            }
    return best


def _streak(matches):
    streak = 0
    kind = None
    for m in matches:
        res = m.get("result")
        if res == "draw":
            continue
        if kind is None:
            kind = res
        if res != kind:
            break
        streak += 1
    if not kind or streak == 0:
        return None
    return f"{'W' if kind == 'win' else 'L'}{streak}"


def _game_values(r):
    rounds = _rounds(r)
    met = player_metrics(r, rounds)
    met["win_rate"] = 1.0 if r.get("result") == "win" else 0.0
    return met


def _std_err(values):
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1) / n)


def _verdict(z, sign):
    if z is None:
        return "too_few"
    z *= sign
    if z >= CLEAR_Z:
        return "better"
    if z <= -CLEAR_Z:
        return "worse"
    if z >= LEANING_Z:
        return "leaning_better"
    if z <= -LEANING_Z:
        return "leaning_worse"
    return "same"


def deviation(stack_rows, base_rows):
    """How a member's 5-stack games differ from their other games.

    Values are pooled the same way as `aggregate` (e.g. ACS = total score / total rounds). The
    difference is judged with a two-sample z statistic built from the per-game spread on each side,
    so a big gap over a handful of games still reads as noise.
    """
    stack_rows = [r for r in stack_rows if _rounds(r)]
    base_rows = [r for r in base_rows if _rounds(r)]
    if not base_rows:
        return None
    pooled = {}
    for side, rows in (("stack", stack_rows), ("usual", base_rows)):
        agg = aggregate(rows)
        rounds = sum(_rounds(r) for r in rows)
        agg["dpr"] = safe_div(sum(r.get("deaths") or 0 for r in rows), rounds)
        agg["apr"] = safe_div(sum(r.get("assists") or 0 for r in rows), rounds)
        pooled[side] = agg
    enough = len(stack_rows) >= MIN_DEVIATION_GAMES and len(base_rows) >= MIN_DEVIATION_GAMES
    stack_games = [_game_values(r) for r in stack_rows]
    base_games = [_game_values(r) for r in base_rows]

    metrics = []
    for key, label, sign in DEVIATION_METRICS:
        s, b = pooled["stack"].get(key), pooled["usual"].get(key)
        if s is None or b is None:
            continue
        diff = s - b
        z = None
        if enough:
            se_s = _std_err([g[key] for g in stack_games if g[key] is not None])
            se_b = _std_err([g[key] for g in base_games if g[key] is not None])
            if se_s is not None and se_b is not None:
                se = math.hypot(se_s, se_b)
                z = diff / se if se > 0 else 0.0
        metrics.append({
            "key": key,
            "label": label,
            "better": "higher" if sign > 0 else "lower",
            "stack": s,
            "usual": b,
            "diff": diff,
            "pct": (diff / b) if b else None,
            "z": round(z, 2) if z is not None else None,
            "verdict": _verdict(z, sign),
        })
    judged = [m for m in metrics if m["verdict"] not in ("same", "too_few")]
    standout = max(judged, key=lambda m: abs(m["z"]))["key"] if judged else None
    return {
        "stack_games": len(stack_rows),
        "usual_games": len(base_rows),
        "usual_modes": sorted({r.get("mode_label") or r.get("mode") for r in base_rows} - {None}),
        "enough": enough,
        "min_games": MIN_DEVIATION_GAMES,
        "metrics": metrics,
        "standout": standout,
    }


def team_stats(matches):
    g = len(matches)
    wins = sum(1 for m in matches if m.get("result") == "win")
    losses = sum(1 for m in matches if m.get("result") == "loss")
    draws = g - wins - losses
    diffs = [(m.get("rounds_won") or 0) - (m.get("rounds_lost") or 0) for m in matches]
    by_map = defaultdict(list)
    by_mode = defaultdict(list)
    for m in matches:
        by_map[m.get("map") or "Unknown"].append(m)
        by_mode[m.get("mode_label") or m.get("mode") or "Unknown"].append(m)

    def rec(name_key, name, grp):
        w = sum(1 for x in grp if x.get("result") == "win")
        l = sum(1 for x in grp if x.get("result") == "loss")
        d = [(x.get("rounds_won") or 0) - (x.get("rounds_lost") or 0) for x in grp]
        return {
            name_key: name,
            "games": len(grp),
            "wins": w,
            "losses": l,
            "draws": len(grp) - w - l,
            "win_rate": w / len(grp),
            "avg_round_diff": sum(d) / len(d),
            "last_played": grp[0].get("started_at"),
        }

    maps = sorted((rec("map", k, v) for k, v in by_map.items()), key=lambda x: (-x["games"], x["map"]))
    modes = sorted((rec("mode", k, v) for k, v in by_mode.items()), key=lambda x: -x["games"])
    recent = [
        {
            "match_id": m["match_id"],
            "map": m.get("map"),
            "mode_label": m.get("mode_label"),
            "result": m.get("result"),
            "rounds_won": m.get("rounds_won"),
            "rounds_lost": m.get("rounds_lost"),
            "started_at": m.get("started_at"),
        }
        for m in matches[:12]
    ]
    return {
        "games": g,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "win_rate": (wins / g) if g else None,
        "avg_round_diff": (sum(diffs) / g) if g else None,
        "streak": _streak(matches),
        "by_map": maps,
        "by_mode": modes,
        "recent": recent,
        "last_played": matches[0].get("started_at") if matches else None,
        "first_played": matches[-1].get("started_at") if matches else None,
    }


def build_stats(db):
    members = db.members()
    rows = db.player_rows()
    matches = db.matches()
    by_member = defaultdict(list)
    for r in rows:
        by_member[r["puuid"]].append(r)
    baseline = defaultdict(list)
    for r in db.baseline_rows():
        baseline[r["puuid"]].append(r)

    out_members = []
    for m in members:
        mr = by_member.get(m["puuid"], [])
        form = []
        for r in mr[:15]:
            met = player_metrics(r, _rounds(r))
            form.append({
                "match_id": r["match_id"],
                "started_at": r.get("started_at"),
                "map": r.get("map"),
                "agent": r.get("agent"),
                "result": r.get("result"),
                "kills": met["kills"],
                "deaths": met["deaths"],
                "assists": met["assists"],
                "acs": round(met["acs"], 1),
                "adr": round(met["adr"], 1),
                "hs_pct": round(met["hs_pct"], 1) if met["hs_pct"] is not None else None,
            })
        latest_tier = next((r.get("tier_name") for r in mr if r.get("tier_name")), None)
        out_members.append({
            "puuid": m["puuid"],
            "name": m["name"],
            "tag": m["tag"],
            "nickname": m.get("nickname") or m["name"],
            "card": m.get("card"),
            "tier_name": latest_tier,
            "overall": aggregate(mr),
            "by_agent": _group(mr, "agent"),
            "by_map": _group(mr, "map"),
            "form": form,
            "deviation": deviation(mr, baseline.get(m["puuid"], [])),
            "best": {
                "kills": _best(mr, "kills"),
                "acs": _best(mr, "acs"),
                "assists": _best(mr, "assists"),
                "hs_pct": _best(mr, "hs_pct"),
            },
        })

    return {
        "generated_at": time.time(),
        "team": team_stats(matches),
        "members": out_members,
    }
