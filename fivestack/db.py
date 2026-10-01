"""SQLite storage for the 5-stack tracker (stdlib only)."""
import json
import sqlite3
import threading
import time

SCHEMA = """
-- The squad (2 to 5 players), managed on the Squad tab; a player keeps their row (puuid) through Riot ID renames.
CREATE TABLE IF NOT EXISTS members (
    puuid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    tag TEXT NOT NULL,
    region TEXT,
    nickname TEXT,
    card TEXT,
    order_index INTEGER DEFAULT 0,
    added_at REAL,
    previous_name TEXT,
    name_checked_ts REAL,
    bettor TEXT,
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    map TEXT,
    mode TEXT,
    mode_label TEXT,
    started_at TEXT,
    started_ts REAL,
    season TEXT,
    region TEXT,
    team TEXT,
    rounds_won INTEGER,
    rounds_lost INTEGER,
    result TEXT,
    game_length_ms INTEGER,
    source TEXT,
    party_verified INTEGER,
    details_fetched INTEGER DEFAULT 0,
    ingested_at REAL
);
CREATE INDEX IF NOT EXISTS idx_matches_started ON matches(started_ts);
CREATE TABLE IF NOT EXISTS match_players (
    match_id TEXT NOT NULL,
    puuid TEXT NOT NULL,
    agent TEXT,
    score INTEGER,
    kills INTEGER,
    deaths INTEGER,
    assists INTEGER,
    headshots INTEGER,
    bodyshots INTEGER,
    legshots INTEGER,
    damage_dealt INTEGER,
    damage_received INTEGER,
    tier INTEGER,
    tier_name TEXT,
    PRIMARY KEY (match_id, puuid)
);
-- Each member's own line from every stored match in a counted mode, whoever they queued with.
-- Rows whose match_id is also in `matches` are 5-stack games; the rest are the member's baseline.
CREATE TABLE IF NOT EXISTS member_games (
    match_id TEXT NOT NULL,
    puuid TEXT NOT NULL,
    map TEXT,
    mode TEXT,
    mode_label TEXT,
    started_at TEXT,
    started_ts REAL,
    rounds_won INTEGER,
    rounds_lost INTEGER,
    result TEXT,
    agent TEXT,
    score INTEGER,
    kills INTEGER,
    deaths INTEGER,
    assists INTEGER,
    headshots INTEGER,
    bodyshots INTEGER,
    legshots INTEGER,
    damage_dealt INTEGER,
    damage_received INTEGER,
    team TEXT,
    PRIMARY KEY (match_id, puuid)
);
CREATE INDEX IF NOT EXISTS idx_member_games_puuid ON member_games(puuid, started_ts);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS bettors (
    name TEXT PRIMARY KEY,
    balance REAL NOT NULL,
    created_at REAL,
    salt TEXT,
    password_hash TEXT
);
CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bettor TEXT NOT NULL,
    market_id TEXT NOT NULL,
    market_type TEXT NOT NULL,
    description TEXT,
    selection TEXT NOT NULL,
    selection_label TEXT,
    line REAL,
    odds_decimal REAL NOT NULL,
    stake REAL NOT NULL,
    placed_ts REAL NOT NULL,
    context TEXT,
    status TEXT NOT NULL,
    settled_match_id TEXT,
    settled_ts REAL,
    payout REAL,
    actual_value REAL,
    note TEXT
);
-- Past betting seasons. A reset archives the season here (final standings, every bet and reward) before clearing it.
CREATE TABLE IF NOT EXISTS seasons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    started_ts REAL,
    ended_ts REAL NOT NULL,
    standings TEXT,
    bets INTEGER,
    rewards INTEGER
);
CREATE TABLE IF NOT EXISTS archived_bets (
    season_id INTEGER NOT NULL,
    id INTEGER,
    bettor TEXT,
    market_id TEXT,
    market_type TEXT,
    description TEXT,
    selection TEXT,
    selection_label TEXT,
    line REAL,
    odds_decimal REAL,
    stake REAL,
    placed_ts REAL,
    context TEXT,
    status TEXT,
    settled_match_id TEXT,
    settled_ts REAL,
    payout REAL,
    actual_value REAL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS archived_rewards (
    season_id INTEGER NOT NULL,
    match_id TEXT,
    puuid TEXT,
    bettor TEXT,
    base REAL,
    bonus REAL,
    acs REAL,
    beat_share REAL,
    baseline_games INTEGER,
    created_ts REAL
);
-- Credits one bettor sent another (BetManager.send). Zero-sum: nothing is created or destroyed. A big enough
-- transfer earns the sender the generosity tax (tax_status 'open'): a cut of the recipient's next winning bet, paid
-- at settlement ('paid', with the bet, game and amount). NULL tax_status: this transfer earned none.
CREATE TABLE IF NOT EXISTS transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    amount REAL NOT NULL,
    note TEXT,
    created_ts REAL NOT NULL,
    tax_status TEXT,
    tax_amount REAL,
    tax_bet_id INTEGER,
    tax_match_id TEXT,
    tax_ts REAL
);
CREATE TABLE IF NOT EXISTS archived_transfers (
    season_id INTEGER NOT NULL,
    id INTEGER,
    sender TEXT,
    recipient TEXT,
    amount REAL,
    note TEXT,
    created_ts REAL,
    tax_status TEXT,
    tax_amount REAL,
    tax_bet_id INTEGER,
    tax_match_id TEXT,
    tax_ts REAL
);
-- Compact round-by-round record of a 5-stack game (see timeline.py); data is JSON, NULL if the record had none.
CREATE TABLE IF NOT EXISTS match_timelines (
    match_id TEXT PRIMARY KEY,
    data TEXT,
    fetched_ts REAL
);
-- Credits paid to a squad member's bettor account for a 5-stack game: win reward + performance bonus.
CREATE TABLE IF NOT EXISTS rewards (
    match_id TEXT NOT NULL,
    puuid TEXT NOT NULL,
    bettor TEXT NOT NULL,
    base REAL NOT NULL,
    bonus REAL NOT NULL,
    acs REAL,
    beat_share REAL,
    baseline_games INTEGER,
    created_ts REAL,
    PRIMARY KEY (match_id, puuid)
);
-- Bananas (see bananas.py): the shop's currency, kept apart from credits. Every change is a row here and a wallet
-- is the sum of its rows. (reason, ref) is unique, so an earning is never paid twice.
CREATE TABLE IF NOT EXISTS banana_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bettor TEXT NOT NULL COLLATE NOCASE,
    delta REAL NOT NULL,
    reason TEXT NOT NULL,
    ref TEXT NOT NULL,
    credits REAL,
    note TEXT,
    created_ts REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_banana_ref ON banana_ledger(reason, ref);
CREATE INDEX IF NOT EXISTS idx_banana_bettor ON banana_ledger(bettor);
-- Shop items a bettor owns (kept through season resets) and the one worn in each slot.
CREATE TABLE IF NOT EXISTS banana_items (
    bettor TEXT NOT NULL COLLATE NOCASE,
    item_id TEXT NOT NULL,
    price REAL,
    bought_ts REAL,
    PRIMARY KEY (bettor, item_id)
);
CREATE TABLE IF NOT EXISTS banana_equipped (
    bettor TEXT NOT NULL COLLATE NOCASE,
    slot TEXT NOT NULL,
    item_id TEXT NOT NULL,
    PRIMARY KEY (bettor, slot)
);
-- Social items used on another bettor (banana peel, jinx, wall note, title swap): active until expires_ts and, when
-- games > 0, until that many 5-stack games have started after created_ts.
CREATE TABLE IF NOT EXISTS banana_pranks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    bettor TEXT NOT NULL COLLATE NOCASE,
    target TEXT NOT NULL COLLATE NOCASE,
    text TEXT,
    price REAL,
    created_ts REAL NOT NULL,
    expires_ts REAL NOT NULL,
    games INTEGER DEFAULT 0
);
-- Onkey's Arcade (see arcade.py): one row per paid play; score stays NULL until the game sends it back.
CREATE TABLE IF NOT EXISTS arcade_plays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bettor TEXT NOT NULL COLLATE NOCASE,
    game TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    price REAL,
    started_ts REAL NOT NULL,
    finished_ts REAL,
    score INTEGER
);
CREATE INDEX IF NOT EXISTS idx_arcade_game ON arcade_plays(game, score);

-- Kept through resets: season_id NULL means the current season. Retry keys stay unique forever.
CREATE TABLE IF NOT EXISTS slot_spins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bettor TEXT NOT NULL REFERENCES bettors(name),
    machine TEXT NOT NULL,
    stake REAL NOT NULL,
    reels TEXT NOT NULL,
    multiplier INTEGER NOT NULL,
    payout REAL NOT NULL,
    created_ts REAL NOT NULL,
    request_id TEXT NOT NULL,
    season_id INTEGER REFERENCES seasons(id),
    rtp REAL,  -- the expected return the spin was played at; NULL before line stats and house tracking began
    UNIQUE(bettor, request_id)
);
CREATE INDEX IF NOT EXISTS idx_slots_season ON slot_spins(season_id, bettor);
"""

MATCH_FIELDS = [
    "match_id", "map", "mode", "mode_label", "started_at", "started_ts", "season",
    "region", "team", "rounds_won", "rounds_lost", "result", "game_length_ms",
    "source", "party_verified", "details_fetched", "ingested_at",
]
PLAYER_FIELDS = [
    "match_id", "puuid", "agent", "score", "kills", "deaths", "assists", "headshots",
    "bodyshots", "legshots", "damage_dealt", "damage_received", "tier", "tier_name",
]
MEMBER_GAME_FIELDS = [
    "match_id", "puuid", "map", "mode", "mode_label", "started_at", "started_ts", "rounds_won",
    "rounds_lost", "result", "agent", "score", "kills", "deaths", "assists", "headshots",
    "bodyshots", "legshots", "damage_dealt", "damage_received", "team",
]
# Columns added after a table first shipped; databases from before get them on startup.
MIGRATIONS = {
    "bettors": (("salt", "TEXT"), ("password_hash", "TEXT")),
    "members": (("previous_name", "TEXT"), ("name_checked_ts", "REAL"), ("bettor", "TEXT"), ("active", "INTEGER DEFAULT 1")),
    "member_games": (("team", "TEXT"),),
    "slot_spins": (("rtp", "REAL"),),
}
ARCHIVED_BET_COLUMNS = [
    "id", "bettor", "market_id", "market_type", "description", "selection", "selection_label", "line", "odds_decimal",
    "stake", "placed_ts", "context", "status", "settled_match_id", "settled_ts", "payout", "actual_value", "note",
]
ARCHIVED_REWARD_COLUMNS = ["match_id", "puuid", "bettor", "base", "bonus", "acs", "beat_share", "baseline_games", "created_ts"]
TRANSFER_COLUMNS = ["id", "sender", "recipient", "amount", "note", "created_ts",
                    "tax_status", "tax_amount", "tax_bet_id", "tax_match_id", "tax_ts"]
BET_FIELDS = [
    "bettor", "market_id", "market_type", "description", "selection", "selection_label",
    "line", "odds_decimal", "stake", "placed_ts", "context", "status",
]


def _placeholders(n):
    return ",".join(["?"] * n)


class DB:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            # Migrations for databases created by earlier versions.
            for table, columns in MIGRATIONS.items():
                have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
                for col, ctype in columns:
                    if col not in have:
                        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ctype}")
            self.conn.commit()

    # ---- low level -------------------------------------------------------
    def execute(self, sql, params=()):
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def query_one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ---- members ---------------------------------------------------------
    def upsert_member(self, puuid, name, tag, region, nickname, card, order_index):
        self.execute(
            """INSERT INTO members(puuid, name, tag, region, nickname, card, order_index, added_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(puuid) DO UPDATE SET
                 name=excluded.name, tag=excluded.tag, region=excluded.region,
                 nickname=excluded.nickname, card=COALESCE(excluded.card, members.card),
                 order_index=excluded.order_index""",
            (puuid, name, tag, region, nickname, card, order_index, time.time()),
        )

    def members(self):
        """The active squad: the players whose games are tracked (the rest of the pool is on the bench)."""
        return self.query("SELECT * FROM members WHERE active=1 ORDER BY order_index, name")

    def pool(self):
        """Every known player, the active squad first, then the bench."""
        return self.query("SELECT * FROM members ORDER BY active DESC, order_index, name")

    def count_pool(self):
        return self.query_one("SELECT COUNT(*) AS n FROM members")["n"]

    def set_active(self, puuid, active, order_index=None):
        if order_index is None:
            self.execute("UPDATE members SET active=? WHERE puuid=?", (1 if active else 0, puuid))
        else:
            self.execute("UPDATE members SET active=?, order_index=? WHERE puuid=?", (1 if active else 0, order_index, puuid))

    def member_by_riot_id(self, name, tag):
        return self.query_one(
            "SELECT * FROM members WHERE lower(name)=lower(?) AND lower(tag)=lower(?)", (name, tag)
        )

    def member(self, puuid):
        return self.query_one("SELECT * FROM members WHERE puuid=?", (puuid,))

    def member_by_bettor(self, name):
        """The squad entry a betting account owns (its Riot ID), if any."""
        return self.query_one("SELECT * FROM members WHERE lower(bettor)=lower(?)", (name,))

    def count_members(self):
        """How many players are on the active squad."""
        return self.query_one("SELECT COUNT(*) AS n FROM members WHERE active=1")["n"]

    def next_member_order(self):
        return self.query_one("SELECT COALESCE(MAX(order_index), -1) + 1 AS n FROM members")["n"]

    def update_member(self, puuid, **fields):
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE members SET {sets} WHERE puuid=?", list(fields.values()) + [puuid])

    def remove_member(self, puuid):
        """Take a player off the squad. Their lines stay in match_players / member_games."""
        self.execute("DELETE FROM members WHERE puuid=?", (puuid,))

    # ---- matches ---------------------------------------------------------
    def has_match(self, match_id):
        return self.query_one("SELECT 1 FROM matches WHERE match_id=?", (match_id,)) is not None

    def count_matches(self):
        row = self.query_one("SELECT COUNT(*) AS n FROM matches")
        return row["n"] if row else 0

    def record(self):
        rows = self.query("SELECT result, COUNT(*) AS n FROM matches GROUP BY result")
        rec = {"games": 0, "wins": 0, "losses": 0, "draws": 0}
        for r in rows:
            rec["games"] += r["n"]
            key = {"win": "wins", "loss": "losses", "draw": "draws"}.get(r["result"])
            if key:
                rec[key] += r["n"]
        return rec

    def insert_match(self, match, players):
        match = dict(match)
        match.setdefault("ingested_at", time.time())
        match.setdefault("details_fetched", 0)
        with self.lock:
            self.conn.execute(
                f"INSERT OR REPLACE INTO matches({','.join(MATCH_FIELDS)}) VALUES({_placeholders(len(MATCH_FIELDS))})",
                [match.get(f) for f in MATCH_FIELDS],
            )
            for p in players:
                p = dict(p)
                p["match_id"] = match["match_id"]
                self.conn.execute(
                    f"INSERT OR REPLACE INTO match_players({','.join(PLAYER_FIELDS)}) VALUES({_placeholders(len(PLAYER_FIELDS))})",
                    [p.get(f) for f in PLAYER_FIELDS],
                )
            self.conn.commit()

    def update_match(self, match_id, **fields):
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE matches SET {sets} WHERE match_id=?", list(fields.values()) + [match_id])

    def update_player(self, match_id, puuid, **fields):
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.execute(
            f"UPDATE match_players SET {sets} WHERE match_id=? AND puuid=?",
            list(fields.values()) + [match_id, puuid],
        )

    def matches(self, limit=None):
        sql = "SELECT * FROM matches ORDER BY started_ts DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.query(sql)

    def match(self, match_id):
        return self.query_one("SELECT * FROM matches WHERE match_id=?", (match_id,))

    def match_players(self, match_id):
        return self.query(
            """SELECT mp.*, m.nickname, m.name, m.tag FROM match_players mp
               LEFT JOIN members m USING(puuid) WHERE mp.match_id=?
               ORDER BY mp.score DESC""",
            (match_id,),
        )

    def all_match_players(self):
        return self.query(
            """SELECT mp.*, m.nickname, m.name, m.tag FROM match_players mp
               LEFT JOIN members m USING(puuid)"""
        )

    def player_rows(self):
        """Every member line joined with its match, newest first."""
        return self.query(
            """SELECT mp.*, ma.map, ma.mode, ma.mode_label, ma.started_at, ma.started_ts,
                      ma.rounds_won, ma.rounds_lost, ma.result, ma.team
               FROM match_players mp JOIN matches ma USING(match_id)
               ORDER BY ma.started_ts DESC"""
        )

    def insert_member_games(self, rows):
        """Store member lines from stored-match history; already-known (match, member) pairs are kept as-is."""
        if not rows:
            return
        with self.lock:
            self.conn.executemany(
                f"INSERT OR IGNORE INTO member_games({','.join(MEMBER_GAME_FIELDS)}) "
                f"VALUES({_placeholders(len(MEMBER_GAME_FIELDS))})",
                [[r.get(f) for f in MEMBER_GAME_FIELDS] for r in rows],
            )
            self.conn.commit()

    def count_member_games(self):
        row = self.query_one("SELECT COUNT(*) AS n FROM member_games")
        return row["n"] if row else 0

    def member_games_of(self, puuids):
        """Every stored line of these members, oldest first (the roster re-evaluation reads them)."""
        if not puuids:
            return []
        return self.query(
            f"SELECT * FROM member_games WHERE puuid IN ({_placeholders(len(puuids))}) ORDER BY started_ts",
            list(puuids),
        )

    def matches_missing_any(self, puuids):
        """Ids of recorded games in which at least one of these members has no line."""
        if not puuids:
            return []
        rows = self.query(
            f"""SELECT m.match_id FROM matches m
                WHERE (SELECT COUNT(*) FROM match_players mp
                       WHERE mp.match_id = m.match_id AND mp.puuid IN ({_placeholders(len(puuids))})) < ?""",
            list(puuids) + [len(puuids)],
        )
        return [r["match_id"] for r in rows]

    def delete_match(self, match_id):
        """Forget a recorded game (its lines, players and timeline); bets and rewards that settled on it stay."""
        with self.lock:
            for table in ("match_players", "match_timelines", "matches"):
                self.conn.execute(f"DELETE FROM {table} WHERE match_id=?", (match_id,))
            self.conn.commit()

    def baseline_rows(self):
        """Member lines from games that were NOT squad games, newest first."""
        return self.query(
            """SELECT * FROM member_games
               WHERE match_id NOT IN (SELECT match_id FROM matches)
               ORDER BY started_ts DESC"""
        )

    def matches_needing_details(self, limit):
        """Games whose full record hasn't been fetched yet, or has but without keeping its round timeline."""
        return self.query(
            """SELECT * FROM matches
               WHERE details_fetched=0 OR match_id NOT IN (SELECT match_id FROM match_timelines)
               ORDER BY started_ts DESC LIMIT ?""", (int(limit),)
        )

    def save_timeline(self, match_id, timeline):
        self.execute("INSERT OR REPLACE INTO match_timelines(match_id, data, fetched_ts) VALUES(?,?,?)",
                     (match_id, json.dumps(timeline) if timeline else None, time.time()))

    def timeline(self, match_id):
        """One game's stored timeline (see timeline.py), or None if it has none (yet)."""
        row = self.query_one("SELECT data FROM match_timelines WHERE match_id=?", (match_id,))
        return json.loads(row["data"]) if row and row["data"] else None

    def timelines(self):
        """Every stored timeline with its game's map and result, newest game first."""
        rows = self.query(
            """SELECT t.match_id, t.data, m.map, m.result, m.started_ts FROM match_timelines t
               JOIN matches m USING(match_id) WHERE t.data IS NOT NULL ORDER BY m.started_ts DESC""")
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    # ---- meta ------------------------------------------------------------
    def get_meta(self, key, default=None):
        row = self.query_one("SELECT value FROM meta WHERE key=?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return default

    def set_meta(self, key, value):
        self.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (key, json.dumps(value)))

    # ---- bettors / bets ---------------------------------------------------
    def bettors(self):
        return self.query("SELECT * FROM bettors ORDER BY balance DESC, name")

    def get_bettor(self, name):
        return self.query_one("SELECT * FROM bettors WHERE lower(name)=lower(?)", (name,))

    def create_bettor(self, name, balance, salt=None, password_hash=None):
        self.execute(
            "INSERT INTO bettors(name, balance, created_at, salt, password_hash) VALUES(?,?,?,?,?)",
            (name, balance, time.time(), salt, password_hash),
        )

    def set_bettor_password(self, name, salt, password_hash):
        self.execute(
            "UPDATE bettors SET salt=?, password_hash=? WHERE lower(name)=lower(?)", (salt, password_hash, name)
        )

    def adjust_balance(self, name, delta):
        self.execute("UPDATE bettors SET balance = balance + ? WHERE lower(name)=lower(?)", (delta, name))

    def season_counts(self):
        """The current (unarchived) season: when it started and how much it holds."""
        bets = self.query_one("SELECT COUNT(*) AS n, MIN(placed_ts) AS first FROM bets")
        rewards = self.query_one("SELECT COUNT(*) AS n FROM rewards")["n"]
        started = self.get_meta("season_started") or bets["first"]
        return {"started_ts": started, "bets": bets["n"], "rewards": rewards,
                "transfers": self.query_one("SELECT COUNT(*) AS n FROM transfers")["n"],
                "bettors": self.query_one("SELECT COUNT(*) AS n FROM bettors")["n"]}

    def archive_and_reset(self, balance, standings):
        """End the season: archive its standings, bets, rewards and transfers, then clear them and reset every balance.
        One transaction, so a failure part-way leaves everything as it was. Returns the new season row."""
        now = time.time()
        with self.lock:
            counts = self.season_counts()
            n = self.query_one("SELECT COUNT(*) AS n FROM seasons")["n"] + 1
            try:
                cur = self.conn.execute(
                    "INSERT INTO seasons(name, started_ts, ended_ts, standings, bets, rewards) VALUES(?,?,?,?,?,?)",
                    (f"Season {n}", counts["started_ts"], now, json.dumps(standings), counts["bets"], counts["rewards"]))
                sid = cur.lastrowid
                cols = ", ".join(ARCHIVED_BET_COLUMNS)
                self.conn.execute(f"INSERT INTO archived_bets(season_id, {cols}) SELECT ?, {cols} FROM bets", (sid,))
                # Bets still open when the season ended never settle; every balance is reset anyway.
                self.conn.execute(
                    "UPDATE archived_bets SET status='cancelled', note='Still open when the season was reset' "
                    "WHERE season_id=? AND status='pending'", (sid,))
                cols = ", ".join(ARCHIVED_REWARD_COLUMNS)
                self.conn.execute(f"INSERT INTO archived_rewards(season_id, {cols}) SELECT ?, {cols} FROM rewards", (sid,))
                cols = ", ".join(TRANSFER_COLUMNS)
                self.conn.execute(f"INSERT INTO archived_transfers(season_id, {cols}) SELECT ?, {cols} FROM transfers", (sid,))
                self.conn.execute("DELETE FROM bets")
                self.conn.execute("DELETE FROM rewards")
                self.conn.execute("DELETE FROM transfers")
                self.conn.execute("UPDATE slot_spins SET season_id=? WHERE season_id IS NULL", (sid,))
                self.conn.execute("UPDATE bettors SET balance=?", (balance,))
                # Bananas go back to zero with the credits (one ledger row per wallet); owned shop items stay.
                self.conn.execute(
                    "INSERT INTO banana_ledger(bettor, delta, reason, ref, note, created_ts) "
                    "SELECT bettor, -SUM(delta), 'season_reset', 'season:' || ? || ':' || lower(bettor), ?, ? "
                    "FROM banana_ledger GROUP BY lower(bettor) HAVING ABS(SUM(delta)) > 1e-9",
                    (sid, f"Season {n} ended", now))
                self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('season_started', ?)", (json.dumps(now),))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return self.season(sid)

    def seasons(self):
        """Archived seasons, newest first, with their final standings."""
        rows = self.query("SELECT * FROM seasons ORDER BY id DESC")
        for r in rows:
            r["standings"] = json.loads(r["standings"] or "[]")
        return rows

    def season(self, season_id):
        r = self.query_one("SELECT * FROM seasons WHERE id=?", (season_id,))
        if r:
            r["standings"] = json.loads(r["standings"] or "[]")
        return r

    def archived_bets(self, season_id):
        return self.query("SELECT * FROM archived_bets WHERE season_id=? ORDER BY placed_ts DESC", (season_id,))

    def insert_bet(self, bet):
        cur = self.execute(
            f"INSERT INTO bets({','.join(BET_FIELDS)}) VALUES({_placeholders(len(BET_FIELDS))})",
            [bet.get(f) for f in BET_FIELDS],
        )
        return cur.lastrowid

    def bets(self, status=None, bettor=None, limit=None):
        """Bets newest first, each with the game it settled on (game_map, game_rounds_won, ...) when there is one."""
        sql = """SELECT b.*, m.map AS game_map, m.mode_label AS game_mode, m.rounds_won AS game_rounds_won,
                        m.rounds_lost AS game_rounds_lost, m.result AS game_result, m.started_ts AS game_started_ts
                 FROM bets b LEFT JOIN matches m ON m.match_id = b.settled_match_id"""
        conds, params = [], []
        if status:
            conds.append("b.status=?")
            params.append(status)
        if bettor:
            conds.append("lower(b.bettor)=lower(?)")
            params.append(bettor)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY b.placed_ts DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.query(sql, params)

    def bet(self, bet_id):
        return self.query_one("SELECT * FROM bets WHERE id=?", (bet_id,))

    def update_bet(self, bet_id, **fields):
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE bets SET {sets} WHERE id=?", list(fields.values()) + [bet_id])

    def pending_bets(self):
        return self.query("SELECT * FROM bets WHERE status='pending' ORDER BY placed_ts ASC")

    # ---- game rewards ------------------------------------------------------
    def pay_reward(self, reward):
        """Record a reward and credit the bettor in one transaction. False if this member was already paid."""
        with self.lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO rewards(match_id, puuid, bettor, base, bonus, acs, beat_share, baseline_games, created_ts) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                [reward[k] for k in ("match_id", "puuid", "bettor", "base", "bonus", "acs", "beat_share", "baseline_games")] + [time.time()],
            )
            if cur.rowcount:
                self.conn.execute("UPDATE bettors SET balance = balance + ? WHERE lower(name)=lower(?)",
                                  (reward["base"] + reward["bonus"], reward["bettor"]))
            self.conn.commit()
            return bool(cur.rowcount)

    def rewards(self, limit=None):
        sql = """SELECT r.*, m.nickname, ma.map, ma.result, ma.rounds_won, ma.rounds_lost, ma.started_ts
                 FROM rewards r LEFT JOIN members m USING(puuid) LEFT JOIN matches ma USING(match_id)
                 ORDER BY ma.started_ts DESC, r.base + r.bonus DESC"""
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.query(sql)

    def reward_totals(self):
        """Total rewards per bettor, keyed by lower-cased name."""
        return {r["k"]: r["total"] for r in self.query(
            "SELECT lower(bettor) AS k, SUM(base + bonus) AS total FROM rewards GROUP BY lower(bettor)")}

    # ---- bananas (see bananas.py) -------------------------------------------
    def earn_bananas(self, rate, now=None):
        """Pay bananas for every credit gain not paid yet: a won bet's profit and each game reward, `rate` bananas per
        credit. The unique (reason, ref) makes it safe to run after every settlement. Returns the rows added."""
        now = now or time.time()
        with self.lock:
            before = self.conn.total_changes
            self.conn.execute(
                "INSERT OR IGNORE INTO banana_ledger(bettor, delta, reason, ref, credits, note, created_ts) "
                "SELECT bettor, ROUND((payout - stake) * ?, 2), 'bet_win', 'bet:' || id, payout - stake, description, ? "
                "FROM bets WHERE status='won' AND payout > stake", (rate, now))
            self.conn.execute(
                "INSERT OR IGNORE INTO banana_ledger(bettor, delta, reason, ref, credits, note, created_ts) "
                "SELECT r.bettor, ROUND((r.base + r.bonus) * ?, 2), 'reward', 'reward:' || r.match_id || ':' || r.puuid, "
                "r.base + r.bonus, 'Game reward: ' || COALESCE(m.map, 'a game'), ? "
                "FROM rewards r LEFT JOIN matches m USING(match_id) WHERE r.base + r.bonus > 0", (rate, now))
            self.conn.commit()
            return self.conn.total_changes - before

    def grant_starting_bananas(self, amount, now=None):
        """Every account gets `amount` bananas once per season (reason 'starter', like starting_balance for credits;
        not counted as earned). The ref names the season, so a season reset, which zeroes wallets, grants them again.
        Returns the rows added."""
        with self.lock:
            season = self.conn.execute("SELECT COUNT(*) FROM seasons").fetchone()[0] + 1
            before = self.conn.total_changes
            self.conn.execute(
                "INSERT OR IGNORE INTO banana_ledger(bettor, delta, reason, ref, note, created_ts) "
                "SELECT name, ?, 'starter', 'starter:' || ? || ':' || lower(name), 'Starting bananas', ? FROM bettors",
                (amount, season, now or time.time()))
            self.conn.commit()
            return self.conn.total_changes - before

    def banana_totals(self):
        """Per bettor (lower-cased name): wallet, earned (all time and since the season started) and spent."""
        since = self.get_meta("season_started") or 0
        return {r["k"]: r for r in self.query(
            """SELECT lower(bettor) AS k, SUM(delta) AS wallet,
                      SUM(CASE WHEN reason IN ('bet_win', 'reward') THEN delta ELSE 0 END) AS earned,
                      SUM(CASE WHEN reason IN ('bet_win', 'reward') AND created_ts >= ? THEN delta ELSE 0 END) AS season_earned,
                      SUM(CASE WHEN reason IN ('bet_win', 'reward') AND created_ts >= ? THEN credits ELSE 0 END) AS season_credits,
                      -SUM(CASE WHEN reason IN ('purchase', 'prank', 'arcade') THEN delta ELSE 0 END) AS spent
               FROM banana_ledger GROUP BY lower(bettor)""", (since, since))}

    def banana_wallet(self, name):
        row = self.query_one("SELECT COALESCE(SUM(delta), 0) AS w FROM banana_ledger WHERE bettor=?", (name,))
        return row["w"] if row else 0.0

    def banana_history(self, name, limit=30):
        return self.query("SELECT * FROM banana_ledger WHERE bettor=? ORDER BY id DESC LIMIT ?", (name, int(limit)))

    def spend_bananas(self, name, price, reason, ref, note):
        """Take `price` bananas in one transaction with the purchase it pays for; False if the wallet is short.
        Call inside `with db.lock:` together with the write it pays for."""
        if self.banana_wallet(name) + 1e-9 < price:
            return False
        self.conn.execute(
            "INSERT INTO banana_ledger(bettor, delta, reason, ref, note, created_ts) VALUES(?,?,?,?,?,?)",
            (name, -price, reason, ref, note, time.time()))
        return True

    def banana_items(self, name=None):
        if name:
            return self.query("SELECT * FROM banana_items WHERE bettor=? ORDER BY bought_ts", (name,))
        return self.query("SELECT * FROM banana_items ORDER BY bought_ts")

    def banana_equipped(self):
        """Every bettor's worn items: {lower-cased name: {slot: item_id}}."""
        out = {}
        for r in self.query("SELECT * FROM banana_equipped"):
            out.setdefault(r["bettor"].lower(), {})[r["slot"]] = r["item_id"]
        return out

    def set_equipped(self, name, slot, item_id):
        if item_id:
            self.execute("INSERT OR REPLACE INTO banana_equipped(bettor, slot, item_id) VALUES(?,?,?)", (name, slot, item_id))
        else:
            self.execute("DELETE FROM banana_equipped WHERE bettor=? AND slot=?", (name, slot))

    def banana_pranks(self, active_only=True, target=None, limit=200):
        """Social items, newest first, each with `games_since` (5-stack games started after it). Active ones haven't
        expired and, when `games` > 0, fewer than `games` games have started since."""
        sql = ("SELECT * FROM (SELECT p.*, (SELECT COUNT(*) FROM matches m WHERE m.started_ts > p.created_ts) AS games_since "
               "FROM banana_pranks p)")
        conds, params = [], []
        if active_only:
            conds.append("expires_ts > ? AND (games = 0 OR games_since < games)")
            params.append(time.time())
        if target:
            conds.append("target=?")
            params.append(target)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        return self.query(sql + f" ORDER BY id DESC LIMIT {int(limit)}", params)

    # ---- transfers between bettors ------------------------------------------
    def transfer(self, sender, recipient, amount, note, tax_min=None):
        """Move credits from one bettor to another and record it, in one transaction. The sender's balance is
        checked inside it, so two sends at once can't overdraw. With tax_min, a transfer of at least that much earns
        the sender the generosity tax (tax_status 'open'), unless they already have one open on this recipient.
        Returns the new row's id, or None if the sender doesn't have enough."""
        with self.lock:
            row = self.conn.execute("SELECT balance FROM bettors WHERE lower(name)=lower(?)", (sender,)).fetchone()
            if row is None or row[0] + 1e-9 < amount:
                return None
            open_already = self.conn.execute(
                "SELECT 1 FROM transfers WHERE lower(sender)=lower(?) AND lower(recipient)=lower(?) AND tax_status='open'",
                (sender, recipient)).fetchone()
            tax = "open" if tax_min is not None and amount >= tax_min and not open_already else None
            try:
                self.conn.execute("UPDATE bettors SET balance = balance - ? WHERE lower(name)=lower(?)", (amount, sender))
                self.conn.execute("UPDATE bettors SET balance = balance + ? WHERE lower(name)=lower(?)", (amount, recipient))
                cur = self.conn.execute(
                    "INSERT INTO transfers(sender, recipient, amount, note, created_ts, tax_status) VALUES(?,?,?,?,?,?)",
                    (sender, recipient, amount, note, time.time(), tax))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            return cur.lastrowid

    def transfers(self, bettor=None, received_by=None, limit=None):
        """Transfers newest first: all of them, those a bettor sent or received, or only those they received. A paid
        generosity tax comes with the bet it was taken from (tax_bet_description, tax_bet_net: its net winnings)."""
        sql = ("SELECT t.*, b.description AS tax_bet_description, b.payout - b.stake AS tax_bet_net "
               "FROM transfers t LEFT JOIN bets b ON b.id = t.tax_bet_id")
        params = []
        if bettor:
            sql += " WHERE lower(t.sender)=lower(?) OR lower(t.recipient)=lower(?)"
            params = [bettor, bettor]
        elif received_by:
            sql += " WHERE lower(t.recipient)=lower(?)"
            params = [received_by]
        sql += " ORDER BY t.created_ts DESC, t.id DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.query(sql, params)

    def transfer_totals(self):
        """Net credits received from other bettors (received minus sent, plus generosity tax collected minus tax
        paid), keyed by lower-cased name."""
        net = {}
        for r in self.query("SELECT lower(sender) AS s, lower(recipient) AS r, amount, tax_amount FROM transfers"):
            moved = r["amount"] - (r["tax_amount"] or 0.0)  # the tax flows back from the recipient to the sender
            net[r["s"]] = net.get(r["s"], 0.0) - moved
            net[r["r"]] = net.get(r["r"], 0.0) + moved
        return net

    def open_taxes(self, recipient, before_ts):
        """Generosity taxes owed on a bettor's next winning bet: open transfers to them made before before_ts."""
        return self.query("SELECT * FROM transfers WHERE lower(recipient)=lower(?) AND tax_status='open' AND created_ts < ? "
                          "ORDER BY created_ts", (recipient, before_ts))

    def pay_tax(self, transfer_id, bet_id, match_id, amount, bet_note):
        """Settle one generosity tax: the recipient pays the sender `amount`, the transfer records which bet and game
        it came from, and the bet's note says so. One transaction; False if it was already paid."""
        with self.lock:
            try:
                cur = self.conn.execute(
                    "UPDATE transfers SET tax_status='paid', tax_amount=?, tax_bet_id=?, tax_match_id=?, tax_ts=? "
                    "WHERE id=? AND tax_status='open'", (amount, bet_id, match_id, time.time(), transfer_id))
                if cur.rowcount:
                    t = self.conn.execute("SELECT sender, recipient FROM transfers WHERE id=?", (transfer_id,)).fetchone()
                    self.conn.execute("UPDATE bettors SET balance = balance - ? WHERE lower(name)=lower(?)", (amount, t[1]))
                    self.conn.execute("UPDATE bettors SET balance = balance + ? WHERE lower(name)=lower(?)", (amount, t[0]))
                    self.conn.execute("UPDATE bets SET note=? WHERE id=?", (bet_note, bet_id))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            return bool(cur.rowcount)

    def taxes_collected(self, sender, limit=20):
        """Generosity taxes a bettor collected, newest first, with the bet they came from."""
        return self.query(
            """SELECT t.*, b.description AS bet_description, b.payout AS bet_payout, b.stake AS bet_stake
               FROM transfers t LEFT JOIN bets b ON b.id = t.tax_bet_id
               WHERE lower(t.sender)=lower(?) AND t.tax_status='paid' ORDER BY t.tax_ts DESC LIMIT ?""",
            (sender, int(limit)))
