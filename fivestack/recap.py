"""Match recap for the Matches tab: one game's header, scoreboard, round by round, betting and rewards, plus the
highlights worth talking about (records, firsts, streaks, clutches, comebacks, odd stat lines, forecast and betting
surprises).

Every highlight is {score, kind, title, detail, puuid, tone}. score ranks them (roughly: 90+ is once-in-a-season,
50-70 is worth a mention, under 40 is trivia for the "more" list); tone is "good", "bad", "odd" or "info" for the
page's colouring. History always means the squad's 5-stack games *before* this one, so re-opening an old game shows
what was noteworthy at the time. Claims like "new high" need MIN_RECORD_GAMES earlier games, so a player's third
game can't set a meaningless record.
"""
import json
from collections import Counter, defaultdict

from .forecasts import MIN_PRIOR, forecast_one
from .gamestate import COMPLETE, FORFEIT, ending, full_game_rounds, total_rounds, went_to_overtime
from .insights import AGENT_ROLE, SESSION_GAP_S
from .odds import decimal_to_american, fair_chance
from .stats import player_metrics
from .timeline import HALF, first_half_attackers

MIN_RECORD_GAMES = 10  # earlier games a player (or the squad) needs before "new high" / "first ..." claims
MIN_SPLIT_GAMES = 5  # earlier games on an agent or map before "best game on Jett / Bind"
SINCE_GAMES = 15  # "best ACS in 23 games" once the run reaches this many
TOP_CARDS = 6  # the page shows this many highlights as cards, the rest in a list
KILL_MILESTONE, GAME_MILESTONE, SQUAD_MILESTONE = 500, 50, 25
KNIVES = {"melee", "knife"}
LOW_RECORDS = {"kills", "acs", "deaths"}  # stats whose worst-ever is worth calling out (fewest kills, most deaths)
FORECAST_FAR = 0.2  # a forecast surprise must land this far outside the range (as a share of its width)
RECORD_STATS = [  # key, label, direction (+1: higher is better), counting stat (partial in a surrender)
    ("kills", "kills", 1, True), ("deaths", "deaths", -1, True), ("assists", "assists", 1, True),
    ("acs", "ACS", 1, False), ("adr", "ADR", 1, False), ("hs_pct", "headshot %", 1, False), ("kd", "K/D", 1, False),
]
USUAL_KEYS = ("kills", "deaths", "assists", "acs", "adr", "hs_pct", "kd")
FORECAST_KEYS = ("kills", "deaths", "acs")


def _hl(score, kind, title, detail="", puuid=None, tone="good"):
    return {"score": round(score, 1), "kind": kind, "title": title, "detail": detail, "puuid": puuid, "tone": tone}


def _shown(key, v):
    """v at the precision the page shows it, so a "record" is never a tie on screen."""
    return None if v is None else round(v, 2 if key == "kd" else 0)


def _fmt(key, v):
    if v is None:
        return "–"
    if key == "hs_pct":
        return f"{v:.0f}%"
    if key == "kd":
        return f"{v:.2f}"
    return f"{v:,.0f}"


def _rounds(row):
    return (row.get("rounds_won") or 0) + (row.get("rounds_lost") or 0)


def _metrics(row):
    m = player_metrics(row, _rounds(row))
    m["kd"] = (row.get("kills") or 0) / max(1, row.get("deaths") or 0)
    return m


def _ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _role(agent):
    return AGENT_ROLE.get((agent or "").lower(), "Unknown")


# ---- round by round -------------------------------------------------------------------------------------------
def _round_story(tl, members):
    """Per round of this game's timeline: result, running score, side, spike, first blood, multi-kills, clutch and
    odd kills; plus per-member totals (first bloods, multi-kills, clutches, operator and knife kills, team kills)."""
    our, team_of = tl["our_team"], tl["team_of"]
    us = {p for p, t in team_of.items() if t == our}
    them = {p for p, t in team_of.items() if t != our}
    first_att = first_half_attackers(tl)
    by_round = defaultdict(list)
    for k in tl["kills"]:
        by_round[k["r"]].append(k)
    per = {p: {"fb": 0, "k3": 0, "k4": 0, "k5": 0, "clutch": [], "op": 0, "knife": [], "team_kills": []} for p in members}
    rounds, score_us, score_them = [], 0, 0
    for i, rnd in enumerate(tl["rounds"]):
        kills = sorted(by_round.get(i, []), key=lambda k: k["t"])
        alive_us, alive_them, clutch = set(us), set(them), None
        count = Counter()
        for k in kills:
            killer, victim = k["killer"], k["victim"]
            alive_us.discard(victim)
            alive_them.discard(victim)
            if killer in us and victim in them:
                count[killer] += 1
                if (k.get("weapon") or "").lower() == "operator" and killer in per:
                    per[killer]["op"] += 1
                if (k.get("weapon") or "").lower() in KNIVES and killer in per:
                    per[killer]["knife"].append(i + 1)
            if killer in us and victim in us and killer != victim and killer in per:
                per[killer]["team_kills"].append({"round": i + 1, "victim": victim})
            if clutch is None and len(alive_us) == 1 and alive_them:
                clutch = {"puuid": next(iter(alive_us)), "vs": min(len(alive_them), 5)}
        won = rnd["winner"] == our
        score_us, score_them = score_us + won, score_them + (not won)
        first = kills[0] if kills else None
        fb = None
        if first and first["killer"] in us | them:
            fb = {"ours": first["killer"] in us, "puuid": first["killer"] if first["killer"] in us else first["victim"]}
            if fb["ours"] and first["killer"] in per:
                per[first["killer"]]["fb"] += 1
        multi = {p: n for p, n in count.items() if n >= 3}
        for p, n in multi.items():
            if p in per:
                per[p]["k5" if n >= 5 else "k4" if n == 4 else "k3"] += 1
        if clutch:
            clutch["won"] = won
            if clutch["puuid"] in per:
                per[clutch["puuid"]]["clutch"].append({"round": i + 1, "vs": clutch["vs"], "won": won})
        side = None
        if first_att and i < 2 * HALF:
            side = "attack" if (first_att == our) == (i < HALF) else "defence"
        planted = None if not rnd.get("planter_team") else ("us" if rnd["planter_team"] == our else "them")
        rounds.append({"n": i + 1, "won": won, "score": [score_us, score_them], "side": side, "site": rnd.get("site"),
                       "planted": planted, "defused": bool(rnd.get("defused")), "first_blood": fb, "multi": multi,
                       "clutch": clutch})
    return rounds, per


# ---- highlights -----------------------------------------------------------------------------------------------
def _player_highlights(p, row, earlier, all_rows_by_match, match, engine, full, prior_timelines, per_round):
    """Personal records, near-records, splits, firsts, rank changes, milestones, streaks and forecast surprises."""
    out = []
    complete_now = ending(match) == COMPLETE
    done = [r for r in earlier if ending(r) == COMPLETE]
    now = _metrics(row)
    agent, role, map_ = row.get("agent"), _role(row.get("agent")), match.get("map")
    done_m = [_metrics(r) for r in done]
    vals = {key: [m[key] for m in done_m] for key, *_ in RECORD_STATS}
    recorded = set()
    for key, label, sign, counting in RECORD_STATS:
        v, hist = _shown(key, now.get(key)), [_shown(key, x) for x in vals[key] if x is not None]
        if v is None or (counting and not complete_now) or len(hist) < MIN_RECORD_GAMES:
            continue
        best, worst = (max(hist), min(hist)) if sign > 0 else (min(hist), max(hist))
        better = lambda a, b: a > b if sign > 0 else a < b  # noqa: E731
        high, low = ("Most", "Fewest") if counting else ("Highest", "Lowest")
        if better(v, best):
            recorded.add(key)
            good = sign > 0
            what = f"{high if sign > 0 else low} {label}"
            out.append(_hl(88 + min(10, len(hist) / 10), "record", f"{what} in a squad game: {_fmt(key, v)}",
                           f"Previous best {_fmt(key, best)} over {len(hist)} games", p, "good" if good else "bad"))
        elif v == best:
            recorded.add(key)
            out.append(_hl(64, "record", f"Tied their squad {'best' if sign > 0 else 'low'}: {_fmt(key, v)} {label}",
                           f"{len(hist)} earlier games", p))
        elif better(worst, v) and key in LOW_RECORDS:
            recorded.add(key)
            what = f"{low if sign > 0 else high} {label}"
            out.append(_hl(62, "record", f"{what} in a squad game: {_fmt(key, v)}",
                           f"Previous low {_fmt(key, worst)} over {len(hist)} games", p, "bad"))
        else:
            rank = 1 + sum(1 for x in hist if better(x, v))
            if 2 <= rank <= 3 and key in ("kills", "acs", "adr", "kd"):
                recorded.add(key)
                out.append(_hl(52 - 4 * (rank - 2), "near_record", f"{_ordinal(rank)}-best {label} in a squad game: {_fmt(key, v)}",
                               f"Out of {len(hist) + 1} games", p))
    # Best (or worst) in a long run of recent games, for the headline stats not already called out.
    for key, label in (("acs", "ACS"), ("kills", "kills")):
        if key in recorded or (key == "kills" and not complete_now):
            continue
        run_hi = next((i for i, m in enumerate(done_m) if m[key] >= now[key]), len(done))
        run_lo = next((i for i, m in enumerate(done_m) if m[key] <= now[key]), len(done))
        if run_hi >= SINCE_GAMES:
            out.append(_hl(40 + min(10, (run_hi - SINCE_GAMES) / 3), "run", f"Best {label} in {run_hi + 1} games: {_fmt(key, now[key])}", "", p))
        elif run_lo >= SINCE_GAMES:
            out.append(_hl(34, "run", f"Lowest {label} in {run_lo + 1} games: {_fmt(key, now[key])}", "", p, "bad"))
    # Best game on this agent / map.
    for what, same in (("agent", [m for r, m in zip(done, done_m) if r.get("agent") == agent]),
                       ("map", [m for r, m in zip(done, done_m) if r.get("map") == map_])):
        if "acs" in recorded or len(same) < MIN_SPLIT_GAMES:
            continue
        top = max(m["acs"] for m in same)
        if now["acs"] > top:
            name = agent if what == "agent" else map_
            out.append(_hl(55 if what == "agent" else 50, "split_best", f"Best game on {name}: {_fmt('acs', now['acs'])} ACS",
                           f"Previous best {_fmt('acs', top)} over {len(same)} games", p))
    # Firsts.
    if len(earlier) >= MIN_SPLIT_GAMES and agent and agent not in {r.get("agent") for r in earlier}:
        out.append(_hl(46, "first", f"First squad game on {agent}", f"After {len(earlier)} games", p, "info"))
    if len(earlier) >= MIN_RECORD_GAMES and role != "Unknown" and role not in {_role(r.get("agent")) for r in earlier}:
        out.append(_hl(52, "first", f"First time playing {role}", f"On {agent}", p, "info"))
    # Rank changes (the rank stored with each game).
    tiers = [r for r in earlier if r.get("tier")]
    if row.get("tier") and tiers:
        prev, peak = tiers[0], max(r["tier"] for r in tiers)
        if row["tier"] > peak and len(tiers) >= MIN_SPLIT_GAMES:
            out.append(_hl(84, "rank", f"New peak rank: {row.get('tier_name') or row['tier']}", f"Up from {prev.get('tier_name') or prev['tier']}", p))
        elif row["tier"] > prev["tier"]:
            out.append(_hl(70, "rank", f"Ranked up to {row.get('tier_name') or row['tier']}", f"From {prev.get('tier_name') or prev['tier']}", p))
        elif row["tier"] < prev["tier"]:
            out.append(_hl(54, "rank", f"Dropped to {row.get('tier_name') or row['tier']}", f"From {prev.get('tier_name') or prev['tier']}", p, "bad"))
    # Milestones.
    before = sum(r.get("kills") or 0 for r in earlier)
    after = before + (row.get("kills") or 0)
    if before // KILL_MILESTONE < after // KILL_MILESTONE:
        out.append(_hl(58, "milestone", f"Passed {after // KILL_MILESTONE * KILL_MILESTONE:,} kills with the squad", f"{after:,} in total", p, "info"))
    games = len(earlier) + 1
    if games % GAME_MILESTONE == 0:
        out.append(_hl(50, "milestone", f"{_ordinal(games)} squad game", "", p, "info"))
    # Top-fragger streak (most kills among the squad), this game included.
    streak = 0
    for mid in [match["match_id"]] + [r["match_id"] for r in earlier]:
        rows = all_rows_by_match.get(mid, [])
        if rows and max(r.get("kills") or 0 for r in rows) == next((r.get("kills") or 0 for r in rows if r["puuid"] == p), -1):
            streak += 1
        else:
            break
    if streak >= 3:
        out.append(_hl(40 + 6 * (streak - 3), "streak", f"Top-fragged {streak} games in a row", "", p))
    # Forecast surprises (the Forecasts tab's replay for this game).
    surprises = []
    if complete_now and len(earlier) >= MIN_PRIOR:
        for key, label, sign in (("kills", "kills", 1), ("acs", "ACS", 1), ("deaths", "deaths", -1)):
            fc = forecast_one(row, earlier, engine, full, key)
            if not fc or now.get(key) is None:
                continue
            lo, hi = fc["range"]
            width = max(1e-9, hi - lo)
            v = now[key]
            far = (v - hi) / width if v > hi else (lo - v) / width if v < lo else 0
            if far >= FORECAST_FAR and key not in recorded:
                beat = (v > hi) == (sign > 0)
                surprises.append(_hl((48 if beat else 38) + min(22, far * 25), "forecast",
                               f"{'Beat' if beat else 'Missed'} their forecast: {_fmt(key, v)} {label}",
                               f"The odds engine expected {_fmt(key, lo)}–{_fmt(key, hi)}", p, "good" if beat else "bad"))
    out += sorted(surprises, key=lambda h: -h["score"])[:1]  # their biggest surprise only
    # Timeline: multi-kills, clutches, first bloods, odd kills; "first ace" if their earlier timelines have none.
    if per_round:
        pr = per_round
        seen = [tl for tl in prior_timelines if p in tl["team_of"]]
        earlier_aces = sum(1 for tl in seen if aced(tl, p))
        if pr["k5"]:
            first = len(seen) >= MIN_RECORD_GAMES and earlier_aces == 0
            out.append(_hl(95 if first else 90, "ace", "First ace in a squad game!" if first else ("Ace" if pr["k5"] == 1 else f"{pr['k5']} aces"),
                           "Killed all five in one round", p))
        if pr["k4"]:
            out.append(_hl(56 + 6 * (pr["k4"] - 1), "multikill", "4K" if pr["k4"] == 1 else f"{pr['k4']} 4Ks", "Four kills in one round", p))
        if pr["k3"] >= 2:
            out.append(_hl(30 + 4 * pr["k3"], "multikill", f"{pr['k3']} 3Ks", "Three kills in a round, more than once", p))
        for c in pr["clutch"]:
            if c["won"] and c["vs"] >= 2:
                out.append(_hl({2: 34, 3: 64, 4: 84, 5: 97}[c["vs"]], "clutch", f"Won a 1v{c['vs']} clutch", f"Round {c['round']}", p))
        if pr["fb"] >= 5:
            out.append(_hl(36 + 2 * pr["fb"], "first_blood", f"Opened {pr['fb']} rounds with the first kill", "", p))
        if pr["op"] >= 6:
            out.append(_hl(38, "operator", f"{pr['op']} Operator kills", "", p, "odd"))
        for rnd in pr["knife"]:
            out.append(_hl(62, "knife", "Knife kill", f"Round {rnd}", p, "odd"))
        for tk in pr["team_kills"]:
            out.append(_hl(50, "team_kill", "Killed a teammate", f"Round {tk['round']}", p, "odd"))
    return out


def aced(tl, p):
    """Whether p aced a round in timeline tl (five enemy kills in one round)."""
    our = tl["team_of"].get(p)
    count = Counter(k["r"] for k in tl["kills"] if k["killer"] == p and tl["team_of"].get(k["victim"]) not in (None, our))
    return any(n >= 5 for n in count.values())


def _game_line_highlights(rows, match):
    """Odd or notable stat lines within this game: carries, efficiency, even scoreboards, assists over kills..."""
    out = []
    mets = {r["puuid"]: _metrics(r) for r in rows}
    total_dmg = sum(r.get("damage_dealt") or 0 for r in rows)
    complete = ending(match) == COMPLETE
    for r in rows:
        p, m = r["puuid"], mets[r["puuid"]]
        share = (r.get("damage_dealt") or 0) / total_dmg if total_dmg else 0
        if share >= 0.33:
            out.append(_hl(50 + min(20, (share - 0.33) * 200), "carry", f"Dealt {share:.0%} of the squad's damage", "An even split is 20%", p))
        if m["assists"] > m["kills"] and m["assists"] >= 8:
            out.append(_hl(34, "odd_line", f"More assists ({m['assists']}) than kills ({m['kills']})", "", p, "odd"))
        shots = (r.get("headshots") or 0) + (r.get("bodyshots") or 0) + (r.get("legshots") or 0)
        if shots >= 25 and not r.get("headshots"):
            out.append(_hl(40, "odd_line", "Not a single headshot", f"{shots} hits", p, "odd"))
        elif m.get("hs_pct") is not None and m["hs_pct"] >= 40 and shots >= 25:
            out.append(_hl(42, "odd_line", f"{m['hs_pct']:.0f}% headshots", "", p))
        if m["kd"] >= 3 and m["kills"] >= 15:
            out.append(_hl(48, "odd_line", f"{m['kd']:.1f} K/D", f"{m['kills']} kills, {m['deaths']} deaths", p))
        if complete and m["deaths"] <= 5 and _rounds(r) >= 18:
            out.append(_hl(44, "odd_line", f"Only died {m['deaths']} times", f"In {_rounds(r)} rounds", p))
    if len(rows) >= 5:
        by_kills = sorted(rows, key=lambda r: -(r.get("kills") or 0))
        by_dmg = sorted(rows, key=lambda r: -(r.get("damage_dealt") or 0))
        if by_kills[0]["puuid"] == by_dmg[-1]["puuid"] and (by_kills[0].get("kills") or 0) > (by_kills[1].get("kills") or 0):
            out.append(_hl(44, "odd_line", "Most kills, least damage", "Efficient, or finishing off everyone else's work", by_kills[0]["puuid"], "odd"))
        if by_dmg[0]["puuid"] == by_kills[-1]["puuid"] and (by_dmg[0].get("damage_dealt") or 0) > (by_dmg[1].get("damage_dealt") or 0):
            out.append(_hl(44, "odd_line", "Most damage, fewest kills", "Plenty of damage, few finishing blows", by_dmg[0]["puuid"], "odd"))
        kills = [r.get("kills") or 0 for r in rows]
        if complete and max(kills) - min(kills) <= 3 and min(kills) >= 10:
            out.append(_hl(38, "team_line", f"Dead even: everyone got {min(kills)}–{max(kills)} kills", "", None, "odd"))
        if all(mets[r["puuid"]]["kills"] > mets[r["puuid"]]["deaths"] for r in rows):
            out.append(_hl(46, "team_line", "All five finished with a positive K/D", "", None))
        elif all(mets[r["puuid"]]["kills"] < mets[r["puuid"]]["deaths"] for r in rows):
            out.append(_hl(40, "team_line", "All five finished with a negative K/D", "", None, "bad"))
    return out


def _round_highlights(rounds, match):
    """Comebacks, blown leads, flawless halves, round streaks, pistols, retakes, overtime, first-blood control."""
    out = []
    if not rounds:
        return out
    won_game = match.get("result") == "win"
    worst = max(rounds, key=lambda r: r["score"][1] - r["score"][0])
    best = max(rounds, key=lambda r: r["score"][0] - r["score"][1])
    deficit, lead = worst["score"][1] - worst["score"][0], best["score"][0] - best["score"][1]
    if won_game and deficit >= 4:
        out.append(_hl(60 + min(30, 6 * (deficit - 4)), "comeback", f"Came back from {worst['score'][0]}–{worst['score'][1]} down",
                       f"Down {deficit} after round {worst['n']}"))
    if match.get("result") == "loss" and lead >= 4:
        out.append(_hl(55 + min(25, 5 * (lead - 4)), "blown_lead", f"Lost from {best['score'][0]}–{best['score'][1]} up",
                       f"Up {lead} after round {best['n']}", None, "bad"))
    if match.get("rounds_lost") == 0 and won_game:
        out.append(_hl(96, "shutout", f"Shutout: {match.get('rounds_won')}–0", "Didn't drop a single round"))
    elif len(rounds) >= HALF and all(r["won"] for r in rounds[:HALF]):
        out.append(_hl(86, "flawless_half", "Flawless first half: 12–0", ""))
    elif won_game and len(rounds) - HALF >= 6 and all(r["won"] for r in rounds[HALF:]):
        out.append(_hl(68, "flawless_half", f"Won every round of the second half ({len(rounds) - HALF}–0)", ""))
    run = longest = lost_run = longest_lost = 0
    for r in rounds:
        run, lost_run = (run + 1, 0) if r["won"] else (0, lost_run + 1)
        longest, longest_lost = max(longest, run), max(longest_lost, lost_run)
    if longest >= 6 and match.get("rounds_lost"):
        out.append(_hl(32 + 3 * (longest - 6), "round_streak", f"Won {longest} rounds in a row", ""))
    if longest_lost >= 6:
        out.append(_hl(30 + 3 * (longest_lost - 6), "round_streak", f"Lost {longest_lost} rounds in a row", "", None, "bad"))
    pistols = [rounds[i]["won"] for i in (0, HALF) if i < len(rounds)]
    if len(pistols) == 2 and all(pistols):
        out.append(_hl(26, "pistols", "Won both pistol rounds", ""))
    elif len(pistols) == 2 and not any(pistols):
        out.append(_hl(22, "pistols", "Lost both pistol rounds", "", None, "bad"))
    retakes = sum(1 for r in rounds if r["planted"] == "them" and r["won"])
    if retakes >= 4:
        out.append(_hl(40 + 2 * retakes, "retakes", f"Won {retakes} rounds after the enemy planted", ""))
    fb = [r["first_blood"]["ours"] for r in rounds if r["first_blood"]]
    if len(fb) >= 12 and sum(fb) / len(fb) >= 0.7:
        out.append(_hl(44, "first_blood", f"Got the first kill in {sum(fb)} of {len(fb)} rounds", ""))
    elif len(fb) >= 12 and sum(fb) / len(fb) <= 0.3:
        out.append(_hl(36, "first_blood", f"Lost the first duel in {len(fb) - sum(fb)} of {len(fb)} rounds", "", None, "bad"))
    return out


def _squad_highlights(match, earlier_matches, rows, earlier_rows_by_match):
    """Squad records, streaks, map firsts, milestones, overtime and surrenders."""
    out = []
    done = [m for m in earlier_matches if ending(m) == COMPLETE]
    complete = ending(match) == COMPLETE
    margin = (match.get("rounds_won") or 0) - (match.get("rounds_lost") or 0)
    if complete and len(done) >= MIN_RECORD_GAMES:
        margins = [(m.get("rounds_won") or 0) - (m.get("rounds_lost") or 0) for m in done]
        if margin > 0 and margin > max(margins):
            out.append(_hl(74, "squad_record", f"Biggest win yet: {match.get('rounds_won')}–{match.get('rounds_lost')}", f"Out of {len(done) + 1} games"))
        if margin < 0 and margin < min(margins):
            out.append(_hl(62, "squad_record", f"Heaviest loss yet: {match.get('rounds_won')}–{match.get('rounds_lost')}", f"Out of {len(done) + 1} games", None, "bad"))
        kills_now = sum(r.get("kills") or 0 for r in rows)
        kills_before = [sum(r.get("kills") or 0 for r in earlier_rows_by_match.get(m["match_id"], [])) for m in done]
        if kills_before and kills_now > max(kills_before):
            out.append(_hl(64, "squad_record", f"Most kills as a squad: {kills_now}", f"Previous best {max(kills_before)}"))
        if total_rounds(match) > max(total_rounds(m) for m in done):
            out.append(_hl(52, "squad_record", f"Longest game yet: {total_rounds(match)} rounds", "", None, "odd"))
        lengths = [m["game_length_ms"] for m in done if m.get("game_length_ms")]
        if match.get("game_length_ms") and len(lengths) >= MIN_RECORD_GAMES and match["game_length_ms"] < min(lengths) and margin > 0:
            out.append(_hl(48, "squad_record", f"Quickest win yet: {match['game_length_ms'] / 60000:.0f} minutes", ""))
    # Result streaks, this game included, and the streak it ended.
    results = [match.get("result")] + [m.get("result") for m in earlier_matches]
    run = next((i for i, r in enumerate(results) if r != results[0]), len(results))
    if results[0] in ("win", "loss") and run >= 3:
        word = "win" if results[0] == "win" else "loss"
        out.append(_hl(40 + 5 * (run - 3), "streak", f"{_ordinal(run)} {word} in a row", "", None, "good" if word == "win" else "bad"))
    elif results[0] in ("win", "loss") and len(results) > 1 and results[1] != results[0]:
        prev = next((i for i, r in enumerate(results[1:]) if r != results[1]), len(results) - 1)
        if prev >= 3 and results[1] in ("win", "loss"):
            snapped = "losing" if results[1] == "loss" else "winning"
            out.append(_hl(50 + 4 * (prev - 3), "streak", f"Snapped a {prev}-game {snapped} streak", "", None,
                           "good" if snapped == "losing" else "bad"))
    # This map.
    map_ = match.get("map")
    on_map = [m for m in earlier_matches if m.get("map") == map_]
    if len(earlier_matches) >= MIN_SPLIT_GAMES and not on_map:
        out.append(_hl(45, "map_first", f"First squad game on {map_}", "", None, "info"))
    elif match.get("result") == "win" and len(on_map) >= 2 and not any(m.get("result") == "win" for m in on_map):
        out.append(_hl(55, "map_first", f"First win on {map_}", f"At the {_ordinal(len(on_map) + 1)} try"))
    else:
        map_results = [match.get("result")] + [m.get("result") for m in on_map]
        map_run = next((i for i, r in enumerate(map_results) if r != map_results[0]), len(map_results))
        if map_run >= 3 and map_results[0] in ("win", "loss"):
            out.append(_hl(38 + 3 * (map_run - 3), "map_streak", f"{_ordinal(map_run)} straight {'win' if map_results[0] == 'win' else 'loss'} on {map_}",
                           "", None, "good" if map_results[0] == "win" else "bad"))
    n = len(earlier_matches) + 1
    if n % SQUAD_MILESTONE == 0:
        out.append(_hl(56, "milestone", f"{_ordinal(n)} squad game together", "", None, "info"))
    if went_to_overtime(match):
        extra = total_rounds(match) - 24
        out.append(_hl(52 + 4 * max(0, extra - 2) if match.get("result") == "win" else 44, "overtime",
                       ("Won in overtime" if match.get("result") == "win" else "Lost in overtime") if extra <= 2 else f"Overtime marathon: {total_rounds(match)} rounds",
                       "", None, "good" if match.get("result") == "win" else "bad"))
    if ending(match) == FORFEIT:
        out.append(_hl(50, "surrender", "The other team surrendered" if match.get("result") == "win" else "The squad surrendered",
                       f"At {match.get('rounds_won')}–{match.get('rounds_lost')}", None, "odd" if match.get("result") == "win" else "bad"))
    return out


def _betting(bets, match, edge, members_by_bettor):
    """The betting recap for this game, and its highlights."""
    settled = [b for b in bets if b["status"] in ("won", "lost", "void")]
    out = []
    if not settled:
        return None, out
    net = defaultdict(float)
    for b in settled:
        net[b["bettor"]] += (b.get("payout") or 0) - b["stake"]
    staked = sum(b["stake"] for b in settled)
    house = -sum(net.values())
    won = [b for b in settled if b["status"] == "won"]
    top = sorted(settled, key=lambda b: -((b.get("payout") or 0) - b["stake"]))
    recap = {"bets": len(settled), "staked": round(staked, 2), "house": round(house, 2),
             "bettors": sorted(({"bettor": k, "net": round(v, 2), "bets": sum(1 for b in settled if b["bettor"] == k)}
                                for k, v in net.items()), key=lambda x: -x["net"]),
             "best": [{"bettor": b["bettor"], "description": b.get("description"), "odds": b["odds_decimal"],
                       "american": decimal_to_american(b["odds_decimal"]), "net": round((b.get("payout") or 0) - b["stake"], 2),
                       "status": b["status"]} for b in top[:5]]}
    if won:
        longest = max(won, key=lambda b: b["odds_decimal"])
        if longest["odds_decimal"] >= 4.0 and longest["market_type"] != "parlay":
            out.append(_hl(46 + min(20, (longest["odds_decimal"] - 4) * 4), "longshot",
                           f"{longest['bettor']} hit a {decimal_to_american(longest['odds_decimal'])} long shot", longest.get("description") or "",
                           members_by_bettor.get(longest["bettor"].lower()), "odd"))
    for b in won:
        if b["market_type"] == "parlay":
            legs = len(json.loads(b.get("context") or "{}").get("legs") or [])
            out.append(_hl(52 + 4 * max(0, legs - 2), "parlay", f"{b['bettor']} hit a {legs}-leg parlay at {decimal_to_american(b['odds_decimal'])}",
                           f"+{(b.get('payout') or 0) - b['stake']:,.0f} credits", members_by_bettor.get(b["bettor"].lower())))
    if recap["bettors"] and recap["bettors"][0]["net"] >= 150:
        best = recap["bettors"][0]
        out.append(_hl(40 + min(15, best["net"] / 100), "big_win", f"{best['bettor']} won {best['net']:,.0f} credits on this game", "",
                       members_by_bettor.get(best["bettor"].lower())))
    if len(settled) >= 5 and not won:
        out.append(_hl(36, "wipeout", "Not a single bet won", f"{len(settled)} bets, all lost or refunded", None, "bad"))
    if abs(house) >= 300:
        out.append(_hl(30, "house", f"The house {'won' if house > 0 else 'lost'} {abs(house):,.0f} credits", "", None, "info"))
    # Underdogs / favourites, from the prices on the match-result bets.
    chances = []
    for b in settled:
        if b["market_type"] != "team_win":
            continue
        ctx = json.loads(b.get("context") or "{}")
        c = ctx.get("fair_prob") or fair_chance(b["odds_decimal"], "team_win", edge)
        chances.append(c if b["selection"] == "win" else 1 - c)
    if chances:
        p_win = sum(chances) / len(chances)
        if match.get("result") == "win" and p_win <= 0.4:
            out.append(_hl(56 + (0.4 - p_win) * 60, "upset", "Won as underdogs", f"The odds gave the squad {p_win:.0%}"))
        elif match.get("result") == "loss" and p_win >= 0.6:
            out.append(_hl(44, "upset", "Lost as favourites", f"The odds gave the squad {p_win:.0%}", None, "bad"))
    return recap, out


def build_recap(db, engine, match_id=None, bettor_of=None, bonus_max=250):
    """The Matches tab's recap for one game (the most recent by default); None when there are no games."""
    matches = db.matches()  # newest first
    if not matches:
        return None
    match = next((m for m in matches if m["match_id"] == match_id), matches[0])
    idx = matches.index(match)
    earlier_matches = matches[idx + 1:]
    members = db.members()
    nick = {m["puuid"]: m.get("nickname") or m["name"] for m in members}
    order = {m["puuid"]: i for i, m in enumerate(members)}
    bettor_of = bettor_of or {}
    members_by_bettor = {(bettor_of.get(m["puuid"]) or nick[m["puuid"]]).lower(): m["puuid"] for m in members}

    all_rows = db.player_rows()  # newest first
    by_match, by_player = defaultdict(list), defaultdict(list)
    for r in all_rows:
        by_match[r["match_id"]].append(r)
        by_player[r["puuid"]].append(r)
    rows = sorted(by_match.get(match["match_id"], []), key=lambda r: order.get(r["puuid"], 99))
    started = match.get("started_ts") or 0
    earlier_of = {p: [r for r in rs if (r.get("started_ts") or 0) < started] for p, rs in by_player.items()}
    full = full_game_rounds(earlier_matches or matches)

    timelines = db.timelines()
    tl = next((t["data"] for t in timelines if t["match_id"] == match["match_id"]), None)
    prior_timelines = [t["data"] for t in timelines if (t.get("started_ts") or 0) < started]
    rounds, per = _round_story(tl, list(nick)) if tl else ([], {})

    # Scoreboard: this game's line, the player's usual (average of earlier complete games) and the forecast.
    total_dmg = sum(r.get("damage_dealt") or 0 for r in rows) or 1
    board = []
    for r in rows:
        p, m = r["puuid"], _metrics(r)
        earlier = earlier_of.get(p, [])
        done = [e for e in earlier if ending(e) == COMPLETE]
        usual = {k: round(sum(_metrics(e)[k] or 0 for e in done) / len(done), 2) for k in USUAL_KEYS} if done else None
        fc = {k: forecast_one(r, earlier, engine, full, k) for k in FORECAST_KEYS} if len(earlier) >= MIN_PRIOR else {}
        tiers = [e for e in earlier if e.get("tier")]
        board.append({"puuid": p, "nickname": nick.get(p, p[:8]), "agent": r.get("agent"), "role": _role(r.get("agent")),
                      **{k: round(m[k], 2) if m.get(k) is not None else None for k in USUAL_KEYS},
                      "damage_share": round((r.get("damage_dealt") or 0) / total_dmg, 3), "tier_name": r.get("tier_name"),
                      "rank_change": (0 if not (r.get("tier") and tiers) else (r["tier"] > tiers[0]["tier"]) - (r["tier"] < tiers[0]["tier"])),
                      "usual": usual, "usual_games": len(done), "forecast": fc,
                      "rounds": {k: per[p][k] for k in ("fb", "k3", "k4", "k5")} | {"clutches": [c for c in per[p]["clutch"] if c["won"] and c["vs"] >= 2]}
                      if p in per else None})

    highlights = []
    for r in rows:
        p = r["puuid"]
        highlights += _player_highlights(p, r, earlier_of.get(p, []), by_match, match, engine, full, prior_timelines, per.get(p))
    highlights += _game_line_highlights(rows, match)
    highlights += _round_highlights(rounds, match)
    highlights += _squad_highlights(match, earlier_matches, rows, by_match)
    bets = [b for b in db.bets() if b.get("settled_match_id") == match["match_id"]]
    betting, bet_highlights = _betting(bets, match, engine.edge, members_by_bettor)
    highlights += bet_highlights
    rewards = [{"puuid": w["puuid"], "nickname": nick.get(w["puuid"], w.get("bettor")), "bettor": w["bettor"],
                "base": w["base"], "bonus": w["bonus"]} for w in db.rewards() if w["match_id"] == match["match_id"]]
    for w in rewards:
        if w["bonus"] >= bonus_max:
            highlights.append(_hl(34, "reward", "Earned the full performance bonus", f"+{w['bonus']:.0f} credits", w["puuid"]))
    for h in highlights:
        h["nickname"] = nick.get(h["puuid"]) if h["puuid"] else None
    highlights.sort(key=lambda h: (-h["score"], order.get(h["puuid"], -1)))

    # The night: games within SESSION_GAP_S of each other, up to and including this one.
    night = [match]
    for m in earlier_matches:
        if (night[-1].get("started_ts") or 0) - (m.get("started_ts") or 0) > SESSION_GAP_S:
            break
        night.append(m)
    wins = sum(1 for m in night if m.get("result") == "win")
    losses = sum(1 for m in night if m.get("result") == "loss")
    if len(night) >= 3 and losses == 0:
        highlights.append(_hl(44, "night", f"Perfect night so far: {wins}–0", "", None))
        highlights.sort(key=lambda h: (-h["score"], order.get(h["puuid"], -1)))
    header = {k: match.get(k) for k in ("match_id", "map", "mode", "mode_label", "started_ts", "started_at", "season",
                                        "rounds_won", "rounds_lost", "result", "game_length_ms")}
    header |= {"ending": ending(match), "overtime": went_to_overtime(match), "night_game": len(night),
               "night_record": [wins, losses], "number": len(earlier_matches) + 1,
               "newer": matches[idx - 1]["match_id"] if idx > 0 else None,
               "older": matches[idx + 1]["match_id"] if idx + 1 < len(matches) else None}
    return {"match": header, "players": board, "rounds": rounds, "betting": betting, "rewards": rewards,
            "highlights": highlights, "top_cards": TOP_CARDS, "min_record_games": MIN_RECORD_GAMES}
