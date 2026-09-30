"""Team "moments": facts about a game read from its round timeline (timeline.py), for the team markets that go past
the final score: the pistol round, half-time, an ace, a comeback and flawless rounds.

game_facts() is the one place these are worked out, so pricing (odds.py, over the squad's history) and settlement
(bets.py, on the game just played) always agree. A fact is None when the game can't decide it: no timeline stored
(the full record was never fetched), or a mode without a round 12 for half-time.
"""

HALF = 12  # rounds in a half in the first-to-13 modes
COMEBACK_DOWN = 5  # "comeback": the squad was this many rounds behind at some point and still won


def game_facts(match, timeline):
    """{fact: value} for one game. match: a matches row (result, mode); timeline: its stored timeline dict
    (our_team, team_of, rounds, kills) or None."""
    facts = {"pistol": None, "half": None, "ace": None, "comeback": None, "flawless": None}
    rounds = (timeline or {}).get("rounds") or []
    if not rounds:
        return facts
    our, team_of, kills = timeline.get("our_team"), timeline.get("team_of") or {}, timeline.get("kills") or []
    won = [r.get("winner") == our for r in rounds]
    facts["pistol"] = won[0]
    if len(won) >= HALF and (match.get("mode") or "").lower() not in ("swiftplay", "spikerush"):
        facts["half"] = sum(won[:HALF]) > HALF // 2  # ahead after round 12 (6-6 is not ahead)
    per_round, deaths = {}, {}
    for k in kills:
        killer_ours, victim_ours = team_of.get(k.get("killer")) == our, team_of.get(k.get("victim")) == our
        if killer_ours and not victim_ours:
            per_round[(k["r"], k["killer"])] = per_round.get((k["r"], k["killer"]), 0) + 1
        if victim_ours:
            deaths[k["r"]] = deaths.get(k["r"], 0) + 1
    facts["ace"] = any(n >= 5 for n in per_round.values())
    facts["flawless"] = sum(1 for i, w in enumerate(won) if w and not deaths.get(i))
    lead = worst = 0
    for w in won:
        lead += 1 if w else -1
        worst = min(worst, lead)
    facts["comeback"] = match.get("result") == "win" and worst <= -COMEBACK_DOWN
    return facts
