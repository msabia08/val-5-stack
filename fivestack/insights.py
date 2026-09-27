"""Datasets for the Visualizations tab. Shaped here (so the self-test can check them), drawn by web/viz.js.

Everything is time-zone independent; the day-of-week x time-of-day heatmap is binned in the browser
from each game's timestamp so it uses the viewer's local time.
"""
from collections import Counter, defaultdict

from .stats import aggregate, safe_div

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
        "bankroll": _bankroll(db),
        "constants": {"form_window": FORM_WINDOW, "session_gap_h": SESSION_GAP_S / 3600},
    }


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
