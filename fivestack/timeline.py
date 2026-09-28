"""Round-by-round data for 5-stack games: clutches, multi-kills and spike plants.

extract_timeline() keeps a compact copy of a HenrikDev v4 match record: which team each player was on, every
round's winner and spike plant, and every kill in order. The full record is large (it carries every player's
position at every kill), so only this is stored, in the match_timelines table. The analysis functions work on
these compact timelines, so they are cheap and easy to test.

The v4 layout isn't under our control, so parsing is defensive: a missing field is skipped rather than raising.
"""
from collections import Counter, defaultdict

HALF = 12  # rounds per half in the 13-round modes; sides swap after round 12, overtime is left out of side stats


def _team(obj):
    obj = obj or {}
    return obj.get("team") or obj.get("team_id")


def extract_timeline(data, member_puuids):
    """Compact timeline for the squad's game, or None when the record has no round data or none of us are in it."""
    players = data.get("players") or []
    team_of = {p["puuid"]: p.get("team_id") or p.get("team") for p in players if p.get("puuid")}
    ours = [pu for pu in member_puuids if pu in team_of]
    rounds_raw = data.get("rounds") or []
    if not ours or not rounds_raw:
        return None
    rounds = []
    for r in rounds_raw:
        plant = r.get("plant") or {}
        rounds.append({
            "winner": r.get("winning_team"),
            "site": plant.get("site"),
            "planter_team": _team(plant.get("player")),
            "defused": bool(r.get("defuse")),
        })
    kills = []
    for k in data.get("kills") or []:
        killer, victim = (k.get("killer") or {}).get("puuid"), (k.get("victim") or {}).get("puuid")
        if victim is None or k.get("round") is None:
            continue
        kills.append({"r": int(k["round"]), "t": k.get("time_in_round_in_ms") or 0, "killer": killer, "victim": victim,
                      "weapon": (k.get("weapon") or {}).get("name")})
    kills.sort(key=lambda k: (k["r"], k["t"]))
    return {"our_team": team_of[ours[0]], "team_of": team_of, "rounds": rounds, "kills": kills}


def clutches_and_multikills(timelines, member_puuids):
    """Per member: 1vX clutch attempts and wins (by X), and rounds with 3, 4 and 5 kills.

    A clutch starts the moment a member is the last of us alive with enemies still standing; it's won if the
    squad takes the round (by elimination, detonation or defuse). Only kills on enemies count toward multi-kills.
    """
    per = {pu: {"clutch": {n: [0, 0] for n in range(1, 6)}, "k3": 0, "k4": 0, "k5": 0, "rounds": 0} for pu in member_puuids}
    for tl in timelines:
        team_of, our = tl["team_of"], tl["our_team"]
        us = {p for p, t in team_of.items() if t == our}
        them = {p for p, t in team_of.items() if t != our}
        by_round = defaultdict(list)
        for k in tl["kills"]:
            by_round[k["r"]].append(k)
        for i, rnd in enumerate(tl["rounds"]):
            alive_us, alive_them = set(us), set(them)
            clutch = None
            kills = Counter()
            for k in by_round.get(i, []):
                alive_us.discard(k["victim"])
                alive_them.discard(k["victim"])
                if k["killer"] in us and k["victim"] in them:
                    kills[k["killer"]] += 1
                if clutch is None and len(alive_us) == 1 and alive_them:
                    clutch = (next(iter(alive_us)), min(len(alive_them), 5))
            for pu in us & per.keys():
                per[pu]["rounds"] += 1
            if clutch and clutch[0] in per:
                rec = per[clutch[0]]["clutch"][clutch[1]]
                rec[0] += 1
                rec[1] += 1 if rnd["winner"] == our else 0
            for pu, n in kills.items():
                if pu in per and n >= 3:
                    per[pu]["k5" if n >= 5 else "k4" if n == 4 else "k3"] += 1
    return per


def _first_half_attackers(tl):
    """The team attacking in rounds 1-12, read off the spike plants (only attackers plant)."""
    votes = Counter()
    for i, rnd in enumerate(tl["rounds"][:2 * HALF]):
        if rnd["planter_team"]:
            first_half = i < HALF
            teams = set(tl["team_of"].values())
            other = next((t for t in teams if t != rnd["planter_team"]), None)
            votes[rnd["planter_team"] if first_half else other] += 1
    return votes.most_common(1)[0][0] if votes else None


def spike_sites(timelines_by_map):
    """Per map and site: our plants and post-plant wins on attack, enemy plants and our wins on defence.

    Also the share of our attack rounds (regulation only) in which we got the spike down.
    """
    out = {}
    for map_name, tls in timelines_by_map.items():
        sites = defaultdict(lambda: {"att_plants": 0, "att_wins": 0, "def_plants": 0, "def_wins": 0})
        attack_rounds = attack_plants = 0
        for tl in tls:
            our = tl["our_team"]
            first_att = _first_half_attackers(tl)
            for i, rnd in enumerate(tl["rounds"]):
                won = rnd["winner"] == our
                if first_att and i < 2 * HALF:
                    attacking = (first_att == our) == (i < HALF)
                    if attacking:
                        attack_rounds += 1
                        attack_plants += 1 if rnd["planter_team"] == our else 0
                if not rnd["site"]:
                    continue
                s = sites[rnd["site"]]
                if rnd["planter_team"] == our:
                    s["att_plants"] += 1
                    s["att_wins"] += 1 if won else 0
                else:
                    s["def_plants"] += 1
                    s["def_wins"] += 1 if won else 0
        out[map_name] = {"sites": dict(sorted(sites.items())), "attack_rounds": attack_rounds, "attack_plants": attack_plants,
                         "games": len(tls)}
    return out
