"""Virtual-credit betting ledger: bettor accounts, placement, cancellation, settlement, leaderboard."""
import hashlib
import hmac
import json
import secrets
import time

from .gamestate import DEFAULT_ROUNDS_TO_WIN, FORFEIT, ending, rounds_to_win, went_to_overtime
from .odds import STAT_DEFS, alt_family, find_market, margin_key, parse_alt, score_key
from .parlay import LOOKBACK, correlation, price, score_conflict
from .stats import aggregate, player_metrics

# Stats that only ever go up during a game: an over that cleared the line before a surrender is already won.
COUNTING_STATS = {d["key"] for d in STAT_DEFS if d["kind"] == "count"}
SCORE_MARKETS = {"team_score", "team_margin", "team_rw", "team_rl"}  # priced from odds.score_model
# Note on a bet voided by a surrender. A void single is refunded; a void parlay leg is dropped from the parlay.
EARLY_END = "Game ended early (surrender) before this was decided"
# A pick on the board that would have won in each of the last STREAK_MIN games (at today's line) gets a flame;
# streaks are counted back through at most STREAK_LOOKBACK games.
STREAK_MIN = 3
STREAK_LOOKBACK = 10
# A pick is "cold" when it lost the last STREAK_MIN+ games, it's a roughly 50/50 pick (its fair chance in this
# range: long shots lose most games anyway, so a run of misses says nothing about them) and its market has three or
# more picks: in a two-way market (over/under, win/loss) the other side of a cold pick already has the flame.
COLD_CHANCE = (0.35, 0.65)


class BetError(Exception):
    pass


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 120_000).hex()
    return salt, digest


EMPTY_STATS = {
    "won": 0, "lost": 0, "void": 0, "cancelled": 0, "pending": 0,
    "pending_stake": 0.0, "staked": 0.0, "returned": 0.0,
}


class BetManager:
    def __init__(self, cfg, db, engine):
        self.db = db
        self.engine = engine
        self.starting = float(cfg.get("starting_balance", 1000))
        # Riot's start time is when the match launched, so bets made while loading in (or during round 1) are
        # still meant for that game: they count for it if placed within this long after it started.
        self.grace_s = max(0.0, float(cfg.get("bet_grace_minutes", 2))) * 60
        # A bettor can take a bet back only this soon after placing it (fixing a misclick), not once a game is under
        # way and it's going badly. Admin cancellations aren't limited.
        self.cancel_s = max(0.0, float(cfg.get("bet_cancel_minutes", 1))) * 60

    # ---- bettor accounts -------------------------------------------------
    @staticmethod
    def _valid_name(name):
        name = (name or "").strip()
        if not name or len(name) > 32:
            raise BetError("Bettor name must be 1-32 characters.")
        return name

    @staticmethod
    def _valid_password(password):
        password = password or ""
        if len(password) < 4 or len(password) > 64:
            raise BetError("Password must be 4 to 64 characters.")
        return password

    @staticmethod
    def public(b):
        return {"name": b["name"], "balance": round(b["balance"], 2)}

    def register(self, name, password):
        """Create a bettor, or claim a name that exists without a password yet."""
        name = self._valid_name(name)
        password = self._valid_password(password)
        b = self.db.get_bettor(name)
        if b and b.get("password_hash"):
            raise BetError("That name already has an account. Sign in with its password.")
        salt, digest = hash_password(password)
        if b:
            self.db.set_bettor_password(b["name"], salt, digest)
        else:
            self.db.create_bettor(name, self.starting, salt, digest)
        return self.db.get_bettor(name)

    def authenticate(self, name, password):
        name = self._valid_name(name)
        b = self.db.get_bettor(name)
        if not b or not b.get("password_hash"):
            raise BetError("No account with that name yet. Create one first.")
        _, digest = hash_password(password or "", b["salt"])
        if not hmac.compare_digest(digest, b["password_hash"]):
            raise BetError("Wrong password.")
        return b

    def change_password(self, name, old, new):
        b = self.authenticate(name, old)
        new = self._valid_password(new)
        salt, digest = hash_password(new)
        self.db.set_bettor_password(b["name"], salt, digest)
        return self.db.get_bettor(b["name"])

    def clear_password(self, name):
        """Admin: forget a bettor's password so the name can be claimed again."""
        b = self.db.get_bettor(self._valid_name(name))
        if not b:
            raise BetError("Bettor not found.")
        self.db.set_bettor_password(b["name"], None, None)
        return self.db.get_bettor(b["name"])

    def reset(self):
        """End the season: its standings, bets and game rewards are archived (see db.archive_and_reset), then every
        balance goes back to the starting amount. Returns the archived season."""
        return self.db.archive_and_reset(self.starting, self.leaderboard())

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
        rewards = self.db.reward_totals()
        out = []
        for b in self.db.bettors():
            s = per.get(b["name"].lower(), dict(EMPTY_STATS))
            earned = rewards.get(b["name"].lower(), 0.0)
            # Betting profit only: game rewards are credits too, but they are reported separately.
            profit = b["balance"] + s["pending_stake"] - self.starting - earned
            row = {
                "name": b["name"],
                "claimed": bool(b.get("password_hash")),
                "balance": round(b["balance"], 2),
                "profit": round(profit, 2),
                "rewards": round(earned, 2),
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
        bettor = self.db.get_bettor(self._valid_name(bettor_name))
        if not bettor:
            raise BetError("Sign in as a bettor first.")
        if stake > bettor["balance"] + 1e-9:
            raise BetError(f"{bettor['name']} only has {bettor['balance']:.0f} credits.")
        alt = parse_alt(market_id)  # a custom line ("Loog gets 25+ kills") or exact number is priced on the spot
        board = self.engine.build(self.db, context or {}, alts=[alt] if alt else None)
        if not board.get("ready"):
            raise BetError(board.get("message", "Odds are not available yet."))
        market, sel = find_market(board, market_id, sel_key)
        if not market or not sel:
            raise BetError(self._unavailable(board, market_id, sel_key) or "That market is no longer available. Refresh the odds board.")

        mtype = market["type"]
        desc, meta = self._describe(market, sel)
        meta["ctx"] = board.get("context")
        meta["fair_prob"] = sel["fair_prob"]  # the model's own chance, for the odds accuracy card

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

    @staticmethod
    def _unavailable(board, market_id, sel_key):
        """Why a custom line can't be bet on: not a whole-number line, too far from the player's usual numbers, or
        so likely there's nothing to win."""
        for mk in board.get("custom", []):
            if mk["market_id"] == market_id:
                sel = next((s for s in mk.get("selections", []) if s["key"] == sel_key), None)
                return (sel or {}).get("reason") or mk.get("reason")
        return None

    @staticmethod
    def _describe(market, sel):
        """(description, meta) for a pick on the board; meta is what _evaluate needs to settle it."""
        mtype = market["type"]
        if mtype in ("ou", "exact"):
            return (f"{market['member']} {market['stat_label']} {sel['label']}",
                    {"stat": market["stat"], "puuid": market["puuid"], **({"custom": True} if market.get("custom") else {})})
        if mtype == "top":
            return f"{market['label']}: {sel['label']}", {"stat": market["stat"], "direction": market.get("direction", "high")}
        if mtype == "team_win":
            return f"Match result: {sel['label']}", {}
        if mtype == "team_ot":
            return f"Overtime: {sel['label']}", {}
        if mtype == "team_ou":
            return f"Total rounds {sel['label']}", {}
        return f"{market['label']}: {sel['label']}", {}  # rounds won / lost, winning margin, exact score

    def quote_parlay(self, legs, context):
        """Price 2-10 picks (each from a different market) as one parlay without placing it. Returns (the legs as
        they're stored on the bet, the board, the price: odds_decimal, independent_decimal, factor, games, linked).

        Legs that decide each other are refused (parlay.score_conflict); legs that tend to land together have their
        multiplied odds cut by how much more often they won together on recent games (parlay.correlation)."""
        if not isinstance(legs, list) or len(legs) < 2:
            raise BetError("A parlay needs at least 2 legs.")
        if len(legs) > 10:
            raise BetError("A parlay can have at most 10 legs.")
        alts = [a for a in (parse_alt((leg or {}).get("market_id")) for leg in legs) if a]
        board = self.engine.build(self.db, context or {}, alts=alts)
        if not board.get("ready"):
            raise BetError(board.get("message", "Odds are not available yet."))

        seen, built = set(), []
        for leg in legs:
            market_id, sel_key = (leg or {}).get("market_id"), (leg or {}).get("selection")
            if not market_id or not sel_key:
                raise BetError("Each parlay leg needs a market and a selection.")
            if alt_family(market_id) in seen:  # a custom line counts as the same market as the board's line
                raise BetError("Each leg of a parlay must be a different market (one line per player and stat).")
            seen.add(alt_family(market_id))
            market, sel = find_market(board, market_id, sel_key)
            if not market or not sel:
                raise BetError(self._unavailable(board, market_id, sel_key) or "One of the legs is no longer available. Refresh the odds board.")
            desc, meta = self._describe(market, sel)
            built.append({
                "market_id": market_id, "market_type": market["type"], "description": desc,
                "selection": sel_key, "selection_label": sel["label"], "line": market.get("line"),
                "odds_decimal": sel["decimal"], "fair_prob": sel["fair_prob"], "meta": meta,
            })
        conflict = score_conflict(built, self._evaluate)
        if conflict:
            raise BetError(conflict)
        hists = self.leg_history(built)
        for leg, hist in zip(built, hists):
            leg["hist"] = hist  # kept on the bet, so settlement can re-price the legs that stood if some are voided
        corr = correlation(hists)
        decimals = [leg["odds_decimal"] for leg in built]
        independent = 1.0
        for d in decimals:
            independent *= d
        return built, board, {"odds_decimal": price(decimals, corr["factor"]),
                              "independent_decimal": round(independent, 2), **corr}

    def place_parlay(self, bettor_name, legs, stake, context):
        """Combine 2+ selections (from different markets) into a single all-or-nothing bet, at quote_parlay's price."""
        try:
            stake = round(float(stake), 2)
        except (TypeError, ValueError):
            raise BetError("Invalid stake.")
        if stake < 1:
            raise BetError("Minimum stake is 1 credit.")
        bettor = self.db.get_bettor(self._valid_name(bettor_name))
        if not bettor:
            raise BetError("Sign in as a bettor first.")
        if stake > bettor["balance"] + 1e-9:
            raise BetError(f"{bettor['name']} only has {bettor['balance']:.0f} credits.")
        built, board, quote = self.quote_parlay(legs, context)

        bet = {
            "bettor": bettor["name"],
            "market_id": "parlay",
            "market_type": "parlay",
            "description": " + ".join(l["description"] for l in built),
            "selection": "parlay",
            "selection_label": f"{len(built)}-leg parlay",
            "line": None,
            "odds_decimal": quote["odds_decimal"],
            "stake": stake,
            "placed_ts": time.time(),
            "context": json.dumps({"legs": built, "ctx": board.get("context"), "corr": quote}),
            "status": "pending",
        }
        with self.db.lock:
            self.db.adjust_balance(bettor["name"], -stake)
            bet_id = self.db.insert_bet(bet)
        return self.db.bet(bet_id)

    def cancel(self, bet_id, by=None, admin=False):
        bet = self.db.bet(bet_id)
        if not bet:
            raise BetError("Bet not found.")
        if bet["status"] != "pending":
            raise BetError("Only pending bets can be cancelled.")
        if not admin and (not by or bet["bettor"].lower() != by.lower()):
            raise BetError("You can only cancel your own bets.")
        if not admin and time.time() - bet["placed_ts"] > self.cancel_s:
            window = self.cancel_s / 60
            raise BetError(f"Bets can only be cancelled within {window:g} minute{'' if window == 1 else 's'} of placing them.")
        with self.db.lock:
            self.db.update_bet(
                bet_id, status="cancelled", settled_ts=time.time(), payout=bet["stake"],
                note="Cancelled before the game",
            )
            self.db.adjust_balance(bet["bettor"], bet["stake"])
        return self.db.bet(bet_id)

    # ---- settlement ------------------------------------------------------
    def settle_for_match(self, match, players):
        """Settle every pending bet placed before this match started, or within the grace period after (see grace_s)."""
        started = match.get("started_ts") or 0
        pending = [b for b in self.db.pending_bets() if b["placed_ts"] < started + self.grace_s]
        if not pending:
            return []
        rounds = (match.get("rounds_won") or 0) + (match.get("rounds_lost") or 0)
        metrics = {p["puuid"]: player_metrics(p, rounds) for p in players}
        self._add_relative_acs(metrics, started)
        settled = []
        for b in pending:
            new_context = None
            if b["market_type"] == "parlay":
                status, payout, actual, note, new_context = self._evaluate_parlay(b, match, metrics)
            else:
                status, actual, note = self._evaluate(b, match, metrics)
                if status == "won":
                    payout = round(b["stake"] * b["odds_decimal"], 2)
                elif status == "void":
                    payout = b["stake"]
                else:
                    payout = 0.0
            with self.db.lock:
                fields = dict(
                    status=status, settled_match_id=match["match_id"],
                    settled_ts=time.time(), payout=payout, actual_value=actual, note=note,
                )
                if new_context is not None:
                    fields["context"] = new_context
                self.db.update_bet(b["id"], **fields)
                if payout:
                    self.db.adjust_balance(b["bettor"], payout)
            settled.append(self.db.bet(b["id"]))
        return settled

    def _add_relative_acs(self, metrics, started, rows=None):
        """ACS as a multiple of each player's own average over their 5-stack games before this one."""
        earlier = {}
        for r in rows if rows is not None else self.db.player_rows():
            if (r.get("started_ts") or 0) < started:
                earlier.setdefault(r["puuid"], []).append(r)
        for puuid, m in metrics.items():
            own = aggregate(earlier.get(puuid, [])).get("acs")
            m["acs_rel"] = m["acs"] / own if own else None

    def mark_streaks(self, board):
        """Give each selection on the board that would have won the last STREAK_MIN games or more in a row a
        "streak" count, and a roughly 50/50 pick (COLD_CHANCE) that lost them a "cold" count, unless its market is
        two-way (the other side's flame already says it). Each pick is settled like a bet placed at today's line on
        each recent game, newest first; a void (a push, a surrender, a tie) neither counts nor breaks the run."""
        if not board.get("ready"):
            return board
        games = self._recent_games(STREAK_LOOKBACK)
        for group in ("team", "player_props", "top_markets"):
            for mk in board.get(group, []):
                meta = json.dumps({"stat": mk.get("stat"), "puuid": mk.get("puuid"), "direction": mk.get("direction", "high")})
                two_way = len(mk["selections"]) == 2
                for s in mk["selections"]:
                    bet = {"market_type": mk["type"], "selection": s["key"], "line": mk.get("line"), "context": meta}
                    won = lost = 0
                    for match, metrics in games:
                        status = self._evaluate(bet, match, metrics)[0]
                        if status == "void":
                            continue
                        if (status == "won" and lost) or (status != "won" and won):
                            break
                        won, lost = won + (status == "won"), lost + (status != "won")
                    if won >= STREAK_MIN:
                        s["streak"] = won
                    elif lost >= STREAK_MIN and not two_way and COLD_CHANCE[0] <= s["fair_prob"] <= COLD_CHANCE[1]:
                        s["cold"] = lost
        board["streak_lookback"] = STREAK_LOOKBACK
        return board

    def _recent_games(self, limit):
        """The last `limit` games, newest first, as (match, metrics) pairs ready for _evaluate."""
        rows = self.db.player_rows()
        lines = {}
        for r in rows:
            lines.setdefault(r["match_id"], []).append(r)
        games = []
        for match in self.db.matches(limit=limit):
            rounds = (match.get("rounds_won") or 0) + (match.get("rounds_lost") or 0)
            metrics = {p["puuid"]: player_metrics(p, rounds) for p in lines.get(match["match_id"], [])}
            self._add_relative_acs(metrics, match.get("started_ts") or 0, rows)
            games.append((match, metrics))
        return games

    def leg_history(self, legs):
        """Each parlay leg settled at its line on the last parlay.LOOKBACK games, newest first, as a string:
        "1" won, "0" lost, "-" void (see parlay.correlation)."""
        games = self._recent_games(LOOKBACK)
        out = []
        for leg in legs:
            bet = {"market_type": leg["market_type"], "selection": leg["selection"], "line": leg.get("line"),
                   "context": json.dumps(leg.get("meta") or {})}
            out.append("".join({"won": "1", "lost": "0"}.get(self._evaluate(bet, m, metrics)[0], "-")
                               for m, metrics in games))
        return out

    def _evaluate_parlay(self, b, match, metrics):
        """Every leg must win. A void leg is dropped (no action); if none are left, the whole parlay is void. The legs
        that stood are re-priced together (parlay.correlation on their saved histories), as if placed without the
        void ones."""
        ctx = json.loads(b.get("context") or "{}")
        legs = ctx.get("legs", [])
        results = []
        for leg in legs:
            fake = {
                "market_type": leg["market_type"], "selection": leg["selection"],
                "line": leg.get("line"), "context": json.dumps(leg.get("meta") or {}),
            }
            status, actual, note = self._evaluate(fake, match, metrics)
            results.append({**leg, "result": status, "actual": actual, "note": note})

        statuses = [r["result"] for r in results]
        voided = sum(1 for s in statuses if s == "void")
        if any(s == "lost" for s in statuses):
            overall, payout = "lost", 0.0
        else:
            won = [r for r in results if r["result"] == "won"]
            if not won:
                overall, payout = "void", b["stake"]
            elif voided:  # bets from before leg histories were saved have none: plain multiplied odds
                factor = correlation([r.get("hist") or "" for r in won])["factor"]
                overall, payout = "won", round(b["stake"] * price([r["odds_decimal"] for r in won], factor), 2)
            else:
                overall, payout = "won", round(b["stake"] * b["odds_decimal"], 2)
        surrendered = ending(match) == FORFEIT
        if overall == "void":
            note = ("Game ended early (surrender) before any leg was decided: stake refunded" if surrendered
                    else "Push: every leg voided, stake refunded")
        elif voided and surrendered and overall == "won":
            note = f"Game ended early (surrender): {voided} undecided leg(s) dropped; payout uses the remaining odds"
        elif voided:
            note = f"{voided} leg(s) voided (no action); payout uses the remaining odds"
        else:
            note = None
        return overall, payout, None, note, json.dumps({**ctx, "legs": results})

    def _evaluate(self, b, match, metrics):
        """Settle one bet on a recorded game.

        A surrendered game (forfeit) is an official result, so the match-result market settles as usual.
        Every other market is void unless the surrender came after it was already decided: an over on a
        counting stat (kills, deaths, assists, total rounds) that had already cleared the line wins, and the
        matching under loses. Per-round rates (ACS, ADR, HS%) and top-of-scoreboard markets could still have
        swung either way, so they are void.
        """
        meta = json.loads(b.get("context") or "{}")
        t = b["market_type"]
        sel = b["selection"]
        line = b.get("line")
        if ending(match) == FORFEIT and t != "team_win":
            return self._evaluate_forfeit(b, match, metrics, meta)
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
            low = meta.get("direction") == "low"  # bets from before counter markets existed are all "high"
            vals = {p: m.get(stat) for p, m in metrics.items() if m.get(stat) is not None}
            if stat == "acs_rel" and len(vals) < len(metrics):
                return "void", None, "Not everyone has earlier 5-stack games to compare against"
            if len(vals) < 2:
                return "void", None, "Not enough data to settle"
            best = min(vals.values()) if low else max(vals.values())
            winners = [p for p, v in vals.items() if abs(v - best) < 1e-9]
            actual = vals.get(sel)
            actual = round(actual, 2) if actual is not None else None
            if len(winners) > 1:
                return "void", actual, f"Tie at the {'bottom' if low else 'top'}: stakes refunded"
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
        if t == "team_ot":
            ot = went_to_overtime(match)
            return ("won" if ot == (sel == "yes") else "lost"), (1 if ot else 0), None
        if t == "exact":  # an exact number on a counting stat
            m = metrics.get(meta.get("puuid"))
            if not m:
                return "void", None, "Player was not in this game"
            v = m.get(meta.get("stat"))
            if v is None:
                return "void", None, "Stat unavailable for this game"
            return ("won" if abs(v - line) < 1e-9 else "lost"), v, None
        if t in SCORE_MARKETS:
            rw, rl = match.get("rounds_won") or 0, match.get("rounds_lost") or 0
            if rounds_to_win(match.get("mode")) != DEFAULT_ROUNDS_TO_WIN:
                return "void", None, "Not a first-to-13 game: score bets are refunded"
            if match.get("result") == "draw":
                return "void", None, "Game was a draw"
            if t == "team_score":
                return ("won" if score_key(match) == sel else "lost"), rw - rl, None
            if t == "team_margin":
                return ("won" if margin_key(match) == sel else "lost"), rw - rl, None
            value = rw if t == "team_rw" else rl
            won = value > line if sel == "over" else value < line
            return ("won" if won else "lost"), value, None
        return "void", None, "Unknown market type"

    @staticmethod
    def _evaluate_forfeit(b, match, metrics, meta):
        t, sel, line = b["market_type"], b["selection"], b.get("line")
        if t == "team_ot":  # overtime is decided only if the surrender came after 12-12
            if went_to_overtime(match):
                return ("won" if sel == "yes" else "lost"), 1, "Decided before the surrender"
            return "void", None, EARLY_END
        if t == "team_ou":
            value = (match.get("rounds_won") or 0) + (match.get("rounds_lost") or 0)
        elif t in ("team_rw", "team_rl"):  # rounds won / lost so far only ever go up
            value = match.get("rounds_won" if t == "team_rw" else "rounds_lost") or 0
        elif t in ("ou", "exact") and meta.get("stat") in COUNTING_STATS:  # an exact number already passed is lost
            m = metrics.get(meta.get("puuid"))
            if not m:
                return "void", None, "Player was not in this game"
            value = m.get(meta.get("stat"))
        else:  # rates and top-of-scoreboard markets are never decided before the end
            m = metrics.get(meta.get("puuid")) if t == "ou" else None
            actual = round(m[meta["stat"]], 2) if m and m.get(meta.get("stat")) is not None else None
            return "void", actual, EARLY_END
        if value is None:
            return "void", None, "Stat unavailable for this game"
        if value > line:  # already past the line when the game stopped
            return ("won" if sel == "over" else "lost"), round(value, 2), "Decided before the surrender"
        return "void", round(value, 2), EARLY_END
