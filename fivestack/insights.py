"""Datasets for the Visualizations tab. Shaped here (so the self-test can check them), drawn by web/viz.js.

Everything is time-zone independent; the day-of-week x time-of-day heatmap is binned in the browser
from each game's timestamp so it uses the viewer's local time.
"""
import json
import math
from collections import Counter, defaultdict

from .odds import fair_chance
from .stats import aggregate, safe_div
from .timeline import clutches_and_multikills, spike_sites

ROLES = {
    "Duelist": ["Iso", "Jett", "Neon", "Phoenix", "Raze", "Reyna", "Waylay", "Yoru"],
    "Controller": ["Astra", "Brimstone", "Clove", "Harbor", "Miks", "Omen", "Viper"],
    "Initiator": ["Breach", "Fade", "Gekko", "KAY/O", "Skye", "Sova", "Tejo"],
    "Sentinel": ["Chamber", "Cypher", "Deadlock", "Killjoy", "Sage", "Veto", "Vyse"],
}
AGENT_ROLE = {agent.lower(): role for role, agents in ROLES.items() for agent in agents}

SESSION_GAP_S = 3 * 3600  # a longer break between game starts begins a new session ("night")
FORM_WINDOW = 10  # rolling win-rate window, in games
FORM_MIN_GAMES = 5  # no rolling value until this many games exist
CLOSE_MARGIN = 2  # 13-11 or overtime
BLOWOUT_MARGIN = 6  # 13-7 or wider
SESSION_BUCKETS = 5  # games 1..4 of a night, then "5+"


def _role_shape(agents):
    counts = Counter(AGENT_ROLE.get((a or "").lower(), "Unknown") for a in agents)
    order = list(ROLES) + ["Unknown"]
    parts = [(r, counts[r]) for r in order if counts[r]]
    key = " ".join(f"{n}{r[0]}" for r, n in parts)
    label = " · ".join(f"{n} {r}{'s' if n > 1 else ''}" for r, n in parts)
    return key, label


def _record(games):
    wins = sum(1 for g in games if g["result"] == "win")
    losses = sum(1 for g in games if g["result"] == "loss")
    return {"games": len(games), "wins": wins, "losses": losses, "win_rate": safe_div(wins, len(games)) if games else None}


def build_insights(db):
    """All Visualizations-tab datasets."""
    members = db.members()
    order = [m["puuid"] for m in members]
    matches = sorted(db.matches(), key=lambda m: m.get("started_ts") or 0)
    lines = defaultdict(dict)
    for p in db.all_match_players():
        lines[p["match_id"]][p["puuid"]] = p

    games, session, prev_ts, in_session = [], 0, None, 0
    for i, m in enumerate(matches):
        ts = m.get("started_ts") or 0
        if prev_ts is None or ts - prev_ts > SESSION_GAP_S:
            session += 1
            in_session = 0
        in_session += 1
        prev_ts = ts
        rw, rl = m.get("rounds_won") or 0, m.get("rounds_lost") or 0
        ps = [lines[m["match_id"]][pu] for pu in order if pu in lines[m["match_id"]]]
        total_dmg = sum(p.get("damage_dealt") or 0 for p in ps)
        shape_key, shape_label = _role_shape(p.get("agent") for p in ps)
        window = matches[max(0, i + 1 - FORM_WINDOW): i + 1]
        games.append({
            "match_id": m["match_id"],
            "ts": ts,
            "started_at": m.get("started_at"),
            "map": m.get("map"),
            "mode_label": m.get("mode_label"),
            "result": m.get("result"),
            "rounds_won": rw,
            "rounds_lost": rl,
            "margin": rw - rl,
            "session": session,
            "game_in_session": in_session,
            "form": (sum(1 for w in window if w.get("result") == "win") / len(window)) if i + 1 >= FORM_MIN_GAMES else None,
            "damage_share": {p["puuid"]: safe_div(p.get("damage_dealt") or 0, total_dmg) for p in ps} if total_dmg else {},
            "comp": shape_key,
            "comp_label": shape_label,
        })

    # ---- moments: close games, blowouts, momentum within a session, session fatigue ----
    after_win = [g for prev, g in zip(games, games[1:]) if g["session"] == prev["session"] and prev["result"] == "win"]
    after_loss = [g for prev, g in zip(games, games[1:]) if g["session"] == prev["session"] and prev["result"] == "loss"]
    by_slot = defaultdict(list)
    for g in games:
        by_slot[min(g["game_in_session"], SESSION_BUCKETS)].append(g)
    session_games = [
        {"slot": n, "label": f"{n}+" if n == SESSION_BUCKETS else str(n), **_record(by_slot[n])}
        for n in range(1, SESSION_BUCKETS + 1)
    ]
    moments = {
        "close": _record([g for g in games if abs(g["margin"]) <= CLOSE_MARGIN]),
        "blowout": _record([g for g in games if abs(g["margin"]) >= BLOWOUT_MARGIN]),
        "after_win": _record(after_win),
        "after_loss": _record(after_loss),
        "first_of_session": _record(by_slot[1]),
        "later_in_session": _record([g for g in games if g["game_in_session"] > 1]),
        "sessions": session,
        "close_margin": CLOSE_MARGIN,
        "blowout_margin": BLOWOUT_MARGIN,
    }

    # ---- per player: map x player ACS vs own average, ACS in wins vs losses, aim split ----
    rows_by_player = defaultdict(list)
    for r in db.player_rows():
        rows_by_player[r["puuid"]].append(r)
    map_games = Counter(m.get("map") or "Unknown" for m in matches)
    maps = [name for name, _ in map_games.most_common()]
    players = []
    for m in members:
        rows = rows_by_player.get(m["puuid"], [])
        overall = aggregate(rows)
        per_map = {}
        for name in maps:
            mr = [r for r in rows if (r.get("map") or "Unknown") == name]
            if mr:
                acs = aggregate(mr)["acs"]
                per_map[name] = {"games": len(mr), "acs": acs,
                                 "vs_avg": (acs / overall["acs"] - 1) if overall.get("acs") else None}
        wins = aggregate([r for r in rows if r.get("result") == "win"])
        losses = aggregate([r for r in rows if r.get("result") == "loss"])
        head = sum(r.get("headshots") or 0 for r in rows)
        body = sum(r.get("bodyshots") or 0 for r in rows)
        leg = sum(r.get("legshots") or 0 for r in rows)
        shots = head + body + leg
        players.append({
            "puuid": m["puuid"],
            "nickname": m.get("nickname") or m["name"],
            "games": overall["games"],
            "acs": overall.get("acs"),
            "maps": per_map,
            "acs_win": wins.get("acs"), "games_win": wins["games"],
            "acs_loss": losses.get("acs"), "games_loss": losses["games"],
            "aim": {"head": head, "body": body, "leg": leg,
                    "head_pct": safe_div(head, shots), "body_pct": safe_div(body, shots), "leg_pct": safe_div(leg, shots)},
            "agents": _agent_pool(rows, overall.get("acs")),
        })

    comps = defaultdict(list)
    labels = {}
    for g in games:
        comps[g["comp"]].append(g)
        labels[g["comp"]] = g["comp_label"]
    comp_rows = sorted(({"key": k, "label": labels[k], **_record(v)} for k, v in comps.items()),
                       key=lambda c: (-c["games"], c["key"]))

    return {
        "members": [{"puuid": m["puuid"], "nickname": m.get("nickname") or m["name"]} for m in members],
        "games": games,
        "moments": moments,
        "session_games": session_games,
        "maps": [{"map": name, "games": map_games[name]} for name in maps],
        "players": players,
        "comps": comp_rows,
        "rounds": _round_insights(db, members, len(matches)),
        "roles": {role: agents for role, agents in ROLES.items()},
        "constants": {"form_window": FORM_WINDOW, "session_gap_h": SESSION_GAP_S / 3600},
    }


def _agent_pool(rows, overall_acs):
    """One member's record on each agent they've played in the stack, most-played first."""
    by_agent = defaultdict(list)
    for r in rows:
        if r.get("agent"):
            by_agent[r["agent"]].append(r)
    out = []
    for agent, ar in by_agent.items():
        agg = aggregate(ar)
        out.append({"agent": agent, "role": AGENT_ROLE.get(agent.lower(), "Unknown"), "games": agg["games"],
                    "wins": agg["wins"], "win_rate": agg["win_rate"], "acs": agg.get("acs"),
                    "vs_avg": (agg["acs"] / overall_acs - 1) if overall_acs and agg.get("acs") else None})
    return sorted(out, key=lambda a: (-a["games"], a["agent"]))


# Betting report card: market types grouped the way bettors think about them.
BET_CATEGORIES = [("ou", "Player props"), ("top", "Scoreboard"), ("team_win", "Match result"),
                  ("team_ou", "Total rounds"), ("team_rw", "Rounds won"), ("team_rl", "Rounds lost"),
                  ("team_margin", "Winning margin"), ("team_score", "Exact score"), ("team_ot", "Overtime"),
                  ("parlay", "Parlays")]


def _bet_record(bets):
    won = sum(1 for b in bets if b["status"] == "won")
    lost = sum(1 for b in bets if b["status"] == "lost")
    staked = sum(b["stake"] for b in bets)
    returned = sum(b.get("payout") or 0 for b in bets)
    return {"bets": len(bets), "won": won, "lost": lost, "staked": staked, "returned": returned,
            "net": returned - staked, "roi": (returned - staked) / staked if staked else None,
            "hit_rate": won / (won + lost) if won + lost else None}


def betting_report(db, bettor_of=None, edge=0.05):
    """The Bettors page's report card: settled bets per bettor by market type, and betting on yourself vs others.
    bettor_of maps a member's puuid to their bettor account (defaults to the nickname). Also carries the odds
    accuracy card's data (see odds_accuracy; edge is the configured house edge)."""
    members, bettor_of = db.members(), bettor_of or {}
    settled = [b for b in db.bets() if b["status"] in ("won", "lost", "void")]
    account = {(bettor_of.get(m["puuid"]) or m.get("nickname") or m["name"]).lower(): m["puuid"] for m in members}
    order = [(bettor_of.get(m["puuid"]) or m.get("nickname") or m["name"]) for m in members]
    names = sorted({b["bettor"] for b in settled},
                   key=lambda n: (order.index(n) if n in order else len(order), n.lower()))
    labels = dict(BET_CATEGORIES)
    by_type = {}
    for name in names:
        mine = [b for b in settled if b["bettor"] == name]
        by_type[name] = {cat: _bet_record([b for b in mine if b["market_type"] == cat])
                         for cat, _ in BET_CATEGORIES if any(b["market_type"] == cat for b in mine)}

    def on_self(b, puuid):
        meta = json.loads(b.get("context") or "{}")
        if b["market_type"] == "ou":
            return meta.get("puuid") == puuid
        if b["market_type"] == "top":
            return b["selection"] == puuid
        return None  # team markets and parlays are about everyone
    self_bets = []
    for name in names:
        puuid = account.get(name.lower())
        if not puuid:
            continue
        mine = [b for b in settled if b["bettor"] == name]
        own = [b for b in mine if on_self(b, puuid) is True]
        others = [b for b in mine if on_self(b, puuid) is False]
        self_bets.append({"bettor": name, "puuid": puuid, "own": _bet_record(own), "others": _bet_record(others)})

    return {"bettors": names, "categories": [{"key": k, "label": labels[k]} for k, _ in BET_CATEGORIES],
            "by_type": by_type, "self": self_bets, "settled": len(settled), "bankroll": _bankroll(db),
            "accuracy": odds_accuracy(db, edge)}


ACCURACY_BINS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]  # by the odds' own chance
ACCURACY_MIN_PICKS = 10  # fewer settled picks than this (overall, or in a market type) gets no verdict
ACCURACY_Z = 2.0  # won this many standard deviations away from the odds' expectation -> not in line
INTERVAL_Z = 1.645  # 90% range around each bin's win rate


def _wilson(won, n, z=INTERVAL_Z):
    """Range for a win rate from few picks (Wilson score interval): wide when n is small."""
    if not n:
        return None, None
    p = won / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def _verdict(picks):
    """"about_right", "generous" (won more often than the odds said, so they paid too much) or "stingy", from how
    many standard deviations the win count is from the odds' expectation; None with too few picks."""
    if len(picks) < ACCURACY_MIN_PICKS:
        return None
    expected = sum(p for p, _ in picks)
    spread = math.sqrt(sum(p * (1 - p) for p, _ in picks)) or 1.0
    z = (sum(won for _, won in picks) - expected) / spread
    return "about_right" if abs(z) < ACCURACY_Z else "generous" if z > 0 else "stingy"


def odds_accuracy(db, edge):
    """How well the odds predicted the picks people bet on: the model's own chance (before the house edge) for each
    settled pick against whether it won. Singles and parlay legs both count; voids are left out. Several bets on the
    same pick in the same game count once, so a popular pick doesn't outweigh the rest."""
    picks = {}  # (game, market, selection, line) -> {"type", "chances", "won"}

    def add(game, item, meta, result):
        if result not in ("won", "lost"):
            return
        chance = item.get("fair_prob", meta.get("fair_prob"))
        if chance is None:  # placed before bets saved it: take the house edge back out of the price
            chance = fair_chance(item["odds_decimal"], item["market_type"], edge)
        key = (game, item["market_id"], item["selection"], item.get("line"))
        pick = picks.setdefault(key, {"type": item["market_type"], "chances": [], "won": result == "won"})
        pick["chances"].append(chance)

    for b in db.bets():
        if b["status"] not in ("won", "lost", "void"):
            continue
        ctx = json.loads(b.get("context") or "{}")
        if b["market_type"] == "parlay":
            for leg in ctx.get("legs") or []:
                add(b["settled_match_id"], leg, leg.get("meta") or {}, leg.get("result"))
        else:
            add(b["settled_match_id"], b, ctx, b["status"])

    rows = [(p["type"], sum(p["chances"]) / len(p["chances"]), p["won"]) for p in picks.values()]

    def summary(sel):
        return {"picks": len(sel), "expected": sum(c for _, c, _ in sel), "won": sum(w for _, _, w in sel),
                "verdict": _verdict([(c, w) for _, c, w in sel])}

    bins = []
    for lo, hi in ACCURACY_BINS:
        sel = [r for r in rows if lo <= r[1] < hi or (hi == 1.0 and r[1] == 1.0)]
        won = sum(w for _, _, w in sel)
        low, high = _wilson(won, len(sel))
        bins.append({"lo": lo, "hi": hi, "picks": len(sel), "won": won,
                     "chance": sum(c for _, c, _ in sel) / len(sel) if sel else None,
                     "win_rate": won / len(sel) if sel else None, "range": [low, high] if sel else None})
    labels = dict(BET_CATEGORIES)
    by_type = [{"key": k, "label": labels[k], **summary([r for r in rows if r[0] == k])}
               for k, _ in BET_CATEGORIES if k != "parlay" and any(r[0] == k for r in rows)]
    return {**summary(rows), "bins": bins, "by_type": by_type, "min_picks": ACCURACY_MIN_PICKS}


def _round_insights(db, members, total_games):
    """Clutches, multi-kills and spike sites from the stored round timelines (see timeline.py)."""
    rows = db.timelines()
    puuids = [m["puuid"] for m in members]
    per = clutches_and_multikills([r["data"] for r in rows], puuids)
    by_map = defaultdict(list)
    for r in rows:
        by_map[r["map"] or "Unknown"].append(r["data"])
    players = [{"puuid": m["puuid"], "nickname": m.get("nickname") or m["name"],
                "clutch": [{"vs": n, "attempts": per[m["puuid"]]["clutch"][n][0], "wins": per[m["puuid"]]["clutch"][n][1]}
                           for n in range(1, 6)],
                "k3": per[m["puuid"]]["k3"], "k4": per[m["puuid"]]["k4"], "k5": per[m["puuid"]]["k5"],
                "rounds": per[m["puuid"]]["rounds"]} for m in members]
    spikes = spike_sites(by_map)
    return {"games": len(rows), "total_games": total_games, "players": players,
            "spikes": [{"map": k, **v} for k, v in sorted(spikes.items(), key=lambda kv: -kv[1]["games"])]}


def _bankroll(db):
    """Cumulative profit per bettor, one point per settled bet, bettors in sign-up order (stable colors)."""
    bettors = sorted(db.bettors(), key=lambda b: (b.get("created_at") or 0, b["name"].lower()))
    settled = sorted((b for b in db.bets() if b["status"] in ("won", "lost", "void")),
                     key=lambda b: b.get("settled_ts") or 0)
    series = {b["name"]: [] for b in bettors}
    running = defaultdict(float)
    for b in settled:
        if b["bettor"] not in series:
            continue
        running[b["bettor"]] += (b.get("payout") or 0) - b["stake"]
        series[b["bettor"]].append({"ts": b.get("settled_ts"), "profit": round(running[b["bettor"]], 2),
                                    "status": b["status"], "description": b.get("description")})
    return [{"name": name, "points": pts} for name, pts in series.items() if pts]
