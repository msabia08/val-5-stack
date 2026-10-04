"""The house: what it takes from bettors, and what it gives back.

The take. Every casino round (slots, blackjack, poker) writes one `house_ledger` row inside its own transaction
(`record()`): the credits staked against the house (0 for a poker pot, where the players bet against each other), the
house's `take` (stakes minus payouts for a house-banked game, so negative when a bettor wins big; the rake for poker)
and the `expected` take from the game's edge when it has one. Rows are unique by (game, ref), so a retried round never
counts twice. Match bets aren't in the ledger: BetManager.house() estimates their take from each bet's price.
Casino results stay out of match-betting profit: `casino_nets()` is each bettor's season net per game, which the
leaderboard reports as its own column. Casino play never earns or costs bananas.

What it gives back. The pot is the house's take, estimated from the edge every bet and round was priced at (steady and
never negative, unlike the actual take, which swings with luck; poker counts its rake), every season: JACKPOT_SHARE of it
builds the progressive jackpot and the rest is given back as secret objectives and bad-beat refunds, and the jackpot is
paid only by the daily wheel's jackpot slice. Every credit given back is a row in `house_payouts`; the pot is the take
minus the objectives and refunds paid (`POT_KINDS`), and the jackpot its share minus the jackpots paid. The daily
wheel's other credit prizes (kind `wheel`) and its insurance refunds (kind `insurance`) are recorded there too, but
they're free: neither comes out of the pot.

Secret objectives: a set of three is drawn for the next game, one of each scope, and stays hidden until that game is
recorded: a personal goal for one squad member (measured against their own earlier games, so the chance is about the
same for everyone, and the member is picked more often the lower their usual ACS), a squad goal (every member in the
game shares it) and a bettor goal (anyone with a bet settled on that game can meet it, and it's never about stake
size). Each prize is a share of the pot, more for a rarer goal, split evenly between the winners. A surrender carries
the set over to the next game.

Bad-beat refunds: a lost bet that only just missed (an over / under by the smallest step, a parlay of three or more legs
one leg short, a match result lost in overtime) gets REFUND_RATE of its stake back, at most REFUND_MAX, while the pot
lasts.
"""
import json
import math
import random
import time

from .gamestate import COMPLETE, ending, went_to_overtime
from .moments import game_facts
from .stats import player_metrics

JACKPOT_SHARE = 0.5  # of the house's take, into the progressive jackpot; the rest is the giveaway pot
POT_KINDS = ("objective", "refund")  # the payouts the pot pays for; the daily wheel's credits and insurance are free
GAME_SHARE = 0.10  # of the pot, offered across one game's objectives (a rarer goal gets more of it)
PRIZE_STEP = 5  # prizes are whole multiples of this
MIN_PRIZE = 5  # no objectives are drawn while the pot can't pay at least this per objective
MIN_GAMES = 5  # complete games a member needs before they get a personal goal (and squad goals need, with data)
REFUND_RATE = 0.5  # of a bad beat's stake, back from the pot
REFUND_MAX = 100  # credits per refund: a big stake doesn't drain the pot
HS_NEAR = 1.0  # a headshot % over / under that missed by this many points or fewer is a bad beat
BET_GOAL_MAX_STAKE = 25  # the "small bet" goal

# Personal goals: (stat, the text after the name; {n} is the threshold). Deaths are "or fewer", the rest "or more".
PLAYER_GOALS = {
    "kills": "{n}+ kills",
    "assists": "{n}+ assists",
    "acs": "{n}+ ACS",
    "adr": "{n}+ damage per round",
    "hs_pct": "{n}%+ headshots",
    "deaths": "{n} deaths or fewer",
}
GOAL_QUANTILES = (0.5, 0.65, 0.8)  # how hard a personal goal is: the share of their own games below the threshold
# Squad goals: text, and how to read it from a game (None when the game can't tell, e.g. no round data).
SQUAD_GOALS = {
    "win": "Win the game",
    "margin": "Win by 5 or more rounds",
    "pistol": "Win the first pistol round",
    "flawless": "Win 2 or more rounds without losing anyone",
    "ace": "Someone gets an ace",
    "comeback": "Come back from 5 rounds down to win",
}
# Bettor goals: text and a rough chance, which only sizes the prize ({nick} is a squad member).
BETTOR_GOALS = {
    "underdog": ("Win a bet the odds gave less than a 35% chance", 0.3),
    "small": (f"Win a bet of {BET_GOAL_MAX_STAKE} credits or less", 0.35),
    "on_player": ("Win a bet on {nick}", 0.35),
    "unlucky": ("Lose every bet you placed on this game", 0.3),
    "show_up": ("Have a bet on this game", 0.7),
}
UNDERDOG = 0.35

_rng = random.SystemRandom()


def _step(v):
    return int(v / PRIZE_STEP + 0.5) * PRIZE_STEP


def _quantile(values, q):
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(q * len(s))))]


def _player_value(stat, metrics):
    v = metrics.get(stat)
    return None if v is None else float(v)

# Each casino game's season net per bettor (payouts minus stakes, current season only). A new game adds its query.
CASINO_NET_SQL = {
    "slots": "SELECT bettor, SUM(payout - stake) AS net FROM slot_spins WHERE season_id IS NULL GROUP BY bettor",
    "stampede": "SELECT bettor, SUM(payout - stake) AS net FROM stampede_spins WHERE season_id IS NULL GROUP BY bettor",
    "blackjack": "SELECT bettor, SUM(payout - stake - COALESCE(tip, 0)) AS net FROM blackjack_hands "
                 "WHERE season_id IS NULL AND status='settled' GROUP BY bettor",
    "poker": "SELECT bettor, SUM(net) AS net FROM poker_results WHERE season_id IS NULL GROUP BY bettor",
}


def record(conn, game, ref, bettor, staked, take, expected=None, now=None):
    """Write one round's house row on `conn` without committing: call it inside the transaction that settles the
    round, under `db.lock`. A ref already recorded for the game is ignored."""
    conn.execute(
        "INSERT OR IGNORE INTO house_ledger(game, ref, bettor, staked, take, expected, created_ts) VALUES(?,?,?,?,?,?,?)",
        (game, str(ref), bettor, round(staked, 2), round(take, 2),
         None if expected is None else round(expected, 4), now or time.time()))


def casino_nets(db):
    """{lower-cased bettor: {"total": net, <game>: net}} for the current season, every casino game."""
    out = {}
    for game, sql in CASINO_NET_SQL.items():
        for r in db.query(sql):
            row = out.setdefault(r["bettor"].lower(), {"total": 0.0})
            row[game] = round(r["net"] or 0.0, 2)
            row["total"] = round(row["total"] + (r["net"] or 0.0), 2)
    return out


class HouseManager:
    def __init__(self, db, bets=None, rewards=None):
        """`bets` (BetManager) and `rewards` (RewardManager) are only needed for the giving back; the ledger works
        without them."""
        self.db, self.bets, self.rewards = db, bets, rewards
        self.backfill()

    # ---- the take ----------------------------------------------------------------------------------------------------
    def backfill(self):
        """Ledger rows for slot spins from before the ledger existed (safe to rerun). Spins from before slots
        recorded their return have no expected take."""
        with self.db.lock:
            self.db.conn.execute(
                "INSERT OR IGNORE INTO house_ledger(game, ref, bettor, staked, take, expected, created_ts, season_id) "
                "SELECT 'slots', 'spin:' || id, bettor, stake, stake - payout, "
                "CASE WHEN rtp IS NULL THEN NULL ELSE ROUND(stake * (1 - rtp), 4) END, created_ts, season_id "
                "FROM slot_spins")
            self.db.conn.commit()

    def summary(self):
        """The house's take per game: this season and all time (rounds, staked, take, expected)."""
        rows = self.db.query(
            "SELECT game, season_id IS NULL AS current, COUNT(*) AS rounds, COALESCE(SUM(staked),0) AS staked, "
            "COALESCE(SUM(take),0) AS take, COALESCE(SUM(expected),0) AS expected "
            "FROM house_ledger GROUP BY game, season_id IS NULL")
        games = {}
        for r in rows:
            g = games.setdefault(r["game"], {"game": r["game"],
                                             "season": {"rounds": 0, "staked": 0.0, "take": 0.0, "expected": 0.0},
                                             "all_time": {"rounds": 0, "staked": 0.0, "take": 0.0, "expected": 0.0}})
            for scope in (("season", "all_time") if r["current"] else ("all_time",)):
                for k in ("rounds", "staked", "take", "expected"):
                    g[scope][k] = round(g[scope][k] + r[k], 2)
        out = sorted(games.values(), key=lambda g: g["game"])
        return {"games": out,
                "season_take": round(sum(g["season"]["take"] for g in out), 2),
                "all_time_take": round(sum(g["all_time"]["take"] for g in out), 2)}

    # ---- the pot -----------------------------------------------------------------------------------------------------
    def paid(self):
        r = self.db.query_one(
            "SELECT COALESCE(SUM(CASE WHEN kind='jackpot' THEN amount END), 0) AS jackpot, "
            f"COALESCE(SUM(CASE WHEN kind IN {POT_KINDS!r} THEN amount END), 0) AS pot FROM house_payouts")
        return r["pot"], r["jackpot"]

    def casino_take(self):
        """The casino's take from the ledger, every season: rounds, staked, the actual take, and the expected take (each
        round's edge where its game has one, poker's rake, nothing for slot spins from before they recorded their
        return)."""
        r = self.db.query_one(
            "SELECT COUNT(*) AS rounds, COALESCE(SUM(staked), 0) AS staked, COALESCE(SUM(take), 0) AS take, "
            "COALESCE(SUM(CASE WHEN expected IS NOT NULL THEN expected WHEN game='poker' THEN take ELSE 0 END), 0) "
            "AS expected FROM house_ledger")
        return {"rounds": r["rounds"], "staked": round(r["staked"], 2), "actual_take": round(r["take"], 2),
                "expected_take": round(r["expected"], 2)}

    def report(self):
        """Everything /api/house serves: the ledger's take per game (summary()), the take from bets and the casino
        (estimated and actual, every season), the pot and jackpot it funds, what has been given back, the next game's
        objectives (how many and their prizes, never what they are) and the latest giveaways."""
        bets, casino = self.bets.house(), self.casino_take()
        expected = bets["expected_take"] + casino["expected_take"]
        pot_paid, jackpot_paid = self.paid()
        rewards = self.db.query_one(
            "SELECT COALESCE(SUM(base + bonus), 0) AS paid FROM (SELECT base, bonus FROM rewards "
            "UNION ALL SELECT base, bonus FROM archived_rewards)")["paid"]
        upcoming = self.db.query("SELECT prize FROM house_objectives WHERE status='open'")
        by_kind = {r["kind"]: r["total"] for r in self.db.query(
            "SELECT kind, SUM(amount) AS total FROM house_payouts GROUP BY kind")}
        return {
            **self.summary(),
            "bets": bets, "casino": casino, "rewards_paid": round(rewards, 2),
            "expected_take": round(expected, 2), "actual_take": round(bets["actual_take"] + casino["actual_take"], 2),
            "pot": round(expected * (1 - JACKPOT_SHARE) - pot_paid, 2),
            "jackpot": round(expected * JACKPOT_SHARE - jackpot_paid, 2),
            "given": {k: round(v, 2) for k, v in by_kind.items()},
            "next": {"objectives": len(upcoming), "prizes": [r["prize"] for r in upcoming]},
            "last": self.objectives(self._last_settled_match()),
            "recent": self.payouts(limit=8),
            "rules": {"jackpot_share": JACKPOT_SHARE, "game_share": GAME_SHARE, "refund_rate": REFUND_RATE,
                      "refund_max": REFUND_MAX},
        }

    def summary_pots(self):
        """(pot, jackpot) right now."""
        take = self.bets.house()["expected_take"] + self.casino_take()["expected_take"]
        pot_paid, jackpot_paid = self.paid()
        return take * (1 - JACKPOT_SHARE) - pot_paid, take * JACKPOT_SHARE - jackpot_paid

    def pot(self):
        return self.summary_pots()[0]

    def pay(self, bettor, amount, kind, ref, match_id, note):
        """Give a bettor credits from the house, once per ref: from the jackpot when kind is `jackpot`, from the pot for
        `POT_KINDS`, and free for anything else (the daily wheel's credits)."""
        return self._pay(bettor, amount, kind, ref, match_id, note)

    def _pay(self, bettor, amount, kind, ref, match_id, note):
        """Credit a giveaway once (the ref is unique). Returns the amount paid, or 0 if it was paid before."""
        amount = round(amount, 2)
        if amount <= 0:
            return 0.0
        with self.db.lock:
            cur = self.db.conn.execute(
                "INSERT OR IGNORE INTO house_payouts(bettor, amount, kind, ref, match_id, note, created_ts) "
                "VALUES(?,?,?,?,?,?,?)", (bettor, amount, kind, ref, match_id, note, time.time()))
            if cur.rowcount:
                self.db.conn.execute("UPDATE bettors SET balance = balance + ? WHERE lower(name)=lower(?)", (amount, bettor))
            self.db.conn.commit()
        return amount if cur.rowcount else 0.0

    def payouts(self, bettor=None, limit=20):
        sql, args = "SELECT * FROM house_payouts", []
        if bettor:
            sql, args = sql + " WHERE lower(bettor)=lower(?)", [bettor]
        return self.db.query(sql + " ORDER BY id DESC LIMIT ?", (*args, int(limit)))

    def totals(self):
        """This season's giveaways per bettor, keyed by lower-cased name (the leaderboard keeps them out of profit)."""
        return {r["k"]: r["total"] for r in self.db.query(
            "SELECT lower(bettor) AS k, SUM(amount) AS total FROM house_payouts WHERE season_id IS NULL GROUP BY k")}

    # ---- after each game ---------------------------------------------------------------------------------------------
    def after_match(self, match, players, settled):
        """Bad-beat refunds for the bets this game just settled, then the open objectives (if this game is the one
        they were drawn for), then a new set for the next game. Returns the giveaways paid."""
        paid = self.refunds(match, settled)
        if ending(match) == COMPLETE:
            paid += self.settle_objectives(match, players)
        self.ensure_objectives()
        return paid

    def refunds(self, match, settled):
        """Bad beats, and the daily wheel's insurance tokens: a lost single that carried one gets its stake back up to
        the token's limit (free, like the wheel's other credits: not from the pot), instead of a bad-beat refund."""
        out = []
        for b in settled:
            if b["status"] != "lost":
                continue
            insured = json.loads(b.get("context") or "{}").get("insured")
            if insured:
                amount = self._pay(b["bettor"], min(b["stake"], insured.get("max") or b["stake"]), "insurance",
                                   f"insurance:{b['id']}", match["match_id"], "Insurance token: stake back on a lost bet")
                if amount:
                    note = f"Insured: {amount:g} credits back from the house"
                    self.db.update_bet(b["id"], note=f"{b['note']}; {note}" if b.get("note") else note)
                    out.append({"bettor": b["bettor"], "amount": amount, "kind": "insurance", "bet_id": b["id"], "why": "insured"})
                continue
            why = self.bad_beat(b, match)
            if not why:
                continue
            due = min(REFUND_MAX, b["stake"] * REFUND_RATE, max(0.0, self.pot()))
            amount = self._pay(b["bettor"], due, "refund", f"refund:{b['id']}", match["match_id"],
                               f"Bad beat: {why}")
            if amount:
                note = f"Bad beat ({why}): {amount:g} credits back from the house"
                self.db.update_bet(b["id"], note=f"{b['note']}; {note}" if b.get("note") else note)
                out.append({"bettor": b["bettor"], "amount": amount, "kind": "refund", "bet_id": b["id"], "why": why})
        return out

    @staticmethod
    def bad_beat(b, match):
        """Why a lost bet counts as a bad beat, or None."""
        if b["market_type"] == "parlay":
            legs = json.loads(b.get("context") or "{}").get("legs") or []
            results = [leg.get("result") for leg in legs]
            if len(legs) >= 3 and results.count("lost") == 1 and results.count("won") >= 2:
                return f"one leg short of a {len(legs)}-leg parlay"
            return None
        if b["market_type"] == "ou" and b.get("actual_value") is not None and b.get("line") is not None:
            stat = json.loads(b.get("context") or "{}").get("stat")
            miss = abs(b["actual_value"] - b["line"])
            if stat == "hs_pct":
                return f"missed by {miss:.1f} points" if miss <= HS_NEAR else None
            return "missed by one" if miss <= 0.5 + 1e-9 else None
        if b["market_type"] == "team_win" and went_to_overtime(match):
            return "lost in overtime"
        return None

    # ---- secret objectives -------------------------------------------------------------------------------------------
    def _history(self, before_ts):
        """Complete games before `before_ts`, oldest first, and each member's metrics in them."""
        games = [m for m in self.db.matches() if (m.get("started_ts") or 0) < before_ts and ending(m) == COMPLETE]
        games.reverse()
        rows = {}
        ids = {g["match_id"] for g in games}
        for r in self.db.player_rows():
            if r["match_id"] in ids:
                rounds = (r.get("rounds_won") or 0) + (r.get("rounds_lost") or 0)
                rows.setdefault(r["puuid"], []).append(player_metrics(r, rounds))
        return games, rows

    def ensure_objectives(self, for_match=None):
        """Draw the next game's secret objectives when none are open and the pot can pay for them. `for_match` (the
        demo's seeding only) draws them for that recorded game instead, from the games before it."""
        if self.db.query_one("SELECT 1 AS x FROM house_objectives WHERE status='open'"):
            return []
        if for_match:
            earlier = [m for m in self.db.matches() if (m.get("started_ts") or 0) < (for_match.get("started_ts") or 0)]
            after, drawn_ts = (earlier[0] if earlier else for_match), (for_match.get("started_ts") or 0) - 1
        else:
            latest = self.db.matches(1)
            if not latest:
                return []
            after = latest[0]
            drawn_ts = (after.get("started_ts") or 0) + 1  # for the first game after the latest one recorded
        base = self.pot() * GAME_SHARE / 3
        if base < MIN_PRIZE:
            return []
        games, rows = self._history(drawn_ts)
        picks = [g for g in (self._player_goal(rows), self._squad_goal(games), self._bettor_goal(rows)) if g]
        now = time.time()
        for g in picks:
            prize = max(MIN_PRIZE, _step(base * min(2.5, max(0.5, 0.5 / max(g["chance"], 0.01)))))
            self.db.execute(
                "INSERT INTO house_objectives(scope, kind, target, params, text, chance, prize, drawn_ts, after_match, "
                "status, created_ts) VALUES(?,?,?,?,?,?,?,?,?,'open',?)",
                (g["scope"], g["kind"], g.get("target"), json.dumps(g.get("params") or {}), g["text"],
                 round(g["chance"], 4), prize, drawn_ts, after["match_id"], now))
        return picks

    def _nick(self, puuid):
        m = next((m for m in self.db.members() if m["puuid"] == puuid), None)
        return (m.get("nickname") or m["name"]) if m else "?"

    def _player_goal(self, rows):
        """One member's personal goal. Members with lower usual ACS are picked more often (the lowest twice as often
        as the highest); the threshold comes from their own games, so it's as reachable for them as for anyone."""
        members = [m for m in self.db.members() if len(rows.get(m["puuid"], [])) >= MIN_GAMES]
        if not members:
            return None
        avg = {m["puuid"]: sum(x["acs"] for x in rows[m["puuid"]]) / len(rows[m["puuid"]]) for m in members}
        ranked = sorted(members, key=lambda m: -avg[m["puuid"]])  # best first
        weights = [1 + i / max(1, len(ranked) - 1) for i in range(len(ranked))]
        member = _rng.choices(ranked, weights)[0]
        hist = rows[member["puuid"]]
        stat = _rng.choice(list(PLAYER_GOALS))
        values = [v for v in (_player_value(stat, x) for x in hist) if v is not None]
        if len(values) < MIN_GAMES:
            return None
        q = _rng.choice(GOAL_QUANTILES)
        if stat == "deaths":
            n = math.floor(_quantile(values, 1 - q))
            hits = sum(v <= n for v in values)
        else:
            n = math.floor(_quantile(values, q)) + 1  # whole numbers: "21+ kills"
            hits = sum(v >= n for v in values)
        return {"scope": "player", "kind": stat, "target": member["puuid"], "params": {"n": n},
                "text": f"{member.get('nickname') or member['name']}: {PLAYER_GOALS[stat].format(n=n)}",
                "chance": (hits + 1) / (len(values) + 2)}

    def _squad_goal(self, games):
        """A team goal whose chance from earlier games is between 5% and 80% (the ones that need round data only once
        enough games have it)."""
        options = []
        facts = [(g, game_facts(g, self.db.timeline(g["match_id"]))) for g in games]
        for kind, text in SQUAD_GOALS.items():
            seen = [v for v in (self._squad_value(kind, g, f) for g, f in facts) if v is not None]
            if len(seen) < MIN_GAMES:
                continue
            chance = (sum(seen) + 1) / (len(seen) + 2)
            if 0.05 <= chance <= 0.8:
                options.append({"scope": "squad", "kind": kind, "text": text, "chance": chance})
        return _rng.choice(options) if options else None

    @staticmethod
    def _squad_value(kind, match, facts):
        if kind == "win":
            return match.get("result") == "win"
        if kind == "margin":
            return (match.get("rounds_won") or 0) - (match.get("rounds_lost") or 0) >= 5
        v = facts.get(kind)
        if v is None:
            return None
        return v >= 2 if kind == "flawless" else bool(v)

    def _bettor_goal(self, rows):
        kind = _rng.choice(list(BETTOR_GOALS))
        text, chance = BETTOR_GOALS[kind]
        target = None
        if kind == "on_player":
            members = self.db.members()
            if not members:
                return None
            target = _rng.choice(members)["puuid"]
            text = text.format(nick=self._nick(target))
        return {"scope": "bettor", "kind": kind, "target": target, "text": text, "chance": chance}

    def settle_objectives(self, match, players):
        """Reveal the open objectives on the first complete game after they were drawn and pay their winners."""
        open_ = self.db.query("SELECT * FROM house_objectives WHERE status='open' AND drawn_ts < ?",
                              (match.get("started_ts") or 0,))
        if not open_:
            return []
        rounds = (match.get("rounds_won") or 0) + (match.get("rounds_lost") or 0)
        metrics = {p["puuid"]: player_metrics(p, rounds) for p in players}
        facts = game_facts(match, self.db.timeline(match["match_id"]))
        bets = [b for b in self.db.bets() if b.get("settled_match_id") == match["match_id"] and b["status"] in ("won", "lost")]
        members = {m["puuid"]: m for m in self.db.pool()}
        paid = []
        for o in open_:
            winners = self._winners(o, match, metrics, facts, bets, members)
            status = "void" if winners is None else "met" if winners else "missed"
            names = sorted(set(winners or []), key=str.lower)
            amounts = {}
            if names:
                share = min(o["prize"], max(0.0, self.pot())) / len(names)
                for name in names:
                    amounts[name] = self._pay(name, share, "objective", f"objective:{o['id']}:{name.lower()}",
                                              match["match_id"], o["text"])
                    if amounts[name]:
                        paid.append({"bettor": name, "amount": amounts[name], "kind": "objective", "text": o["text"]})
            self.db.execute("UPDATE house_objectives SET status=?, match_id=?, settled_ts=?, winners=? WHERE id=?",
                            (status, match["match_id"], time.time(), json.dumps(amounts), o["id"]))
        return paid

    def _winners(self, o, match, metrics, facts, bets, members):
        """The bettor accounts that met an objective in this game, [] if nobody did, None if the game can't tell."""
        params = json.loads(o["params"] or "{}")
        if o["scope"] == "player":
            m, member = metrics.get(o["target"]), members.get(o["target"])
            if not m or not member:
                return None  # they didn't play this one
            v = _player_value(o["kind"], m)
            if v is None:
                return None
            met = v <= params["n"] if o["kind"] == "deaths" else v >= params["n"]
            return [self.rewards.bettor_for(member)] if met else []
        if o["scope"] == "squad":
            met = self._squad_value(o["kind"], match, facts)
            if met is None:
                return None
            return [self.rewards.bettor_for(members[p]) for p in metrics if p in members] if met else []
        by = {}
        for b in bets:
            by.setdefault(b["bettor"], []).append(b)
        if o["kind"] == "unlucky":
            return [n for n, bs in by.items() if all(b["status"] == "lost" for b in bs)]
        if o["kind"] == "show_up":
            return list(by)
        return [n for n, bs in by.items() if any(b["status"] == "won" and self._counts(o, b) for b in bs)]

    @staticmethod
    def _counts(o, b):
        """Whether a won bet meets a bettor objective."""
        ctx = json.loads(b.get("context") or "{}")
        if o["kind"] == "small":
            return b["stake"] <= BET_GOAL_MAX_STAKE
        if o["kind"] == "underdog":
            if b["market_type"] == "parlay":
                chance = 1.0
                for leg in ctx.get("legs") or []:
                    if leg.get("result") == "won":
                        chance *= leg.get("fair_prob") or 1.0
                return chance < UNDERDOG
            return (ctx.get("fair_prob") or 1 / b["odds_decimal"]) < UNDERDOG
        if o["kind"] == "on_player":
            legs = ctx.get("legs") if b["market_type"] == "parlay" else [{"meta": ctx}]
            return any((leg.get("meta") or {}).get("puuid") == o["target"] for leg in legs or [])
        return False

    # ---- reading -----------------------------------------------------------------------------------------------------
    def _last_settled_match(self):
        r = self.db.query_one("SELECT match_id FROM house_objectives WHERE status!='open' ORDER BY settled_ts DESC LIMIT 1")
        return r["match_id"] if r else None

    def objectives(self, match_id):
        """A game's revealed objectives (met, missed or void) with what each winner got."""
        if not match_id:
            return []
        rows = self.db.query("SELECT * FROM house_objectives WHERE match_id=? AND status!='open' ORDER BY id", (match_id,))
        return [{"scope": r["scope"], "text": r["text"], "prize": r["prize"], "chance": r["chance"], "status": r["status"],
                 "winners": json.loads(r["winners"] or "{}")} for r in rows]

    def for_match(self, match_id):
        """What the house gave back on one game: the objectives it revealed and the bad beats it refunded."""
        return {"objectives": self.objectives(match_id),
                "refunds": self.db.query("SELECT bettor, amount, note FROM house_payouts WHERE kind='refund' AND match_id=? "
                                         "ORDER BY amount DESC", (match_id,))}
