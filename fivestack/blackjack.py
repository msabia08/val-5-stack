"""Blackjack against Onkey, for betting credits (never bananas).

Rules, fixed: six decks, reshuffled when less than a quarter is left; the dealer stands on every 17; blackjack pays
3:2; double down on any first two cards (after a split too, except split aces); split once, into two hands, with split
aces taking one card each; the dealer checks for blackjack under an ace or ten-value card, so a dealer blackjack only
takes the original stakes; no insurance, no surrender. That's a house edge of about 0.5% (EDGE), the house's cut.

Tables: every bettor has their own solo table ("solo"), and there is ONE shared table ("shared", SHARED_SEATS seats)
where whoever sits down plays against the same dealer hand. On the shared table a round opens for bets; betting
closes BET_WINDOW_S seconds after the first bet, or as soon as everyone seated has bet. Players then act in seat
order, TURN_S seconds each (a timeout stands), the dealer plays, and the results stay up RESULT_S seconds before the
next round opens. A solo table deals as soon as you bet and waits for you.

Money: a bet takes its stake from the balance and inserts a `blackjack_hands` row ('playing') in one transaction;
doubling and splitting take the extra stake the same way. Settling credits the payout, marks the row 'settled' and
writes its house_ledger row (house.record) in one transaction. Round state lives in memory, so start-up (and a season
reset) voids any row still 'playing' and refunds its stake.
"""
import json
import time

from . import cards, house
from .bets import BetError
from .tables import LiveManager, LiveTable, check_ref

STAKES = (5, 10, 25, 50, 100, 250, 500)
DECKS = 6
RESHUFFLE_AT = DECKS * 52 // 4
EDGE = 0.005
SHARED_SEATS = 5
BET_WINDOW_S = 15
TURN_S = 30
RESULT_S = 5
TEN = set("TJQK")
RULES = ["Six decks, reshuffled when a quarter is left", "Dealer stands on all 17s", "Blackjack pays 3 to 2",
         "Double down on any first two cards, after a split too", "Split once; split aces get one card each",
         "No insurance or surrender"]


def card_value(card):
    r = card[0]
    return 11 if r == "A" else 10 if r in TEN else int(r)


def total(hand):
    """(best total, soft): soft when an ace still counts as 11."""
    t = sum(card_value(c) for c in hand)
    aces = sum(c[0] == "A" for c in hand)
    while t > 21 and aces:
        t -= 10
        aces -= 1
    return t, aces > 0


def is_blackjack(hand, split=False):
    return not split and len(hand) == 2 and total(hand)[0] == 21


def dealer_should_hit(hand):
    return total(hand)[0] < 17


def outcome(hand, dealer):
    """A finished hand against the dealer's: (result, payout including the stake)."""
    pt, stake = total(hand["cards"])[0], hand["stake"]
    pbj, dbj = is_blackjack(hand["cards"], hand["split"]), is_blackjack(dealer)
    if pt > 21:
        return "bust", 0
    if pbj and not dbj:
        return "blackjack", stake * 2.5
    if dbj:
        return ("push", stake) if pbj else ("lose", 0)
    dt = total(dealer)[0]
    if dt > 21 or pt > dt:
        return "win", stake * 2
    return ("push", stake) if pt == dt else ("lose", 0)


class Table(LiveTable):
    def __init__(self, key, shared):
        super().__init__()
        self.key, self.shared = key, shared
        self.seats = []          # bettor names in seat order (a solo table's is its owner)
        self.shoe = []
        self.round = 0
        self.reset_round()

    def reset_round(self):
        self.phase = "betting"   # betting -> playing -> done
        self.players = {}        # bettor -> {"row", "stake", "hands", "step"}
        self.order = []          # this round's players, in seat order
        self.dealer = []
        self.turn = None         # (bettor, hand index)
        self.deadline = None     # the betting window's or the turn's end
        self.next_round_at = None


class BlackjackManager(LiveManager):
    def __init__(self, db, clock=None):
        super().__init__(db, clock or time.time)
        self.tables = {"shared": Table("shared", True)}
        with self.lock:
            self.void_open("The server restarted mid-hand")

    # ---- helpers ---------------------------------------------------------------
    def _table(self, name, which):
        if which == "shared":
            return self.tables["shared"]
        if which != "solo":
            raise BetError("Pick the solo table or the shared table.")
        key = "solo:" + name.lower()
        if key not in self.tables:
            self.tables[key] = Table(key, False)
            self.tables[key].seats = [name]
        return self.tables[key]

    def _bettor(self, name):
        b = self.db.get_bettor(name)
        if not b:
            raise BetError("Sign in as a bettor to play.")
        return b

    def _draw(self, t):
        if not t.shoe:
            t.shoe = cards.shuffled(DECKS)
        return t.shoe.pop(0)

    def _move(self, name, delta, row_id, stake=None):
        """Credits in or out of a bettor, with the hand row's stake kept in step. Inside a transaction."""
        self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance + ?, 2) WHERE name=?", (delta, name))
        if stake is not None:
            self.db.conn.execute("UPDATE blackjack_hands SET stake=? WHERE id=?", (stake, row_id))

    def _txn(self, fn):
        with self.db.lock:
            try:
                out = fn()
                self.db.conn.commit()
                return out
            except Exception:
                self.db.conn.rollback()
                raise

    # ---- seats -----------------------------------------------------------------
    def sit(self, name):
        with self.lock:
            name = self._bettor(name)["name"]
            t = self.tables["shared"]
            if name not in t.seats:
                if len(t.seats) >= SHARED_SEATS:
                    raise BetError(f"The shared table is full ({SHARED_SEATS} seats). Try the solo table.")
                t.seats.append(name)
                t.add_log("sit", name, self.clock())
                self.changed(t)
            return self.view(name, "shared")

    def leave(self, name):
        with self.lock:
            name = self._bettor(name)["name"]
            t = self.tables["shared"]
            p = t.players.get(name)
            if p and t.phase == "playing" and not all(h["done"] for h in p["hands"]):
                raise BetError("Finish your hand first.")
            if p and t.phase == "betting":  # a bet placed but not dealt yet comes back
                self._txn(lambda: self._void_row(name, p["row"], p["stake"], "Left before the deal"))
                del t.players[name]
            if name in t.seats:
                t.seats.remove(name)
                t.add_log("leave", name, self.clock())
                if t.phase == "betting" and not t.players:
                    t.deadline = None
                self._maybe_deal(t, self.clock())
                self.changed(t)
            return self.view(name, "shared")

    def _void_row(self, name, row_id, stake, note):
        self._move(name, stake, row_id)
        self.db.conn.execute("UPDATE blackjack_hands SET status='void', payout=?, note=?, settled_ts=? WHERE id=?",
                             (stake, note, self.clock(), row_id))

    def void_open(self, note):
        """Refund every hand still in play and clear the tables (start-up, season reset). Call under self.lock."""
        def run():
            for r in self.db.query("SELECT id, bettor, stake FROM blackjack_hands WHERE status='playing'"):
                self._void_row(r["bettor"], r["id"], r["stake"], note)
        self._txn(run)
        for t in self.tables.values():
            t.reset_round()
            self.changed(t)

    # ---- betting and dealing -----------------------------------------------------
    def bet(self, name, which, stake, request_id):
        if type(stake) not in (int, float) or stake not in STAKES:
            raise BetError(f"Choose a stake of {', '.join(map(str, STAKES[:-1]))} or {STAKES[-1]} credits.")
        check_ref(request_id, "bet")
        with self.lock:
            now = self.clock()
            b = self._bettor(name)
            name = b["name"]
            t = self._table(name, which)
            old = self.db.query_one("SELECT id FROM blackjack_hands WHERE bettor=? AND request_id=?", (name, request_id))
            if old:  # a retry: that bet is already on the table (or settled)
                return self.view(name, which)
            if t.shared:
                if name not in t.seats:
                    raise BetError("Take a seat at the shared table first.")
                if t.phase != "betting":
                    raise BetError("Bets open again when this round is over.")
                if name in t.players:
                    raise BetError("You already have a bet on this round.")
            else:
                if t.phase == "playing":
                    raise BetError("Finish your hand first.")
                t.reset_round()
                t.round += 1
            if b["balance"] < stake:
                raise BetError("Not enough credits for that bet. Choose a smaller stake.")

            def run():
                self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance - ?, 2) WHERE name=?", (stake, name))
                cur = self.db.conn.execute(
                    "INSERT INTO blackjack_hands(bettor, tbl, round, stake, payout, hands, dealer, status, created_ts, "
                    "request_id, edge) VALUES(?,?,?,?,0,'[]','[]','playing',?,?,?)",
                    (name, "shared" if t.shared else "solo", t.round, stake, now, request_id, EDGE))
                return cur.lastrowid
            row = self._txn(run)
            t.players[name] = {"row": row, "stake": stake, "hands": [], "step": 0}
            t.add_log("bet", name, now, amount=stake)
            if t.shared and t.deadline is None:
                t.deadline = now + BET_WINDOW_S
            self._maybe_deal(t, now)
            self.changed(t)
            return self.view(name, which)

    def _maybe_deal(self, t, now, force=False):
        if t.phase != "betting" or not t.players:
            return
        if not t.shared or force or all(s in t.players for s in t.seats):
            self._deal(t, now)

    def _deal(self, t, now):
        if len(t.shoe) < RESHUFFLE_AT:
            t.shoe = cards.shuffled(DECKS)
            t.add_log("shuffle", None, now)
        t.order = [s for s in t.seats if s in t.players] + [s for s in t.players if s not in t.seats]
        for name in t.order:
            p = t.players[name]
            p["hands"] = [{"cards": [], "stake": p["stake"], "doubled": False, "split": False, "done": False,
                           "result": None, "payout": 0}]
        for _ in range(2):
            for name in t.order:
                t.players[name]["hands"][0]["cards"].append(self._draw(t))
            t.dealer.append(self._draw(t))
        t.phase, t.deadline = "playing", None
        t.add_log("deal", None, now, players=len(t.order))
        if card_value(t.dealer[0]) >= 10 and is_blackjack(t.dealer):  # the peek
            for name in t.order:
                t.players[name]["hands"][0]["done"] = True
            t.add_log("dealer_blackjack", None, now)
            return self._finish(t, now)
        for name in t.order:
            h = t.players[name]["hands"][0]
            if is_blackjack(h["cards"]):
                h["done"] = True
                t.add_log("blackjack", name, now)
        self._advance(t, now)

    def _advance(self, t, now):
        for name in t.order:
            for i, h in enumerate(t.players[name]["hands"]):
                if not h["done"]:
                    if t.turn != (name, i):
                        t.turn = (name, i)
                        t.deadline = now + TURN_S if t.shared else None
                    return
        t.turn, t.deadline = None, None
        self._finish(t, now)

    # ---- playing -----------------------------------------------------------------
    def actions(self, t, name):
        """What `name` may do right now."""
        if t.phase != "playing" or not t.turn or t.turn[0] != name:
            return []
        p = t.players[name]
        h = p["hands"][t.turn[1]]
        balance = self.db.get_bettor(name)["balance"]
        out = ["hit", "stand"]
        split_aces = h["split"] and h["cards"][0][0] == "A"
        if len(h["cards"]) == 2 and not split_aces and balance >= h["stake"]:
            out.append("double")
        if (len(p["hands"]) == 1 and len(h["cards"]) == 2 and card_value(h["cards"][0]) == card_value(h["cards"][1])
                and balance >= h["stake"]):
            out.append("split")
        return out

    def action(self, name, which, act, step):
        with self.lock:
            now = self.clock()
            name = self._bettor(name)["name"]
            t = self._table(name, which)
            p = t.players.get(name)
            if not p or t.phase != "playing" or not t.turn or t.turn[0] != name:
                raise BetError("It's not your turn.")
            if step != p["step"]:
                raise BetError("That hand has moved on. Here's where it stands now.")
            if act not in self.actions(t, name):
                raise BetError("You can't do that with this hand.")
            self._apply(t, name, act, now)
            self._advance(t, now)
            self.changed(t)
            return self.view(name, which)

    def _apply(self, t, name, act, now):
        p = t.players[name]
        i = t.turn[1]
        h = p["hands"][i]
        p["step"] += 1
        if act in ("double", "split"):
            extra = h["stake"]
            total_stake = sum(x["stake"] for x in p["hands"]) + extra
            self._txn(lambda: self._move(name, -extra, p["row"], total_stake))
            p["stake"] = total_stake
        if act == "stand":
            h["done"] = True
            t.add_log("stand", name, now, total=total(h["cards"])[0])
        elif act == "hit":
            h["cards"].append(self._draw(t))
            t.add_log("hit", name, now, card=h["cards"][-1])
        elif act == "double":
            h["stake"] *= 2
            h["doubled"] = True
            h["cards"].append(self._draw(t))
            h["done"] = True
            t.add_log("double", name, now, amount=h["stake"], card=h["cards"][-1])
        elif act == "split":
            second = {"cards": [h["cards"].pop()], "stake": h["stake"], "doubled": False, "split": True,
                      "done": False, "result": None, "payout": 0}
            h["split"] = True
            p["hands"].append(second)
            for x in p["hands"]:
                x["cards"].append(self._draw(t))
            if h["cards"][0][0] == "A":  # split aces: one card each
                for x in p["hands"]:
                    x["done"] = True
            t.add_log("split", name, now)
        for x in p["hands"]:
            if not x["done"] and total(x["cards"])[0] >= 21:
                x["done"] = True  # a bust, or 21 (stands by itself)
        if total(h["cards"])[0] > 21:
            t.add_log("bust", name, now, total=total(h["cards"])[0])

    def _finish(self, t, now):
        live = [h for n in t.order for h in t.players[n]["hands"]
                if total(h["cards"])[0] <= 21 and not is_blackjack(h["cards"], h["split"])]
        if live and not is_blackjack(t.dealer):
            while dealer_should_hit(t.dealer):
                t.dealer.append(self._draw(t))
        dt = total(t.dealer)[0]
        if dt > 21:
            t.add_log("dealer_bust", None, now, total=dt)
        else:
            t.add_log("dealer_stands", None, now, total=dt)
        for name in t.order:
            p = t.players[name]
            for h in p["hands"]:
                h["result"], h["payout"] = outcome(h, t.dealer)
            staked = sum(h["stake"] for h in p["hands"])
            paid = sum(h["payout"] for h in p["hands"])

            def run(name=name, p=p, staked=staked, paid=paid):
                self._move(name, paid, p["row"])
                self.db.conn.execute(
                    "UPDATE blackjack_hands SET stake=?, payout=?, hands=?, dealer=?, status='settled', settled_ts=? "
                    "WHERE id=?", (staked, paid, json.dumps(p["hands"]), json.dumps(t.dealer), now, p["row"]))
                house.record(self.db.conn, "blackjack", f"hand:{p['row']}", name, staked, staked - paid, staked * EDGE, now)
            self._txn(run)
            kind = "win" if paid > staked else "push" if paid == staked else "lose"
            t.add_log(kind, name, now, amount=round(paid - staked, 2), staked=staked,
                      blackjack=any(h["result"] == "blackjack" for h in p["hands"]))
        t.phase, t.turn, t.deadline = "done", None, None
        t.next_round_at = now + RESULT_S if t.shared else None

    # ---- the clock -----------------------------------------------------------------
    def tick(self, now=None):
        """Close the shared table's betting window, stand a timed-out hand, open the next round."""
        with self.lock:
            now = now or self.clock()
            t = self.tables["shared"]
            if t.phase == "betting" and t.deadline and now >= t.deadline:
                self._maybe_deal(t, now, force=True)
            elif t.phase == "playing" and t.deadline and now >= t.deadline and t.turn:
                name = t.turn[0]
                t.add_log("timeout", name, now)
                self._apply(t, name, "stand", now)
                self._advance(t, now)
            elif t.phase == "done" and t.next_round_at and now >= t.next_round_at:
                t.reset_round()
                t.round += 1
            else:
                return
            self.changed(t)

    # ---- what the page sees ------------------------------------------------------------
    def view(self, name, which):
        with self.lock:
            t = self._table(name, which) if name else self.tables["shared"]
            hide = t.phase == "playing"
            dealer = [t.dealer[0], None] if hide and t.dealer else list(t.dealer)
            shown = [c for c in dealer if c]
            seats = []
            names = t.seats + [n for n in t.order if n not in t.seats]
            for n in names:
                p = t.players.get(n)
                hands = []
                for i, h in enumerate(p["hands"] if p else []):
                    tot, soft = total(h["cards"])
                    hands.append({**h, "total": tot, "soft": soft and tot < 21,
                                  "blackjack": is_blackjack(h["cards"], h["split"]),
                                  "turn": t.turn == (n, i)})
                seats.append({"bettor": n, "seated": n in t.seats, "stake": p["stake"] if p else None,
                              "hands": hands, "playing": n in t.order})
            me = None
            if name:
                p = t.players.get(name)
                b = self.db.get_bettor(name)
                me = {"name": name, "seated": name in t.seats, "bet": bool(p), "step": p["step"] if p else 0,
                      "actions": self.actions(t, name), "turn": bool(t.turn and t.turn[0] == name),
                      "balance": round(b["balance"], 2) if b else 0, "season": self.season(name)}
            return {"table": "shared" if t.shared else "solo", "phase": t.phase, "round": t.round,
                    "version": t.version, "seats": seats, "max_seats": SHARED_SEATS if t.shared else 1,
                    "dealer": {"cards": dealer, "total": total(shown)[0] if shown else 0,
                               "blackjack": not hide and is_blackjack(t.dealer)},
                    "turn": {"bettor": t.turn[0], "hand": t.turn[1]} if t.turn else None,
                    "deadline": t.deadline, "next_round_at": t.next_round_at, "server_time": self.clock(),
                    "shoe_left": len(t.shoe), "log": list(t.log), "me": me,
                    "stakes": STAKES, "rules": RULES, "edge": EDGE, "turn_s": TURN_S, "bet_window_s": BET_WINDOW_S,
                    "shared_seats": {"taken": len(self.tables["shared"].seats), "max": SHARED_SEATS,
                                     "names": list(self.tables["shared"].seats)}}

    def season(self, name):
        r = self.db.query_one(
            "SELECT COUNT(*) AS hands, COALESCE(SUM(stake),0) AS staked, COALESCE(SUM(payout),0) AS returned, "
            "COUNT(CASE WHEN payout > stake THEN 1 END) AS wins, "
            "COUNT(CASE WHEN hands LIKE '%\"result\": \"blackjack\"%' THEN 1 END) AS blackjacks "
            "FROM blackjack_hands WHERE bettor=? AND status='settled' AND season_id IS NULL", (name,))
        return {**r, "net": round(r["returned"] - r["staked"], 2)}
