"""Synthetic 5-stack history for `python server.py --demo` (no API key needed)."""
import json
import random
import time
import uuid

MEMBERS = [
    # name, tag, nickname, agent pool (repeats weight a pick), skill multiplier, skill outside the 5-stack
    ("Matt", "5STK", "Matt", ["Jett", "Raze", "Neon", "Reyna"], 1.10, 1.02),
    ("Jordan", "DUEL", "Jordan", ["Omen", "Astra", "Clove", "Omen", "Sova"], 0.95, 0.96),
    ("Sam", "SMOKE", "Sam", ["Sova", "Fade", "Gekko"], 1.00, 1.12),
    ("Alex", "FLASH", "Alex", ["Killjoy", "Cypher", "Vyse", "Killjoy", "Viper"], 0.90, 0.78),
    ("Riley", "INFO", "Riley", ["Phoenix", "Yoru", "Iso", "Jett", "Skye", "Fade"], 1.05, 1.07),
]
MAPS = ["Ascent", "Bind", "Haven", "Lotus", "Sunset", "Abyss", "Corrode", "Split", "Pearl"]
MAP_EDGE = {
    "Ascent": 0.68, "Bind": 0.55, "Haven": 0.60, "Lotus": 0.45, "Sunset": 0.50,
    "Abyss": 0.40, "Corrode": 0.52, "Split": 0.58, "Pearl": 0.47,
}


def _score(rng, win):
    loser = rng.choice([3, 5, 7, 8, 9, 10, 11, 11, 12])
    rw, rl = (13, loser) if win else (loser, 13)
    if loser == 12 and rng.random() < 0.6:  # overtime
        rw, rl = (14, 12) if win else (12, 14)
    return rw, rl


def _line(rng, puuid, agents, skill, win, rounds):
    team_factor = 1.08 if win else 0.92
    kpr = max(0.25, rng.gauss(0.72 * skill * team_factor, 0.16))
    kills = int(round(kpr * rounds))
    deaths = int(round(max(3, rng.gauss(rounds * 0.62 / (skill ** 0.5) * (0.94 if win else 1.06), 2.5))))
    assists = int(round(max(0, rng.gauss(rounds * (0.25 if skill < 1 else 0.18), 2))))
    dmg = int(kills * rng.uniform(135, 165) + assists * 40 + rng.uniform(-150, 250))
    score = int(dmg * 1.25 + kills * 30 + assists * 10)
    shots = max(1, kills * rng.randint(4, 7))
    hs = int(shots * min(0.45, max(0.08, rng.gauss(0.22 * skill, 0.06))))
    legs = int(shots * rng.uniform(0.02, 0.08))
    return {
        "puuid": puuid,
        "agent": rng.choice(agents),
        "score": score,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "headshots": hs,
        "bodyshots": max(0, shots - hs - legs),
        "legshots": legs,
        "damage_dealt": max(0, dmg),
        "damage_received": int(deaths * rng.uniform(120, 150)),
    }


def _stamp(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def seed(db, games=48, seed=7):
    rng = random.Random(seed)
    if not db.count_matches():
        _seed_stack(db, rng, games)
    if not db.count_member_games():
        _seed_other_games(db, random.Random(seed + 1))
    if not db.bets():
        _seed_bets(db, random.Random(seed + 2))
    if not db.rewards():
        _seed_rewards(db)


def _game_times(rng, games):
    """Evening sessions of 1-4 games (UTC evening/late night, longer on weekends), oldest first."""
    times, day = [], time.time() - 42 * 86400
    while len(times) < games:
        day += rng.choice([1, 1, 1, 2, 2, 3]) * 86400
        weekend = time.gmtime(day).tm_wday >= 5
        start = day - day % 86400 + rng.choice([19, 21, 22, 23, 24, 25, 26] if weekend else [23, 24, 24, 25, 26]) * 3600
        count = rng.choice([1, 2, 2, 3, 3, 4] + ([4, 5] if weekend else []))
        for k in range(count):
            times.append(start + k * rng.uniform(38, 52) * 60)
    times = times[:games]
    shift = (time.time() - 86400 - times[-1]) // 86400 * 86400  # whole days, so times of day are kept
    return [t + shift for t in times]


def _seed_stack(db, rng, games):
    for i, (name, tag, nick, *_rest) in enumerate(MEMBERS):
        db.upsert_member(str(uuid.UUID(int=rng.getrandbits(128))), name, tag, "na", nick, None, i)
    members = db.members()
    profile = {m[0]: m for m in MEMBERS}

    prev = None
    in_session = 0
    for t in _game_times(rng, games):
        in_session = in_session + 1 if prev and t - prev < 3 * 3600 else 1
        prev = t
        map_name = rng.choice(MAPS)
        win = rng.random() < MAP_EDGE[map_name] - 0.06 * (in_session - 1)  # the squad fades late at night
        rw, rl = _score(rng, win)
        if abs(rw - rl) >= 8 and rng.random() < 0.2:  # lopsided games sometimes end in a surrender
            ahead, behind = rng.randint(8, 10), min(rw, rl, 3)
            rw, rl = (ahead, behind) if win else (behind, ahead)
        rounds = rw + rl
        mode = "Competitive" if rng.random() < 0.8 else "Unrated"
        match = {
            "match_id": str(uuid.UUID(int=rng.getrandbits(128))),
            "map": map_name,
            "mode": mode.lower(),
            "mode_label": mode,
            "started_at": _stamp(t),
            "started_ts": t,
            "season": "e11a2",
            "region": "na",
            "team": rng.choice(["Red", "Blue"]),
            "rounds_won": rw,
            "rounds_lost": rl,
            "result": "win" if rw > rl else "loss",
            "game_length_ms": int(rounds * rng.uniform(95, 130) * 1000),
            "source": "demo",
            "party_verified": 1,
            "details_fetched": 1,
        }
        players = []
        for m in members:
            _, _, _, agents, skill, _other = profile[m["name"]]
            line = _line(rng, m["puuid"], agents, skill, win, rounds)
            line.update({"tier": 20, "tier_name": rng.choice(["Diamond 1", "Diamond 2", "Platinum 3"])})
            players.append(line)
        db.insert_match(match, players)


def _seed_other_games(db, rng, per_member=60):
    """Each member's solo-queue and small-party games, played at their outside-the-stack skill."""
    profile = {m[0]: m for m in MEMBERS}
    rows = []
    for m in db.members():
        if m["name"] not in profile:
            continue
        _, _, _, agents, _skill, other = profile[m["name"]]
        t = time.time() - per_member * 86400 * 0.8
        for _ in range(per_member):
            t += rng.uniform(0.2, 1.4) * 86400
            win = rng.random() < 0.5
            rw, rl = _score(rng, win)
            mode = "Competitive" if rng.random() < 0.85 else "Unrated"
            match = {
                "match_id": str(uuid.UUID(int=rng.getrandbits(128))),
                "map": rng.choice(MAPS), "mode": mode.lower(), "mode_label": mode,
                "started_at": _stamp(t), "started_ts": t, "rounds_won": rw, "rounds_lost": rl,
                "result": "win" if win else "loss",
            }
            rows.append(_member_game(match, _line(rng, m["puuid"], agents, other, win, rw + rl)))
    db.insert_member_games(rows)


def _member_game(match, line):
    row = {k: match[k] for k in ("match_id", "map", "mode", "mode_label", "started_at", "started_ts",
                                  "rounds_won", "rounds_lost", "result")}
    row.update({k: v for k, v in line.items() if k not in ("tier", "tier_name")})
    return row


def _seed_rewards(db, games=8):
    """Game rewards for the most recent games, as if rewards had been on for them."""
    from .rewards import RewardManager
    rm = RewardManager({}, db)
    rm.since = 0
    for m in db.matches(games):
        rm.pay_for_match(m, db.match_players(m["match_id"]))


def _seed_bets(db, rng):
    """Three bettors with a settled history on the most recent games, so the betting views have data."""
    bettors = [("Matt", 0.06), ("Jordan", -0.06), ("Sam", 0.0)]  # name, edge over the book
    base = time.time() - 60 * 86400
    for i, (name, _edge) in enumerate(bettors):
        if not db.get_bettor(name):
            db.execute("INSERT INTO bettors(name, balance, created_at) VALUES(?,?,?)", (name, 1000, base + i))
    members = db.members()
    stats = [("kills", "kills", 16.5), ("acs", "ACS", 205.5), ("assists", "assists", 5.5), ("deaths", "deaths", 15.5)]
    for m in db.matches(30):
        for name, edge in bettors:
            if rng.random() > 0.6:
                continue
            who = rng.choice(members)
            key, label, line = rng.choice(stats)
            side = rng.choice(["over", "under"])
            odds = round(rng.uniform(1.6, 2.8), 2)
            stake = float(rng.choice([25, 50, 50, 75, 100, 150]))
            won = rng.random() < min(0.9, 0.95 / odds + edge)
            bet_id = db.insert_bet({
                "bettor": name, "market_id": f"ou:{key}:{who['puuid']}", "market_type": "ou",
                "description": f"{who['nickname'] or who['name']} {label} {side} {line}", "selection": side,
                "selection_label": f"{side.capitalize()} {line}", "line": line, "odds_decimal": odds, "stake": stake,
                "placed_ts": m["started_ts"] - 1800, "context": "{}", "status": "won" if won else "lost",
            })
            payout = round(stake * odds, 2) if won else 0.0
            db.update_bet(bet_id, settled_match_id=m["match_id"], settled_ts=m["started_ts"] + 2700, payout=payout)
            db.adjust_balance(name, payout - stake)

    # A couple of parlays, so the slip UI has a multi-leg example out of the box.
    recent = db.matches(1)
    if recent and len(members) >= 2:
        last = recent[0]
        p1, p2 = rng.sample(members, 2)
        won_legs = [
            {"market_id": "team:win", "market_type": "team_win", "description": "5-stack wins",
             "selection": "win", "selection_label": "5-stack wins", "line": None, "odds_decimal": 1.85,
             "meta": {}, "result": "won", "actual": None, "note": None},
            {"market_id": f"ou:kills:{p1['puuid']}", "market_type": "ou",
             "description": f"{p1['nickname'] or p1['name']} kills Over 16.5", "selection": "over",
             "selection_label": "Over 16.5", "line": 16.5, "odds_decimal": 1.9,
             "meta": {"stat": "kills", "puuid": p1["puuid"]}, "result": "won", "actual": 22, "note": None},
        ]
        odds = round(won_legs[0]["odds_decimal"] * won_legs[1]["odds_decimal"], 2)
        stake = 40.0
        bet_id = db.insert_bet({
            "bettor": "Jordan", "market_id": "parlay", "market_type": "parlay",
            "description": " + ".join(l["description"] for l in won_legs), "selection": "parlay",
            "selection_label": "2-leg parlay", "line": None, "odds_decimal": odds, "stake": stake,
            "placed_ts": last["started_ts"] - 1800, "context": json.dumps({"legs": won_legs}), "status": "won",
        })
        payout = round(stake * odds, 2)
        db.update_bet(bet_id, settled_match_id=last["match_id"], settled_ts=last["started_ts"] + 2700, payout=payout)
        db.adjust_balance("Jordan", payout - stake)

        open_legs = [
            {"market_id": "team:win", "market_type": "team_win", "description": "5-stack wins",
             "selection": "win", "selection_label": "5-stack wins", "line": None, "odds_decimal": 1.85, "meta": {}},
            {"market_id": f"ou:acs:{p2['puuid']}", "market_type": "ou",
             "description": f"{p2['nickname'] or p2['name']} ACS Over 205.5", "selection": "over",
             "selection_label": "Over 205.5", "line": 205.5, "odds_decimal": 1.95,
             "meta": {"stat": "acs", "puuid": p2["puuid"]}},
        ]
        odds2 = round(open_legs[0]["odds_decimal"] * open_legs[1]["odds_decimal"], 2)
        stake2 = 25.0
        db.insert_bet({
            "bettor": "Sam", "market_id": "parlay", "market_type": "parlay",
            "description": " + ".join(l["description"] for l in open_legs), "selection": "parlay",
            "selection_label": "2-leg parlay", "line": None, "odds_decimal": odds2, "stake": stake2,
            "placed_ts": time.time() - 60, "context": json.dumps({"legs": open_legs}), "status": "pending",
        })
        db.adjust_balance("Sam", -stake2)
