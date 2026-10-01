"""Texas Hold'em at Onkey's table: one shared cash table, the players betting credits against each other.

There's no host. Any signed-in bettor can sit down (MAX_SEATS seats) with a buy-in, which moves from their balance into
the seat (`poker_seats`, the escrow). Everyone seated can change the table's rules while no game is running, and any
change un-readies everybody; the game starts once at least two players are seated and every one of them has readied
up. Hands then follow each other (a SHOWDOWN_S pause between them), dealt to the seated players who are ready and have
chips. Un-ready to sit out from the next hand; when fewer than two are ready, the game stops and the table is back in
the lobby, where the rules can change again. Players join and leave between hands; leaving during a hand folds your
cards and cashes you out when it ends. The blinds are the only forced bets.

The rules (`settings`, saved in meta `poker_settings`): no limit, pot limit or fixed limit; the blinds (`BLINDS`); the
buy-in range (BUYIN_MIN to BUYIN_MAX, at least ten big blinds); the turn timer (`TURN_CHOICES`; a timeout checks if it
can and folds if not, and TIMEOUTS_TO_SIT_OUT in a row sit you out); and whether you can top up between hands.

The house's cut (the rake): RAKE_RATE of each pot, at most RAKE_CAP credits, rounded down, and nothing from a hand
that ends before the flop. It's written to house_ledger with the hand.

Money: a buy-in, top-up or cash-out moves credits and the `poker_seats` row in one transaction (`poker_buyins` keeps
the record). A finished hand writes `poker_hands`, one `poker_results` row per player (their net) and the seats' new
stacks in one transaction. The hand in progress lives in memory only, so if the server restarts mid-hand, start-up
cashes every seat out at its stack from before that hand, which cancels the hand. A season reset does the same.
"""
import json
import time

from . import cards, house
from .bets import BetError
from .tables import LiveManager, LiveTable

MAX_SEATS = 8
LIMITS = {"no": "No limit", "pot": "Pot limit", "fixed": "Fixed limit"}
BLINDS = [(1, 2), (2, 5), (5, 10), (10, 20), (25, 50)]
TURN_CHOICES = (15, 20, 30, 45, 60)
BUYIN_MIN, BUYIN_MAX = 50, 1000
MIN_BUYIN_BLINDS = 10  # the minimum buy-in is at least this many big blinds
DEFAULTS = {"limit": "no", "small_blind": 5, "big_blind": 10, "min_buyin": 200, "max_buyin": 1000,
            "turn_seconds": 30, "rebuys": True}
RAKE_RATE, RAKE_CAP = 0.01, 5
SHOWDOWN_S = 6
FIXED_CAP = 4  # fixed limit: a bet and three raises per street
TIMEOUTS_TO_SIT_OUT = 2
STREETS = ["preflop", "flop", "turn", "river"]


def order_from(seats, after):
    """`seats` (sorted seat numbers) clockwise, starting with the first one after seat `after`."""
    i = next((k for k, s in enumerate(seats) if s > after), 0)
    return seats[i:] + seats[:i]


def build_pots(contrib, live):
    """Main and side pots from each seat's chips in the pot: [{"amount", "eligible": [seats]}]. A seat is eligible
    for every pot it put the full level into; folded chips count but never win."""
    remaining = dict(contrib)
    pots = []
    while sum(remaining.values()) > 0:
        in_play = sorted(s for s in live if remaining[s] > 0)
        if not in_play:  # folded chips above every live player's level go to the last pot
            if pots:
                pots[-1]["amount"] += sum(remaining.values())
            else:
                pots.append({"amount": sum(remaining.values()), "eligible": sorted(live)})
            break
        level = min(remaining[s] for s in in_play)
        amount = 0
        for s in remaining:
            take = min(remaining[s], level)
            amount += take
            remaining[s] -= take
        if pots and pots[-1]["eligible"] == in_play:
            pots[-1]["amount"] += amount
        else:
            pots.append({"amount": amount, "eligible": in_play})
    return pots


def rake_for(pot, saw_flop):
    return min(RAKE_CAP, int(pot * RAKE_RATE)) if saw_flop else 0


def check_settings(new, old):
    """The table's rules with `new`'s changes applied, or a BetError saying what's wrong."""
    s = dict(old)
    for k in DEFAULTS:
        if k in new and new[k] is not None:
            s[k] = new[k]
    if s["limit"] not in LIMITS:
        raise BetError("Pick no limit, pot limit or fixed limit.")
    blinds = (s["small_blind"], s["big_blind"])
    if blinds not in BLINDS:
        raise BetError("Pick blinds from the list: " + ", ".join(f"{a}/{b}" for a, b in BLINDS) + ".")
    for k in ("min_buyin", "max_buyin"):
        if type(s[k]) is not int:
            raise BetError("Buy-ins are whole credits.")
    if not BUYIN_MIN <= s["min_buyin"] <= s["max_buyin"] <= BUYIN_MAX:
        raise BetError(f"Buy-ins go from {BUYIN_MIN} to {BUYIN_MAX} credits, the minimum no higher than the maximum.")
    if s["min_buyin"] < MIN_BUYIN_BLINDS * s["big_blind"]:
        raise BetError(f"The minimum buy-in has to be at least {MIN_BUYIN_BLINDS} big blinds "
                       f"({MIN_BUYIN_BLINDS * s['big_blind']} credits at these blinds).")
    if s["turn_seconds"] not in TURN_CHOICES:
        raise BetError("Pick a turn timer from the list.")
    if not isinstance(s["rebuys"], bool):
        raise BetError("Top-ups are either allowed or not.")
    return s


class PokerTable(LiveTable):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.seats = [None] * MAX_SEATS  # each {"bettor", "stack", "ready", "timeouts", "leaving"}
        self.phase = "lobby"             # lobby (rules can change) or playing
        self.hand = None                 # the hand in play, or the one just finished during the pause
        self.hands_dealt = 0
        self.button = -1
        self.last = None                 # the last finished hand's results
        self.next_hand_at = None


class PokerManager(LiveManager):
    def __init__(self, db, clock=None):
        super().__init__(db, clock or time.time)
        saved = db.get_meta("poker_settings") or {}
        try:
            settings = check_settings(saved, DEFAULTS)
        except BetError:
            settings = dict(DEFAULTS)
        self.table = PokerTable(settings)
        with self.lock:
            self.close_all("The server restarted")

    # ---- helpers -------------------------------------------------------------------
    def _bettor(self, name):
        b = self.db.get_bettor(name)
        if not b:
            raise BetError("Sign in as a bettor to play.")
        return b

    def _seat_of(self, name):
        for i, s in enumerate(self.table.seats):
            if s and s["bettor"].lower() == name.lower():
                return i
        return None

    def _txn(self, fn):
        with self.db.lock:
            try:
                out = fn()
                self.db.conn.commit()
                return out
            except Exception:
                self.db.conn.rollback()
                raise

    def _in_hand(self, idx):
        """Is seat `idx` dealt into a hand that's still being played?"""
        h = self.table.hand
        return bool(h and h["street"] != "done" and idx in h["seats"])

    def _cash_out(self, idx, now, kind="cashout"):
        t = self.table
        seat = t.seats[idx]

        def run():
            self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance + ?, 2) WHERE name=?", (seat["stack"], seat["bettor"]))
            self.db.conn.execute("DELETE FROM poker_seats WHERE bettor=?", (seat["bettor"],))
            self.db.conn.execute("INSERT INTO poker_buyins(bettor, kind, amount, created_ts) VALUES(?,?,?,?)",
                                 (seat["bettor"], kind, seat["stack"], now))
        self._txn(run)
        t.seats[idx] = None
        t.add_log("leave", seat["bettor"], now, amount=seat["stack"])

    def close_all(self, note):
        """Cash every seat out at its stack from before the hand in play (start-up, season reset). Under self.lock."""
        now = self.clock()
        t = self.table
        h = t.hand
        if h and h["street"] != "done":
            for idx in h["seats"]:  # the unfinished hand is cancelled: everyone gets their chips back
                if t.seats[idx]:
                    t.seats[idx]["stack"] = h["start"][idx]
        for idx, seat in enumerate(t.seats):
            if seat:
                self._cash_out(idx, now)

        def refund_saved():  # seats saved from before a restart
            for r in self.db.query("SELECT bettor, stack FROM poker_seats"):
                self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance + ?, 2) WHERE name=?", (r["stack"], r["bettor"]))
                self.db.conn.execute("INSERT INTO poker_buyins(bettor, kind, amount, created_ts) VALUES(?,?,?,?)",
                                     (r["bettor"], "cashout", r["stack"], now))
            self.db.conn.execute("DELETE FROM poker_seats")
        self._txn(refund_saved)
        t.phase, t.hand, t.next_hand_at = "lobby", None, None
        t.add_log("closed", None, now, note=note)
        self.changed(t)

    # ---- seats and rules -------------------------------------------------------------
    def sit(self, name, buyin):
        with self.lock:
            now = self.clock()
            b = self._bettor(name)
            name = b["name"]
            t = self.table
            st = t.settings
            if self._seat_of(name) is not None:
                raise BetError("You're already at the table.")
            if None not in t.seats:
                raise BetError(f"The table is full ({MAX_SEATS} seats).")
            if type(buyin) is not int or not st["min_buyin"] <= buyin <= st["max_buyin"]:
                raise BetError(f"Buy in for {st['min_buyin']} to {st['max_buyin']} credits.")
            if b["balance"] < buyin:
                raise BetError("Not enough credits for that buy-in.")
            idx = t.seats.index(None)

            def run():
                self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance - ?, 2) WHERE name=?", (buyin, name))
                self.db.conn.execute("INSERT INTO poker_seats(bettor, seat, stack, joined_ts) VALUES(?,?,?,?)",
                                     (name, idx, buyin, now))
                self.db.conn.execute("INSERT INTO poker_buyins(bettor, kind, amount, created_ts) VALUES(?,?,?,?)",
                                     (name, "buyin", buyin, now))
            self._txn(run)
            t.seats[idx] = {"bettor": name, "stack": buyin, "ready": False, "timeouts": 0, "leaving": False}
            t.add_log("sit", name, now, amount=buyin)
            self.changed(t)
            return self.view(name)

    def leave(self, name):
        with self.lock:
            now = self.clock()
            name = self._bettor(name)["name"]
            idx = self._seat_of(name)
            if idx is None:
                raise BetError("You're not at the table.")
            t = self.table
            if self._in_hand(idx):
                t.seats[idx]["leaving"] = True
                t.seats[idx]["ready"] = False
                if idx not in t.hand["folded"] and idx not in t.hand["allin"]:  # all in, they stay in for the pot
                    self._fold_out(idx, now)
            else:
                self._cash_out(idx, now)
                self._maybe_start(now)
            self.changed(t)
            return self.view(name)

    def _fold_out(self, idx, now):
        """Fold seat `idx` now, in turn or not (leaving the table)."""
        h = self.table.hand
        h["folded"].add(idx)
        h["labels"][idx] = "Fold"
        h["step"] += 1
        self.table.add_log("fold", self.table.seats[idx]["bettor"], now)
        if h["to_act"] == idx:
            self._after_action(idx, now)
        elif len([s for s in h["seats"] if s not in h["folded"]]) == 1:
            self._settle(now)

    def topup(self, name, amount):
        with self.lock:
            now = self.clock()
            b = self._bettor(name)
            name = b["name"]
            t = self.table
            idx = self._seat_of(name)
            if idx is None:
                raise BetError("Take a seat first.")
            if not t.settings["rebuys"]:
                raise BetError("Top-ups are off at this table.")
            if self._in_hand(idx):
                raise BetError("Top up between hands.")
            room = t.settings["max_buyin"] - t.seats[idx]["stack"]
            if type(amount) is not int or not 1 <= amount <= room:
                raise BetError(f"You can add up to {max(room, 0)} credits (the table's maximum is {t.settings['max_buyin']}).")
            if b["balance"] < amount:
                raise BetError("Not enough credits for that top-up.")

            def run():
                self.db.conn.execute("UPDATE bettors SET balance=ROUND(balance - ?, 2) WHERE name=?", (amount, name))
                self.db.conn.execute("UPDATE poker_seats SET stack=stack + ? WHERE bettor=?", (amount, name))
                self.db.conn.execute("INSERT INTO poker_buyins(bettor, kind, amount, created_ts) VALUES(?,?,?,?)",
                                     (name, "topup", amount, now))
            self._txn(run)
            t.seats[idx]["stack"] += amount
            t.add_log("topup", name, now, amount=amount)
            self.changed(t)
            return self.view(name)

    def set_ready(self, name, ready):
        with self.lock:
            now = self.clock()
            name = self._bettor(name)["name"]
            idx = self._seat_of(name)
            if idx is None:
                raise BetError("Take a seat first.")
            seat = self.table.seats[idx]
            ready = bool(ready)
            if ready and seat["stack"] <= 0:
                raise BetError("You're out of chips. Top up or leave the table.")
            if seat["ready"] != ready:
                seat["ready"] = ready
                seat["timeouts"] = 0
                self.table.add_log("ready" if ready else "unready", name, now)
                self._maybe_start(now)
                self.changed(self.table)
            return self.view(name)

    def update_settings(self, name, new):
        with self.lock:
            now = self.clock()
            name = self._bettor(name)["name"]
            t = self.table
            if self._seat_of(name) is None:
                raise BetError("Take a seat to change the rules.")
            if t.phase != "lobby":
                raise BetError("The rules can change once the game stops: everyone un-readies, and it stops after the hand.")
            if not isinstance(new, dict):
                raise BetError("Send the rules to change.")
            settings = check_settings(new, t.settings)
            if settings != t.settings:
                t.settings = settings
                self.db.set_meta("poker_settings", settings)
                for seat in t.seats:
                    if seat:
                        seat["ready"] = False
                t.add_log("settings", name, now, settings=settings)
                self.changed(t)
            return self.view(name)

    # ---- dealing -----------------------------------------------------------------------
    def _maybe_start(self, now):
        t = self.table
        if t.phase != "lobby" or (t.hand and t.hand["street"] != "done"):
            return
        seated = [s for s in t.seats if s]
        if len(seated) >= 2 and all(s["ready"] for s in seated) and sum(s["stack"] > 0 for s in seated) >= 2:
            t.phase = "playing"
            t.add_log("start", None, now)
            self._start_hand(now)

    def _start_hand(self, now):
        t = self.table
        st = t.settings
        dealt = [i for i, s in enumerate(t.seats) if s and s["ready"] and s["stack"] > 0]
        if len(dealt) < 2:
            t.phase, t.hand, t.next_hand_at = "lobby", None, None
            t.add_log("stop", None, now)
            return
        t.button = order_from(dealt, t.button)[0]
        if len(dealt) == 2:  # heads-up: the button is the small blind and acts first before the flop
            sb, bb = t.button, order_from(dealt, t.button)[0]
        else:
            sb, bb = order_from(dealt, t.button)[:2]
        t.hands_dealt += 1
        h = t.hand = {
            "no": t.hands_dealt, "started_ts": now, "seats": dealt, "start": {i: t.seats[i]["stack"] for i in dealt},
            "deck": cards.shuffled(1), "hole": {i: [] for i in dealt}, "board": [], "street": "preflop",
            "bets": {i: 0 for i in dealt}, "contrib": {i: 0 for i in dealt}, "folded": set(), "allin": set(),
            "acted": set(), "current": st["big_blind"], "last_raise": st["big_blind"], "raises": 1,
            "to_act": None, "deadline": None, "step": 0, "button": t.button, "sb": sb, "bb": bb,
            "labels": {}, "settings": dict(st)}
        t.next_hand_at = None
        self._put(sb, st["small_blind"])
        self._put(bb, st["big_blind"])
        h["labels"] = {sb: f"Small blind {h['bets'][sb]}", bb: f"Big blind {h['bets'][bb]}"}
        for _ in range(2):
            for i in order_from(dealt, t.button):
                h["hole"][i].append(h["deck"].pop(0))
        t.add_log("hand", None, now, no=h["no"], players=len(dealt))
        nxt = self._next_to_act(bb)
        if nxt is None:
            self._end_street(now)
        else:
            self._turn(nxt, now)

    def _put(self, idx, amount):
        h, seat = self.table.hand, self.table.seats[idx]
        amount = min(amount, seat["stack"])
        seat["stack"] -= amount
        h["bets"][idx] += amount
        h["contrib"][idx] += amount
        if seat["stack"] == 0:
            h["allin"].add(idx)
        return amount

    def _needs_to_act(self, idx):
        h = self.table.hand
        return (idx not in h["folded"] and idx not in h["allin"]
                and (idx not in h["acted"] or h["bets"][idx] < h["current"]))

    def _next_to_act(self, after):
        h = self.table.hand
        return next((s for s in order_from(h["seats"], after) if self._needs_to_act(s)), None)

    def _turn(self, idx, now):
        h = self.table.hand
        h["to_act"] = idx
        h["deadline"] = now + self.table.settings["turn_seconds"]

    # ---- betting -------------------------------------------------------------------------
    def legal(self, idx):
        """What seat `idx` may do now: fold, check, call (amount), raise ({min, max} raise-to totals) or None."""
        t = self.table
        h = t.hand
        if not h or h["street"] == "done" or h["to_act"] != idx:
            return None
        st, seat = h["settings"], t.seats[idx]
        owe = h["current"] - h["bets"][idx]
        out = {"fold": True, "check": owe == 0, "call": min(owe, seat["stack"]) if owe > 0 else 0, "raise": None,
               "verb": "Bet" if h["current"] == 0 else "Raise"}
        others = [s for s in h["seats"] if s != idx and s not in h["folded"] and s not in h["allin"]]
        if idx in h["acted"] or seat["stack"] <= owe or not others:
            return out
        all_in_to = h["bets"][idx] + seat["stack"]
        if st["limit"] == "fixed":
            if h["raises"] >= FIXED_CAP:
                return out
            size = st["big_blind"] * (1 if h["street"] in ("preflop", "flop") else 2)
            lo = hi = h["current"] + size
        else:
            lo = h["current"] + h["last_raise"]
            if st["limit"] == "pot":
                hi = h["current"] + sum(h["contrib"].values()) + owe
            else:
                hi = all_in_to
        hi, lo = min(hi, all_in_to), min(lo, all_in_to)
        out["raise"] = {"min": lo, "max": max(lo, hi)}
        return out

    def act(self, name, action, amount=None, hand_no=None, step=None):
        with self.lock:
            now = self.clock()
            name = self._bettor(name)["name"]
            idx = self._seat_of(name)
            t = self.table
            h = t.hand
            if idx is None or not h or h["street"] == "done" or h["to_act"] != idx:
                raise BetError("It's not your turn.")
            if hand_no != h["no"] or step != h["step"]:
                raise BetError("The table moved on. Here's where it stands now.")
            legal = self.legal(idx)
            if action == "allin":  # all in: the biggest raise allowed, or a call when you can't raise
                if legal["raise"] and legal["raise"]["max"] == h["bets"][idx] + t.seats[idx]["stack"]:
                    action, amount = "raise", legal["raise"]["max"]
                elif legal["call"] == t.seats[idx]["stack"] and legal["call"] > 0:
                    action = "call"
                else:
                    raise BetError("You can't go all in here.")
            self._apply(idx, action, amount, legal, now)
            t.seats[idx]["timeouts"] = 0
            self._after_action(idx, now)
            self.changed(t)
            return self.view(name)

    def _apply(self, idx, action, amount, legal, now):
        t = self.table
        h, seat = t.hand, t.seats[idx]
        name = seat["bettor"]
        if action == "fold":
            h["folded"].add(idx)
            h["labels"][idx] = "Fold"
            t.add_log("fold", name, now)
        elif action == "check":
            if not legal["check"]:
                raise BetError(f"You need to call {legal['call']} or fold.")
            h["acted"].add(idx)
            h["labels"][idx] = "Check"
            t.add_log("check", name, now)
        elif action == "call":
            if not legal["call"]:
                raise BetError("There's nothing to call. Check instead.")
            paid = self._put(idx, legal["call"])
            h["acted"].add(idx)
            h["labels"][idx] = "All in" if seat["stack"] == 0 else f"Call {paid}"
            t.add_log("allin" if seat["stack"] == 0 else "call", name, now, amount=paid)
        elif action == "raise":
            r = legal["raise"]
            if not r:
                raise BetError("You can't raise now.")
            if type(amount) is not int or not r["min"] <= amount <= r["max"]:
                raise BetError(f"{legal['verb']} to between {r['min']} and {r['max']}." if r["min"] != r["max"]
                               else f"{legal['verb']} to {r['min']}.")
            verb = legal["verb"]
            self._put(idx, amount - h["bets"][idx])
            size = amount - h["current"]
            if size >= h["last_raise"]:  # a full raise reopens the betting
                h["last_raise"] = size
                h["acted"] = {idx}
                h["raises"] += 1
            else:  # an all-in short of a full raise: the others call or fold, but can't raise again
                h["acted"].add(idx)
            h["current"] = amount
            allin = seat["stack"] == 0
            h["labels"][idx] = "All in" if allin else f"{verb} {amount}" if verb == "Bet" else f"Raise to {amount}"
            t.add_log("allin" if allin else verb.lower(), name, now, amount=amount)
        else:
            raise BetError("Fold, check, call or raise.")
        h["step"] += 1

    def _after_action(self, idx, now):
        h = self.table.hand
        if len([s for s in h["seats"] if s not in h["folded"]]) == 1:
            return self._settle(now)
        nxt = self._next_to_act(idx)
        if nxt is not None:
            return self._turn(nxt, now)
        self._end_street(now)

    def _end_street(self, now):
        t = self.table
        h = t.hand
        h["bets"] = {i: 0 for i in h["seats"]}
        live = [s for s in h["seats"] if s not in h["folded"]]
        active = [s for s in live if s not in h["allin"]]
        if h["street"] == "river" or len(active) <= 1:
            while len(h["board"]) < 5:  # everyone's all in: run the board out
                self._deal_street(now)
            return self._settle(now)
        self._deal_street(now)
        h["current"], h["last_raise"], h["raises"], h["acted"], h["labels"] = 0, h["settings"]["big_blind"], 0, set(), {}
        nxt = self._next_to_act(h["button"])
        if nxt is None:
            return self._end_street(now)
        self._turn(nxt, now)

    def _deal_street(self, now):
        h = self.table.hand
        h["deck"].pop(0)  # burn
        n = 3 if not h["board"] else 1
        h["board"].extend(h["deck"].pop(0) for _ in range(n))
        h["street"] = STREETS[min(len(STREETS) - 1, {3: 1, 4: 2, 5: 3}[len(h["board"])])]
        self.table.add_log(h["street"], None, now, board=list(h["board"]))

    # ---- the end of a hand ----------------------------------------------------------------
    def _settle(self, now):
        t = self.table
        h = t.hand
        st = h["settings"]
        live = [s for s in h["seats"] if s not in h["folded"]]
        contrib = dict(h["contrib"])
        # An uncalled bet goes back to whoever made it.
        ranked = sorted(contrib.values(), reverse=True)
        top = max(contrib, key=lambda s: contrib[s])
        if len(ranked) > 1 and ranked[0] > ranked[1]:
            back = ranked[0] - ranked[1]
            contrib[top] -= back
            t.seats[top]["stack"] += back
        pot = sum(contrib.values())
        rake = rake_for(pot, len(h["board"]) >= 3)
        pots = build_pots(contrib, live)
        max(pots, key=lambda p: p["amount"])["amount"] -= rake
        showdown = len(live) > 1
        scores = {s: cards.best_hand(h["hole"][s] + h["board"]) for s in live} if showdown else {}
        order = order_from(h["seats"], h["button"])  # odd chips go to the first winner left of the button
        won = {s: 0 for s in h["seats"]}
        pot_rows = []
        for p in pots:
            elig = [s for s in p["eligible"] if s in live]
            if len(elig) > 1:
                best = max(scores[s][0] for s in elig)
                winners = [s for s in order if s in elig and scores[s][0] == best]
            else:
                winners = elig
            share, odd = divmod(p["amount"], len(winners))
            for k, s in enumerate(winners):
                won[s] += share + (1 if k < odd else 0)
            pot_rows.append({"amount": p["amount"], "winners": [t.seats[s]["bettor"] for s in winners],
                             "hand": cards.describe(scores[winners[0]][0]) if showdown and len(elig) > 1 else None})
        for s, amount in won.items():
            t.seats[s]["stack"] += amount
        players = []
        for s in h["seats"]:
            seat = t.seats[s]
            shown = showdown and s in live
            players.append({"seat": s, "bettor": seat["bettor"], "start": h["start"][s], "end": seat["stack"],
                            "net": seat["stack"] - h["start"][s], "won": won[s],
                            "cards": h["hole"][s] if shown else None,
                            "hand": cards.describe(scores[s][0]) if shown else None,
                            "best": scores[s][1] if shown else None})

        def run():
            cur = self.db.conn.execute(
                "INSERT INTO poker_hands(started_ts, ended_ts, settings, board, players, pots, pot, rake) VALUES(?,?,?,?,?,?,?,?)",
                (h["started_ts"], now, json.dumps(st), json.dumps(h["board"]), json.dumps(players), json.dumps(pot_rows), pot, rake))
            hid = cur.lastrowid
            for p in players:
                self.db.conn.execute("INSERT INTO poker_results(hand_id, bettor, start, end, net) VALUES(?,?,?,?,?)",
                                     (hid, p["bettor"], p["start"], p["end"], p["net"]))
                self.db.conn.execute("UPDATE poker_seats SET stack=? WHERE bettor=?", (p["end"], p["bettor"]))
            house.record(self.db.conn, "poker", f"hand:{hid}", None, 0, rake, None, now)
            return hid
        hid = self._txn(run)
        h["street"], h["to_act"], h["deadline"] = "done", None, None
        t.last = {"no": h["no"], "id": hid, "board": list(h["board"]), "pots": pot_rows, "rake": rake, "pot": pot,
                  "showdown": showdown, "players": players, "ended_ts": now}
        winners = [p for p in players if p["won"] > 0]
        for p in winners:
            t.add_log("win", p["bettor"], now, amount=p["won"], net=p["net"], hand=p["hand"], pot=pot,
                      split=len(winners) > 1)
        t.next_hand_at = now + SHOWDOWN_S
        for s in h["seats"]:  # seats out of chips sit out until they top up; leavers cash out now
            seat = t.seats[s]
            if seat["leaving"]:
                self._cash_out(s, now)
            elif seat["stack"] <= 0:
                seat["ready"] = False
                t.add_log("bust", seat["bettor"], now)

    # ---- the clock ---------------------------------------------------------------------------
    def tick(self, now=None):
        """Time out the player to act, and deal the next hand once the pause after a hand is over."""
        with self.lock:
            now = now or self.clock()
            t = self.table
            h = t.hand
            if h and h["street"] != "done" and h["deadline"] and now >= h["deadline"]:
                idx = h["to_act"]
                seat = t.seats[idx]
                legal = self.legal(idx)
                seat["timeouts"] += 1
                t.add_log("timeout", seat["bettor"], now)
                self._apply(idx, "check" if legal["check"] else "fold", None, legal, now)
                if seat["timeouts"] >= TIMEOUTS_TO_SIT_OUT and seat["ready"]:
                    seat["ready"] = False
                    t.add_log("sit_out", seat["bettor"], now)
                self._after_action(idx, now)
            elif t.next_hand_at and now >= t.next_hand_at:
                t.next_hand_at = None
                if t.phase == "playing":
                    self._start_hand(now)  # back to the lobby when fewer than two are ready
                else:
                    t.hand = None
            else:
                return
            self.changed(t)

    # ---- what the page sees ------------------------------------------------------------------
    def view(self, name=None):
        with self.lock:
            t = self.table
            h = t.hand
            me_idx = self._seat_of(name) if name else None
            done = bool(h and h["street"] == "done")
            shown = {p["seat"]: p["cards"] for p in (t.last or {}).get("players", []) if p["cards"]} if done else {}
            seats = []
            for i, s in enumerate(t.seats):
                if not s:
                    seats.append(None)
                    continue
                dealt = bool(h and i in h["seats"])
                hole = h["hole"][i] if dealt else []
                cards_out = hole if (i == me_idx or i in shown) else None
                seats.append({
                    "seat": i, "bettor": s["bettor"], "stack": s["stack"], "ready": s["ready"], "leaving": s["leaving"],
                    "timeouts": s["timeouts"], "dealt": dealt, "folded": dealt and i in h["folded"],
                    "allin": dealt and i in h["allin"], "bet": h["bets"][i] if dealt else 0,
                    "label": h["labels"].get(i) if dealt else None, "button": bool(h and h["button"] == i),
                    "sb": bool(h and h["sb"] == i), "bb": bool(h and h["bb"] == i),
                    "cards": cards_out, "hidden": len(hole) if dealt and cards_out is None and i not in h["folded"] else 0,
                    "to_act": bool(h and not done and h["to_act"] == i)})
            hand = None
            if h:
                hand = {"no": h["no"], "street": h["street"], "board": h["board"], "pot": sum(h["contrib"].values()),
                        "current": h["current"], "to_act": h["to_act"], "deadline": h["deadline"], "step": h["step"],
                        "limit": h["settings"]["limit"]}
            me = None
            if name:
                b = self.db.get_bettor(name)
                me = {"name": b["name"] if b else name, "balance": round(b["balance"], 2) if b else 0,
                      "seat": me_idx, "season": self.season(b["name"]) if b else None}
                if me_idx is not None:
                    seat = t.seats[me_idx]
                    me.update({"stack": seat["stack"], "ready": seat["ready"], "legal": self.legal(me_idx),
                               "in_hand": self._in_hand(me_idx),
                               "topup_room": max(0, t.settings["max_buyin"] - seat["stack"])})
            return {"phase": t.phase, "version": t.version, "server_time": self.clock(), "settings": t.settings,
                    "seats": seats, "hand": hand, "last": t.last, "next_hand_at": t.next_hand_at, "log": list(t.log),
                    "me": me, "max_seats": MAX_SEATS,
                    "choices": {"limits": LIMITS, "blinds": BLINDS, "turns": TURN_CHOICES, "buyin_min": BUYIN_MIN,
                                "buyin_max": BUYIN_MAX, "min_buyin_blinds": MIN_BUYIN_BLINDS},
                    "rake": {"rate": RAKE_RATE, "cap": RAKE_CAP}}

    def season(self, name):
        r = self.db.query_one(
            "SELECT COUNT(*) AS hands, COALESCE(SUM(net),0) AS net, COUNT(CASE WHEN net > 0 THEN 1 END) AS won, "
            "COALESCE(MAX(net),0) AS best FROM poker_results WHERE bettor=? AND season_id IS NULL", (name,))
        return r
