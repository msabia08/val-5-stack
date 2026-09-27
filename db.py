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
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS bettors (
    name TEXT PRIMARY KEY,
    balance REAL NOT NULL,
    created_at REAL
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

    def matches_needing_details(self, limit):
        return self.query(
            "SELECT * FROM matches WHERE details_fetched=0 ORDER BY started_ts DESC LIMIT ?", (int(limit),)
        )

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

    def create_bettor(self, name, balance):
        self.execute("INSERT INTO bettors(name, balance, created_at) VALUES(?,?,?)", (name, balance, time.time()))

    def adjust_balance(self, name, delta):
        self.execute("UPDATE bettors SET balance = balance + ? WHERE lower(name)=lower(?)", (delta, name))

    def reset_betting(self, balance):
        with self.lock:
            self.conn.execute("DELETE FROM bets")
            self.conn.execute("UPDATE bettors SET balance=?", (balance,))
            self.conn.commit()

    def insert_bet(self, bet):
        cur = self.execute(
            f"INSERT INTO bets({','.join(BET_FIELDS)}) VALUES({_placeholders(len(BET_FIELDS))})",
            [bet.get(f) for f in BET_FIELDS],
        )
        return cur.lastrowid

    def bets(self, status=None, bettor=None, limit=None):
        sql = "SELECT * FROM bets"
        conds, params = [], []
        if status:
            conds.append("status=?")
            params.append(status)
        if bettor:
            conds.append("lower(bettor)=lower(?)")
            params.append(bettor)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY placed_ts DESC"
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
