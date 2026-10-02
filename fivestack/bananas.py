"""Bananas: the shop's currency, earned by playing and never turned back into credits.

Every Competitive game a squad member plays pays `banana_per_game` bananas (5 by default), win or lose, whoever
they queued with: a solo game counts as much as a squad game. Credits are different: they come only from squad
games (game rewards) and from betting. Bananas are whole numbers. They only ever go into banana_ledger; nothing
here writes to `bettors`, `bets` or `rewards`, so the shop can't move the leaderboard.

`earn()` (db.earn_game_bananas) pays every member line in `member_games` that started after meta `bananas_since`
(set the first time it runs, so an upgrade doesn't pay for the whole backlog) and isn't paid yet, so it can run
after every sync and at start-up; the ledger's unique (reason, ref) keeps it from paying twice. Wallets left with a
fraction from the old per-credit scheme get one rounding row (db.round_bananas). A season reset zeroes every wallet
(db.archive_and_reset) but owned items stay.

The catalogue is code, not data: CATALOG lists every item with its slot, price and `look` (what the page needs to
draw it). Cosmetic slots hold one worn item each; SOCIAL items are used on another bettor and wear off. Slots come in
groups (GROUPS): your looks across the site, and Onkey's Casino, which everyone at the casino tables sees (card backs,
chips, seat style, the line Onkey announces you with, and what bursts from your seat when you win).
"""
import time

from .bets import BetError

GROUPS = [
    # key, label, what the group is for
    ("looks", "Your looks", "How you show up across the site."),
    ("casino", "Onkey's Casino", "Your style at the casino tables. Everyone sitting with you sees it."),
]
SLOTS = [
    # key, label, what it changes, group
    ("name_color", "Name colour", "How your name is written everywhere: rankings, tickets, the casino tables, the Monkeys page.", "looks"),
    ("badge", "Badge", "An emoji next to your name.", "looks"),
    ("title", "Title", "A title under your name on the Monkeys page, your profile and your casino seat.", "looks"),
    ("banner", "Profile banner", "The header of your profile page.", "looks"),
    ("ticket", "Ticket style", "Your bet tickets on Odds & Bets and Bettors, for everyone to see.", "looks"),
    ("celebration", "Win celebration", "What bursts out of your balance when a bet wins.", "looks"),
    ("theme", "Site theme", "Unlocks a theme for the ◐ button (on your devices).", "looks"),
    ("card_back", "Card backs", "Your face-down cards at the poker table, and Onkey's hole card at your solo blackjack table.", "casino"),
    ("chips", "Chips", "Your chips at the tables: your bets at poker, your stake at blackjack.", "casino"),
    ("seat", "Seat style", "Your nameplate at the poker table and your spot at the blackjack table.", "casino"),
    ("entrance", "Entrance", "What Onkey announces when you sit down at a shared table, for the whole table to hear.", "casino"),
    ("table_win", "Table win", "What bursts out of your seat when you win a pot or a blackjack hand, for everyone watching.", "casino"),
]
SLOT_KEYS = [s[0] for s in SLOTS]


def _item(id_, slot, name, price, desc, **look):
    return {"id": id_, "slot": slot, "name": name, "price": price, "desc": desc, "look": look}


CATALOG = [
    _item("nc-peel", "name_color", "Ripe", 120, "Fresh banana yellow.", cls="nc-peel"),
    _item("nc-jungle", "name_color", "Jungle", 120, "Deep canopy green.", cls="nc-jungle"),
    _item("nc-onkey", "name_color", "Onkey Brown", 200, "Onkey's fur fading into his muzzle.", cls="nc-onkey"),
    _item("nc-sunset", "name_color", "Tropical Sunset", 250, "Mango to hibiscus.", cls="nc-sunset"),
    _item("nc-gold", "name_color", "Golden Banana", 900, "A shimmer that never stops.", cls="nc-gold"),
    _item("nc-ice", "name_color", "Ice Cold", 150, "Frozen blue, for clutch nerves.", cls="nc-ice"),
    _item("nc-toxic", "name_color", "Toxic", 150, "Radioactive lime.", cls="nc-toxic"),
    _item("nc-valorant", "name_color", "Valorant Red", 200, "The red from the menu screen.", cls="nc-valorant"),
    _item("nc-lava", "name_color", "Lava", 250, "Molten orange into red.", cls="nc-lava"),
    _item("nc-galaxy", "name_color", "Galaxy", 300, "Deep purple, blue and pink.", cls="nc-galaxy"),
    _item("nc-chrome", "name_color", "Chrome", 400, "Polished silver.", cls="nc-chrome"),
    _item("nc-rainbow", "name_color", "Rainbow", 1200, "Every colour, always moving.", cls="nc-rainbow"),

    _item("bd-banana", "badge", "Banana", 40, "The classic.", emoji="🍌"),
    _item("bd-monkey", "badge", "Monkey", 60, "One of the troop.", emoji="🐒"),
    _item("bd-noevil", "badge", "See No Evil", 60, "For when you saw that bet.", emoji="🙈"),
    _item("bd-coconut", "badge", "Coconut", 60, "Hard shell, soft middle.", emoji="🥥"),
    _item("bd-palm", "badge", "Palm Tree", 80, "Island time.", emoji="🌴"),
    _item("bd-gorilla", "badge", "Silverback", 150, "Big and in charge.", emoji="🦍"),
    _item("bd-crown", "badge", "Crown", 400, "Say less.", emoji="👑"),
    _item("bd-fire", "badge", "On Fire", 80, "Can't miss right now.", emoji="🔥"),
    _item("bd-skull", "badge", "Skull", 80, "Dead inside, or making others so.", emoji="💀"),
    _item("bd-target", "badge", "Bullseye", 100, "Heads only.", emoji="🎯"),
    _item("bd-brain", "badge", "Big Brain", 100, "Util lineups memorised.", emoji="🧠"),
    _item("bd-clover", "badge", "Lucky Clover", 100, "Parlays hit different.", emoji="🍀"),
    _item("bd-bolt", "badge", "Lightning", 120, "Entry in 0.2 seconds.", emoji="⚡"),
    _item("bd-clown", "badge", "Clown", 120, "Owning it.", emoji="🤡"),
    _item("bd-rocket", "badge", "Rocket", 150, "Straight up the leaderboard.", emoji="🚀"),
    _item("bd-goat", "badge", "GOAT", 300, "Bold. Hope you can back it up.", emoji="🐐"),
    _item("bd-diamond", "badge", "Diamond", 500, "Hands, rank, or both.", emoji="💎"),

    _item("tt-cheeky", "title", "Cheeky Monkey", 100, "Always up to something.", text="Cheeky Monkey"),
    _item("tt-barrel", "title", "Barrel of Monkeys", 120, "More fun than one.", text="Barrel of Monkeys"),
    _item("tt-eco", "title", "Eco Monkey", 120, "Saves on everything but bets.", text="Eco Monkey"),
    _item("tt-top", "title", "Top Banana", 250, "Head of the bunch.", text="Top Banana"),
    _item("tt-parlay", "title", "Parlay Primate", 250, "Never met a leg they didn't like.", text="Parlay Primate"),
    _item("tt-silverback", "title", "Silverback", 400, "Leader of the troop.", text="Silverback"),
    _item("tt-king", "title", "King of the Jungle", 800, "Technically a lion's title. Took it anyway.", text="King of the Jungle"),
    _item("tt-baiter", "title", "Certified Baiter", 100, "Someone has to go second.", text="Certified Baiter"),
    _item("tt-spike", "title", "Spike Planter", 100, "Carries the bomb, carries the team.", text="Spike Planter"),
    _item("tt-hoarder", "title", "Ult Hoarder", 120, "Saving it for the right moment. Any day now.", text="Ult Hoarder"),
    _item("tt-ecofrag", "title", "Eco Frag Enjoyer", 150, "Sheriff diffs only.", text="Eco Frag Enjoyer"),
    _item("tt-entry", "title", "Entry Fragger", 150, "First through the door.", text="Entry Fragger"),
    _item("tt-anchor", "title", "The Anchor", 200, "Holds the site alone.", text="The Anchor"),
    _item("tt-lurk", "title", "Lurk God", 200, "Nobody knows where they are. Including the team.", text="Lurk God"),
    _item("tt-degen", "title", "Degenerate Gambler", 250, "Every leg, every game.", text="Degenerate Gambler"),
    _item("tt-clutch", "title", "Clutch Merchant", 350, "1v3? Light work.", text="Clutch Merchant"),
    _item("tt-radiant", "title", "Radiant (In Spirit)", 500, "The rank will catch up eventually.", text="Radiant (In Spirit)"),

    _item("bn-canopy", "banner", "Canopy", 300, "Sunlight through the leaves.", cls="bn-canopy"),
    _item("bn-split", "banner", "Banana Split", 300, "Yellow and brown stripes.", cls="bn-split"),
    _item("bn-sunset", "banner", "Sunset Beach", 350, "Last light over the water.", cls="bn-sunset"),
    _item("bn-onkey", "banner", "Onkey", 600, "The man himself, across your profile.", cls="bn-onkey"),
    _item("bn-arctic", "banner", "Arctic", 300, "Ice shelves and a pale sky.", cls="bn-arctic"),
    _item("bn-valorant", "banner", "Protocol", 350, "Red and black slashes.", cls="bn-valorant"),
    _item("bn-lava", "banner", "Lava Flow", 400, "It's getting hot in here.", cls="bn-lava"),
    _item("bn-stars", "banner", "Starfield", 450, "A night sky full of stars.", cls="bn-stars"),
    _item("bn-neon", "banner", "Neon Grid", 500, "An 80s horizon, forever.", cls="bn-neon"),

    _item("tk-peel", "ticket", "Peel", 300, "Tickets printed on banana yellow.", cls="tk-peel"),
    _item("tk-leaf", "ticket", "Banana Leaf", 300, "Wrapped in green.", cls="tk-leaf"),
    _item("tk-vines", "ticket", "Jungle Vines", 400, "Vines growing over the edge.", cls="tk-vines"),
    _item("tk-gold", "ticket", "Gold Foil", 600, "A foil border that catches the light.", cls="tk-gold"),

    _item("cb-jungle", "celebration", "Jungle Confetti", 250, "Greens and yellows.",
          colors=["#2f9a45", "#7ac943", "#f5c518", "#ffe066", "#1e6b30"]),
    _item("cb-bananas", "celebration", "Banana Rain", 350, "It rains bananas.", emoji=["🍌"]),
    _item("cb-island", "celebration", "Island Shower", 350, "Coconuts, palms and monkeys.", emoji=["🥥", "🌴", "🐒"]),

    _item("th-greg", "theme", "Greg Mode", 750, "Greg behind every page, and a little Greg falls from every click.", theme="greg"),
    _item("th-onkey", "theme", "Onkey Mode", 1000, "Warm browns and tan, with Onkey watching.", theme="onkey"),
    _item("th-jungle", "theme", "Jungle Mode", 1000, "A dark canopy with banana-yellow bars.", theme="jungle"),
    _item("th-sakura", "theme", "Sakura", 800, "Soft pinks, cherry-blossom light.", theme="sakura"),
    _item("th-midnight", "theme", "Midnight", 900, "Deep navy with ice-blue accents.", theme="midnight"),
    _item("th-terminal", "theme", "Terminal", 1000, "Green on black. You're in.", theme="terminal"),
    _item("th-synthwave", "theme", "Synthwave", 1200, "Purple night, hot-pink neon.", theme="synthwave"),

    # Onkey's Casino. cls: a class on your cards, chips or seat (style.css); text: Onkey's line ({name} is you);
    # emoji / colors: the burst from your seat.
    _item("cbk-banana", "card_back", "Banana Leaf", 150, "Yellow and green, like a fresh bunch.", cls="cbk-banana"),
    _item("cbk-jungle", "card_back", "Canopy", 150, "Deep green leaves.", cls="cbk-jungle"),
    _item("cbk-midnight", "card_back", "Midnight", 200, "Navy, with a sprinkle of stars.", cls="cbk-midnight"),
    _item("cbk-onkey", "card_back", "Onkey Original", 350, "Onkey's face on every card. He's watching.", cls="cbk-onkey"),
    _item("cbk-neon", "card_back", "Neon", 300, "Hot pink and cyan stripes.", cls="cbk-neon"),
    _item("cbk-gold", "card_back", "Gold Leaf", 600, "A gold back that shimmers when it moves.", cls="cbk-gold"),

    _item("chp-jungle", "chips", "Jungle Chips", 100, "Green and cream.", cls="chp-jungle"),
    _item("chp-ice", "chips", "Ice Chips", 120, "Frosted blue.", cls="chp-ice"),
    _item("chp-ruby", "chips", "Ruby Chips", 150, "Deep red, white edge.", cls="chp-ruby"),
    _item("chp-banana", "chips", "Banana Chips", 200, "Yellow with brown spots. Not edible.", cls="chp-banana"),
    _item("chp-onyx", "chips", "Onyx and Gold", 400, "Black chips with a gold rim. High roller only.", cls="chp-onyx"),
    _item("chp-rainbow", "chips", "Rainbow Chips", 700, "Every colour, always turning.", cls="chp-rainbow"),

    _item("st-wood", "seat", "Mahogany", 150, "A polished wooden plate.", cls="st-wood"),
    _item("st-vines", "seat", "Overgrown", 200, "Vines creeping round your seat.", cls="st-vines"),
    _item("st-velvet", "seat", "Velvet Rope", 250, "Plush purple, VIP section.", cls="st-velvet"),
    _item("st-neon", "seat", "Neon Sign", 350, "Your seat lit up in pink neon.", cls="st-neon"),
    _item("st-gold", "seat", "Gold Throne", 800, "Gold all round. Nobody else gets this seat.", cls="st-gold"),

    _item("en-bananas", "entrance", "Banana Delivery", 80, "Onkey thanks you for the snacks.",
          text="{name} brought bananas for the whole table. Onkey approves."),
    _item("en-shark", "entrance", "Shark Warning", 120, "Everyone checks their wallet.",
          text="Careful, everybody. A shark just sat down. Hello, {name}."),
    _item("en-highroller", "entrance", "High Roller", 200, "Make way.",
          text="Make way! The high roller {name} has arrived."),
    _item("en-fish", "entrance", "Fresh Fish", 60, "Lean into it.",
          text="Fresh fish at the table! Go easy on {name}. Or don't."),
    _item("en-royal", "entrance", "Royal Arrival", 400, "All rise.",
          text="All rise for their Royal Highness, {name}, ruler of the jungle."),
    _item("en-legend", "entrance", "Living Legend", 600, "Onkey loses his mind a little.",
          text="Is that... it is! {name}! The legend! Onkey needs an autograph!"),

    _item("tw-coins", "table_win", "Coin Shower", 150, "A shower of gold coins.", emoji=["🪙", "💰"]),
    _item("tw-bananas", "table_win", "Banana Split", 200, "Bananas everywhere.", emoji=["🍌"]),
    _item("tw-fire", "table_win", "On Fire", 250, "The seat is too hot to touch.", emoji=["🔥", "💥"]),
    _item("tw-confetti", "table_win", "Gold Confetti", 300, "Gold and cream confetti.",
          colors=["#ffd700", "#ffe082", "#fff3c4", "#e6a800", "#ffffff"]),
    _item("tw-crown", "table_win", "Crowned", 500, "Crowns and diamonds, for royalty.", emoji=["👑", "💎", "✨"]),
]

# Used on another bettor. games: wears off once that many 5-stack games have started since (or after `hours`,
# whichever comes first; 0 = time only). text: the buyer writes something (max `max_len` characters).
# look: what the page draws: `cls` on their name, `ticket_cls` on their bet tickets,
# `avatar` in place of their badge, `mark` = show the emoji next to their name; `kind` names the ones drawn specially.
PRANK_GAMES = 3
SOCIAL = [
    {"id": "sc-peel", "name": "Banana Peel", "price": 40, "hours": 168, "games": PRANK_GAMES,
     "desc": f"Their name slips and wobbles for the next {PRANK_GAMES} games.", "look": {"cls": "slipped", "emoji": "🍌", "mark": True}},
    {"id": "sc-jinx", "name": "Jinx", "price": 60, "hours": 168, "games": PRANK_GAMES,
     "desc": f"Frost on their name and bet tickets for the next {PRANK_GAMES} games.",
     "look": {"cls": "jinxed", "ticket_cls": "jinxed", "emoji": "🥶", "mark": True}},
    {"id": "sc-upside", "name": "Upside Down", "price": 45, "hours": 168, "games": PRANK_GAMES,
     "desc": f"Flip their name on its head for the next {PRANK_GAMES} games.", "look": {"cls": "flipped", "emoji": "🙃", "mark": True}},
    {"id": "sc-shrink", "name": "Shrink Ray", "price": 35, "hours": 168, "games": PRANK_GAMES,
     "desc": f"Their name goes tiny for the next {PRANK_GAMES} games.", "look": {"cls": "shrunk", "emoji": "🔬", "mark": True}},
    {"id": "sc-fog", "name": "Smoke Screen", "price": 55, "hours": 168, "games": PRANK_GAMES,
     "desc": f"Their name hides in smoke until you hover it, for {PRANK_GAMES} games.", "look": {"cls": "fogged", "emoji": "💨", "mark": True}},
    {"id": "sc-clown", "name": "Clown Makeup", "price": 50, "hours": 168, "games": PRANK_GAMES,
     "desc": f"Their badge and avatar become a clown for the next {PRANK_GAMES} games.", "look": {"avatar": "🤡", "emoji": "🤡"}},
    {"id": "sc-glitter", "name": "Glitter Bomb", "price": 70, "hours": 168, "games": PRANK_GAMES,
     "desc": f"Sparkles all over their name and bet tickets for {PRANK_GAMES} games. It never comes off.",
     "look": {"cls": "glittered", "ticket_cls": "glitter", "emoji": "✨", "mark": True}},
    {"id": "sc-bounty", "name": "Bounty", "price": 75, "hours": 168, "games": PRANK_GAMES,
     "desc": f"A bounty poster on their profile and bet tickets for {PRANK_GAMES} games, with your name on it.",
     "look": {"kind": "bounty", "ticket_cls": "bountied", "emoji": "🎯", "mark": True}},
    {"id": "sc-heckle", "name": "Heckle", "price": 30, "hours": 168, "games": PRANK_GAMES, "max_len": 60,
     "desc": f"A speech bubble on their bet tickets for {PRANK_GAMES} games. Say it with your chest.", "look": {"kind": "heckle", "emoji": "📣"}},
    {"id": "sc-nick", "name": "Nickname", "price": 60, "hours": 24, "games": 0, "max_len": 20,
     "desc": "Add a nickname in quotes after their name for 24 hours.", "look": {"kind": "nick", "emoji": "🗯️"}},
    {"id": "sc-note", "name": "Wall Note", "price": 25, "hours": 72, "games": 0, "max_len": 80,
     "desc": "Pin a note on their profile for 3 days.", "look": {"emoji": "📌"}},
    {"id": "sc-title", "name": "Title Swap", "price": 80, "hours": 24, "games": 0, "max_len": 24,
     "desc": "Give them a title of your choosing for 24 hours.", "look": {"emoji": "🏷️"}},
]
# Everything the page lists follows this order: by slot, then cheapest first (ties by name).
CATALOG.sort(key=lambda i: (SLOT_KEYS.index(i["slot"]), i["price"], i["name"]))
SOCIAL.sort(key=lambda i: (i["price"], i["name"]))
ITEMS = {i["id"]: i for i in CATALOG}
SOCIAL_ITEMS = {i["id"]: i for i in SOCIAL}


class BananaManager:
    def __init__(self, cfg, db, bets, accounts=None):
        self.db = db
        self.bets = bets
        self.per_game = max(0, int(cfg.get("banana_per_game", 5)))
        self.starting = max(0, int(cfg.get("starting_bananas", 50)))
        # Who gets paid for a member's games: a callable returning {puuid: bettor account name}.
        self.accounts = accounts or (lambda: {})

    def since(self):
        """Games that started before this never pay (set the first time bananas are paid, like rewards_since)."""
        since = self.db.get_meta("bananas_since")
        if since is None:
            since = time.time()
            self.db.set_meta("bananas_since", since)
        return since

    def earn(self):
        """Give any account without this season's starting bananas its `starting_bananas`, then pay bananas for every
        game played and not paid yet (see the module docstring), and round away any fraction left from the old
        per-credit scheme. Returns how many ledger rows were added."""
        added = self.db.grant_starting_bananas(self.starting) if self.starting > 0 else 0
        if self.per_game > 0:
            added += self.db.earn_game_bananas(self.per_game, self.accounts(), self.since())
        added += self.db.round_bananas()
        return added

    # ---- reading ---------------------------------------------------------------
    def looks(self):
        """What the page needs to draw any bettor's name: worn items and active pranks, keyed by lower-cased name."""
        out = {}
        for name, slots in self.db.banana_equipped().items():
            out[name] = {"worn": {slot: ITEMS[i]["look"] | {"id": i, "name": ITEMS[i]["name"]}
                                  for slot, i in slots.items() if i in ITEMS}}
        for p in self.db.banana_pranks():
            item = SOCIAL_ITEMS.get(p["item_id"])
            if not item:
                continue
            entry = out.setdefault(p["target"].lower(), {"worn": {}})
            entry.setdefault("pranks", []).append({"id": p["id"], "item": p["item_id"], "name": item["name"], "by": p["bettor"],
                                                   "text": p["text"], "expires_ts": p["expires_ts"],
                                                   "games": p["games"], "games_left": max(0, p["games"] - p["games_since"]) if p["games"] else None,
                                                   **item["look"]})
        return out

    def wallet(self, name):
        return int(round(self.db.banana_wallet(name)))

    def summary(self, name, totals=None):
        t = (totals if totals is not None else self.db.banana_totals()).get(name.lower()) or {}
        whole = lambda k: int(round(t.get(k) or 0))  # noqa: E731
        return {"wallet": whole("wallet"), "earned": whole("earned"), "season_earned": whole("season_earned"),
                "season_games": whole("season_games"), "spent": whole("spent")}

    def shop(self, me=None):
        """The catalogue, plus the signed-in bettor's wallet, items and recent history."""
        out = {"per_game": self.per_game, "groups": [{"key": k, "label": l, "desc": d} for k, l, d in GROUPS],
               "slots": [{"key": k, "label": l, "desc": d, "group": g} for k, l, d, g in SLOTS],
               "catalog": CATALOG, "social": SOCIAL, "me": None}
        if me:
            owned = {r["item_id"] for r in self.db.banana_items(me["name"])}
            out["me"] = {"name": me["name"], **self.summary(me["name"]), "owned": sorted(owned),
                         "worn": self.db.banana_equipped().get(me["name"].lower(), {}),
                         "history": self.db.banana_history(me["name"], 25)}
        return out

    def troop(self):
        """Every bettor with their bananas and items, most spent first (the shop's rankings)."""
        totals = self.db.banana_totals()
        items = {}
        for r in self.db.banana_items():
            items.setdefault(r["bettor"].lower(), []).append(r["item_id"])
        board = {b["name"].lower(): b for b in self.bets.leaderboard()}
        rows = []
        for b in self.db.bettors():
            k = b["name"].lower()
            own = [i for i in items.get(k, []) if i in ITEMS]
            rows.append({"name": b["name"], "claimed": bool(b.get("password_hash")), **self.summary(b["name"], totals),
                         "items": len(own), "collection": sum(ITEMS[i]["price"] for i in own),
                         "credits": round(b["balance"], 2), "profit": board.get(k, {}).get("profit")})
        rows.sort(key=lambda r: (-r["collection"], -r["wallet"], r["name"].lower()))
        return {"per_game": self.per_game, "catalog_size": len(CATALOG), "troop": rows, "looks": self.looks()}

    def profile(self, name):
        b = self.db.get_bettor(name)
        if not b:
            raise BetError("No bettor with that name.")
        name = b["name"]
        owned = [dict(r, **{k: ITEMS[r["item_id"]][k] for k in ("slot", "name", "look", "desc")})
                 for r in self.db.banana_items(name) if r["item_id"] in ITEMS]
        board = next((r for r in self.bets.leaderboard() if r["name"].lower() == name.lower()), {})
        pranks = []
        for p in self.db.banana_pranks(active_only=False, target=name, limit=30):
            item = SOCIAL_ITEMS.get(p["item_id"])
            if item:
                pranks.append({**p, "name": item["name"], "look": item["look"]})
        active = {p["id"] for p in self.db.banana_pranks(target=name)}
        for p in pranks:
            p["active"] = p["id"] in active
        by_me = [dict(p, name=SOCIAL_ITEMS[p["item_id"]]["name"]) for p in self.db.query(
            "SELECT * FROM banana_pranks WHERE bettor=? ORDER BY id DESC LIMIT 15", (name,)) if p["item_id"] in SOCIAL_ITEMS]
        return {"name": name, "claimed": bool(b.get("password_hash")), "created_at": b.get("created_at"), "per_game": self.per_game,
                **self.summary(name), "owned": owned, "worn": self.db.banana_equipped().get(name.lower(), {}),
                "collection": sum(o["price"] for o in owned), "catalog_size": len(CATALOG),
                "pranks": pranks, "pranks_sent": by_me,
                "betting": {k: board.get(k) for k in ("balance", "profit", "rewards", "won", "lost", "roi", "pending")},
                "looks": self.looks().get(name.lower(), {"worn": {}})}

    # ---- spending ---------------------------------------------------------------
    def buy(self, name, item_id, target=None, text=None):
        """Buy a shop item (worn straight away) or use a social item on `target`. Only bananas change hands."""
        if item_id in SOCIAL_ITEMS:
            return self._prank(name, SOCIAL_ITEMS[item_id], target, text)
        item = ITEMS.get(item_id)
        if not item:
            raise BetError("That item isn't in the shop.")
        with self.db.lock:
            if self.db.query_one("SELECT 1 FROM banana_items WHERE bettor=? AND item_id=?", (name, item_id)):
                raise BetError("You already own that.")
            try:
                if not self.db.spend_bananas(name, item["price"], "purchase", f"{item_id}:{time.time_ns()}", item["name"]):
                    raise BetError(f"{item['name']} costs {item['price']} bananas; you have {self.wallet(name):g}.")
                self.db.conn.execute("INSERT INTO banana_items(bettor, item_id, price, bought_ts) VALUES(?,?,?,?)",
                                     (name, item_id, item["price"], time.time()))
                self.db.conn.execute("INSERT OR REPLACE INTO banana_equipped(bettor, slot, item_id) VALUES(?,?,?)",
                                     (name, item["slot"], item_id))
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        return {"item": item, "wallet": self.wallet(name)}

    def _prank(self, name, item, target, text):
        t = self.db.get_bettor((target or "").strip()) if target else None
        if not t:
            raise BetError("Pick who to use it on.")
        if t["name"].lower() == name.lower():
            raise BetError("That one is for somebody else.")
        text = " ".join(str(text or "").split())
        if item.get("max_len"):
            if not text:
                raise BetError("Write something first.")
            if len(text) > item["max_len"]:
                raise BetError(f"Keep it to {item['max_len']} characters.")
        else:
            text = None
        now = time.time()
        with self.db.lock:
            try:
                if not self.db.spend_bananas(name, item["price"], "prank", f"{item['id']}:{time.time_ns()}", f"{item['name']} on {t['name']}"):
                    raise BetError(f"{item['name']} costs {item['price']} bananas; you have {self.wallet(name):g}.")
                self.db.conn.execute(
                    "INSERT INTO banana_pranks(item_id, bettor, target, text, price, created_ts, expires_ts, games) VALUES(?,?,?,?,?,?,?,?)",
                    (item["id"], name, t["name"], text, item["price"], now, now + item["hours"] * 3600, int(item["games"])))
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        return {"item": item, "target": t["name"], "wallet": self.wallet(name)}

    def equip(self, name, slot, item_id):
        """Wear an owned item in its slot, or take the slot off (item_id empty)."""
        if slot not in SLOT_KEYS:
            raise BetError("Unknown slot.")
        if item_id:
            item = ITEMS.get(item_id)
            if not item or item["slot"] != slot:
                raise BetError("That item doesn't go there.")
            if not self.db.query_one("SELECT 1 FROM banana_items WHERE bettor=? AND item_id=?", (name, item_id)):
                raise BetError("Buy it first.")
        self.db.set_equipped(name, slot, item_id or None)
        return self.db.banana_equipped().get(name.lower(), {})
