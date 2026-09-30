"""Parlay pricing: legs that decide each other are refused, legs that tend to land together are priced together.

A parlay's price is its legs' odds multiplied together, which is only fair when the legs are independent. Two
kinds of leg aren't:

- Legs that decide each other. Every team market (result, total rounds, overtime, rounds won / lost, margin,
  exact score) is settled from the final score, so score_conflict() settles each team leg on every possible final
  score: if one leg can only win when another does ("13-7" and "Win"), or they can never both win, the parlay is
  refused. Pricing these from history wouldn't be safe, and the refusal explains itself.
- Legs that tend to land together (a player's kills over and ACS over, a win and the enemy's rounds under). Each leg
  is settled at today's line on the squad's last LOOKBACK games (bets.BetManager.leg_history), giving a string per
  leg: "1" won, "0" lost, "-" void. correlation() compares how often the legs all won together with how often they
  would have if they were independent, and the parlay's odds are divided by that ratio (its "factor").

The factor is worked out two ways and the larger one is used:
- linked groups: pairs of legs that won together clearly more often than chance (a z score of at least LINK_Z) are
  joined into groups, and each group's joint-win ratio is multiplied in. This finds one strong pair (kills + ACS)
  inside a long parlay without the other legs' noise drowning it out.
- the whole parlay's joint-win ratio, which catches many weak links adding up (five players' kills overs and total
  rounds over all ride on a long game).
Every ratio is pulled toward 1 by PRIOR pseudo-games, so a handful of games can't swing the price. The factor never
goes below 1 (odds are only ever cut, never boosted, since bettors pick the combinations and would find the ones
history flatters by chance) or above MAX_FACTOR, and a parlay never pays less than its longest leg would alone.

The leg strings are saved on the bet, so settlement can re-price the legs that stood when some are voided.
"""
import math

LOOKBACK = 60  # recent games each leg is replayed on
MIN_GAMES = 10  # fewer usable games than this: no adjustment
PRIOR = 3.0  # pseudo-games at "independent" added to each ratio
LINK_Z = 2.0  # how far above chance a pair's joint wins must be to link the two legs
MAX_FACTOR = 4.0

# Team markets settled purely from the final score, and the final scores a first-to-13 game can end on: regulation
# wins and losses, then overtime (win by two) up to 20-18.
SCORE_TYPES = {"team_win", "team_ou", "team_ot", "team_rw", "team_rl", "team_margin", "team_score"}
FINAL_SCORES = ([(13, x) for x in range(12)] + [(x, 13) for x in range(12)]
                + [(14 + j, 12 + j) for j in range(7)] + [(12 + j, 14 + j) for j in range(7)])


def score_conflict(legs, evaluate):
    """Why these legs can't share a parlay, or None. legs: dicts with market_type, selection, line, description;
    evaluate: bets.BetManager._evaluate. One team leg that only wins when another does, or two that can't both win,
    are refused."""
    team = [leg for leg in legs if leg["market_type"] in SCORE_TYPES]
    if len(team) < 2:
        return None
    wins = []
    for leg in team:
        bet = {"market_type": leg["market_type"], "selection": leg["selection"], "line": leg.get("line"), "context": "{}"}
        wins.append({score for score in FINAL_SCORES if evaluate(bet, _final(score), {})[0] == "won"})
    for i, a in enumerate(team):
        for j in range(i + 1, len(team)):
            b, wa, wb = team[j], wins[i], wins[j]
            if not wa & wb:
                return f"{a['description']} and {b['description']} can't both win."
            if wa <= wb or wb <= wa:
                first, second = (a, b) if wa <= wb else (b, a)
                return f"{first['description']} already decides {second['description']}: pick one of them."
    return None


def _final(score):
    rw, rl = score
    return {"mode": "competitive", "rounds_won": rw, "rounds_lost": rl, "result": "win" if rw > rl else "loss"}


def _ratio(hists, idx):
    """(joint-win ratio pulled toward 1, games used, z score) for the legs at idx, over the games where none of them
    was void."""
    games = [g for g in range(min(len(hists[i]) for i in idx)) if all(hists[i][g] != "-" for i in idx)]
    n = len(games)
    if n < MIN_GAMES:
        return 1.0, n, 0.0
    p0 = 1.0
    for i in idx:
        p0 *= sum(hists[i][g] == "1" for g in games) / n
    joint = sum(all(hists[i][g] == "1" for i in idx) for g in games)
    expected = n * p0
    sd = math.sqrt(n * p0 * (1 - p0))
    return (joint + PRIOR) / (expected + PRIOR), n, ((joint - expected) / sd if sd else 0.0)


def correlation(hists):
    """{"factor", "games", "linked"} for legs replayed on recent games (strings of "1" / "0" / "-", newest first).
    linked: groups of leg indexes that won together more often than chance."""
    k = len(hists)
    if k < 2 or not all(hists):
        return {"factor": 1.0, "games": 0, "linked": []}
    parent = list(range(k))

    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i

    for i in range(k):
        for j in range(i + 1, k):
            if _ratio(hists, [i, j])[2] >= LINK_Z:
                parent[root(j)] = root(i)
    groups = {}
    for i in range(k):
        groups.setdefault(root(i), []).append(i)
    linked = [g for g in groups.values() if len(g) > 1]
    by_groups = 1.0
    for g in linked:
        by_groups *= _ratio(hists, g)[0]
    whole, games, _ = _ratio(hists, list(range(k)))
    factor = min(MAX_FACTOR, max(1.0, by_groups, whole))
    return {"factor": round(factor, 3), "games": games, "linked": linked}


def price(decimals, factor):
    """The parlay's decimal odds: the legs' odds multiplied, divided by the correlation factor, and never less than
    the longest leg alone."""
    independent = 1.0
    for d in decimals:
        independent *= d
    return round(max(independent / max(1.0, factor), max(decimals)), 2)
