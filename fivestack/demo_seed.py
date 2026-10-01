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
    if not db.timelines():
        _seed_timelines(db, random.Random(seed + 3))


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
    # A sixth friend on the bench: in the pool, not playing right now, so the Squad tab has someone to swap in.
    casey = str(uuid.UUID(int=rng.getrandbits(128)))
    db.upsert_member(casey, "Casey", "SUBS", "na", "Casey", None, len(MEMBERS))
    db.update_member(casey, active=0)
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


def _seed_bets(db, rng, games=10):
    """All five squad members betting 3-7 times each on each of the last `games` games, settled by the real
    settlement code against what actually happened, plus open bets on the next game."""
    from statistics import mean

    from .bets import BetManager
    from .stats import player_metrics

    members = db.members()
    names = [m.get("nickname") or m["name"] for m in members]
    base = time.time() - 60 * 86400
    for i, name in enumerate(names):
        if not db.get_bettor(name):
            db.execute("INSERT INTO bettors(name, balance, created_at) VALUES(?,?,?)", (name, 1000, base + i))
        if not members[i].get("bettor"):  # in the demo everyone signed up with their own Riot ID
            db.update_member(members[i]["puuid"], bettor=name)

    # Lines near each player's averages, so roughly half the overs win.
    rows = db.player_rows()
    lines = {}
    for m in members:
        mets = [player_metrics(r, (r["rounds_won"] or 0) + (r["rounds_lost"] or 0)) for r in rows if r["puuid"] == m["puuid"]]
        lines[m["puuid"]] = {k: mean(x[k] for x in mets if x.get(k) is not None) for k in ("kills", "deaths", "assists", "acs", "adr", "hs_pct")}
    props = [("kills", "kills"), ("deaths", "deaths"), ("assists", "assists"), ("acs", "ACS"), ("adr", "ADR"), ("hs_pct", "Headshot %")]
    tops = [("kills", "Top fragger", "Bottom fragger"), ("acs", "Highest ACS", "Lowest ACS"),
            ("assists", "Most assists", "Fewest assists"), ("acs_rel", "Popped off", "Got diff'd")]

    def leg():
        roll = rng.random()
        if roll < 0.62:  # player prop
            m = rng.choice(members)
            key, label = rng.choice(props)
            avg = lines[m["puuid"]][key]
            line = (round(avg) if key in ("acs", "adr") else int(avg)) + 0.5
            side = rng.choice(["over", "under"])
            return {"market_id": f"ou:{key}:{m['puuid']}", "market_type": "ou",
                    "description": f"{m.get('nickname') or m['name']} {label} {side.capitalize()} {line:g}",
                    "selection": side, "selection_label": f"{side.capitalize()} {line:g}", "line": line,
                    "odds_decimal": round(rng.uniform(1.75, 2.05), 2), "meta": {"stat": key, "puuid": m["puuid"]}}
        if roll < 0.85:  # top or bottom of the scoreboard
            m = rng.choice(members)
            key, top_label, low_label = rng.choice(tops)
            low = rng.random() < 0.4
            return {"market_id": f"{'low' if low else 'top'}:{key}", "market_type": "top",
                    "description": f"{low_label if low else top_label}: {m.get('nickname') or m['name']}",
                    "selection": m["puuid"], "selection_label": m.get("nickname") or m["name"], "line": None,
                    "odds_decimal": round(rng.uniform(3.0, 6.5), 2), "meta": {"stat": key, "direction": "low" if low else "high"}}
        if roll < 0.95:
            win = rng.random() < 0.6
            return {"market_id": "team:win", "market_type": "team_win", "description": f"Match result: {'Win' if win else 'Loss'}",
                    "selection": "win" if win else "loss", "selection_label": "Win" if win else "Loss",
                    "line": None, "odds_decimal": round(rng.uniform(1.7, 2.3), 2), "meta": {}}
        side = rng.choice(["over", "under"])
        return {"market_id": "team:rounds", "market_type": "team_ou", "description": f"Total rounds {side.capitalize()} 22.5",
                "selection": side, "selection_label": f"{side.capitalize()} 22.5", "line": 22.5,
                "odds_decimal": round(rng.uniform(1.8, 2.0), 2), "meta": {}}

    def place(name, placed_ts):
        """3-7 picks for one bettor; now and then a few of them go in as a parlay instead of singles."""
        n_bets, parlay = rng.randint(3, 7), rng.random() < 0.3
        n_legs = rng.randint(2, 3) if parlay else 0
        picks, seen = [], set()
        while len(picks) < n_bets - (1 if parlay else 0) + n_legs:  # every market at most once per bettor
            lg = leg()
            if lg["market_id"] not in seen:
                seen.add(lg["market_id"])
                picks.append(lg)
        bets = []
        if parlay:
            legs, picks = picks[:n_legs], picks[n_legs:]
            odds = 1.0
            for lg in legs:
                odds *= lg["odds_decimal"]
            bets.append({"market_id": "parlay", "market_type": "parlay", "description": " + ".join(lg["description"] for lg in legs),
                         "selection": "parlay", "selection_label": f"{len(legs)}-leg parlay", "line": None,
                         "odds_decimal": round(odds, 2), "context": json.dumps({"legs": legs})})
        for lg in picks:
            bets.append({k: v for k, v in lg.items() if k != "meta"} | {"context": json.dumps(lg["meta"])})
        for b in bets:
            stake = float(rng.choice([10, 15, 20, 25, 25, 40, 50]))
            if stake > db.get_bettor(name)["balance"]:
                continue
            db.insert_bet({**b, "bettor": name, "stake": stake, "placed_ts": placed_ts - rng.uniform(60, 1800),
                           "status": "pending"})
            db.adjust_balance(name, -stake)

    manager = BetManager({}, db, None)
    for m in reversed(db.matches(games)):  # oldest first, each game settled before the next is bet on
        for name in names:
            place(name, m["started_ts"])
        manager.settle_for_match(m, db.match_players(m["match_id"]))
        db.execute("UPDATE bets SET settled_ts=? WHERE settled_match_id=?", (m["started_ts"] + 2700, m["match_id"]))  # as if settled after the game
    for name in names:  # open bets on the next game
        place(name, time.time())


# What each demo bettor buys with their bananas, in order, while they can afford it (see seed_shop).
DEMO_SHOPPING = {
    "Matt": ["bd-crown", "nc-sunset", "tt-top", "tk-gold"],
    "Jordan": ["bd-monkey", "tt-cheeky", "nc-jungle", "bn-canopy"],
    "Sam": ["bd-gorilla", "nc-onkey", "tt-silverback", "bn-onkey"],
    "Alex": ["bd-coconut", "tt-eco", "tk-leaf"],
    "Riley": ["bd-banana", "nc-peel", "tt-parlay", "cb-bananas", "tk-peel"],
}


def seed_house(db, house):
    """The house's giveaways on the latest game (its secret objectives revealed and paid, its bad beats refunded), then
    the next game's set, so the Standings card and the recap have something to show."""
    if db.query_one("SELECT 1 AS x FROM house_objectives"):
        return
    latest = db.matches(1)
    if not latest:
        return
    match = latest[0]
    house.ensure_objectives(for_match=match)
    house.refunds(match, [b for b in db.bets() if b.get("settled_match_id") == match["match_id"]])
    house.settle_objectives(match, db.match_players(match["match_id"]))
    house.ensure_objectives()


def seed_shop(db, bananas):
    """A few shop purchases and one of each prank, paid for with the demo's earned bananas plus a starter grant."""
    if db.banana_items():
        return
    for name, wishlist in DEMO_SHOPPING.items():
        if not db.get_bettor(name):
            continue
        # Demo only: a starter grant so the shop has something to show. It isn't "earned" (reason 'demo').
        db.execute("INSERT OR IGNORE INTO banana_ledger(bettor, delta, reason, ref, note, created_ts) VALUES(?,?,?,?,?,?)",
                   (name, 1500, "demo", f"grant:{name.lower()}", "Demo starter bananas", time.time() - 7 * 86400))
        for item in wishlist:
            try:
                bananas.buy(name, item)
            except Exception:  # noqa: BLE001  (can't afford it: stop shopping)
                break
    for buyer, item, target, text in [("Matt", "sc-note", "Jordan", "gg on the 4k last night, still owe me a Vandal"),
                                      ("Riley", "sc-peel", "Matt", None), ("Sam", "sc-jinx", "Riley", None),
                                      ("Jordan", "sc-title", "Alex", "Professional Baiter"),
                                      ("Alex", "sc-heckle", "Matt", "bet the under on yourself, coward"), ("Alex", "sc-bounty", "Sam", None),
                                      ("Jordan", "sc-clown", "Sam", None),
                                      ("Matt", "sc-nick", "Riley", "Bot Frag")]:
        try:
            bananas.buy(buyer, item, target, text)
        except Exception:  # noqa: BLE001
            pass


THREE_SITE_MAPS = {"Haven", "Lotus"}


def _seed_timelines(db, rng):
    """A plausible round-by-round record for every demo game, matching its final score (see timeline.py)."""
    profile = {m[0]: m for m in MEMBERS}
    for match in db.matches():
        players = db.match_players(match["match_id"])
        us = [p["puuid"] for p in players]
        skill = {p["puuid"]: profile[p["name"]][4] if p.get("name") in profile else 1.0 for p in players}
        them = [f"enemy-{match['match_id'][:6]}-{i}" for i in range(5)]
        team_of = {p: "Blue" for p in us} | {p: "Red" for p in them}
        rw, rl = match["rounds_won"] or 0, match["rounds_lost"] or 0
        results = ["Blue"] * rw + ["Red"] * rl
        last = results.pop(results.index("Blue" if rw > rl else "Red"))  # the deciding round goes to the winner
        rng.shuffle(results)
        results.append(last)
        sites = ["A", "B", "C"] if match["map"] in THREE_SITE_MAPS else ["A", "B"]
        blue_attacks_first = rng.random() < 0.5
        rounds, kills = [], []
        for i, winner in enumerate(results):
            first_half = i < 12 or i >= 24
            attackers = "Blue" if blue_attacks_first == first_half else "Red"
            alive = {"Blue": list(us), "Red": list(them)}
            loser = "Red" if winner == "Blue" else "Blue"
            by_spike = rng.random() < 0.35  # otherwise the round ends in an elimination
            stop_at = rng.randint(1, 3) if by_spike else 0  # a spike round ends with some of the losers alive
            t = 0
            while len(alive[loser]) > stop_at:
                t += rng.randint(2000, 12000)
                team = winner if rng.random() < 0.6 or len(alive[winner]) == 1 else loser  # the winners keep someone alive
                other = loser if team == winner else winner
                killer = rng.choices(alive[team], [skill.get(p, 1.0) for p in alive[team]])[0]
                victim = rng.choice(alive[other])
                alive[other].remove(victim)
                kills.append({"r": i, "t": t, "killer": killer, "victim": victim, "weapon": rng.choice(["Vandal", "Phantom", "Operator", "Sheriff"])})
            planted = attackers == winner and by_spike or rng.random() < 0.45
            site = rng.choice(sites) if planted else None
            rounds.append({"winner": winner, "site": site, "planter_team": attackers if planted else None,
                           "defused": bool(planted and winner != attackers)})
        db.save_timeline(match["match_id"], {"our_team": "Blue", "team_of": team_of, "rounds": rounds, "kills": kills})
