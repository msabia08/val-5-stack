"""Blackjack against Onkey, for betting credits (never bananas).

Rules, fixed: six decks, reshuffled when less than a quarter is left; the dealer stands on every 17; blackjack pays
3:2; double down on any first two cards (after a split too, except split aces); split any pair, and split again as
often as a new pair comes, with split aces taking one card each (a new ace splits again); the dealer checks for
blackjack under an ace or ten-value card, so a dealer blackjack only takes the original stakes; no insurance, no
surrender. Onkey's save (below) is part of the rules too. That's a house edge of about 0.11% (EDGE,
bjstrategy.house_edge()), the house's cut.

Onkey's hint: on your turn `me.hint` is the move worth most on average and every legal move's value
(bjstrategy.best(): basic strategy, worked out for these rules), which the page has him say after a pause. It knows what
you can afford: with only a few stakes left, a split is valued by the doubles and re-splits you could still pay for.

Credits: doubling and splitting each take another stake. A move you could make but can't pay for is left out of
`me.actions` and listed in `me.short`, so the page can say why its button is off. A split ace that draws another ace
stays open only to be split again; with no stake for that there's nothing to decide, and it stands by itself.

Onkey's peek: on PEEK_CHANCE of your decisions (where hitting is allowed) he peeks at a card he shouldn't, the next
card in the shoe or (PEEK_HOLE of the time, once a round) his own hole card, and tells you its rank, lying PEEK_LIE of
the time (a small card for a big one or the other way round). `me.peek` carries only what he claims; the truth stays
here until the card shows (the next card when you hit, double or split, standing keeps it secret; the hole card when
the round ends), and then `me.peek_result` says what it was and whether he lied, so the page can have him gloat or
own up.

Onkey's save: SAVE_CHANCE (bjstrategy's, 1 in 100) of the busts a hit or a double causes, Onkey takes his pen to the
card and makes it the one that gives exactly 21, same suit (there always is one: you can only bust from 12 or more).
The hand keeps `saved` (`index`, `was`: the card that came out of the shoe) so the page can draw it crossed out.

Side bets, optional, each 0 or a stake no bigger than the main one, taken with it: Perfect Pairs on your first two
cards (PAIRS_PAY) and 21+3 on those two plus Onkey's upcard read as a three-card poker hand (PLUS3_PAY). They're decided
at the deal (shown at once, logged as `side`) and paid with the hand; their house edges (SIDE_EDGE, worked out exactly
for six decks by side_edge()) go into the round's expected take.

Streaks: each bettor's run of winning or losing rounds (pushes leave it alone), kept across tables while the server
runs; the seats carry it and the log marks a hot run (`streak`) or a cold one (`slump`) at STREAK_MARKS.

Emotes: anyone at a table can send one of EMOTES (one every EMOTE_GAP_S), logged as `emote` for the page to pop over
their seat; a banana is thrown at Onkey.

Tips: after a round you won, you can tip Onkey one of TIPS, up to what you won and once a round, until the next round
opens. It comes off your balance and goes to the house: kept on the round's row (`tip`, counted against your casino net)
and added to that round's house_ledger take.

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
import random
import time

from . import bjstrategy, cards, house
from .bets import BetError
from .tables import LiveManager, LiveTable, check_ref

STAKES = (5, 10, 25, 50, 100, 250, 500)
DECKS = 6
RESHUFFLE_AT = DECKS * 52 // 4
EDGE = 0.0011  # bjstrategy.house_edge() for these rules, the save included; the self-test checks it
SAVE_CHANCE = bjstrategy.SAVE_CHANCE
SHARED_SEATS = 5
BET_WINDOW_S = 15
TURN_S = 30
RESULT_S = 5
PEEK_CHANCE = 0.15
PEEK_HOLE = 0.5
PEEK_LIE = 0.4
SIDE_BETS = ("pairs", "plus3")
SIDE_BETS_OPEN = False  # side bets are switched off for now: True brings them back (the page follows `side_bets.open`)
PAIRS_PAY = {"perfect": 25, "coloured": 12, "mixed": 6}
PLUS3_PAY = {"suited_trips": 100, "straight_flush": 40, "trips": 30, "straight": 10, "flush": 5}
SIDE_NAMES = {"perfect": "Perfect pair", "coloured": "Coloured pair", "mixed": "Mixed pair", "suited_trips": "Suited trips",
              "straight_flush": "Straight flush", "trips": "Three of a kind", "straight": "Straight", "flush": "Flush"}
SIDE_EDGE = {"pairs": 0.0611, "plus3": 0.0462}  # side_edge() for six decks; the self-test checks them
STREAK_MARKS = (3, 5, 8, 12)  # a run of wins (streak) or losses (slump) this long is logged
EMOTES = ("👏", "😂", "😬", "🔥", "🙈", "🍌")
EMOTE_GAP_S = 1.5
TIPS = (5, 10, 25)
TEN = set("TJQK")
RULES = ["Six decks, reshuffled when a quarter is left", "Dealer stands on all 17s", "Blackjack pays 3 to 2",
         "Double down on any first two cards, after a split too",
         "Split and re-split pairs as often as you like; split aces get one card each",
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


RED = set("hd")


def pairs_result(a, b):
    """Perfect Pairs on two cards: perfect (same card), coloured (same rank and colour), mixed, or None."""
    if a[0] != b[0]:
        return None
    return "perfect" if a[1] == b[1] else "coloured" if (a[1] in RED) == (b[1] in RED) else "mixed"


def plus3_result(a, b, up):
    """21+3 on your two cards and the dealer's upcard as a poker hand, or None."""
    order = "23456789TJQKA"
    ranks = sorted(order.index(c[0]) for c in (a, b, up))
    flush = a[1] == b[1] == up[1]
    trips = ranks[0] == ranks[2]
    straight = (ranks[1] == ranks[0] + 1 and ranks[2] == ranks[1] + 1) or ranks == [0, 1, 12]  # A-2-3 counts
    if trips:
        return "suited_trips" if flush else "trips"
    if straight:
        return "straight_flush" if flush else "straight"
    return "flush" if flush else None


def side_edge(kind, decks=DECKS):
    """The house edge of a side bet, exactly, over every first deal from a fresh shoe of `decks` decks."""
    faces = [r + s for r in "23456789TJQKA" for s in "shdc"]
    n = 52 * decks
    ev = 0.0
    if kind == "pairs":
        for a in faces:
            for b in faces:
                w = decks * (decks - (a == b)) / (n * (n - 1))
                res = pairs_result(a, b)
                ev += w * (PAIRS_PAY[res] if res else -1)
        return round(-ev, 6)
    for a in faces:
        for b in faces:
            wab = decks * (decks - (a == b))
            for c in faces:
                w = wab * (decks - (c == a) - (c == b)) / (n * (n - 1) * (n - 2))
                res = plus3_result(a, b, c)
                ev += w * (PLUS3_PAY[res] if res else -1)
    return round(-ev, 6)


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
        self.streaks = {}      # bettor -> wins in a row (positive) or losses in a row (negative)
        self.last_emote = {}   # bettor -> when they last sent one
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
                self._txn(lambda: self._void_row(name, p["row"], p["stake"] + p["side_total"], "Left before the deal"))
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
    def bet(self, name, which, stake, request_id, side=None):
        if type(stake) not in (int, float) or stake not in STAKES:
            raise BetError(f"Choose a stake of {', '.join(map(str, STAKES[:-1]))} or {STAKES[-1]} credits.")
        side = self._check_side(side, stake)
        side_total = sum(side.values())
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
            if b["balance"] < stake + side_total:
                raise BetError("Not enough credits for that bet. Choose a smaller stake." if b["balance"] < stake
                               else "Not enough credits for those side bets too.")

            def run():
                self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance - ?, 2) WHERE name=?",
                                     (stake + side_total, name))
                cur = self.db.conn.execute(
                    "INSERT INTO blackjack_hands(bettor, tbl, round, stake, payout, hands, dealer, status, created_ts, "
                    "request_id, edge, side) VALUES(?,?,?,?,0,'[]','[]','playing',?,?,?,?)",
                    (name, "shared" if t.shared else "solo", t.round, stake + side_total, now, request_id, EDGE,
                     json.dumps(side) if side else None))
                return cur.lastrowid
            row = self._txn(run)
            t.players[name] = {"row": row, "stake": stake, "side": side, "side_total": side_total, "side_results": {},
                               "hands": [], "step": 0}
            t.add_log("bet", name, now, amount=stake + side_total)
            if t.shared and t.deadline is None:
                t.deadline = now + BET_WINDOW_S
            self._maybe_deal(t, now)
            self.changed(t)
            return self.view(name, which)

    @staticmethod
    def _check_side(side, stake):
        """Side bets from a request: {kind: stake} for the ones placed, each one of STAKES and at most the main stake."""
        if not side or (isinstance(side, dict) and not any(side.values())):  # none placed
            return {}
        if not SIDE_BETS_OPEN:
            raise BetError("Side bets are closed for now.")
        if not isinstance(side, dict) or set(side) - set(SIDE_BETS):
            raise BetError("Unknown side bet.")
        out = {}
        for kind, amount in side.items():
            if amount in (0, None):
                continue
            if type(amount) not in (int, float) or amount not in STAKES:
                raise BetError("Side bets use the same chips as the main bet.")
            if amount > stake:
                raise BetError("A side bet can't be bigger than your main bet.")
            out[kind] = amount
        return out

    def _settle_sides(self, t, now):
        """Decide every side bet on the first two cards (and the upcard), right after the deal."""
        for name in t.order:
            p = t.players[name]
            a, b = p["hands"][0]["cards"]
            for kind, amount in p["side"].items():
                res = pairs_result(a, b) if kind == "pairs" else plus3_result(a, b, t.dealer[0])
                pays = (PAIRS_PAY if kind == "pairs" else PLUS3_PAY).get(res, 0)
                payout = amount * (pays + 1) if res else 0
                p["side_results"][kind] = {"stake": amount, "result": res, "name": SIDE_NAMES.get(res), "pays": pays,
                                           "payout": payout}
                if res:
                    t.add_log("side", name, now, bet=kind, hand=SIDE_NAMES[res], amount=payout - amount)

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
            p["hands"] = [{"id": 0, "cards": [], "stake": p["stake"], "doubled": False, "split": False, "done": False,
                           "result": None, "payout": 0}]
        for _ in range(2):
            for name in t.order:
                t.players[name]["hands"][0]["cards"].append(self._draw(t))
            t.dealer.append(self._draw(t))
        t.phase, t.deadline = "playing", None
        t.add_log("deal", None, now, players=len(t.order))
        self._settle_sides(t, now)
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
                if h["done"]:
                    continue
                # A split ace still open drew another ace: it can only be split again. With no stake for that there's
                # nothing to choose, so it stands by itself.
                if h["split"] and h["cards"][0][0] == "A" and self.db.get_bettor(name)["balance"] < h["stake"]:
                    h["done"] = True
                    t.add_log("stand", name, now, total=total(h["cards"])[0])
                    continue
                if t.turn != (name, i):
                    t.turn = (name, i)
                    t.deadline = now + TURN_S if t.shared else None
                return
        t.turn, t.deadline = None, None
        self._finish(t, now)

    # ---- playing -----------------------------------------------------------------
    def _moves(self, t, name):
        """(what `name` may do right now, the moves their hand allows but their credits don't cover)."""
        if t.phase != "playing" or not t.turn or t.turn[0] != name:
            return [], []
        h = t.players[name]["hands"][t.turn[1]]
        funded = self.db.get_bettor(name)["balance"] >= h["stake"]  # doubling and splitting each take another stake
        split_aces = h["split"] and h["cards"][0][0] == "A"
        out, short = ["stand"] if split_aces else ["hit", "stand"], []  # a split ace still open drew another ace
        if len(h["cards"]) == 2 and not split_aces:
            (out if funded else short).append("double")
        if len(h["cards"]) == 2 and card_value(h["cards"][0]) == card_value(h["cards"][1]):
            (out if funded else short).append("split")
        return out, short

    def actions(self, t, name):
        """What `name` may do right now."""
        return self._moves(t, name)[0]

    def hint(self, t, name):
        """The best move for `name`'s hand right now and every legal move's value (bjstrategy.best), or None. It
        counts what the balance still covers (`spare` stakes) and the split hands waiting behind this one."""
        acts = self.actions(t, name)
        if not acts:
            return None
        hands, i = t.players[name]["hands"], t.turn[1]
        spare = int(self.db.get_bettor(name)["balance"] // hands[i]["stake"])
        later = [h["cards"][1] for h in hands[i + 1:] if not h["done"]]
        return bjstrategy.best(hands[i]["cards"], t.dealer[0], acts, spare, later)

    def _peek(self, t, name):
        """What Onkey claims about a card for `name`'s decision now (decided once per step), or None."""
        p = t.players[name]
        if p.get("peek_step") != p["step"]:
            p["peek_step"], p["peek"] = p["step"], None
            if "hit" in self.actions(t, name) and t.shoe and random.random() < PEEK_CHANCE:
                kind = "hole" if not p.get("hole_peek") and random.random() < PEEK_HOLE else "next"
                truth = (t.dealer[1] if kind == "hole" else t.shoe[0])[0]
                lie = random.random() < PEEK_LIE
                big = "89" if kind == "hole" and t.dealer[0][0] == "A" else "89TJQK"  # never a blackjack he's ruled out
                claim = random.choice("23456" if card_value(truth) >= 7 else big) if lie else truth
                p["peek"] = {"kind": kind, "card": claim, "step": p["step"]}
                if kind == "hole":
                    p["hole_peek"] = p["peek"]
        return p["peek"]

    def _reveal(self, p, pk, actual):
        p["peek_result"] = {"kind": pk["kind"], "claimed": pk["card"], "actual": actual, "honest": pk["card"] == actual,
                            "step": pk["step"]}

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
        pk = p.get("peek")
        if pk and pk["kind"] == "next" and pk["step"] == p["step"] and act != "stand":
            self._reveal(p, pk, t.shoe[0][0])  # the card this move draws first
        p["step"] += 1
        if act in ("double", "split"):
            extra = h["stake"]
            total_stake = sum(x["stake"] for x in p["hands"]) + extra
            self._txn(lambda: self._move(name, -extra, p["row"], total_stake + p["side_total"]))
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
        elif act == "split":  # the new hand goes right after this one, so hands are played left to right
            second = {"id": len(p["hands"]), "cards": [h["cards"].pop()], "stake": h["stake"], "doubled": False,
                      "split": True, "done": False, "result": None, "payout": 0}
            h["split"] = True
            p["hands"].insert(i + 1, second)
            for x in (h, second):
                x["cards"].append(self._draw(t))
                if x["cards"][0][0] == "A" and x["cards"][1][0] != "A":  # split aces: one card each, unless another ace
                    x["done"] = True
            t.add_log("split", name, now, hands=len(p["hands"]))
        if act in ("hit", "double"):
            self._maybe_save(t, name, h, now)
        for x in p["hands"]:
            if not x["done"] and total(x["cards"])[0] >= 21:
                x["done"] = True  # a bust, or 21 (stands by itself)
        if total(h["cards"])[0] > 21:
            t.add_log("bust", name, now, total=total(h["cards"])[0])

    def _maybe_save(self, t, name, h, now):
        """Rarely, a bust gets Onkey's pen: the card that busted becomes the one that makes 21 (same suit)."""
        if total(h["cards"])[0] <= 21 or random.random() >= SAVE_CHANCE:
            return
        was = h["cards"][-1]
        for r in "A23456789T":
            if total(h["cards"][:-1] + [r + was[1]])[0] == 21:
                h["cards"][-1] = r + was[1]
                h["saved"] = {"index": len(h["cards"]) - 1, "was": was}
                t.add_log("save", name, now, was=was, card=h["cards"][-1])
                return

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
            if p.get("hole_peek"):
                self._reveal(p, p["hole_peek"], t.dealer[1][0])
            for h in p["hands"]:
                h["result"], h["payout"] = outcome(h, t.dealer)
            main = sum(h["stake"] for h in p["hands"])
            staked = main + p["side_total"]
            paid = sum(h["payout"] for h in p["hands"]) + sum(r["payout"] for r in p["side_results"].values())
            expected = main * EDGE + sum(amount * SIDE_EDGE[kind] for kind, amount in p["side"].items())
            side = json.dumps({k: {"stake": r["stake"], "result": r["result"], "payout": r["payout"]}
                               for k, r in p["side_results"].items()}) if p["side"] else None

            def run(name=name, p=p, staked=staked, paid=paid, expected=expected, side=side):
                self._move(name, paid, p["row"])
                self.db.conn.execute(
                    "UPDATE blackjack_hands SET stake=?, payout=?, hands=?, dealer=?, side=?, status='settled', "
                    "settled_ts=? WHERE id=?",
                    (staked, paid, json.dumps(p["hands"]), json.dumps(t.dealer), side, now, p["row"]))
                house.record(self.db.conn, "blackjack", f"hand:{p['row']}", name, staked, staked - paid, expected, now)
            self._txn(run)
            p["net"] = round(paid - staked, 2)
            kind = "win" if paid > staked else "push" if paid == staked else "lose"
            t.add_log(kind, name, now, amount=round(paid - staked, 2), staked=staked,
                      blackjack=any(h["result"] == "blackjack" for h in p["hands"]))
            self._streak(t, name, kind, now)
        t.phase, t.turn, t.deadline = "done", None, None
        t.next_round_at = now + RESULT_S if t.shared else None

    def _streak(self, t, name, kind, now):
        s = self.streaks.get(name, 0)
        if kind == "win":
            s = s + 1 if s > 0 else 1
        elif kind == "lose":
            s = s - 1 if s < 0 else -1
        self.streaks[name] = s
        if abs(s) in STREAK_MARKS or abs(s) > STREAK_MARKS[-1] and abs(s) % 4 == 0:
            t.add_log("streak" if s > 0 else "slump", name, now, n=abs(s))

    # ---- emotes ----------------------------------------------------------------------
    def emote(self, name, which, emote):
        with self.lock:
            now = self.clock()
            name = self._bettor(name)["name"]
            t = self._table(name, which)
            if emote not in EMOTES:
                raise BetError("Onkey doesn't know that one.")
            if t.shared and name not in t.seats:
                raise BetError("Take a seat at the shared table first.")
            if now - self.last_emote.get(name, 0) < EMOTE_GAP_S:
                raise BetError("Easy. One at a time.")
            self.last_emote[name] = now
            t.add_log("emote", name, now, emote=emote)
            self.changed(t)
            return self.view(name, which)

    # ---- tips ------------------------------------------------------------------------
    def tip(self, name, which, amount):
        if type(amount) not in (int, float) or amount not in TIPS:
            raise BetError(f"Tip Onkey {', '.join(map(str, TIPS[:-1]))} or {TIPS[-1]} credits.")
        with self.lock:
            now = self.clock()
            b = self._bettor(name)
            name = b["name"]
            t = self._table(name, which)
            p = t.players.get(name)
            if not p or t.phase != "done":
                raise BetError("Tips go to Onkey once a round is over.")
            if p.get("tip"):
                raise BetError("Onkey already has your tip. Thank you!")
            if p.get("net", 0) <= 0:
                raise BetError("Tips come out of a win.")
            if amount > p["net"]:
                raise BetError(f"You can tip up to {p['net']:g} from that win.")
            if b["balance"] < amount:
                raise BetError("Not enough credits for that tip.")

            def run():
                self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance - ?, 2) WHERE name=?", (amount, name))
                self.db.conn.execute("UPDATE blackjack_hands SET tip=? WHERE id=?", (amount, p["row"]))
                self.db.conn.execute(
                    "UPDATE house_ledger SET take=ROUND(take + ?, 2), expected=ROUND(COALESCE(expected, 0) + ?, 4) "
                    "WHERE game='blackjack' AND ref=?", (amount, amount, f"hand:{p['row']}"))
            self._txn(run)
            p["tip"] = amount
            t.add_log("tip", name, now, amount=amount)
            self.changed(t)
            return self.view(name, which)

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
                              "side": p["side"] if p else {}, "side_results": p["side_results"] if p else {},
                              "hands": hands, "playing": n in t.order, "streak": self.streaks.get(n, 0)})
            me = None
            if name:
                p = t.players.get(name)
                b = self.db.get_bettor(name)
                me = {"name": name, "seated": name in t.seats, "bet": bool(p), "step": p["step"] if p else 0,
                      "actions": self.actions(t, name), "short": self._moves(t, name)[1], "hint": self.hint(t, name),
                      "peek": self._peek(t, name) if p and t.turn and t.turn[0] == name else None,
                      "peek_result": p.get("peek_result") if p else None,
                      "tip": {"open": t.phase == "done" and p.get("net", 0) > 0 and not p.get("tip"), "max": p.get("net", 0),
                              "tipped": p.get("tip"), "amounts": TIPS} if p else None,
                      "turn": bool(t.turn and t.turn[0] == name),
                      "balance": round(b["balance"], 2) if b else 0, "season": self.season(name)}
            return {"table": "shared" if t.shared else "solo", "phase": t.phase, "round": t.round,
                    "version": t.version, "seats": seats, "max_seats": SHARED_SEATS if t.shared else 1,
                    "dealer": {"cards": dealer, "total": total(shown)[0] if shown else 0,
                               "blackjack": not hide and is_blackjack(t.dealer)},
                    "turn": {"bettor": t.turn[0], "hand": t.turn[1]} if t.turn else None,
                    "deadline": t.deadline, "next_round_at": t.next_round_at, "server_time": self.clock(),
                    "shoe_left": len(t.shoe), "log": list(t.log), "me": me,
                    "stakes": STAKES, "rules": RULES, "edge": EDGE, "turn_s": TURN_S, "bet_window_s": BET_WINDOW_S,
                    "side_bets": {"open": SIDE_BETS_OPEN, "pairs": {"pays": PAIRS_PAY, "edge": SIDE_EDGE["pairs"]},
                                  "plus3": {"pays": PLUS3_PAY, "edge": SIDE_EDGE["plus3"]}, "names": SIDE_NAMES},
                    "emotes": EMOTES,
                    "shared_seats": {"taken": len(self.tables["shared"].seats), "max": SHARED_SEATS,
                                     "names": list(self.tables["shared"].seats)}}

    def season(self, name):
        r = self.db.query_one(
            "SELECT COUNT(*) AS hands, COALESCE(SUM(stake),0) AS staked, COALESCE(SUM(payout),0) AS returned, "
            "COUNT(CASE WHEN payout > stake THEN 1 END) AS wins, "
            "COUNT(CASE WHEN hands LIKE '%\"result\": \"blackjack\"%' THEN 1 END) AS blackjacks, "
            "COALESCE(SUM(tip),0) AS tips "
            "FROM blackjack_hands WHERE bettor=? AND status='settled' AND season_id IS NULL", (name,))
        return {**r, "net": round(r["returned"] - r["staked"] - r["tips"], 2)}
