"""The house: what the casino games (slots, and blackjack and poker to come) take from bettors, in one ledger.

Each round a casino game settles writes one `house_ledger` row inside its own transaction (`record()`): the credits
staked against the house (0 for a poker pot, where the players bet against each other), the house's `take` (stakes
minus payouts for a house-banked game, so negative when a bettor wins big; the rake for poker) and the `expected`
take from the game's edge when it has one. Rows are unique by (game, ref), so a retried round never counts twice.

This is scaffolding: what the house *does* with its take isn't decided yet, so nothing here moves credits. Wire new
behaviour in through HouseManager, which reads the ledger, and keep games writing rows through record().

Casino results stay out of match-betting profit: `casino_nets()` is each bettor's season net per game, which the
leaderboard reports as its own column. Casino play never earns or costs bananas.
"""
import time

# Each casino game's season net per bettor (payouts minus stakes, current season only). A new game adds its query.
CASINO_NET_SQL = {
    "slots": "SELECT bettor, SUM(payout - stake) AS net FROM slot_spins WHERE season_id IS NULL GROUP BY bettor",
    "blackjack": "SELECT bettor, SUM(payout - stake) AS net FROM blackjack_hands "
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
    def __init__(self, db):
        self.db = db
        self.backfill()

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
