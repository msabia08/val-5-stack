"""SQLite storage for the 5-stack tracker (stdlib only)."""
import json
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    puuid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    tag TEXT NOT NULL,
    region TEXT,
    nickname TEXT,
    card TEXT,
    order_index INTEGER DEFAULT 0,
    added_at REAL
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
    "bodyshots", "legshots", "damage_dealt", "damage_received",
]
ARCHIVED_BET_COLUMNS = [
    "id", "bettor", "market_id", "market_type", "description", "selection", "selection_label", "line", "odds_decimal",
    "stake", "placed_ts", "context", "status", "settled_match_id", "settled_ts", "payout", "actual_value", "note",
]
ARCHIVED_REWARD_COLUMNS = ["match_id", "puuid", "bettor", "base", "bonus", "acs", "beat_share", "baseline_games", "created_ts"]
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
            cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(bettors)").fetchall()}
            for col in ("salt", "password_hash"):
                if col not in cols:
                    self.conn.execute(f"ALTER TABLE bettors ADD COLUMN {col} TEXT")
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
        return self.query("SELECT * FROM members ORDER BY order_index, name")

    def member_by_riot_id(self, name, tag):
        return self.query_one(
            "SELECT * FROM members WHERE lower(name)=lower(?) AND lower(tag)=lower(?)", (name, tag)
        )

    def member(self, puuid):
        return self.query_one("SELECT * FROM members WHERE puuid=?", (puuid,))

    def remove_members_not_in(self, puuids):
        keep = list(puuids)
        if not keep:
            return
        self.execute(f"DELETE FROM members WHERE puuid NOT IN ({_placeholders(len(keep))})", keep)

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

    def baseline_rows(self):
        """Member lines from games that were NOT 5-stack games, newest first."""
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
                "bettors": self.query_one("SELECT COUNT(*) AS n FROM bettors")["n"]}

    def archive_and_reset(self, balance, standings):
        """End the season: archive its standings, bets and rewards, then clear them and reset every balance.
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
                self.conn.execute("DELETE FROM bets")
                self.conn.execute("DELETE FROM rewards")
                self.conn.execute("UPDATE bettors SET balance=?", (balance,))
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
