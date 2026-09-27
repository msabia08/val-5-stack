"""Virtual-credit betting ledger: placement, cancellation, settlement, leaderboard."""
import json
import time

from odds import find_market
from stats import player_metrics


class BetError(Exception):
    pass


EMPTY_STATS = {
    "won": 0, "lost": 0, "void": 0, "cancelled": 0, "pending": 0,
    "pending_stake": 0.0, "staked": 0.0, "returned": 0.0,
}


class BetManager:
    def __init__(self, cfg, db, engine):
        self.db = db
        self.engine = engine
        self.starting = float(cfg.get("starting_balance", 1000))

    # ---- bettors ---------------------------------------------------------
    def ensure_bettor(self, name):
        name = (name or "").strip()
        if not name or len(name) > 32:
            raise BetError("Bettor name must be 1-32 characters.")
        b = self.db.get_bettor(name)
        if not b:
            self.db.create_bettor(name, self.starting)
            b = self.db.get_bettor(name)
        return b

    def reset(self):
        self.db.reset_betting(self.starting)

    def leaderboard(self):
        per = {}
        for b in self.db.bets():
            s = per.setdefault(b["bettor"].lower(), dict(EMPTY_STATS))
            st = b["status"]
            if st == "pending":
                s["pending"] += 1
                s["pending_stake"] += b["stake"]
            elif st == "cancelled":
                s["cancelled"] += 1
            else:
                s["staked"] += b["stake"]
                s["returned"] += b["payout"] or 0.0
                s[st] = s.get(st, 0) + 1
        out = []
        for b in self.db.bettors():
            s = per.get(b["name"].lower(), dict(EMPTY_STATS))
            profit = b["balance"] + s["pending_stake"] - self.starting
            row = {
                "name": b["name"],
                "balance": round(b["balance"], 2),
                "profit": round(profit, 2),
                "roi": round((s["returned"] - s["staked"]) / s["staked"], 3) if s["staked"] else None,
            }
            for k, v in s.items():
                row[k] = round(v, 2) if isinstance(v, float) else v
            out.append(row)
        out.sort(key=lambda x: -x["balance"])
        return out

    # ---- placement -------------------------------------------------------
    def place(self, bettor_name, market_id, sel_key, stake, context):
        try:
            stake = round(float(stake), 2)
        except (TypeError, ValueError):
            raise BetError("Invalid stake.")
        if stake < 1:
            raise BetError("Minimum stake is 1 credit.")
        bettor = self.ensure_bettor(bettor_name)
        if stake > bettor["balance"] + 1e-9:
            raise BetError(f"{bettor['name']} only has {bettor['balance']:.0f} credits.")
        board = self.engine.build(self.db, context or {})
        if not board.get("ready"):
            raise BetError(board.get("message", "Odds are not available yet."))
        market, sel = find_market(board, market_id, sel_key)
        if not market or not sel:
            raise BetError("That market is no longer available. Refresh the odds board.")

        mtype = market["type"]
        if mtype == "ou":
            desc = f"{market['member']} {market['stat_label']} {sel['label']}"
            meta = {"stat": market["stat"], "puuid": market["puuid"]}
        elif mtype == "top":
            desc = f"{market['label']}: {sel['label']}"
            meta = {"stat": market["stat"]}
        elif mtype == "team_win":
            desc = sel["label"]
            meta = {}
        else:
            desc = f"Total rounds {sel['label']}"
            meta = {}
        meta["ctx"] = board.get("context")

        bet = {
            "bettor": bettor["name"],
            "market_id": market_id,
            "market_type": mtype,
            "description": desc,
            "selection": sel_key,
            "selection_label": sel["label"],
            "line": market.get("line"),
            "odds_decimal": sel["decimal"],
            "stake": stake,
            "placed_ts": time.time(),
            "context": json.dumps(meta),
            "status": "pending",
        }
        with self.db.lock:
            self.db.adjust_balance(bettor["name"], -stake)
            bet_id = self.db.insert_bet(bet)
        return self.db.bet(bet_id)

    def cancel(self, bet_id):
        bet = self.db.bet(bet_id)
        if not bet:
            raise BetError("Bet not found.")
        if bet["status"] != "pending":
            raise BetError("Only pending bets can be cancelled.")
        with self.db.lock:
            self.db.update_bet(
                bet_id, status="cancelled", settled_ts=time.time(), payout=bet["stake"],
                note="Cancelled before the game",
            )
            self.db.adjust_balance(bet["bettor"], bet["stake"])
        return self.db.bet(bet_id)

    # ---- settlement ------------------------------------------------------
    def settle_for_match(self, match, players):
        """Settle every pending bet that was placed before this match started."""
        started = match.get("started_ts") or 0
        pending = [b for b in self.db.pending_bets() if b["placed_ts"] < started]
        if not pending:
            return []
        rounds = (match.get("rounds_won") or 0) + (match.get("rounds_lost") or 0)
        metrics = {p["puuid"]: player_metrics(p, rounds) for p in players}
        settled = []
        for b in pending:
            status, actual, note = self._evaluate(b, match, metrics)
            if status == "won":
                payout = round(b["stake"] * b["odds_decimal"], 2)
            elif status == "void":
                payout = b["stake"]
            else:
                payout = 0.0
            with self.db.lock:
                self.db.update_bet(
                    b["id"], status=status, settled_match_id=match["match_id"],
                    settled_ts=time.time(), payout=payout, actual_value=actual, note=note,
                )
                if payout:
                    self.db.adjust_balance(b["bettor"], payout)
            settled.append(self.db.bet(b["id"]))
        return settled

    def _evaluate(self, b, match, metrics):
        meta = json.loads(b.get("context") or "{}")
        t = b["market_type"]
        sel = b["selection"]
        line = b.get("line")
        if t == "ou":
            m = metrics.get(meta.get("puuid"))
            if not m:
                return "void", None, "Player was not in this game"
            v = m.get(meta.get("stat"))
            if v is None:
                return "void", None, "Stat unavailable for this game"
            if abs(v - line) < 1e-9:
                return "void", v, "Push: landed exactly on the line"
            won = v > line if sel == "over" else v < line
            return ("won" if won else "lost"), round(v, 2), None
        if t == "top":
            stat = meta.get("stat")
            vals = {p: m.get(stat) for p, m in metrics.items() if m.get(stat) is not None}
            if len(vals) < 2:
                return "void", None, "Not enough data to settle"
            best = max(vals.values())
            winners = [p for p, v in vals.items() if abs(v - best) < 1e-9]
            actual = vals.get(sel)
            actual = round(actual, 2) if actual is not None else None
            if len(winners) > 1:
                return "void", actual, "Tie at the top: stakes refunded"
            return ("won" if winners[0] == sel else "lost"), actual, None
        if t == "team_win":
            res = match.get("result")
            diff = (match.get("rounds_won") or 0) - (match.get("rounds_lost") or 0)
            if res == "draw":
                return "void", diff, "Game was a draw"
            won = (res == "win") == (sel == "win")
            return ("won" if won else "lost"), diff, None
        if t == "team_ou":
            total = (match.get("rounds_won") or 0) + (match.get("rounds_lost") or 0)
            if abs(total - line) < 1e-9:
                return "void", total, "Push: landed exactly on the line"
            won = total > line if sel == "over" else total < line
            return ("won" if won else "lost"), total, None
        return "void", None, "Unknown market type"
