"""Betting-odds engine.

Each member's history of 5-stack games becomes a recency-weighted sample.
Optional context (expected map, expected agent per member) boosts the weight
of matching games. Lines sit at the weighted median, probabilities come from a
Gaussian-kernel smoothed empirical distribution, and a house edge is applied
so the book is not perfectly fair (just like a real sportsbook).

Games that ended early (a surrender) are partial data. Their counting stats are
scaled up to a full-length game and the game's weight is cut to the share of a
full game that was played, so a 9-round forfeit neither drags the kills line
down nor counts as much as a real game. They are left out of the total-rounds
market altogether, and count normally toward the match-result market.
"""
import math
from functools import lru_cache
import random
import time
from bisect import bisect_left
from collections import defaultdict
from statistics import NormalDist

from .gamestate import DEFAULT_ROUNDS_TO_WIN, FORFEIT, ending, full_game_rounds, rounds_to_win, went_to_overtime
from .moments import game_facts
from .stats import aggregate, player_metrics

STAT_DEFS = [
    {"key": "kills", "label": "Kills", "kind": "count", "floor_h": 1.2},
    {"key": "deaths", "label": "Deaths", "kind": "count", "floor_h": 1.2},
    {"key": "assists", "label": "Assists", "kind": "count", "floor_h": 1.0},
    {"key": "acs", "label": "ACS", "kind": "score", "floor_h": 12.0},
    {"key": "adr", "label": "ADR", "kind": "score", "floor_h": 8.0},
    {"key": "hs_pct", "label": "Headshot %", "kind": "pct", "floor_h": 2.5},
]
# The stats with an over / under line per player on the board (and custom lines on the page). ACS and ADR aren't
# offered as player props; they still price the "top of the scoreboard" markets and appear in stats and forecasts.
PROP_KEYS = ("kills", "deaths", "assists", "hs_pct")
FLOOR_H = {d["key"]: d["floor_h"] for d in STAT_DEFS}
STAT_BY_KEY = {d["key"]: d for d in STAT_DEFS}
# Custom lines ("I think Loog gets 25 kills"): any .5 line on a player prop, priced from the same smoothed
# distribution as the board's lines. The side you bet needs a fair chance between these, so long shots top out
# around +1800 and near-certainties (stake 50 to win 1) aren't offered.
ALT_MIN_CHANCE = 0.05
ALT_MAX_CHANCE = 0.90
# Exact numbers ("exactly 25 kills") on the counting stats: always long shots, so the floor is lower (about +4700).
EXACT_MIN_CHANCE = 0.02
FLOOR_H["acs_rel"] = 0.06  # ACS as a multiple of the player's own average
COUNT_KEYS = [d["key"] for d in STAT_DEFS if d["kind"] == "count"]


def partial_game(metrics, rounds, full_rounds):
    """Scale a forfeited game's counting stats to a full-length game. Returns (metrics, share of a game played)."""
    share = min(1.0, rounds / full_rounds) if rounds else 0.0
    if share <= 0 or share >= 1:
        return metrics, max(share, 0.0)
    scaled = dict(metrics)
    for k in COUNT_KEYS:
        if scaled.get(k) is not None:
            scaled[k] = scaled[k] / share
    return scaled, share

# "Who tops the scoreboard" markets. Each has a counter market for the bottom of the same stat ("low").
# acs_rel is ACS divided by the player's own average ACS over their 5-stack history, so anyone can win it.
TOP_DEFS = [
    {"id": "top:kills", "key": "kills", "label": "Top fragger", "desc": "Most kills on the team",
     "low_id": "low:kills", "low_label": "Bottom fragger", "low_desc": "Fewest kills on the team"},
    {"id": "top:acs", "key": "acs", "label": "Highest ACS", "desc": "Highest average combat score",
     "low_id": "low:acs", "low_label": "Lowest ACS", "low_desc": "Lowest average combat score"},
    {"id": "top:assists", "key": "assists", "label": "Most assists", "desc": "Most assists on the team",
     "low_id": "low:assists", "low_label": "Fewest assists", "low_desc": "Fewest assists on the team"},
    {"id": "top:deaths", "key": "deaths", "label": "Most deaths", "desc": "Bottom of the scoreboard",
     "low_id": "low:deaths", "low_label": "Fewest deaths", "low_desc": "Hardest to kill"},
    {"id": "top:hs_pct", "key": "hs_pct", "label": "Best HS %", "desc": "Highest headshot percentage",
     "low_id": "low:hs_pct", "low_label": "Worst HS %", "low_desc": "Lowest headshot percentage"},
    {"id": "top:acs_rel", "key": "acs_rel", "label": "Popped off", "desc": "Highest ACS compared with their own average",
     "low_id": "low:acs_rel", "low_label": "Got diff'd", "low_desc": "Lowest ACS compared with their own average"},
]


def phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def decimal_to_american(dec):
    if dec >= 2.0:
        return f"+{int(round((dec - 1.0) * 100))}"
    return f"-{int(round(100.0 / (dec - 1.0)))}"


def selection(key, label, fair, overround):
    p = clamp(fair * overround, 0.01, 0.985)
    dec = round(1.0 / p, 2)
    return {
        "key": key,
        "label": label,
        "fair_prob": round(fair, 4),
        "prob": round(p, 4),
        "decimal": dec,
        "american": decimal_to_american(dec),
    }


MULTI_WAY = {"top", "team_score", "team_margin", "exact"}  # many possible outcomes: these carry double the house edge


def market_overround(market_type, edge):
    """How far a market's prices add up past 100%: multi-way markets ("top"/"low" have one selection per player,
    exact score and winning margin several each) carry double the house edge."""
    return 1.0 + edge * (2.0 if market_type in MULTI_WAY else 1.0)


# ---- final-score model ------------------------------------------------------------------------------------------
# Exact score, winning margin and rounds won / lost come from one distribution of final scores: every round is won
# with chance r, first to 13, and 12-12 goes to overtime (won by whoever takes two rounds in a row, chance
# r^2 / (r^2 + (1-r)^2)). r itself varies from game to game (normal, standard deviation sd), because some nights
# the squad is simply better or worse than usual. mu and sd are tuned so the model agrees with the match-result
# and overtime markets, so no two markets on the board contradict each other.
SCORE_Z = [NormalDist().inv_cdf((i + 0.5) / 9) for i in range(9)]  # equal-weight points of the r distribution
SCORE_SD_MAX = 0.2
MARGIN_BANDS = [("w1-2", "Win by 1–2", 1, 2), ("w3-5", "Win by 3–5", 3, 5), ("w6+", "Win by 6+", 6, 13),
                ("l1-2", "Lose by 1–2", -2, -1), ("l3-5", "Lose by 3–5", -5, -3), ("l6+", "Lose by 6+", -13, -6)]

# Team markets read from the round timeline (moments.py), in the page's order. kind "yes": a yes/no-style pair whose
# first key is the fact being true, pulled toward `prior`; kind "ou": an over / under on a count or minutes.
MOMENT_DEFS = [
    {"fact": "pistol", "kind": "yes", "label": "Pistol round", "desc": "Does the 5-stack win round 1?", "prior": 0.5,
     "keys": [("yes", "Yes"), ("no", "No")]},
    {"fact": "half", "kind": "yes", "label": "Ahead at half-time", "desc": "Is the 5-stack ahead after round 12?", "prior": 0.45,
     "keys": [("yes", "Yes"), ("no", "No")]},
    {"fact": "ace", "kind": "yes", "label": "Ace", "desc": "Does anyone in the 5-stack kill all five in one round?", "prior": 0.2,
     "keys": [("yes", "Yes"), ("no", "No")]},
    {"fact": "comeback", "kind": "yes", "label": "Comeback", "desc": "The 5-stack falls 5+ rounds behind and still wins", "prior": 0.03,
     "keys": [("yes", "Yes"), ("no", "No")]},
    {"fact": "flawless", "kind": "ou", "label": "Flawless rounds", "desc": "Rounds the 5-stack wins without anyone dying", "floor_h": 0.7,
     "line_kind": "count"},
]
MOMENT_MIN_GAMES = 5  # games that decide a moment before it's offered


def _score_dist(r):
    """{(13, x) / (x, 13) for x < 12: chance, "ot-win" / "ot-loss": chance} for one round-win chance r."""
    dist = {}
    for x in range(12):
        c = math.comb(12 + x, x)
        dist[(13, x)] = c * r ** 13 * (1 - r) ** x
        dist[(x, 13)] = c * (1 - r) ** 13 * r ** x
    p_ot = math.comb(24, 12) * r ** 12 * (1 - r) ** 12
    w_ot = r * r / (r * r + (1 - r) ** 2)
    dist["ot-win"], dist["ot-loss"] = p_ot * w_ot, p_ot * (1 - w_ot)
    return dist


def _mixed_dist(mu, sd):
    dists = [_score_dist(clamp(mu + sd * z, 0.03, 0.97)) for z in SCORE_Z]
    return {k: sum(d[k] for d in dists) / len(dists) for k in dists[0]}


def _p_win(dist):
    return sum(p for k, p in dist.items() if k == "ot-win" or (isinstance(k, tuple) and k[0] == 13))


@lru_cache(maxsize=256)
def score_model(p_win, p_ot):
    """(dist, mu, sd): the final-score distribution whose chance of winning is p_win and of reaching 12-12 is as
    close to p_ot as the model allows. Cached: the inputs only change when a game is added or the map changes."""
    best = None
    for step in range(21):
        sd = SCORE_SD_MAX * step / 20
        lo, hi = 0.03, 0.97
        for _ in range(40):  # the squad's win chance rises with mu
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if _p_win(_mixed_dist(mid, sd)) < p_win else (lo, mid)
        dist = _mixed_dist((lo + hi) / 2, sd)
        gap = abs(dist["ot-win"] + dist["ot-loss"] - p_ot)
        if best is None or gap < best[0]:
            best = (gap, dist, (lo + hi) / 2, sd)
    return best[1], round(best[2], 4), round(best[3], 3)


def score_key(match):
    """The final score of a finished 13-round game as an exact-score key: "13-7", "9-13", or "ot-win" / "ot-loss"
    (only squad wins in regulation are offered, so the others just lose)."""
    if went_to_overtime(match):
        return "ot-win" if match.get("result") == "win" else "ot-loss"
    return f"{match.get('rounds_won') or 0}-{match.get('rounds_lost') or 0}"


def margin_key(match):
    diff = (match.get("rounds_won") or 0) - (match.get("rounds_lost") or 0)
    return next((key for key, _, lo, hi in MARGIN_BANDS if lo <= diff <= hi), None)



def fair_chance(decimal, market_type, edge):
    """The model's own chance behind a locked-in price, with the house edge taken back out. For bets placed before
    `fair_prob` was saved on them; approximate, because prices are rounded to 2 decimals."""
    return clamp(1.0 / decimal / market_overround(market_type, edge), 0.0, 1.0)


def weighted_quantile(pts, q):
    """pts: list of (weight, value)."""
    s = sorted(pts, key=lambda t: t[1])
    total = sum(w for w, _ in s)
    if total <= 0:
        return s[len(s) // 2][1]
    acc = 0.0
    for w, v in s:
        acc += w
        if acc >= q * total:
            return v
    return s[-1][1]


def weighted_moments(pts):
    tw = sum(w for w, _ in pts)
    mean = sum(w * v for w, v in pts) / tw
    var = sum(w * (v - mean) ** 2 for w, v in pts) / tw
    return mean, math.sqrt(max(var, 0.0))


def kde_over(pts, line, h):
    tw = sum(w for w, _ in pts)
    return sum(w * (1.0 - phi((line - v) / h)) for w, v in pts) / tw


def kde_quantile(pts, q, h, steps=24):
    """The value with share q of the smoothed distribution (see kde_over) below it, found by bisection."""
    tw = sum(w for w, _ in pts)
    lo, hi = min(v for _, v in pts) - 6 * h, max(v for _, v in pts) + 6 * h
    for _ in range(steps):
        mid = (lo + hi) / 2
        if sum(w * phi((mid - v) / h) for w, v in pts) / tw < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def eff_n(samples):
    tw = sum(w for w, _ in samples)
    sq = sum(w * w for w, _ in samples)
    return (tw * tw / sq) if sq > 0 else 0.0


def confidence(n_eff):
    if n_eff < 5:
        return "low"
    if n_eff < 15:
        return "medium"
    return "high"


class OddsEngine:
    def __init__(self, cfg):
        self.edge = float(cfg.get("house_edge", 0.05))
        self.half_life = max(1.0, float(cfg.get("recency_half_life_games", 15)))
        self.map_boost = float(cfg.get("map_weight_boost", 2.5))
        self.agent_boost = float(cfg.get("agent_weight_boost", 2.0))
        self.sims = int(cfg.get("simulations", 4000))

    # ---- weighting -------------------------------------------------------
    def _weights(self, rows, ctx_map=None, ctx_agent=None):
        ws = []
        for i, r in enumerate(rows):  # rows are newest first
            w = 0.5 ** (i / self.half_life)
            if ctx_map and (r.get("map") or "").lower() == ctx_map.lower():
                w *= self.map_boost
            if ctx_agent and (r.get("agent") or "").lower() == ctx_agent.lower():
                w *= self.agent_boost
            ws.append(w)
        return ws

    def samples(self, rows, full_rounds, ctx_map=None, ctx_agent=None, relative=True):
        """(weight, per-game metrics) for one member's rows (newest first), as the odds use them: recency-weighted,
        boosted for the chosen map and agent, with surrendered games scaled to a full game at reduced weight.
        relative adds acs_rel (ACS over the player's own average) for the "popped off" markets."""
        own_acs = aggregate(rows).get("acs") if relative else None
        out = []
        for w, r in zip(self._weights(rows, ctx_map, ctx_agent), rows):
            rounds = (r.get("rounds_won") or 0) + (r.get("rounds_lost") or 0)
            metrics = player_metrics(r, rounds)
            if relative:
                metrics["acs_rel"] = metrics["acs"] / own_acs if own_acs else None
            if ending(r) == FORFEIT:
                game_full = full_rounds * rounds_to_win(r.get("mode")) / DEFAULT_ROUNDS_TO_WIN
                metrics, share = partial_game(metrics, rounds, game_full)
                w *= share
            out.append((w, metrics))
        return out

    @staticmethod
    def stat_range(samples, key, coverage=0.8, floor_h=None):
        """(low, typical, high, expected) for one stat, from the same smoothed distribution as the over/under lines
        (bandwidth floor_h or 0.6 sigma). low / high are its percentiles around the middle `coverage` of games, so
        the range is lopsided when the stat is (a long tail of big games); typical is its median, where the betting
        line sits; expected is the weighted average. floor_h defaults to the stat's own (STAT_DEFS) and is needed for
        stats the odds don't price. None without data."""
        pts = [(w, x[key]) for w, x in samples if x.get(key) is not None]
        if not pts or sum(w for w, _ in pts) <= 0:
            return None
        mean, sigma = weighted_moments(pts)
        h = max(FLOOR_H[key] if floor_h is None else floor_h, 0.6 * sigma)
        tail = (1.0 - coverage) / 2
        low, typical, high = (kde_quantile(pts, q, h) for q in (tail, 0.5, 1.0 - tail))
        return max(0.0, low), max(0.0, typical), high, mean

    # ---- markets ---------------------------------------------------------
    def _choose_line(self, pts, median, h, kind):
        if kind == "score":
            base = round(median)
            cands = [base + 0.5 + k for k in range(-4, 5)]
        else:
            base = math.floor(median)
            cands = [base - 0.5, base + 0.5, base + 1.5]
        cands = [c for c in cands if c > 0] or [base + 0.5]
        return min(cands, key=lambda c: abs(kde_over(pts, c, h) - 0.5))

    def ou_market(self, member, samples, sd):
        pts = [(w, x[sd["key"]]) for w, x in samples if x.get(sd["key"]) is not None]
        if not pts:
            return None
        mean, sigma = weighted_moments(pts)
        h = max(sd["floor_h"], 0.6 * sigma)
        median = weighted_quantile(pts, 0.5)
        line = self._choose_line(pts, median, h, sd["kind"])
        p_over = clamp(kde_over(pts, line, h), 0.05, 0.95)
        overround = market_overround("ou", self.edge)
        return {
            "market_id": f"ou:{sd['key']}:{member['puuid']}",
            "type": "ou",
            "stat": sd["key"],
            "stat_label": sd["label"],
            "puuid": member["puuid"],
            "member": member["nickname"],
            "label": f"{member['nickname']} {sd['label']}",
            "line": line,
            "mean": round(mean, 1),
            "sigma": round(sigma, 1),
            "n_eff": round(eff_n(samples), 1),
            "selections": [
                selection("over", f"Over {line:g}", p_over, overround),
                selection("under", f"Under {line:g}", 1.0 - p_over, overround),
            ],
        }

    def alt_market(self, member, samples, sd, line):
        """A custom over/under line on a player prop. Always returns the reasonable range for the stat ("limits":
        the whole numbers you can pick for "at least N" (over N - 0.5) and "at most N" (under N + 0.5)). A side is
        only offered ("available") when its fair chance is between ALT_MIN_CHANCE and ALT_MAX_CHANCE; otherwise it
        carries a "reason". The market itself is unavailable, with a reason, when the line isn't a .5 line."""
        pts = [(w, x[sd["key"]]) for w, x in samples if x.get(sd["key"]) is not None]
        if not pts:
            return None
        mean, sigma = weighted_moments(pts)
        h = max(sd["floor_h"], 0.6 * sigma)
        q_min, q_safe, typical, q_unsafe, q_max = (kde_quantile(pts, q, h) for q in (
            ALT_MIN_CHANCE, 1.0 - ALT_MAX_CHANCE, 0.5, ALT_MAX_CHANCE, 1.0 - ALT_MIN_CHANCE))
        mk = {
            "market_id": alt_market_id(sd["key"], member["puuid"], line), "type": "ou", "custom": True,
            "stat": sd["key"], "stat_label": sd["label"], "puuid": member["puuid"], "member": member["nickname"],
            "label": f"{member['nickname']} {sd['label']}", "line": line, "mean": round(mean, 1),
            "typical": round(typical, 1),
            "limits": {"at_least": [max(0, math.ceil(q_safe + 0.5)), math.floor(q_max + 0.5)],
                       "at_most": [max(0, math.ceil(q_min - 0.5)), math.floor(q_unsafe - 0.5)]},
        }
        doubled = line * 2
        if not math.isfinite(line) or abs(doubled - round(doubled)) > 1e-9 or round(doubled) % 2 == 0:
            return {**mk, "available": False, "reason": "Custom lines are whole numbers: at least N or at most N."}
        p_over = kde_over(pts, line, h)
        overround = market_overround("ou", self.edge)
        sels = []
        for key, word, p in (("over", "Over", p_over), ("under", "Under", 1.0 - p_over)):
            sel = selection(key, f"{word} {line:g}", p, overround)
            sel["available"] = ALT_MIN_CHANCE <= p <= ALT_MAX_CHANCE
            if p < ALT_MIN_CHANCE:
                sel["reason"] = (f"That's too far from {member['nickname']}'s usual {sd['label'].lower()} (typically about "
                                 f"{typical:.0f}), so the odds would be silly.")
            elif p > ALT_MAX_CHANCE:
                sel["reason"] = f"That's nearly certain ({p:.0%}), so there'd be almost nothing to win."
            sels.append(sel)
        return {**mk, "available": any(s["available"] for s in sels), "selections": sels,
                "reason": None if any(s["available"] for s in sels) else sels[0].get("reason")}

    def exact_market(self, member, samples, sd, n):
        """An exact number on a counting stat ("Loog gets exactly 25 kills"): the share of the smoothed distribution
        between n - 0.5 and n + 0.5, priced with the multi-way edge. Offered ("available") when that chance is at
        least EXACT_MIN_CHANCE; "limits" has the numbers that qualify."""
        pts = [(w, x[sd["key"]]) for w, x in samples if x.get(sd["key"]) is not None]
        if not pts:
            return None
        mean, sigma = weighted_moments(pts)
        h = max(sd["floor_h"], 0.6 * sigma)
        chance = lambda k: kde_over(pts, k - 0.5, h) - kde_over(pts, k + 0.5, h)  # noqa: E731
        mk = {"market_id": exact_market_id(sd["key"], member["puuid"], n), "type": "exact", "custom": True,
              "stat": sd["key"], "stat_label": sd["label"], "puuid": member["puuid"], "member": member["nickname"],
              "label": f"{member['nickname']} {sd['label']}", "line": n, "mean": round(mean, 1),
              "typical": round(kde_quantile(pts, 0.5, h), 1)}
        if sd["kind"] != "count":
            return {**mk, "available": False, "limits": {"exactly": [1, 0]},
                    "reason": "Exact numbers are only for kills, deaths and assists."}
        top = int(max(v for _, v in pts) + 6 * h) + 1
        allowed = [k for k in range(0, top) if chance(k) >= EXACT_MIN_CHANCE]
        mk["limits"] = {"exactly": [min(allowed), max(allowed)] if allowed else [1, 0]}
        if not math.isfinite(n) or n < 0 or n != int(n):
            return {**mk, "available": False, "reason": "Exact numbers are whole numbers."}
        p = chance(int(n))
        sel = selection("exact", f"Exactly {n:g}", p, market_overround("exact", self.edge))
        sel["available"] = p >= EXACT_MIN_CHANCE
        if not sel["available"]:
            sel["reason"] = (f"Exactly {n:g} is too unlikely for {member['nickname']} (usually about {mk['typical']:.0f} "
                             f"{sd['label'].lower()}), so the odds would be silly.")
        return {**mk, "available": sel["available"], "selections": [sel], "reason": sel.get("reason")}

    def top_market(self, members, member_samples, td, rng):
        """The "tops the scoreboard" market for td and its counter ("bottoms"), from the same simulated games."""
        prep = []
        for m in members:
            pts = [(w, x[td["key"]]) for w, x in member_samples[m["puuid"]] if x.get(td["key"]) is not None]
            if not pts:
                continue
            cum, t = [], 0.0
            for w, _ in pts:
                t += w
                cum.append(t)
            _, sigma = weighted_moments(pts)
            h = max(FLOOR_H[td["key"]], 0.6 * sigma)
            prep.append((m, pts, cum, t, h))
        if len(prep) < 2:
            return None
        highs = {p[0]["puuid"]: 0 for p in prep}
        lows = dict(highs)
        for _ in range(self.sims):
            best = worst = None
            bestv, worstv = -math.inf, math.inf
            for m, pts, cum, t, h in prep:
                i = min(bisect_left(cum, rng.random() * t), len(pts) - 1)
                v = pts[i][1] + rng.gauss(0.0, h)
                if v > bestv:
                    bestv, best = v, m["puuid"]
                if v < worstv:
                    worstv, worst = v, m["puuid"]
            highs[best] += 1
            lows[worst] += 1
        k = len(prep)
        overround = market_overround("top", self.edge)

        def market(counts, direction, market_id, label, desc, counter_id):
            sels = []
            for m, *_ in prep:
                fair = clamp((counts[m["puuid"]] + 1.0) / (self.sims + k), 0.02, 0.9)
                sels.append(selection(m["puuid"], m["nickname"], fair, overround))
            sels.sort(key=lambda s: -s["prob"])
            return {
                "market_id": market_id,
                "type": "top",
                "direction": direction,  # "high": most of the stat wins; "low": least of it wins
                "pair": td["id"],
                "counter_id": counter_id,
                "stat": td["key"],
                "label": label,
                "desc": desc,
                "selections": sels,
            }
        return (market(highs, "high", td["id"], td["label"], td["desc"], td["low_id"]),
                market(lows, "low", td["low_id"], td["low_label"], td["low_desc"], td["id"]))

    def team_markets(self, matches, ctx_map, timelines=None):
        """Match result, overtime, margin and exact score (from the final score), then the moment markets read from
        the round timelines ({match_id: timeline}). Total rounds and rounds won / lost over-unders used to be offered
        too and are gone from the board; bets already placed on them still settle in bets._evaluate."""
        pts_win = []
        map_games = 0
        for i, m in enumerate(matches):
            w = 0.5 ** (i / self.half_life)
            if ctx_map and (m.get("map") or "").lower() == ctx_map.lower():
                w *= self.map_boost
                map_games += 1
            wv = 1.0 if m.get("result") == "win" else (0.5 if m.get("result") == "draw" else 0.0)
            pts_win.append((w, wv))  # a surrender is still a real result
        tw = sum(w for w, _ in pts_win)
        p_win = clamp((sum(w * v for w, v in pts_win) + 1.0) / (tw + 2.0), 0.1, 0.9)
        raw_wr = sum(1 for m in matches if m.get("result") == "win") / len(matches)
        two_way = market_overround("team_win", self.edge)
        markets = [{
            "market_id": "team:win",
            "type": "team_win",
            "label": "Match result",
            "desc": "Does the squad win?",
            # recent: the last five results, newest first, for the form guide under the match result.
            "basis": {"games": len(matches), "win_rate": round(raw_wr, 3), "map_games": map_games,
                      "recent": [m.get("result") for m in matches[:5]]},
            "selections": [
                selection("win", "Win", p_win, two_way),
                selection("loss", "Loss", 1.0 - p_win, two_way),
            ],
        }]
        # Overtime is rare, so the history is shrunk toward a ~10% base rate (0.2 of 2 pseudo-games).
        # Surrendered games are left out: they stopped before we know whether they'd have reached 12-12.
        pts_ot = [(0.5 ** (i / self.half_life), 1.0 if went_to_overtime(m) else 0.0)
                  for i, m in enumerate(matches) if ending(m) != FORFEIT]
        ot_games = sum(1 for m in matches if went_to_overtime(m))
        p_ot = clamp((sum(w * v for w, v in pts_ot) + 0.2) / (sum(w for w, _ in pts_ot) + 2.0), 0.03, 0.6)
        markets.append({
            "market_id": "team:ot",
            "type": "team_ot",
            "label": "Overtime",
            "desc": f"Past 12–12? {ot_games} of {len(matches)} games so far",
            "basis": {"games": len(matches), "hits": ot_games},
            "selections": [
                selection("yes", "Yes", p_ot, two_way),
                selection("no", "No", 1.0 - p_ot, two_way),
            ],
        })
        markets += self.score_markets(p_win, p_ot)
        markets += self.moment_markets(matches, timelines or {})
        return markets

    def score_markets(self, p_win, p_ot):
        """Winning margin and exact score, both from score_model(). (Rounds won / lost over-unders used to be offered
        too; they mostly repeated the match result, "rounds lost under 11.5" being a win, so the board dropped them.
        Bets already placed on them, types team_rw / team_rl, still settle in bets._evaluate.)"""
        dist, mu, sd = score_model(round(p_win, 4), round(p_ot, 4))
        multi = market_overround("team_score", self.edge)
        basis = {"round_win": mu, "spread": sd}
        out = []
        diff = {(k if isinstance(k, str) else f"{k[0]}-{k[1]}"): p for k, p in dist.items()}
        margin = {key: sum(p for k, p in dist.items() if k != "ot-win" and k != "ot-loss" and lo <= k[0] - k[1] <= hi)
                  + (dist["ot-win"] if key == "w1-2" else dist["ot-loss"] if key == "l1-2" else 0.0)
                  for key, _, lo, hi in MARGIN_BANDS}
        # Both are offered for every result, so each game has exactly one winning pick. Margin runs from a heavy loss to
        # a heavy win (overtime counts as 1-2 either way); exact score runs 0-13 .. 11-13, overtime (a loss, then a
        # win), 13-11 .. 13-0, the order the page draws them in, left to right.
        order = ["l6+", "l3-5", "l1-2", "w1-2", "w3-5", "w6+"]
        label = {key: lbl for key, lbl, _, _ in MARGIN_BANDS}
        out.append({"market_id": "team:margin", "type": "team_margin", "label": "Margin",
                    "desc": "How much the squad wins or loses by · OT counts as 1–2", "model": basis,
                    "selections": [selection(key, label[key], margin[key], multi) for key in order]})
        keys = [f"{x}-13" for x in range(12)] + ["ot-loss", "ot-win"] + [f"13-{x}" for x in range(11, -1, -1)]
        names = {"ot-loss": "Overtime loss", "ot-win": "Overtime win"}
        out.append({"market_id": "team:score", "type": "team_score", "label": "Exact score",
                    "desc": "The final score; an overtime game is its own pick (win or loss)", "model": basis,
                    "selections": [selection(k, names.get(k, k.replace("-", "–")), diff[k], multi) for k in keys]})
        return out

    def moment_markets(self, matches, timelines):
        """The team markets read from each game's round timeline (moments.game_facts), priced from the squad's recent
        games like the rest: recency-weighted, a yes/no pulled toward MOMENT_PRIOR (two pseudo-games at its base
        rate) and an over/under from the same smoothed distribution as total rounds. A market needs MOMENT_MIN_GAMES
        games that decide it, so it only appears once enough full records are stored. Each carries `basis` (games,
        hits or median) for the page's one-line facts."""
        two_way = market_overround("team_moment", self.edge)
        facts = []
        for i, m in enumerate(matches):  # newest first
            facts.append((0.5 ** (i / self.half_life), game_facts(m, timelines.get(m["match_id"]))))
        out = []
        for spec in MOMENT_DEFS:
            fact = spec["fact"]
            pts = [(w, f[fact]) for w, f in facts if f[fact] is not None]
            if len(pts) < MOMENT_MIN_GAMES:
                continue
            base = {"market_id": f"team:{fact}", "type": "team_moment", "fact": fact, "label": spec["label"], "desc": spec["desc"]}
            if spec["kind"] == "yes":
                tw = sum(w for w, _ in pts)
                p = clamp((sum(w * (1.0 if v else 0.0) for w, v in pts) + 2 * spec["prior"]) / (tw + 2), 0.02, 0.98)
                hits = sum(1 for _, v in pts if v)
                out.append({**base, "basis": {"games": len(pts), "hits": hits},
                            "selections": [selection(k, lbl, p if k == spec["keys"][0][0] else 1.0 - p, two_way) for k, lbl in spec["keys"]]})
            else:  # over / under
                _, sigma = weighted_moments(pts)
                h = max(spec["floor_h"], 0.6 * sigma)
                median = weighted_quantile(pts, 0.5)
                line = self._choose_line(pts, median, h, spec["line_kind"])
                p_over = clamp(kde_over(pts, line, h), 0.05, 0.95)
                unit = spec.get("unit", "")
                out.append({**base, "line": line, "basis": {"games": len(pts), "median": round(sorted(v for _, v in pts)[len(pts) // 2], 1),
                                                            "mean": round(sum(v for _, v in pts) / len(pts), 1)},
                            "selections": [selection("over", f"Over {line:g}{unit}", p_over, two_way),
                                           selection("under", f"Under {line:g}{unit}", 1.0 - p_over, two_way)]})
        return out

    # ---- board -----------------------------------------------------------
    def build(self, db, context=None, alts=None):
        """The whole odds board for the next game. alts: custom lines to price as well, [(stat, puuid, line)];
        they come back under "custom" (see alt_market)."""
        context = context or {}
        ctx_map = (context.get("map") or "").strip() or None
        ctx_agents = {k: v for k, v in (context.get("agents") or {}).items() if v}
        members = db.members()
        matches = db.matches()
        rows = db.player_rows()
        if not members or not matches:
            return {
                "ready": False,
                "message": "No squad games recorded yet. Odds appear after the first tracked game.",
                "generated_at": time.time(),
                "house_edge": self.edge,
            }
        by_member = defaultdict(list)
        for r in rows:
            by_member[r["puuid"]].append(r)
        full = full_game_rounds(matches)
        forfeits = sum(1 for m in matches if ending(m) == FORFEIT)

        pooled = []
        member_samples = {}
        for m in members:
            samples = self.samples(by_member.get(m["puuid"], []), full, ctx_map, ctx_agents.get(m["puuid"]))
            member_samples[m["puuid"]] = samples
            pooled.extend(samples)

        info = []
        for m in members:
            m["nickname"] = m.get("nickname") or m["name"]
            own = member_samples[m["puuid"]]
            n_eff = eff_n(own)
            borrowed = False
            if n_eff < 3 and pooled:
                # Thin history: borrow the team's pooled distribution at low weight.
                member_samples[m["puuid"]] = own + [(w * 0.25, x) for w, x in pooled]
                borrowed = True
            info.append({
                "puuid": m["puuid"],
                "name": m["name"],
                "tag": m["tag"],
                "nickname": m["nickname"],
                "games": len(own),
                "n_eff": round(n_eff, 1),
                "confidence": confidence(n_eff),
                "borrowed": borrowed,
                "context_agent": ctx_agents.get(m["puuid"]),
            })

        seed = f"{len(matches)}|{matches[0]['match_id']}|{ctx_map}|{sorted(ctx_agents.items())}"
        rng = random.Random(seed)

        props = []
        for m in members:
            for sd in (d for d in STAT_DEFS if d["key"] in PROP_KEYS):
                mk = self.ou_market(m, member_samples[m["puuid"]], sd)
                if mk:
                    props.append(mk)
        by_puuid = {m["puuid"]: m for m in members}
        custom = []
        for alt in alts or []:  # (stat, puuid, line) for a custom line, (stat, puuid, n, "exact") for an exact number
            st, pu, value, kind = (*alt, "line")[:4]
            if pu in by_puuid and st in PROP_KEYS:
                market = self.exact_market if kind == "exact" else self.alt_market
                custom.append(market(by_puuid[pu], member_samples[pu], STAT_BY_KEY[st], value))
        tops = []
        for td in TOP_DEFS:
            pair = self.top_market(members, member_samples, td, rng)
            if pair:
                tops.extend(pair)

        return {
            "ready": True,
            "generated_at": time.time(),
            "context": {"map": ctx_map, "agents": ctx_agents},
            "house_edge": self.edge,
            "games_considered": len(matches),
            "partial_games": {"forfeits": forfeits, "full_game_rounds": full},
            "latest_match": {
                "match_id": matches[0]["match_id"],
                "started_at": matches[0].get("started_at"),
                "map": matches[0].get("map"),
            },
            "members": info,
            "team": self.team_markets(matches, ctx_map, {t["match_id"]: t["data"] for t in db.timelines()}),
            "player_props": props,
            "top_markets": tops,
            "custom": [mk for mk in custom if mk],
            "stat_defs": [{"key": d["key"], "label": d["label"], "count": d["kind"] == "count"} for d in STAT_DEFS if d["key"] in PROP_KEYS],
        }


def alt_market_id(stat, puuid, line):
    return f"alt:{stat}:{puuid}:{line:g}"


def exact_market_id(stat, puuid, n):
    return f"exact:{stat}:{puuid}:{n:g}"


def parse_alt(market_id):
    """(stat, puuid, value, kind) from a custom-line ("alt:...", kind "line") or exact-number ("exact:...", kind
    "exact") market id, or None if it isn't one (or is malformed)."""
    parts = (market_id or "").split(":")
    if len(parts) != 4 or parts[0] not in ("alt", "exact") or parts[1] not in STAT_BY_KEY:
        return None
    try:
        value = float(parts[3])
    except ValueError:
        return None
    return (parts[1], parts[2], value, "exact" if parts[0] == "exact" else "line") if math.isfinite(value) else None


def alt_family(market_id):
    """Custom lines and exact numbers on a player and stat count as the same market as the board's line on it
    (for parlays)."""
    alt = parse_alt(market_id)
    return f"ou:{alt[0]}:{alt[1]}" if alt else market_id


def find_market(board, market_id, sel_key):
    for group in ("team", "player_props", "top_markets", "custom"):
        for mk in board.get(group, []):
            if mk["market_id"] == market_id and mk.get("available", True):
                for s in mk["selections"]:
                    if s["key"] == sel_key:
                        return (mk, s) if s.get("available", True) else (mk, None)
                return mk, None
    return None, None
