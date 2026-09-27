"""Synthetic 5-stack history for `python server.py --demo` (no API key needed)."""
import random
import time
import uuid

MEMBERS = [
    # name, tag, nickname, agent pool, skill multiplier, skill multiplier outside the 5-stack
    ("Matt", "5STK", "Matt", ["Jett", "Raze", "Neon", "Reyna"], 1.10, 1.02),
    ("Jordan", "DUEL", "Jordan", ["Omen", "Astra", "Clove"], 0.95, 0.96),
    ("Sam", "SMOKE", "Sam", ["Sova", "Fade", "Gekko"], 1.00, 1.12),
    ("Alex", "FLASH", "Alex", ["Killjoy", "Cypher", "Vyse"], 0.90, 0.78),
    ("Riley", "INFO", "Riley", ["Phoenix", "Yoru", "Iso", "Jett"], 1.05, 1.07),
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
    for n in ("Matt", "Jordan"):
        if not db.get_bettor(n):
            db.create_bettor(n, 1000)


def _seed_stack(db, rng, games):
    for i, (name, tag, nick, *_rest) in enumerate(MEMBERS):
        db.upsert_member(str(uuid.UUID(int=rng.getrandbits(128))), name, tag, "na", nick, None, i)
    members = db.members()
    profile = {m[0]: m for m in MEMBERS}

    t = time.time() - games * 86400 * 0.9
    for _ in range(games):
        t += rng.uniform(0.3, 1.6) * 86400
        map_name = rng.choice(MAPS)
        win = rng.random() < MAP_EDGE[map_name]
        rw, rl = _score(rng, win)
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
