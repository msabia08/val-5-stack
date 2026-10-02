"""Virtual-credit betting ledger: bettor accounts, placement, cancellation, settlement, leaderboard."""
import hashlib
import hmac
import json
import secrets
import time

from .gamestate import DEFAULT_ROUNDS_TO_WIN, FORFEIT, ending, rounds_to_win, went_to_overtime
from .house import casino_nets
from .moments import game_facts
from .odds import STAT_DEFS, alt_family, decimal_to_american, fair_chance, find_market, margin_key, parse_alt, score_key
from .parlay import LOOKBACK, correlation, price, score_conflict
from .stats import aggregate, player_metrics

# Stats that only ever go up during a game: an over that cleared the line before a surrender is already won.
COUNTING_STATS = {d["key"] for d in STAT_DEFS if d["kind"] == "count"}
SCORE_MARKETS = {"team_score", "team_margin", "team_rw", "team_rl"}  # priced from odds.score_model
# Note on a bet voided by a surrender. A void single is refunded; a void parlay leg is dropped from the parlay.
EARLY_END = "Game ended early (surrender) before this was decided"
# Note on a bet on a moment market (pistol, ace, ...) when the game's round timeline was never fetched: refunded.
NO_ROUND_DATA = "Round-by-round data for this game isn't available: stake refunded"
# A pick on the board that would have won in each of the last STREAK_MIN games (at today's line) gets a flame;
# streaks are counted back through at most STREAK_LOOKBACK games.
STREAK_MIN = 3
STREAK_LOOKBACK = 10
# A pick is "cold" when it lost the last STREAK_MIN+ games, it's a roughly 50/50 pick (its fair chance in this
# range: long shots lose most games anyway, so a run of misses says nothing about them) and its market has three or
# more picks: in a two-way market (over/under, win/loss) the other side of a cold pick already has the flame.
COLD_CHANCE = (0.35, 0.65)
NOTE_MAX = 80  # characters in a transfer's note
# The generosity tax ("the generous monkey gets rewarded"): send another bettor at least TAX_MIN_TRANSFER credits and
# you take TAX_RATE of the net winnings of their next winning bet (the biggest one, if several win on that game).
# One open tax per sender and recipient: sending again before it's paid doesn't stack. The minimum stops a 1-credit
# transfer to everyone from earning a cut of all their wins.
TAX_RATE = 0.10
TAX_MIN_TRANSFER = 250
# The odds boost of the game: one pick on the board, drawn again after every game, pays BOOST more profit (2.00 becomes
# 2.50) on singles of up to BOOST_MAX_STAKE credits. It's a promotion, not paid from the house's pot: the pot counts a
# boosted bet at its price before the boost. Picks the model gives a chance in BOOST_CHANCE are eligible.
BOOST = 0.5
BOOST_MAX_STAKE = 100
BOOST_CHANCE = (0.3, 0.6)
# The daily wheel's tokens (wheel.py, table wheel_perks), used on a single when the bettor asks for them from the bet
# slip (place()'s `tokens`): a boost token adds TOKEN_BOOST profit to a single of up to TOKEN_MAX_STAKE credits (not on
# top of the odds boost of the game), and an insurance token gets the stake back, up to TOKEN_MAX_STAKE, if it loses
# (paid by HouseManager.refunds).
TOKEN_BOOST = 0.5
TOKEN_MAX_STAKE = 200


def _credits(v):
    return f"{v:,.2f}".rstrip("0").rstrip(".")


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
        with self.db.lock:
            return self.db.archive_and_reset(self.starting, self.leaderboard())

    def house(self):
        """What the house has taken from match bets, every season (archived bets included): the estimate (each stake
        times the edge it was priced at: 1 minus the price times the model's chance, the chance of every leg that stood
        for a parlay) and what actually happened (stakes minus payouts). Voids and cancels are refunds and count for
        neither; open bets aren't counted until they settle."""
        rows = self.db.query(
            "SELECT market_type, odds_decimal, stake, payout, status, context, settled_ts FROM bets "
            "WHERE status IN ('won','lost') UNION ALL "
            "SELECT market_type, odds_decimal, stake, payout, status, context, settled_ts FROM archived_bets "
            "WHERE status IN ('won','lost')")
        edge = self.engine.edge
        staked = paid = expected = 0.0
        for b in rows:
            ctx = json.loads(b["context"] or "{}")
            if b["market_type"] == "parlay":
                legs = [leg for leg in ctx.get("legs") or [] if leg.get("result") != "void"]
                ret = 1.0
                for leg in legs:  # the correlation cut lowers the price and raises the joint chance alike
                    ret *= leg["odds_decimal"] * leg.get("fair_prob", fair_chance(leg["odds_decimal"], leg["market_type"], edge))
            else:  # a boosted bet counts at its price before the boost: the boost isn't paid from the pot
                dec = ctx.get("boost") or b["odds_decimal"]
                ret = dec * ctx.get("fair_prob", fair_chance(dec, b["market_type"], edge))
            staked += b["stake"]
            paid += b["payout"] or 0.0
            expected += b["stake"] * (1 - ret)
        return {"since": min((b["settled_ts"] for b in rows if b["settled_ts"]), default=None), "bets": len(rows),
                "staked": round(staked, 2), "paid": round(paid, 2), "expected_take": round(expected, 2),
                "actual_take": round(staked - paid, 2)}

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
        transfers = self.db.transfer_totals()
        casino = casino_nets(self.db)
        house = {r["k"]: r["total"] for r in self.db.query(
            "SELECT lower(bettor) AS k, SUM(amount) AS total FROM house_payouts WHERE season_id IS NULL GROUP BY k")}
        loans = self.db.loan_totals()
        out = []
        for b in self.db.bettors():
            s = per.get(b["name"].lower(), dict(EMPTY_STATS))
            earned = rewards.get(b["name"].lower(), 0.0)
            received = transfers.get(b["name"].lower(), 0.0)
            loan = loans.get(b["name"].lower()) or {}
            # Match-betting profit only: game rewards, transfers, the casino, the house's giveaways and bank loans
            # change the balance too, but they are reported separately (a loan's credits aren't profit, and the
            # interest isn't a loss).
            nets = casino.get(b["name"].lower(), {})
            given = house.get(b["name"].lower(), 0.0)
            profit = (b["balance"] + s["pending_stake"] - self.starting - earned - received - nets.get("total", 0.0)
                      - given - (loan.get("net") or 0.0))
            row = {
                "name": b["name"],
                "claimed": bool(b.get("password_hash")),
                "balance": round(b["balance"], 2),
                "debt": round(loan.get("debt") or 0.0, 2),  # what they still owe Onkey's Bank, interest included
                "borrowed": round(loan.get("borrowed") or 0.0, 2),
                "profit": round(profit, 2),
                "rewards": round(earned, 2),
                "transfers": round(received, 2),
                "casino": round(nets.get("total", 0.0), 2),  # every casino game this season
                "slots": round(nets.get("slots", 0.0), 2),
                "giveaways": round(given, 2),
                "roi": round((s["returned"] - s["staked"]) / s["staked"], 3) if s["staked"] else None,
            }
            for k, v in s.items():
                row[k] = round(v, 2) if isinstance(v, float) else v
            out.append(row)
        out.sort(key=lambda x: -(x["balance"] - x["debt"]))  # credits minus bank debt: borrowed credits don't rank
        return out

    # ---- transfers between bettors ------------------------------------------
    def send(self, sender_name, recipient_name, amount, note=None):
        """Send credits from one bettor to another (paying off a side bet, spotting a friend). Whole credits or
        cents, at least 1, no more than the sender has; the note is optional and short. Returns the transfer."""
        try:
            amount = round(float(amount), 2)
        except (TypeError, ValueError):
            raise BetError("Enter an amount.")
        if not amount >= 1:  # also catches NaN
            raise BetError("The smallest transfer is 1 credit.")
        sender = self.db.get_bettor(self._valid_name(sender_name))
        if not sender:
            raise BetError("Sign in as a bettor first.")
        recipient = self.db.get_bettor((recipient_name or "").strip()) if (recipient_name or "").strip() else None
        if not recipient:
            raise BetError("Pick who to send credits to.")
        if recipient["name"].lower() == sender["name"].lower():
            raise BetError("You can't send credits to yourself.")
        note = " ".join(str(note or "").split())
        if len(note) > NOTE_MAX:
            raise BetError(f"Keep the note to {NOTE_MAX} characters.")
        transfer_id = self.db.transfer(sender["name"], recipient["name"], amount, note or None, tax_min=TAX_MIN_TRANSFER)
        if transfer_id is None:
            raise BetError(f"{sender['name']} only has {self.db.get_bettor(sender['name'])['balance']:.0f} credits.")
        return self.db.query_one("SELECT * FROM transfers WHERE id=?", (transfer_id,))

    # ---- placement -------------------------------------------------------
    def place(self, bettor_name, market_id, sel_key, stake, context, tokens=None):
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
        self.apply_boost(board)
        market, sel = find_market(board, market_id, sel_key)
        if not market or not sel:
            raise BetError(self._unavailable(board, market_id, sel_key) or "That market is no longer available. Refresh the odds board.")
        if sel.get("boost") and stake > BOOST_MAX_STAKE:
            raise BetError(f"The odds boost takes up to {BOOST_MAX_STAKE} credits.")

        mtype = market["type"]
        desc, meta = self._describe(market, sel)
        meta["ctx"] = board.get("context")
        if sel.get("boost"):
            meta["boost"] = sel["boost"]["from_decimal"]  # the price before the boost, which the house's pot counts
        meta["fair_prob"] = sel["fair_prob"]  # the model's own chance, for the odds accuracy card

        price = sel["decimal"]
        with self.db.lock:
            # The daily wheel's tokens the bettor asked to use on this single (`tokens`: {"boost": true, "insurance":
            # true}, from the bet slip; see TOKEN_BOOST).
            tokens = tokens if isinstance(tokens, dict) else {}
            ready = {p["kind"]: p for p in self.db.query(
                "SELECT * FROM wheel_perks WHERE lower(bettor)=lower(?) AND status='ready' ORDER BY id", (bettor["name"],))}
            used = []
            if tokens.get("boost"):
                if not ready.get("boost"):
                    raise BetError("You don't have a boost token. Win one on the daily wheel.")
                if sel.get("boost"):
                    raise BetError("This pick already has the odds boost of the game. Keep your token for another one.")
                if stake > TOKEN_MAX_STAKE:
                    raise BetError(f"A boost token covers a single of up to {TOKEN_MAX_STAKE} credits.")
                meta["boost"], meta["boost_token"] = price, ready["boost"]["id"]
                price = self.boosted(price, TOKEN_BOOST)
                used.append(ready["boost"]["id"])
            if tokens.get("insurance"):
                if not ready.get("insurance"):
                    raise BetError("You don't have an insurance token. Win one on the daily wheel.")
                meta["insured"] = {"perk": ready["insurance"]["id"], "max": TOKEN_MAX_STAKE}
                used.append(ready["insurance"]["id"])
            bet = {
                "bettor": bettor["name"],
                "market_id": market_id,
                "market_type": mtype,
                "description": desc,
                "selection": sel_key,
                "selection_label": sel["label"],
                "line": market.get("line"),
                "odds_decimal": price,
                "stake": stake,
                "placed_ts": time.time(),
                "context": json.dumps(meta),
                "status": "pending",
            }
            self.db.adjust_balance(bettor["name"], -stake)
            bet_id = self.db.insert_bet(bet)
            for perk in used:
                self.db.execute("UPDATE wheel_perks SET status='used', bet_id=?, used_ts=? WHERE id=?", (bet_id, time.time(), perk))
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
        if mtype == "team_moment":  # pistol, half-time, ace, comeback, flawless rounds: settled from the timeline
            return f"{market['label']}: {sel['label']}", {"fact": market["fact"]}
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
        match = {**match, "timeline": self.db.timeline(match["match_id"])}  # the moment markets settle from it
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
        if self.collect_taxes(settled, match):
            settled = [self.db.bet(b["id"]) for b in settled]  # the taxed bets' notes changed
        return settled

    def collect_taxes(self, settled, match):
        """The generosity tax. Everyone with an open tax on a bettor (they sent them TAX_MIN_TRANSFER+ credits before
        this settlement) takes TAX_RATE of the net winnings of that bettor's biggest winning bet on this game, paid
        by the bettor. The bet's note says where the money went. Returns the taxes paid."""
        best = {}
        for b in settled:
            net = (b.get("payout") or 0.0) - b["stake"]
            key = b["bettor"].lower()
            if b["status"] == "won" and net > 0 and (key not in best or net > best[key][1]):
                best[key] = (b, net)
        paid = []
        for bet, net in best.values():
            note, amount = bet.get("note"), round(net * TAX_RATE, 2)
            if amount <= 0:
                continue
            for t in self.db.open_taxes(bet["bettor"], bet.get("settled_ts") or time.time()):
                line = (f"Generosity tax: {_credits(amount)} of the winnings went to {t['sender']}, "
                        f"who sent you {_credits(t['amount'])} credits")
                note = f"{note} · {line}" if note else line
                if self.db.pay_tax(t["id"], bet["id"], match["match_id"], amount, note):
                    paid.append({"transfer_id": t["id"], "sender": t["sender"], "recipient": bet["bettor"],
                                 "bet_id": bet["id"], "amount": amount})
        return paid

    def _add_relative_acs(self, metrics, started, rows=None):
        """ACS as a multiple of each player's own average over their 5-stack games before this one."""
        earlier = {}
        for r in rows if rows is not None else self.db.player_rows():
            if (r.get("started_ts") or 0) < started:
                earlier.setdefault(r["puuid"], []).append(r)
        for puuid, m in metrics.items():
            own = aggregate(earlier.get(puuid, [])).get("acs")
            m["acs_rel"] = m["acs"] / own if own else None

    @staticmethod
    def boosted(decimal, pct=BOOST):
        return round(1 + (decimal - 1) * (1 + pct), 2)

    def apply_boost(self, board):
        """Mark the odds boost of the game on a board and raise its price (see BOOST). The pick is drawn once per game
        (meta `odds_boost`, keyed by the latest recorded game) from the board's picks with a fair chance in
        BOOST_CHANCE, and drawn again if it leaves the board. Returns the board."""
        if not board.get("ready"):
            return board
        latest = self.db.matches(1)
        after = latest[0]["match_id"] if latest else None
        cur = self.db.get_meta("odds_boost")
        market = sel = None
        if cur and cur.get("after") == after:
            market, sel = find_market(board, cur["market_id"], cur["selection"])
        if not sel:
            options = [(mk, s) for group in ("team", "player_props", "top_markets") for mk in board.get(group, [])
                       if mk.get("available", True) for s in mk["selections"]
                       if s.get("available", True) and BOOST_CHANCE[0] <= s["fair_prob"] <= BOOST_CHANCE[1]]
            if not options:
                return board
            market, sel = options[secrets.randbelow(len(options))]
            self.db.set_meta("odds_boost", {"after": after, "market_id": market["market_id"], "selection": sel["key"],
                                            "drawn_ts": time.time()})
        if not sel.get("boost"):
            sel["boost"] = {"from_decimal": sel["decimal"], "from_american": sel["american"], "pct": BOOST,
                            "max_stake": BOOST_MAX_STAKE}
            sel["decimal"] = self.boosted(sel["decimal"])
            sel["american"] = decimal_to_american(sel["decimal"])
        board["boost"] = {"market_id": market["market_id"], "selection": sel["key"],
                          "description": self._describe(market, sel)[0], **sel["boost"],
                          "decimal": sel["decimal"], "american": sel["american"], "fair_prob": sel["fair_prob"]}
        return board

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
                meta = json.dumps({"stat": mk.get("stat"), "puuid": mk.get("puuid"), "direction": mk.get("direction", "high"), "fact": mk.get("fact")})
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
        timelines = {t["match_id"]: t["data"] for t in self.db.timelines()}
        games = []
        for match in self.db.matches(limit=limit):
            match = {**match, "timeline": timelines.get(match["match_id"])}  # for the moment markets
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
        if t == "team_moment":  # surrenders are handled inside, fact by fact
            return self._evaluate_moment(b, match, meta)
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
                return "void", None, "Not everyone has earlier squad games to compare against"
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
    def _evaluate_moment(b, match, meta):
        """A team market read from the round timeline (moments.game_facts: pistol, half, ace, comeback, flawless).
        Without the game's timeline (the full record wasn't fetched) it's void. On a surrender, a fact that was
        already decided settles (the pistol, half-time once round 12 was played, an ace that happened, flawless rounds
        already past the line, a comeback since the result stands) and the rest are void."""
        sel, line, fact = b["selection"], b.get("line"), meta.get("fact")
        if not match.get("timeline"):
            return "void", None, NO_ROUND_DATA
        forfeit = ending(match) == FORFEIT
        v = game_facts(match, match["timeline"]).get(fact)
        if v is None:
            return "void", None, EARLY_END if forfeit else "This game had no round 12 (a shorter mode)"
        decided = "Decided before the surrender" if forfeit else None
        if line is not None:  # an over / under on a count, which only goes up
            if forfeit and v <= line:
                return "void", v, EARLY_END
            if abs(v - line) < 1e-9:
                return "void", v, "Push: landed exactly on the line"
            return ("won" if (v > line) == (sel == "over") else "lost"), v, decided
        if forfeit and fact == "ace" and not v:
            return "void", 0, EARLY_END  # nobody had aced yet, but someone still could have
        return ("won" if bool(v) == (sel in ("yes", "us")) else "lost"), int(bool(v)), decided

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
