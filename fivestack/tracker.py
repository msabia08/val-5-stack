"""Finds games where every squad member played on the same team, and stores them.

How detection works
-------------------
1. The squad (ROSTER_MIN to ROSTER_MAX players) lives in the `members` table:
   seeded from config.json the first time, then managed on the Squad tab
   (add_member / remove_member). Each Riot ID is resolved to a PUUID once; the
   PUUID is the identity, so renames are followed (refresh_names) rather than
   breaking tracking.
2. Each sync pulls every member's *stored matches* from HenrikDev (one call per
   member). Each entry carries that member's own line (agent, K/D/A, score,
   shots, damage) plus the match metadata and the final round score.
3. A match id that shows up for ALL members, with everybody on the same team,
   is a squad game. It is stored straight from those lines.
4. If a match shows up for all-but-one member (HenrikDev's stored history can
   have holes), the full v4 match record is fetched to check whether the
   missing member was in it too.
5. Optionally the v4 record is fetched for stored games as well, to add game
   length, rank names and to verify everybody shared one party.
6. Every member line in a counted mode is also kept in member_games, so each
   member's other games serve as the baseline their squad play is compared
   against, and so a roster change can re-derive the squad games (reevaluate)
   without new API calls.
"""
import threading
import time
from collections import defaultdict, deque
from datetime import datetime

from .config import PLACEHOLDER_IDS
from .gamestate import NO_CONTEST, ending
from .henrik import HenrikError
from .timeline import extract_timeline

ROSTER_MIN, ROSTER_MAX = 2, 5  # a squad game needs every member on one team; between two and five of them
NAME_REFRESH_S = 24 * 3600     # how often each member's current Riot ID is looked up, to follow renames

MODE_ALIASES = {
    "competitive": "competitive",
    "unrated": "unrated",
    "premier": "premier",
    "swiftplay": "swiftplay",
    "swift play": "swiftplay",
    "spike rush": "spikerush",
    "spikerush": "spikerush",
    "deathmatch": "deathmatch",
    "team deathmatch": "teamdeathmatch",
    "teamdeathmatch": "teamdeathmatch",
    "escalation": "escalation",
    "replication": "replication",
    "custom game": "custom",
    "custom": "custom",
    "new map": "newmap",
    "newmap": "newmap",
    "snowball fight": "snowballfight",
    "snowballfight": "snowballfight",
}


class TrackerError(Exception):
    pass


def parse_riot_id(s):
    s = (s or "").strip()
    if "#" not in s:
        raise TrackerError(f"'{s}' is not a Riot ID (expected Name#TAG)")
    name, tag = s.rsplit("#", 1)
    name, tag = name.strip(), tag.strip()
    if not name or not tag:
        raise TrackerError(f"'{s}' is not a Riot ID (expected Name#TAG)")
    return name, tag


def parse_ts(iso):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def normalize_mode(label):
    key = (label or "").strip().lower()
    return MODE_ALIASES.get(key, key.replace(" ", ""))


def result_of(rw, rl):
    if rw is None or rl is None:
        return None
    return "win" if rw > rl else ("loss" if rw < rl else "draw")


def parse_stored_item(item):
    meta = item.get("meta") or {}
    st = item.get("stats") or {}
    teams = item.get("teams") or {}
    team = (st.get("team") or "").strip().capitalize()
    red, blue = teams.get("red"), teams.get("blue")
    if team == "Blue":
        rw, rl = blue, red
    elif team == "Red":
        rw, rl = red, blue
    else:
        rw = rl = None
    dmg = st.get("damage") or {}
    shots = st.get("shots") or {}
    char = st.get("character") or {}
    started = meta.get("started_at")
    mode_label = meta.get("mode") or ""
    return {
        "match_id": meta.get("id"),
        "map": (meta.get("map") or {}).get("name"),
        "mode": normalize_mode(mode_label),
        "mode_label": mode_label or None,
        "started_at": started,
        "started_ts": parse_ts(started),
        "season": (meta.get("season") or {}).get("short"),
        "region": meta.get("region"),
        "team": team,
        "rounds_won": rw,
        "rounds_lost": rl,
        "player_name": st.get("name"),  # the member's Riot ID as stored with the match, when the API includes it
        "player_tag": st.get("tag"),
        "player": {
            "puuid": st.get("puuid"),
            "agent": char.get("name"),
            "score": st.get("score"),
            "kills": st.get("kills"),
            "deaths": st.get("deaths"),
            "assists": st.get("assists"),
            "headshots": shots.get("head"),
            "bodyshots": shots.get("body"),
            "legshots": shots.get("leg"),
            "damage_dealt": dmg.get("made", dmg.get("dealt")),
            "damage_received": dmg.get("received"),
            "tier": st.get("tier"),
            "tier_name": None,
        },
    }


def member_game_row(rec):
    """Flatten a parsed stored item into a member_games row."""
    row = {k: rec[k] for k in ("match_id", "map", "mode", "mode_label", "started_at", "started_ts",
                                "rounds_won", "rounds_lost", "team")}
    row["result"] = result_of(rec["rounds_won"], rec["rounds_lost"])
    row.update({k: v for k, v in rec["player"].items() if k not in ("tier", "tier_name")})
    return row


def parse_details(data, member_puuids):
    """Turn a v4 match record into (match, players) if every member was on one team, else None."""
    meta = data.get("metadata") or {}
    players = data.get("players") or []
    ours = [p for p in players if p.get("puuid") in member_puuids]
    if len(ours) != len(member_puuids):
        return None
    team_ids = {p.get("team_id") for p in ours}
    if len(team_ids) != 1:
        return None
    team = ours[0].get("team_id") or ""
    tinfo = next((t for t in (data.get("teams") or []) if t.get("team_id") == team), None) or {}
    other = next((t for t in (data.get("teams") or []) if t.get("team_id") != team), None) or {}
    rounds = tinfo.get("rounds") or {}
    rw, rl = rounds.get("won"), rounds.get("lost")
    # The record's own winner flag beats the score: after a surrender the team that gave up loses,
    # even in the rare case it was ahead on rounds.
    if tinfo.get("won") is True:
        result = "win"
    elif other.get("won") is True:
        result = "loss"
    else:
        result = result_of(rw, rl)
    party_ids = {p.get("party_id") for p in ours}
    queue = meta.get("queue") or {}
    mode_label = queue.get("name") or queue.get("id") or ""
    started = meta.get("started_at")
    match = {
        "match_id": meta.get("match_id"),
        "map": (meta.get("map") or {}).get("name"),
        "mode": normalize_mode(queue.get("id") or mode_label),
        "mode_label": mode_label or None,
        "started_at": started,
        "started_ts": parse_ts(started),
        "season": (meta.get("season") or {}).get("short"),
        "region": meta.get("region"),
        "team": team.capitalize(),
        "rounds_won": rw,
        "rounds_lost": rl,
        "result": result,
        "game_length_ms": meta.get("game_length_in_ms"),
        "source": "details",
        "party_verified": 1 if (len(party_ids) == 1 and None not in party_ids) else 0,
        "details_fetched": 1,
    }
    out = []
    for p in ours:
        s = p.get("stats") or {}
        dmg = s.get("damage") or {}
        tier = p.get("tier") or {}
        out.append({
            "puuid": p["puuid"],
            "agent": (p.get("agent") or {}).get("name"),
            "score": s.get("score"),
            "kills": s.get("kills"),
            "deaths": s.get("deaths"),
            "assists": s.get("assists"),
            "headshots": s.get("headshots"),
            "bodyshots": s.get("bodyshots"),
            "legshots": s.get("legshots"),
            "damage_dealt": dmg.get("dealt"),
            "damage_received": dmg.get("received"),
            "tier": tier.get("id"),
            "tier_name": tier.get("name"),
        })
    return match, out


class Tracker:
    def __init__(self, cfg, db, client, on_new_matches=None):
        self.cfg = cfg
        self.db = db
        self.client = client
        self.on_new_matches = on_new_matches
        self.region = (cfg.get("region") or "na").lower()
        self.modes = {normalize_mode(m) for m in (cfg.get("modes") or []) if m}
        self.fetch_details = bool(cfg.get("fetch_match_details", True))
        self.details_per_sync = int(cfg.get("details_per_sync", 6))
        self.poll_size = int(cfg.get("poll_size", 40))
        self.interval = max(2.0, float(cfg.get("poll_interval_minutes", 10))) * 60.0
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self.state = {
            "syncing": False, "last_sync": None, "next_sync": None,
            "last_error": None, "last_result": db.get_meta("last_sync"),
        }
        self.logs = deque(maxlen=120)

    # ---- helpers ---------------------------------------------------------
    def log(self, msg):
        self.logs.append({"ts": time.time(), "msg": msg})
        print(f"[tracker] {msg}", flush=True)

    # ---- the squad roster ----------------------------------------------------
    def member_entries(self):
        """The members listed in config.json; they only seed an empty squad (placeholders ignored)."""
        out = []
        for i, entry in enumerate(self.cfg.get("members") or []):
            if isinstance(entry, dict):
                riot = entry.get("riot_id") or entry.get("id") or ""
                nick = (entry.get("nickname") or "").strip()
            else:
                riot, nick = str(entry), ""
            if riot.strip().lower() in PLACEHOLDER_IDS:
                continue
            try:
                name, tag = parse_riot_id(riot)
            except TrackerError:
                continue
            out.append({"name": name, "tag": tag, "nickname": nick or name, "order": i})
        return out[:ROSTER_MAX]

    def roster(self):
        """The current squad. An empty database is seeded from config.json once; after that the Squad tab rules."""
        members = self.db.members()
        if members or self.db.get_meta("roster_seeded"):
            return members
        problems = []
        for e in self.member_entries():
            try:
                self.add_member(f"{e['name']}#{e['tag']}", e["nickname"], reevaluate=False)
            except (TrackerError, HenrikError) as ex:
                msg = getattr(ex, "message", None) or str(ex)
                problems.append(f"{e['name']}#{e['tag']}: {msg}")
                self.log(f"Could not resolve {e['name']}#{e['tag']}: {msg}")
        members = self.db.members()
        if len(members) >= ROSTER_MIN:
            self.db.set_meta("roster_seeded", True)  # from here on config.json's members are ignored
            if problems:
                self.log("Seeded the squad without: " + "; ".join(problems) + ". Add them on the Squad tab.")
        elif problems:
            raise TrackerError("Could not resolve the members in config.json: " + "; ".join(problems))
        return members

    def _lookup(self, riot_id):
        """Resolve a Riot ID on HenrikDev: (account, name, tag)."""
        name, tag = parse_riot_id(riot_id)
        acct = self.client.account(name, tag)
        if not acct.get("puuid"):
            raise TrackerError(f"{name}#{tag}: HenrikDev returned no account id")
        return acct, acct.get("name") or name, acct.get("tag") or tag

    def add_member(self, riot_id, nickname="", bettor=None, reevaluate=True, active=True):
        """Put a player in the pool by their current Riot ID, on the active squad (`active`, the default; refused
        when the squad is full) or on the bench, optionally linked to the betting account `bettor`. A Riot ID that
        belongs to a known player (they renamed) just updates that player. Returns {member, added, renamed[, changes]}."""
        acct, name, tag = self._lookup(riot_id)
        return self._place(acct, name, tag, nickname, bettor, reevaluate, active)

    def _place(self, acct, name, tag, nickname="", bettor=None, reevaluate=True, active=True):
        puuid = acct["puuid"]
        nickname = (nickname or "").strip()[:32]
        card = acct.get("card")
        if isinstance(card, dict):
            card = card.get("small") or card.get("id")
        now = time.time()
        existing = self.db.member(puuid)
        if existing:
            if bettor and existing.get("bettor") and existing["bettor"].lower() != bettor.lower():
                raise TrackerError(f"{name}#{tag} is already linked to the account {existing['bettor']}.")
            renamed = self._apply_name(existing, name, tag, now)
            fields = {}
            if card:
                fields["card"] = card
            if nickname:
                fields["nickname"] = nickname
            if bettor and not existing.get("bettor"):
                fields["bettor"] = bettor
                self.log(f"{name}#{tag} is now {bettor}'s account")
            self.db.update_member(puuid, **fields)
            return {"member": self.db.member(puuid), "added": False, "renamed": renamed}
        if active and self.db.count_members() >= ROSTER_MAX:
            raise TrackerError(f"The squad is full ({ROSTER_MAX} players). Bench someone first, or add them to the bench.")
        self.db.upsert_member(puuid, name, tag, (acct.get("region") or self.region).lower(),
                              nickname or name, card, self.db.next_member_order())
        self.db.update_member(puuid, name_checked_ts=now, bettor=bettor, active=1 if active else 0)
        self.log(f"Added {name}#{tag} to the {'squad' if active else 'bench'}" + (f" ({bettor}'s account)" if bettor else ""))
        result = {"member": self.db.member(puuid), "added": True, "renamed": False}
        if active and reevaluate:
            result["changes"] = self.roster_changed()
        return result

    def set_active_list(self, puuids):
        """Make exactly these pool players (in this order) the active squad; everyone else goes to the bench. Games
        are re-derived when the line-up actually changed. Returns {changed, changes}."""
        wanted = []
        for p in puuids or []:
            if p not in wanted:
                wanted.append(p)
        pool = {m["puuid"]: m for m in self.db.pool()}
        unknown = [p for p in wanted if p not in pool]
        if unknown:
            raise TrackerError("Not in the pool: " + ", ".join(unknown))
        if len(wanted) < ROSTER_MIN:
            raise TrackerError(f"The squad needs at least {ROSTER_MIN} players.")
        if len(wanted) > ROSTER_MAX:
            raise TrackerError(f"The squad can have at most {ROSTER_MAX} players.")
        before = [m["puuid"] for m in self.db.members()]
        for i, p in enumerate(wanted):
            self.db.set_active(p, True, i)
        for i, m in enumerate(self.db.pool()):
            if m["puuid"] not in wanted:
                self.db.set_active(m["puuid"], False, len(wanted) + i)
        if set(before) == set(wanted):
            return {"changed": False, "changes": {"removed": 0, "added": 0}}
        names = ", ".join(f"{pool[p]['name']}#{pool[p]['tag']}" for p in wanted)
        self.log(f"Line-up changed: {names}")
        return {"changed": True, "changes": self.roster_changed()}

    def set_active(self, puuid, active):
        """Swap one pool player onto the squad or onto the bench."""
        current = [m["puuid"] for m in self.db.members()]
        if active and puuid not in current:
            if len(current) >= ROSTER_MAX:
                raise TrackerError(f"The squad is full ({ROSTER_MAX} players). Bench someone first.")
            current.append(puuid)
        elif not active and puuid in current:
            current.remove(puuid)
        return self.set_active_list(current)

    def set_riot_id(self, bettor, riot_id, nickname=""):
        """A betting account's own pool entry: add it (on the squad if there's room, else on the bench), rename it,
        claim an unlinked player with that Riot ID, or switch to another account's Riot ID (the old entry leaves).
        Returns _place()'s result plus `replaced` and `active`."""
        acct, name, tag = self._lookup(riot_id)
        own = self.db.member_by_bettor(bettor)
        target = self.db.member(acct["puuid"])
        if target and target.get("bettor") and target["bettor"].lower() != bettor.lower():
            raise TrackerError(f"{name}#{tag} is already linked to the account {target['bettor']}.")
        replaced = bool(own and own["puuid"] != acct["puuid"])
        own_active = bool(own and own.get("active"))
        if replaced:
            self.db.remove_member(own["puuid"])  # the new entry takes its place; roster_changed() below re-derives
            self.log(f"{bettor} switched from {own['name']}#{own['tag']} to {name}#{tag}")
        active = own_active if replaced else self.db.count_members() < ROSTER_MAX
        result = self._place(acct, name, tag, nickname or (own or {}).get("nickname") or "", bettor,
                             reevaluate=True, active=active)
        if (replaced and own_active) and "changes" not in result:
            result["changes"] = self.roster_changed()
        result["replaced"] = replaced
        result["active"] = bool(result["member"].get("active"))
        return result

    def set_own_active(self, bettor, active):
        """Swap your own entry onto the squad or onto the bench."""
        own = self.db.member_by_bettor(bettor)
        if not own:
            raise TrackerError("You are not in the pool yet: add your Riot ID first.")
        return self.set_active(own["puuid"], active)

    def unlink_member(self, bettor):
        """Take a betting account's Riot ID out of the pool (never below ROSTER_MIN on the squad)."""
        own = self.db.member_by_bettor(bettor)
        if not own:
            raise TrackerError("You are not in the pool.")
        return self.remove_member(own["puuid"])

    def remove_member(self, puuid):
        """Take a player out of the pool altogether. From the bench that changes nothing else; from the active squad
        it re-derives the games (never below ROSTER_MIN)."""
        m = self.db.member(puuid)
        if not m:
            raise TrackerError("That player is not in the pool.")
        was_active = bool(m.get("active"))
        if was_active and self.db.count_members() <= ROSTER_MIN:
            raise TrackerError(f"The squad needs at least {ROSTER_MIN} players; swap someone in before removing them.")
        self.db.remove_member(puuid)
        self.log(f"Removed {m['name']}#{m['tag']} from the {'squad' if was_active else 'bench'}")
        changes = self.roster_changed() if was_active else {"removed": 0, "added": 0}
        return {**changes, "was_active": was_active}

    def set_nickname(self, puuid, nickname):
        m = self.db.member(puuid)
        if not m:
            raise TrackerError("That player is not on the squad.")
        self.db.update_member(puuid, nickname=(nickname or "").strip()[:32] or m["name"])
        return self.db.member(puuid)

    def refresh_names(self, force=False):
        """Look up each member's current Riot ID (the PUUID survives a rename), at most once a day unless forced.
        Returns the renames found, as {puuid, from, to}."""
        renamed = []
        now = time.time()
        for m in self.db.pool():  # the bench too, so a swapped-in player comes back under their current name
            if not force and (m.get("name_checked_ts") or 0) > now - NAME_REFRESH_S:
                continue
            try:
                acct = self.client.account_by_puuid(m["puuid"])
            except HenrikError as ex:
                self.log(f"Could not check {m['name']}#{m['tag']} for a rename: {ex.message}")
                continue
            if self._apply_name(m, acct.get("name"), acct.get("tag"), now):
                renamed.append({"puuid": m["puuid"], "from": f"{m['name']}#{m['tag']}",
                                "to": f"{acct.get('name')}#{acct.get('tag')}"})
        return renamed

    def _apply_name(self, member, name, tag, now):
        """Record a checked (and possibly new) Riot ID for a member. True if it changed."""
        fields = {"name_checked_ts": now}
        changed = bool(name and tag) and (name.lower(), tag.lower()) != (member["name"].lower(), member["tag"].lower())
        if changed:
            fields.update(name=name, tag=tag, previous_name=f"{member['name']}#{member['tag']}")
            self.log(f"{member['name']}#{member['tag']} is now {name}#{tag}")
        self.db.update_member(member["puuid"], **fields)
        return changed

    def roster_changed(self):
        """After the squad changes: earlier rejections no longer mean anything, the next sync must fetch full
        history (a newcomer has none stored), and the recorded games are re-derived for the new squad."""
        self.db.set_meta("rejected_matches", [])
        self.db.set_meta("history_backfilled", False)
        return self.reevaluate()

    def reevaluate(self):
        """Make `matches` match the current squad using only the lines already stored (no API calls): games some
        member wasn't in are dropped, and games every member's stored line proves they played together are added.
        Games only the full match record can prove (a stored-history hole) come back on the next sync."""
        puuids = [m["puuid"] for m in self.db.members()]
        removed = added = 0
        if len(puuids) < ROSTER_MIN:
            return {"removed": 0, "added": 0}
        for mid in self.db.matches_missing_any(puuids):
            self.db.delete_match(mid)
            removed += 1
        by_id = defaultdict(dict)
        for row in self.db.member_games_of(puuids):
            by_id[row["match_id"]][row["puuid"]] = row
        for mid, rows in by_id.items():
            if len(rows) != len(puuids) or self.db.has_match(mid):
                continue
            sample = next(iter(rows.values()))
            if self.modes and sample["mode"] not in self.modes:
                continue
            teams = {r.get("team") for r in rows.values()}
            if None in teams or "" in teams:  # lines stored before `team` existed: the same score means the same team
                same_team = len({(r["rounds_won"], r["rounds_lost"]) for r in rows.values()}) == 1
            else:
                same_team = len(teams) == 1
            if not same_team or ending(sample) == NO_CONTEST:
                continue
            match = {k: sample.get(k) for k in ("match_id", "map", "mode", "mode_label", "started_at", "started_ts",
                                                "rounds_won", "rounds_lost", "result", "team")}
            match.update({"source": "roster", "party_verified": None, "details_fetched": 0})
            players = [{k: r.get(k) for k in ("puuid", "agent", "score", "kills", "deaths", "assists", "headshots",
                                              "bodyshots", "legshots", "damage_dealt", "damage_received")}
                       for r in rows.values()]
            self.db.insert_match(match, players)
            added += 1
        if removed or added:
            self.log(f"Squad changed: {added} more game(s) count, {removed} no longer do")
        return {"removed": removed, "added": added}

    def wake(self):
        """Let the poller run its next sync now rather than at the end of the interval."""
        self._wake.set()

    # ---- sync ------------------------------------------------------------
    def sync(self, full=False):
        if not self._lock.acquire(blocking=False):
            return {"ok": True, "busy": True}
        started = time.time()
        calls0 = self.client.calls
        self.state["syncing"] = True
        try:
            members = self.roster()
            if len(members) < ROSTER_MIN:
                raise TrackerError(f"Add at least {ROSTER_MIN} players on the Squad tab (up to {ROSTER_MAX}).")
            if len(members) > ROSTER_MAX:
                raise TrackerError(f"The squad has {len(members)} players; the most is {ROSTER_MAX}. Remove someone on the Squad tab.")
            self.refresh_names()  # once a day: follow Riot ID renames
            members = self.db.members()
            puuids = [m["puuid"] for m in members]
            pset = set(puuids)
            stack = f"{len(puuids)}-stack"
            if not full and not self.db.get_meta("history_backfilled"):
                # A database from before member_games existed, or a changed squad: pull everyone's full history once.
                full = True
                self.log("Fetching full history once to build each member's baseline")

            by_id = defaultdict(dict)
            member_games = []
            for m in members:
                region = (m.get("region") or self.region).lower()
                self.log(f"Fetching {'full' if full else 'recent'} stored matches for {m['name']}#{m['tag']}")
                payload = self.client.stored_matches(region, m["puuid"], size=None if full else self.poll_size)
                items = payload.get("data") or []
                if items:  # the newest stored game carries the member's Riot ID as of that game: a free rename check
                    rec = parse_stored_item(items[0])
                    if rec.get("player_name") and rec.get("player_tag"):
                        self._apply_name(m, rec["player_name"], rec["player_tag"], time.time())
                for item in items:
                    rec = parse_stored_item(item)
                    if rec["match_id"] and rec["player"]["puuid"]:
                        by_id[rec["match_id"]][m["puuid"]] = rec
                        if (rec["rounds_won"] is not None and (not self.modes or rec["mode"] in self.modes)
                                and ending(rec) != NO_CONTEST):
                            member_games.append(member_game_row(rec))
            self.db.insert_member_games(member_games)

            rejected = set(self.db.get_meta("rejected_matches", []) or [])
            new_matches, skipped_mode, candidates, verified = [], 0, 0, 0
            for mid, recs in by_id.items():
                if mid in rejected or self.db.has_match(mid):
                    continue
                candidates += 1
                sample = next(iter(recs.values()))
                if self.modes and sample["mode"] not in self.modes:
                    skipped_mode += 1
                    continue
                teams = {r["team"] for r in recs.values()}
                if len(teams) != 1 or not (teams & {"Red", "Blue"}):
                    rejected.add(mid)
                    continue
                if ending(sample) == NO_CONTEST:
                    rejected.add(mid)  # remake / abandoned lobby: not a game, so bets roll to the next one
                    self.log(f"Skipped {sample['map']} {sample['rounds_won']}-{sample['rounds_lost']}: ended within the first rounds (remake)")
                    continue
                if len(recs) == len(puuids):
                    match = {k: v for k, v in sample.items() if k != "player"}
                    match.update({
                        "result": result_of(match["rounds_won"], match["rounds_lost"]),
                        "source": "stored", "party_verified": None, "details_fetched": 0,
                    })
                    self.db.insert_match(match, [r["player"] for r in recs.values()])
                    new_matches.append(match)
                    self.log(f"New {stack} game: {match['map']} {match['rounds_won']}-{match['rounds_lost']} ({match['started_at']})")
                elif len(recs) >= len(puuids) - 1 and self.fetch_details:
                    region = (sample.get("region") or self.region).lower()
                    try:
                        data = self.client.match_details(region, mid)
                    except HenrikError as ex:
                        self.log(f"Could not verify {mid[:8]}... via match details: {ex.message}")
                        continue
                    verified += 1
                    parsed = parse_details(data, pset)
                    if parsed:
                        self.db.insert_match(*parsed)
                        self.db.save_timeline(parsed[0]["match_id"], extract_timeline(data, pset))
                        new_matches.append(parsed[0])
                        self.log(f"New {stack} game (verified): {parsed[0]['map']} {parsed[0]['rounds_won']}-{parsed[0]['rounds_lost']}")
                    else:
                        rejected.add(mid)
                else:
                    rejected.add(mid)

            enriched = 0
            if self.fetch_details:
                for m in self.db.matches_needing_details(self.details_per_sync):
                    try:
                        data = self.client.match_details((m.get("region") or self.region).lower(), m["match_id"])
                    except HenrikError as ex:
                        self.log(f"Details for {m['match_id'][:8]}... unavailable: {ex.message}")
                        if ex.status == 429:
                            break
                        if ex.status == 404:
                            self.db.update_match(m["match_id"], details_fetched=1)
                            self.db.save_timeline(m["match_id"], None)  # gone for good: don't ask again
                        continue
                    self.db.save_timeline(m["match_id"], extract_timeline(data, pset))
                    parsed = parse_details(data, pset)
                    if parsed:
                        pm, players = parsed
                        fields = dict(game_length_ms=pm.get("game_length_ms"),
                                      party_verified=pm.get("party_verified"), details_fetched=1,
                                      season=pm.get("season") or m.get("season"))
                        if pm.get("result") and pm["result"] != m.get("result"):
                            fields["result"] = pm["result"]  # e.g. a surrender by the team that led on rounds
                            self.log(f"Corrected {m['map']} {m['rounds_won']}-{m['rounds_lost']} to a {pm['result']} from the full record")
                        self.db.update_match(m["match_id"], **fields)
                        for p in players:
                            self.db.update_player(m["match_id"], p["puuid"], tier_name=p.get("tier_name"), tier=p.get("tier"))
                    else:
                        self.db.update_match(m["match_id"], details_fetched=1, party_verified=0)
                    enriched += 1

            self.db.set_meta("rejected_matches", sorted(rejected)[-4000:])
            if full:
                self.db.set_meta("history_backfilled", True)
            new_matches.sort(key=lambda x: x.get("started_ts") or 0)
            settled = []
            if new_matches and self.on_new_matches:
                settled = self.on_new_matches([self.db.match(m["match_id"]) for m in new_matches])

            summary = {
                "ok": True, "full": full, "members": len(members), "candidates": candidates,
                "new_matches": len(new_matches), "skipped_mode": skipped_mode, "verified": verified,
                "enriched": enriched, "bets_settled": len(settled),
                "api_calls": self.client.calls - calls0, "duration_s": round(time.time() - started, 1),
                "finished_at": time.time(),
            }
            self.state.update({"last_sync": time.time(), "last_result": summary, "last_error": None})
            self.db.set_meta("last_sync", summary)
            self.log(f"Sync done: {len(new_matches)} new game(s), {summary['api_calls']} API call(s), {len(settled)} bet(s) settled")
            return summary
        except (TrackerError, HenrikError) as ex:
            msg = getattr(ex, "message", None) or str(ex)
            self.state["last_error"] = msg
            self.log(f"Sync failed: {msg}")
            return {"ok": False, "error": msg}
        except Exception as ex:  # keep the poller alive no matter what
            msg = f"{type(ex).__name__}: {ex}"
            self.state["last_error"] = msg
            self.log(f"Sync crashed: {msg}")
            return {"ok": False, "error": msg}
        finally:
            self.state["syncing"] = False
            self._lock.release()

    # ---- background polling ----------------------------------------------
    def start(self):
        threading.Thread(target=self._loop, name="poller", daemon=True).start()

    def request_sync(self, full=False):
        threading.Thread(target=self.sync, kwargs={"full": full}, daemon=True).start()

    def _loop(self):
        time.sleep(1.5)
        while True:
            full = self.db.count_matches() == 0 and not self.db.get_meta("backfilled")
            res = self.sync(full=full)
            if full and res.get("ok") and not res.get("busy"):
                self.db.set_meta("backfilled", True)
            self.state["next_sync"] = time.time() + self.interval
            self._wake.wait(self.interval)
            self._wake.clear()
