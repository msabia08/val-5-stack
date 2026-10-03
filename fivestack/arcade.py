"""Onkey's Arcade: three small games that cost bananas to play, like putting a quarter in a machine.

A play costs ARCADE_PRICE bananas (paid into banana_ledger as reason 'arcade', so it's spending like the shop) and the
only prize is a place on the game's high-score board: nothing is ever paid back, and credits are never touched.

`start()` takes the bananas and hands the page a one-time token; `finish()` records the score against it. The
games run in the browser, so the score is only sanity-checked: a play can be finished once, by the bettor who paid,
within PLAY_TTL_S, and its score has to fit the game's ceiling for how long the play actually lasted (max_rate points
a second) and its absolute `max_score`. Scores are kept across season resets (they're all-time boards).
"""
import secrets
import time

from .bets import BetError

ARCADE_PRICE = 5
PLAY_TTL_S = 3600  # a play not finished within an hour can't be finished any more (the bananas stay spent)
BOARD_SIZE = 10

# The page draws and runs the games (web/arcade.js); these are the server's names, blurbs and score ceilings.
GAMES = {
    "catch": {"name": "Banana Catch", "icon": "🍌", "max_rate": 250, "max_score": 20000,
              "desc": "Onkey's long arms catch falling bananas. Golden ones are worth more; dodge the Gregs. 60 seconds, 3 lives."},
    "says": {"name": "Onkey Says", "icon": "🎤", "max_rate": 1500, "max_score": 25000,
             "desc": "Hit every syllable of Onkey's song as it lands. Three verses, each one faster and squeakier."},
    "dash": {"name": "Spike Dash", "icon": "💣", "max_rate": 400, "max_score": 1000000,
             "desc": "Onkey sprints over planted spikes. Jump, double jump, grab bananas, and don't stop."},
    "lab": {"name": "Lab Escape", "icon": "🧪", "max_rate": 150, "max_score": 200000,
            "desc": "The scientist chases Onkey round his lab, faster all the time. Grab bananas, dodge the evil candies, don't get caught."},
}


class ArcadeManager:
    def __init__(self, db):
        self.db = db

    def start(self, name, game):
        """Pay for a play of `game`; returns the token the page sends back with the score."""
        g = GAMES.get(game)
        if not g:
            raise BetError("That machine doesn't exist.")
        token = secrets.token_urlsafe(18)
        with self.db.lock:
            try:
                if not self.db.spend_bananas(name, ARCADE_PRICE, "arcade", f"play:{token}", g["name"]):
                    raise BetError(f"A play costs {ARCADE_PRICE} bananas; you have {self.db.banana_wallet(name):g}.")
                self.db.conn.execute("INSERT INTO arcade_plays(bettor, game, token, price, started_ts) VALUES(?,?,?,?,?)",
                                     (name, game, token, ARCADE_PRICE, time.time()))
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        return {"token": token, "game": game, "price": ARCADE_PRICE, "wallet": round(self.db.banana_wallet(name), 2)}

    def finish(self, name, token, score):
        """Record a play's score. Returns where it landed: the player's best before it, rank on the board, new best."""
        try:
            score = int(score)
        except (TypeError, ValueError):
            raise BetError("Invalid score.")
        now = time.time()
        with self.db.lock:
            play = self.db.query_one("SELECT * FROM arcade_plays WHERE token=?", (str(token or ""),))
            if not play or play["bettor"].lower() != name.lower():
                raise BetError("That play isn't yours.")
            if play["finished_ts"] is not None:
                raise BetError("That play already has a score.")
            if now - play["started_ts"] > PLAY_TTL_S:
                raise BetError("That play timed out.")
            g = GAMES[play["game"]]
            elapsed = now - play["started_ts"]
            if score < 0 or score > g["max_score"] or score > g["max_rate"] * (elapsed + 2):
                raise BetError("That score doesn't add up.")
            before = self.best(name, play["game"])
            self.db.execute("UPDATE arcade_plays SET finished_ts=?, score=? WHERE id=?", (now, score, play["id"]))
        board = self.board(play["game"])
        rank = next((i + 1 for i, r in enumerate(board) if r["bettor"].lower() == name.lower() and r["score"] == score), None)
        return {"game": play["game"], "score": score, "best_before": before, "new_best": before is None or score > before,
                "rank": rank, "champion": rank == 1, "board": board}

    def best(self, name, game):
        row = self.db.query_one("SELECT MAX(score) AS s FROM arcade_plays WHERE bettor=? AND game=? AND score IS NOT NULL", (name, game))
        return row["s"] if row else None

    def board(self, game, limit=BOARD_SIZE):
        """Each bettor's best score on a game, highest first (ties go to whoever got there first)."""
        rows = self.db.query("SELECT bettor, score, finished_ts FROM arcade_plays WHERE game=? AND score > 0 "
                             "ORDER BY score DESC, finished_ts ASC", (game,))
        seen, out = set(), []
        for r in rows:
            if r["bettor"].lower() in seen:
                continue
            seen.add(r["bettor"].lower())
            out.append(r)
            if len(out) >= limit:
                break
        return out

    def summary(self, me=None):
        """Every machine with its high-score board and play count, plus the signed-in bettor's bests and wallet."""
        counts = {r["game"]: r["n"] for r in self.db.query("SELECT game, COUNT(*) AS n FROM arcade_plays GROUP BY game")}
        games = [{"key": k, "name": g["name"], "icon": g["icon"], "desc": g["desc"], "plays": counts.get(k, 0),
                  "board": self.board(k), "my_best": self.best(me["name"], k) if me else None} for k, g in GAMES.items()]
        out = {"price": ARCADE_PRICE, "games": games, "me": None}
        if me:
            out["me"] = {"name": me["name"], "wallet": round(self.db.banana_wallet(me["name"]), 2),
                         "plays": self.db.query_one("SELECT COUNT(*) AS n FROM arcade_plays WHERE bettor=?", (me["name"],))["n"]}
        return out
