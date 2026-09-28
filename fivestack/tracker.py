"""Finds games where every configured member played on the same team, and stores them.

How detection works
-------------------
1. Every member's Riot ID is resolved to a PUUID once (account endpoint).
2. Each sync pulls every member's *stored matches* from HenrikDev (one call per
   member). Each entry carries that member's own line (agent, K/D/A, score,
   shots, damage) plus the match metadata and the final round score.
3. A match id that shows up for ALL members, with everybody on the same team,
   is a 5-stack game. It is stored straight from those lines.
4. If a match shows up for all-but-one member (HenrikDev's stored history can
   have holes), the full v4 match record is fetched to check whether the
   missing member was in it too.
5. Optionally the v4 record is fetched for stored games as well, to add game
   length, rank names and to verify everybody shared one party.
6. Every member line in a counted mode is also kept in member_games, so each
   member's non-5-stack games serve as the baseline their 5-stack play is
   compared against.
"""
import threading
import time
from collections import defaultdict, deque
from datetime import datetime

from .gamestate import NO_CONTEST, ending
from .henrik import HenrikError
from .timeline import extract_timeline

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
                                "rounds_won", "rounds_lost")}
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

    def member_entries(self):
        out = []
        for i, entry in enumerate(self.cfg.get("members") or []):
            if isinstance(entry, dict):
                riot = entry.get("riot_id") or entry.get("id") or ""
                nick = (entry.get("nickname") or "").strip()
            else:
                riot, nick = str(entry), ""
            name, tag = parse_riot_id(riot)
            out.append({"name": name, "tag": tag, "nickname": nick or name, "order": i})
        return out

    def resolve_members(self):
        entries = self.member_entries()
        resolved, problems = [], []
        for e in entries:
            existing = self.db.member_by_riot_id(e["name"], e["tag"])
            if existing:
                self.db.upsert_member(
                    existing["puuid"], existing["name"], existing["tag"],
                    existing.get("region") or self.region, e["nickname"], existing.get("card"), e["order"],
                )
                resolved.append(self.db.member(existing["puuid"]))
                continue
            try:
                acct = self.client.account(e["name"], e["tag"])
            except HenrikError as ex:
                problems.append(f"{e['name']}#{e['tag']}: {ex.message}")
                self.log(f"Could not resolve {e['name']}#{e['tag']}: {ex.message}")
                continue
            puuid = acct.get("puuid")
            if not puuid:
                problems.append(f"{e['name']}#{e['tag']}: no PUUID in response")
                continue
            card = acct.get("card")
            if isinstance(card, dict):
                card = card.get("small") or card.get("id")
            self.db.upsert_member(
                puuid, acct.get("name") or e["name"], acct.get("tag") or e["tag"],
                (acct.get("region") or self.region).lower(), e["nickname"], card, e["order"],
            )
            resolved.append(self.db.member(puuid))
            self.log(f"Resolved {e['name']}#{e['tag']} -> {puuid[:8]}...")
        # Drop members that are no longer in the config (keep their old games).
        configured = {(e["name"].lower(), e["tag"].lower()) for e in entries}
        keep = [m["puuid"] for m in self.db.members()
                if (m["name"].lower(), m["tag"].lower()) in configured or m["puuid"] in {r["puuid"] for r in resolved}]
        self.db.remove_members_not_in(keep)
        return resolved, problems

    # ---- sync ------------------------------------------------------------
    def sync(self, full=False):
        if not self._lock.acquire(blocking=False):
            return {"ok": True, "busy": True}
        started = time.time()
        calls0 = self.client.calls
        self.state["syncing"] = True
        try:
            members, problems = self.resolve_members()
            expected = len(self.member_entries())
            if expected < 2:
                raise TrackerError("Add at least two members to config.json (a 5-stack needs five).")
            if len(members) < expected:
                raise TrackerError("Could not resolve every member: " + "; ".join(problems))
            puuids = [m["puuid"] for m in members]
            pset = set(puuids)
            if not full and not self.db.get_meta("history_backfilled"):
                # Databases from before member_games existed only hold 5-stack games: pull everything once.
                full = True
                self.log("Fetching full history once to build each member's non-5-stack baseline")

            by_id = defaultdict(dict)
            member_games = []
            for m in members:
                region = (m.get("region") or self.region).lower()
                self.log(f"Fetching {'full' if full else 'recent'} stored matches for {m['name']}#{m['tag']}")
                payload = self.client.stored_matches(region, m["puuid"], size=None if full else self.poll_size)
                for item in payload.get("data") or []:
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
                    self.log(f"New 5-stack game: {match['map']} {match['rounds_won']}-{match['rounds_lost']} ({match['started_at']})")
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
                        self.log(f"New 5-stack game (verified): {parsed[0]['map']} {parsed[0]['rounds_won']}-{parsed[0]['rounds_lost']}")
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
