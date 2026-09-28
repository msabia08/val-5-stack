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
import random
import time
from bisect import bisect_left
from collections import defaultdict
from statistics import NormalDist

from .gamestate import DEFAULT_ROUNDS_TO_WIN, FORFEIT, ending, full_game_rounds, rounds_to_win, went_to_overtime
from .stats import aggregate, player_metrics

STAT_DEFS = [
    {"key": "kills", "label": "Kills", "kind": "count", "floor_h": 1.2},
    {"key": "deaths", "label": "Deaths", "kind": "count", "floor_h": 1.2},
    {"key": "assists", "label": "Assists", "kind": "count", "floor_h": 1.0},
    {"key": "acs", "label": "ACS", "kind": "score", "floor_h": 12.0},
    {"key": "adr", "label": "ADR", "kind": "score", "floor_h": 8.0},
    {"key": "hs_pct", "label": "Headshot %", "kind": "pct", "floor_h": 2.5},
]
FLOOR_H = {d["key"]: d["floor_h"] for d in STAT_DEFS}
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


def market_overround(market_type, edge):
    """How far a market's prices add up past 100%: "top"/"low" markets have one selection per player, so they
    carry double the house edge."""
    return 1.0 + edge * (2.0 if market_type == "top" else 1.0)


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
        """(low, expected, high) for one stat: the weighted average and the range that should hold `coverage` of
        games. It uses the same smoothing as the over/under lines (bandwidth floor_h or 0.6 sigma), treating the
        smoothed distribution as normal: its spread is sqrt(sigma^2 + h^2). floor_h defaults to the stat's own
        (STAT_DEFS) and is needed for stats the odds don't price. None without data."""
        pts = [(w, x[key]) for w, x in samples if x.get(key) is not None]
        if not pts or sum(w for w, _ in pts) <= 0:
            return None
        mean, sigma = weighted_moments(pts)
        h = max(FLOOR_H[key] if floor_h is None else floor_h, 0.6 * sigma)
        z = NormalDist().inv_cdf(0.5 + coverage / 2)
        half = z * math.sqrt(sigma * sigma + h * h)
        return max(0.0, mean - half), mean, mean + half

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

    def team_markets(self, matches, ctx_map):
        pts_win, pts_rounds = [], []
        map_games = 0
        for i, m in enumerate(matches):
            w = 0.5 ** (i / self.half_life)
            if ctx_map and (m.get("map") or "").lower() == ctx_map.lower():
                w *= self.map_boost
                map_games += 1
            wv = 1.0 if m.get("result") == "win" else (0.5 if m.get("result") == "draw" else 0.0)
            pts_win.append((w, wv))  # a surrender is still a real result
            if ending(m) != FORFEIT:  # a surrendered game's round total was cut short
                pts_rounds.append((w, (m.get("rounds_won") or 0) + (m.get("rounds_lost") or 0)))
        if not pts_rounds:
            pts_rounds = [(1.0, 22.0)]
        tw = sum(w for w, _ in pts_win)
        p_win = clamp((sum(w * v for w, v in pts_win) + 1.0) / (tw + 2.0), 0.1, 0.9)
        raw_wr = sum(1 for m in matches if m.get("result") == "win") / len(matches)
        two_way = market_overround("team_win", self.edge)
        markets = [{
            "market_id": "team:win",
            "type": "team_win",
            "label": "Match result",
            "desc": "Does the 5-stack win the next game?",
            "basis": {"games": len(matches), "win_rate": round(raw_wr, 3), "map_games": map_games},
            "selections": [
                selection("win", "5-stack wins", p_win, two_way),
                selection("loss", "5-stack loses", 1.0 - p_win, two_way),
            ],
        }]
        _, sigma = weighted_moments(pts_rounds)
        h = max(1.5, 0.6 * sigma)
        median = weighted_quantile(pts_rounds, 0.5)
        line = self._choose_line(pts_rounds, median, h, "count")
        p_over = clamp(kde_over(pts_rounds, line, h), 0.05, 0.95)
        markets.append({
            "market_id": "team:rounds",
            "type": "team_ou",
            "label": "Total rounds",
            "desc": "Rounds played in the next game (both teams combined)",
            "line": line,
            "mean": round(sum(w * v for w, v in pts_rounds) / sum(w for w, _ in pts_rounds), 1),
            "selections": [
                selection("over", f"Over {line:g}", p_over, two_way),
                selection("under", f"Under {line:g}", 1.0 - p_over, two_way),
            ],
        })
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
            "desc": f"Does the next game go past 12–12? ({ot_games} of {len(matches)} games so far)",
            "selections": [
                selection("yes", "Yes", p_ot, two_way),
                selection("no", "No", 1.0 - p_ot, two_way),
            ],
        })
        return markets

    # ---- board -----------------------------------------------------------
    def build(self, db, context=None):
        context = context or {}
        ctx_map = (context.get("map") or "").strip() or None
        ctx_agents = {k: v for k, v in (context.get("agents") or {}).items() if v}
        members = db.members()
        matches = db.matches()
        rows = db.player_rows()
        if not members or not matches:
            return {
                "ready": False,
                "message": "No 5-stack games recorded yet. Odds appear after the first tracked game.",
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
            for sd in STAT_DEFS:
                mk = self.ou_market(m, member_samples[m["puuid"]], sd)
                if mk:
                    props.append(mk)
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
            "team": self.team_markets(matches, ctx_map),
            "player_props": props,
            "top_markets": tops,
            "stat_defs": [{"key": d["key"], "label": d["label"]} for d in STAT_DEFS],
        }


def find_market(board, market_id, sel_key):
    for group in ("team", "player_props", "top_markets"):
        for mk in board.get(group, []):
            if mk["market_id"] == market_id:
                for s in mk["selections"]:
                    if s["key"] == sel_key:
                        return mk, s
                return mk, None
    return None, None
